"""Shared test fixtures for OpenSoul integration tests."""

import copy
from pathlib import Path

import httpx
import pytest

BASE_URL = "http://localhost:8090"


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
    src/main.py 的 /api/will、/api/hippo 前缀），三层数据面换tmp实例——
    测试面=真实router/handler代码，数据面=隔离：
    - job_queue：tmp jobs.db（monkeypatch模块单例_job_queue）
    - heredity.evolution_engine：tmp evolution.db（真实handler评估写入落tmp）
    - hippo._lt_store/_dream_distiller：tmp LTM库（dream蒸馏写入落tmp）

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

    register_default_handlers(jq)  # 幂等硬化：不依赖api/will.py的import时机

    import src.api.will as will_api

    app = FastAPI()
    app.include_router(will_api.router, prefix="/api/will")
    app.include_router(hippo_api.router, prefix="/api/hippo")
    app.state.will_client_job_db = jq._db_path  # 暴露tmp库路径供隔离护栏断言
    with TestClient(app) as c:
        yield c
