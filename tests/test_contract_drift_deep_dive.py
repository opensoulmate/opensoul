"""CD-③ 待确认层深挖——裁决完整性门 + 裁决证据形状锁。

两层：
1. 完整性门：data/census_contract_drift_deep_dive.json 必须逐条覆盖
   data/census_contract_drift_post_fix.json 的全部非 produced findings
   （build_artifact 的完整性门真跑；反向多余裁决同样拒）。
2. 证据形状锁：每个 confirmed_drift/关键 resolved_ok 裁决的「真源形状」
   用运行时/源码契约锁定——真源形状变了裁决即失效，测试必须先炸，
   防裁决工件与真源漂移（裁决被测试真消费）。
"""

from __future__ import annotations

import importlib.util
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "census_contract_drift_deep_dive",
        ROOT / "scripts" / "census_contract_drift_deep_dive.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 完整性门 ────────────────────────────────────────────────────────────


class TestVerdictCoverage:
    def test_artifact_covers_every_pending_finding(self):
        tool = _load_tool()
        artifact = tool.build_artifact(check_only=True)
        post = json.loads((ROOT / "data" / "census_contract_drift_post_fix.json").read_text())
        pending = [f for f in post["findings"] if f["verdict"] != "produced"]
        assert len(pending) == artifact["counts_input_findings"] == 43
        covered = {(r["page"], r["line"], tuple(r["consumed_key"])) for r in artifact["verdicts"]}
        expected = {(f["page"], f["line"], tuple(f["key"])) for f in pending}
        assert covered == expected

    def test_verdict_counts_add_up(self):
        artifact = _load_tool().build_artifact(check_only=True)
        c = artifact["counts"]
        assert sum(c.values()) == artifact["counts_input_findings"] == 43
        assert c == {
            "resolved_ok": 33,
            "confirmed_drift": 8,
            "ruled_false_positive": 2,
        }

    def test_every_verdict_carries_evidence(self):
        artifact = _load_tool().build_artifact(check_only=True)
        for row in artifact["verdicts"]:
            assert row.get("evidence"), f"无据裁决禁出工件: {row}"
            if row["verdict"] == "confirmed_drift":
                assert row.get("repair_candidate"), f"confirmed_drift 必登记修复候选: {row}"

    def test_repair_candidates_are_four_ordered_slices(self):
        artifact = _load_tool().build_artifact(check_only=True)
        assert artifact["repair_candidates"] == [
            "①dedup 元素键收编",
            "②gland 页消费键收编",
            "③traces events 端点改指真端点",
            "④limb windows 元素容器收编",
        ]

    def test_misattribution_notes_recorded(self):
        artifact = _load_tool().build_artifact(check_only=True)
        # gland 3 键（挂错 /api/gland/test）+ system status（挂错 /api/organs）=4 条
        assert len(artifact["tool_misattribution_notes"]) == 4


# ── 证据形状锁（真源形状=裁决依据；形状变→裁决失效→测试先炸）──────────────


class TestDedupShapeEvidence:
    """候选①裁决依据：get_duplicate_report 元素=嵌套形，UI 平铺键全缺席。"""

    @pytest.mark.asyncio
    async def test_report_element_is_nested_not_flat(self, monkeypatch):
        from src.services import deduplication

        rows = [
            {
                "id": "a1",
                "user_id": "u1",
                "title": "同一标题",
                "content": "同一内容",
                "created_at": "t1",
            },
            {
                "id": "b2",
                "user_id": "u1",
                "title": "同一标题",
                "content": "同一内容",
                "created_at": "t2",
            },
        ]

        async def fake_fetch(sql, *args):
            return rows

        monkeypatch.setattr(deduplication, "db_pool", SimpleNamespace(fetch=fake_fetch))
        report = await deduplication.get_duplicate_report()
        assert len(report) == 1
        el = report[0]
        assert set(el.keys()) == {"item_a", "item_b", "similarity"}
        assert set(el["item_a"].keys()) == {"id", "title"}
        assert set(el["item_b"].keys()) == {"id", "title"}
        # UI 平铺消费键（DuplicatePair 接口 8 键中 7 键）对真源元素恒缺席=裁决锁死
        for flat_key in (
            "id_a",
            "id_b",
            "title_a",
            "title_b",
            "content_type",
            "created_at_a",
            "created_at_b",
        ):
            assert flat_key not in el, f"真源形状变了→①裁决失效须复挖: {flat_key}"

    @pytest.mark.asyncio
    async def test_similarity_key_is_the_only_produced_ui_key(self, monkeypatch):
        from src.services import deduplication

        rows = [
            {"id": "a1", "user_id": "u1", "title": "T", "content": "C", "created_at": "t1"},
            {"id": "b2", "user_id": "u1", "title": "T", "content": "C", "created_at": "t2"},
        ]

        async def fake_fetch(sql, *args):
            return rows

        monkeypatch.setattr(deduplication, "db_pool", SimpleNamespace(fetch=fake_fetch))
        report = await deduplication.get_duplicate_report()
        assert "similarity" in report[0]  # 普查 produced 判定的唯一真键


class TestGlandShapeEvidence:
    """候选②裁决依据：by_provider 元素=int（非对象）；test_provider 无 message 键。"""

    def test_by_provider_values_are_int_not_object(self):
        from src.gland.token_meter import TokenMeter

        tm = TokenMeter()
        tm.record("prov-x", "m", 10, 5)
        tm.record("prov-x", "m", 3, 2)
        by = tm.summary()["by_provider"]
        assert by["prov-x"] == 20
        assert isinstance(by["prov-x"], int), (
            "元素是 int→UI data.input/output/requests 恒 undefined"
        )
        assert not isinstance(by["prov-x"], dict)

    def test_test_provider_has_no_message_key(self):
        from src.gland.router import ModelRouter

        src = inspect.getsource(ModelRouter.test_provider)
        assert '"reply": reply.strip()' in src  # 真源键
        # 键定义形 '"message":' 缺席（["message"] 是上游响应读取非返回键——区分访问与定义）
        assert '"message":' not in src  # UI data.message 恒 undefined→回落遮蔽=②裁决锁死


class TestTrajectoryShapeEvidence:
    """候选③裁决依据：session.to_dict() 无 events；事件元素字段=搜索面 produced 键。"""

    def test_session_to_dict_has_no_events_key(self):
        from src.trajectory.store import TrajectorySession

        d = TrajectorySession().to_dict()
        assert "events" not in d, "真源形状变了→③裁决失效须复挖"
        for k in ("id", "status", "total_events"):
            assert k in d

    def test_event_element_has_search_consumed_keys(self):
        from src.trajectory.store import TrajectoryEvent

        d = TrajectoryEvent().to_dict()
        for k in ("id", "agent_id", "event_type", "status"):
            assert k in d  # trajectory/page.tsx:361 四键 produced 裁决锁死


class TestLimbShapeEvidence:
    """候选④裁决依据：list_windows 返回裸 list[WindowInfo] 非 {windows:[…]}。"""

    def test_list_windows_returns_bare_list(self):
        from src.api import limb_rpa

        src = inspect.getsource(limb_rpa.list_windows)
        assert "async def list_windows() -> list[WindowInfo]:" in src
        assert '"windows"' not in src  # 无 {windows: …} 包裹=UI data.windows 恒 undefined


class TestResolvedOkShapeEvidence:
    """resolved_ok 主要裁决组的真源形状锁（形状变→裁决复挖）。"""

    def test_tenant_to_dict_has_five_consumed_keys(self):
        from src.nest.tenant import Tenant

        d = Tenant(tenant_id="t", name="n").to_dict()
        for k in ("name", "tier", "description", "owner_user_id", "tags"):
            assert k in d

    def test_download_task_to_dict_has_consumed_keys(self):
        from src.api.native_downloader import DownloadTask

        d = DownloadTask(id="x", url="u", dest="d").to_dict()
        for k in ("id", "status"):
            assert k in d

    def test_knowledge_response_fields(self):
        from src.models.knowledge import KnowledgeResponse

        for k in ("title", "content", "metadata"):
            assert k in KnowledgeResponse.model_fields

    @pytest.mark.asyncio
    async def test_workspace_entries_element_keys(self, tmp_path, monkeypatch):
        from src.api import workspace_api

        (tmp_path / "a.txt").write_text("x")
        monkeypatch.setattr(workspace_api, "_validate_path", lambda p: tmp_path)
        out = await workspace_api.list_directory(str(tmp_path))
        assert out["entries"], "entries 元素形状=for-loop 字面量（普查提取边界）源码定型"
        for k in ("name", "path", "type", "size", "modified", "hidden"):
            assert k in out["entries"][0]

    def test_hermes_sessions_dual_keys(self):
        from src.api import hermes_bridge

        src = inspect.getsource(hermes_bridge._list_sessions_sync)
        assert '{"sessions": sessions, "total": len(sessions)}' in src

    def test_agent_response_model_fields(self):
        from src.api.agent_proxy import AgentResponse

        for k in ("response", "success", "error"):
            assert k in AgentResponse.model_fields

    def test_organs_and_checkall_element_keys(self):
        from src.api import config_api, diagnostics

        cfg_src = inspect.getsource(config_api.list_organs_config)
        for k in ('"key": organ', '"enabled": cfg.get("enabled", True)'):
            assert k in cfg_src
        diag_src = inspect.getsource(diagnostics.check_all_detailed)
        for k in ('"key": key', '"status": "ok" if r.status_code == 200 else "error"'):
            assert k in diag_src
