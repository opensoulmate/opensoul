"""F3b-⑲ hippo 自动建会话注册键错位收口——注册键=行身份单一源。

修前（F3b-⑱ 诚实边界③留档）：create_memory 自动建会话走
create()（按 uuid4 注册）后 handler 改 .session_id 不改 _sessions dict 键
→ 键/身份错位孤儿行：
- 同 session_id 逐条记忆各建一孤儿会话（get(req.session_id) 恒 miss）
- increment_memory_count/touch 按 req.session_id 恒查不到（计数恒 0、
  记忆访问不刷新活动）
- by-ID 面（get/archive/delete）按真实 session_id 恒不可达（键≠身份）

本文件覆盖：
- 单一源不变量：_sessions 注册键恒 == 行 session_id（显式/缺省两路）
- create(session_id=) 显式注册（含 '' 原值保真禁 fabricate uuid）
- 重复键如实 ValueError 禁静默覆盖既有会话
- API 自动建会话：同 id 复用单行（零孤儿）+ 计数/touch/by-ID 真可达
- 不变面：缺省 create 键==身份（修前即成立）、'default' 兜底归属（F3b-⑱）
"""

import ast
import time
from pathlib import Path

import pytest

from src.hippo.session import SessionManager, SessionStatus


def _age(session, seconds: float = 100.0) -> float:
    old = time.time() - seconds
    session.last_active_at = old
    return old


class TestRegistrationKeySingleSource:
    """SessionManager 注册键=行身份单一源（纯内存单元面）。"""

    def test_create_default_key_equals_identity(self):
        """不变面：缺省 create 键==身份（修前即成立，create 后未被改身份）。"""
        sm = SessionManager()
        s = sm.create()
        assert s.session_id in sm._sessions
        assert sm._sessions[s.session_id] is s

    def test_create_explicit_id_registers_under_it(self):
        sm = SessionManager()
        s = sm.create(session_id="sess-f3b20")
        assert s.session_id == "sess-f3b20"
        assert sm.get("sess-f3b20") is s
        assert sm._sessions["sess-f3b20"] is s

    def test_create_explicit_empty_id_preserved(self):
        """显式 '' 原值保真：禁 `session_id or uuid4()` 把空串 fabricate 成 uuid。"""
        sm = SessionManager()
        s = sm.create(session_id="")
        assert s.session_id == ""
        assert sm._sessions[""] is s

    def test_create_duplicate_refused_no_clobber(self):
        sm = SessionManager()
        first = sm.create(session_id="dup", title="first")
        with pytest.raises(ValueError, match="dup"):
            sm.create(session_id="dup", title="second")
        assert sm.get("dup") is first
        assert first.title == "first"  # 行零改写
        assert len(sm._sessions) == 1

    def test_explicit_id_reach_memory_count_and_touch(self):
        """修前反证面：自动建会话（键错位）increment/touch 按 id 恒 no-op。"""
        sm = SessionManager()
        s = sm.create(session_id="reach", user_id="default")
        sm.increment_memory_count("reach")
        assert sm.touch("reach") is True
        assert s.memory_count == 1

    def test_no_key_identity_drift_invariant(self):
        """混合创建后全表键==身份（错位孤儿行零容忍）。"""
        sm = SessionManager()
        sm.create()
        sm.create(session_id="explicit-a")
        sm.create(session_id="")
        for key, row in sm._sessions.items():
            assert key == row.session_id


@pytest.fixture()
def env(will_client, monkeypatch):
    """hippo 路由进程内测试面（test_session_touch_wiring 同款路线）。"""
    import src.api.hippo as hippo_api
    from src.hippo.memory_store import MemoryStore

    monkeypatch.setattr(hippo_api, "sessions", SessionManager())
    monkeypatch.setattr(hippo_api, "store", MemoryStore())
    return will_client, hippo_api


class TestAutoCreateRegistration:
    """API 全链：真实 router → handler → SessionManager 注册键。"""

    def test_auto_create_registered_under_requested_id(self, env):
        client, hippo_api = env
        resp = client.post(
            "/api/hippo/memories",
            json={"content": "auto 注册", "session_id": "auto-f3b20"},
        )
        assert resp.status_code == 200
        # 修前：dict 键=uuid，get("auto-f3b20") 恒 None（孤儿行）
        assert hippo_api.sessions.get("auto-f3b20") is not None
        # by-ID 真可达（修前键错位恒 404）
        detail = client.get("/api/hippo/sessions/auto-f3b20", params={"user_id": "default"})
        assert detail.status_code == 200
        assert detail.json()["session_id"] == "auto-f3b20"

    def test_second_write_reuses_session_no_orphan(self, env):
        client, hippo_api = env
        for i in range(2):
            resp = client.post(
                "/api/hippo/memories",
                json={"content": f"记忆{i}", "session_id": "reuse-f3b20"},
            )
            assert resp.status_code == 200
        # 修前：2 条记忆=2 个孤儿会话（total_sessions==2）
        assert hippo_api.sessions.get_stats()["total_sessions"] == 1
        sess = client.get("/api/hippo/sessions/reuse-f3b20", params={"user_id": "default"}).json()
        assert sess["memory_count"] == 2  # 修前恒 0（increment 按 id 查不到）

    def test_no_orphan_rows_after_many_writes(self, env):
        client, hippo_api = env
        for i in range(5):
            assert (
                client.post(
                    "/api/hippo/memories",
                    json={"content": f"批量记忆{i}", "session_id": "bulk-f3b20"},
                ).status_code
                == 200
            )
        sm = hippo_api.sessions
        assert len(sm._sessions) == 1
        assert set(sm._sessions.keys()) == {"bulk-f3b20"}
        assert sm._sessions["bulk-f3b20"].session_id == "bulk-f3b20"

    def test_memory_access_touches_auto_session(self, env):
        client, hippo_api = env
        mid = client.post(
            "/api/hippo/memories",
            json={"content": "被访问的自动会话记忆", "session_id": "touch-f3b20"},
        ).json()["memory_id"]
        sm = hippo_api.sessions
        sm._sessions["touch-f3b20"].status = SessionStatus.IDLE
        old = _age(sm._sessions["touch-f3b20"])

        resp = client.get(f"/api/hippo/memories/{mid}")
        assert resp.status_code == 200
        # 修前：touch("touch-f3b20") 按 id 查不到 → no-op，IDLE 不复活
        sess = client.get("/api/hippo/sessions/touch-f3b20", params={"user_id": "default"}).json()
        assert sess["status"] == "active"
        assert sess["last_active_at"] > old + 50

    def test_auto_session_archive_delete_reachable(self, env):
        client, hippo_api = env
        assert (
            client.post(
                "/api/hippo/memories",
                json={"content": "可归档记忆", "session_id": "arch-f3b20"},
            ).status_code
            == 200
        )
        # 修前：按真实 id 归档/删除恒 404（键错位不可达）
        assert (
            client.post(
                "/api/hippo/sessions/arch-f3b20/archive", params={"user_id": "default"}
            ).status_code
            == 200
        )
        assert (
            client.delete(
                "/api/hippo/sessions/arch-f3b20", params={"user_id": "default"}
            ).status_code
            == 200
        )
        assert hippo_api.sessions.get("arch-f3b20") is None

    def test_fallback_default_bucket_unchanged(self, env):
        """不变面：自动建会话 'default' 兜底归属（F3b-⑱ 契约，按 title 找行=
        修前修后同判法，新旧皆过非自嗨）。"""
        client, hippo_api = env
        assert (
            client.post(
                "/api/hippo/memories",
                json={"content": "兜底记忆", "session_id": "bucket-f3b20"},
            ).status_code
            == 200
        )
        rows = hippo_api.sessions.list_sessions()
        row = next(s for s in rows if s.title == "Auto: bucket-f3b20")
        assert row.user_id == "default"


class TestStaticWiring:
    """写了≠接线了：handler 注册点单一源静态锁。"""

    def test_no_session_id_mutation_in_handler(self):
        """session.session_id 赋值=键/身份错位根因面，全 src 零残留。"""
        src = Path("src")
        offenders = []
        for path in src.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and target.attr == "session_id"
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "session"
                        ):
                            offenders.append(f"{path}:{node.lineno}")
        assert offenders == []

    def test_create_memory_passes_explicit_session_id(self):
        """create_memory 自动建会话必须把 req.session_id 传进 create（接线锁）。"""
        tree = ast.parse(Path("src/api/hippo.py").read_text(encoding="utf-8"))
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "create_memory"
        )
        creates = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "create"
        ]
        assert len(creates) == 1
        kw = {k.arg: k.value for k in creates[0].keywords}
        assert "session_id" in kw
        assert ast.unparse(kw["session_id"]) == "req.session_id"
