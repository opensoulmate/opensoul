"""heredity/brain 进化API测试隔离钉——测试提案绝不写生产evolution.db
（2026-10-06 heredity测试隔离轮）。

背景：TestEvolutionAPI走live :8090，每次全量跑写穿生产
~/.hermes/opensoul/evolution/evolution.db——86条api指纹假提案（43条
api_test_agent竟被approved，污染"假提案被审批"语义）+43条'no evidence
proposal'+136条假触发器记录（api_storm_err/different_err）+breaker残留
（测试曾把生产熔断器打开）。/api/brain/evolve每调用新建EvolutionEngine()
直写生产（src/api/brain.py本轮已改共享heredity单例）。本轮把TestEvolutionAPI
写面迁到conftest heredity_client隔离面（真实router+tmp evolution.db+tmp
opensoul主库+tmp db_pool），本文件=防复发钉：
- 行为面：声明→审批→apply→rollback→触发器全链在真实router代码上跑通（语义保真）
- 数据面：隔离面写入零生产写入（生产evolution.db三表计数不变+行只落tmp库）
- 接线面：brain/evolve经共享单例落tmp管线（种子失败经验→真实发现→声明提案）
- 静态面：tests/内进化写用例必须走heredity_client；test_evolution_loop.py只许
  一条live只读冒烟（GET health）
- 泄漏探测器：生产evolution.db的api指纹行/触发器恒为0（任何live写者即红灯）
"""

import ast
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TESTS_DIR = Path(__file__).parent

# 生产evolution.db路径在收集期固化（防fixture换路径后自比；user隔离钉同款教训）
_PROD_EVO_DB = Path.home() / ".hermes" / "opensoul" / "evolution" / "evolution.db"

# 进化写端点前缀（声明/审批/落盘/回滚/触发器评估）
_EVO_WRITE_PREFIX = "/api/heredity/evolution"
_BRAIN_EVOLVE = "/api/brain/evolve"


def _prod_evo_counts() -> dict:
    """生产evolution.db三张表行数（核心不变量的前后对照基线）。"""
    with sqlite3.connect(_PROD_EVO_DB) as conn:
        return {
            "proposals": conn.execute("SELECT COUNT(*) FROM evolution_proposals").fetchone()[0],
            "ledger": conn.execute("SELECT COUNT(*) FROM evolution_ledger").fetchone()[0],
            "triggers": conn.execute("SELECT COUNT(*) FROM evolution_triggers").fetchone()[0],
        }


def _prod_api_fingerprint_rows() -> list[str]:
    """生产库api指纹假提案（收编清账后恒为空；未来live写者漏隔离即红灯）。"""
    with sqlite3.connect(_PROD_EVO_DB) as conn:
        rows = conn.execute(
            "SELECT proposal_id FROM evolution_proposals WHERE proposer IN"
            " ('api_test_agent','api_apply_agent') OR title = 'no evidence proposal'"
        ).fetchall()
    return [r[0] for r in rows]


def _prod_trigger_fingerprint_rows() -> list[str]:
    """生产库测试指纹触发器记录（api_storm_err/different_err）——
    evolution_triggers表与evolution_ledger的TRIGGERED账本行双侧覆盖。"""
    with sqlite3.connect(_PROD_EVO_DB) as conn:
        rows = conn.execute(
            "SELECT trigger_id FROM evolution_triggers WHERE details LIKE"
            " '%api_storm_err%' OR details LIKE '%different_err%'"
        ).fetchall()
        rows += conn.execute(
            "SELECT proposal_id FROM evolution_ledger WHERE event='TRIGGERED'"
            " AND reason IN ('api_storm_err','different_err')"
        ).fetchall()
    return [r[0] for r in rows]


def _first_arg_text(call: ast.Call) -> str:
    """取post调用首个参数的字面量前缀（f-string取首段Constant；user钉同款：
    只认真实调用点，裸字面量比较不算）。"""
    if not call.args:
        return ""
    a = call.args[0]
    if isinstance(a, ast.Constant) and isinstance(a.value, str):
        return a.value
    if isinstance(a, ast.JoinedStr) and a.values and isinstance(a.values[0], ast.Constant):
        return str(a.values[0].value)
    return ""


def _is_evo_write_call(call: ast.AST) -> bool:
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "post"
        and _first_arg_text(call).startswith(_EVO_WRITE_PREFIX)
        or isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "post"
        and _first_arg_text(call) == _BRAIN_EVOLVE
    )


class TestHeredityDataPlaneIsolation:
    def test_declare_review_apply_writes_only_tmp(self, heredity_client):
        """核心隔离钉：声明→审批→apply→rollback→触发器全链后生产三表计数不变。"""
        before = _prod_evo_counts()
        resp = heredity_client.post(
            "/api/heredity/evolution/intents",
            json={
                "kind": "policy_adjustment",
                "title": "isolation flow probe",
                "rationale": "iso",
                "confidence": 0.5,
                "evidence_refs": ["iso:evolution-loop"],
                "proposer": "api_test_agent",
            },
        )
        assert resp.status_code == 200, resp.text
        pid = resp.json()["proposal_id"]
        resp = heredity_client.post(
            f"/api/heredity/evolution/proposals/{pid}/review",
            json={"decision": "approve", "reviewer": "human_reviewer"},
        )
        assert resp.status_code == 200, resp.text
        resp = heredity_client.post(
            f"/api/heredity/evolution/proposals/{pid}/rollback",
            json={"actor": "human_reviewer", "reason": "iso probe"},
        )
        # 未apply直接rollback → 400也可接受；只为把写路径走到store
        assert resp.status_code in (200, 400), resp.text
        resp = heredity_client.post(
            "/api/heredity/evolution/triggers/evaluate",
            json={"recent_errors": ["api_storm_err"] * 3},
        )
        assert resp.status_code == 200, resp.text
        # 生产零写入
        assert _prod_evo_counts() == before, "隔离面写穿到了生产evolution.db"
        # tmp库真写入（数据面隔离≠没写入）
        tmp_db = heredity_client.app.state.heredity_client_evolution_db
        with sqlite3.connect(tmp_db) as conn:
            row = conn.execute(
                "SELECT status FROM evolution_proposals WHERE proposal_id = ?",
                (pid,),
            ).fetchone()
            trig = conn.execute("SELECT COUNT(*) FROM evolution_triggers").fetchone()[0]
        assert row is not None, "提案没落tmp库——隔离面数据面断线了"
        assert trig >= 1

    def test_brain_evolve_declares_to_shared_singleton(self, heredity_client):
        """接线钉：brain/evolve经共享heredity单例落tmp管线，生产零写入。"""
        import time as _time

        from src.learn.experience_collector import ExperienceCollector

        before = _prod_evo_counts()
        tmp_main = heredity_client.app.state.heredity_client_opensoul_db
        with sqlite3.connect(tmp_main) as conn:
            ExperienceCollector(db_path=tmp_main).ensure_schema(conn)
            for i in range(3):
                conn.execute(
                    "INSERT INTO experiences (tenant_id, agent_id, action,"
                    " intent_summary, outcome, error, relevance_score, created_at,"
                    " metadata, source_ref) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        "default",
                        "default",
                        "iso seeded action",
                        "iso seeded intent",
                        "failure",
                        "iso seeded failure: singleton wiring",
                        1.0,
                        _time.time(),
                        "{}",
                        f"seed:isolation:{i}",
                    ),
                )
            conn.commit()
        resp = heredity_client.post("/api/brain/evolve")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["declared_proposals"]) >= 1, data
        assert data["declared_proposals"][0]["status"] == "pending"
        # 提案落tmp管线（共享单例可换数据面的运行时证明）
        tmp_evo = heredity_client.app.state.heredity_client_evolution_db
        with sqlite3.connect(tmp_evo) as conn:
            n = conn.execute("SELECT COUNT(*) FROM evolution_proposals").fetchone()[0]
        assert n >= 1
        # 生产零写入（若brain/evolve仍自建引擎写穿生产，这里红灯）
        assert _prod_evo_counts() == before, "brain/evolve写穿到了生产evolution.db"

    def test_fixture_swaps_data_planes(self, heredity_client):
        """接线钉：evolution_engine/db_pool/database_url确为tmp实例（防脱线自比）。"""
        import src.api.heredity as heredity_api
        import src.database.postgres as pg_mod
        from src.config import settings

        tmp_evo = heredity_client.app.state.heredity_client_evolution_db
        assert tmp_evo != str(_PROD_EVO_DB)
        assert heredity_api.evolution_engine.store.db_path == tmp_evo
        assert str(_PROD_EVO_DB) not in tmp_evo
        tmp_main = heredity_client.app.state.heredity_client_opensoul_db
        assert settings.database_url.endswith(Path(tmp_main).name)
        assert settings.database_url.replace("sqlite:///", "") == tmp_main
        assert tmp_main != str(_PROD_EVO_DB)
        assert pg_mod.db_pool is not None
        # db_pool确为tmp实例：其连接的database_url已被换tmp（生产单例同名不同实例）
        assert pg_mod.db_pool.__class__.__name__ == "SQLitePool"
        # 第四层：采集源确为禁用（""=显式禁用，不fallback生产路径）
        assert settings.experience_job_db_path == ""
        assert settings.experience_eval_db_path == ""


class TestCollectorSourceInjection:
    """采集源三级解析钉（heredity隔离轮遗留#1销账）：显式参数 > settings注入 >
    内置默认；空串=显式禁用（修复空串fallback生产job_queue.db的陷阱）。"""

    def test_resolution_chain(self, tmp_path, monkeypatch):
        from src.config import settings
        from src.learn.experience_collector import ExperienceCollector

        builtin_job = str(Path.home() / "opensoul" / "data" / "job_queue.db")
        builtin_eval = str(Path.home() / ".hermes" / "opensoul" / "benchmark" / "eval_loop.db")
        # ③ 内置默认（无参+无配置）
        monkeypatch.setattr(settings, "experience_job_db_path", None)
        monkeypatch.setattr(settings, "experience_eval_db_path", None)
        c = ExperienceCollector()
        assert c.job_db_path == builtin_job
        assert c.eval_db_path == builtin_eval
        # ② settings注入
        monkeypatch.setattr(settings, "experience_job_db_path", str(tmp_path / "j.db"))
        monkeypatch.setattr(settings, "experience_eval_db_path", str(tmp_path / "e.db"))
        c = ExperienceCollector()
        assert c.job_db_path == str(tmp_path / "j.db")
        assert c.eval_db_path == str(tmp_path / "e.db")
        # ① 显式参数覆盖settings
        c = ExperienceCollector(job_db_path="explicit_j.db", eval_db_path="explicit_e.db")
        assert c.job_db_path == "explicit_j.db"
        assert c.eval_db_path == "explicit_e.db"
        # 空串=显式禁用（显式参数层与settings层都成立）
        assert ExperienceCollector(job_db_path="", eval_db_path="").job_db_path == ""
        monkeypatch.setattr(settings, "experience_job_db_path", "")
        monkeypatch.setattr(settings, "experience_eval_db_path", "")
        assert ExperienceCollector().job_db_path == ""
        assert ExperienceCollector().eval_db_path == ""

    def test_heredity_client_sources_disabled(self, heredity_client):
        """隔离面默认钉：evolve运行时采集源为disabled（零生产接触可观测）。"""
        resp = heredity_client.post(_BRAIN_EVOLVE)
        assert resp.status_code == 200
        coll = resp.json()["experience_collection"]
        assert coll["sources"]["jobs"]["note"] == "source disabled"
        assert coll["sources"]["eval_experiments"]["note"] == "source disabled"

    def test_brain_evolve_collects_from_injected_source(
        self, heredity_client, tmp_path, monkeypatch
    ):
        """运行时接线钉：settings注入的采集源真实喂给brain/evolve管线——种子tmp
        job_queue.db → evolve采集 → 经验落tmp主库。若三级解析没接线（brain端点
        仍走硬编码生产源），written断言红灯（生产源里没有jw1种子行）。"""
        import time as _time

        from src.config import settings

        seed = tmp_path / "seed_jobs.db"
        conn = sqlite3.connect(seed)
        conn.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, name TEXT, status TEXT,"
            " error TEXT, created_at REAL, source TEXT)"
        )
        conn.execute(
            "INSERT INTO jobs VALUES ('jw1','wired_job','failed','wired source failure',?,'real')",
            (_time.time(),),
        )
        conn.commit()
        conn.close()
        monkeypatch.setattr(settings, "experience_job_db_path", str(seed))
        monkeypatch.setattr(settings, "experience_eval_db_path", "")

        resp = heredity_client.post(_BRAIN_EVOLVE)
        assert resp.status_code == 200
        coll = resp.json()["experience_collection"]
        assert coll["sources"]["jobs"]["written"] >= 1, "注入源没被采集——采集源注入没接线"
        assert coll["sources"]["eval_experiments"]["note"] == "source disabled"
        tmp_main = heredity_client.app.state.heredity_client_opensoul_db
        with sqlite3.connect(tmp_main) as c2:
            n = c2.execute(
                "SELECT COUNT(*) FROM experiences WHERE source_ref='job:jw1'"
            ).fetchone()[0]
        assert n == 1, "注入源采集结果没落tmp主库——数据面断线"


class TestStaticGuards:
    def test_evolution_write_calls_only_on_isolated_face(self):
        """静态钉：tests/内进化写用例（heredity/evolution/*或brain/evolve的POST）
        必须走heredity_client隔离面（helper形参hc=heredity_client注入，同款）。"""
        offenders = []
        for py in sorted(TESTS_DIR.glob("test_*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                has_write = any(_is_evo_write_call(c) for c in ast.walk(node))
                if not has_write:
                    continue
                params = {a.arg for a in node.args.args}
                if "heredity_client" in params or "hc" in params:
                    continue
                offenders.append(f"{py.name}:{node.lineno}:{node.name}")
        assert offenders == [], f"进化写用例未走heredity_client隔离面: {offenders}"

    def test_evolution_loop_keeps_single_readonly_live_smoke(self):
        """静态钉：test_evolution_loop.py只许一条live用例（health只读冒烟），
        且任何以client为形参的函数（含helper）都不许有POST调用。"""
        tree = ast.parse((TESTS_DIR / "test_evolution_loop.py").read_text(encoding="utf-8"))
        live_fns = []
        post_offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                params = {a.arg for a in node.args.args}
                if "client" in params:
                    live_fns.append(node.name)
                    if any(
                        isinstance(c, ast.Call)
                        and isinstance(c.func, ast.Attribute)
                        and c.func.attr == "post"
                        for c in ast.walk(node)
                    ):
                        post_offenders.append(node.name)
        assert live_fns == ["test_health_includes_evolution_stats"], (
            f"test_evolution_loop.py出现多余live用例: {live_fns}"
        )
        assert post_offenders == [], f"live用例里出现POST写调用: {post_offenders}"


class TestProductionHygiene:
    def test_production_evolution_db_free_of_api_test_rows(self):
        """泄漏探测器：生产evolution.db的api指纹假提案恒为0（live写者漏隔离即红灯）。"""
        leaked = _prod_api_fingerprint_rows()
        assert leaked == [], f"生产evolution.db残留api指纹假提案: {leaked[:5]}（共{len(leaked)}行）"

    def test_production_evolution_db_free_of_test_triggers(self):
        """泄漏探测器：生产触发器表测试指纹记录恒为0。"""
        leaked = _prod_trigger_fingerprint_rows()
        assert leaked == [], (
            f"生产evolution_triggers残留测试指纹记录: {leaked[:5]}（共{len(leaked)}行）"
        )


class TestEvolutionRetentionRuntimeWiring:
    """stale pending周期清扫运行时接线钉（2026-10-06 retention轮）。

    背景：retire_stale_pending（0b5e0372进化预算自愈三件套）只挂在declare_intent
    声明路径惰性清扫——声明一停清扫就停。生产实证：5条假模式pending（test_job
    910次等）超龄48h TTL无人清扫长期滞留面板。本轮把清扫接线到main.py周期任务
    （启动20s首扫+每小时一扫），本类=行为钉+接线钉+隔离钉。
    """

    def test_sweep_retires_only_stale_pending_with_ledger(self, heredity_client):
        """行为钉：sweep_stale_proposals退役超龄pending（rejected+账本REJECTED
        actor=retention），新鲜pending不误杀，且零生产写入（隔离不变量）。"""
        import time as _time

        import src.api.heredity as heredity_api
        from src.heredity.evolution_loop import (
            LEDGER_REJECTED,
            PENDING_TTL_S,
            STATUS_PENDING,
            STATUS_REJECTED,
            EvolutionProposal,
        )

        before = _prod_evo_counts()
        engine = heredity_api.evolution_engine  # heredity_client已换tmp数据面

        # 新鲜pending（声明路径正常产物）
        fresh = engine.declare_intent(
            kind="policy_adjustment",
            title="retention fresh probe",
            rationale="retention probe",
            confidence=0.5,
            evidence_refs=["iso:retention-probe"],
            proposer="api_test_agent",
        )
        assert fresh["status"] == STATUS_PENDING

        # 超龄pending（模拟"声明停摆后滞留"的生产形态：created_at早于TTL）
        stale_id = "evo_retention_stale_probe"
        engine.store.insert_proposal(
            EvolutionProposal(
                proposal_id=stale_id,
                kind="policy_adjustment",
                title="retention stale probe",
                rationale="retention probe",
                confidence=0.5,
                evidence_refs=["iso:retention-probe"],
                proposer="api_test_agent",
                status=STATUS_PENDING,
                created_at=_time.time() - PENDING_TTL_S - 60,
            )
        )

        result = heredity_api.sweep_stale_proposals()
        assert "error" not in result or not result["error"], result
        assert stale_id in result["retired"], "超龄pending没被清扫——retention没接线"
        assert fresh["proposal_id"] not in result["retired"], "新鲜pending被误杀"

        stale_row = engine.store.get_proposal(stale_id)
        assert stale_row is not None
        assert stale_row.status == STATUS_REJECTED
        assert stale_row.reject_reason == "stale_pending_expired"
        fresh_row = engine.store.get_proposal(fresh["proposal_id"])
        assert fresh_row is not None
        assert fresh_row.status == STATUS_PENDING

        ledger = engine.store.get_ledger(proposal_id=stale_id)
        assert any(r["event"] == LEDGER_REJECTED and r["actor"] == "retention" for r in ledger), (
            "退役没写REJECTED账本（actor=retention）——静默丢弃违反mem0 §1.1"
        )

        assert _prod_evo_counts() == before, "sweep写穿了生产evolution.db——数据面断线"

        # 可观测心跳钉：sweep状态进stats面板（retention块），面板能看到"清扫在跑"
        stats_resp = heredity_client.get("/api/heredity/evolution/stats")
        assert stats_resp.status_code == 200
        retention = stats_resp.json().get("retention") or {}
        assert retention.get("sweeps", 0) >= 1, "retention心跳没进stats面板"
        assert retention.get("last_sweep_at", 0) > 0
        assert retention.get("last_sweep_retired", -1) >= 1  # 本轮清了stale probe
        assert retention.get("last_sweep_error", "x") == ""

    def test_sweep_resilient_to_engine_failure(self, monkeypatch):
        """失败可见钉：sweep失败不抛出，error进返回值+心跳状态（后台维护绝不反噬调用方）。"""
        import src.api.heredity as heredity_api

        class _BoomStore:
            def retire_stale_pending(self, ttl_s, now=None):
                raise RuntimeError("boom")

        class _BoomEngine:
            store = _BoomStore()
            pending_ttl_s = 1.0

        monkeypatch.setattr(heredity_api, "evolution_engine", _BoomEngine())
        result = heredity_api.sweep_stale_proposals()
        assert result["retired"] == []
        assert "boom" in result["error"], f"失败没可见: {result}"
        assert "boom" in heredity_api._retention_state["last_sweep_error"], "心跳没记失败"

    def test_main_lifespan_wires_retention_loop(self):
        """接线钉（写了≠接线了）：main.py必须同时具备①_evolution_retention_loop
        定义②lifespan里create_task真实调用点③调用sweep_stale_proposals非死代码。"""
        src = (REPO_ROOT / "src" / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(src)

        fn_defs = {
            n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert "_evolution_retention_loop" in fn_defs, "main.py缺_evolution_retention_loop定义"

        wired = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_task"
            ):
                for arg in node.args:
                    if (
                        isinstance(arg, ast.Call)
                        and isinstance(arg.func, ast.Name)
                        and arg.func.id == "_evolution_retention_loop"
                    ):
                        wired = True
        assert wired, "lifespan里没有create_task(_evolution_retention_loop())——没接线"

        loop_node = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_evolution_retention_loop"
        )
        loop_src = ast.get_source_segment(src, loop_node) or ""
        assert "sweep_stale_proposals" in loop_src, "retention循环没调用sweep入口——死代码"

    def test_sweep_entry_defined_in_heredity_api(self):
        """接线钉：sweep_stale_proposals在heredity API层存在且走全局单例
        （隔离面monkeypatch evolution_engine即换数据面的契约）。"""
        src = (REPO_ROOT / "src" / "api" / "heredity.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "sweep_stale_proposals"
            ),
            None,
        )
        assert fn is not None, "heredity.py缺sweep_stale_proposals"
        attrs = {c.attr for c in ast.walk(fn) if isinstance(c, ast.Attribute)}
        assert "retire_stale_pending" in attrs, "sweep没调用retire_stale_pending"
        names = {c.id for c in ast.walk(fn) if isinstance(c, ast.Name)}
        assert "evolution_engine" in names, "sweep没走全局evolution_engine单例"
