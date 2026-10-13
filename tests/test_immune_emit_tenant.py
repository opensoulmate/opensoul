"""F3b-⑨b-②b-2 immune 6 调用点接派生——push_event 带 bind_identity 派生租户键。

F3b-⑨b-② 留档候选「其余 HTTP 调用点分 organ 拆拍（vein 8/immune 6/mirror 5/…）」
拆件第二件=immune 模块 6 调用点（F3b-⑨ census：immune 6 全在 src/api/immune.py，
content_blocked/ip_blacklisted/intrusion_detected/brute_force/ip_blocked/
permission_denied 各 1，②b-1 vein 同型先例）。

修前：6 调用点不传 tenant_id→事件 dict 无键→record() 'default' 兜底
（timeline/store.py record tenant_val），非 default 租户身份用户的 immune
安全动作事件落 'default'→被自己的租户过滤查询挡掉=自动作自我不可见
（F3b-⑨ live 硬证同型、②b-1 vein 面同型）。

数据面全 tmp（USERS_DB/ENTERPRISE_DB/tmp TimelineStore/tmp permission.db/
notifications 桩/死端口 _BASE），绝不写生产 opensoul.db/enterprise.db/
timeline.db/permission.db、不打 live :8090。
"""

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jose import jwt

from src.config import settings
from src.nerve import event_bridge
from src.services import tenant_binding as tb

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


@pytest.fixture()
def immune_env(tmp_path, monkeypatch):
    """immune router + tmp 身份库/TimelineStore/permission.db（绝不碰生产数据面）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.immune as immune_api
    from src.immune.permission_engine import PermissionService
    from src.timeline.store import TimelineStore

    # 身份库隔离（bind_identity 真源绝不碰生产）
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    ent_db = tmp_path / "enterprise.db"
    conn = sqlite3.connect(ent_db)
    conn.execute(ENTERPRISE_DDL)
    conn.commit()
    conn.close()

    # permission.db 隔离（PermissionStore 真落盘绝不写生产 permission.db）
    monkeypatch.setattr(
        immune_api,
        "permission_service",
        PermissionService(db_path=str(tmp_path / "permission.db")),
    )

    # notifications 桩（记录调用即可，不进任何通知面）
    import src.api.notifications as notif_mod

    calls = []
    monkeypatch.setattr(
        notif_mod,
        "push_notification",
        lambda **kw: calls.append(kw),
    )

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
    app.include_router(immune_api.router, prefix="/api/immune")
    with TestClient(app) as c:
        yield c, tl_db, ent_db, event_stream
    event_stream._event_buffer.clear()


def _auth(token):
    return {"Authorization": f"Bearer {token}"} if token else {}


# ── ① 6 调用点真接派生 → record 行归属 ───────────────────────────


class TestImmuneEmitTenantWiring:
    def test_content_blocked_event_row_tenant_derived(self, immune_env):
        """认证派生租户→moderate 拦截事件行落派生租户（修前恒 'default' 自我不可见）。"""
        c, tl_db, ent_db, _es = immune_env
        uid = _insert_ent_user(ent_db, "alice", "tenant-immune-a")
        r = c.post(
            "/api/immune/moderate",
            json={"text": "password: hunter2-secret"},
            headers=_auth(_ent_token(uid, "alice")),
        )
        assert r.status_code == 200
        assert r.json()["is_safe"] is False  # 真走到 push_event 的前置条件
        rows = [x for x in _rows(tl_db) if x["event_type"] == "content_blocked"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-immune-a"  # 非 'default'=真接派生
        assert rows[0]["organ"] == "immune"

    def test_ip_blacklisted_event_row_tenant_derived(self, immune_env):
        c, tl_db, ent_db, _es = immune_env
        uid = _insert_ent_user(ent_db, "bob", "tenant-immune-b")
        r = c.post(
            "/api/immune/ip/blacklist",
            json={"ip": "203.0.113.10", "reason": "probe"},
            headers=_auth(_ent_token(uid, "bob")),
        )
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "ip_blacklisted"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-immune-b"

    def test_intrusion_detected_event_row_tenant_derived(self, immune_env):
        c, tl_db, ent_db, _es = immune_env
        uid = _insert_ent_user(ent_db, "carol", "tenant-immune-c")
        r = c.post(
            "/api/immune/intrusion/inspect",
            json={
                "ip": "203.0.113.11",
                "method": "GET",
                "path": "/api/search",
                "query": "' OR 1=1",
            },
            headers=_auth(_ent_token(uid, "carol")),
        )
        assert r.status_code == 200
        assert r.json()["threats_found"] >= 1  # 真走到 push_event（high/critical）
        rows = [x for x in _rows(tl_db) if x["event_type"] == "intrusion_detected"]
        assert len(rows) >= 1
        assert all(x["tenant_id"] == "tenant-immune-c" for x in rows)

    def test_brute_force_event_row_tenant_derived(self, immune_env):
        c, tl_db, ent_db, _es = immune_env
        uid = _insert_ent_user(ent_db, "dave", "tenant-immune-d")
        token = _ent_token(uid, "dave")
        # BRUTE_FORCE_THRESHOLD=10：第 10 次失败触发威胁→push_event
        for _ in range(10):
            r = c.post(
                "/api/immune/intrusion/login-attempt",
                json={"ip": "203.0.113.12", "success": False},
                headers=_auth(token),
            )
            assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "brute_force"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-immune-d"

    def test_ip_blocked_event_row_tenant_derived(self, immune_env):
        c, tl_db, ent_db, _es = immune_env
        uid = _insert_ent_user(ent_db, "eve", "tenant-immune-e")
        r = c.post(
            "/api/immune/intrusion/block",
            json={"ip": "203.0.113.13"},
            headers=_auth(_ent_token(uid, "eve")),
        )
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "ip_blocked"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-immune-e"

    def test_permission_denied_event_row_tenant_derived(self, immune_env):
        c, tl_db, ent_db, _es = immune_env
        uid = _insert_ent_user(ent_db, "frank", "tenant-immune-f")
        token = _ent_token(uid, "frank")
        tool = "evil_tool_immune_f"
        r = c.post(
            "/api/immune/permission/rules",
            json={"tool_name": tool, "behavior": "deny", "source": "user"},
            headers=_auth(token),
        )
        assert r.status_code == 200
        r = c.post(
            "/api/immune/permission/check",
            json={"tool_name": tool, "arguments": {}},
            headers=_auth(token),
        )
        assert r.status_code == 200
        assert r.json()["behavior"] == "deny"  # 真走到 push_event 的前置条件
        rows = [x for x in _rows(tl_db) if x["event_type"] == "permission_denied"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-immune-f"

    def test_buffer_dict_carries_tenant_key(self, immune_env):
        """事件 dict 自带键（bus/缓冲/record 同源注入）——⑨b-① 三处注入真流经调用点。"""
        c, _tl, ent_db, es = immune_env
        uid = _insert_ent_user(ent_db, "iris", "tenant-immune-i")
        c.post(
            "/api/immune/moderate",
            json={"text": "password: leak-again"},
            headers=_auth(_ent_token(uid, "iris")),
        )
        buffered = [e for e in es._event_buffer if e.get("type") == "content_blocked"]
        assert len(buffered) == 1
        assert buffered[0]["tenant_id"] == "tenant-immune-i"

    def test_dual_tenant_rows_isolated(self, immune_env):
        """双租户各自落行互不串键。"""
        c, tl_db, ent_db, _es = immune_env
        uid_a = _insert_ent_user(ent_db, "u_a", "tenant-dual-a")
        uid_b = _insert_ent_user(ent_db, "u_b", "tenant-dual-b")
        assert (
            c.post(
                "/api/immune/moderate",
                json={"text": "password: aaa"},
                headers=_auth(_ent_token(uid_a, "u_a")),
            ).status_code
            == 200
        )
        time.sleep(0.02)  # evt id 含 ms 时间戳，防同 ms 去重假阴（②b-1 坑延续）
        assert (
            c.post(
                "/api/immune/moderate",
                json={"text": "password: bbb"},
                headers=_auth(_ent_token(uid_b, "u_b")),
            ).status_code
            == 200
        )
        rows = [x for x in _rows(tl_db) if x["event_type"] == "content_blocked"]
        assert sorted(x["tenant_id"] for x in rows) == ["tenant-dual-a", "tenant-dual-b"]


# ── ② 不变面（契约锁死） ─────────────────────────────────────────


class TestImmuneEmitTenantInvariants:
    def test_anonymous_default_contract_unchanged(self, immune_env):
        """匿名='default' 行值不变+响应契约逐键不变（接派生不改响应面）。"""
        c, tl_db, _ent, _es = immune_env
        r = c.post("/api/immune/moderate", json={"text": "password: anon"})
        assert r.status_code == 200
        data = r.json()
        for key in ("is_safe", "risk_level", "findings", "redacted_text", "original_length"):
            assert key in data
        rows = [x for x in _rows(tl_db) if x["event_type"] == "content_blocked"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"

    def test_allow_path_no_event_and_contract(self, immune_env):
        """安全文本=零事件（只有不安全才发）+check 放行不发事件——接派生不改触发面。"""
        c, tl_db, ent_db, _es = immune_env
        uid = _insert_ent_user(ent_db, "gin", "tenant-immune-g")
        token = _ent_token(uid, "gin")
        r = c.post(
            "/api/immune/moderate",
            json={"text": "hello world"},
            headers=_auth(token),
        )
        assert r.status_code == 200
        assert r.json()["is_safe"] is True
        tool = "safe_tool_immune_g"
        c.post(
            "/api/immune/permission/rules",
            json={"tool_name": tool, "behavior": "allow", "source": "user"},
            headers=_auth(token),
        )
        r = c.post(
            "/api/immune/permission/check",
            json={"tool_name": tool, "arguments": {}},
            headers=_auth(token),
        )
        assert r.status_code == 200
        assert r.json()["behavior"] == "allow"
        assert [x for x in _rows(tl_db)] == []

    def test_bad_token_401_zero_event(self, immune_env):
        """坏 token=401（bind_identity 闸门），零事件行落库（身份证明先于一切）。"""
        c, tl_db, _ent, _es = immune_env
        r = c.post(
            "/api/immune/moderate",
            json={"text": "password: bad"},
            headers=_auth("not-a-jwt-token"),
        )
        assert r.status_code == 401
        assert _rows(tl_db) == []

    def test_all_six_call_sites_wired(self):
        """静态接线普查：6 handler 全带派生、6 调用点全传租户键（写了必须接线）。"""
        src = (Path(__file__).parent.parent / "src" / "api" / "immune.py").read_text(
            encoding="utf-8"
        )
        assert src.count("identity = bind_identity(authorization)") == 6
        assert src.count("tenant_id=identity.tenant_id") == 6
        for etype in (
            "content_blocked",
            "ip_blacklisted",
            "intrusion_detected",
            "brute_force",
            "ip_blocked",
            "permission_denied",
        ):
            i = src.index(f'"type": "{etype}",')
            # 每个 push_event 块尾部都带租户键注入（禁只改声明不接线）
            assert "tenant_id=identity.tenant_id" in src[i : i + 700]


# ── ③ 顺手收口回归：brute-force 报警路径自死锁（Gate3 逼出的真断点） ──


class TestIntrusionNoSelfDeadlock:
    def test_brute_force_alert_path_returns_not_hangs(self):
        """第 10 次失败=报警路径必须返回威胁而非自死锁（修前 with self._lock 内调
        _record_threats 二次取非重入锁=永久挂死+扣死全局锁）。"""
        import threading

        from src.immune.intrusion import IntrusionDetector

        det = IntrusionDetector()
        result = []

        def run():
            for _ in range(det.BRUTE_FORCE_THRESHOLD):
                threat = det.record_login_attempt("198.51.100.7", False)
            result.append(threat)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=5)
        assert not t.is_alive(), "record_login_attempt 自死锁（报警路径挂死）"
        assert result and result[0] is not None
        assert result[0].attack_type.value == "brute_force"
        # 全局锁未被扣死：后续系统路径仍可用
        assert isinstance(det.stats(), dict)
