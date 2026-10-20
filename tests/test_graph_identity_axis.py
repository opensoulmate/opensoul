"""graph 面 user_id 轴 bind_identity 收口测试（user 从认证派生，禁自报冒读）。

修前（HEAD 源码实证）：/api/graph 的 7 个带 user_id Query 端点（entities 列表/
详情/neighbors/paths/relations/full//）user_id 完全客户端自报直接进档案键
（认证请求可冒读他人实体/关系/图），与 F3b-⑥ 修前 search 面同型（H1-②b-⑦a
诚实边界②「user_id 自报轴与既有 graph 面同轴不扩大…bind_identity 收口可
另拍」留档件）。

本文件 tmp 全隔离（USERS_DB/ENTERPRISE_DB），绝不写生产库。数据面走
api.graph 模块接缝捕获桩（真 router 真 bind_identity）。
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


def _md5_uuid(s: str) -> uuid.UUID:
    h = hashlib.md5(s.encode()).hexdigest()
    return uuid.UUID(f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}")


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


ENT_ID = str(uuid.uuid4())
ENT2_ID = str(uuid.uuid4())


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """tmp 身份库 + graph 服务捕获桩（真 router 真 bind_identity，数据面全隔离）。"""
    users_db = tmp_path / "users.db"
    ent_db = tmp_path / "enterprise.db"
    monkeypatch.setenv("USERS_DB", str(users_db))
    monkeypatch.setenv("ENTERPRISE_DB", str(ent_db))
    with sqlite3.connect(users_db) as c:
        c.execute(USERS_DDL)
        c.commit()

    import src.api.graph as graph_api

    calls: dict = {
        "list_entities": [],
        "get_entity": [],
        "neighbors": [],
        "find_path": [],
        "list_relations": [],
        "get_graph": [],
        "create_relation": [],
    }

    async def _list_entities(uid, entity_type=None, offset=0, limit=50):
        calls["list_entities"].append({"uid": uid})
        return []

    async def _get_entity_with_relations(entity_id, uid):
        calls["get_entity"].append({"entity_id": entity_id, "uid": uid})
        return {"id": entity_id, "user_id": uid}

    async def _get_entity_neighbors(uid, entity_id):
        calls["neighbors"].append({"uid": uid, "entity_id": entity_id})
        return {"entity": {"id": entity_id}, "neighbors": []}

    async def _find_path(uid, source_id, target_id, max_depth):
        calls["find_path"].append({"uid": uid, "source_id": source_id, "target_id": target_id})
        return ([], False)

    async def _list_relations(uid, offset=0, limit=100):
        calls["list_relations"].append({"uid": uid})
        return []

    async def _get_graph(uid, depth=2, entity_id=None):
        calls["get_graph"].append({"uid": uid, "entity_id": entity_id})
        return {"nodes": [], "edges": []}

    async def _create_relation(data, uid):
        calls["create_relation"].append({"data": data, "uid": uid})
        return {
            "id": uuid.uuid4(),
            "source_entity_id": data.source_entity_id,
            "target_entity_id": data.target_entity_id,
            "relation_type": data.relation_type,
            "properties": data.properties,
            "created_at": "2026-01-01T00:00:00",
        }

    monkeypatch.setattr(graph_api, "list_entities", _list_entities)
    monkeypatch.setattr(graph_api, "get_entity_with_relations", _get_entity_with_relations)
    monkeypatch.setattr(graph_api, "get_entity_neighbors", _get_entity_neighbors)
    monkeypatch.setattr(graph_api, "find_path", _find_path)
    monkeypatch.setattr(graph_api, "list_relations", _list_relations)
    monkeypatch.setattr(graph_api, "get_graph", _get_graph)
    monkeypatch.setattr(graph_api, "create_relation", _create_relation)

    app = FastAPI()
    app.include_router(graph_api.router, prefix="/api/graph")
    client = TestClient(app)

    user_a = _seed_user(users_db, str(uuid.uuid4()), "alice-g")
    user_b = _seed_user(users_db, str(uuid.uuid4()), "bob-g")
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


class TestGraphIdentityAxis:
    def test_anon_self_report_preserved(self, env):
        """匿名保留自报（既有本地多档案语义，F3b-② 契约零变化）。"""
        resp = env["client"].get("/api/graph/entities", params={"user_id": "alice"})
        assert resp.status_code == 200
        assert env["calls"]["list_entities"][0]["uid"] == _md5_uuid("alice")

    def test_anon_default_contract_unchanged(self, env):
        """匿名缺省 user_id='default' 档案键与修前逐值一致（entities+full 两面）。"""
        r1 = env["client"].get("/api/graph/entities")
        r2 = env["client"].get("/api/graph/full")
        assert r1.status_code == r2.status_code == 200
        assert env["calls"]["list_entities"][0]["uid"] == _md5_uuid("default")
        assert env["calls"]["get_graph"][0]["uid"] == _md5_uuid("default")

    def test_authed_undeclared_derives_user(self, env):
        """认证+未声明→档案键=派生（users.id），修前自报 'default' 顶替。"""
        resp = env["client"].get("/api/graph/entities", headers=_token(env["user_a"]))
        assert resp.status_code == 200
        assert env["calls"]["list_entities"][0]["uid"] == uuid.UUID(env["user_a"])

    def test_authed_declare_default_is_undeclared(self, env):
        """'default'/空=未声明→取派生（不误伤不传参调用方）。"""
        resp = env["client"].get(
            "/api/graph/entities",
            params={"user_id": "default"},
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 200
        assert env["calls"]["list_entities"][0]["uid"] == uuid.UUID(env["user_a"])

    def test_authed_conflict_403_zero_side_effect(self, env):
        """认证请求自报他人档案=403 冒读面收口（修前 200 直接读 victim 实体）。"""
        resp = env["client"].get(
            "/api/graph/entities",
            params={"user_id": env["user_b"]},
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]
        assert _all_uids(env["calls"]) == []  # 零副作用：服务函数未被调用

    def test_authed_matching_declared_ok(self, env):
        """自报与派生一致=确认绑定，照常查询。"""
        resp = env["client"].get(
            "/api/graph/entities",
            params={"user_id": env["user_a"]},
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 200
        assert env["calls"]["list_entities"][0]["uid"] == uuid.UUID(env["user_a"])

    def test_bad_token_401_zero_side_effect(self, env):
        resp = env["client"].get("/api/graph/entities", headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 401
        assert _all_uids(env["calls"]) == []


class TestAllReadEndpointsSameAxis:
    """7 个带 user_id 自报轴端点同轴收口（派生流入+冒读 403 逐点）。"""

    ENDPOINTS = [
        ("list_entities", "GET", "/api/graph/entities", {}),
        ("get_entity", "GET", f"/api/graph/entities/{ENT_ID}", {}),
        ("neighbors", "GET", f"/api/graph/entities/{ENT_ID}/neighbors", {}),
        ("find_path", "GET", "/api/graph/paths", {"source_id": ENT_ID, "target_id": ENT2_ID}),
        ("list_relations", "GET", "/api/graph/relations", {}),
        ("get_graph", "GET", "/api/graph/full", {}),
        ("get_graph", "GET", "/api/graph/", {}),
    ]

    @pytest.mark.parametrize("seam,method,path,params", ENDPOINTS)
    def test_derived_uid_reaches_seam(self, env, seam, method, path, params):
        resp = env["client"].get(path, params=params, headers=_token(env["user_a"]))
        assert resp.status_code == 200
        assert env["calls"][seam], f"seam {seam} 未被调用"
        assert env["calls"][seam][0]["uid"] == uuid.UUID(env["user_a"])

    @pytest.mark.parametrize("seam,method,path,params", ENDPOINTS)
    def test_conflict_403_on_every_endpoint(self, env, seam, method, path, params):
        merged = dict(params)
        merged["user_id"] = env["user_b"]
        resp = env["client"].get(path, params=merged, headers=_token(env["user_a"]))
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]
        assert _all_uids(env["calls"]) == []  # 零副作用硬反证

    def test_paths_uid_flows_to_visibility_and_find(self, env):
        """paths 的 uid 同时流入两端可见性检查与 find_path（同派生禁分叉）。"""
        resp = env["client"].get(
            "/api/graph/paths",
            params={"source_id": ENT_ID, "target_id": ENT2_ID},
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 200
        uid = uuid.UUID(env["user_a"])
        assert [e["uid"] for e in env["calls"]["get_entity"]] == [uid, uid]
        assert env["calls"]["find_path"][0]["uid"] == uid


class TestContractUnchanged:
    """既有契约零变化面（身份轴之外的行为锁死）。"""

    def test_entity_detail_404_contract(self, env, monkeypatch):
        """缺实体 404 契约不变（身份派生后仍逐字一致）。"""
        import src.api.graph as graph_api

        async def _none(entity_id, uid):
            return None

        monkeypatch.setattr(graph_api, "get_entity_with_relations", _none)
        resp = env["client"].get(f"/api/graph/entities/{ENT_ID}")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Entity not found"

    def test_paths_missing_entity_404_contract(self, env, monkeypatch):
        """paths 两端任一不可见=404（缺/跨用户同形）契约不变。"""
        import src.api.graph as graph_api

        async def _none(entity_id, uid):
            return None

        monkeypatch.setattr(graph_api, "get_entity_with_relations", _none)
        resp = env["client"].get(
            "/api/graph/paths", params={"source_id": ENT_ID, "target_id": ENT2_ID}
        )
        assert resp.status_code == 404
        assert env["calls"]["find_path"] == []

    def test_post_relations_derived_uid_flows(self, env):
        """POST /relations 写面身份轴收口（H1-②b-⑦a-⑤ 边界②执行件）：派生键流入
        归属闸（修前无身份轴无归属检查，边界锁死断言契约随更新）。"""
        resp = env["client"].post(
            "/api/graph/relations",
            json={
                "source_entity_id": ENT_ID,
                "target_entity_id": ENT2_ID,
                "relation_type": "related",
            },
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 200
        assert len(env["calls"]["create_relation"]) == 1
        assert env["calls"]["create_relation"][0]["uid"] == uuid.UUID(env["user_a"])

    def test_post_relations_anon_self_report_preserved(self, env):
        """匿名保留自报（既有本地多档案语义契约零变化）。"""
        resp = env["client"].post(
            "/api/graph/relations",
            params={"user_id": "alice"},
            json={
                "source_entity_id": ENT_ID,
                "target_entity_id": ENT2_ID,
                "relation_type": "related",
            },
        )
        assert resp.status_code == 200
        assert env["calls"]["create_relation"][0]["uid"] == _md5_uuid("alice")

    def test_post_relations_conflict_403_zero_side_effect(self, env):
        """认证请求自报他人档案=403（写面冒写面收口），服务函数零调用硬反证。"""
        resp = env["client"].post(
            "/api/graph/relations",
            params={"user_id": env["user_b"]},
            json={
                "source_entity_id": ENT_ID,
                "target_entity_id": ENT2_ID,
                "relation_type": "related",
            },
            headers=_token(env["user_a"]),
        )
        assert resp.status_code == 403
        assert "user mismatch" in resp.json()["detail"]
        assert env["calls"]["create_relation"] == []

    def test_post_relations_gate_404_mapping(self, env, monkeypatch):
        """归属闸拒（create_relation 回 None）→404 'Entity not found'（缺/跨用户
        同形，entity detail/paths 同契约）。"""
        import src.api.graph as graph_api

        async def _reject(data, uid):
            return None

        monkeypatch.setattr(graph_api, "create_relation", _reject)
        resp = env["client"].post(
            "/api/graph/relations",
            json={
                "source_entity_id": ENT_ID,
                "target_entity_id": ENT2_ID,
                "relation_type": "related",
            },
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Entity not found"

    def test_health_stats_no_auth_axis(self, env):
        """health/stats 系统视图无身份轴（既有契约零变化）。"""
        r1 = env["client"].get("/api/graph/health")
        assert r1.status_code == 200
        assert r1.json()["status"] == "ok"


class TestStaticWiring:
    def test_seven_endpoints_bind_identity(self):
        """静态接线：8 端点恰 8 处 bind_identity 真调用+Header 形参（禁漏收口；
        POST /relations 写面随 H1-②b-⑦a-⑤ 边界②收口入列）。"""
        import ast
        from pathlib import Path

        src = (Path(__file__).resolve().parent.parent / "src" / "api" / "graph.py").read_text(
            encoding="utf-8"
        )
        assert src.count("bind_identity(authorization") == 8
        assert src.count("authorization: str " + chr(124) + " None = Header(None)") == 8
        tree = ast.parse(src)
        funcs = {
            n.name: n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in (
            "get_entities",
            "get_entity_detail",
            "get_neighbors",
            "get_paths",
            "get_relations",
            "get_full_graph",
            "get_graph_data",
            "add_relation",
        ):
            arg_names = [a.arg for a in funcs[name].args.args]
            assert "authorization" in arg_names, f"{name} 缺 authorization 形参"
            # bind_identity 调用先于 _resolve_user_id（派生禁被自报顶替）
            body_src = ast.dump(funcs[name])
            assert "bind_identity" in body_src
