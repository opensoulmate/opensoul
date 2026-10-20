"""H1-②b-② session.touch 会话活动接线——活动必须反映到 last_active_at/状态。

修前（H1-② 定性⑬）：touch() 全仓零生产消费、increment_memory_count 不刷
last_active_at——长会话持续收记忆但活跃时间戳恒陈旧，IDLE/EXPIRED 后连记忆
写入都无法复活，lifecycle 按陈旧时间戳把它判 idle/expired。

本文件覆盖：
- 活动语义单一源（_note_activity_locked）：时间戳刷新 + IDLE/EXPIRED 复活，
  ARCHIVED 保持（显式归档意图不被顺带活动撤销）——touch 与记忆写同语义
- 真实接线面：记忆写入（increment_memory_count）+ 记忆访问（GET /memories/{id}）
- 不变面：memory_count 计数、404 契约、create 语义、缺席会话 no-op
"""

import time

import pytest

from src.hippo.session import SessionManager, SessionStatus


def _age(session, seconds: float = 100.0) -> float:
    """把会话活跃时间戳拨旧，返回拨旧前的值（后续断言比较基准）。"""
    old = time.time() - seconds
    session.last_active_at = old
    return old


class TestSessionActivityPrimitive:
    """SessionManager 活动原语单元面（纯内存，零生产接触）。"""

    def test_touch_missing_session_returns_false(self):
        sm = SessionManager()
        assert sm.touch("no-such-session") is False

    def test_touch_active_refreshes_timestamp_only(self):
        sm = SessionManager()
        s = sm.create()
        old = _age(s)
        assert sm.touch(s.session_id) is True
        assert s.last_active_at > old + 50
        assert s.status == SessionStatus.ACTIVE

    def test_touch_revives_idle(self):
        sm = SessionManager()
        s = sm.create()
        s.status = SessionStatus.IDLE
        old = _age(s)
        assert sm.touch(s.session_id) is True
        assert s.status == SessionStatus.ACTIVE
        assert s.last_active_at > old + 50

    def test_touch_revives_expired(self):
        sm = SessionManager()
        s = sm.create()
        s.status = SessionStatus.EXPIRED
        old = _age(s)
        assert sm.touch(s.session_id) is True
        assert s.status == SessionStatus.ACTIVE
        assert s.last_active_at > old + 50

    def test_touch_archived_stays_archived(self):
        """ARCHIVED=显式用户意图：活动只留真值时间戳，不撤销归档。"""
        sm = SessionManager()
        s = sm.create()
        assert sm.archive(s.session_id) is True
        old = _age(s)
        assert sm.touch(s.session_id) is True
        assert s.status == SessionStatus.ARCHIVED
        assert s.last_active_at > old + 50

    def test_increment_memory_count_records_activity(self):
        """修前反证面：increment 不刷时间戳（H1-② 证据原文）。"""
        sm = SessionManager()
        s = sm.create()
        old = _age(s)
        sm.increment_memory_count(s.session_id)
        assert s.last_active_at > old + 50

    def test_increment_revives_idle_long_session(self):
        """长会话场景：持续收记忆的会话不再被判 idle/expired。"""
        sm = SessionManager()
        s = sm.create()
        s.status = SessionStatus.IDLE
        old = _age(s)
        sm.increment_memory_count(s.session_id, count=3)
        assert s.status == SessionStatus.ACTIVE
        assert s.last_active_at > old + 50
        assert s.memory_count == 3

    def test_increment_archived_not_revived(self):
        sm = SessionManager()
        s = sm.create()
        assert sm.archive(s.session_id) is True
        old = _age(s)
        sm.increment_memory_count(s.session_id)
        assert s.status == SessionStatus.ARCHIVED
        assert s.last_active_at > old + 50

    def test_increment_missing_session_noop(self):
        """不变面：缺席会话不抛不建（既有语义）。"""
        sm = SessionManager()
        sm.increment_memory_count("no-such-session")
        assert sm.get("no-such-session") is None
        assert sm.get_stats()["total_sessions"] == 0

    def test_increment_still_counts(self):
        """不变面：memory_count 计数语义零变化。"""
        sm = SessionManager()
        s = sm.create()
        sm.increment_memory_count(s.session_id)
        sm.increment_memory_count(s.session_id, count=2)
        assert s.memory_count == 3


@pytest.fixture()
def env(will_client, monkeypatch):
    """hippo 路由进程内测试面（will_client 同款路线）：真实 router/handler，
    sessions/store 换全新实例（纯内存单例，逐用例隔离防跨用例状态泄漏）。"""
    import src.api.hippo as hippo_api
    from src.hippo.memory_store import MemoryStore

    monkeypatch.setattr(hippo_api, "sessions", SessionManager())
    monkeypatch.setattr(hippo_api, "store", MemoryStore())
    return will_client, hippo_api


class TestSessionActivityAPI:
    """API 全链：真实 router → handler → SessionManager 活动原语。"""

    def _create_session(self, client):
        resp = client.post("/api/hippo/sessions", json={"user_id": "touch_user"})
        assert resp.status_code == 200
        return resp.json()["session_id"]

    def test_memory_write_revives_idle_session(self, env):
        client, hippo_api = env
        sid = self._create_session(client)

        sm = hippo_api.sessions
        sm._sessions[sid].status = SessionStatus.IDLE
        old = _age(sm._sessions[sid])

        resp = client.post(
            "/api/hippo/memories",
            params={"user_id": "touch_user"},
            json={"content": "活动接线记忆", "session_id": sid},
        )
        assert resp.status_code == 200

        sess = client.get(f"/api/hippo/sessions/{sid}", params={"user_id": "touch_user"}).json()
        assert sess["status"] == "active"
        assert sess["last_active_at"] > old + 50

    def test_memory_access_touches_session(self, env):
        client, hippo_api = env
        sid = self._create_session(client)
        mid = client.post(
            "/api/hippo/memories",
            params={"user_id": "touch_user"},
            json={"content": "被访问的记忆", "session_id": sid},
        ).json()["memory_id"]

        sm = hippo_api.sessions
        sm._sessions[sid].status = SessionStatus.IDLE
        old = _age(sm._sessions[sid])

        resp = client.get(f"/api/hippo/memories/{mid}", params={"user_id": "touch_user"})
        assert resp.status_code == 200

        sess = client.get(f"/api/hippo/sessions/{sid}", params={"user_id": "touch_user"}).json()
        assert sess["status"] == "active"
        assert sess["last_active_at"] > old + 50

    def test_archived_session_activity_keeps_status_fresh_timestamp(self, env):
        client, hippo_api = env
        sid = self._create_session(client)
        assert (
            client.post(
                f"/api/hippo/sessions/{sid}/archive", params={"user_id": "touch_user"}
            ).status_code
            == 200
        )

        old = _age(hippo_api.sessions._sessions[sid])
        resp = client.post(
            "/api/hippo/memories",
            params={"user_id": "touch_user"},
            json={"content": "归档会话收到的记忆", "session_id": sid},
        )
        assert resp.status_code == 200

        sess = client.get(f"/api/hippo/sessions/{sid}", params={"user_id": "touch_user"}).json()
        assert sess["status"] == "archived"
        assert sess["last_active_at"] > old + 50

    def test_memory_count_increments_contract(self, env):
        client, _ = env
        sid = self._create_session(client)
        for i in range(2):
            resp = client.post(
                "/api/hippo/memories",
                params={"user_id": "touch_user"},
                json={"content": f"计数记忆{i}", "session_id": sid},
            )
            assert resp.status_code == 200
        sess = client.get(f"/api/hippo/sessions/{sid}", params={"user_id": "touch_user"}).json()
        assert sess["memory_count"] == 2

    def test_get_memory_404_contract(self, env):
        client, _ = env
        resp = client.get("/api/hippo/memories/no-such-memory")
        assert resp.status_code == 404

    def test_create_session_active_contract(self, env):
        client, _ = env
        sid = self._create_session(client)
        sess = client.get(f"/api/hippo/sessions/{sid}", params={"user_id": "touch_user"}).json()
        assert sess["status"] == "active"
        assert sess["last_active_at"] >= sess["created_at"]
