"""Shared test fixtures for OpenSoul integration tests."""

import copy
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

BASE_URL = "http://localhost:8090"


@pytest.fixture(autouse=True)
def _isolate_nest_db(tmp_path, monkeypatch):
    """NEST_DB 全测试隔离（F3b-③a 增补）：数据面咨询 IsolationEngine 的审计日志
    落 NestStore——测试绝不写生产 data/nest.db（JobQueue 等 gate 调用会留审计行）。
    显式指定 NEST_DB 的测试（test_nest_persistence/test_tenant_binding）按其
    monkeypatch.setenv 覆盖为准。"""
    monkeypatch.setenv("NEST_DB", str(tmp_path / "nest.db"))


@pytest.fixture(autouse=True)
def _isolate_learn_db(tmp_path, monkeypatch):
    """LEARN_DB 全测试隔离（F3b-③c-2②b 增补）：src.api.learn 模块级 CourseEngine
    单例 import 即构造（_ensure_schema+旧 JSON 迁移扫描 ~/.opensoul/learn）——测试
    绝不写生产 learn.db。显式指定 LEARN_DB/显式 db_path 的测试按其覆盖为准。"""
    monkeypatch.setenv("LEARN_DB", str(tmp_path / "learn.db"))


@pytest.fixture(autouse=True)
def _isolate_timeline_db(tmp_path, monkeypatch):
    """TIMELINE_DB 全测试隔离（timeline 测试隔离拆件①增补）：event_bridge 懒缓存
    （push_event._timeline/emit._timeline）与 api.timeline 模块单例在构造时按
    TIMELINE_DB 解析库路径——修前缺省恒 ~/opensoul/data/timeline.db，pytest 全量
    ~74 行/轮 brain think/verify 事件经 push_event 懒构造 TimelineStore() 写生产
    timeline.db（2026-10-13 全量实测 120 行/轮 中的 in-process 部分）。懒缓存每
    用例复位（delattr）：每用例真源=本用例 tmp 库，禁跨用例陈旧缓存。显式指定
    db_path/TIMELINE_DB 的测试（test_timeline_tenant_isolation 等）按其覆盖为准。"""
    import src.nerve.event_bridge as event_bridge_mod

    monkeypatch.setenv("TIMELINE_DB", str(tmp_path / "timeline.db"))
    monkeypatch.delattr(event_bridge_mod.push_event, "_timeline", raising=False)
    monkeypatch.delattr(event_bridge_mod.emit, "_timeline", raising=False)


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def client():
    """Shared HTTP client for all tests."""
    with httpx.Client(base_url=BASE_URL, timeout=30) as c:
        yield c


@pytest.fixture()
def gene_client(tmp_path, monkeypatch):
    """gene API隔离测试面：真实router + tmp独立库，绝不写生产skills.db（也不依赖live服务）。

    背景（2026-10-03测试隔离轮）：gene API测试此前走live :8090，每次全量跑把测试
    技能行写进 ~/.hermes/opensoul/gene/skills.db（生产库）——测试行挤占find_relevant
    候选曾引爆假失败（dev-reports 2026-10-03 14:55轮遗留#3）。本fixture把gene与
    gene_templates两个真实router挂进进程内app（挂载镜像src/main.py:554-555的
    /api/gene前缀），模块单例换成tmp实例——测试面=真实router代码，数据面=隔离。

    app.state.gene_skills_db 暴露tmp库路径供隔离护栏断言。

    src.api.*在fixture内延迟import：模块import有单例构造副作用（SkillLearner()
    打开生产skills.db），不能在conftest收集期全局import。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.gene as gene_api
    import src.api.gene_templates as gene_templates_api
    from src.gene.skill_learner import SkillLearner
    from src.gene.templates import TemplateEngine

    app = FastAPI()
    app.include_router(gene_api.router, prefix="/api/gene")
    app.include_router(gene_templates_api.router, prefix="/api/gene")

    learner = SkillLearner(db_path=str(tmp_path / "skills.db"))
    monkeypatch.setattr(gene_api, "skill_learner", learner)
    monkeypatch.setattr(gene_api, "engine", TemplateEngine(storage_dir=tmp_path / "templates"))
    # gene_templates持久化与内存表也换tmp副本（写入随fixture销毁丢弃，不落~/.openmate）
    monkeypatch.setattr(gene_templates_api, "_PERSIST_DIR", Path(tmp_path) / "gtpersist")
    monkeypatch.setattr(
        gene_templates_api, "_PERSIST_FILE", Path(tmp_path) / "gtpersist" / "gene_templates.json"
    )
    monkeypatch.setattr(
        gene_templates_api, "_templates", copy.deepcopy(gene_templates_api._templates)
    )
    monkeypatch.setattr(
        gene_templates_api, "_instances", copy.deepcopy(gene_templates_api._instances)
    )
    monkeypatch.setattr(
        gene_templates_api, "_capabilities", copy.deepcopy(gene_templates_api._capabilities)
    )

    app.state.gene_skills_db = learner.db_path
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def will_client(tmp_path, monkeypatch):
    """will/hippo jobs API隔离测试面：真实router + tmp独立数据面，绝不写生产
    job_queue.db / evolution.db / long_term_memory.db（gene_client同款路线；
    2026-10-06 live测试收编轮从 test_job_queue_wiring.py 迁出为共享fixture）。

    背景：TestLiveJobQueueAPI/TestLiveRequeueAPI的写用例走live :8090，作业行
    source默认'real'落生产 job_queue.db → experience_collector._collect_from_jobs
    把测试作业采集为假experiences（dev-reports多轮挂账"live测试写生产库"）。
    本fixture把 will 与 hippo 两个真实router挂进进程内app（挂载镜像
    src/main.py 的 /api/will、/api/hippo 前缀），四层数据面换tmp实例——
    测试面=真实router/handler代码，数据面=隔离：
    - job_queue：tmp jobs.db（monkeypatch模块单例_job_queue）
    - heredity.evolution_engine：tmp evolution.db（真实handler评估写入落tmp）
    - hippo._lt_store/_dream_distiller：tmp LTM库（dream蒸馏写入落tmp）
    - will.wakeup：tmp wakeup.db（monkeypatch模块单例_scheduler——live测试
      收编轮新增：schedule/purge/overview数据面绝不落生产wakeup.db）

    handler注册硬化：api/will.py模块import时的_register_job_handlers只在
    首次import执行一次——pytest同进程第二次构造本fixture时模块已缓存，新tmp
    队列默认无handler（/jobs/health等GET路径查get_stats().handlers会是空，
    submit端点虽有幂等兜底注册但只覆盖写路径）。故fixture内显式
    register_default_handlers(jq)（幂等，api/hippo.py:644同款语义）。

    src.api.*在fixture内延迟import：模块import有单例构造副作用
    （_register_job_handlers(_get_job_queue())、_lt_store=LongTermMemoryStore()），
    不能在conftest收集期全局import。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.will.job_queue as jq_mod
    from src.will.job_handlers import register_default_handlers
    from src.will.job_queue import JobQueue

    jq = JobQueue(db_path=str(tmp_path / "jobs.db"), max_workers=1)
    monkeypatch.setattr(jq_mod, "_job_queue", jq)

    import src.api.heredity as heredity_api
    from src.heredity.evolution_loop import EvolutionEngine

    monkeypatch.setattr(
        heredity_api, "evolution_engine", EvolutionEngine(db_path=str(tmp_path / "evolution.db"))
    )

    import src.api.hippo as hippo_api
    from src.hippo.dream_distiller import DreamDistiller
    from src.hippo.long_term_memory import LongTermMemoryStore

    tmp_lt_store = LongTermMemoryStore(db_path=str(tmp_path / "ltm.db"))
    monkeypatch.setattr(hippo_api, "_lt_store", tmp_lt_store)
    monkeypatch.setattr(hippo_api, "_dream_distiller", DreamDistiller(ltm_store=tmp_lt_store))

    import src.will.wakeup as wakeup_mod
    from src.will.wakeup import WakeupScheduler

    tmp_wakeup = WakeupScheduler(db_path=str(tmp_path / "wakeup.db"))
    monkeypatch.setattr(wakeup_mod, "_scheduler", tmp_wakeup)

    # F3b-⑧：goal_runner 持久化面隔离（will_goals.json 绝不落生产）——单例重建指向 tmp
    import src.will.goal_runner as goal_runner_mod

    monkeypatch.setattr(goal_runner_mod.GoalRunner, "_PERSIST_PATH", tmp_path / "goals.json")
    monkeypatch.setattr(goal_runner_mod, "_runner", None)

    register_default_handlers(jq)  # 幂等硬化：不依赖api/will.py的import时机

    import src.api.will as will_api

    app = FastAPI()
    app.include_router(will_api.router, prefix="/api/will")
    app.include_router(hippo_api.router, prefix="/api/hippo")
    app.state.will_client_job_db = jq._db_path  # 暴露tmp库路径供隔离护栏断言
    app.state.will_client_wakeup_db = tmp_wakeup._db_path  # 同上（wakeup数据面）
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def user_client(tmp_path, monkeypatch):
    """user API隔离测试面：真实router + tmp独立users库，绝不写生产 data/opensoul.db。

    背景（2026-10-06 live测试普查轮）：tests/test_user.py走live :8090注册→
    生产users表积173/179行testuser_/logintest_/metest_测试账号（每次全量跑
    +3行，无删除端点永不自清——03:27轮遗留#1残余"测试注册写生产users表"）。
    本fixture把user真实router挂进进程内app（镜像src/main.py:539的 /api/user
    前缀），数据面两层换tmp（gene_client/will_client同款路线——测试面=真实
    router/auth代码，数据面=隔离）：
    - users表：tmp users.db（settings.database_url换tmp + auth.db_pool换新
      SQLitePool实例，startup时connect——真实SQL适配层/bcrypt/JWT全程不变）
    - RBAC策略：env OPENSOUL_RBAC_POLICY_PATH→tmp策略文件 + enforcer单例复位
      （register的add_role副作用落tmp，不碰~/.hermes/opensoul/rbac_policy.csv）

    schema保真：users表DDL运行时从生产库sqlite_master只读镜像（永不与生产
    漂移）；生产库缺失时退回内联DDL（与生产同款列集）。
    app.state.user_client_db 暴露tmp库路径供隔离护栏断言。
    """
    import sqlite3 as _sqlite3

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.services.auth as auth_mod
    import src.services.permission as permission
    from src.config import settings
    from src.database.postgres import SQLitePool

    prod_db = settings.database_url.replace("sqlite:///", "").replace("sqlite://", "")
    try:
        with _sqlite3.connect(prod_db) as _c:
            ddl = _c.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
            ).fetchone()[0]
    except Exception:
        ddl = (
            "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
            "email TEXT DEFAULT '', password_hash TEXT NOT NULL, "
            "role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1, "
            "created_at TEXT DEFAULT (datetime('now')), "
            "updated_at TEXT DEFAULT (datetime('now')))"
        )
    tmp_db = tmp_path / "users.db"
    with _sqlite3.connect(tmp_db) as _c:
        _c.execute(ddl)
        _c.commit()

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(auth_mod, "db_pool", tmp_pool)
    monkeypatch.setenv(permission._RUNTIME_POLICY_ENV, str(tmp_path / "rbac_policy.csv"))
    monkeypatch.setattr(permission, "_enforcer", None)

    import src.api.user as user_api

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()  # tmp库startup时连接（真实SQLitePool数据面）
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(user_api.router, prefix="/api/user")
    app.state.user_client_db = str(tmp_db)  # 暴露tmp库路径供隔离护栏断言
    with TestClient(app) as c:
        yield c
    monkeypatch.setattr(permission, "_enforcer", None)


@pytest.fixture()
def heredity_client(tmp_path, monkeypatch):
    """heredity/brain 进化API隔离测试面：真实router + tmp独立进化数据面，绝不写
    生产 ~/.hermes/opensoul/evolution/evolution.db 与 data/opensoul.db 的
    experiences/evolution_log（gene_client/will_client/user_client同款路线；
    2026-10-06 heredity测试隔离轮收编TestEvolutionAPI）。

    背景：TestEvolutionAPI走live :8090，每次全量跑写生产evolution.db——86条api
    指纹假提案（43条api_test_agent竟被approved污染审批语义）+43条'no evidence
    proposal'+136条假触发器记录（api_storm_err/different_err）+breaker残留（测试
    曾把生产熔断器打开）。/api/brain/evolve更甚：每调用EvolutionEngine()新建引擎
    直写生产（本轮src侧已改共享heredity单例，见src/api/brain.py evolve）。
    本fixture把heredity与brain两个真实router挂进进程内app（镜像src/main.py:570
    的 /api/heredity、/api/brain 前缀），数据面三层换tmp——测试面=真实router/
    engine/collector代码，数据面=隔离：
    - evolution.db：heredity_api.evolution_engine换tmp EvolutionEngine（brain/evolve
      共享该单例——src/api/brain.py late import，will/job_handlers.py:34同款）
    - opensoul主库：settings.database_url换tmp（SelfEvolution.experiences读取/
    evolution_log写入 + ExperienceCollector.experiences写入全部落tmp；collector
    默认db_path与SQLitePool同源解析settings.database_url，自动跟随）
    - db_pool：src.database.postgres.db_pool换tmp SQLitePool（lifespan startup
      connect——SelfEvolution真实SQL适配层全程不变）
    - 采集源（第四层，heredity隔离轮遗留#1销账）：settings.experience_job_db_path/
    experience_eval_db_path换""（显式禁用）——ExperienceCollector._resolve_source_path
    三级解析，brain/evolve运行时经settings跟随，测试面零生产接触（此前
    job_queue.db/eval_loop.db只读豁免仍接触生产）。
    app.state.heredity_client_evolution_db / heredity_client_opensoul_db 暴露tmp
    库路径供隔离护栏断言。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.database.postgres as pg_mod
    from src.config import settings
    from src.database.postgres import SQLitePool

    tmp_db = tmp_path / "opensoul.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(pg_mod, "db_pool", tmp_pool)
    # 第四层数据面（heredity隔离轮遗留#1销账）：采集源也换掉——""=显式禁用
    # （experience_collector._resolve_source_path），brain/evolve运行时经settings
    # 跟随，测试面零生产接触（此前job_queue.db/eval_loop.db只读豁免仍接触生产）。
    monkeypatch.setattr(settings, "experience_job_db_path", "")
    monkeypatch.setattr(settings, "experience_eval_db_path", "")

    import src.api.heredity as heredity_api
    from src.heredity.evolution_loop import EvolutionEngine

    tmp_engine = EvolutionEngine(db_path=str(tmp_path / "evolution.db"))
    monkeypatch.setattr(heredity_api, "evolution_engine", tmp_engine)

    import src.api.brain as brain_api

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()  # tmp库startup时连接（真实SQLitePool数据面）
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(heredity_api.router, prefix="/api/heredity")
    app.include_router(brain_api.router, prefix="/api/brain")
    app.state.heredity_client_evolution_db = tmp_engine.store.db_path
    app.state.heredity_client_opensoul_db = str(tmp_db)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def capture_client(tmp_path, monkeypatch):
    """capture API隔离测试面：真实router + tmp独立captures库（开局=生产旧版
    漂移schema），绝不写生产 data/opensoul.db（user_client/heredity_client同款路线）。

    背景（2026-10-06 capture复活轮）：生产data/opensoul.db的captures是旧版漂移
    schema(id TEXT PRIMARY KEY/type/metadata/created_at TEXT)，引用capture_type的
    查询恒报"no such column"。tests/test_capture.py走live :8090的写用例
    （POST /api/capture/page）此前因此恒500从没写进去过；capture.py schema自愈
    修复后，live写用例会开始往生产captures表写测试行（修复本身制造新的测试污染
    源）——本fixture把真实capture router挂进进程内app（镜像src/main.py的
    /api/capture 前缀），数据面换tmp：
    - captures表：tmp capture.db **以生产旧版漂移schema原样开局**——每个用例
      天然演练_migrate_legacy_captures自愈路径（测试面=真实router/迁移代码，
      数据面=隔离）；lifespan startup连接tmp SQLitePool（真实SQL适配层全程不变）
    app.state.capture_client_db 暴露tmp库路径供隔离护栏断言/测试内直接改seed。
    """
    import sqlite3 as _sqlite3

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.capture as capture_api
    from src.config import settings
    from src.database.postgres import SQLitePool

    tmp_db = tmp_path / "capture.db"
    # 生产旧版漂移schema原样复刻（2026-10-06 sqlite3 data/opensoul.db
    # ".schema captures"实测DDL）——开局即漂移，自愈路径每个用例必经
    with _sqlite3.connect(tmp_db) as _c:
        _c.execute(
            "CREATE TABLE captures (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
            "type TEXT DEFAULT '', content TEXT DEFAULT '', metadata TEXT DEFAULT '{}', "
            "created_at TEXT DEFAULT (datetime('now')))"
        )
        _c.commit()

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(capture_api, "db_pool", tmp_pool)

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()  # tmp库startup时连接（真实SQLitePool数据面）
        yield
        await tmp_pool.disconnect()

    app = FastAPI(lifespan=_lifespan)
    app.include_router(capture_api.router, prefix="/api/capture")
    app.state.capture_client_db = str(tmp_db)  # 暴露tmp库路径供隔离护栏断言
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def mirror_client(tmp_path, monkeypatch):
    """mirror API隔离测试面：真实router + tmp独立数据面，绝不写生产
    data/timeline.db 与 ~/.opensoul/sandboxes（gene_client/will_client 同款路线；
    timeline 测试隔离拆件② live 测试写方收编——test_sandbox_isolation TestApi
    此前走 live :8090，服务进程把 sandbox_created/exec/fork 事件写生产
    timeline.db，2026-10-13 修前实测 11 行/轮）。测试面=真实 router/handler
    代码，数据面三层换 tmp：
    - sandbox 管理面：SandboxManager(sandbox_dir=tmp)（修前缺省 ~/.opensoul/sandboxes）
    - 模板面：SandboxTemplateEngine(db_path=tmp)（修前缺省 ~/opensoul/data/mirror/templates.db）
    - timeline 事件面：TIMELINE_DB autouse 隔离 + 懒缓存复位（_isolate_timeline_db）
      —— push_event 进程内构造 TimelineStore 读 env，事件落本用例 tmp 库
    app.state.mirror_client_* 暴露 tmp 路径供隔离护栏断言。
    src.api.mirror 在 fixture 内延迟 import：模块 import 有单例构造副作用
    （SandboxManager()/SandboxTemplateEngine() 缺省解析生产路径），不能在
    conftest 收集期全局 import。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.mirror as mirror_api
    from src.mirror.sandbox import SandboxManager
    from src.mirror.templates import SandboxTemplateEngine

    tmp_manager = SandboxManager(sandbox_dir=tmp_path / "sandboxes")
    tmp_templates = SandboxTemplateEngine(db_path=str(tmp_path / "templates.db"))
    monkeypatch.setattr(mirror_api, "manager", tmp_manager)
    monkeypatch.setattr(mirror_api, "template_engine", tmp_templates)

    from src.timeline.store import default_db_path

    app = FastAPI()
    app.include_router(mirror_api.router, prefix="/api/mirror")
    app.state.mirror_client_sandbox_dir = str(tmp_manager.sandbox_dir)
    app.state.mirror_client_templates_db = str(tmp_path / "templates.db")
    app.state.mirror_client_timeline_db = default_db_path()
    with TestClient(app) as c:
        yield c


# pipeline 知识入库面 tmp 库预建 schema（对齐 src/main.py lifespan 核心业务表 DDL +
# create_knowledge 真实触达的 tags/knowledge_tags 两表——生产表历史建表，tmp 库开局补齐）
_PIPELINE_KNOWLEDGE_DDL = """CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
    email TEXT DEFAULT '', password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1,
    tenant_id TEXT DEFAULT 'default',
    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS knowledge (
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
    knowledge_id TEXT NOT NULL, tag_id TEXT NOT NULL);"""


@pytest.fixture()
def pipeline_client(tmp_path, monkeypatch):
    """pipeline API隔离测试面：真实router + tmp独立数据面，绝不写生产
    data/timeline.db / data/vein/ / data/opensoul.db（gene_client/will_client/
    mirror_client 同款路线；timeline 测试隔离拆件② live 测试写方收编——
    test_pipeline.py+test_pipeline_api.py 此前走 live :8090，服务进程把
    pipeline_completed 事件写生产 timeline.db（2026-10-13 修前实测 8 行/轮=
    两文件 4+4），且上传用例同时写生产 vein 文件面与 opensoul.db 知识/用户行）。
    测试面=真实 router/handler 代码，数据面换 tmp：
    - vein 文件面：FileStore 工厂→tmp root+tmp vein_meta.db（修前缺省
      ~/opensoul/data/vein/files 与同目录 vein_meta.db）
    - 知识入库面：db_pool 三绑定（src.database.postgres / src.services.auth /
      src.services.knowledge 模块全局）→tmp SQLitePool（tmp opensoul.db 预建真
      schema）；embedding 降级空向量（空向量不写 qdrant=既有真降级路径）、meili
      AVAILABLE=False（外部索引面不写）
    - timeline 事件面：TIMELINE_DB autouse 隔离+懒缓存复位（_isolate_timeline_db）
    - pipeline history 内存面：每用例 fresh dict（修前 live 服务进程累积）
    app.state.pipeline_client_* 暴露 tmp 路径供隔离护栏断言。
    """
    import sqlite3 as _sqlite3

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.pipeline as pipeline_api
    import src.database.postgres as postgres_mod
    import src.services.auth as auth_mod
    import src.services.knowledge as knowledge_mod
    import src.vein.file_store as vein_store_mod
    from src.config import settings
    from src.database.postgres import SQLitePool

    # ── vein 文件面：tmp root + tmp meta db（FileStore 缺省解析生产路径）──
    tmp_vein_root = tmp_path / "vein" / "files"
    tmp_vein_db = tmp_path / "vein_meta.db"
    _real_file_store = vein_store_mod.FileStore  # 先捕获真类（patch 后模块属性=工厂自身，自递归坑）

    def _tmp_file_store(root=None, db_path=None):
        return _real_file_store(root=str(tmp_vein_root), db_path=str(tmp_vein_db))

    monkeypatch.setattr(vein_store_mod, "FileStore", _tmp_file_store)

    # ── 知识入库面：tmp opensoul.db 真 schema + 三绑定换 tmp pool ──
    tmp_os_db = tmp_path / "opensoul.db"
    with _sqlite3.connect(tmp_os_db) as _c:
        _c.executescript(_PIPELINE_KNOWLEDGE_DDL)
        _c.commit()
    monkeypatch.setenv("USERS_DB", str(tmp_os_db))  # register_user 自愈面随 tmp（F3b-② 先例）
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_os_db}")
    tmp_pool = SQLitePool()
    monkeypatch.setattr(postgres_mod, "db_pool", tmp_pool)
    monkeypatch.setattr(auth_mod, "db_pool", tmp_pool)
    monkeypatch.setattr(knowledge_mod, "db_pool", tmp_pool)

    async def _no_embeddings(_texts):
        return []

    monkeypatch.setattr(knowledge_mod, "get_embeddings_batch", _no_embeddings)
    monkeypatch.setattr(knowledge_mod.meili_client, "AVAILABLE", False)

    # ── pipeline history 内存面：每用例 fresh dict ──
    monkeypatch.setattr(pipeline_api, "_pipeline_history", {})

    @asynccontextmanager
    async def _lifespan(_app):
        await tmp_pool.connect()  # tmp库startup时连接（真实SQLitePool数据面）
        yield
        await tmp_pool.disconnect()

    from src.timeline.store import default_db_path

    app = FastAPI(lifespan=_lifespan)
    app.include_router(pipeline_api.router, prefix="/api/pipeline")
    app.state.pipeline_client_timeline_db = default_db_path()
    app.state.pipeline_client_vein_root = str(tmp_vein_root)
    app.state.pipeline_client_vein_db = str(tmp_vein_db)
    app.state.pipeline_client_opensoul_db = str(tmp_os_db)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def soma_client(tmp_path, monkeypatch):
    """soma-connector API隔离测试面：真实router + tmp独立数据面，绝不写生产
    data/timeline.db 与生产服务进程的组件注册表（gene_client/will_client/
    mirror_client 同款路线；timeline 测试隔离拆件② live 测试写方收编——
    test_soma_connector.py 此前走 live :8090，服务进程把 component_registered/
    data_pushed/component_unregistered 事件写生产 timeline.db（2026-10-13
    修前实测 7 行/轮），且测试组件（test-node-1/test-hb-node/test-push-node）
    残留在生产服务进程的内存注册表里冒充 online 组件）。测试面=真实
    router/handler 代码，数据面换 tmp：
    - 组件注册面：fresh ComponentRegistry()（修前=生产服务进程内存单例，
      测试行永不自清；进程内 app 用 tmp 实例，monkeypatch 模块单例）
    - timeline 事件面：TIMELINE_DB autouse 隔离+懒缓存复位（_isolate_timeline_db）
      —— push_event 进程内构造 TimelineStore 读 env，事件落本用例 tmp 库
    app.state.soma_client_* 暴露 tmp 真源供隔离护栏断言。
    src.api.soma_connector 在 fixture 内延迟 import（模块 import 有单例构造
    副作用，conftest 收集期不全局 import）。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.soma_connector as soma_api
    from src.api.soma_connector import ComponentRegistry
    from src.timeline.store import default_db_path

    tmp_registry = ComponentRegistry()
    monkeypatch.setattr(soma_api, "registry", tmp_registry)

    app = FastAPI()
    app.include_router(soma_api.router, prefix="/api/soma")
    app.state.soma_client_timeline_db = default_db_path()
    app.state.soma_client_registry = tmp_registry
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def vein_client(tmp_path, monkeypatch):
    """vein API隔离测试面：真实router + tmp独立数据面，绝不写生产
    data/timeline.db 与 data/vein/（gene_client/will_client/mirror_client/
    pipeline_client/soma_client 同款路线；timeline 测试隔离拆件② live 测试
    写方收编——test_vein.py 此前走 live :8090，服务进程把 file_uploaded/
    file_downloaded/file_deleted 事件写生产 timeline.db（2026-10-13 修前
    实测 7 行/轮=uploaded 3+downloaded 1+deleted 3），且上传/分片会话写生产
    vein 文件面（files/vein_meta.db/uploads）。测试面=真实 router/handler
    代码，数据面三层换 tmp：
    - 文件面：FileStore(root=tmp, db_path=tmp vein_meta.db)（修前缺省
      ~/opensoul/data/vein/files 与同目录 vein_meta.db）
    - 分片面：ChunkedUploader(temp_dir=tmp)（修前缺省 ~/opensoul/data/vein/uploads）
    - timeline 事件面：TIMELINE_DB autouse 隔离+懒缓存复位（_isolate_timeline_db）
      —— push_event 进程内构造 TimelineStore 读 env，事件落本用例 tmp 库
    cache 内存面每用例 fresh CacheManager（模块单例 monkeypatch 替换）。
    app.state.vein_client_* 暴露 tmp 真源供隔离护栏断言。
    src.api.vein 在 fixture 内延迟 import：模块 import 有单例构造副作用
    （FileStore()/ChunkedUploader() 缺省解析生产路径），不能在 conftest
    收集期全局 import。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.vein as vein_api
    from src.timeline.store import default_db_path
    from src.vein.cache import CacheManager
    from src.vein.chunked import ChunkedUploader
    from src.vein.file_store import FileStore

    tmp_root = tmp_path / "vein" / "files"
    tmp_meta_db = tmp_path / "vein_meta.db"
    tmp_uploads = tmp_path / "vein" / "uploads"
    tmp_store = FileStore(root=str(tmp_root), db_path=str(tmp_meta_db))
    tmp_uploader = ChunkedUploader(temp_dir=str(tmp_uploads))
    tmp_cache = CacheManager(max_size_mb=256, default_ttl=3600)
    monkeypatch.setattr(vein_api, "store", tmp_store)
    monkeypatch.setattr(vein_api, "uploader", tmp_uploader)
    monkeypatch.setattr(vein_api, "cache", tmp_cache)

    app = FastAPI()
    app.include_router(vein_api.router, prefix="/api/vein")
    app.state.vein_client_timeline_db = default_db_path()
    app.state.vein_client_root = str(tmp_root)
    app.state.vein_client_meta_db = str(tmp_meta_db)
    app.state.vein_client_uploads = str(tmp_uploads)
    app.state.vein_client_store = tmp_store
    app.state.vein_client_uploader = tmp_uploader
    with TestClient(app) as c:
        yield c
