"""F3b-⑨b-②b-8 剩余零争议 HTTP 调用点 3 处接派生——push_event 带 bind_identity 派生租户键。

F3b-⑨b-② 留档候选「其余 HTTP 调用点分 organ 拆拍」第八件=零争议剩余三点
（F3b-⑨ census：link create_connector 1 / system_overview bootstrap 1 /
cortex_enhanced graphrag_build 1）。link receive_webhook 1 + soma_connector 4 +
link_gateway 3（helper 汇聚点+HTTP+WS）+ 中间件 1 共 9 点全归 F3b-⑨ ③
「非 HTTP 面/外部调用方归属语义待拍」，本拍不动——receive_webhook 是外部
webhook 入口，按用户端点语义接 401 会打断外部 webhook（真回归），③ 拍板前
行为字节不变（测试锁死）。

修前：3 调用点不传 tenant_id→事件 dict 无键→record() 'default' 兜底
（timeline/store.py record tenant_val），非 default 租户身份用户的动作事件落
'default'→被自己的租户过滤查询挡掉=自动作自我不可见（⑨ 审计同型、
②b-1~②b-7 同型）。

数据面全 tmp（USERS_DB/ENTERPRISE_DB/tmp TimelineStore/死端口 _BASE/
bootstrap 与 graphrag 桩/link manager 新实例），绝不写生产
opensoul.db/enterprise.db/timeline.db、不打 live :8090。
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

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

ROOT = Path(__file__).resolve().parent.parent


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


def _rows(db_path, event_type=None):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    sql = "SELECT * FROM events"
    args: tuple = ()
    if event_type:
        sql += " WHERE event_type = ?"
        args = (event_type,)
    sql += " ORDER BY timestamp, event_id"
    out = [dict(r) for r in con.execute(sql, args)]
    con.close()
    return out


def _auth(token):
    return {"Authorization": "Bearer " + token} if token else {}


class _StubBootstrap:
    """SystemBootstrap 桩——handler→push_event 接线是被测物，
    bootstrap 本体（真初始化生产系统）不进测试面；calls 记录可证
    坏 token 时零副作用（身份证明先于一切）。"""

    def __init__(self):
        self.calls = []

    def run_bootstrap(self, force=False):
        self.calls.append({"force": force})
        return {"status": "ok", "steps": 3}


class _StubGraphrag:
    """GraphRAGEngine 桩——build_graph_from_knowledge 本体（写知识图谱）不进测试面。"""

    def __init__(self):
        self.calls = []

    async def build_graph_from_knowledge(self, db_pool, user_id):
        self.calls.append({"user_id": user_id})
        return {"entities_new": 0, "relations_new": 0, "scanned": 0}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """三 router + tmp 身份库/TimelineStore/桩（绝不碰生产数据面）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.cortex_enhanced as cortex_api
    import src.api.link as link_api
    import src.api.system_overview as system_api
    from src.api import event_stream
    from src.link.connector import IntegrationManager
    from src.timeline.store import TimelineStore

    # 桩（本体不进测试面，接线是被测物）
    stub_bootstrap = _StubBootstrap()
    monkeypatch.setattr(system_api, "bootstrap", stub_bootstrap)
    stub_graphrag = _StubGraphrag()
    monkeypatch.setattr(cortex_api, "graphrag", stub_graphrag)
    monkeypatch.setattr(link_api, "manager", IntegrationManager())

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

    event_stream._event_buffer.clear()

    app = FastAPI()
    app.include_router(link_api.router, prefix="/api/link")
    app.include_router(system_api.router, prefix="/api/system")
    app.include_router(cortex_api.router, prefix="/api/cortex")
    with TestClient(app) as c:
        yield {
            "client": c,
            "tl_db": tl_db,
            "ent_db": ent_db,
            "buffer": event_stream._event_buffer,
            "bootstrap": stub_bootstrap,
            "graphrag": stub_graphrag,
            "link_api": link_api,
        }
    event_stream._event_buffer.clear()


def _mk_user(env, username, tenant):
    return _insert_ent_user(env["ent_db"], username, tenant)


def _create_connector(env, headers=None, name="c1"):
    return env["client"].post(
        "/api/link/connectors",
        json={"name": name, "type": "webhook_in"},
        headers=headers or {},
    )


def _bootstrap_run(env, headers=None):
    return env["client"].post("/api/system/bootstrap/run", json={}, headers=headers or {})


def _build_graph(env, headers=None):
    return env["client"].post("/api/cortex/graphrag/build", headers=headers or {})


# ── link create_connector ─────────────────────────────────────────────


def test_link_create_connector_row_tenant(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    r = _create_connector(env, _auth(_ent_token(uid, "u-a")), name="c-a-mark")
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "connector_created")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "tenant-a"
    assert "c-a-mark" in rows[0]["summary"]
    # 缓冲 dict 自带键（⑨b-① 三处注入真流经调用点）
    buffered = [
        e for e in env["buffer"] if isinstance(e, dict) and e.get("type") == "connector_created"
    ]
    assert buffered and buffered[0].get("tenant_id") == "tenant-a"


def test_link_create_connector_anonymous_default(env):
    r = _create_connector(env)
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "connector_created")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"


def test_link_create_connector_bad_token_401_zero_event(env):
    r = _create_connector(env, _auth("not-a-jwt"))
    assert r.status_code == 401
    assert _rows(env["tl_db"]) == []
    # 零副作用：连接器未被创建（身份证明先于一切）
    assert env["link_api"].manager.list_connectors() == []


def test_link_create_connector_dual_tenant(env):
    ua = _mk_user(env, "u-a", "tenant-a")
    ub = _mk_user(env, "u-b", "tenant-b")
    assert _create_connector(env, _auth(_ent_token(ua, "u-a")), name="ca").status_code == 200
    assert _create_connector(env, _auth(_ent_token(ub, "u-b")), name="cb").status_code == 200
    rows = _rows(env["tl_db"], "connector_created")
    assert sorted(r["tenant_id"] for r in rows) == ["tenant-a", "tenant-b"]


def test_link_receive_webhook_third_party_boundary(env):
    """③ 边界锁死：receive_webhook 是外部 webhook 入口，归属语义待拍——
    本拍零改动。垃圾 Authorization 不得被 bind_identity 语义 401 打断
    （外部 webhook 真回归面），事件照旧落 'default' 兜底。
    契约随更新（DEC-⑲ 2026-10-16）：入口必持 connector 回调凭据（X-Callback-Token），
    本例持证跑——Authorization 仍不是 401 来源（凭据轴与用户 JWT 轴分开）。"""
    r0 = _create_connector(env, name="wh1")
    assert r0.status_code == 200
    cid = env["link_api"].manager.list_connectors()[0]["connector_id"]
    r = env["client"].post(
        f"/api/link/webhook/{cid}",
        json={"hello": "world"},
        headers={
            "Authorization": "Bearer some-third-party-secret",
            "X-Callback-Token": r0.json()["callback_token"],
        },
    )
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "webhook_received")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "default"


# ── system_overview run_bootstrap ─────────────────────────────────────


def test_bootstrap_row_tenant(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    r = _bootstrap_run(env, _auth(_ent_token(uid, "u-a")))
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "bootstrap")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "tenant-a"
    assert len(env["bootstrap"].calls) == 1


def test_bootstrap_anonymous_default(env):
    r = _bootstrap_run(env)
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "bootstrap")
    assert rows[0]["tenant_id"] == "default"


def test_bootstrap_bad_token_401_no_side_effect(env):
    r = _bootstrap_run(env, _auth("not-a-jwt"))
    assert r.status_code == 401
    assert _rows(env["tl_db"]) == []
    # 身份证明先于副作用：bootstrap 本体零调用
    assert env["bootstrap"].calls == []


def test_bootstrap_response_shape(env):
    r = _bootstrap_run(env)
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "steps": 3}


# ── cortex_enhanced build_graph ───────────────────────────────────────


def test_build_graph_row_tenant(env):
    uid = _mk_user(env, "u-a", "tenant-a")
    r = _build_graph(env, _auth(_ent_token(uid, "u-a")))
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "graphrag_build")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "tenant-a"
    assert len(env["graphrag"].calls) == 1


def test_build_graph_anonymous_default(env):
    r = _build_graph(env)
    assert r.status_code == 200
    rows = _rows(env["tl_db"], "graphrag_build")
    assert rows[0]["tenant_id"] == "default"


def test_build_graph_bad_token_401_no_side_effect(env):
    r = _build_graph(env, _auth("not-a-jwt"))
    assert r.status_code == 401
    assert _rows(env["tl_db"]) == []
    assert env["graphrag"].calls == []


def test_build_graph_dual_tenant(env):
    ua = _mk_user(env, "u-a", "tenant-a")
    ub = _mk_user(env, "u-b", "tenant-b")
    assert _build_graph(env, _auth(_ent_token(ua, "u-a"))).status_code == 200
    assert _build_graph(env, _auth(_ent_token(ub, "u-b"))).status_code == 200
    rows = _rows(env["tl_db"], "graphrag_build")
    assert sorted(r["tenant_id"] for r in rows) == ["tenant-a", "tenant-b"]


# ── 静态接线普查 + ③ 边界锁死 ─────────────────────────────────────────


def test_static_census():
    """接线普查：3 文件各恰 1 identity 派生+1 tenant 注入；
    ③ 待拍文件（soma_connector/中间件）零改动锁死（link_gateway 已由
    F3b-⑨b-③-1c 收口，其接线普查见 test_link_gateway_emit_tenant）。"""

    def count(path, needle):
        return (ROOT / path).read_text(encoding="utf-8").count(needle)

    pipe_sig = "authorization: str " + chr(124) + " None = Header(None)"
    expect = {
        "src/api/link.py": (1, 1),
        "src/api/system_overview.py": (1, 1),
        "src/api/cortex_enhanced.py": (1, 1),
    }
    for path, (n_sig, n_inject) in expect.items():
        assert count(path, pipe_sig) == n_sig, path
        assert count(path, "identity = bind_identity(authorization)") == n_sig, path
        assert count(path, "tenant_id=identity.tenant_id") == n_inject, path
    for path in (
        "src/api/soma_connector.py",
        "src/middleware/intrusion_middleware.py",
    ):
        assert count(path, "bind_identity(") == 0, path
