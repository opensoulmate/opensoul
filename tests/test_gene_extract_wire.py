"""gene技能提炼wire契约（P0 wire bug回归）—— execution_log解码 + 哨兵过滤

缺口后果（acp-proxy生产端旧载荷 str(tool_calls_log)[:1000]）：
1. Python repr不是JSON → json.loads失败 → tool_calls=[] →
   extract_from_execution空契约直接None → 真实回合技能提炼恒400（学习链全灭）；
2. 修复端（acp-proxy agent/gene_wire.py）超预算截断会发 __TRUNCATED__ 哨兵
   条目——消费方必须按dunder名过滤，否则同一任务不同截断点→不同skill_id碎片。

钉四件事：
1. API解码宽容：JSON契约形态 + 旧Python repr形态都能解（goose宽容反序列化同门）；
2. 哨兵不进tool_sequence/模板；
3. skill_id对哨兵稳定（有无截断标记同id，防碎片化）；
4. 解不出的垃圾载荷照旧400（空契约非静默吞）。

单元测试tmp db直跑SkillLearner；API测试走live服务（conftest client→:8090，
每测独立uuid marker防测试耦合）。
"""

import json
import uuid

import pytest

from src.gene.skill_learner import SkillLearner


def _calls(*names):
    return [
        {"function": {"name": n, "arguments": {"path": "a.txt", "content": "x" * 20}}}
        for n in names
    ]


def _with_marker(*names, dropped=7, total=12):
    mid = {"function": {"name": "__TRUNCATED__", "arguments": {"dropped": dropped, "total": total}}}
    calls = _calls(*names)
    return calls[:1] + [mid] + calls[1:]


@pytest.fixture
def learner(tmp_path):
    return SkillLearner(db_path=str(tmp_path / "skills.db"))


class TestSentinelFilter:
    def test_marker_not_in_tool_sequence_or_template(self, learner):
        """__TRUNCATED__哨兵不进技能的工具序列/模板（gene_wire消费契约）"""
        s = learner.extract_from_execution(
            "s1", "哨兵过滤任务", _with_marker("read_file", "write_file"), success=True
        )
        assert s is not None
        assert "__TRUNCATED__" not in (s.code_template or "")
        assert "read_file" in s.code_template and "write_file" in s.code_template

    def test_skill_id_stable_across_truncation_marker(self, learner):
        """同一任务同一工具序列：有无截断哨兵→同一skill_id（防截断点碎片化）"""
        s1 = learner.extract_from_execution(
            "s1", "稳定性任务", _calls("read_file", "write_file"), success=True
        )
        s2 = learner.extract_from_execution(
            "s2",
            "稳定性任务",
            _with_marker("read_file", "write_file", dropped=3, total=5),
            success=True,
        )
        assert s1.skill_id == s2.skill_id

    def test_marker_only_payload_not_extracted(self, learner):
        """只剩哨兵的载荷=只有元数据→不提炼（None），且不落FAIL审计"""
        s = learner.extract_from_execution(
            "s1",
            "纯哨兵任务",
            [{"function": {"name": "__TRUNCATED__", "arguments": {"dropped": 9, "total": 9}}}],
            success=True,
        )
        assert s is None

    def test_real_tool_names_with_dunder_neighbour_untouched(self, learner):
        """非dunder名不受过滤影响（普通工具名原样进序列）"""
        s = learner.extract_from_execution(
            "s1", "普通名任务", _calls("read_file", "write_file"), success=True
        )
        assert s is not None  # 正常提炼=过滤器不误伤


class TestApiDecoding:
    """live服务（:8090）router解码契约：JSON / 旧repr / 垃圾三态。"""

    def _marker(self):
        return f"wire解码测试-{uuid.uuid4().hex[:8]}"

    def _payload(self, marker, execution_log):
        return {"task_description": marker, "execution_log": execution_log, "success": True}

    def test_contract_json_shape_accepted(self, client):
        """修复端gene_wire产物形态（function包装JSON）→ 200提炼成功"""
        marker = self._marker()
        log = json.dumps(_calls("read_file", "write_file"))
        r = client.post("/api/gene/skill/extract", json=self._payload(marker, log))
        assert r.status_code == 200, r.text
        assert r.json()["skill_id"].startswith("skill_")

    def test_legacy_python_repr_fallback(self, client):
        """旧生产端载荷 str(list)（Python repr + 扁平形态）→ literal_eval解码+
        wire边界形态归一→200；且与契约形态JSON载荷产生同一skill_id（解码等价，
        防碎片化——live E2E曾实锤扁平形态不归一会得空tool_sequence+异id）"""
        marker = self._marker()
        flat = [
            {"name": "read_file", "arguments": {"path": "a.txt"}, "result_preview": "p"},
            {"name": "write_file", "arguments": {"path": "b.txt"}, "result_preview": "p"},
        ]
        contract = _calls("read_file", "write_file")
        r1 = client.post("/api/gene/skill/extract", json=self._payload(marker, str(flat)))
        assert r1.status_code == 200, r1.text
        assert "read_file" in r1.json()["description"]  # 归一后tool_sequence非空
        r2 = client.post(
            "/api/gene/skill/extract", json=self._payload(marker, json.dumps(contract))
        )
        assert r2.status_code == 200, r2.text
        assert r1.json()["skill_id"] == r2.json()["skill_id"]

    def test_flat_json_also_normalized(self, client):
        """扁平形态即使走JSON（非repr）也被归一→同skill_id（形态归一不看解码方式）"""
        marker = self._marker()
        flat = [
            {"name": "read_file", "arguments": {"path": "a.txt"}},
            {"name": "write_file", "arguments": {"path": "b.txt"}},
        ]
        r1 = client.post("/api/gene/skill/extract", json=self._payload(marker, json.dumps(flat)))
        r2 = client.post(
            "/api/gene/skill/extract",
            json=self._payload(marker, json.dumps(_calls("read_file", "write_file"))),
        )
        assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
        assert r1.json()["skill_id"] == r2.json()["skill_id"]

    def test_truncation_marker_stripped_at_api(self, client):
        """带__TRUNCATED__哨兵的载荷→200且skill_id==无哨兵同任务（API面防碎片）"""
        marker = self._marker()
        log_marker = json.dumps(_with_marker("read_file", "write_file"))
        log_plain = json.dumps(_calls("read_file", "write_file"))
        r1 = client.post("/api/gene/skill/extract", json=self._payload(marker, log_marker))
        r2 = client.post("/api/gene/skill/extract", json=self._payload(marker, log_plain))
        assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
        assert r1.json()["skill_id"] == r2.json()["skill_id"]

    def test_garbage_payload_still_400(self, client):
        """解不出的垃圾载荷→400（空契约不静默吞，语义不变）"""
        r = client.post(
            "/api/gene/skill/extract", json=self._payload(self._marker(), "not json at all {")
        )
        assert r.status_code == 400

    def test_non_list_json_still_empty_contract(self, client):
        """JSON但非列表（dict/标量）→空契约→400（isinstance门保留）"""
        for bad in (json.dumps({"a": 1}), json.dumps("str"), json.dumps(42)):
            r = client.post("/api/gene/skill/extract", json=self._payload(self._marker(), bad))
            assert r.status_code == 400, bad
