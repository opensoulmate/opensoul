"""G1-⑤b check_file_conflict·send_message·get_messages 消费面接线测试。

修前（G1-⑤ 诚实边界留档）：三方法全仓零生产调用（wired-but-not-functional）。
本文件验证 collab WS 协议真实流程的三方法消费闭环：

①声明面：heartbeat 事件携 current_task/current_files（工作集数据真源）+
  keep-alive 保留语义（COALESCE，看门狗节拍不洗声明）+ offline 清空
②check_file_conflict 真消费：check_conflicts 事件→conflict_check 帧
  （同租户活跃代理工作集重叠、零自冲突、跨租户零泄露）
③send_message 真消费：冲突通知落协调收件箱（message_type=conflict）
④get_messages 真消费：inbox 拉取/连接接取消息，拉取即送达标 read
⑤纪律：非法载荷如实 error 帧、桥故障如实 error 不谎报 ok、未注册绝不
  fabricate 协调行、旁路纪律不吃 WS 主循环。

全部 tmp 独立库（AGENT_COLLAB_DB/USERS_DB/ENTERPRISE_DB/settings.database_url
换 tmp SQLitePool），测试行绝不写生产 data/*.db。
"""

import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from src.config import settings

ENT_USERS_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""


def _tok(uid):
    payload = {
        "sub": str(uid),
        "scope": "enterprise",
        "exp": datetime.now(UTC) + timedelta(minutes=10),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _auth(uid):
    return {"Authorization": "Bearer " + _tok(uid)}


def _rows(db, sql, params=()):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _coord_row(db, agent_id):
    try:
        rows = _rows(
            db,
            "SELECT tenant_id, agent_id, status, current_task, current_files "
            "FROM agent_registry WHERE agent_id = ?",
            (agent_id,),
        )
    except sqlite3.OperationalError:
        return None
    return rows[0] if rows else None


def _wait_row(db, agent_id, pred, timeout=5.0):
    """WS 写路径门户线程异步处理：短轮询等行到位（超时如实返回断言必炸）。"""
    deadline = time.time() + timeout
    row = _coord_row(db, agent_id)
    while time.time() < deadline:
        if row is not None and pred(row):
            return row
        time.sleep(0.05)
        row = _coord_row(db, agent_id)
    return row


def _inbox_rows(db, to_agent):
    return _rows(
        db,
        "SELECT * FROM agent_coord_messages WHERE to_agent = ? ORDER BY id",
        (to_agent,),
    )


def _wait_inbox(db, to_agent, n, timeout=5.0):
    deadline = time.time() + timeout
    rows = _inbox_rows(db, to_agent)
    while time.time() < deadline:
        if len(rows) >= n:
            return rows
        time.sleep(0.05)
        rows = _inbox_rows(db, to_agent)
    return rows


def _wait_inbox_read(db, to_agent, idx, timeout=5.0):
    """轮询收件箱第 idx 行 read 标记到位（送达帧先于标 read 的 UPDATE 落库=竞态，
    WS 写路径门户线程异步；超时如实返回仍由调用方断言必炸）。"""
    deadline = time.time() + timeout
    rows = _inbox_rows(db, to_agent)
    while time.time() < deadline:
        if len(rows) > idx and rows[idx]["read"] == 1:
            return rows
        time.sleep(0.05)
        rows = _inbox_rows(db, to_agent)
    return rows


def _drain_pong(ws):
    """ping/pong 同步屏障：此前若有未取帧会先到（零噪音断言用）。"""
    ws.send_json({"event": "ping"})
    return ws.receive_json()


@pytest.fixture()
def coord_env(tmp_path, monkeypatch):
    """隔离数据面：tmp agent_collaboration.db / enterprise.db / opensoul.db
    （协调注册表+协调收件箱真源）+ tmp SQLitePool 换 src.database.postgres.db_pool
    （_coord call-time import 真消费）。uid 1=tenant-a、uid 2=tenant-b、uid 3=default。"""
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'opensoul.db'}")

    conn = sqlite3.connect(str(tmp_path / "enterprise.db"))
    conn.execute(ENT_USERS_DDL)
    conn.execute("INSERT INTO users (username, password, tenant_id) VALUES ('ua', 'h', 'tenant-a')")
    conn.execute("INSERT INTO users (username, password, tenant_id) VALUES ('ub', 'h', 'tenant-b')")
    conn.execute("INSERT INTO users (username, password, tenant_id) VALUES ('ud', 'h', 'default')")
    conn.commit()
    conn.close()

    monkeypatch.setenv("AGENT_COLLAB_DB", str(tmp_path / "agent_collaboration.db"))
    import src.api.agent_collaboration as collab

    monkeypatch.setattr(collab, "DB_PATH", tmp_path / "agent_collaboration.db")
    collab._init_db()

    import src.database.postgres as pg_mod
    from src.database.postgres import SQLitePool

    tmp_pool = SQLitePool()
    monkeypatch.setattr(pg_mod, "db_pool", tmp_pool)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    from src.api.brain import router as brain_router

    app = FastAPI(lifespan=_lifespan)
    app.include_router(collab.router, prefix="/api/collab")
    app.include_router(brain_router, prefix="/api/brain")
    with TestClient(app) as c:
        yield {
            "client": c,
            "collab": collab,
            "coord_db": tmp_path / "opensoul.db",
            "collab_db": tmp_path / "agent_collaboration.db",
        }


def _register(client, agent_id, headers=None, role="runner"):
    r = client.post(
        "/api/collab/register",
        json={"agent_id": agent_id, "name": agent_id, "role": role},
        headers=headers,
    )
    assert r.status_code == 200
    return r.json()["token"]


# ── ① 声明面：heartbeat current_task/current_files ─────────────────────


class TestDeclareFace:
    def test_heartbeat_declares_working_set(self, coord_env):
        client = coord_env["client"]
        token = _register(client, "g15b-decl")
        with client.websocket_connect(f"/api/collab/ws/agent/g15b-decl?token={token}") as ws:
            ws.send_json(
                {
                    "event": "heartbeat",
                    "current_task": "task-x",
                    "current_files": ["a.py", "b.py"],
                }
            )
            row = _wait_row(
                coord_env["coord_db"],
                "g15b-decl",
                lambda r: json.loads(r["current_files"] or "[]") == ["a.py", "b.py"],
            )
            assert row is not None, "heartbeat 声明未落协调行"
            assert row["current_task"] == "task-x"
            assert json.loads(row["current_files"]) == ["a.py", "b.py"]

    def test_keepalive_preserves_declared_set(self, coord_env, monkeypatch):
        """COALESCE 语义：keep-alive 节拍缺省 None=保留现值（修前恒写 ''/'[]'
        洗掉声明=check_file_conflict 数据真源必空）。"""
        client = coord_env["client"]
        collab = coord_env["collab"]
        token = _register(client, "g15b-keep")
        ticks = []
        real = collab._coord_heartbeat

        async def spy(tenant_id, agent_id, status="active", current_task=None, current_files=None):
            ticks.append((tenant_id, agent_id, current_task, current_files))
            await real(
                tenant_id,
                agent_id,
                status=status,
                current_task=current_task,
                current_files=current_files,
            )

        monkeypatch.setattr(collab, "_coord_heartbeat", spy)
        monkeypatch.setattr(collab, "TOKEN_REVALIDATE_INTERVAL_S", 0.05)
        with client.websocket_connect(f"/api/collab/ws/agent/g15b-keep?token={token}") as ws:
            ws.send_json(
                {
                    "event": "heartbeat",
                    "current_task": "task-keep",
                    "current_files": ["k.py"],
                }
            )
            _wait_row(
                coord_env["coord_db"],
                "g15b-keep",
                lambda r: json.loads(r["current_files"] or "[]") == ["k.py"],
            )
            # 等看门狗 keep-alive 节拍（缺省 None 透传）真跑几拍
            deadline = time.time() + 5.0
            while time.time() < deadline:
                bare = [t for t in ticks if t[1] == "g15b-keep" and t[2] is None and t[3] is None]
                if len(bare) >= 2:
                    break
                time.sleep(0.05)
            bare = [t for t in ticks if t[1] == "g15b-keep" and t[2] is None and t[3] is None]
            assert len(bare) >= 2, ticks
            row = _coord_row(coord_env["coord_db"], "g15b-keep")
            assert row is not None
            assert json.loads(row["current_files"]) == ["k.py"], "keep-alive 洗掉已声明工作集"
            assert row["current_task"] == "task-keep"

    def test_offline_clears_working_set(self, coord_env):
        client = coord_env["client"]
        token = _register(client, "g15b-off")
        with client.websocket_connect(f"/api/collab/ws/agent/g15b-off?token={token}") as ws:
            ws.send_json({"event": "heartbeat", "current_task": "t", "current_files": ["o.py"]})
            _wait_row(
                coord_env["coord_db"],
                "g15b-off",
                lambda r: json.loads(r["current_files"] or "[]") == ["o.py"],
            )
        # 断开 offline=显式清空工作集（heartbeat_sync 绑 ''/'[]'）
        row = _wait_row(
            coord_env["coord_db"],
            "g15b-off",
            lambda r: r["status"] == "offline" and (r["current_files"] or "[]") == "[]",
        )
        assert row is not None
        assert row["current_task"] == ""

    def test_invalid_declare_error_frame_and_skip(self, coord_env):
        client = coord_env["client"]
        token = _register(client, "g15b-bad")
        with client.websocket_connect(f"/api/collab/ws/agent/g15b-bad?token={token}") as ws:
            ws.send_json({"event": "heartbeat", "current_files": "not-a-list"})
            frame = ws.receive_json()
            assert frame["event"] == "error"
            assert "current_files" in frame["detail"]
            # liveness 与声明语义分开：心跳照常（pong 正常），声明被拒不落行
            assert _drain_pong(ws) == {"event": "pong"}
            time.sleep(0.2)
            row = _coord_row(coord_env["coord_db"], "g15b-bad")
            assert row is not None  # 连接=活跃照常
            assert json.loads(row["current_files"] or "[]") == []  # 声明被拒零落行

    def test_explicit_empty_clears_declare(self, coord_env):
        client = coord_env["client"]
        token = _register(client, "g15b-clr")
        with client.websocket_connect(f"/api/collab/ws/agent/g15b-clr?token={token}") as ws:
            ws.send_json({"event": "heartbeat", "current_files": ["c.py"]})
            _wait_row(
                coord_env["coord_db"],
                "g15b-clr",
                lambda r: json.loads(r["current_files"] or "[]") == ["c.py"],
            )
            ws.send_json({"event": "heartbeat", "current_files": []})
            row = _wait_row(
                coord_env["coord_db"],
                "g15b-clr",
                lambda r: json.loads(r["current_files"] or "[]") == [],
            )
            assert row is not None


# ── ② check_file_conflict 真消费 ──────────────────────────────────────


class TestConflictCheck:
    def test_check_conflicts_detects_overlap_and_ack(self, coord_env):
        client = coord_env["client"]
        tok_a = _register(client, "g15b-a")
        tok_b = _register(client, "g15b-b")
        with client.websocket_connect(f"/api/collab/ws/agent/g15b-a?token={tok_a}") as wa:
            wa.send_json(
                {
                    "event": "heartbeat",
                    "current_task": "task-x",
                    "current_files": ["a.py", "b.py"],
                }
            )
            _wait_row(
                coord_env["coord_db"],
                "g15b-a",
                lambda r: json.loads(r["current_files"] or "[]") == ["a.py", "b.py"],
            )
            with client.websocket_connect(f"/api/collab/ws/agent/g15b-b?token={tok_b}") as wb:
                wb.send_json({"event": "check_conflicts", "files": ["b.py", "z.py"]})
                ack = wb.receive_json()
                assert ack["event"] == "conflict_check"
                assert ack["files"] == ["b.py", "z.py"]
                assert ack["conflicts"] == [
                    {
                        "agent_id": "g15b-a",
                        "conflicting_files": ["b.py"],
                        "their_task": "task-x",
                    }
                ]
                # 零重叠查询零噪音：ack 空冲突后无任何自发帧
                wb.send_json({"event": "check_conflicts", "files": ["q.py"]})
                ack2 = wb.receive_json()
                assert ack2["conflicts"] == []
                assert _drain_pong(wb) == {"event": "pong"}

    def test_no_self_conflict(self, coord_env):
        client = coord_env["client"]
        token = _register(client, "g15b-self")
        with client.websocket_connect(f"/api/collab/ws/agent/g15b-self?token={token}") as ws:
            ws.send_json({"event": "heartbeat", "current_files": ["s.py"]})
            _wait_row(
                coord_env["coord_db"],
                "g15b-self",
                lambda r: json.loads(r["current_files"] or "[]") == ["s.py"],
            )
            ws.send_json({"event": "check_conflicts", "files": ["s.py"]})
            ack = ws.receive_json()
            assert ack["event"] == "conflict_check"
            assert ack["conflicts"] == []  # 自己的工作集不算冲突

    def test_tenant_isolation_zero_leak(self, coord_env):
        client = coord_env["client"]
        tok_a = _register(client, "g15b-ta", headers=_auth(1))
        tok_b = _register(client, "g15b-tb", headers=_auth(2))
        with client.websocket_connect(f"/api/collab/ws/agent/g15b-ta?token={tok_a}") as wa:
            wa.send_json({"event": "heartbeat", "current_files": ["shared.py"]})
            _wait_row(
                coord_env["coord_db"],
                "g15b-ta",
                lambda r: json.loads(r["current_files"] or "[]") == ["shared.py"],
            )
            with client.websocket_connect(f"/api/collab/ws/agent/g15b-tb?token={tok_b}") as wb:
                wb.send_json({"event": "check_conflicts", "files": ["shared.py"]})
                ack = wb.receive_json()
                assert ack["conflicts"] == []  # 跨租户视图零泄露


# ── ③④ send_message / get_messages 真消费（协调收件箱）────────────────


class TestCoordInbox:
    def test_conflict_notice_lands_inbox_and_pull_marks_read(self, coord_env):
        client = coord_env["client"]
        tok_a = _register(client, "g15b-na")
        tok_b = _register(client, "g15b-nb")
        with client.websocket_connect(f"/api/collab/ws/agent/g15b-na?token={tok_a}") as wa:
            wa.send_json({"event": "heartbeat", "current_files": ["n.py"]})
            _wait_row(
                coord_env["coord_db"],
                "g15b-na",
                lambda r: json.loads(r["current_files"] or "[]") == ["n.py"],
            )
            with client.websocket_connect(f"/api/collab/ws/agent/g15b-nb?token={tok_b}") as wb:
                wb.send_json({"event": "check_conflicts", "files": ["n.py"]})
                assert wb.receive_json()["conflicts"] != []
            # send_message 真消费：冲突通知落 A 的协调收件箱
            rows = _wait_inbox(coord_env["coord_db"], "g15b-na", 1)
            assert len(rows) == 1
            assert rows[0]["from_agent"] == "g15b-nb"
            assert rows[0]["to_agent"] == "g15b-na"
            assert rows[0]["message_type"] == "conflict"
            content = json.loads(rows[0]["content"])
            assert content["files"] == ["n.py"]
            # get_messages 真消费：inbox 拉取真回消息
            wa.send_json({"event": "inbox"})
            frame = wa.receive_json()
            assert frame["event"] == "inbox"
            assert len(frame["messages"]) == 1
            assert frame["messages"][0]["from_agent"] == "g15b-nb"
            assert frame["messages"][0]["message_type"] == "conflict"
            # 拉取即送达：unread_only 缺省 True 的二次拉取为空
            wa.send_json({"event": "inbox"})
            assert wa.receive_json()["messages"] == []
            # 显式 False=含已读历史（read 标记闭环）
            wa.send_json({"event": "inbox", "unread_only": False})
            hist = wa.receive_json()["messages"]
            assert len(hist) == 1
            assert _drain_pong(wa) == {"event": "pong"}

    def test_connect_flush_delivers_backlog(self, coord_env):
        """离线期积压：连接即接取（coord_message 帧）+标 read。"""
        client = coord_env["client"]
        tok_a = _register(client, "g15b-fa")
        tok_b = _register(client, "g15b-fb")
        # A 先声明后离线
        with client.websocket_connect(f"/api/collab/ws/agent/g15b-fa?token={tok_a}") as wa:
            wa.send_json({"event": "heartbeat", "current_files": ["f.py"]})
            _wait_row(
                coord_env["coord_db"],
                "g15b-fa",
                lambda r: json.loads(r["current_files"] or "[]") == ["f.py"],
            )
        # A 离线但工作集声明因 offline 清空——重新声明（模拟离线前有在办文件）
        conn = sqlite3.connect(str(coord_env["coord_db"]))
        conn.execute(
            "UPDATE agent_registry SET current_files = ?, current_task = ? WHERE agent_id = ?",
            (json.dumps(["f.py"]), "task-f", "g15b-fa"),
        )
        conn.commit()
        conn.close()
        # B 冲突检查→通知落 A 收件箱（A 离线）
        with client.websocket_connect(f"/api/collab/ws/agent/g15b-fb?token={tok_b}") as wb:
            wb.send_json({"event": "check_conflicts", "files": ["f.py"]})
            assert wb.receive_json()["conflicts"] != []
        rows = _wait_inbox(coord_env["coord_db"], "g15b-fa", 1)
        assert len(rows) == 1 and rows[0]["read"] == 0
        # A 重连：连接接取积压消息
        with client.websocket_connect(f"/api/collab/ws/agent/g15b-fa?token={tok_a}") as wa:
            frame = wa.receive_json()
            assert frame["event"] == "coord_message"
            assert frame["from_agent"] == "g15b-fb"
            assert frame["message_type"] == "conflict"
            assert json.loads(frame["content"])["files"] == ["f.py"]
            # 接取即送达：read 已标（送达帧先于标 read 的 UPDATE 落库=竞态；修前
            # lambda r: True 恒真空等后即刻断言，并发下偶发 0==1 假失败=本文件
            # 并发 flake 根因，2026-10-21 收口）——真谓词轮询到 read==1。
            rows = _wait_inbox_read(coord_env["coord_db"], "g15b-fa", 0)
            assert rows[0]["read"] == 1
            # 无积压时连接零噪音
            assert _drain_pong(wa) == {"event": "pong"}

    def test_inbox_invalid_unread_only_error(self, coord_env):
        client = coord_env["client"]
        token = _register(client, "g15b-ib")
        with client.websocket_connect(f"/api/collab/ws/agent/g15b-ib?token={token}") as ws:
            ws.send_json({"event": "inbox", "unread_only": "yes"})
            frame = ws.receive_json()
            assert frame["event"] == "error"
            assert "unread_only" in frame["detail"]
            assert _drain_pong(ws) == {"event": "pong"}


# ── ⑤ 纪律：非法载荷/旁路/未注册/静态接线 ─────────────────────────────


class TestContractAndFaults:
    def test_invalid_check_conflicts_payload_error(self, coord_env):
        client = coord_env["client"]
        token = _register(client, "g15b-ic")
        with client.websocket_connect(f"/api/collab/ws/agent/g15b-ic?token={token}") as ws:
            for bad in (None, [], "x.py", ["ok.py", 3]):
                ws.send_json({"event": "check_conflicts", "files": bad})
                frame = ws.receive_json()
                assert frame["event"] == "error"
                assert "files" in frame["detail"]
            assert _drain_pong(ws) == {"event": "pong"}

    def test_ghost_never_fabricates_registry_row(self, coord_env):
        client = coord_env["client"]
        with client.websocket_connect("/api/collab/ws/agent/g15b-ghost") as ws:
            ws.send_json({"event": "heartbeat", "current_files": ["g.py"]})
            ws.send_json({"event": "check_conflicts", "files": ["g.py"]})
            ack = ws.receive_json()
            # 未注册 agent 检查照常服务（同租户 'default' 视图），但绝不 fabricate 行
            assert ack["event"] == "conflict_check"
            assert _drain_pong(ws) == {"event": "pong"}
        time.sleep(0.1)
        assert _coord_row(coord_env["coord_db"], "g15b-ghost") is None

    def test_bridge_fault_isolation(self, coord_env, monkeypatch):
        """桥故障如实 error 帧禁谎报 ok（C2），WS 主循环照常（旁路纪律）。"""
        import src.database.postgres as pg_mod

        class BrokenPool:
            async def execute(self, *a, **k):
                raise RuntimeError("coord bridge probe failure")

            async def fetch(self, *a, **k):
                raise RuntimeError("coord bridge probe failure")

        monkeypatch.setattr(pg_mod, "db_pool", BrokenPool())
        client = coord_env["client"]
        token = _register(client, "g15b-fault")
        with client.websocket_connect(f"/api/collab/ws/agent/g15b-fault?token={token}") as ws:
            ws.send_json({"event": "check_conflicts", "files": ["x.py"]})
            frame = ws.receive_json()
            assert frame["event"] == "error"
            assert "coord conflict check failed" in frame["detail"]
            assert "probe failure" in frame["detail"]  # 真因如实
            ws.send_json({"event": "inbox"})
            frame = ws.receive_json()
            assert frame["event"] == "error"
            assert "coord inbox fetch failed" in frame["detail"]
            assert _drain_pong(ws) == {"event": "pong"}  # 主循环不吃

    def test_static_wiring(self, coord_env):
        """写了≠接线了：三方法真消费点静态普查。"""
        src = open("src/api/agent_collaboration.py").read()
        coord_src = open("src/cortex/multi_agent_coord.py").read()
        assert "async def check_file_conflict(" in coord_src
        assert "async def send_message(" in coord_src
        assert "async def get_messages(" in coord_src
        assert "async def mark_messages_read(" in coord_src
        assert ".check_file_conflict(agent_id, target_files)" in src
        assert ".send_message(" in src
        assert ".get_messages(agent_id, unread_only=unread_only)" in src
        assert "mark_messages_read(agent_id)" in src
        # 事件面真接线（check_conflicts/inbox/声明）
        assert 'event_type == "check_conflicts"' in src
        assert 'event_type == "inbox"' in src
        assert "current_files=current_files," in src
