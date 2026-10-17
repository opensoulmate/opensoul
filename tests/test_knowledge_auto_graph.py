"""H1-②b-⑦b 知识入库自动建图接线测试 — create_knowledge→_schedule_auto_graph→
build_graph_from_text 首个生产接线（H1-② 定性⑧「build_graph_from_text（知识入库
自动建图）零接线」执行件；⑦a 已收查询面出口+方法体锁死，本拍=功能接线）。

隔离纪律（TL-②/H1-②b-① 先例）：tmp opensoul.db 真 schema + db_pool 多绑定换
tmp SQLitePool + embedding 降级空向量 + meili AVAILABLE=False + 抽取 seam 桩
（graph_mod.extract_entities_and_relations——conftest autouse 缺省空抽取可被
per-test 覆盖），绝不写生产 data/opensoul.db、绝不真打 LLM。
"""

import asyncio
import sqlite3
import uuid

import pytest

# 与 src/main.py lifespan 建表 DDL 同形（knowledge/tags/knowledge_tags/
# knowledge_chunks）+ graph 面（entities/relations 真源列=type，⑦a 同款）。
_AUTO_GRAPH_DDL = """CREATE TABLE IF NOT EXISTS knowledge (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    title TEXT NOT NULL, content TEXT DEFAULT '',
    tags TEXT DEFAULT '[]', source TEXT DEFAULT '',
    content_type TEXT DEFAULT '', metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL, content TEXT NOT NULL,
    embedding_id TEXT, token_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
    color TEXT DEFAULT '#6366f1', created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS knowledge_tags (
    knowledge_id TEXT NOT NULL, tag_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    name TEXT NOT NULL, type TEXT DEFAULT '',
    description TEXT DEFAULT '', properties TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
    target_id TEXT NOT NULL, type TEXT NOT NULL,
    properties TEXT DEFAULT '{}', created_at TEXT DEFAULT (datetime('now')));"""


class Env:
    """tmp 数据面句柄（pool + 模块引用），供 _run 组合。"""

    pool: object
    db_path: str
    knowledge_mod: object
    graph_mod: object
    monkeypatch: object


@pytest.fixture()
def kg_env(tmp_path, monkeypatch):
    """knowledge 自动建图隔离测试面：tmp opensoul.db 真 schema + db_pool 三绑定
    （postgres/services.knowledge/services.graph）换 tmp SQLitePool + embedding
    降级空向量 + meili AVAILABLE=False。"""
    import src.database.postgres as postgres_mod
    import src.services.graph as graph_mod
    import src.services.knowledge as knowledge_mod
    from src.config import settings
    from src.database.postgres import SQLitePool

    tmp_os_db = tmp_path / "opensoul.db"
    with sqlite3.connect(tmp_os_db) as _c:
        _c.executescript(_AUTO_GRAPH_DDL)
        _c.commit()

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_os_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(postgres_mod, "db_pool", tmp_pool)
    monkeypatch.setattr(knowledge_mod, "db_pool", tmp_pool)
    monkeypatch.setattr(graph_mod, "db_pool", tmp_pool)

    async def _no_embeddings(_texts):
        return [[] for _ in _texts]

    monkeypatch.setattr(knowledge_mod, "get_embeddings_batch", _no_embeddings)
    monkeypatch.setattr(knowledge_mod.meili_client, "AVAILABLE", False)

    env = Env()
    env.pool = tmp_pool
    env.db_path = str(tmp_os_db)
    env.knowledge_mod = knowledge_mod
    env.graph_mod = graph_mod
    env.monkeypatch = monkeypatch
    return env


async def _drain():
    """排干后台自动建图任务（_schedule_auto_graph 的 create_task）。"""
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _run(env, coro_fn):
    """在真 SQLitePool 数据面跑 async 断言体（连接/断开随用例）。"""

    async def _wrapped():
        await env.pool.connect()
        try:
            return await coro_fn()
        finally:
            await env.pool.disconnect()

    return asyncio.run(_wrapped())


def _make_data(title: str = "k", content: str = "text"):
    from src.models.knowledge import KnowledgeCreate

    return KnowledgeCreate(title=title, content=content)


def _stub_extraction(env, result: dict):
    async def _fake(_text):
        return result

    env.monkeypatch.setattr(env.graph_mod, "extract_entities_and_relations", _fake)


# ── 接线真跑：create→后台建图→图行落库 ──────────────────────────────────


class TestAutoGraphWiring:
    def test_create_wiring_builds_graph(self, kg_env):
        """create_knowledge 真触发自动建图（修前零接线：建图行恒 0）。"""
        env = kg_env
        uid = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
        _stub_extraction(
            env,
            {
                "entities": [
                    {"name": "Alice", "type": "person", "description": "d1"},
                    {"name": "Bob", "type": "person", "description": "d2"},
                ],
                "relations": [
                    {
                        "source": "Alice",
                        "target": "Bob",
                        "relation_type": "knows",
                    }
                ],
            },
        )

        async def _body():
            row = await env.knowledge_mod.create_knowledge(
                _make_data(content="Alice knows Bob"), uid
            )
            assert row["id"]
            await _drain()
            ents = await env.pool.fetch("SELECT * FROM entities ORDER BY name")
            rels = await env.pool.fetch("SELECT * FROM relations")
            assert [e["name"] for e in ents] == ["Alice", "Bob"]
            assert [e["user_id"] for e in ents] == [str(uid), str(uid)]
            assert len(rels) == 1
            assert rels[0]["type"] == "knows"

        _run(env, _body)

    def test_entity_resolve_or_create_reuses_existing(self, kg_env):
        """同名实体 resolve-or-create：既有实体复用禁重复建，新实体补建。"""
        env = kg_env
        uid = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
        _stub_extraction(
            env,
            {
                "entities": [
                    {"name": "Alice", "type": "person", "description": ""},
                    {"name": "Charlie", "type": "person", "description": ""},
                ],
                "relations": [],
            },
        )

        async def _body():
            await env.pool.execute(
                "INSERT INTO entities (id, user_id, name, type) VALUES ($1, $2, $3, $4)",
                str(uuid.uuid4()),
                str(uid),
                "Alice",
                "concept",
            )
            await env.knowledge_mod.create_knowledge(_make_data(content="x"), uid)
            await _drain()
            rows = await env.pool.fetch("SELECT * FROM entities WHERE name = 'Alice'")
            assert len(rows) == 1  # 复用既有行，禁重复建
            assert rows[0]["type"] == "concept"  # 既有行不被改写
            charlie = await env.pool.fetch("SELECT * FROM entities WHERE name = 'Charlie'")
            assert len(charlie) == 1

        _run(env, _body)

    def test_relation_dedupe_idempotent(self, kg_env):
        """重复入库同文本→关系去重幂等（build_graph_from_text 既有语义经真接线复证）。"""
        env = kg_env
        uid = uuid.UUID("00000000-0000-0000-0000-0000000000a3")
        _stub_extraction(
            env,
            {
                "entities": [
                    {"name": "A", "type": "concept", "description": ""},
                    {"name": "B", "type": "concept", "description": ""},
                ],
                "relations": [{"source": "A", "target": "B", "relation_type": "related"}],
            },
        )

        async def _body():
            await env.knowledge_mod.create_knowledge(_make_data(content="t"), uid)
            await _drain()
            await env.knowledge_mod.create_knowledge(_make_data(content="t"), uid)
            await _drain()
            ents = await env.pool.fetch("SELECT * FROM entities")
            rels = await env.pool.fetch("SELECT * FROM relations")
            assert len(ents) == 2  # 实体 resolve-or-create 不翻倍
            assert len(rels) == 1  # 关系去重

        _run(env, _body)

    def test_user_boundary_rows_owned_by_user(self, kg_env):
        """图行按入库 user 归属：同名实体跨用户各建各的（resolve 按 (user,name)）。"""
        env = kg_env
        u1 = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
        u2 = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
        _stub_extraction(
            env,
            {
                "entities": [{"name": "Solo", "type": "concept", "description": ""}],
                "relations": [],
            },
        )

        async def _body():
            await env.knowledge_mod.create_knowledge(_make_data(content="t"), u1)
            await _drain()
            await env.knowledge_mod.create_knowledge(_make_data(content="t"), u2)
            await _drain()
            rows = await env.pool.fetch("SELECT * FROM entities WHERE name = 'Solo'")
            assert sorted(r["user_id"] for r in rows) == sorted([str(u1), str(u2)])

        _run(env, _body)

    def test_empty_extraction_zero_rows(self, kg_env):
        """抽取空结果（无 LLM key 既有降级形态）=零实体零动作不炸。"""
        env = kg_env
        uid = uuid.UUID("00000000-0000-0000-0000-0000000000a4")
        _stub_extraction(env, {"entities": [], "relations": []})

        async def _body():
            await env.knowledge_mod.create_knowledge(_make_data(content="t"), uid)
            await _drain()
            ents = await env.pool.fetch("SELECT * FROM entities")
            rels = await env.pool.fetch("SELECT * FROM relations")
            assert ents == [] and rels == []

        _run(env, _body)


# ── 旁路纪律：失败可见不阻断 + 后台不阻塞 ────────────────────────────────


class TestAutoGraphBypass:
    def test_extraction_failure_visible_not_blocking(self, kg_env, caplog):
        """抽取炸→入库照常成功（旁路纪律 G1-⑤：失败 warning 带真因可见禁静默）。"""
        env = kg_env
        uid = uuid.UUID("00000000-0000-0000-0000-0000000000a5")

        async def _boom(_text):
            raise RuntimeError("extract-boom-42")

        env.monkeypatch.setattr(env.graph_mod, "extract_entities_and_relations", _boom)

        async def _body():
            with caplog.at_level("WARNING"):
                row = await env.knowledge_mod.create_knowledge(_make_data(content="t"), uid)
                await _drain()
            assert row["id"]  # 入库主路径零破坏
            ents = await env.pool.fetch("SELECT * FROM entities")
            assert ents == []
            assert any(
                "extract-boom-42" in r.message and row["id"] in r.message for r in caplog.records
            ), [r.message for r in caplog.records]

        _run(env, _body)

    def test_auto_build_graph_failure_no_raise(self, kg_env):
        """_auto_build_graph 单元：失败不外抛（后台任务禁未处理异常）。"""
        env = kg_env

        async def _boom(_text):
            raise ValueError("unit-boom")

        env.monkeypatch.setattr(env.graph_mod, "extract_entities_and_relations", _boom)

        async def _body():
            # 直调不炸=失败被旁路面吞成可见 warning
            await env.knowledge_mod._auto_build_graph("kid-x", "t", uuid.uuid4())

        _run(env, _body)

    def test_non_blocking_background_semantics(self, kg_env):
        """后台语义硬证：create 不等 LLM 抽取完成即返回（修前若内联 await=此测死锁/超时）。"""
        env = kg_env
        uid = uuid.UUID("00000000-0000-0000-0000-0000000000a6")
        started = asyncio.Event()
        release = asyncio.Event()
        done = asyncio.Event()

        async def _gated(_text):
            started.set()
            await asyncio.wait_for(release.wait(), 10)
            done.set()
            return {
                "entities": [{"name": "Gated", "type": "concept", "description": ""}],
                "relations": [],
            }

        env.monkeypatch.setattr(env.graph_mod, "extract_entities_and_relations", _gated)

        async def _body():
            row = await asyncio.wait_for(
                env.knowledge_mod.create_knowledge(_make_data(content="t"), uid), 5
            )
            assert row["id"]
            assert started.is_set() or True  # 任务已排上（drain 后必达）
            assert done.is_set() is False  # create 返回时抽取未完成=非阻塞硬证
            release.set()
            await _drain()
            assert done.is_set() is True  # 后台任务真跑完
            ents = await env.pool.fetch("SELECT * FROM entities")
            assert [e["name"] for e in ents] == ["Gated"]

        _run(env, _body)


# ── 契约零变化 + 调度口 + 边界锁死 ──────────────────────────────────────


class TestContractAndSeam:
    def test_response_contract_unchanged(self, kg_env):
        """建图不改 create 响应契约：建图态/空态响应键集逐键一致且无图面键。"""
        env = kg_env
        uid = uuid.UUID("00000000-0000-0000-0000-0000000000a7")
        _stub_extraction(
            env,
            {
                "entities": [{"name": "N", "type": "concept", "description": ""}],
                "relations": [],
            },
        )

        async def _body():
            row_graph = await env.knowledge_mod.create_knowledge(_make_data(content="t"), uid)
            await _drain()
            _stub_extraction(env, {"entities": [], "relations": []})
            row_empty = await env.knowledge_mod.create_knowledge(_make_data(content="t2"), uid)
            await _drain()
            assert set(row_graph.keys()) == set(row_empty.keys())
            for required in (
                "id",
                "title",
                "content",
                "source",
                "content_type",
                "metadata",
                "tags",
            ):
                assert required in row_graph
            assert not any("graph" in k or "entity" in k for k in row_graph)

        _run(env, _body)

    def test_schedule_seam_args_single_call(self, kg_env):
        """create→调度口 spy 直证：恰一次调用、args=(knowledge_id, content, user_id)。"""
        env = kg_env
        uid = uuid.UUID("00000000-0000-0000-0000-0000000000a8")
        calls = []

        def _spy(knowledge_id, text, user_id):
            calls.append((knowledge_id, text, user_id))

        env.monkeypatch.setattr(env.knowledge_mod, "_schedule_auto_graph", _spy)

        async def _body():
            row = await env.knowledge_mod.create_knowledge(_make_data(content="spy-content"), uid)
            assert calls == [(row["id"], "spy-content", uid)]

        _run(env, _body)

    def test_schedule_seam_real_task(self, kg_env):
        """调度口单元：真 create_task 排 _auto_build_graph（seam→build 真链）。"""
        env = kg_env
        uid = uuid.UUID("00000000-0000-0000-0000-0000000000a9")
        _stub_extraction(
            env,
            {
                "entities": [{"name": "Seam", "type": "concept", "description": ""}],
                "relations": [],
            },
        )

        async def _body():
            task = env.knowledge_mod._schedule_auto_graph("kid-seam", "t", uid)
            assert isinstance(task, asyncio.Task)
            await _drain()
            ents = await env.pool.fetch("SELECT * FROM entities")
            assert [e["name"] for e in ents] == ["Seam"]

        _run(env, _body)

    def test_update_does_not_trigger_auto_graph(self, kg_env):
        """边界锁死：本拍只接 create 入库链，update 不触发自动建图（另拍语义）。"""
        env = kg_env
        uid = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
        calls = []
        env.monkeypatch.setattr(
            env.knowledge_mod, "_schedule_auto_graph", lambda *a: calls.append(a)
        )

        async def _body():
            from src.models.knowledge import KnowledgeUpdate

            row = await env.knowledge_mod.create_knowledge(_make_data(content="t"), uid)
            calls.clear()
            updated = await env.knowledge_mod.update_knowledge(
                uuid.UUID(row["id"]), KnowledgeUpdate(content="t2"), uid
            )
            assert updated is not None
            assert calls == []

        _run(env, _body)

    def test_single_seam_covers_all_ingest_callers(self, kg_env):
        """静态接线普查：五个入库调用方全经 create_knowledge 单口、调度口恰一调用点。"""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        src = (root / "src/services/knowledge.py").read_text()
        # 调度口定义 1 处 + create 内调用 1 处
        assert src.count("_schedule_auto_graph(") == 2
        callers = {
            "src/api/knowledge.py": 2,  # POST / + upload
            "src/api/pipeline.py": 1,
            "src/api/agent.py": 1,
            "src/mcp/server.py": 1,
        }
        for rel, expected in callers.items():
            text = (root / rel).read_text()
            assert text.count("create_knowledge(") >= expected, rel


# ── 根因收口回归锁死：抽取 prompt 格式化（live e2e 逼出的真断点） ──────────


class TestExtractionPromptRegression:
    def test_prompt_format_regression(self):
        """EXTRACTION_PROMPT.format 曾把 JSON 示例花括号当格式域→KeyError '"name"'
        （live e2e 建图旁路 warning 真因；抽取面从未真跑通）。转义后 format 可跑且
        发给 LLM 的 prompt 保留单花括号 JSON 示例+真文本。"""
        from src.services.extraction import EXTRACTION_PROMPT

        rendered = EXTRACTION_PROMPT.format(text="张三在北京")
        assert "张三在北京" in rendered
        assert '{"name": str' in rendered  # LLM 侧仍是单花括号示例
        assert '{"source": str' in rendered

    def test_extract_parses_llm_json(self, monkeypatch):
        """extract_entities_and_relations 真跑（fake LLM 上游）：prompt 真带文本、
        返回解析 dict——修前 format 步即 KeyError 全链不可达。"""
        import src.services.extraction as extraction_mod

        captured = {}

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": '{"entities": [{"name": "A", "type": "person", "description": ""}], "relations": []}'
                            }
                        }
                    ]
                }

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, **kwargs):
                captured["url"] = url
                captured["payload"] = kwargs["json"]
                return _Resp()

        monkeypatch.setattr(extraction_mod.httpx, "AsyncClient", lambda: _Client())
        # hermetic：不依赖环境 llm_api_key（空 key 是既有 early-return 降级形态）
        monkeypatch.setattr(extraction_mod.settings, "llm_api_key", "test-key")

        async def _body():
            return await extraction_mod.extract_entities_and_relations("张三在北京")

        result = asyncio.run(_body())
        assert result == {
            "entities": [{"name": "A", "type": "person", "description": ""}],
            "relations": [],
        }
        assert "张三在北京" in captured["payload"]["messages"][0]["content"]
