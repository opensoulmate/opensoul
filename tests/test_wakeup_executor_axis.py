"""DEC-① ② wakeup executor 认证轴测试（F3b-⑧c 边界①收口，建议默认 C）。

覆盖三层（全部 tmp 独立数据面 + NEST_DB/USERS_DB/ENTERPRISE_DB 环境隔离，
绝不写生产 data/wakeup.db / data/nest.db）：
①判定口纯函数（_executor_token_verdict 四态：absent/ok/bad-config/bad-token，
  显式空串头=带着头来按坏凭据拒，禁静默降级用户轴）
②系统视图全链（executor 凭据过=due 跨租户全返回逐事件 tenant_id 键=点火落
  行租户键、claim/ack 跨租户真写且行租户键不漂移、CAS at-most-once 跨租户）
③fail-closed + 契约零变化（坏凭据/轴未配置 401 零副作用、缺头=用户轴契约
  字节不变：匿名 default 视图/跨租户 403/缺行 409·404 全在）
"""

import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from src.api.will import _executor_token_verdict
from src.config import settings
from src.services import tenant_binding as tb

ENTERPRISE_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""

EXEC_TOKEN = "exec-secret-e2e-42"


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


def _force_due(db_path: str, event_id: int) -> None:
    """把事件的 next_wakeup_at 改到过去（越过 MIN_WAKEUP_DELAY_S 钳制，制造到点现场）。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE wakeup_events SET next_wakeup_at=? WHERE id=?", (time.time() - 1, event_id)
        )
        conn.commit()
    finally:
        conn.close()


def _row(db_path: str, event_id: int) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM wakeup_events WHERE id=?", (event_id,)).fetchone())
    finally:
        conn.close()


# ── ① 判定口纯函数 ───────────────────────────────────────────────


class TestExecutorTokenVerdict:
    def test_absent_header_is_user_axis(self, monkeypatch):
        monkeypatch.setattr(settings, "wakeup_executor_token", EXEC_TOKEN)
        assert _executor_token_verdict(None) == "absent"

    def test_valid_token_ok(self, monkeypatch):
        monkeypatch.setattr(settings, "wakeup_executor_token", EXEC_TOKEN)
        assert _executor_token_verdict(EXEC_TOKEN) == "ok"

    def test_wrong_token_bad(self, monkeypatch):
        monkeypatch.setattr(settings, "wakeup_executor_token", EXEC_TOKEN)
        assert _executor_token_verdict("wrong-token") == "bad-token"

    def test_unconfigured_bad_config(self, monkeypatch):
        monkeypatch.setattr(settings, "wakeup_executor_token", "")
        assert _executor_token_verdict(EXEC_TOKEN) == "bad-config"

    def test_explicit_empty_header_bad_token(self, monkeypatch):
        """显式空串头=带着头来→按坏凭据拒，禁静默降级用户轴。"""
        monkeypatch.setattr(settings, "wakeup_executor_token", EXEC_TOKEN)
        assert _executor_token_verdict("") == "bad-token"
        monkeypatch.setattr(settings, "wakeup_executor_token", "")
        assert _executor_token_verdict("") == "bad-token"


# ── ②③ API 全链（真实 will router + tmp 数据面）─────────────────


class TestExecutorAPI:
    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        ent_db = tmp_path / "enterprise.db"
        monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
        monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
        conn = sqlite3.connect(ent_db)
        conn.execute(ENTERPRISE_DDL)
        conn.commit()
        conn.close()
        return ent_db

    @pytest.fixture()
    def exec_headers(self, monkeypatch):
        monkeypatch.setattr(settings, "wakeup_executor_token", EXEC_TOKEN)
        return {"X-Executor-Token": EXEC_TOKEN}

    def _schedule(self, client, tok, agent, reason):
        resp = client.post(
            "/api/will/wakeup/schedule",
            json={
                "agent_id": agent,
                "session_id": "s",
                "reason": reason,
                "noop": True,
                "source": "",
            },
            headers=tok,
        )
        assert resp.status_code == 200
        return resp.json()["event_id"]

    def test_executor_due_all_tenants_row_tenant_keys(self, will_client, env, exec_headers):
        """executor 系统视图=各租户到点事件全返回，逐事件 tenant_id 键=点火落行租户键。"""
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        ea = self._schedule(will_client, tok_a, "ka", "A机密")
        eb = self._schedule(will_client, tok_b, "kb", "B机密")
        _force_due(will_client.app.state.will_client_wakeup_db, ea)
        _force_due(will_client.app.state.will_client_wakeup_db, eb)

        due = will_client.get("/api/will/wakeup/due", headers=exec_headers).json()
        assert due["count"] == 2
        by_tenant = {e["tenant_id"]: e for e in due["due"]}
        assert set(by_tenant) == {"tenant-a", "tenant-b"}
        assert by_tenant["tenant-a"]["reason"] == "A机密"
        assert by_tenant["tenant-b"]["reason"] == "B机密"

        # 零回归：用户轴契约字节不变（B 仍只看得见自己的）
        due_b = will_client.get("/api/will/wakeup/due", headers=tok_b).json()
        assert due_b["count"] == 1 and due_b["due"][0]["tenant_id"] == "tenant-b"

    def test_executor_claim_ack_cross_tenant_row_tenant_stable(
        self, will_client, env, exec_headers
    ):
        """跨租户 claim/ack 真写，且只写行本身=行租户键不漂移（点火落行租户键）。"""
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        eid = self._schedule(will_client, tok_a, "ka", "A机密")
        _force_due(will_client.app.state.will_client_wakeup_db, eid)

        r = will_client.post("/api/will/wakeup/claim", json={"event_id": eid}, headers=exec_headers)
        assert r.status_code == 200 and r.json()["claimed"] is True
        assert r.json()["event"]["tenant_id"] == "tenant-a"  # 点火动作落行租户键

        row = _row(will_client.app.state.will_client_wakeup_db, eid)
        assert row["tenant_id"] == "tenant-a"  # 行租户键不漂移
        assert row["fired_at"] is not None and row["fire_attempts"] == 1

        r2 = will_client.post(
            "/api/will/wakeup/fire-result",
            json={"event_id": eid, "ok": True},
            headers=exec_headers,
        )
        assert r2.status_code == 200 and r2.json()["state"] == "fired"
        assert _row(will_client.app.state.will_client_wakeup_db, eid)["tenant_id"] == "tenant-a"

    def test_executor_ack_fail_releases_for_retry(self, will_client, env, exec_headers):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        eid = self._schedule(will_client, tok_a, "ka", "A")
        _force_due(will_client.app.state.will_client_wakeup_db, eid)
        assert (
            will_client.post(
                "/api/will/wakeup/claim", json={"event_id": eid}, headers=exec_headers
            ).status_code
            == 200
        )
        r = will_client.post(
            "/api/will/wakeup/fire-result",
            json={"event_id": eid, "ok": False, "error": "boom"},
            headers=exec_headers,
        )
        assert r.status_code == 200 and r.json()["state"] == "retry"
        row = _row(will_client.app.state.will_client_wakeup_db, eid)
        assert row["fired_at"] is None and row["last_fire_error"] == "boom"
        # 释放后重新可见=自动重试，executor 再领取得到
        due = will_client.get("/api/will/wakeup/due", headers=exec_headers).json()
        assert [e["event_id"] for e in due["due"]] == [eid]

    def test_executor_cas_at_most_once(self, will_client, env, exec_headers):
        """双实例 executor 并发领取只有一个赢家（at-most-once 跨租户语义不变）。"""
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        eid = self._schedule(will_client, tok_a, "ka", "A")
        _force_due(will_client.app.state.will_client_wakeup_db, eid)
        r1 = will_client.post(
            "/api/will/wakeup/claim", json={"event_id": eid}, headers=exec_headers
        )
        r2 = will_client.post(
            "/api/will/wakeup/claim", json={"event_id": eid}, headers=exec_headers
        )
        assert r1.status_code == 200 and r2.status_code == 409

    def test_bad_token_401_zero_side_effects(self, will_client, env, exec_headers):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        eid = self._schedule(will_client, tok_a, "ka", "A机密")
        _force_due(will_client.app.state.will_client_wakeup_db, eid)
        bad = {"X-Executor-Token": "wrong-token"}

        r_due = will_client.get("/api/will/wakeup/due", headers=bad)
        assert r_due.status_code == 401
        assert "invalid" in r_due.json()["detail"]
        assert "A机密" not in str(r_due.json())  # 零泄露

        r_claim = will_client.post("/api/will/wakeup/claim", json={"event_id": eid}, headers=bad)
        assert r_claim.status_code == 401
        r_ack = will_client.post(
            "/api/will/wakeup/fire-result",
            json={"event_id": eid, "ok": True},
            headers=bad,
        )
        assert r_ack.status_code == 401
        row = _row(will_client.app.state.will_client_wakeup_db, eid)
        assert row["fired_at"] is None  # 行零改写（401 先于一切写）

    def test_unconfigured_401_fail_closed(self, will_client, env, monkeypatch):
        """轴未配置（token 空）带头=401 可见——禁静默降级 'default' 匿名视图
        （静默降级会让非 default 租户到点事件永久无人点火=静默停滞）。"""
        monkeypatch.setattr(settings, "wakeup_executor_token", "")
        r = will_client.get("/api/will/wakeup/due", headers={"X-Executor-Token": EXEC_TOKEN})
        assert r.status_code == 401
        assert "not configured" in r.json()["detail"]
        r2 = will_client.post(
            "/api/will/wakeup/claim",
            json={"event_id": 1},
            headers={"X-Executor-Token": EXEC_TOKEN},
        )
        assert r2.status_code == 401

    def test_explicit_empty_header_401(self, will_client, env, exec_headers):
        r = will_client.get("/api/will/wakeup/due", headers={"X-Executor-Token": ""})
        assert r.status_code == 401

    def test_absent_header_user_axis_contract_unchanged(self, will_client, env):
        """缺头=用户轴契约字节不变（bind_identity 语义零变化）。"""
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        eid = self._schedule(will_client, tok_a, "ka", "A机密")
        _force_due(will_client.app.state.will_client_wakeup_db, eid)

        due_anon = will_client.get("/api/will/wakeup/due").json()
        assert due_anon["count"] == 0  # 匿名=default 视图，看不见 A
        due_b = will_client.get("/api/will/wakeup/due", headers=tok_b).json()
        assert due_b["count"] == 0

        r_claim = will_client.post("/api/will/wakeup/claim", json={"event_id": eid}, headers=tok_b)
        assert r_claim.status_code == 403  # 跨租户闸门照旧
        assert (
            will_client.post(
                "/api/will/wakeup/claim", json={"event_id": eid}, headers=tok_a
            ).status_code
            == 200
        )

    def test_executor_token_takes_precedence_over_authorization(
        self, will_client, env, exec_headers
    ):
        """executor 轴与用户轴互不顶替：带头（有效）=系统视图，Authorization 忽略。"""
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        eid = self._schedule(will_client, tok_a, "ka", "A机密")
        _force_due(will_client.app.state.will_client_wakeup_db, eid)
        mixed = dict(exec_headers)
        mixed["Authorization"] = "Bearer garbage-token"  # 坏 JWT 不参与判定
        due = will_client.get("/api/will/wakeup/due", headers=mixed).json()
        assert due["count"] == 1 and due["due"][0]["tenant_id"] == "tenant-a"

    def test_executor_missing_row_contracts_unchanged(self, will_client, env, exec_headers):
        """缺行契约不变（claim 409 / fire-result 404）在 executor 轴同样成立。"""
        r = will_client.post(
            "/api/will/wakeup/claim", json={"event_id": 424242}, headers=exec_headers
        )
        assert r.status_code == 409
        r2 = will_client.post(
            "/api/will/wakeup/fire-result",
            json={"event_id": 424242, "ok": True},
            headers=exec_headers,
        )
        assert r2.status_code == 404
