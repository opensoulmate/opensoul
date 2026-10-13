"""F3b-⑧c wakeup 面（ScheduleWakeup 唤醒面）租户归属收口测试。

覆盖四层（全部 tmp 独立数据面 + NEST_DB 环境隔离（conftest autouse），绝不写生产
data/nest.db / data/wakeup.db）：
①迁移+行归属（wakeup_events.tenant_id 列自愈回填 'default'、record=身份派生、
  缺失/空白→'default'、streak 判定按 (tenant, agent, session) 三键隔离）
②查询过滤（status/folded/overview/due/purge 三面隔离 + None=系统视图）
③IsolationEngine.check_access 真执行（claim/ack by-ID 闸门：跨租户
  WakeupAccessDenied + 引擎审计留痕 ns-tenant-a:{event_id} 硬断言）
④API 层派生租户全链（auth token → bind_identity → 行归属落行 → 过滤/闸门 403，
  真实 will router）
"""

import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from src.config import settings
from src.services import tenant_binding as tb
from src.will.wakeup import WakeupAccessDenied, WakeupScheduler

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


def _make_sched(tmp_path) -> WakeupScheduler:
    return WakeupScheduler(db_path=str(tmp_path / "wakeup.db"))


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


_OLD_DDL = """
CREATE TABLE wakeup_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL DEFAULT 'default',
    session_id TEXT NOT NULL DEFAULT '',
    ts REAL NOT NULL,
    requested_delay_s REAL NOT NULL,
    delay_s REAL NOT NULL,
    clamped INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL,
    noop INTEGER NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0,
    stopped INTEGER NOT NULL DEFAULT 0,
    next_wakeup_at REAL
)
"""


# ── ① 迁移 + 行归属 ─────────────────────────────────────────────────


class TestMigrationOwnership:
    def test_old_db_upgraded_backfill_and_index(self, tmp_path):
        db = str(tmp_path / "wakeup.db")
        conn = sqlite3.connect(db)
        conn.execute(_OLD_DDL)
        conn.execute(
            "INSERT INTO wakeup_events (agent_id, session_id, ts, requested_delay_s, "
            "delay_s, clamped, reason, noop, streak, stopped, next_wakeup_at) "
            "VALUES ('soulmate','old1',?,60.0,60.0,1,'旧事件',0,0,0,NULL)",
            (time.time(),),
        )
        conn.commit()
        conn.close()

        s = WakeupScheduler(db_path=db)  # 迁移发生在这里
        conn = sqlite3.connect(db)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(wakeup_events)")}
            assert "tenant_id" in cols
            backfilled = conn.execute(
                "SELECT tenant_id FROM wakeup_events WHERE session_id='old1'"
            ).fetchall()
            assert [r[0] for r in backfilled] == ["default"]
            idx = {r[1] for r in conn.execute("PRAGMA index_list(wakeup_events)")}
            assert "idx_wakeup_tenant" in idx
        finally:
            conn.close()
        # 迁移行照常被读面消费（tenant_id 键在场）
        ev = s.status("soulmate", "old1")["last_event"]
        assert ev["tenant_id"] == "default"

    def test_record_tenant_lands(self, tmp_path):
        s = _make_sched(tmp_path)
        ev = s.record(reason="A", noop=True, agent_id="a", session_id="s1", tenant_id="tenant-a")
        assert ev["tenant_id"] == "tenant-a"
        assert s.status("a", "s1", tenant_id="tenant-a")["last_event"]["tenant_id"] == "tenant-a"

    def test_missing_blank_defaults_tenant(self, tmp_path):
        s = _make_sched(tmp_path)
        e1 = s.record(reason="A", noop=True, agent_id="a", session_id="s1")
        e2 = s.record(reason="B", noop=True, agent_id="a", session_id="s2", tenant_id="   ")
        assert e1["tenant_id"] == "default"
        assert e2["tenant_id"] == "default"

    def test_streak_key_isolated_by_tenant(self, tmp_path):
        """同 (agent_id, session_id) key 跨租户=两个独立循环（streak 互不串）。"""
        s = _make_sched(tmp_path)
        for i in (1, 2):
            ev_a = s.record(
                reason=f"A{i}", noop=True, agent_id="k", session_id="s", tenant_id="tenant-a"
            )
        ev_b = s.record(reason="B1", noop=True, agent_id="k", session_id="s", tenant_id="tenant-b")
        assert ev_a["streak"] == 2  # A 自己的连续 noop
        assert ev_b["streak"] == 1  # B 的首条不继承 A 的 streak


# ── ② 查询过滤（三面隔离 + 系统视图） ───────────────────────────────


class TestQueryFiltering:
    def test_status_three_way_isolation(self, tmp_path):
        s = _make_sched(tmp_path)
        s.record(reason="A机密", noop=True, agent_id="k", session_id="s", tenant_id="tenant-a")
        s.record(reason="B机密", noop=True, agent_id="k", session_id="s", tenant_id="tenant-b")
        st_a = s.status("k", "s", tenant_id="tenant-a")
        st_b = s.status("k", "s", tenant_id="tenant-b")
        st_sys = s.status("k", "s")
        assert st_a["total_events"] == 1 and st_a["last_event"]["reason"] == "A机密"
        assert st_b["total_events"] == 1 and st_b["last_event"]["reason"] == "B机密"
        assert "A机密" not in str(st_b)  # B 视图零 A 痕迹
        assert st_sys["total_events"] == 2  # None=系统视图全量

    def test_folded_view_filter(self, tmp_path):
        s = _make_sched(tmp_path)
        for i in (1, 2):
            s.record(reason=f"A{i}", noop=True, agent_id="k", session_id="s", tenant_id="tenant-a")
        s.record(reason="B1", noop=True, agent_id="k", session_id="s", tenant_id="tenant-b")
        fd_a = s.folded_view("k", "s", tenant_id="tenant-a")
        fd_b = s.folded_view("k", "s", tenant_id="tenant-b")
        assert fd_a["total_events"] == 2 and fd_a["current_streak"] == 2
        assert fd_b["total_events"] == 1 and fd_b["current_streak"] == 1
        assert "A1" not in str(fd_b)

    def test_overview_filter_reason_no_leak(self, tmp_path):
        s = _make_sched(tmp_path)
        s.record(
            reason="A机密原因", noop=True, agent_id="ka", session_id="sa", tenant_id="tenant-a"
        )
        s.record(reason="B原因", noop=True, agent_id="kb", session_id="sb", tenant_id="tenant-b")
        ov_a = s.overview(tenant_id="tenant-a")
        ov_b = s.overview(tenant_id="tenant-b")
        ov_sys = s.overview()
        assert ov_a["total_keys"] == 1 and "A机密原因" in str(ov_a)
        assert ov_b["total_keys"] == 1 and "A机密原因" not in str(ov_b)
        assert ov_sys["total_keys"] == 2

    def test_due_wakeups_filter(self, tmp_path):
        s = _make_sched(tmp_path)
        ea = s.record(
            reason="A到点", noop=True, agent_id="ka", session_id="sa", tenant_id="tenant-a"
        )
        eb = s.record(
            reason="B到点", noop=True, agent_id="kb", session_id="sb", tenant_id="tenant-b"
        )
        _force_due(s._db_path, ea["event_id"])
        _force_due(s._db_path, eb["event_id"])
        due_a = s.due_wakeups(tenant_id="tenant-a")
        due_b = s.due_wakeups(tenant_id="tenant-b")
        due_sys = s.due_wakeups()
        assert [e["event_id"] for e in due_a] == [ea["event_id"]]
        assert [e["event_id"] for e in due_b] == [eb["event_id"]]
        assert "B到点" not in str(due_a)
        assert {e["event_id"] for e in due_sys} == {ea["event_id"], eb["event_id"]}

    def test_purge_tenant_scope(self, tmp_path):
        """F3b-③a jobs purge 纪律："只给租户"的清理不得变相清他租户历史。"""
        s = _make_sched(tmp_path)
        s.record(
            reason="A探针",
            noop=True,
            agent_id="a",
            session_id="x",
            source="test",
            tenant_id="tenant-a",
        )
        s.record(
            reason="B探针",
            noop=True,
            agent_id="b",
            session_id="y",
            source="test",
            tenant_id="tenant-b",
        )
        res = s.purge("test", tenant_id="tenant-a")
        assert res == {"source": "test", "purged": 1}
        assert s.status("a", "x", tenant_id="tenant-a")["total_events"] == 0
        assert s.status("b", "y", tenant_id="tenant-b")["total_events"] == 1
        # 系统清理（None=既有契约按 source 全量）
        assert s.purge("test") == {"source": "test", "purged": 1}

    def test_purge_zero_filter_rejected_contract(self, tmp_path):
        s = _make_sched(tmp_path)
        s.record(reason="真实", noop=True, agent_id="c", session_id="z", tenant_id="tenant-a")
        with pytest.raises(ValueError):
            s.purge("", tenant_id="tenant-a")
        assert s.status("c", "z", tenant_id="tenant-a")["total_events"] == 1


# ── ③ IsolationEngine.check_access 真执行 ───────────────────────────


class TestIsolationGate:
    def test_claim_cross_tenant_denied_with_audit(self, tmp_path):
        s = _make_sched(tmp_path)
        ev = s.record(reason="A", noop=True, agent_id="a", session_id="s", tenant_id="tenant-a")
        eid = ev["event_id"]
        with pytest.raises(WakeupAccessDenied) as e:
            s.claim_fire(eid, tenant_id="tenant-b")
        assert "namespace" in e.value.reason or "different tenant" in e.value.reason

        stats = s.isolation.stats()
        assert stats["total_access_checks"] >= 1
        assert stats["blocked_attempts"] >= 1
        blocked = s.isolation.get_access_log(allowed=False)
        hit = [
            x
            for x in blocked
            if x["resource_id"] == f"ns-tenant-a:{eid}" and x["action"] == "write"
        ]
        assert hit, f"blocked audit entry missing: {blocked}"
        assert "namespace mismatch" in hit[0]["reason"]
        assert hit[0]["resource_type"] == "wakeup_event"
        # 行零改写：未被领取
        assert s.status("a", "s", tenant_id="tenant-a")["last_event"]["fired"] is False

    def test_ack_cross_tenant_denied_row_zero_mutation(self, tmp_path):
        s = _make_sched(tmp_path)
        ev = s.record(reason="A", noop=True, agent_id="a", session_id="s", tenant_id="tenant-a")
        eid = ev["event_id"]
        assert s.claim_fire(eid, tenant_id="tenant-a") is not None
        with pytest.raises(WakeupAccessDenied):
            s.ack_fire(eid, ok=False, error="越权回执", tenant_id="tenant-b")
        row = s.status("a", "s", tenant_id="tenant-a")["last_event"]
        assert row["fired"] is True  # 领取态不被越权改写
        assert row["last_fire_error"] == ""  # 行零改写
        # 同租户过闸
        ack = s.ack_fire(eid, ok=True, tenant_id="tenant-a")
        assert ack["state"] == "fired"

    def test_same_tenant_allowed_and_logged(self, tmp_path):
        s = _make_sched(tmp_path)
        ev = s.record(reason="A", noop=True, agent_id="a", session_id="s", tenant_id="tenant-a")
        eid = ev["event_id"]
        assert s.claim_fire(eid, tenant_id="tenant-a") is not None
        allowed = s.isolation.get_access_log(allowed=True)
        assert any(
            x["resource_id"] == f"ns-tenant-a:{eid}" and x["action"] == "write" for x in allowed
        )

    def test_claim_gate_before_state_check(self, tmp_path):
        """闸门前置于状态检查：已领取事件跨租户 claim 恒 403（禁 None/409 差分泄露状态）。"""
        s = _make_sched(tmp_path)
        ev = s.record(reason="A", noop=True, agent_id="a", session_id="s", tenant_id="tenant-a")
        eid = ev["event_id"]
        assert s.claim_fire(eid, tenant_id="tenant-a") is not None  # 已领取
        with pytest.raises(WakeupAccessDenied):
            s.claim_fire(eid, tenant_id="tenant-b")  # 非 None=不泄露"已被领取"

    def test_missing_row_contracts_unchanged(self, tmp_path):
        s = _make_sched(tmp_path)
        # 缺行契约不变（API 409/404 语义不因闸门变味）
        assert s.claim_fire(99999, tenant_id="tenant-a") is None
        with pytest.raises(ValueError):
            s.ack_fire(99999, ok=True, tenant_id="tenant-a")

    def test_system_view_no_gate_no_audit_noise(self, tmp_path):
        s = _make_sched(tmp_path)
        ev = s.record(reason="A", noop=True, agent_id="a", session_id="s", tenant_id="tenant-a")
        eid = ev["event_id"]
        assert s.claim_fire(eid) is not None
        assert s.ack_fire(eid, ok=True)["state"] == "fired"
        s.status("a", "s")
        s.due_wakeups()
        s.overview()
        assert s.isolation.stats()["total_access_checks"] == 0


# ── ④ API 层：认证派生租户 → 行归属 → 过滤/闸门 403 全链 ─────────────


class TestAPI:
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

    def test_api_tenant_lands_and_cross_hidden(self, will_client, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}

        # A 调度：行归属从认证身份派生（body 自报 tenant_id 被丢弃）
        # source=""（tmp 隔离库内需真实 source 才能过 due/overview 的 test 排除面）
        resp = will_client.post(
            "/api/will/wakeup/schedule",
            json={
                "agent_id": "k",
                "session_id": "s",
                "reason": "A机密",
                "noop": True,
                "source": "",
                "tenant_id": "attacker-tenant",
            },
            headers=tok_a,
        )
        assert resp.status_code == 200
        eid = resp.json()["event_id"]
        assert resp.json()["tenant_id"] == "tenant-a"

        # B/匿名读面恒不可见 A 的循环
        st_b = will_client.get(
            "/api/will/wakeup/status", params={"agent_id": "k", "session_id": "s"}, headers=tok_b
        ).json()
        assert st_b["total_events"] == 0
        ov_b = will_client.get("/api/will/wakeup/overview", headers=tok_b).json()
        assert "A机密" not in str(ov_b)
        assert will_client.get("/api/will/wakeup/overview").json()["total_keys"] == 0

        # 到点后 due 三面隔离（匿名/default 与 B 均看不到 A 的到点事件）
        _force_due(will_client.app.state.will_client_wakeup_db, eid)
        due_b = will_client.get("/api/will/wakeup/due", headers=tok_b).json()
        due_anon = will_client.get("/api/will/wakeup/due").json()
        due_a = will_client.get("/api/will/wakeup/due", headers=tok_a).json()
        assert due_b["count"] == 0 and due_anon["count"] == 0
        assert due_a["count"] == 1 and due_a["due"][0]["reason"] == "A机密"

        # B 冒领取 403 带真因 + 行零改写；A 过闸
        r_claim_b = will_client.post(
            "/api/will/wakeup/claim", json={"event_id": eid}, headers=tok_b
        )
        assert r_claim_b.status_code == 403
        assert "wakeup access denied" in r_claim_b.json()["detail"]
        conn = sqlite3.connect(will_client.app.state.will_client_wakeup_db)
        try:
            fired_at = conn.execute(
                "SELECT fired_at FROM wakeup_events WHERE id=?", (eid,)
            ).fetchone()[0]
        finally:
            conn.close()
        assert fired_at is None  # 行零改写（禁跨租户破坏）
        r_claim_a = will_client.post(
            "/api/will/wakeup/claim", json={"event_id": eid}, headers=tok_a
        )
        assert r_claim_a.status_code == 200 and r_claim_a.json()["claimed"] is True

        # B 冒回执 403 行零改写；A 回执过闸
        r_ack_b = will_client.post(
            "/api/will/wakeup/fire-result",
            json={"event_id": eid, "ok": False, "error": "越权"},
            headers=tok_b,
        )
        assert r_ack_b.status_code == 403
        r_ack_a = will_client.post(
            "/api/will/wakeup/fire-result", json={"event_id": eid, "ok": True}, headers=tok_a
        )
        assert r_ack_a.status_code == 200 and r_ack_a.json()["state"] == "fired"

    def test_api_anonymous_default_contract(self, will_client, env):
        resp = will_client.post(
            "/api/will/wakeup/schedule",
            json={
                "agent_id": "k",
                "session_id": "s",
                "reason": "匿名",
                "noop": True,
                "source": "test",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == "default"
        st = will_client.get(
            "/api/will/wakeup/status", params={"agent_id": "k", "session_id": "s"}
        ).json()
        assert st["total_events"] == 1 and st["active"] is True
        fd = will_client.get(
            "/api/will/wakeup/folded", params={"agent_id": "k", "session_id": "s"}
        ).json()
        assert fd["total_events"] == 1
        # 匿名自清（source='test'）契约不变
        r = will_client.post("/api/will/wakeup/purge", params={"source": "test"})
        assert r.status_code == 200 and r.json() == {"source": "test", "purged": 1}

    def test_api_purge_tenant_scope(self, will_client, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        uid_b = _insert_ent_user(env, "bob", "tenant-b")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        tok_b = {"Authorization": f"Bearer {_ent_token(uid_b, 'bob')}"}
        for tok, key in ((tok_a, "ka"), (tok_b, "kb")):
            will_client.post(
                "/api/will/wakeup/schedule",
                json={
                    "agent_id": key,
                    "session_id": "s",
                    "reason": "探针",
                    "noop": True,
                    "source": "test",
                },
                headers=tok,
            )
        r = will_client.post("/api/will/wakeup/purge", params={"source": "test"}, headers=tok_b)
        assert r.status_code == 200 and r.json()["purged"] == 1  # 只清 B 自己的行
        st_a = will_client.get(
            "/api/will/wakeup/status", params={"agent_id": "ka", "session_id": "s"}, headers=tok_a
        ).json()
        assert st_a["total_events"] == 1  # A 的行存活
        # 收尾：A 自清
        will_client.post("/api/will/wakeup/purge", params={"source": "test"}, headers=tok_a)

    def test_api_missing_contracts_unchanged(self, will_client, env):
        uid_a = _insert_ent_user(env, "alice", "tenant-a")
        tok_a = {"Authorization": f"Bearer {_ent_token(uid_a, 'alice')}"}
        # 缺行：claim 409、fire-result 404 契约不变
        r = will_client.post("/api/will/wakeup/claim", json={"event_id": 424242}, headers=tok_a)
        assert r.status_code == 409
        r = will_client.post(
            "/api/will/wakeup/fire-result", json={"event_id": 424242, "ok": True}, headers=tok_a
        )
        assert r.status_code == 404
        # 零过滤 purge 400 契约不变
        assert will_client.post("/api/will/wakeup/purge", headers=tok_a).status_code == 400

    def test_api_bad_token_401(self, will_client, env):
        r = will_client.get(
            "/api/will/wakeup/status", headers={"Authorization": "Bearer garbage-token"}
        )
        assert r.status_code == 401
