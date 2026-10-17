"""T3 死面处置（H1-③）——删除件禁复活锁死 + 覆盖活面语义回归。

处置裁决（docs/pending-decisions.md DEC-⑳ / docs/dev-roadmap.md T3-①）：
- 删 10：语义被活面 100% 覆盖（重复原语/别名/便利包装/被 route_policy 取代）
- 留 13：配置 API / 独特语义 / 测试支撑面（H0「无消费≠断点」宁窄勿滥）

本文件职责：
1) 静态锁死——已删符号在目标模块永久缺席（禁复活），覆盖活面符号恒在场
2) 语义回归——被覆盖语义经活面仍然可达（删包装没删能力）
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"

# (模块路径, 已删符号, 覆盖该语义的活面符号)
DELETED_WITH_COVER = [
    ("services/meili_indexer.py", "delete_knowledge_doc", "index_knowledge_doc"),
    ("database/qdrant.py", "delete_by_knowledge_id", "delete_points"),
    ("middleware/auth.py", "get_current_active_user", "get_current_user"),
    ("mcp/session_grants.py", "filter_tools", "is_tool_allowed"),
    ("hippo/decision_log.py", "batch_update_with_outcomes", "update_with_outcome"),
    ("healer/diagnose.py", "heal_all", "diagnose_and_heal"),
    ("healer/diagnose.py", "_noop", "diagnose"),
    ("heredity/migration.py", "get_pending_migrations", "list_scripts"),
    ("api/enterprise.py", "audit_write", "_record_audit"),
    ("api/model_router.py", "load_config", "get_router_config"),
    ("api/model_router.py", "save_config", "set_router_mode"),
    ("config_manager.py", "save_config", "update"),
    ("config_manager.py", "get_config", "get"),
]


def _defs_and_assigns(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
    return names


class TestDisposedSymbolsStayDead:
    @pytest.mark.parametrize("mod,gone,cover", DELETED_WITH_COVER)
    def test_deleted_symbol_absent_and_cover_alive(self, mod, gone, cover):
        names = _defs_and_assigns(SRC / mod)
        assert gone not in names, f"{mod} 的 {gone} 复活了（T3 处置已删，禁复活）"
        assert cover in names, f"{mod} 的覆盖活面 {cover} 缺席（删件不得带走活面）"

    def test_model_router_config_file_face_removed(self):
        # load_config/save_config 的孤儿常量随删（CONFIG_FILE 面=被 route_policy 取代）
        names = _defs_and_assigns(SRC / "api" / "model_router.py")
        for gone in ("CONFIG_DIR", "CONFIG_FILE", "DEFAULT_CONFIG"):
            assert gone not in names, f"model_router {gone} 孤儿常量复活"

    def test_no_test_or_script_reconsumes_disposed_symbols(self):
        # 测试/脚本面同样禁复活（消费方断=处置完整性）
        root = Path(__file__).resolve().parent.parent
        import subprocess

        pattern = "|".join(
            [
                "delete_knowledge_doc",
                "delete_by_knowledge_id",
                "get_current_active_user",
                "batch_update_with_outcomes",
                "get_pending_migrations",
                r"\baudit_write\b",
            ]
        )
        out = subprocess.run(
            ["grep", "-rn", "-E", pattern, "tests", "scripts"],
            cwd=root,
            capture_output=True,
            text=True,
        ).stdout
        residual = [
            ln
            for ln in out.splitlines()
            if ln.strip() and "test_t3_disposal.py" not in ln and "T3 处置" not in ln
        ]
        assert residual == [], f"处置符号仍有消费残留：{residual}"

    def test_config_manager_get_config_not_reimported(self):
        # get_config 专查：同名符号在 llm/reflex/download_api 是各自活端点函数（禁误伤），
        # 这里只查 config_manager 面的消费残留（import/属性访问）。
        root = Path(__file__).resolve().parent.parent
        hits = []
        for base in ("src", "tests", "scripts"):
            for p in (root / base).rglob("*.py"):
                if p.name == "test_t3_disposal.py":
                    continue
                for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                    if "config_manager" in line and "get_config" in line:
                        hits.append(f"{p.relative_to(root)}:{i}:{line.strip()}")
        assert hits == [], f"config_manager.get_config 仍有消费残留：{hits}"


class TestCoveringFacesCarrySemantics:
    """删包装没删能力：被覆盖语义经活面仍然可达。"""

    def test_grant_filtering_semantics_via_is_tool_allowed(self, tmp_path):
        from src.mcp.session_grants import SessionToolGate

        gate = SessionToolGate(db_path=str(tmp_path / "grants.db"))
        gate.grant("sess-1", "mcp-github", "list_issues")
        tools = [
            {"server_id": "mcp-github", "name": "list_issues"},
            {"server_id": "mcp-github", "name": "create_pr"},
            {"server_id": "mcp-memory", "name": "search_nodes"},
        ]
        visible = [t for t in tools if gate.is_tool_allowed("sess-1", t["server_id"], t["name"])]
        assert [t["name"] for t in visible] == ["list_issues"]
        # fail-closed：零 grant 零可见
        assert [
            t for t in tools if gate.is_tool_allowed("sess-none", t["server_id"], t["name"])
        ] == []

    def test_batch_outcome_semantics_via_update_with_outcome(self, tmp_path):
        from src.hippo.decision_log import DecisionLog

        log = DecisionLog(db_path=str(tmp_path / "dl.db"), max_resolved=500)
        e1 = log.store_decision(decision="b1", domain="batch")
        e2 = log.store_decision(decision="b2", domain="batch")
        results = []
        for u in [
            {"decision_id": e1["decision_id"], "outcome": "o1", "success": True},
            {"decision_id": e2["decision_id"], "outcome": "o2", "success": False},
            {"decision_id": "dec_missing", "outcome": "x", "success": None},
        ]:
            r = log.update_with_outcome(
                decision_id=u["decision_id"], outcome=u["outcome"], success=u["success"]
            )
            results.append(r is not None)
        assert results == [True, True, False]

    def test_pending_migrations_semantics_via_list_scripts(self):
        from src.heredity.migration import MigrationEngine

        engine = MigrationEngine()
        engine.create_migration_script(
            migration_type="data",
            component_id="comp-a",
            from_version="1.0",
            to_version="1.1",
            up_sql="SELECT 1",
        )
        engine.create_migration_script(
            migration_type="data",
            component_id="comp-a",
            from_version="1.1",
            to_version="1.2",
            up_sql="SELECT 2",
        )
        pending = engine.list_scripts(applied=False)
        assert len(pending) == 2
        engine.apply_script(pending[0]["script_id"])
        pending_after = engine.list_scripts(applied=False)
        assert [s["script_id"] for s in pending_after] == [pending[1]["script_id"]]
        assert engine.get_stats()["pending_scripts"] == 1

    def test_config_read_write_semantics_via_manager(self, tmp_path, monkeypatch):
        # get_config/save_config 语义经活面 ConfigManager.get/update 仍可达（删包装没删能力）
        import src.config_manager as cm

        monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cm, "CONFIG_PATH", tmp_path / "config.yaml")
        mgr = cm.ConfigManager()
        # 读语义（原 get_config 覆盖面）：整表/分节/分键/缺省 None
        cfg = mgr.get()
        assert isinstance(cfg, dict) and cfg["version"] == cm.DEFAULT_CONFIG["version"]
        assert mgr.get(section="models") == cfg["models"]
        assert mgr.get(section="models", key="default") == cfg["models"]["default"]
        assert mgr.get(section="nope") is None
        assert mgr.get(section="models", key="nope") is None
        # 写语义（原 save_config 覆盖面）：update 落盘、新实例回读一致
        mgr.update({"models": {"default": "x-test-model"}})
        assert (tmp_path / "config.yaml").exists()
        fresh = cm.ConfigManager()
        assert fresh.get(section="models", key="default") == "x-test-model"

    def test_config_api_face_carries_read_semantics(self, client):
        # 覆盖语义的生产活面：GET /api/config（config_api 真消费 config_manager.get）
        resp = client.get("/api/config")
        assert resp.status_code in (200, 401)
        if resp.status_code == 200:
            assert isinstance(resp.json(), dict)
