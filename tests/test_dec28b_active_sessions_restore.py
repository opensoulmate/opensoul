#!/usr/bin/env python3
"""DEC-㉘b-① system 页「活跃Agent/近期会话」显示恢复——真源补现两统计（㉘b 取 B
语义随拍）＋UI 复原（统计条两卡＋数据统计面板两行，CD-⑱ 移除件复原语义）回归锁死。

背景（pending-decisions ㉘b，CD-⑱ 深挖裁决留档件）：两统计在 /api/system/quick 与
/api/system/overview 修前均无产出面（quick.active_agents/quick.recent_sessions 恒
undefined=假渲染），CD-⑱ 按 CD 系列 wire 权威纪律把死显示清零，恢复=真源补现候选。
本拍按「别停工等批」铁律＋DEC-①~㉘a 自决先例自决取 B（语义随拍）先行执行：

- 活跃Agent=多智能体协调器 get_active_agents（5 分钟心跳活跃窗）行数，system_view=
  系统页全局统计口径（与 organs/knowledge 全局统计同轴；GET /api/brain/agents
  租户口径零变化）——「活跃」=活跃窗（心跳）语义，acp_agent_registry 探测注册是
  存量注册轴非活跃轴（生产实证：4 行注册 2 行，last_probe_at 停在 2026-10-11/14，
  按探测时刻计活跃恒 0=语义不符），随拍取协调器心跳轴（G1-⑤ 注册桥/WS 心跳/看门狗
  保活真接线面）。
- 近期会话=会话登记面（agent_sessions：title/message_count/last_activity_at，
  acp-proxy ws_chat 写入）近 24h 活动计数（窗口闭区间）——trajectory_sessions=
  任务轨迹面、hippo SessionManager=内存执行面，均非会话登记本体；窗口=24h 与
  ㉗a recent_count RECENT_WINDOW_S 同窗（契约锁两常量相等防漂移）。
- 逐键独立收集（_get_plugin_stats 家族 C2 模式）：单轴故障=该键缺席（logger.warning
  失败可见）禁 fabricate 0，UI 缺席='—' 占位（㉘a 同款，真 0 显示 0 禁遮蔽 CD-⑳/㉚）。

断言四层（禁自嗨恒真断言）：
①真源面：成功面真产出（种子 tmp 库系统视图跨租户计数/单一源 spy/窗口语义打桩时钟/
  常量同窗锁）＋失败面键缺席非假 0＋overview 加性键合并契约；
②UI 消费面：两卡两行复原（CD-⑱ 移除件复原语义）＋grid-cols-8＋Globe/Activity 复活
  ＋接口可选声明＋缺席占位守卫；
③行为桥：卡/行 value 表达式逐字提取经 node 真执行（真 0='0' 禁遮蔽/缺席='—'）＋
  真源真跑→node 渲染逐值对账；
④真源锚点：单一源（count_recent_sessions/get_active_agents system_view/merge 行）＋
  quick/health 零变化面。
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "system" / "page.tsx"
OVERVIEW_SRC = REPO / "src" / "api" / "system_overview.py"
COORD_SRC = REPO / "src" / "cortex" / "multi_agent_coord.py"
SI_SRC = REPO / "src" / "hippo" / "session_importer.py"

AGENT_SESSIONS_DDL = """CREATE TABLE agent_sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT,
    title TEXT,
    created_at REAL,
    last_activity_at REAL,
    message_count INTEGER,
    archived INTEGER DEFAULT 0)"""


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（历史记述/裁决注释里的键名不算消费形）。"""
    return re.sub(r"//.*$", "", _src(), flags=re.M)


@pytest.fixture()
def stats_env(tmp_path, monkeypatch):
    """隔离数据面：tmp opensoul.db（协调注册表+会话登记面同库）+ tmp SQLitePool。

    OPENSOUL_DB env→tmp（count_recent_sessions call-time 读取真消费）；
    settings.database_url→tmp（SQLitePool 缺省跟随）；pg_mod.db_pool→已连接 tmp 池
    （_get_agent_session_stats call-time import 真消费）。测试行绝不写生产 data/*.db。
    """
    from src.config import settings
    from src.database.postgres import SQLitePool

    db_file = tmp_path / "opensoul.db"
    monkeypatch.setenv("OPENSOUL_DB", str(db_file))
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_file}")

    pool = SQLitePool(str(db_file))
    asyncio.run(pool.connect())
    import src.database.postgres as pg_mod

    monkeypatch.setattr(pg_mod, "db_pool", pool)
    yield {"db_file": db_file, "pool": pool}
    asyncio.run(pool.disconnect())


def _seed_coord_rows(db_file, rows):
    """rows=[(tenant_id, agent_id, last_heartbeat)] 直写协调注册表（_ensure_table 先建）。"""
    conn = sqlite3.connect(str(db_file))
    try:
        for tenant, agent_id, hb in rows:
            conn.execute(
                "INSERT OR REPLACE INTO agent_registry "
                "(tenant_id, agent_id, agent_type, status, current_task, current_files, "
                "last_heartbeat, created_at) VALUES (?, ?, 'probe', 'idle', NULL, '[]', ?, ?)",
                (tenant, agent_id, hb, hb),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_sessions(db_file, rows):
    """rows=[(id, last_activity_at)] 直写会话登记面。"""
    conn = sqlite3.connect(str(db_file))
    try:
        conn.execute(AGENT_SESSIONS_DDL)
        for sid, last_at in rows:
            conn.execute(
                "INSERT INTO agent_sessions (id, agent_id, title, created_at, "
                "last_activity_at, message_count) VALUES (?, 'soulmate', 't', ?, ?, 2)",
                (sid, last_at, last_at),
            )
        conn.commit()
    finally:
        conn.close()


# ── ① 真源面 ───────────────────────────────────────────────────


def test_active_agents_system_view_counts_all_tenants(stats_env):
    """活跃Agent=协调器 get_active_agents（5min 心跳窗）行数：系统视图跨租户全量计数、
    过期心跳不计；租户口径零变化（对照面=GET /api/brain/agents 语义）。"""
    from src.cortex.multi_agent_coord import MultiAgentCoordinator

    now = time.time()
    db_file = stats_env["db_file"]
    # 先让 _ensure_table 建表（协调 DDL 单一源），再种子
    coord = MultiAgentCoordinator(stats_env["pool"], "default")
    asyncio.run(coord._ensure_table())
    _seed_coord_rows(
        db_file,
        [
            ("tenant-a", "ga-fresh-1", now - 10),
            ("tenant-b", "gb-fresh-2", now - 20),
            ("tenant-a", "ga-stale-3", now - 1000),  # 出活跃窗
        ],
    )
    from src.api import system_overview

    out = asyncio.run(system_overview._get_agent_session_stats())
    assert out["active_agents"] == 2, f"系统视图活跃窗计数漂移: {out}"
    # 租户口径零变化（brain /agents 语义）：tenant-a 只见自己的 1 行
    scoped = asyncio.run(MultiAgentCoordinator(stats_env["pool"], "tenant-a").get_active_agents())
    assert len(scoped) == 1 and scoped[0]["agent_id"] == "ga-fresh-1", (
        f"租户口径被 system_view 改写: {scoped}"
    )
    # 5 分钟窗语义（cutoff=300s 单一源）：窗边界 299s 在场、301s 出场
    _seed_coord_rows(
        db_file,
        [("tenant-a", "ga-edge-299", now - 299), ("tenant-a", "ga-edge-301", now - 301)],
    )
    out2 = asyncio.run(system_overview._get_agent_session_stats())
    assert out2["active_agents"] == 3, f"5 分钟窗边界漂移: {out2}"


def test_active_agents_single_source_get_active_agents(monkeypatch):
    """活跃Agent 计数=get_active_agents 本体长度（单一源 spy：另写 SQL 判法先炸）。"""
    from src.cortex import multi_agent_coord

    calls = []

    async def _fake(self, system_view=False):
        calls.append(system_view)
        return [{"agent_id": f"g{i}"} for i in range(3)]

    monkeypatch.setattr(multi_agent_coord.MultiAgentCoordinator, "get_active_agents", _fake)
    from src.api import system_overview

    out = asyncio.run(system_overview._get_agent_session_stats())
    assert out["active_agents"] == 3, f"计数未取 get_active_agents 长度: {out}"
    assert calls == [True], f"未走 system_view 系统视图口径: {calls}"


def test_recent_sessions_window_semantics(monkeypatch, tmp_path):
    """近期会话=会话登记面 24h 窗口闭区间计数（打桩时钟确定性）：窗内+边界计、窗外不计。"""
    from src.hippo import session_importer

    fixed_now = 1_800_000_000.0
    monkeypatch.setattr(session_importer.time, "time", lambda: fixed_now)
    db_file = tmp_path / "opensoul.db"
    window = session_importer.RECENT_SESSIONS_WINDOW_S
    _seed_sessions(
        db_file,
        [
            ("s-in-1", fixed_now - 10),
            ("s-edge", fixed_now - window),  # 边界闭区间=计
            ("s-out", fixed_now - window - 1),
            ("s-ancient", fixed_now - window * 10),
        ],
    )
    assert session_importer.count_recent_sessions(db_path=str(db_file)) == 2, (
        "窗口语义漂移（窗内+边界应计 2）"
    )


def test_recent_sessions_missing_store_zero_and_error_visible(tmp_path):
    """库/表缺席=空存储如实 0；库损坏如实抛（禁静默吞造 0，C2 失败可见）。"""
    from src.hippo import session_importer

    assert session_importer.count_recent_sessions(db_path=str(tmp_path / "nope.db")) == 0
    empty = tmp_path / "empty.db"
    sqlite3.connect(str(empty)).close()  # 文件在表缺席
    assert session_importer.count_recent_sessions(db_path=str(empty)) == 0
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database at all")
    with pytest.raises(sqlite3.DatabaseError):
        session_importer.count_recent_sessions(db_path=str(bad))


def test_window_constant_matches_recent_count_contract():
    """㉘b 近期会话窗==㉗a recent_count 窗（两常量相等防漂移，语义同一 24h）。"""
    from src.api import event_stream
    from src.hippo import session_importer

    assert session_importer.RECENT_SESSIONS_WINDOW_S == 86400
    assert session_importer.RECENT_SESSIONS_WINDOW_S == event_stream.RECENT_WINDOW_S, (
        "两套窗口解释漂移（㉗a/㉘b 同窗契约破）"
    )


def test_single_axis_failure_omits_key_not_fake_zero(monkeypatch):
    """逐键失败可见：单轴故障=该键缺席（禁 fabricate 0）也不吞另一轴真值。"""
    from src.cortex import multi_agent_coord
    from src.hippo import session_importer

    async def _boom(self, system_view=False):
        raise RuntimeError("dec28b probe")

    monkeypatch.setattr(multi_agent_coord.MultiAgentCoordinator, "get_active_agents", _boom)
    monkeypatch.setattr(session_importer, "count_recent_sessions", lambda **kw: 7)
    from src.api import system_overview

    out = asyncio.run(system_overview._get_agent_session_stats())
    assert "active_agents" not in out, f"故障轴 fabricate 键: {out}"
    assert out.get("recent_sessions") == 7, f"故障轴吞掉另一轴真值: {out}"

    def _bad(**kw):
        raise RuntimeError("dec28b probe 2")

    async def _ok(self, system_view=False):
        return []

    monkeypatch.setattr(multi_agent_coord.MultiAgentCoordinator, "get_active_agents", _ok)
    monkeypatch.setattr(session_importer, "count_recent_sessions", _bad)
    out2 = asyncio.run(system_overview._get_agent_session_stats())
    assert "recent_sessions" not in out2, f"故障轴 fabricate 键: {out2}"
    assert out2.get("active_agents") == 0, f"真 0 被吞（真 0 禁遮蔽 CD-⑳/㉚）: {out2}"


def test_overview_additive_merge_contract(monkeypatch):
    """overview 加性键合并契约：成功面真进响应（恰 12 键）；失败面键缺席非假 0。"""
    from src.api import system_overview

    async def _ok():
        return {"active_agents": 1, "recent_sessions": 2}

    monkeypatch.setattr(system_overview, "_get_agent_session_stats", _ok)
    resp = asyncio.run(system_overview.system_overview())
    assert resp["active_agents"] == 1 and resp["recent_sessions"] == 2, (
        f"加性键未进响应: {sorted(resp)}"
    )
    assert set(resp) == {
        "timestamp",
        "elapsed_ms",
        "version",
        "system_status",
        "uptime_seconds",
        "organs",
        "metrics",
        "knowledge",
        "plugins",
        "gland",
        "active_agents",
        "recent_sessions",
    }, f"overview 形状漂移（加性契约）: {sorted(resp)}"

    async def _boom():
        raise RuntimeError("dec28b probe 3")

    monkeypatch.setattr(system_overview, "_get_agent_session_stats", _boom)
    resp2 = asyncio.run(system_overview.system_overview())
    assert "active_agents" not in resp2 and "recent_sessions" not in resp2, (
        f"失败面 fabricate 键: {sorted(resp2)}"
    )


# ── ② UI 消费面 ─────────────────────────────────────────────────


def test_ui_restored_cards_and_rows():
    """CD-⑱ 移除件复原语义：统计条两卡＋数据统计面板两行，消费 stats 新键非 quick 旧形。"""
    code = _code()
    assert (
        "{ label: '活跃Agent', value: stats.active_agents != null ? stats.active_agents : '—'"
        in code
    ), "活跃Agent 卡未复活（复原面回退先炸）"
    assert (
        "{ label: '近期会话', value: stats.recent_sessions != null ? stats.recent_sessions : '—'"
        in code
    ), "近期会话卡未复活（复原面回退先炸）"
    assert (
        "{ label: '活跃 Agent', value: stats.active_agents != null ? stats.active_agents : '—' }"
        in code
    ), "数据统计「活跃 Agent」行未复活"
    assert (
        "{ label: '近期会话', value: stats.recent_sessions != null ? stats.recent_sessions : '—' }"
        in code
    ), "数据统计「近期会话」行未复活"
    # 修前消费形（quick 旧键）禁回潮
    assert "quick.active_agents" not in code and "quick.recent_sessions" not in code, (
        "quick 旧形消费回潮（打错面回归）"
    )


def test_ui_layout_and_icons_restored():
    """grid-cols-8 复原（CD-⑱ 8→6 反向）＋ Activity/Globe 图标复活为真消费（恰 2 卡）。"""
    code = _code()
    assert "grid-cols-2 sm:grid-cols-4 lg:grid-cols-8" in code, "统计条 grid 未复原 8 列"
    # 修前 CD-⑱ 6 列统计条组合禁回潮（器官健康网格的 md:grid-cols-4 lg:grid-cols-6
    # 是另一网格既有形，范围纪律不误伤）
    assert "grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2" not in code, (
        "CD-⑱ 6 列统计条残留（复原面回退）"
    )
    assert code.count("icon: <Activity") == 1, "Activity 消费点数漂移（活跃Agent 卡恰 1）"
    assert code.count("icon: <Globe") == 1, "Globe 消费点数漂移（近期会话卡恰 1）"


def test_ui_declaration_optional_and_honest():
    """OverviewStats 声明两键可选（收集失败=键缺席真语义，类型如实非恒在场谎言）。"""
    code = _code()
    assert "active_agents?: number;" in code, "active_agents 可选声明缺席"
    assert "recent_sessions?: number;" in code, "recent_sessions 可选声明缺席"


# ── ③ 行为桥（node 真执行 + 真源真跑） ──────────────────────────


def _card_exprs():
    code = _code()
    exprs = []
    for key in ("active_agents", "recent_sessions"):
        m = re.search(rf"stats\.{key} != null \? stats\.{key} : '—'", code)
        assert m, f"卡/行 value 表达式提取失败: {key}"
        exprs.append(m.group(0))
    return exprs


def test_value_expr_node_bridge_zero_honest_not_masked():
    """value 表达式逐字提取经 node 真执行：真 0='0'（禁遮蔽 CD-⑳/㉚）、缺席='—'。"""
    e1, e2 = _card_exprs()
    probe = (
        "const run = (stats, e) => Function('stats', 'return (' + e + ')')(stats);\n"
        "const cases = [\n"
        "  [{active_agents: 0, recent_sessions: 3}, " + json_repr(e1) + ", '0'],\n"
        "  [{active_agents: 0, recent_sessions: 3}, " + json_repr(e2) + ", '3'],\n"
        "  [{}, " + json_repr(e1) + ", '—'],\n"
        "  [{}, " + json_repr(e2) + ", '—'],\n"
        "];\n"
        "const out = cases.map(([s, e, x]) => [String(run(s, e)), x]);\n"
        "console.log(JSON.stringify(out));"
    )
    r = subprocess.run(["node", "-e", probe], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"node 执行失败: {r.stderr[:300]}"
    import json

    for got, expect in json.loads(r.stdout.strip()):
        assert got == expect, f"渲染形态漂移: got={got!r} expect={expect!r}"


def json_repr(s: str) -> str:
    import json

    return json.dumps(s)


def test_true_source_to_ui_pipeline(stats_env):
    """真源真跑→node 渲染逐值对账（行为桥）：种子真库两轴真值过 UI 表达式==展示。"""
    from src.cortex.multi_agent_coord import MultiAgentCoordinator

    now = time.time()
    coord = MultiAgentCoordinator(stats_env["pool"], "default")
    asyncio.run(coord._ensure_table())
    _seed_coord_rows(
        stats_env["db_file"], [("tenant-a", "ga-p1", now - 5), ("tenant-b", "gb-p2", now - 6)]
    )
    _seed_sessions(
        stats_env["db_file"], [("s-p1", now - 100), ("s-p2", now - 200), ("s-old", now - 999999)]
    )

    from src.api import system_overview

    out = asyncio.run(system_overview._get_agent_session_stats())
    assert out == {"active_agents": 2, "recent_sessions": 2}, f"真源真值漂移: {out}"
    e1, e2 = _card_exprs()
    probe = (
        "const run = (stats, e) => Function('stats', 'return (' + e + ')')(stats);\n"
        f"const s = {{active_agents: {out['active_agents']}, recent_sessions: {out['recent_sessions']}}};\n"
        f"console.log(JSON.stringify([String(run(s, {json_repr(e1)})), String(run(s, {json_repr(e2)}))]));"
    )
    r = subprocess.run(["node", "-e", probe], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"node 执行失败: {r.stderr[:300]}"
    import json

    assert json.loads(r.stdout.strip()) == ["2", "2"], "真源真值→渲染对账不符"


# ── ④ 真源锚点（单一源） + 零变化面 ─────────────────────────────


def test_single_source_anchors():
    """单一源锚点：count_recent_sessions 读口/get_active_agents system_view/merge 行。"""
    os_ = OVERVIEW_SRC.read_text(encoding="utf-8")
    assert "from src.hippo.session_importer import count_recent_sessions" in os_, (
        "会话读口单一源 import 缺席"
    )
    assert "coord.get_active_agents(system_view=True)" in os_, "活跃Agent 单一源调用缺席"
    assert "result.update(agent_stats)" in os_, "加性键 merge 行缺席"
    si = SI_SRC.read_text(encoding="utf-8")
    assert "def count_recent_sessions(" in si, "count_recent_sessions 定义缺席"
    cs = COORD_SRC.read_text(encoding="utf-8")
    assert "async def get_active_agents(self, system_view: bool = False)" in cs, (
        "system_view 口径参数缺席"
    )
    # SELECT 面单一（禁两套查询判法）：租户过滤只以条件段出现恰 1 处
    assert cs.count('" AND tenant_id = ?"') == 1, "查询判法分叉（两套 SQL 回潮）"


def test_quick_health_zero_change():
    """零变化面：/api/system/quick 仍恰 4 键、/api/health 仍恰 6 键（统计走 overview 非两口）。"""
    from src.api import system_overview
    from src.main import health as soul_health

    q = asyncio.run(system_overview.system_quick_status())
    assert set(q) == {"status", "db", "version", "timestamp"}, f"quick 形状变了须复挖: {sorted(q)}"
    h = asyncio.run(soul_health())
    assert "active_agents" not in h and "recent_sessions" not in h, "统计打进 health=打错面"
