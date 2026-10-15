"""ACP agent 探测即注册（B3）：/api/agents/detect 与 acp-proxy /agents/probe 打通。

铁律（一条真源）：
- 检测成功=必能开会话：注册必须同时过两级真探测——
  L1 真ACP握手（acp-proxy POST /agents/probe，PTY拉起→initialize→认JSON-RPC应答）
  + L2 真开会话（acp-proxy ws /ws/acp → session/new 拿到 sessionId）。
  装没装（shutil.which）永远只是目录信息，不构成"检测成功"。
- 未注册不进列表：registered=1（两级全过）才进会话列表；一次失败不除名但
  session_ready=0（状态徽章不说谎）。
- 禁冒名兜底：ws_acp 对未知 agent_id 会回落 soulmate 路由——探测前先查
  acp-proxy 注册表（GET /agents），不在表内且无显式 cmd 的直接拒绝，
  绝不拿别人的握手冒充本 agent 的"检测成功"。
- 失败必留痕：probe_detail/last_probe_at/probe_count 落 SQLite，
  acp-proxy 不在线时 fail-closed 返回 ok=False，禁谎报。

探测即注册（对齐 wiki/entities/aionui.md：AgentManagementStatus × 探测时机）：
本切片实现 manual 时机（POST /api/agents/probe）；注册结果供 detect/registry
同表读出（检测/管理页/列表一条真源）。

副作用说明（如实记录）：L2 会真拉起目标 agent 一个会话（与用户在页面开一个
会话同路径），WS 摘除后由 acp-proxy _reap_detached 空闲30分钟兜底回收——
所以探测是手动/按需动作，不做后台批量巡检。
"""

import asyncio
import json
import logging
import os
import shlex
import sqlite3
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

ACPPROXY_HTTP = os.environ.get("ACPPROXY_HTTP", "http://127.0.0.1:8092")
ACPPROXY_WS = os.environ.get("ACPPROXY_WS", "ws://127.0.0.1:8092/ws/acp")
# 自研适配器模块（agent.*）的真实 cwd=acp-proxy 目录（AGENT_ROUTES 各 route 的 cwd）
ACPPROXY_DIR = os.environ.get("ACPPROXY_DIR") or str(Path.home() / "openmate" / "acp-proxy")

_HANDSHAKE_TIMEOUT = 45.0  # acp-proxy侧npx桥包probe_timeout=30s，留余量
_SESSION_TIMEOUT = 90.0  # session/new要等agent进程真起完（npx首跑下载慢）


def _db_path() -> str:
    return os.environ.get("AGENT_REGISTRY_DB") or str(
        Path(__file__).resolve().parents[2] / "data" / "agent_registry.db"
    )


class AgentTenantDenied(Exception):
    """跨租户写拒（行归属不可被跨租户改写）：API层映射为HTTP 403。

    DEC-⑦-③ TemplateTenantDenied / ④ FileTenantDenied 同款（劫持闸语义，
    F3b-③c-2②d 纪律）。
    """


def agent_row_tenant(entry: dict) -> str:
    """行租户键单一判法（DEC-⑦-⑤）：自带键、缺失/空白→'default' 兜底。

    写读同源：_record 落行与 list_registered 过滤、劫持闸、API 加性 tenant_id
    键共用本函数，禁消费点另发明判法（F4b-㉗ 纪律）；DEC-⑦-① _event_row_tenant
    / ② message_row_tenant / ③ template_row_tenant / ④ file_row_tenant 同款语义
    （系统/缺省写方→'default'，键派生故障绝不吃掉落库）。
    """
    return str((entry or {}).get("tenant_id") or "").strip() or "default"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS acp_agent_registry (
            agent_id TEXT PRIMARY KEY,
            cmd TEXT NOT NULL DEFAULT '[]',
            registered INTEGER NOT NULL DEFAULT 0,
            session_ready INTEGER NOT NULL DEFAULT 0,
            probe_ok INTEGER,
            probe_detail TEXT,
            session_id TEXT,
            last_probe_at TEXT,
            probe_count INTEGER NOT NULL DEFAULT 0,
            tenant_id TEXT NOT NULL DEFAULT 'default'
        )"""
    )
    # 老库自愈（C1 幂等双轨，③c-2① 先例）：CREATE TABLE IF NOT EXISTS 不补列，
    # 存量库按 PRAGMA 探测补 tenant_id 列（常量默认='default' 回填存量行，与 F3
    # 实测分布一致）；租户索引必须在补列之后建（③c-2① 坑延续）。
    cols = {r[1] for r in conn.execute("PRAGMA table_info(acp_agent_registry)").fetchall()}
    if "tenant_id" not in cols:
        conn.execute(
            "ALTER TABLE acp_agent_registry ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
        )
        conn.commit()
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_acp_agents_tenant ON acp_agent_registry(tenant_id)"
    )
    conn.commit()
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["cmd"] = json.loads(d.get("cmd") or "[]")
    except Exception:
        d["cmd"] = []
    d["registered"] = bool(d["registered"])
    d["session_ready"] = bool(d["session_ready"])
    if d.get("probe_ok") is not None:
        d["probe_ok"] = bool(d["probe_ok"])
    # 行归属可观测（DEC-⑦-⑤ 加性键）：归一值=agent_row_tenant 单一判法。
    d["tenant_id"] = agent_row_tenant(d)
    return d


def list_registered(tenant_id: str | None = None) -> list[dict]:
    """未注册不进列表：只回 registered=1（两级真探测全过）的行。

    tenant_id=显式租户→恒按行租户键过滤（DEC-⑦-⑤）；None=系统视图（既有
    调用面字节不变）。
    """
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM acp_agent_registry WHERE registered=1 ORDER BY agent_id"
        ).fetchall()
        entries = [_row_to_dict(r) for r in rows]
        if tenant_id is not None:
            entries = [e for e in entries if agent_row_tenant(e) == tenant_id]
        return entries
    finally:
        conn.close()


def get_entry(agent_id: str, tenant_id: str | None = None) -> dict | None:
    """by-ID 唯一行读取口归属闸（DEC-⑦-⑮）：显式租户=跨租户回 None 与 not-found
    同契约，不差分泄露行存在性（③c-1 纪律；⑦-⑫ TemplateEngine.get_template 单点
    归属闸同款，判法 agent_row_tenant 单一源禁消费点另发明）；tenant_id=None=
    系统视图字节不变（既有调用面零变化）。acp_agent_registry 的 by-ID 读唯一入口
    ——闸收在单一读源本体=未来消费方构造上无法绕闸（⑦-⑩ get_meta 单点闸覆盖全
    读面同款）；_record 内行读属写路径劫持闸（跨租户 raise 语义刻意不同）不并入。
    """
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM acp_agent_registry WHERE agent_id=?", (str(agent_id),)
        ).fetchone()
        entry = _row_to_dict(row) if row else None
        if entry is not None and tenant_id is not None and agent_row_tenant(entry) != tenant_id:
            return None
        return entry
    finally:
        conn.close()


def _record(
    agent_id: str,
    cmd: list | None,
    probe_ok: bool,
    probe_detail: str,
    session_ok: bool | None,
    session_id: str = "",
    tenant_id: str | None = None,
) -> dict:
    """落痕+探测即注册：两级全过才 registered=1；失败不除名但 session_ready=0。
    session_ok=None 表示本次未验会话（连通性体检）——session_ready/registered
    保持既有值，未验证≠不可用（徽章不说谎的双向语义）。

    tenant_id=写点派生租户键（DEC-⑦-⑤）；未提供/空白→'default' 兜底=系统写方
    语义（agent_row_tenant 单一判法，键派生故障绝不吃掉落库）。劫持闸：同
    agent_id 跨租户重探测=行归属不可被跨租户改写→AgentTenantDenied 行零改写
    （DEC-⑦-③④ 同款，F3b-③c-2②d 纪律）；ON CONFLICT 不带 tenant_id（粘性：
    行归属=首次写入，同租户再探绝不洗键）。
    """
    checked = session_ok is not None
    registered = bool(probe_ok and session_ok)
    row_tenant = agent_row_tenant({"tenant_id": tenant_id})
    conn = _conn()
    try:
        existing = conn.execute(
            "SELECT * FROM acp_agent_registry WHERE agent_id=?", (str(agent_id),)
        ).fetchone()
        if existing is not None and agent_row_tenant(_row_to_dict(existing)) != row_tenant:
            raise AgentTenantDenied(f"agent_id {str(agent_id)!r} belongs to another tenant")
        conn.execute(
            """INSERT INTO acp_agent_registry
               (agent_id, cmd, registered, session_ready, probe_ok, probe_detail,
                session_id, last_probe_at, probe_count, tenant_id)
               VALUES (:agent_id, :cmd, :registered, :session_ready, :probe_ok,
                       :probe_detail, :session_id, :last_probe_at, 1, :tenant_id)
               ON CONFLICT(agent_id) DO UPDATE SET
                 cmd=excluded.cmd,
                 registered=CASE WHEN :checked
                                 THEN MAX(acp_agent_registry.registered, excluded.registered)
                                 ELSE acp_agent_registry.registered END,
                 session_ready=CASE WHEN :checked
                                    THEN excluded.session_ready
                                    ELSE acp_agent_registry.session_ready END,
                 probe_ok=excluded.probe_ok,
                 probe_detail=excluded.probe_detail,
                 session_id=CASE WHEN :checked
                                 THEN excluded.session_id
                                 ELSE acp_agent_registry.session_id END,
                 last_probe_at=excluded.last_probe_at,
                 probe_count=acp_agent_registry.probe_count+1""",
            {
                "agent_id": str(agent_id),
                "cmd": json.dumps(list(cmd or [])),
                "registered": 1 if registered else 0,
                "session_ready": 1 if (checked and session_ok) else 0,
                "probe_ok": 1 if probe_ok else 0,
                "probe_detail": str(probe_detail)[:500],
                "session_id": str(session_id or ""),
                "last_probe_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "checked": 1 if checked else 0,
                "tenant_id": row_tenant,
            },
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM acp_agent_registry WHERE agent_id=?", (str(agent_id),)
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


async def known_agents() -> dict[str, dict]:
    """acp-proxy 注册表=唯一真源（GET /agents）。取不到返回 {}（fail-closed）。"""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{ACPPROXY_HTTP}/agents")
            r.raise_for_status()
            data = r.json() or {}
            return {a["id"]: a for a in data.get("agents", []) if a.get("id")}
    except Exception as e:
        logger.warning("acp-proxy注册表不可达(fail-closed): %s", e)
        return {}


def _probe_cmd(cmd: list | None) -> list | None:
    """acp-proxy _probe_agent 固定 chdir(~) 的绕行（该bug在acp-proxy侧，本项目禁改）：
    自研适配器路由（cmd 形如 [<python>, -m, agent.*]，真实 cwd=acp-proxy 目录）在 ~
    下 import 必败（no ACP response 假阴性）——补真实 cwd 再探，探测才真走它的
    启动路径（禁一刀切）。其余路由（hermes/mimo/opencode/openclaw 在 PATH、
    npx 桥包）cwd 无关，原样探测。"""
    if cmd and len(cmd) >= 3 and cmd[1] == "-m" and str(cmd[2]).startswith("agent."):
        joined = " ".join(shlex.quote(str(c)) for c in cmd)
        return ["sh", "-c", f"cd {shlex.quote(ACPPROXY_DIR)} && exec {joined}"]
    return cmd


async def handshake_probe(agent_id: str, cmd: list | None = None) -> dict:
    """L1 真ACP握手：打通 acp-proxy POST /agents/probe（"测试连接"的真身）。
    返回的 cmd 是真实会话启动命令（探测 payload 内部按需补 cwd 包装）。"""
    import httpx

    payload: dict = {"agent_id": str(agent_id)}
    if cmd:
        payload["cmd"] = _probe_cmd(list(cmd))
    try:
        async with httpx.AsyncClient(timeout=_HANDSHAKE_TIMEOUT) as client:
            r = await client.post(f"{ACPPROXY_HTTP}/agents/probe", json=payload)
            r.raise_for_status()
            data = r.json() or {}
            return {
                "ok": bool(data.get("ok")),
                "detail": str(data.get("detail") or ""),
                "cmd": list(cmd or data.get("cmd") or []),
            }
    except Exception as e:
        return {
            "ok": False,
            "detail": f"acp-proxy /agents/probe 不可达: {e}",
            "cmd": list(cmd or []),
        }


async def session_probe(agent_id: str, timeout: float = _SESSION_TIMEOUT) -> dict:
    """L2 真开会话：ws /ws/acp → initialize + session/new，拿到 sessionId 才算过。

    线协议对齐 ai_groups._run_soulmate_turn（live :8092 实测）：initialize(id=1)
    由代理应答；session/new(id=2) 的 result.sessionId 来自 agent 本体。
    """
    import websockets

    from src.services.auth import create_access_token

    token = create_access_token(uuid.uuid4())
    url = f"{ACPPROXY_WS}?token={token}"
    try:
        async with websockets.connect(url, max_size=8 * 1024 * 1024, open_timeout=15) as ws:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout
            await ws.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": 1},
                    }
                )
            )
            await ws.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "session/new",
                        "params": {
                            "agent_id": str(agent_id),
                            "_meta": {"source": "agent-probe"},
                        },
                    }
                )
            )
            session_id = ""
            error = ""
            pending = {1, 2}
            while pending and loop.time() < deadline:
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=max(1.0, deadline - loop.time())
                    )
                except TimeoutError:
                    break
                try:
                    frame = json.loads(raw)
                except Exception:
                    continue
                if frame.get("error"):
                    error = str(frame["error"])[:300]
                    break
                fid = frame.get("id")
                if fid in pending:
                    pending.discard(fid)
                    if fid == 2:
                        session_id = str((frame.get("result") or {}).get("sessionId") or "")
            if session_id:
                return {"ok": True, "session_id": session_id, "detail": "session/new OK"}
            return {
                "ok": False,
                "session_id": "",
                "detail": error or "session/new未返回sessionId",
            }
    except Exception as e:
        return {"ok": False, "session_id": "", "detail": f"ws会话探测失败: {e}"}


async def probe_agent(
    agent_id: str,
    cmd: list | None = None,
    verify_session: bool = True,
    tenant_id: str | None = None,
) -> dict:
    """探测即注册全路径：注册表预检（禁冒名兜底）→ L1握手 → L2开会话 → 落库。

    verify_session=False 时只做 L1 快速握手（连通性体检），不改注册状态。
    tenant_id=写点派生租户键（DEC-⑦-⑤，透传 _record；跨租户同 agent_id=
    AgentTenantDenied 行零改写）。
    """
    agent_id = str(agent_id)
    known = await known_agents()
    in_registry = agent_id in known
    if not in_registry and not cmd:
        detail = (
            f"agent '{agent_id}' 不在 acp-proxy 注册表且未显式给 cmd——"
            "拒绝探测（禁冒名兜底：ws侧未知agent会回落soulmate路由）"
        )
        entry = _record(
            agent_id,
            cmd,
            probe_ok=False,
            probe_detail=detail,
            session_ok=False,
            tenant_id=tenant_id,
        )
        return {
            "agent_id": agent_id,
            "ok": False,
            "registered": False,
            "verify_session": verify_session,
            "probe": {"ok": False, "detail": detail},
            "session": None,
            "registry": entry,
        }

    probe = await handshake_probe(agent_id, cmd=cmd or (known.get(agent_id) or {}).get("cmd"))
    session: dict | None = None
    session_ok: bool | None = False
    if probe["ok"] and verify_session:
        if not in_registry:
            # 未注册agent只能验握手（ws路由会冒名兜底，session结果不可信）
            session = {
                "ok": False,
                "session_id": "",
                "detail": "未注册agent不做会话探测（禁冒名兜底），无法证明必能开会话",
            }
        else:
            session = await session_probe(agent_id)
            session_ok = bool(session.get("ok"))
    elif probe["ok"]:
        session_ok = None  # 只握手不验会话：注册状态保持既有值

    ok = bool(probe["ok"] and session_ok is True)
    detail_parts = [f"L1握手: {probe.get('detail', '')}"]
    if session is not None:
        detail_parts.append(f"L2会话: {session.get('detail', '')}")
    elif not verify_session:
        detail_parts.append("L2会话: 跳过(verify_session=false，仅连通性体检，不注册)")
    entry = _record(
        agent_id,
        probe.get("cmd") or cmd,
        probe_ok=bool(probe["ok"]),
        probe_detail=" | ".join(detail_parts),
        session_ok=session_ok,
        session_id=(session or {}).get("session_id", ""),
        tenant_id=tenant_id,
    )
    return {
        "agent_id": agent_id,
        "ok": ok,
        "registered": bool(entry.get("registered")),
        "verify_session": verify_session,
        "probe": probe,
        "session": session,
        "registry": entry,
    }
