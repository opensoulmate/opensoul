"""unified agents 子搜索存储层加轴测试（DEC-⑦-⑤：agents 面最小件）。

修前（HEAD 源码实证）两断点：①_search_agents 读 ~/.openmate/agents.json——该
文件本机恒不存在且全仓零写方=幽灵源，子搜索恒空集（wired-but-not-functional，
DEC-⑦-② echo 面同型；真源=acp_agent_registry「检测/管理页/列表一条真源」）
②acp_agent_registry 行无租户键、写点（probe）零身份轴、list 零过滤=跨租户
泄露。收口=agent_row_tenant 单一判法（行自带键缺失/空白→'default' 兜底，
⑦-① _event_row_tenant / ② message_row_tenant / ③ template_row_tenant /
④ file_row_tenant 同款；写读同一判法）+acp_agent_registry.tenant_id 行键
（DDL+ALTER 双轨幂等迁移回填 'default'，③c-2① 索引补列之后建坑延续）+写点
行归属（probe→_record(tenant_id=) =bind_identity 派生租户、缺省→'default'
系统写方）+劫持闸（跨租户同 agent_id 重探测=AgentTenantDenied→403 行零改写，
⑦-③④ 同款）+粘性（ON CONFLICT 不带 tenant_id，行归属=首次写入不洗键）
+list_registered(tenant_id=) 恒按行租户键过滤（None=系统视图字节不变）
+_search_agents 必选 tenant_id 恒按派生租户过滤+读真源=acp_agent_registry
（与 /registry、/detect.session_agents 一条真源；「未注册不进列表」语义延续）。

本文件 tmp 全隔离（AGENT_REGISTRY_DB/USERS_DB/ENTERPRISE_DB/ACPPROXY_HTTP/
HOME 归 tmp，绝不碰生产 data/agent_registry.db）。
"""

import inspect
import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

USERS_DDL = (
    "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
    "password_hash TEXT, is_active INTEGER NOT NULL DEFAULT 1, "
    "tenant_id TEXT NOT NULL DEFAULT 'default')"
)

OLD_REGISTRY_DDL = (
    "CREATE TABLE acp_agent_registry ("
    "agent_id TEXT PRIMARY KEY, cmd TEXT NOT NULL DEFAULT '[]', "
    "registered INTEGER NOT NULL DEFAULT 0, session_ready INTEGER NOT NULL DEFAULT 0, "
    "probe_ok INTEGER, probe_detail TEXT, session_id TEXT, last_probe_at TEXT, "
    "probe_count INTEGER NOT NULL DEFAULT 0)"
)


def _seed_user(db_path, uid, username, tenant="default"):
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, ?)",
            (uid, username, tenant),
        )
        c.commit()
    return uid


def _token(uid):
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": "Bearer " + tok}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 注册表库 + tmp 身份库 + 死 acp-proxy 端口 + 真 search/agents router。

    probe 链路默认全假桩成功（known_agents/handshake_probe/session_probe），
    POST /probe 即可产出 registered=1 真行（两级全过语义），写点归属经真 API
    流入 _record。
    """
    reg_db = tmp_path / "agent_registry.db"
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("AGENT_REGISTRY_DB", str(reg_db))
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    monkeypatch.setenv("ACPPROXY_HTTP", "http://127.0.0.1:1")  # 死端口=fail-closed
    monkeypatch.setenv("HOME", str(tmp_path))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    import src.services.agent_registry as reg

    async def _known():
        return {"probe-alpha": {"cmd": ["fake"]}, "probe-beta": {"cmd": ["fake"]}}

    async def _handshake(agent_id, cmd=None):
        return {"ok": True, "detail": "L1 fake ok", "cmd": ["fake"]}

    async def _session(agent_id, timeout=None):
        return {"ok": True, "session_id": "sess-" + str(agent_id), "detail": "L2 fake ok"}

    monkeypatch.setattr(reg, "known_agents", _known)
    monkeypatch.setattr(reg, "handshake_probe", _handshake)
    monkeypatch.setattr(reg, "session_probe", _session)

    import src.api.agents as agents_api
    import src.api.search as search_api

    app = FastAPI()
    app.include_router(search_api.router, prefix="/api/search")
    app.include_router(agents_api.router, prefix="/api/agents")
    client = TestClient(app)

    user_tenant_a = _seed_user(users_db, str(uuid.uuid4()), "alice-a", "tenant-a")
    user_tenant_b = _seed_user(users_db, str(uuid.uuid4()), "bob-b", "tenant-b")
    user_default = _seed_user(users_db, str(uuid.uuid4()), "carol-default", "default")

    yield {
        "client": client,
        "reg_db": str(reg_db),
        "user_tenant_a": user_tenant_a,
        "user_tenant_b": user_tenant_b,
        "user_default": user_default,
    }


def _raw_rows(reg_db):
    with sqlite3.connect(reg_db) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM acp_agent_registry")]


def _raw_row(reg_db, agent_id):
    for r in _raw_rows(reg_db):
        if r["agent_id"] == agent_id:
            return r
    raise AssertionError(f"row {agent_id!r} not found in registry")


def _probe(client, headers, agent_id):
    r = client.post(
        "/api/agents/probe",
        json={"agent_id": agent_id, "cmd": ["fake"], "verify_session": True},
        headers=headers or {},
    )
    return r


def _seed_registered(reg_db, agent_id, tenant_id=None):
    """经真写点 _record 落 registered=1 行（search/list 过滤断言需要在列行）。"""
    import src.services.agent_registry as reg

    return reg._record(
        agent_id,
        ["fake"],
        probe_ok=True,
        probe_detail="seeded ok",
        session_ok=True,
        session_id="s-" + agent_id,
        tenant_id=tenant_id,
    )


def _search(client, headers, params=None):
    p = {"q": "probe", "sources": "agents", "limit": 10}
    p.update(params or {})
    r = client.get("/api/search/unified", params=p, headers=headers or {})
    assert r.status_code == 200
    return [m["title"] for m in r.json()["by_source"]["agents"]]


# ── 迁移与单一判法 ─────────────────────────────────────────


class TestMigrationAndRowTenant:
    def test_old_schema_backfill_and_index(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_REGISTRY_DB", str(tmp_path / "old.db"))
        with sqlite3.connect(tmp_path / "old.db") as c:
            c.execute(OLD_REGISTRY_DDL)
            c.execute("INSERT INTO acp_agent_registry (agent_id, probe_count) VALUES ('legacy', 3)")
            c.commit()
        import src.services.agent_registry as reg

        entry = reg.get_entry("legacy")  # 触发 _conn 自愈
        assert entry is not None
        with sqlite3.connect(tmp_path / "old.db") as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(acp_agent_registry)")}
            idx = [r[1] for r in c.execute("PRAGMA index_list(acp_agent_registry)")]
        assert "tenant_id" in cols  # ALTER 自愈补列
        assert entry["tenant_id"] == "default"  # 存量行回填 'default'
        assert "idx_acp_agents_tenant" in idx  # 索引补列之后建（③c-2① 坑延续）

    def test_agent_row_tenant_fallback(self):
        from src.services.agent_registry import agent_row_tenant

        assert agent_row_tenant({}) == "default"
        assert agent_row_tenant({"tenant_id": ""}) == "default"
        assert agent_row_tenant({"tenant_id": "   "}) == "default"
        assert agent_row_tenant({"tenant_id": None}) == "default"
        assert agent_row_tenant({"tenant_id": "tenant-a"}) == "tenant-a"


# ── 写点归属 / 粘性 / 劫持闸 ────────────────────────────────


class TestRecordAxis:
    def test_record_row_attribution(self, env):
        m = _seed_registered(env["reg_db"], "attr-1", tenant_id="tenant-a")
        assert m["tenant_id"] == "tenant-a"
        assert _raw_row(env["reg_db"], "attr-1")["tenant_id"] == "tenant-a"
        m2 = _seed_registered(env["reg_db"], "attr-2")  # 缺省=系统写方
        assert m2["tenant_id"] == "default"
        assert _raw_row(env["reg_db"], "attr-2")["tenant_id"] == "default"
        m3 = _seed_registered(env["reg_db"], "attr-3", tenant_id="  ")  # 空白兜底
        assert m3["tenant_id"] == "default"

    def test_record_sticky_no_wash(self, env):
        m = _seed_registered(env["reg_db"], "sticky-1", tenant_id="tenant-a")
        assert m["tenant_id"] == "tenant-a"
        # 同租户再探（ON CONFLICT 不带 tenant_id：粘性不洗键）
        import src.services.agent_registry as reg

        m2 = reg._record(
            "sticky-1",
            ["fake"],
            probe_ok=True,
            probe_detail="reprobe",
            session_ok=True,
            tenant_id="tenant-a",
        )
        assert m2["tenant_id"] == "tenant-a"
        assert _raw_row(env["reg_db"], "sticky-1")["tenant_id"] == "tenant-a"
        assert _raw_row(env["reg_db"], "sticky-1")["probe_count"] == 2

    def test_hijack_gate_row_zero_touch(self, env):
        _seed_registered(env["reg_db"], "hij-1", tenant_id="tenant-a")
        before = _raw_row(env["reg_db"], "hij-1")
        import src.services.agent_registry as reg

        with pytest.raises(reg.AgentTenantDenied):
            reg._record(
                "hij-1",
                ["evil"],
                probe_ok=False,
                probe_detail="evil overwrite",
                session_ok=False,
                tenant_id="tenant-b",
            )
        after = _raw_row(env["reg_db"], "hij-1")
        assert after == before  # 行零改写（含 probe_count 不递增）


# ── list 过滤 ──────────────────────────────────────────────


class TestListAxis:
    def test_list_system_view_all(self, env):
        _seed_registered(env["reg_db"], "lv-1", tenant_id="tenant-a")
        _seed_registered(env["reg_db"], "lv-2", tenant_id="tenant-b")
        _seed_registered(env["reg_db"], "lv-3")  # default
        import src.services.agent_registry as reg

        ids = [e["agent_id"] for e in reg.list_registered()]
        assert set(ids) == {"lv-1", "lv-2", "lv-3"}  # None=系统视图全量

    def test_list_tenant_sees_own_only(self, env):
        _seed_registered(env["reg_db"], "lv-4", tenant_id="tenant-a")
        _seed_registered(env["reg_db"], "lv-5", tenant_id="tenant-b")
        _seed_registered(env["reg_db"], "lv-6")
        import src.services.agent_registry as reg

        assert [e["agent_id"] for e in reg.list_registered(tenant_id="tenant-a")] == ["lv-4"]
        assert [e["agent_id"] for e in reg.list_registered(tenant_id="tenant-b")] == ["lv-5"]

    def test_list_default_sees_default_and_unkeyed(self, env):
        _seed_registered(env["reg_db"], "lv-7", tenant_id="tenant-a")
        _seed_registered(env["reg_db"], "lv-8")
        with sqlite3.connect(env["reg_db"]) as c:
            c.execute(
                "INSERT INTO acp_agent_registry "
                "(agent_id, registered, session_ready, probe_ok, probe_detail, tenant_id) "
                "VALUES ('lv-9', 1, 1, 1, 'raw unkeyed', '')"
            )
            c.commit()
        import src.services.agent_registry as reg

        ids = [e["agent_id"] for e in reg.list_registered(tenant_id="default")]
        assert set(ids) == {"lv-8", "lv-9"}  # 缺键行判 'default'（归一值）


# ── 搜索面（读真源+加轴） ──────────────────────────────────


class TestSearchFace:
    def test_search_true_source_registry_rows(self, env):
        """读真源硬证：无 agents.json 也可搜到注册表真行（修前恒空集）。"""
        _seed_registered(env["reg_db"], "probe-alpha", tenant_id="default")
        titles = _search(env["client"], None)
        assert titles == ["probe-alpha"]

    def test_search_cross_tenant_invisibility_and_limit(self, env):
        # tenant 行先落（旧代码零过滤时占名额在前）——limit 只计可见命中
        _seed_registered(env["reg_db"], "probe-hid", tenant_id="tenant-a")
        _seed_registered(env["reg_db"], "probe-vis", tenant_id="default")
        titles = _search(env["client"], None, {"limit": 1})
        assert titles == ["probe-vis"]  # 跨租户行不占名额
        titles_a = _search(env["client"], _token(env["user_tenant_a"]), {"limit": 1})
        assert titles_a == ["probe-hid"]  # 派生轴非自报：A 只见己行

    def test_search_shape_unchanged(self, env):
        _seed_registered(env["reg_db"], "probe-alpha", tenant_id="default")
        r = env["client"].get(
            "/api/search/unified",
            params={"q": "probe", "sources": "agents", "limit": 10},
        )
        item = r.json()["by_source"]["agents"][0]
        assert set(item.keys()) == {"source", "icon", "title", "snippet", "agent_id"}

    def test_phantom_source_ignored_and_static_lock(self, env, tmp_path, monkeypatch):
        """幽灵源 ~/.openmate/agents.json 即使存在也不再是搜索真源。"""
        p = tmp_path / ".openmate"
        p.mkdir()
        (p / "agents.json").write_text(
            json.dumps([{"id": "phantom", "name": "probe-phantom", "description": "probe"}])
        )
        monkeypatch.setenv("HOME", str(tmp_path))
        _seed_registered(env["reg_db"], "unrelated", tenant_id="default")
        assert _search(env["client"], None) == []  # 幽灵行不进结果

        import src.api.search as search_api

        src_text = inspect.getsource(search_api._search_agents)
        # 读真源收口锁死：代码体不再有文件读取面（docstring 记档不算读）
        assert "expanduser" not in src_text
        assert "json.load" not in src_text
        assert "open(" not in src_text


# ── API 全链 ───────────────────────────────────────────────


class TestAPIAxis:
    def test_probe_row_attribution(self, env):
        r1 = _probe(env["client"], None, "probe-alpha")
        assert r1.status_code == 200
        assert r1.json()["registered"] is True  # 假桩两级全过=registered=1
        assert r1.json()["registry"]["tenant_id"] == "default"  # 匿名='default'
        assert _raw_row(env["reg_db"], "probe-alpha")["tenant_id"] == "default"

        r2 = _probe(env["client"], _token(env["user_tenant_a"]), "probe-beta")
        assert r2.status_code == 200
        assert r2.json()["registry"]["tenant_id"] == "tenant-a"  # 派生租户真流入
        assert _raw_row(env["reg_db"], "probe-beta")["tenant_id"] == "tenant-a"

    def test_probe_hijack_403_row_zero_touch(self, env):
        assert _probe(env["client"], _token(env["user_tenant_a"]), "probe-alpha").status_code == 200
        before = _raw_row(env["reg_db"], "probe-alpha")
        r = _probe(env["client"], _token(env["user_tenant_b"]), "probe-alpha")
        assert r.status_code == 403
        assert "another tenant" in r.json()["detail"]
        assert _raw_row(env["reg_db"], "probe-alpha") == before  # 行零改写

    def test_registry_filtered_and_additive_key(self, env):
        _seed_registered(env["reg_db"], "reg-1", tenant_id="tenant-a")
        _seed_registered(env["reg_db"], "reg-2")  # default
        r0 = env["client"].get("/api/agents/registry")
        assert r0.status_code == 200
        assert [a["agent_id"] for a in r0.json()["agents"]] == ["reg-2"]  # 匿名契约
        assert r0.json()["agents"][0]["tenant_id"] == "default"  # 加性键=归一值
        rA = env["client"].get("/api/agents/registry", headers=_token(env["user_tenant_a"]))
        assert [a["agent_id"] for a in rA.json()["agents"]] == ["reg-1"]
        assert rA.json()["agents"][0]["tenant_id"] == "tenant-a"

    def test_detect_session_agents_filtered(self, env):
        _seed_registered(env["reg_db"], "det-1", tenant_id="tenant-a")
        _seed_registered(env["reg_db"], "det-2")
        import src.api.agents as agents_api

        agents_api._detect_cache = None
        agents_api._detect_cache_time = 0.0
        r0 = env["client"].get("/api/agents/detect")
        assert [a["agent_id"] for a in r0.json()["session_agents"]] == ["det-2"]
        rA = env["client"].get("/api/agents/detect", headers=_token(env["user_tenant_a"]))
        assert [a["agent_id"] for a in rA.json()["session_agents"]] == ["det-1"]

    def test_bad_token_401(self, env):
        bad = {"Authorization": "Bearer not-a-real-token"}
        assert env["client"].get("/api/agents/registry", headers=bad).status_code == 401
        assert _probe(env["client"], bad, "probe-alpha").status_code == 401
        import src.services.agent_registry as reg

        reg._conn().close()  # 确保表在场（401 先于副作用=零行硬证非空表假象）
        assert _raw_rows(env["reg_db"]) == []  # 401 先于副作用零落库
