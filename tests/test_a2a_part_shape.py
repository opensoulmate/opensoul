"""F4b-⑭ a2a Part 形状 v1.0 对齐 —— wire 出口平铺 oneof + 输入三形兼容真读。

修前漂移（F4b-⑬ 留档候选"Part·Artifact 形状未对齐"）：
①出线 part 用 {"type":"text","text":...} —— v1.0 Part（spec §1.4 spec/a2a.proto 唯一
  规范定义；§4.1.6 字段表）是平铺 oneof {text|raw|url|data}+metadata/filename/mediaType，
  判别式=成员名本身，wire 无 kind 无 type（kind 是 0.3 形，v1.0 §A.2.1 已移除；type 是
  0.2 代/文档混用误形 a2aproject/A2A#1365）——出线形两代都不是，v1.0 客户端无从识别。
②文本提取只认 type=="text"（_extract_text/_handle_chat 上下文）——v1.0 客户端按 §4.1.6
  发 {"text":...} 被静默丢弃：提取文本为空、prompt 变空（真断点）。
③Message/Artifact 的 proto 可选成员（metadata/extensions/referenceTaskIds）被 pydantic
  默认 extra=ignore 静默丢弃（F4b-② LLMRequest.stream 同型）。

收口（声明从实现派生 + 单点出口，F4b-⑤/envelope/wire() 同哲学）：
- part_text 三形统一读取（v1.0 平铺/0.3 kind/legacy type），非文本 part 不冒充文本
- _part_to_wire/_parts_to_wire 单点派生 v1.0 形（只改返回 dict 不污染真源；
  未知判别值原样透传不 fabricate，⑬ 未知枚举同款纪律）
- Task.wire() 唯一序列化出口全响应面（status.message/history/artifacts）派生
- Message/Artifact 可选成员补齐（缺省 None=wire 面整键省略，proto3 JSON 语义）
"""

import pytest
from fastapi.testclient import TestClient

from src.a2a.models import (
    Artifact,
    Message,
    Task,
    TaskStatus,
    _part_to_wire,
    part_text,
)
from src.a2a.task_manager import task_manager
from src.main import app


@pytest.fixture()
def app_client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def clean_tasks():
    """全局 TaskManager 隔离：任务面清空，事后逐字还原（禁污染既有用例）。"""
    saved_tasks = dict(task_manager.tasks)
    task_manager.tasks.clear()
    try:
        yield task_manager
    finally:
        task_manager.tasks.clear()
        task_manager.tasks.update(saved_tasks)


def rpc(client, method, params=None, rid="part-1"):
    resp = client.post(
        "/a2a",
        json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}},
    )
    assert resp.status_code == 200
    return resp.json()


def _v10(text):
    return {"role": "user", "parts": [{"text": text}]}


class TestPartTextThreeShapes:
    """part_text：输入三形统一读取（真断点②的修正面）。"""

    def test_v10_flat_text_extracted(self):
        # 修前反证：{"text":...} 无 type 键，旧提取只认 type=="text" → 丢弃
        assert part_text({"text": "你好v10形状"}) == "你好v10形状"

    def test_legacy_type_shape_extracted(self):
        assert part_text({"type": "text", "text": "legacy"}) == "legacy"

    def test_kind_shape_extracted(self):
        assert part_text({"kind": "text", "text": "kind03"}) == "kind03"

    def test_v10_empty_string_preserved(self):
        assert part_text({"text": ""}) == ""

    def test_legacy_text_key_missing_yields_empty(self):
        assert part_text({"type": "text"}) == ""

    def test_data_part_not_text(self):
        assert part_text({"data": {"k": 1}}) is None
        assert part_text({"type": "data", "data": {"k": 1}}) is None

    def test_file_part_not_text(self):
        assert part_text({"kind": "file", "file": {"uri": "https://x"}}) is None

    def test_unknown_discriminator_not_text(self):
        assert part_text({"type": "widget", "text": "x"}) is None

    def test_non_dict_not_text(self):
        assert part_text("just-a-string") is None
        assert part_text(None) is None

    def test_non_str_text_not_text(self):
        assert part_text({"kind": "text", "text": 5}) is None


class TestPartWireV10Shape:
    """_part_to_wire：出线唯一派生形（真断点①的修正面）。"""

    def test_legacy_text_part_to_v10(self):
        out = _part_to_wire({"type": "text", "text": "hi"})
        assert out == {"text": "hi"}
        assert "type" not in out  # 硬反证：判别键不出线

    def test_kind_text_part_to_v10(self):
        out = _part_to_wire({"kind": "text", "text": "hi"})
        assert out == {"text": "hi"}
        assert "kind" not in out  # 硬反证

    def test_v10_part_passthrough_unchanged_and_not_mutated(self):
        src = {"text": "hi", "metadata": {"m": 1}}
        out = _part_to_wire(src)
        assert out == {"text": "hi", "metadata": {"m": 1}}
        assert out == src  # v1.0 形原样
        assert out is not src  # 返回新 dict
        _part_to_wire({"type": "text", "text": "x", "metadata": {}})
        assert src == {"text": "hi", "metadata": {"m": 1}}  # 真源零污染硬反证

    def test_data_part_to_v10(self):
        assert _part_to_wire({"kind": "data", "data": {"a": 1}}) == {"data": {"a": 1}}
        assert _part_to_wire({"type": "data", "data": [1, 2]}) == {"data": [1, 2]}

    def test_file_uri_part_to_v10(self):
        # 0.3→1.0 映射照抄 a2a-js migration-guide 对照表
        out = _part_to_wire(
            {
                "kind": "file",
                "file": {"uri": "https://x/p.png", "mimeType": "image/png", "name": "p.png"},
            }
        )
        assert out == {"url": "https://x/p.png", "mediaType": "image/png", "filename": "p.png"}

    def test_file_bytes_part_to_v10(self):
        out = _part_to_wire(
            {"type": "file", "file": {"bytes": "aGk=", "mimeType": "text/plain", "name": "a.txt"}}
        )
        assert out == {"raw": "aGk=", "mediaType": "text/plain", "filename": "a.txt"}

    def test_unknown_discriminator_passthrough(self):
        src = {"type": "widget", "payload": 1}
        out = _part_to_wire(src)
        assert out == src  # off-spec 如实暴露，禁 fabricate
        assert out is not src

    def test_known_disc_without_content_passthrough(self):
        # {"kind":"text"} 无 text——不编造空内容成员
        assert _part_to_wire({"kind": "text"}) == {"kind": "text"}

    def test_extras_preserved_on_conversion(self):
        out = _part_to_wire(
            {
                "type": "text",
                "text": "hi",
                "metadata": {"m": 1},
                "filename": "f",
                "mediaType": "text/plain",
            }
        )
        assert out == {
            "text": "hi",
            "metadata": {"m": 1},
            "filename": "f",
            "mediaType": "text/plain",
        }

    def test_non_dict_passthrough(self):
        assert _part_to_wire("x") == "x"


class TestMessageArtifactAdditiveFields:
    """Message/Artifact proto 可选成员（真断点③的修正面）。"""

    def test_message_additive_fields_roundtrip(self):
        m = Message(
            role="user",
            parts=[{"text": "hi"}],
            metadata={"k": "v"},
            extensions=["urn:x"],
            referenceTaskIds=["t-9"],
        )
        d = m.model_dump(exclude_none=True)
        assert d["metadata"] == {"k": "v"}
        assert d["extensions"] == ["urn:x"]
        assert d["referenceTaskIds"] == ["t-9"]

    def test_message_unset_fields_omitted_on_wire(self):
        m = Message(role="user", parts=[{"text": "hi"}])
        w = m.model_dump(exclude_none=True)
        assert "metadata" not in w
        assert "extensions" not in w
        assert "referenceTaskIds" not in w

    def test_artifact_additive_fields_roundtrip(self):
        a = Artifact(parts=[{"text": "r"}], metadata={"m": 1}, extensions=["urn:y"])
        d = a.model_dump(exclude_none=True)
        assert d["metadata"] == {"m": 1}
        assert d["extensions"] == ["urn:y"]

    def test_artifact_unset_fields_omitted_on_wire(self):
        a = Artifact(parts=[{"text": "r"}])
        d = a.model_dump(exclude_none=True)
        assert "metadata" not in d
        assert "extensions" not in d


class TestWireTaskPartFace:
    """Task.wire() 全响应面 part 派生 + 真源零污染。"""

    def _task(self):
        return Task(
            id="t-wire",
            contextId="c-wire",
            status=TaskStatus(
                state="completed",
                message=Message(role="agent", parts=[{"type": "text", "text": "resp"}]),
            ),
            history=[Message(role="user", parts=[{"kind": "text", "text": "req"}])],
            artifacts=[Artifact(parts=[{"type": "text", "text": "report"}])],
        )

    def test_wire_converts_status_history_artifacts(self):
        w = self._task().wire()
        sp = w["status"]["message"]["parts"][0]
        assert sp == {"text": "resp"} and "type" not in sp
        hp = w["history"][0]["parts"][0]
        assert hp == {"text": "req"} and "kind" not in hp
        ap = w["artifacts"][0]["parts"][0]
        assert ap == {"text": "report"} and "type" not in ap

    def test_wire_does_not_pollute_memory(self):
        task = self._task()
        task.wire()
        # 硬反证：内存真源仍保留内部形（只改返回 dump 不污染真源，⑫/⑬ 同纪律）
        assert task.history[0].parts == [{"kind": "text", "text": "req"}]
        assert task.artifacts[0].parts == [{"type": "text", "text": "report"}]


class TestAPIPartFullChain:
    """API 全链：v1.0 输入真被读取（真断点②）+ 出线真 v1.0 形（真断点①）。"""

    def test_v10_input_text_fed_to_processing(self, app_client, clean_tasks):
        # v1.0 客户端按 §4.1.6 发 {"text":...}：修前 _extract_text 丢弃 → stub 回显空 prompt
        body = rpc(
            app_client,
            "SendMessage",
            {
                "metadata": {"llm_mode": "stub"},
                "message": _v10("你好v10形状"),
            },
        )
        task = body["result"]["task"]
        reply_part = task["status"]["message"]["parts"][0]
        assert "type" not in reply_part and "kind" not in reply_part  # 出线 v1.0 形
        assert "你好v10形状" in reply_part["text"]  # 提取真读 v1.0 形（硬反证：修前为空）
        user_part = task["history"][0]["parts"][0]
        assert user_part == {"text": "你好v10形状"}

    def test_legacy_input_response_parts_v10_flat(self, app_client, clean_tasks):
        body = rpc(
            app_client,
            "tasks/send",
            {
                "metadata": {"llm_mode": "stub"},
                "message": {"role": "user", "parts": [{"type": "text", "text": "legacy入"}]},
            },
        )
        task = body["result"]
        reply_part = task["status"]["message"]["parts"][0]
        assert "legacy入" in reply_part["text"]
        assert set(reply_part.keys()) == {"text"}  # 判别键不出线（硬反证）

    def test_message_additive_fields_survive_api(self, app_client, clean_tasks):
        msg = _v10("带元数据")
        msg["metadata"] = {"k": "v"}
        msg["extensions"] = ["urn:x"]
        msg["referenceTaskIds"] = ["t-9"]
        task = rpc(
            app_client,
            "SendMessage",
            {"metadata": {"llm_mode": "stub"}, "message": msg},
        )["result"]["task"]
        hist = task["history"][0]
        assert hist["metadata"] == {"k": "v"}
        assert hist["extensions"] == ["urn:x"]
        assert hist["referenceTaskIds"] == ["t-9"]

    def test_artifact_parts_flat_on_get(self, app_client, clean_tasks):
        task_obj = Task(
            id="t-art-part",
            contextId="c-art",
            status=TaskStatus(state="completed"),
            history=[Message(role="user", parts=[{"type": "text", "text": "x"}])],
        )
        task_obj.artifacts.append(
            Artifact(parts=[{"type": "text", "text": "report"}], metadata={"m": 1})
        )
        clean_tasks.tasks[task_obj.id] = task_obj
        got = rpc(app_client, "GetTask", {"id": task_obj.id})["result"]
        ap = got["artifacts"][0]["parts"][0]
        assert ap == {"text": "report"}
        assert "type" not in ap
        assert got["artifacts"][0]["metadata"] == {"m": 1}
