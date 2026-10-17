"""H1-②b-⑦a graph 查询面出口测试 — /api/graph/entities/{id}/neighbors + /api/graph/paths。

H1-② 定性「graph 3 件真缺口」拆件⑦a：get_entity_neighbors/find_path 查询面无
API 出口（api/graph.py 修前只有 get_graph/list_relations/create_relation）。
连带接线前提收口（probe 实证非推断，/tmp/probe_graph_pre.py 修前三炸）：
①列漂移——services/graph.py SQL 写读 relation_type 而 relations 表 SQLite
真源列=type（main.py DDL），create_relation/两查询面读路径全炸；
②properties 序列化——dict 直接绑参（json.dumps 铁律）+ TEXT 直塞 dict 契约
（services/entity.py C3-⑪ 同款根因）；
③UUID/TEXT 比较——SQLite 行值恒 TEXT 与 UUID 对象恒不相等，出边邻居被误判
成自身（get_graph BFS 出边不展开同根）。

隔离纪律（TL-②/H1-②b-① 先例）：tmp opensoul.db 真 schema（与 src/main.py
lifespan DDL 同形，relations 真源列=type）+ db_pool 多绑定换 tmp SQLitePool，
绝不写生产 data/opensoul.db。
"""

import json
import sqlite3
import uuid

import pytest

# 与 src/main.py lifespan 建表 DDL 同形（relations 真源列=type，禁手抄漂移：
# 若 main.py 改形状须同步，行为面测试 14/15 会随真源炸）。
_GRAPH_DDL = """CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    name TEXT NOT NULL, type TEXT DEFAULT '',
    description TEXT DEFAULT '', properties TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
    target_id TEXT NOT NULL, type TEXT NOT NULL,
    properties TEXT DEFAULT '{}', created_at TEXT DEFAULT (datetime('now')));"""


@pytest.fixture()
def graph_client(tmp_path, monkeypatch):
    """graph API 隔离测试面：真实 router + tmp opensoul.db（真 schema），绝不写
    生产 data/opensoul.db。db_pool 三绑定（postgres/services.graph/services.entity）
    全换 tmp SQLitePool——api 端点 404 预查走 services.entity 同一数据面。
    """
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.graph as graph_api
    import src.database.postgres as postgres_mod
    import src.services.entity as entity_mod
    import src.services.graph as graph_mod
    from src.config import settings
    from src.database.postgres import SQLitePool

    tmp_os_db = tmp_path / "opensoul.db"
    with sqlite3.connect(tmp_os_db) as _c:
        _c.executescript(_GRAPH_DDL)
        _c.commit()

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_os_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(postgres_mod, "db_pool", tmp_pool)
    monkeypatch.setattr(graph_mod, "db_pool", tmp_pool)
    monkeypatch.setattr(entity_mod, "db_pool", tmp_pool)
    monkeypatch.setattr(graph_api, "db_pool", tmp_pool)

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(graph_api.router, prefix="/api/graph")
    app.state.graph_db = str(tmp_os_db)
    with TestClient(app) as c:
        yield c


def _uid(user_id: str = "user-a"):
    from src.api.graph import _resolve_user_id

    return _resolve_user_id(user_id)


def _seed_entity(db_path, user_id: str, name: str, etype: str = "person", properties=None) -> str:
    eid = str(uuid.uuid4())
    with sqlite3.connect(db_path) as _c:
        _c.execute(
            "INSERT INTO entities (id, user_id, name, type, description, properties) "
            "VALUES (?, ?, ?, ?, '', ?)",
            (eid, str(_uid(user_id)), name, etype, json.dumps(properties or {})),
        )
        _c.commit()
    return eid


def _seed_relation(db_path, source: str, target: str, rtype: str = "knows", properties=None) -> str:
    rid = str(uuid.uuid4())
    with sqlite3.connect(db_path) as _c:
        _c.execute(
            "INSERT INTO relations (id, source_id, target_id, type, properties) "
            "VALUES (?, ?, ?, ?, ?)",
            (rid, source, target, rtype, json.dumps(properties or {})),
        )
        _c.commit()
    return rid


def _counts(db_path) -> tuple[int, int]:
    with sqlite3.connect(db_path) as _c:
        e = _c.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        r = _c.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    return e, r


class TestNeighborsExit:
    """GET /api/graph/entities/{entity_id}/neighbors 出口行为面。"""

    def test_direction_and_exact_shape(self, graph_client):
        db = graph_client.app.state.graph_db
        a = _seed_entity(db, "user-a", "A")
        b = _seed_entity(db, "user-a", "B")
        c = _seed_entity(db, "user-a", "C")
        _seed_relation(db, a, b, rtype="knows")
        _seed_relation(db, c, a, rtype="mentors")

        resp = graph_client.get(f"/api/graph/entities/{a}/neighbors", params={"user_id": "user-a"})
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"entity", "neighbors"}
        assert set(data["entity"].keys()) == {"id", "name", "type"}
        assert data["entity"]["id"] == a
        by_name = {n["name"]: n for n in data["neighbors"]}
        assert len(data["neighbors"]) == 2
        for n in data["neighbors"]:
            assert set(n.keys()) == {"entity_id", "name", "type", "relation_type", "direction"}
        # DB 真源列=type，API 契约 relation_type（读侧映射硬证）
        assert by_name["B"]["relation_type"] == "knows"
        assert by_name["B"]["direction"] == "outgoing"
        assert by_name["C"]["relation_type"] == "mentors"
        assert by_name["C"]["direction"] == "incoming"

    def test_cross_user_neighbor_hidden_and_foreign_anchor_404(self, graph_client):
        db = graph_client.app.state.graph_db
        a = _seed_entity(db, "user-a", "A")
        foreign = _seed_entity(db, "user-b", "B2")
        _seed_relation(db, a, foreign, rtype="knows")

        resp = graph_client.get(f"/api/graph/entities/{a}/neighbors", params={"user_id": "user-a"})
        assert resp.status_code == 200
        assert resp.json()["neighbors"] == []  # 跨用户邻居不暴露

        # 他用户锚点：缺/跨用户同形 404（无存在性探测面）
        resp2 = graph_client.get(
            f"/api/graph/entities/{foreign}/neighbors", params={"user_id": "user-a"}
        )
        assert resp2.status_code == 404

    def test_missing_entity_404_zero_side_effects(self, graph_client):
        db = graph_client.app.state.graph_db
        before = _counts(db)
        ghost = str(uuid.uuid4())
        resp = graph_client.get(
            f"/api/graph/entities/{ghost}/neighbors", params={"user_id": "user-a"}
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Entity not found"
        assert _counts(db) == before

    def test_endpoint_delegates_to_service(self, graph_client, monkeypatch):
        """端点真委托 get_entity_neighbors（spy 硬证禁重实现第二套查询）。"""
        import src.api.graph as graph_api

        calls = []

        async def fake_neighbors(user_id, entity_id):
            calls.append((str(user_id), str(entity_id)))
            return {
                "entity": {"id": str(entity_id), "name": "A", "type": "person"},
                "neighbors": [],
            }

        monkeypatch.setattr(graph_api, "get_entity_neighbors", fake_neighbors)
        eid = str(uuid.uuid4())
        resp = graph_client.get(
            f"/api/graph/entities/{eid}/neighbors", params={"user_id": "user-a"}
        )
        assert resp.status_code == 200
        assert calls == [(str(_uid("user-a")), eid)]


class TestPathsExit:
    """GET /api/graph/paths 出口行为面。"""

    def test_direct_hop_step_shape(self, graph_client):
        db = graph_client.app.state.graph_db
        a = _seed_entity(db, "user-a", "A")
        b = _seed_entity(db, "user-a", "B")
        _seed_relation(db, a, b, rtype="knows")

        resp = graph_client.get(
            "/api/graph/paths",
            params={"source_id": a, "target_id": b, "user_id": "user-a"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"source_id", "target_id", "max_depth", "paths", "count"}
        assert data["count"] == 1
        assert data["max_depth"] == 5
        path = data["paths"][0]
        # 步形状单一源：每步恒 4 键，首步 via_relation=None
        for step in path:
            assert set(step.keys()) == {"entity_id", "name", "type", "via_relation"}
        assert [s["entity_id"] for s in path] == [a, b]
        assert path[0]["via_relation"] is None
        assert path[1]["via_relation"] == "knows"

    def test_multi_hop_order_and_labels(self, graph_client):
        db = graph_client.app.state.graph_db
        a = _seed_entity(db, "user-a", "A")
        b = _seed_entity(db, "user-a", "B")
        c = _seed_entity(db, "user-a", "C")
        _seed_relation(db, a, b, rtype="knows")
        _seed_relation(db, b, c, rtype="mentors")

        resp = graph_client.get(
            "/api/graph/paths", params={"source_id": a, "target_id": c, "user_id": "user-a"}
        )
        data = resp.json()
        assert data["count"] == 1
        path = data["paths"][0]
        assert [s["name"] for s in path] == ["A", "B", "C"]
        assert [s["via_relation"] for s in path] == [None, "knows", "mentors"]

    def test_no_route_empty_not_error(self, graph_client):
        db = graph_client.app.state.graph_db
        a = _seed_entity(db, "user-a", "A")
        d = _seed_entity(db, "user-a", "D")
        resp = graph_client.get(
            "/api/graph/paths", params={"source_id": a, "target_id": d, "user_id": "user-a"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["paths"] == []
        assert set(data.keys()) == {"source_id", "target_id", "max_depth", "paths", "count"}

    def test_max_depth_is_node_cap(self, graph_client):
        """max_depth=路径节点数上限（含起终点，实现既有语义如实锁定）。"""
        db = graph_client.app.state.graph_db
        names = ["A", "B", "C", "D", "E"]
        ids = [_seed_entity(db, "user-a", n) for n in names]
        for i in range(4):
            _seed_relation(db, ids[i], ids[i + 1], rtype="knows")

        ok = graph_client.get(
            "/api/graph/paths",
            params={"source_id": ids[0], "target_id": ids[4], "user_id": "user-a", "max_depth": 5},
        )
        assert ok.json()["count"] == 1
        too_short = graph_client.get(
            "/api/graph/paths",
            params={"source_id": ids[0], "target_id": ids[4], "user_id": "user-a", "max_depth": 4},
        )
        assert too_short.json()["count"] == 0  # 5 节点 > 4 上限，如实无路

    def test_invalid_max_depth_422(self, graph_client):
        db = graph_client.app.state.graph_db
        a = _seed_entity(db, "user-a", "A")
        b = _seed_entity(db, "user-a", "B")
        for bad in (0, -1, "abc"):
            resp = graph_client.get(
                "/api/graph/paths",
                params={
                    "source_id": a,
                    "target_id": b,
                    "user_id": "user-a",
                    "max_depth": bad,
                },
            )
            assert resp.status_code == 422, bad

    def test_missing_and_foreign_404_same_shape(self, graph_client):
        db = graph_client.app.state.graph_db
        a = _seed_entity(db, "user-a", "A")
        foreign = _seed_entity(db, "user-b", "B2")
        ghost = str(uuid.uuid4())
        before = _counts(db)  # 播种后取基线（测试作者坑：首版取在播种前）

        r1 = graph_client.get(
            "/api/graph/paths", params={"source_id": ghost, "target_id": a, "user_id": "user-a"}
        )
        r2 = graph_client.get(
            "/api/graph/paths", params={"source_id": a, "target_id": foreign, "user_id": "user-a"}
        )
        for r in (r1, r2):
            assert r.status_code == 404
            assert r.json()["detail"] == "Entity not found"
        assert _counts(db) == before

    def test_cross_user_intermediate_not_traversed(self, graph_client):
        """A(u1)->X(u2)->C(u1)：X 不属查询者，路径不穿越（接线前定形用户边界）。"""
        db = graph_client.app.state.graph_db
        a = _seed_entity(db, "user-a", "A")
        x = _seed_entity(db, "user-b", "X")
        c = _seed_entity(db, "user-a", "C")
        _seed_relation(db, a, x, rtype="knows")
        _seed_relation(db, x, c, rtype="knows")

        resp = graph_client.get(
            "/api/graph/paths", params={"source_id": a, "target_id": c, "user_id": "user-a"}
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_cycle_bounded_and_self_path_empty(self, graph_client):
        db = graph_client.app.state.graph_db
        a = _seed_entity(db, "user-a", "A")
        b = _seed_entity(db, "user-a", "B")
        c = _seed_entity(db, "user-a", "C")
        d = _seed_entity(db, "user-a", "D")
        _seed_relation(db, a, b, rtype="knows")
        _seed_relation(db, b, c, rtype="knows")
        _seed_relation(db, c, a, rtype="knows")  # 环
        _seed_relation(db, c, d, rtype="knows")

        resp = graph_client.get(
            "/api/graph/paths", params={"source_id": a, "target_id": d, "user_id": "user-a"}
        )
        data = resp.json()
        # 关系按无向遍历（既有实现语义如实）：A-C 有边（c→a 反向可达），
        # 故恰 2 条 [A,B,C,D] 与 [A,C,D]；环不产生重复/自环路径。
        assert data["count"] == 2
        seqs = {tuple(s["entity_id"] for s in p) for p in data["paths"]}
        assert seqs == {(a, b, c, d), (a, c, d)}

        self_resp = graph_client.get(
            "/api/graph/paths", params={"source_id": a, "target_id": a, "user_id": "user-a"}
        )
        assert self_resp.json()["count"] == 0  # 自环禁硬造

    def test_endpoint_delegates_to_service(self, graph_client, monkeypatch):
        """端点真委托 find_path（spy 硬证禁重实现第二套查询）。"""
        import src.api.graph as graph_api

        db = graph_client.app.state.graph_db
        a = _seed_entity(db, "user-a", "A")
        b = _seed_entity(db, "user-a", "B")
        calls = []

        async def fake_find_path(user_id, source_id, target_id, max_depth=5):
            calls.append((str(user_id), str(source_id), str(target_id), max_depth))
            return [
                [{"entity_id": str(source_id), "name": "A", "type": "person", "via_relation": None}]
            ]

        monkeypatch.setattr(graph_api, "find_path", fake_find_path)
        resp = graph_client.get(
            "/api/graph/paths",
            params={"source_id": a, "target_id": b, "user_id": "user-a", "max_depth": 3},
        )
        assert resp.status_code == 200
        assert calls == [(str(_uid("user-a")), a, b, 3)]
        assert resp.json()["count"] == 1


class TestFaceRootFixes:
    """接线前提收口：列漂移/序列化/ID 比较三根因（probe 修前三炸）。"""

    def test_create_relation_roundtrip_contract(self, graph_client):
        db = graph_client.app.state.graph_db
        a = _seed_entity(db, "user-a", "A")
        b = _seed_entity(db, "user-a", "B")
        resp = graph_client.post(
            "/api/graph/relations",
            json={
                "source_entity_id": a,
                "target_entity_id": b,
                "relation_type": "knows",
                "properties": {"w": 1},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["relation_type"] == "knows"
        assert data["properties"] == {"w": 1}  # 写 json.dumps/读 json.loads 往返

        listed = graph_client.get("/api/graph/relations", params={"user_id": "user-a"})
        assert listed.status_code == 200
        rows = listed.json()
        assert len(rows) == 1
        assert rows[0]["relation_type"] == "knows"  # API 契约键不随 DB 列名漂移

    def test_get_full_graph_edges_and_properties(self, graph_client):
        db = graph_client.app.state.graph_db
        a = _seed_entity(db, "user-a", "A", properties={"k": "v"})
        b = _seed_entity(db, "user-a", "B")
        _seed_relation(db, a, b, rtype="knows", properties={"w": 2})

        resp = graph_client.get("/api/graph/full", params={"user_id": "user-a"})
        assert resp.status_code == 200
        data = resp.json()
        props = {n["label"]: n["properties"] for n in data["nodes"]}
        assert props["A"] == {"k": "v"}  # TEXT JSON→dict（C3-⑪ 同款回归）
        assert len(data["edges"]) == 1
        assert data["edges"][0]["relation_type"] == "knows"
        assert data["edges"][0]["properties"] == {"w": 2}

    def test_get_graph_bfs_expands_outgoing_edges(self, graph_client):
        """UUID/TEXT 比较根因回归：出边邻居必须被展开（修前误判成自身不展开）。"""
        db = graph_client.app.state.graph_db
        a = _seed_entity(db, "user-a", "A")
        b = _seed_entity(db, "user-a", "B")
        _seed_relation(db, a, b, rtype="knows")

        resp = graph_client.get(
            "/api/graph/", params={"user_id": "user-a", "entity_id": a, "depth": 1}
        )
        assert resp.status_code == 200
        labels = {n["label"] for n in resp.json()["nodes"]}
        assert labels == {"A", "B"}


@pytest.mark.asyncio
async def test_build_graph_from_text_body(tmp_path, monkeypatch):
    """build_graph_from_text 方法体收口锁死（列映射+json 铁律+去重）——功能接线
    进知识入库链=⑦b 另拍，本拍只锁本体可跑。"""
    import src.services.graph as graph_mod
    from src.database.postgres import SQLitePool

    pool = SQLitePool(db_path=str(tmp_path / "g.db"))
    await pool.connect()
    try:
        for ddl in _GRAPH_DDL.split(";"):
            if ddl.strip():
                await pool.execute(ddl)
        monkeypatch.setattr(graph_mod, "db_pool", pool)

        async def fake_extract(_text):
            return {
                "entities": [
                    {"name": "Alice", "type": "person"},
                    {"name": "Bob", "type": "person"},
                ],
                "relations": [{"source": "Alice", "target": "Bob", "relation_type": "knows"}],
            }

        monkeypatch.setattr(graph_mod, "extract_entities_and_relations", fake_extract)
        uid = uuid.uuid4()

        g = await graph_mod.build_graph_from_text("Alice knows Bob", uid)
        assert {n.label for n in g.nodes} == {"Alice", "Bob"}
        assert len(g.edges) == 1
        assert g.edges[0].relation_type == "knows"
        assert await pool.fetchval("SELECT COUNT(*) FROM relations") == 1
        assert await pool.fetchval("SELECT type FROM relations") == "knows"

        # 去重：同文再跑不重复建行
        await graph_mod.build_graph_from_text("Alice knows Bob", uid)
        assert await pool.fetchval("SELECT COUNT(*) FROM entities") == 2
        assert await pool.fetchval("SELECT COUNT(*) FROM relations") == 1
    finally:
        # 失败路径也必须收池（aiosqlite 未关连接会挂住解释器退出——实测坑）。
        await pool.disconnect()
