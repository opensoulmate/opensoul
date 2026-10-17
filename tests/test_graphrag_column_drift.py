"""H1-②b-⑦c graphrag 同族列漂移+user_id 非列 INSERT 收口测试。

⑦b 留档「graphrag.py 同族列漂移+user_id 非列 INSERT（另一张脸，修前修后均必炸）
未动留档另拍」执行件。修前（控制组实证）src/cortex/graphrag.py 三处必炸：
①build 关系去重 SELECT 按 API 契约键 relation_type 查列（relations 表真源列=type）
②INSERT INTO relations 列清单 relation_type+user_id 两个非列（真源五列无 user_id）
③query_graph 出/入边 SELECT r.relation_type 同族读侧漂移。
收口约定=⑦a services/graph.py 同款：DB 真源列=type、API/dict 契约键 relation_type
在读侧映射（SELECT r.type AS relation_type，输出键 "type" 不变）、INSERT 五列
（⑦a create_relation 同款，用户边界=两端实体归属非行键）、DB 零迁移。
接线前定形（⑦a get_entity_neighbors「跨用户邻居不暴露」同款）：query_graph 关系
JOIN 按查询者过滤——列漂移修复后该查询首次真正执行，返回面先定形；推进按
te.id/se.id（修前 name 二次查回）。

隔离纪律（TL-②/H1-②b-① 先例）：tmp opensoul.db 真 schema（与 src/main.py lifespan
DDL 同形，relations 真源列=type 无 user_id）+ SQLitePool(db_path=tmp)，
绝不写生产 data/opensoul.db。
"""

import ast
import uuid

import pytest
import pytest_asyncio

# 与 src/main.py lifespan 建表 DDL 同形（禁手抄漂移：若 main.py 改形状须同步，
# 形状锁死例会随真源炸）。knowledge 为 build_graph_from_knowledge 扫描面。
_GRAPH_DDL = """CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    name TEXT NOT NULL, type TEXT DEFAULT '',
    description TEXT DEFAULT '', properties TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
    target_id TEXT NOT NULL, type TEXT NOT NULL,
    properties TEXT DEFAULT '{}', created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS knowledge (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    title TEXT DEFAULT '', content TEXT DEFAULT '');"""

_REAL_COLUMNS = ["id", "source_id", "target_id", "type", "properties", "created_at"]

# 端到端真抽取锚定文本（probe 实证确定性：entities={Acme Corp, Kubernetes}，
# relations=[(Acme Corp, Kubernetes, uses)]）。
_E2E_TEXT = "Acme Corp uses Kubernetes."

# 抽取桩（build SQL 面测试不押注 pattern 抽取；端到端真抽取例另测）。
# 注意契约键：extract_relations 产出 "type" 键（非 relation_type）。
_STUB_ENTITIES = [
    {"name": "AlphaCorp", "type": "organization", "properties": {}},
    {"name": "BetaServer", "type": "product", "properties": {}},
]
_STUB_RELATIONS = [
    {"source": "AlphaCorp", "target": "BetaServer", "type": "uses", "properties": {}},
]


@pytest_asyncio.fixture()
async def pool(tmp_path):
    from src.database.postgres import SQLitePool

    p = SQLitePool(db_path=str(tmp_path / "opensoul.db"))
    await p.connect()
    try:
        for ddl in _GRAPH_DDL.split(";"):
            if ddl.strip():
                await p.execute(ddl)
        yield p
    finally:
        # 失败路径也必须收池（aiosqlite 未关连接会挂住解释器退出——实测坑）。
        await p.disconnect()


def _stub_extract(monkeypatch, relations=None):
    rels = relations if relations is not None else _STUB_RELATIONS
    from src.cortex.graphrag import GraphRAGEngine

    monkeypatch.setattr(
        GraphRAGEngine, "extract_entities", lambda self, text: [dict(e) for e in _STUB_ENTITIES]
    )
    monkeypatch.setattr(
        GraphRAGEngine, "extract_relations", lambda self, text, ents: [dict(r) for r in rels]
    )


async def _insert_knowledge(pool, user_id: str, content: str = "AlphaCorp uses BetaServer") -> str:
    kid = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO knowledge (id, user_id, title, content) VALUES ($1, $2, $3, $4)",
        kid,
        user_id,
        "doc",
        content,
    )
    return kid


async def _columns(pool, table: str) -> list[str]:
    rows = await pool.fetch(f"PRAGMA table_info({table})")
    return [r["name"] for r in rows]


async def _seed_pair(pool, ua: str = "user-a", ub: str = "user-a", rtype: str = "knows"):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO entities (id, user_id, name, type, description, properties) "
        "VALUES ($1, $2, 'A', 'person', '', '{}')",
        a,
        ua,
    )
    await pool.execute(
        "INSERT INTO entities (id, user_id, name, type, description, properties) "
        "VALUES ($1, $2, 'B', 'person', '', '{}')",
        b,
        ub,
    )
    await pool.execute(
        "INSERT INTO relations (id, source_id, target_id, type, properties) "
        "VALUES ($1, $2, $3, $4, '{}')",
        str(uuid.uuid4()),
        a,
        b,
        rtype,
    )
    return a, b


class TestBuildSQLFace:
    """build_graph_from_knowledge 关系写侧（修前两处必炸）。"""

    pytestmark = pytest.mark.asyncio

    async def test_relations_insert_lands_on_real_columns(self, pool, monkeypatch):
        """关系行落真源五列：修前 relation_type+user_id 两个非列 INSERT 必炸
        （sqlite3.OperationalError no column named）——控制组硬反证。"""
        from src.cortex.graphrag import GraphRAGEngine

        _stub_extract(monkeypatch)
        await _insert_knowledge(pool, "user-a")
        stats = await GraphRAGEngine().build_graph_from_knowledge(pool, "user-a")

        assert stats["entities_new"] == 2
        assert stats["relations_new"] == 1
        rel = (await pool.fetch("SELECT * FROM relations"))[0]
        assert list(rel.keys()) == _REAL_COLUMNS  # 恰真源列集（无 user_id）
        assert rel["type"] == "uses"
        ent_ids = {r["id"] for r in await pool.fetch("SELECT id FROM entities")}
        assert rel["source_id"] in ent_ids and rel["target_id"] in ent_ids
        # 用户边界=实体行归属（relations 行无 user_id 列，⑦a create_relation 同约定）
        owners = {r["user_id"] for r in await pool.fetch("SELECT user_id FROM entities")}
        assert owners == {"user-a"}

    async def test_relations_table_zero_migration(self, pool, monkeypatch):
        """INSERT 不 ALTER 表（user_id 列不加不改）——DB 零迁移锁死。"""
        from src.cortex.graphrag import GraphRAGEngine

        _stub_extract(monkeypatch)
        before = await _columns(pool, "relations")
        await _insert_knowledge(pool, "user-a")
        await GraphRAGEngine().build_graph_from_knowledge(pool, "user-a")
        assert await _columns(pool, "relations") == before == _REAL_COLUMNS

    async def test_relations_dedup_by_type_column(self, pool, monkeypatch):
        """去重键 (source_id,target_id,type) 真走 type 列：同文二跑零虚增；
        同 (s,t) 异 type 不合并（修前去重 SELECT 按 relation_type 查列必炸）。"""
        from src.cortex.graphrag import GraphRAGEngine

        _stub_extract(monkeypatch)
        await _insert_knowledge(pool, "user-a")
        eng = GraphRAGEngine()
        await eng.build_graph_from_knowledge(pool, "user-a")
        await eng.build_graph_from_knowledge(pool, "user-a")
        assert await pool.fetchval("SELECT COUNT(*) FROM relations") == 1
        assert await pool.fetchval("SELECT COUNT(*) FROM entities") == 2  # 按名 upsert

        _stub_extract(
            monkeypatch,
            relations=[
                {
                    "source": "AlphaCorp",
                    "target": "BetaServer",
                    "type": "deals_with",
                    "properties": {},
                }
            ],
        )
        await eng.build_graph_from_knowledge(pool, "user-a")
        assert await pool.fetchval("SELECT COUNT(*) FROM relations") == 2

    async def test_build_real_pattern_extraction_end_to_end(self, pool):
        """真 pattern 抽取端到端（不打桩）：英文句抽取→建图→关系行落 type 列。"""
        from src.cortex.graphrag import GraphRAGEngine

        await _insert_knowledge(pool, "user-a", _E2E_TEXT)
        eng = GraphRAGEngine()
        ents = eng.extract_entities(_E2E_TEXT)
        rels = eng.extract_relations(_E2E_TEXT, ents)
        assert [(r["source"], r["target"], r["type"]) for r in rels] == [
            ("Acme Corp", "Kubernetes", "uses")
        ], "真抽取锚定（probe 实证）"

        stats = await eng.build_graph_from_knowledge(pool, "user-a")
        assert stats["relations_new"] == 1
        assert stats["knowledge_scanned"] == 1
        row = (await pool.fetch("SELECT * FROM relations"))[0]
        assert row["type"] == "uses"
        assert list(row.keys()) == _REAL_COLUMNS


class TestQueryGraphFace:
    """query_graph 关系读侧（修前 r.relation_type 必炸）。"""

    pytestmark = pytest.mark.asyncio

    async def test_query_out_in_relations_read_type_column(self, pool):
        """出/入边读真源 type 列（SQL 读侧映射 relation_type），输出契约键 "type" 不变。"""
        from src.cortex.graphrag import GraphRAGEngine

        await _seed_pair(pool)
        eng = GraphRAGEngine()

        out = await eng.query_graph(pool, "user-a", "A", depth=1)
        assert len(out["relations"]) == 1
        rel = out["relations"][0]
        assert rel["type"] == "knows" and rel["direction"] == "out"
        assert set(rel.keys()) == {"id", "source", "target", "type", "direction"}
        assert {e["name"] for e in out["entities"]} == {"A", "B"}

        inn = await eng.query_graph(pool, "user-a", "B", depth=1)
        assert inn["relations"][0]["direction"] == "in"
        assert inn["relations"][0]["source"] == "A" and inn["relations"][0]["target"] == "B"

    async def test_query_contract_shape(self, pool):
        """响应形状锁死：顶层 6 键、实体 5 键（既有契约零变化）。"""
        from src.cortex.graphrag import GraphRAGEngine

        await _seed_pair(pool)
        out = await GraphRAGEngine().query_graph(pool, "user-a", "A", depth=2)
        assert set(out.keys()) == {
            "center",
            "entities",
            "relations",
            "depth",
            "total_entities",
            "total_relations",
        }
        assert set(out["entities"][0].keys()) == {"id", "name", "type", "description", "properties"}
        assert out["total_entities"] == len(out["entities"])
        assert out["total_relations"] == len(out["relations"])

    async def test_query_cross_user_relation_hidden(self, pool):
        """接线前定形（⑦a「跨用户邻居不暴露」同款）：对端属他人实体的关系行
        不进返回面也不推进——列漂移修复后此查询首次真执行，边界先定形。"""
        from src.cortex.graphrag import GraphRAGEngine

        await _seed_pair(pool, ua="user-a", ub="user-b")
        out = await GraphRAGEngine().query_graph(pool, "user-a", "A", depth=3)
        assert out["relations"] == []
        assert [e["name"] for e in out["entities"]] == ["A"]

    async def test_query_unknown_entity_error(self, pool):
        """不变面：未知实体如实 {"error": ...}（新旧一致，API 层转 404）。"""
        from src.cortex.graphrag import GraphRAGEngine

        out = await GraphRAGEngine().query_graph(pool, "user-a", "Nope", depth=1)
        assert out == {"error": "Entity 'Nope' not found"}


class TestExtractionUnchangedFace:
    """不变面（新旧皆过非自嗨）：pattern 抽取本体零改动。"""

    def test_extract_entities_and_relations_basic(self):
        from src.cortex.graphrag import GraphRAGEngine

        eng = GraphRAGEngine()
        ents = eng.extract_entities(_E2E_TEXT)
        names = {e["name"] for e in ents}
        assert {"Acme Corp", "Kubernetes"} <= names
        rels = eng.extract_relations(_E2E_TEXT, ents)
        assert [(r["source"], r["target"], r["type"]) for r in rels] == [
            ("Acme Corp", "Kubernetes", "uses")
        ], "抽取锚定（probe 实证）"


class TestStaticContract:
    """列漂移禁复发静态锁死（H1-① 单一路径静态锁死同款纪律）。"""

    _SQL_HEADS = ("SELECT", "INSERT", "UPDATE", "DELETE")

    def _sql_literals(self):
        src = open("src/cortex/graphrag.py", encoding="utf-8").read()
        tree = ast.parse(src)
        out = []

        def walk(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.strip().startswith(self._SQL_HEADS):
                    out.append(node.value)
            for child in ast.iter_child_nodes(node):
                walk(child)

        walk(tree)
        return out

    def test_no_relation_type_column_reference_in_sql(self):
        for sql in self._sql_literals():
            cleaned = sql.replace("AS relation_type", "")
            assert "relation_type" not in cleaned, f"列漂移复发: {sql}"

    def test_relations_insert_exact_real_columns(self):
        inserts = [s for s in self._sql_literals() if s.startswith("INSERT INTO relations")]
        assert inserts, "INSERT INTO relations 语句面须在场"
        for sql in inserts:
            cols = sql.split("(", 1)[1].split(")", 1)[0]
            # 五列恰真源列集前五（created_at 走 DEFAULT 不进列清单）
            assert [c.strip() for c in cols.split(",")] == _REAL_COLUMNS[:-1]
            assert "user_id" not in sql

    def test_query_face_reads_type_via_alias(self):
        selects = [s for s in self._sql_literals() if "FROM relations" in s and "JOIN" in s]
        assert len(selects) == 2  # 出边+入边恰好两面
        for sql in selects:
            assert "r.type AS relation_type" in sql
            assert "user_id = $2" in sql  # 用户边界 JOIN 过滤在场
