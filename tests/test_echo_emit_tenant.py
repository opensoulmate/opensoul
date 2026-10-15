"""F3b-⑨b-②b-7 echo 3 调用点接派生——push_event 带 bind_identity 派生租户键。

F3b-⑨b-② 留档候选「其余 HTTP 调用点分 organ 拆拍」第七件=echo 模块全 3 点
（F3b-⑨ census：echo 3 全在 src/api/echo.py，
message_sent/template_created/template_sent 各 1，三个 handler）。

修前：3 调用点不传 tenant_id→事件 dict 无键→record() 'default' 兜底
（timeline/store.py record tenant_val），非 default 租户身份用户的 echo
动作事件落 'default'→被自己的租户过滤查询挡掉=自动作自我不可见
（F3b-⑨ live 硬证同型、②b-1 vein/②b-2 immune/②b-3 mirror/②b-4 healer/
②b-5 pipeline/②b-6 marrow 同型）。

数据面全 tmp（USERS_DB/ENTERPRISE_DB/tmp TimelineStore/死端口 _BASE/
TemplateEngine tmp db/MessageDispatcher 单例桩），绝不写生产
opensoul.db/enterprise.db/timeline.db/echo/templates.db、不打 live :8090。
"""

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from jose import jwt

from src.config import settings
from src.nerve import event_bridge

ENTERPRISE_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""


def _ent_token(uid, username="u"):
    payload = {
        "sub": str(uid),
        "username": username,
        "scope": "enterprise",
        "exp": datetime.now(UTC) + timedelta(minutes=10),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _insert_ent_user(db, username, tenant, is_active=1):
    from src.services import tenant_binding as tb

    tb.ensure_user_tenant_columns(str(db))
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password, is_active, tenant_id) VALUES (?, ?, ?, ?)",
            (username, "hashed", is_active, tenant),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _rows(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute("SELECT * FROM events ORDER BY timestamp, event_id")]
    con.close()
    return out


def _auth(token):
    return {"Authorization": "Bearer " + token} if token else {}


class _StubDispatcher:
    """MessageDispatcher 桩——handler→push_event 接线是本拍被测物，
    消息投递本体（console/webhook/网络）不进测试面。"""

    def send(self, channel, title, content, target, priority, tenant_id=None):
        # tenant_id=DEC-⑦-② 行租户键形参（桩只接收不投递，接线是被测物）
        return SimpleNamespace(
            success=True,
            msg_id="msg_stub_1",
            channel=getattr(channel, "value", str(channel)),
            error=None,
        )


@pytest.fixture()
def echo_env(tmp_path, monkeypatch):
    """echo router + tmp 身份库/TimelineStore/templates.db（绝不碰生产数据面）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.echo as echo_api
    from src.echo.templates import TemplateEngine
    from src.timeline.store import TimelineStore

    # 投递换桩（消息投递本体不进测试面，接线是被测物）
    monkeypatch.setattr(echo_api, "dispatcher", _StubDispatcher())
    # 模板引擎=tmp db（生产 echo/templates.db 绝不写）
    monkeypatch.setattr(echo_api, "template_engine", TemplateEngine(db_path=str(tmp_path / "t.db")))

    # 身份库隔离（bind_identity 真源绝不碰生产）
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    ent_db = tmp_path / "enterprise.db"
    conn = sqlite3.connect(ent_db)
    conn.execute(ENTERPRISE_DDL)
    conn.commit()
    conn.close()

    # timeline 行归属真源=tmp（push_event._timeline 注入，绝不写生产 timeline.db）
    tl_db = str(tmp_path / "timeline.db")
    store = TimelineStore(tl_db)
    monkeypatch.setattr(event_bridge.push_event, "_timeline", store, raising=False)
    monkeypatch.setattr(event_bridge.emit, "_timeline", store, raising=False)
    # 死端口：nerve publish 快速连接失败（被 push_event 自身吞掉），绝不打 live
    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9")

    from src.api import event_stream

    event_stream._event_buffer.clear()

    app = FastAPI()
    app.include_router(echo_api.router, prefix="/api/echo")
    with TestClient(app) as c:
        yield c, tl_db, ent_db, event_stream, echo_api, tmp_path
    event_stream._event_buffer.clear()


def _send(c, headers=None):
    return c.post(
        "/api/echo/send",
        json={"channel": "console", "title": "T", "content": "C"},
        headers=headers or {},
    )


def _create_tpl(c, headers=None):
    return c.post(
        "/api/echo/templates",
        json={"name": "n", "title_template": "TT", "content_template": "CC"},
        headers=headers or {},
    )


def _send_tpl(c, echo_api, headers=None):
    tpl = echo_api.template_engine.create(
        name="t", title_template="Hello", content_template="World"
    )
    return c.post(
        f"/api/echo/templates/{tpl.template_id}/send",
        json={"variables": {}},
        headers=headers or {},
    )


# ── ① 3 调用点真接派生 → record 行归属 ───────────────────────────


class TestEchoEmitTenantWiring:
    def test_message_sent_event_row_tenant_derived(self, echo_env):
        """认证派生租户→message_sent 事件行落派生租户（修前恒 'default' 自我不可见）。"""
        c, tl_db, ent_db, _es, echo_api, _tp = echo_env
        uid = _insert_ent_user(ent_db, "alice", "tenant-echo-a")
        r = _send(c, _auth(_ent_token(uid, "alice")))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "message_sent"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-echo-a"  # 非 'default'=真接派生
        assert rows[0]["organ"] == "echo"

    def test_template_created_event_row_tenant_derived(self, echo_env):
        c, tl_db, ent_db, _es, _m, _tp = echo_env
        uid = _insert_ent_user(ent_db, "bob", "tenant-echo-b")
        r = _create_tpl(c, _auth(_ent_token(uid, "bob")))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "template_created"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-echo-b"

    def test_template_sent_event_row_tenant_derived(self, echo_env):
        c, tl_db, ent_db, _es, echo_api, _tp = echo_env
        uid = _insert_ent_user(ent_db, "carol", "tenant-echo-c")
        r = _send_tpl(c, echo_api, _auth(_ent_token(uid, "carol")))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "template_sent"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-echo-c"

    def test_buffer_dict_carries_tenant_key(self, echo_env):
        """事件 dict 自带键（bus/缓冲/record 同源注入）——⑨b-① 三处注入真流经调用点。"""
        c, _tl, ent_db, es, _m, _tp = echo_env
        uid = _insert_ent_user(ent_db, "iris", "tenant-echo-i")
        _send(c, _auth(_ent_token(uid, "iris")))
        buffered = [e for e in es._event_buffer if e.get("type") == "message_sent"]
        assert len(buffered) == 1
        assert buffered[0]["tenant_id"] == "tenant-echo-i"

    def test_dual_tenant_rows_isolated(self, echo_env):
        """双租户各自落行互不串键。"""
        c, tl_db, ent_db, _es, echo_api, _tp = echo_env
        uid_a = _insert_ent_user(ent_db, "u_a", "tenant-dual-a")
        uid_b = _insert_ent_user(ent_db, "u_b", "tenant-dual-b")
        time.sleep(0.02)  # evt id 含 ms 时间戳，防同 ms 去重假阴（②b-1 坑延续）
        assert _send(c, _auth(_ent_token(uid_a, "u_a"))).status_code == 200
        time.sleep(0.02)
        assert _send_tpl(c, echo_api, _auth(_ent_token(uid_b, "u_b"))).status_code == 200
        rows = _rows(tl_db)
        ms = [x for x in rows if x["event_type"] == "message_sent"]
        ts = [x for x in rows if x["event_type"] == "template_sent"]
        assert ms[0]["tenant_id"] == "tenant-dual-a"
        assert ts[0]["tenant_id"] == "tenant-dual-b"

    def test_bad_token_send_401_first_zero_event(self, echo_env):
        """身份证明先于一切（F3b-⑤ 纪律）：坏 token 在消息投递/任何副作用之前即 401，零事件。"""
        c, tl_db, _ent, _es, _m, _tp = echo_env
        r = _send(c, _auth("not-a-jwt-token"))
        assert r.status_code == 401
        assert _rows(tl_db) == []

    def test_bad_token_create_tpl_401_zero_event(self, echo_env):
        c, tl_db, _ent, _es, _m, _tp = echo_env
        r = _create_tpl(c, _auth("not-a-jwt-token"))
        assert r.status_code == 401
        assert _rows(tl_db) == []

    def test_bad_token_send_tpl_401_zero_event(self, echo_env):
        c, tl_db, _ent, _es, echo_api, _tp = echo_env
        r = _send_tpl(c, echo_api, _auth("not-a-jwt-token"))
        assert r.status_code == 401
        assert _rows(tl_db) == []


# ── ② 不变面（契约锁死） ─────────────────────────────────────────


class TestEchoEmitTenantInvariants:
    def test_send_anonymous_default_contract_unchanged(self, echo_env):
        """匿名='default' 行值不变+send 响应契约逐键不变（接派生不改响应面）。"""
        c, tl_db, _ent, _es, _m, _tp = echo_env
        r = _send(c)
        assert r.status_code == 200
        data = r.json()
        assert sorted(data.keys()) == ["channel", "error", "msg_id", "success"]
        rows = [x for x in _rows(tl_db) if x["event_type"] == "message_sent"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"

    def test_create_tpl_anonymous_default_contract(self, echo_env):
        """/templates 匿名契约逐键不变+行 'default'。"""
        c, tl_db, _ent, _es, _m, _tp = echo_env
        r = _create_tpl(c)
        assert r.status_code == 200
        data = r.json()
        assert sorted(data.keys()) == ["name", "template_id", "variables"]
        rows = [x for x in _rows(tl_db) if x["event_type"] == "template_created"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"

    def test_send_tpl_anonymous_default_contract(self, echo_env):
        """/templates/{id}/send 匿名契约逐键不变+行 'default'。"""
        c, tl_db, _ent, _es, echo_api, _tp = echo_env
        r = _send_tpl(c, echo_api)
        assert r.status_code == 200
        data = r.json()
        assert sorted(data.keys()) == [
            "channel",
            "error",
            "msg_id",
            "rendered_content",
            "rendered_title",
            "success",
        ]
        rows = [x for x in _rows(tl_db) if x["event_type"] == "template_sent"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"

    def test_all_three_call_sites_wired(self):
        """静态接线普查：5 handler 全带派生（send/broadcast/send_from_template/
        create_template/message_history——⑦-⑬ /history 读面收编）、7 注入点全传
        租户键（3 push_event+3 消息行+1 history 读面，DEC-⑦-② 消息行归属）
        （写了必须接线）。"""
        pipe = chr(124)
        src = (Path(__file__).parent.parent / "src" / "api" / "echo.py").read_text(encoding="utf-8")
        assert src.count("identity = bind_identity(authorization)") == 5
        assert src.count("tenant_id=identity.tenant_id") == 7
        assert src.count("push_event(") == 3
        assert src.count("authorization: str " + pipe + " None = Header(None)") == 5  # 竖线完好探针
        starts = []
        i = 0
        while True:
            j = src.find('"organ": "echo"', i)
            if j < 0:
                break
            starts.append(j)
            i = j + 1
        assert len(starts) == 3
        for j in starts:
            # 每个 push_event 块尾部都带租户键注入（禁只改声明不接线）
            assert "tenant_id=identity.tenant_id" in src[j : j + 900]
