"""F3b-⑨b-②b-3 mirror 5 调用点接派生——push_event 带 bind_identity 派生租户键。

F3b-⑨b-② 留档候选「其余 41 HTTP 调用点分 organ 拆拍（vein 8/immune 6/mirror 5/…）」
拆件第三件=mirror 模块 5 调用点（F3b-⑨ census：mirror 5 全在 src/api/mirror.py，
sandbox_created/sandbox_exec/sandbox_fork/sandbox_template_created/
sandbox_from_template 各 1）。

修前：5 调用点不传 tenant_id→事件 dict 无键→record() 'default' 兜底
（timeline/store.py record tenant_val），非 default 租户身份用户的 mirror
沙箱动作事件落 'default'→被自己的租户过滤查询挡掉=自动作自我不可见
（F3b-⑨ live 硬证同型、②b-1 vein/②b-2 immune 同型）。

数据面全 tmp（USERS_DB/ENTERPRISE_DB/tmp SandboxManager/tmp TemplateEngine/
tmp TimelineStore/死端口 _BASE/isolation.run_in_sandbox 桩），绝不写生产
opensoul.db/enterprise.db/timeline.db/~/opensoul/sandboxes、不打 live :8090。
"""

import sqlite3
import time
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
    return {"Authorization": f"Bearer {token}"} if token else {}


@pytest.fixture()
def mirror_env(tmp_path, monkeypatch):
    """mirror router + tmp 沙箱库/模板库/身份库/TimelineStore（绝不碰生产数据面）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.mirror as mirror_api
    from src.mirror import isolation as isolation_mod
    from src.mirror.isolation import ExecResult
    from src.mirror.sandbox import SandboxManager
    from src.mirror.templates import SandboxTemplateEngine
    from src.timeline.store import TimelineStore

    # 沙箱/模板库隔离（module 单例换 tmp 实例）
    monkeypatch.setattr(mirror_api, "manager", SandboxManager(tmp_path / "sandboxes"))
    monkeypatch.setattr(
        mirror_api, "template_engine", SandboxTemplateEngine(str(tmp_path / "templates.db"))
    )

    # 身份库隔离（bind_identity 真源绝不碰生产）
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    ent_db = tmp_path / "enterprise.db"
    conn = sqlite3.connect(ent_db)
    conn.execute(ENTERPRISE_DDL)
    conn.commit()
    conn.close()

    # 真隔离执行换桩（bwrap 环境依赖不进测试面；handler→push_event 接线是本拍被测物）
    def _stub_run(command, workspace, network_policy="deny", timeout=30, env=None):
        return ExecResult(
            command=command,
            exit_code=0,
            stdout="stub-out\n",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            stdout_total_bytes=9,
            stderr_total_bytes=0,
            timed_out=False,
            duration_s=0.01,
            network_policy=network_policy,
        )

    monkeypatch.setattr(isolation_mod, "run_in_sandbox", _stub_run)

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
    app.include_router(mirror_api.router, prefix="/api/mirror")
    with TestClient(app) as c:
        yield c, tl_db, ent_db, event_stream
    event_stream._event_buffer.clear()


# ── ① 5 调用点真接派生 → record 行归属 ───────────────────────────


class TestMirrorEmitTenantWiring:
    def test_create_sandbox_event_row_tenant_derived(self, mirror_env):
        """认证派生租户→sandbox_created 事件行落派生租户（修前恒 'default' 自我不可见）。"""
        c, tl_db, ent_db, _es = mirror_env
        uid = _insert_ent_user(ent_db, "alice", "tenant-mirror-a")
        r = c.post(
            "/api/mirror/sandboxes",
            json={"name": "s-a"},
            headers=_auth(_ent_token(uid, "alice")),
        )
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "sandbox_created"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-mirror-a"  # 非 'default'=真接派生
        assert rows[0]["organ"] == "mirror"

    def test_exec_event_row_tenant_derived(self, mirror_env):
        c, tl_db, ent_db, _es = mirror_env
        uid = _insert_ent_user(ent_db, "bob", "tenant-mirror-b")
        token = _ent_token(uid, "bob")
        sid = c.post("/api/mirror/sandboxes", json={"name": "s-b"}, headers=_auth(token)).json()[
            "sandbox_id"
        ]
        time.sleep(0.02)  # evt id 含 ms 时间戳，防同 ms 去重假阴（②b-1 坑延续）
        r = c.post(
            f"/api/mirror/sandboxes/{sid}/exec",
            json={"command": "echo hi"},
            headers=_auth(token),
        )
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "sandbox_exec"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-mirror-b"

    def test_fork_event_row_tenant_derived(self, mirror_env):
        c, tl_db, ent_db, _es = mirror_env
        uid = _insert_ent_user(ent_db, "carol", "tenant-mirror-c")
        token = _ent_token(uid, "carol")
        sid = c.post("/api/mirror/sandboxes", json={"name": "s-c"}, headers=_auth(token)).json()[
            "sandbox_id"
        ]
        time.sleep(0.02)
        snap = c.post(
            f"/api/mirror/sandboxes/{sid}/snapshot",
            json={"name": "snap-c"},
            headers=_auth(token),
        )
        assert snap.status_code == 200
        snap_id = snap.json()["snapshot_id"]
        time.sleep(0.02)
        r = c.post(
            f"/api/mirror/sandboxes/{sid}/fork",
            json={"snapshot_id": snap_id},
            headers=_auth(token),
        )
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "sandbox_fork"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-mirror-c"

    def test_template_created_event_row_tenant_derived(self, mirror_env):
        c, tl_db, ent_db, _es = mirror_env
        uid = _insert_ent_user(ent_db, "dave", "tenant-mirror-d")
        r = c.post(
            "/api/mirror/templates",
            json={"name": "tpl-d"},
            headers=_auth(_ent_token(uid, "dave")),
        )
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "sandbox_template_created"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-mirror-d"

    def test_from_template_event_row_tenant_derived(self, mirror_env):
        c, tl_db, ent_db, _es = mirror_env
        uid = _insert_ent_user(ent_db, "erin", "tenant-mirror-e")
        token = _ent_token(uid, "erin")
        tid = c.post("/api/mirror/templates", json={"name": "tpl-e"}, headers=_auth(token)).json()[
            "template_id"
        ]
        time.sleep(0.02)
        r = c.post(
            f"/api/mirror/templates/{tid}/instantiate",
            json={"template_id": tid},
            headers=_auth(token),
        )
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "sandbox_from_template"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-mirror-e"

    def test_buffer_dict_carries_tenant_key(self, mirror_env):
        """事件 dict 自带键（bus/缓冲/record 同源注入）——⑨b-① 三处注入真流经调用点。"""
        c, _tl, ent_db, es = mirror_env
        uid = _insert_ent_user(ent_db, "iris", "tenant-mirror-i")
        c.post(
            "/api/mirror/sandboxes",
            json={"name": "s-i"},
            headers=_auth(_ent_token(uid, "iris")),
        )
        buffered = [e for e in es._event_buffer if e.get("type") == "sandbox_created"]
        assert len(buffered) == 1
        assert buffered[0]["tenant_id"] == "tenant-mirror-i"

    def test_dual_tenant_rows_isolated(self, mirror_env):
        """双租户各自落行互不串键。"""
        c, tl_db, ent_db, _es = mirror_env
        uid_a = _insert_ent_user(ent_db, "u_a", "tenant-dual-a")
        uid_b = _insert_ent_user(ent_db, "u_b", "tenant-dual-b")
        assert (
            c.post(
                "/api/mirror/sandboxes",
                json={"name": "s-dual-a"},
                headers=_auth(_ent_token(uid_a, "u_a")),
            ).status_code
            == 200
        )
        time.sleep(0.02)  # evt id 含 ms 时间戳，防同 ms 去重假阴（②b-1 坑延续）
        assert (
            c.post(
                "/api/mirror/sandboxes",
                json={"name": "s-dual-b"},
                headers=_auth(_ent_token(uid_b, "u_b")),
            ).status_code
            == 200
        )
        rows = [x for x in _rows(tl_db) if x["event_type"] == "sandbox_created"]
        assert sorted(x["tenant_id"] for x in rows) == ["tenant-dual-a", "tenant-dual-b"]


# ── ② 不变面（契约锁死） ─────────────────────────────────────────


class TestMirrorEmitTenantInvariants:
    def test_anonymous_default_contract_unchanged(self, mirror_env):
        """匿名='default' 行值不变+响应契约逐键不变（接派生不改响应面）。"""
        c, tl_db, _ent, _es = mirror_env
        r = c.post("/api/mirror/sandboxes", json={"name": "s-anon"})
        assert r.status_code == 200
        data = r.json()
        assert sorted(data.keys()) == ["name", "sandbox_id", "status", "ttl_seconds"]
        rows = [x for x in _rows(tl_db) if x["event_type"] == "sandbox_created"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"

    def test_exec_and_template_response_contract(self, mirror_env):
        """exec/instantiate 响应形状逐键不变（接派生不改 handler 返回面）。"""
        c, tl_db, ent_db, _es = mirror_env
        uid = _insert_ent_user(ent_db, "gina", "tenant-mirror-g")
        token = _ent_token(uid, "gina")
        sid = c.post("/api/mirror/sandboxes", json={"name": "s-g"}, headers=_auth(token)).json()[
            "sandbox_id"
        ]
        time.sleep(0.02)
        r = c.post(
            f"/api/mirror/sandboxes/{sid}/exec",
            json={"command": "echo hi"},
            headers=_auth(token),
        )
        assert r.status_code == 200
        for key in ("command", "exit_code", "stdout", "timed_out", "network_policy"):
            assert key in r.json()
        tid = c.post("/api/mirror/templates", json={"name": "tpl-g"}, headers=_auth(token)).json()[
            "template_id"
        ]
        time.sleep(0.02)
        r = c.post(
            f"/api/mirror/templates/{tid}/instantiate",
            json={"template_id": tid},
            headers=_auth(token),
        )
        assert r.status_code == 200
        assert sorted(r.json().keys()) == [
            "name",
            "sandbox_id",
            "status",
            "template_id",
            "variables",
        ]

    def test_bad_token_401_zero_event(self, mirror_env):
        """坏 token=401（bind_identity 闸门），零事件行落库（身份证明先于一切）。"""
        c, tl_db, _ent, _es = mirror_env
        r = c.post(
            "/api/mirror/sandboxes",
            json={"name": "s-bad"},
            headers=_auth("not-a-jwt-token"),
        )
        assert r.status_code == 401
        assert _rows(tl_db) == []

    def test_all_call_sites_wired(self):
        """静态接线普查：5 handler 全带派生、5 调用点全传租户键（写了必须接线）。"""
        pipe = chr(124)
        src = (Path(__file__).parent.parent / "src" / "api" / "mirror.py").read_text(
            encoding="utf-8"
        )
        assert src.count("identity = bind_identity(authorization)") == 9
        assert src.count("tenant_id=identity.tenant_id") == 11
        assert src.count("authorization: str " + pipe + " None") == 9  # 竖线完好探针
        for etype in (
            "sandbox_created",
            "sandbox_exec",
            "sandbox_fork",
            "sandbox_template_created",
            "sandbox_from_template",
        ):
            i = src.index(f'"type": "{etype}",')
            # 每个 push_event 块尾部都带租户键注入（禁只改声明不接线）
            assert "tenant_id=identity.tenant_id" in src[i : i + 900]
