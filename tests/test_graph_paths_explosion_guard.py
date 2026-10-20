"""H1-②b-⑦a 留档④「paths 路径爆炸防护（dense 图）」收口测试。

修前（控制组实证非推断）：find_path 无界枚举 source→target 全部简单路径——
dense 图指数爆炸（K8 无路场景 1.7 万级数据面查询无上限），返回魔数 paths[:10]
（输出有上限但工作量无上限），且无「搜索是否完成」语义（空 paths 无法区分
「确证无路」与「没搜完」）。

收口契约：find_path 返回 (paths, truncated)——
①输出满 PATH_RESULT_CAP 即止（精确语义：BFS 发现序前 N 条与修前全量枚举后
取前 N 逐条一致，truncated=False=输出完整非截断）；
②查询数超 PATH_EXPLORE_BUDGET 即止（truncated=True 如实报告搜索未完成，
已发现路径保留不丢弃；空 paths 须结合 truncated 读）。

隔离纪律（test_graph_query_faces 同款）：tmp opensoul.db 真 schema（relations
真源列=type）+ db_pool 多绑定换 tmp SQLitePool，绝不写生产 data/opensoul.db。
"""

import sqlite3
import typing
import uuid

import pytest

# 与 test_graph_query_faces._GRAPH_DDL 同形（main.py lifespan DDL，禁手抄漂移）。
_GRAPH_DDL = """CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    name TEXT NOT NULL, type TEXT DEFAULT '',
    description TEXT DEFAULT '', properties TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
    target_id TEXT NOT NULL, type TEXT NOT NULL,
    properties TEXT DEFAULT '{}', created_at TEXT DEFAULT (datetime('now')));"""


class CountingPool:
    """查询计数代理（真 tmp SQLitePool 委托）——find_path 数据面查询数=工作量
    单一可观测量（探索预算语义=查询数上限），行为断言不经时序间接。"""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name in ("fetch", "fetchrow", "execute", "executemany"):

            async def _wrapped(*args, **kwargs):
                self.calls += 1
                return await attr(*args, **kwargs)

            return _wrapped
        return attr


@pytest.fixture()
def guard_env(tmp_path, monkeypatch):
    """graph 隔离测试面：真实 router + tmp opensoul.db，四 db_pool 绑定全换 tmp。"""
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
    with TestClient(app) as c:
        yield {"client": c, "db": str(tmp_os_db), "pool": tmp_pool, "graph_mod": graph_mod}


def _uid(user_id: str = "user-a"):
    from src.api.graph import _resolve_user_id

    return _resolve_user_id(user_id)


def _seed_entity(db_path, user_id: str, name: str) -> str:
    eid = str(uuid.uuid4())
    with sqlite3.connect(db_path) as _c:
        _c.execute(
            "INSERT INTO entities (id, user_id, name, type, description, properties) "
            "VALUES (?, ?, ?, 'probe', '', '{}')",
            (eid, str(_uid(user_id)), name),
        )
        _c.commit()
    return eid


def _seed_relation(db_path, source: str, target: str, rtype: str = "knows") -> str:
    rid = str(uuid.uuid4())
    with sqlite3.connect(db_path) as _c:
        _c.execute(
            "INSERT INTO relations (id, source_id, target_id, type, properties) "
            "VALUES (?, ?, ?, ?, '{}')",
            (rid, source, target, rtype),
        )
        _c.commit()
    return rid


def _paths(client, source, target, user_id="user-a", max_depth=5):
    resp = client.get(
        "/api/graph/paths",
        params={
            "source_id": source,
            "target_id": target,
            "user_id": user_id,
            "max_depth": max_depth,
        },
    )
    return resp


class TestPathsExplosionGuard:
    """dense 图路径爆炸防护行为面。"""

    def test_output_cap_early_stop_complete_not_truncated(self, guard_env):
        """输出满 PATH_RESULT_CAP 即止：11 条平行 2 跳路→恰 10 条，truncated=False
        （输出契约「最多 N 条」完整非截断——非搜索未完成）。"""
        db, client = guard_env["db"], guard_env["client"]
        s = _seed_entity(db, "user-a", "S")
        t = _seed_entity(db, "user-a", "T")
        mids = []
        for i in range(11):
            m = _seed_entity(db, "user-a", f"M{i}")
            mids.append(m)
            _seed_relation(db, s, m)
            _seed_relation(db, m, t)

        resp = _paths(client, s, t, max_depth=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == guard_env["graph_mod"].PATH_RESULT_CAP == 10
        assert data["truncated"] is False  # 输出满即止=完整语义非截断
        true_seqs = {(s, m, t) for m in mids}
        seqs = {tuple(step["entity_id"] for step in p) for p in data["paths"]}
        assert len(seqs) == 10  # 10 条互异
        assert seqs <= true_seqs  # 全部是真路径，禁 fabricate

    def test_dense_unreachable_budget_bounds_queries_truncated(self, guard_env):
        """dense 图（K8 团+孤立终点）无路场景：查询数被预算钉死（修前 1.7 万级
        无上限指数爆炸）+ truncated=True 如实（空 paths=预算内未发现禁当无路）。"""
        graph_mod = guard_env["graph_mod"]
        db, client = guard_env["db"], guard_env["client"]
        clique = [_seed_entity(db, "user-a", f"K{i}") for i in range(8)]
        for i in range(8):
            for j in range(i + 1, 8):
                _seed_relation(db, clique[i], clique[j])
        target = _seed_entity(db, "user-a", "ISOLATED")  # 有实体无边=真无路但须搜完才知

        counting = CountingPool(guard_env["pool"])
        # find_path 读 graph 模块全局 db_pool——计数代理只包 graph 面（预算语义=
        # find_path 自身查询数；端点 404 预查走 entity 面不入账）。
        prev = graph_mod.db_pool
        graph_mod.db_pool = counting
        try:
            resp = _paths(client, clique[0], target, max_depth=7)
        finally:
            graph_mod.db_pool = prev

        # 行为断言先行（工作量硬证，断言细节回读真查询计数）：修前旧码无界枚举
        # =万级查询；5000 上界与 test_budget_and_cap_constants_locked 的常量上界
        # 同界组合（常量 ≤5000 且 calls ≤5000=工作量恒常数级），精确单源界随后。
        assert counting.calls <= 5000, f"find_path queries={counting.calls}（修前无界爆炸硬证）"
        assert counting.calls <= graph_mod.PATH_EXPLORE_BUDGET
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["paths"] == []
        assert data["truncated"] is True  # 预算耗尽如实报告搜索未完成

    def test_budget_truncation_preserves_found_paths(self, guard_env):
        """预算截断不丢弃已发现路径（如实部分结果）：直连路已找到、后续爆炸枝
        触预算→返回 [S,T] + truncated=True。"""
        graph_mod = guard_env["graph_mod"]
        db, client = guard_env["db"], guard_env["client"]
        s = _seed_entity(db, "user-a", "S")
        t = _seed_entity(db, "user-a", "T")
        _seed_relation(db, s, t)  # 直连路（先落=先发现）
        prev_n = s
        for i in range(6):  # 爆炸枝：链式不断生成新态吃预算
            n = _seed_entity(db, "user-a", f"D{i}")
            _seed_relation(db, prev_n, n)
            prev_n = n

        prev = graph_mod.PATH_EXPLORE_BUDGET
        graph_mod.PATH_EXPLORE_BUDGET = 6
        try:
            resp = _paths(client, s, t, max_depth=5)
        finally:
            graph_mod.PATH_EXPLORE_BUDGET = prev

        assert resp.status_code == 200
        data = resp.json()
        assert data["truncated"] is True  # 预算耗尽如实
        assert data["count"] == 1  # 已发现路径保留不丢弃
        seqs = [tuple(step["entity_id"] for step in p) for p in data["paths"]]
        assert seqs == [(s, t)]

    def test_no_route_proven_absent_untruncated(self, guard_env):
        """小图确证无路：truncated=False 语义锁（空 paths + 搜索已完成=真无路，
        与预算截断的空结果区分）。"""
        db, client = guard_env["db"], guard_env["client"]
        s = _seed_entity(db, "user-a", "S")
        t = _seed_entity(db, "user-a", "T")
        x = _seed_entity(db, "user-a", "X")
        y = _seed_entity(db, "user-a", "Y")
        _seed_relation(db, s, x)
        _seed_relation(db, x, y)  # S-X-Y 链与 T 不连通

        resp = _paths(client, s, t, max_depth=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["truncated"] is False  # 确证无路（非没搜完）

    def test_two_path_response_truncated_is_bool(self, guard_env):
        """恰 2 条路响应：truncated 恒 bool——find_path 回裸 list 形时端点解包
        会把第 2 条路误当 truncated（list 非 bool）先炸=形状回归锁。"""
        db, client = guard_env["db"], guard_env["client"]
        s = _seed_entity(db, "user-a", "S")
        t = _seed_entity(db, "user-a", "T")
        m = _seed_entity(db, "user-a", "M")
        _seed_relation(db, s, t)  # 直连
        _seed_relation(db, s, m)
        _seed_relation(db, m, t)  # 2 跳（恰 2 条路）

        resp = _paths(client, s, t, max_depth=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert isinstance(data["truncated"], bool)
        assert data["truncated"] is False
        for p in data["paths"]:
            assert isinstance(p, list)
            assert all(
                set(step.keys()) == {"entity_id", "name", "type", "via_relation"} for step in p
            )


class TestGuardContract:
    """护栏契约面（结构锁）。"""

    def test_find_path_return_shape_is_tuple(self):
        """find_path 返回注解=二元组（(paths, truncated) 契约结构锁）。"""
        import src.services.graph as graph_mod

        ret = typing.get_type_hints(graph_mod.find_path)["return"]
        assert typing.get_origin(ret) is tuple
        assert typing.get_args(ret)[1] is bool

    def test_budget_and_cap_constants_locked(self):
        """PATH_RESULT_CAP=「最多 10 条」返回契约单一源（修前魔数 10）；
        PATH_EXPLORE_BUDGET 在场且有界（1..5000=最坏查询量恒常数级）。"""
        import src.services.graph as graph_mod

        assert graph_mod.PATH_RESULT_CAP == 10
        assert 1 <= graph_mod.PATH_EXPLORE_BUDGET <= 5000
        # 预算语义=查询数上限单一源（find_path 内恰一个计数变量消费）。
        src_text = open(graph_mod.__file__, encoding="utf-8").read()
        assert src_text.count("PATH_EXPLORE_BUDGET") >= 4  # 定义+注释+2 消费点
        assert src_text.count("queries > PATH_EXPLORE_BUDGET") == 2  # 2 个预算闸门
