"""cortex-enhanced 面 user_id 轴 bind_identity 收口测试（user 从认证派生，禁自报冒读/冒写）。

F3b-⑥ 已裁决语义（user_id 自报轴改认证派生）同族既有面延伸（F3b-④b 先例
「已裁决语义的同族既有面延伸=无需拍板可立即执行件」）：/api/cortex 的 8 个
带 user_id Query 端点（graphrag build/query、recommend trending/recent/{id}、
quality report/batch/score/{id}）修前 user_id 完全客户端自报直接进档案键——
认证请求可冒读/冒写他人知识图谱/推荐/质量面（F3b-⑥ search 面、H1-②b-⑦a-⑤
graph 读面、-⑥ graph 写面、F3b-⑦ entity CRUD 面同型，本件=该族 cortex 智能面
收口）。

语义与 F3b-⑥ 逐字一致：认证=派生档案键、自报只可确认绑定、矛盾=403、
'default'/空=未声明→取派生、匿名=保留自报（既有本地多档案语义契约零变化）、
坏 token=401 零副作用。档案键形与 entity/graph/search 家族同源
（uid=str(_resolve_user_id(identity.user_id))）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB），绝不写生产库。数据面走
cortex 单例模块接缝捕获桩（真 router 真 bind_identity）。
"""

import hashlib
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


def _md5_uuid_str(s: str) -> str:
    h = hashlib.md5(s.encode()).hexdigest()
    return str(uuid.UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"))


def _seed_user(db_path: Path, uid: str, username: str, tenant: str = "default") -> str:
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO users (id, username, password_hash, is_active, tenant_id) "
            "VALUES (?, ?, 'x', 1, ?)",
            (uid, username, tenant),
        )
        c.commit()
    return uid


def _token(uid: str) -> dict:
    from src.services.auth import create_access_token

    tok = create_access_token(user_id=uuid.UUID(uid), role="user")
    return {"Authorization": f"Bearer {tok}"}


QUERY_BODY = {"entity_name": "probe", "depth": 2}

# (seam, method, path, body)：8 个带 user_id 自报轴端点。
ENDPOINTS = [
    ("build_graph_from_knowledge", "POST", "/api/cortex/graphrag/build", {}),
    ("query_graph", "POST", "/api/cortex/graphrag/query", QUERY_BODY),
    ("get_trending", "GET", "/api/cortex/recommend/trending", None),
    ("get_recent", "GET", "/api/cortex/recommend/recent", None),
    ("recommend", "GET", "/api/cortex/recommend/k-x", None),
    ("get_quality_report", "GET", "/api/cortex/quality/report", None),
    ("score_all", "GET", "/api/cortex/quality/batch", None),
    ("score_knowledge", "GET", "/api/cortex/quality/score/k-x", None),
]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + cortex 单例捕获桩（真 router 真 bind_identity，数据面全隔离）。"""
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    import src.api.cortex_enhanced as cortex_api

    calls: dict = {seam: [] for seam, _, _, _ in ENDPOINTS}

    async def _build(db, user_id):
        calls["build_graph_from_knowledge"].append({"uid": user_id})
        return {"entities_new": 1, "relations_new": 0, "knowledge_scanned": 1}

    async def _query(db, user_id, entity_name, depth):
        calls["query_graph"].append({"uid": user_id})
        return {"entity": {"name": entity_name}, "neighbors": []}

    async def _trending(db, user_id, limit):
        calls["get_trending"].append({"uid": user_id})
        return []

    async def _recent(db, user_id, limit):
        calls["get_recent"].append({"uid": user_id})
        return []

    async def _recommend(db, user_id, knowledge_id, limit):
        calls["recommend"].append({"uid": user_id})
        return []

    async def _quality_report(db, user_id):
        calls["get_quality_report"].append({"uid": user_id})
        return {"total": 0}

    async def _score_all(db, user_id, limit):
        calls["score_all"].append({"uid": user_id})
        return []

    async def _score_knowledge(db, user_id, knowledge_id):
        calls["score_knowledge"].append({"uid": user_id})
        return {"knowledge_id": knowledge_id}

    monkeypatch.setattr(cortex_api.graphrag, "build_graph_from_knowledge", _build)
    monkeypatch.setattr(cortex_api.graphrag, "query_graph", _query)
    monkeypatch.setattr(cortex_api.recommender, "get_trending", _trending)
    monkeypatch.setattr(cortex_api.recommender, "get_recent", _recent)
    monkeypatch.setattr(cortex_api.recommender, "recommend", _recommend)
    monkeypatch.setattr(cortex_api.scorer, "get_quality_report", _quality_report)
    monkeypatch.setattr(cortex_api.scorer, "score_all", _score_all)
    monkeypatch.setattr(cortex_api.scorer, "score_knowledge", _score_knowledge)

    app = FastAPI()
    app.include_router(cortex_api.router, prefix="/api/cortex")
    client = TestClient(app)

    user_a = _seed_user(users_db, str(uuid.uuid4()), "alice-cx")
    user_b = _seed_user(users_db, str(uuid.uuid4()), "bob-cx")
    return {
        "client": client,
        "calls": calls,
        "user_a": user_a,
        "user_b": user_b,
    }


def _all_uids(calls: dict):
    """回收全部捕获桩收到的 uid（跨桩对账）。"""
    out = []
    for entries in calls.values():
        for e in entries:
            if "uid" in e:
                out.append(e["uid"])
    return out


class TestCortexIdentityAxis:
    def test_anon_self_report_preserved(self, env):
        """匿名保留自报（既有本地多档案语义，F3b-② 契约零变化）。"""
        resp = env["client"].get("/api/cortex/recommend/recent", params={"user_id": "alice"})
        assert resp.status_code == 200
        assert env["calls"]["get_recent"][0]["uid"] == _md5_uuid_str("alice")

    def test_anon_default_contract_unchanged(self, env):
        """匿名缺省 user_id='default' 档案键与家族正典键形一致（trending+recent 两面）。"""
        r1 = env["client"].get("/api/cortex/recommend/recent")
        r2 = env["client"].get("/api/cortex/recommend/trending")
        assert r1.status_code == r2.status_code == 200
        assert env["calls"]["get_recent"][0]["uid"] == _md5_uuid_str("default")
        assert env["calls"]["get_trending"][0]["uid"] == _md5_uuid_str("default")

    def test_authed_undeclared_derives_user(self, env):
        """认证+未声明→档案键=派生（users.id），修前自报 'default' 顶替。"""
        resp = env["client"].get("/api/cortex/recommend/recent", headers=_token(env["user_a"]))
        assert resp.status_code == 200
        assert env["calls"]["get_recent"][0]["uid"] == str(uuid.UUID(env["user_a"]))

    def test_authed_declare_default_is_undeclared(self, env):
        """'default'/空=未声明→取派生（不误伤不传参调用方）。"""
        resp = env["client"].get(
            "/api/cortex/recommend/recent",
            params={"user_id": "default"},
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 200
        assert env["calls"]["get_recent"][0]["uid"] == str(uuid.UUID(env["user_a"]))

    def test_authed_conflict_403_zero_side_effect(self, env):
        """认证请求自报他人档案=403 冒读/冒写面收口（修前 200 直接读写 victim 面）。"""
        resp = env["client"].get(
            "/api/cortex/recommend/recent",
            params={"user_id": env["user_b"]},
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]
        assert _all_uids(env["calls"]) == []  # 零副作用：引擎函数未被调用

    def test_authed_matching_declared_ok(self, env):
        """自报与派生一致=确认绑定，照常查询。"""
        resp = env["client"].get(
            "/api/cortex/recommend/recent",
            params={"user_id": env["user_a"]},
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 200
        assert env["calls"]["get_recent"][0]["uid"] == str(uuid.UUID(env["user_a"]))

    def test_bad_token_401_zero_side_effect(self, env):
        resp = env["client"].get(
            "/api/cortex/recommend/recent", headers={"Authorization": "Bearer garbage"}
        )
        assert resp.status_code == 401
        assert _all_uids(env["calls"]) == []


class TestAllEndpointsSameAxis:
    """8 个带 user_id 自报轴端点同轴收口（派生流入+冒读冒写 403 逐点）。"""

    def _req(self, env, method, path, body, headers=None, params=None):
        kw = {"params": params or {}}
        if body is not None:
            kw["json"] = body
        return env["client"].request(method, path, headers=headers or {}, **kw)

    @pytest.mark.parametrize("seam,method,path,body", ENDPOINTS)
    def test_derived_uid_reaches_seam(self, env, seam, method, path, body):
        resp = self._req(env, method, path, body, headers=_token(env["user_a"]))
        assert resp.status_code == 200
        assert env["calls"][seam], f"seam {seam} 未被调用"
        assert env["calls"][seam][0]["uid"] == str(uuid.UUID(env["user_a"]))

    @pytest.mark.parametrize("seam,method,path,body", ENDPOINTS)
    def test_conflict_403_on_every_endpoint(self, env, seam, method, path, body):
        resp = self._req(
            env,
            method,
            path,
            body,
            headers=_token(env["user_a"]),
            params={"user_id": env["user_b"]},
        )
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]
        assert _all_uids(env["calls"]) == []  # 零副作用硬反证（含 build 禁冒写）


class TestContractUnchanged:
    """既有契约零变化面（身份轴之外的行为锁死）。"""

    def test_extract_no_identity_axis(self, env):
        """/graphrag/extract 纯抽取无 user_id/无 DB 写，不进收口（既有契约）。"""
        resp = env["client"].post(
            "/api/cortex/graphrag/extract", params={"text": "华为技术生产交换机。"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["entity_count"] >= 1

    def test_health_system_view_no_auth(self, env):
        """/enhanced/health 系统视图不闸（F3b-⑥ 边界③同款）。"""
        resp = env["client"].get("/api/cortex/enhanced/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_query_error_contract_404(self, env, monkeypatch):
        """query_graph 回 error 键=404 既有契约不变。"""
        import src.api.cortex_enhanced as cortex_api

        async def _err(db, user_id, entity_name, depth):
            return {"error": "Entity not found"}

        monkeypatch.setattr(cortex_api.graphrag, "query_graph", _err)
        resp = env["client"].post("/api/cortex/graphrag/query", json=QUERY_BODY)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Entity not found"

    def test_score_error_contract_404(self, env, monkeypatch):
        """score_knowledge 回 error 键=404 既有契约不变。"""
        import src.api.cortex_enhanced as cortex_api

        async def _err(db, user_id, knowledge_id):
            return {"error": "Knowledge not found"}

        monkeypatch.setattr(cortex_api.scorer, "score_knowledge", _err)
        resp = env["client"].get("/api/cortex/quality/score/k-x")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Knowledge not found"

    def test_recommend_shape_unchanged(self, env):
        """recommend/{id} 响应形状逐键不变（knowledge_id/recommendations/count）。"""
        resp = env["client"].get("/api/cortex/recommend/k-x")
        assert resp.status_code == 200
        assert set(resp.json().keys()) == {"knowledge_id", "recommendations", "count"}


class TestStaticWiring:
    def test_wiring_shape(self):
        """静态接线：恰 8 处 bind_identity 派生调用 + 8 个 authorization Header 形参、
        旧自报直通形清零、import 在文件顶部。"""
        src = Path("src/api/cortex_enhanced.py").read_text(encoding="utf-8")
        assert src.count("bind_identity(authorization, " + chr(34) + chr(34) + ", user_id)") == 8
        assert "bind_identity(authorization)" not in src
        assert (
            src.count("= Header(None)") == 8
        )  # 8 个 authorization Header 形参（类型注解判法注解无关防显示层竖线损坏）
        assert "uid = user_id" not in src
        assert src.count("uid = str(_resolve_user_id(identity.user_id))") == 8
        import_line = "from src.services.tenant_binding import bind_identity"
        assert import_line in src
        assert src.index(import_line) < src.index("@router")
