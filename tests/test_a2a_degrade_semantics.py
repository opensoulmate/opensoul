"""F4b-㉘ a2a 内层降级文本完成态语义收口——降级通知≠成功回复，禁 fabricated completed。

复核即切片本体（F4b-⑲ 诚实边界①留档件"内层三级降级文本（_handle_chat'AI服务超时…'/
_handle_acp_chat'AI服务暂时不可用…'）仍以 completed 回复——设计内降级…留档另拍可
复核"）。复核定性：降级文本内容是"服务故障通知+请检查配置"而非对用户问题的回答，
与 F4b-⑲ 已裁决的"AI处理超时…请稍后重试" fabricated completed 完全同型——同型同判
（F4b-㉗ 纪律）：任务没干成活不标 completed；failed 的 status.message 同样把通知送
达客户端（⑲ 先例）=UX 零损失、机器可读面如实。

收口：TaskServiceUnavailable（处理层信号，TaskInputRequired/TaskCancelled 同族）
→ run_processing 唯一归口 → failed，notice 进 status.message 不进 history
（F4b-⑧ 错误不入史）。同轴同判覆盖全部降级点（chat 三级链 + knowledge/search/
graph 出错降级 + search 双腿全失败=fabricated 空结果）。

不变面（锁死）：查询真跑空（"未找到…"）=真实回答恒 completed；llm_mode=stub 确定性
应答恒 completed；ACP 正常回复恒 completed。

真栈非 mock：真 handler 走真代码路径，仅上游 HTTP（httpx.AsyncClient）与检索服务
函数按故障注入（AsyncClient 级 seam=URL 分派假上游，AsyncClient 级 seam）。
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from src.a2a.api import router as a2a_router
from src.a2a.task_manager import TaskManager, TaskServiceUnavailable, task_manager


def _msg(text, **extra):
    m = {"role": "ROLE_USER", "parts": [{"text": text}], "messageId": f"dg-{text[:8]}"}
    m.update(extra)
    return m


def _rpc(method, params, rid="degrade-rpc"):
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}


async def _json_call(payload):
    """直驱 ASGI 走真 a2a router（tasks/send 非流式面）。"""
    app = FastAPI()
    app.include_router(a2a_router)
    body = json.dumps(payload).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/a2a",
        "raw_path": b"/a2a",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body))),
        ],
        "client": ("127.0.0.1", 50003),
        "server": ("testserver", 80),
    }
    state = {"sent": False}
    out = []

    async def receive():
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        if message["type"] == "http.response.body":
            out.append(message.get("body", b""))

    await app(scope, receive, send)
    raw = b"".join(out)
    assert raw, "empty JSON response body"
    return json.loads(raw)


async def _send(text, metadata=None):
    params = {"message": _msg(text)}
    if metadata:
        params["metadata"] = metadata
    env = await _json_call(_rpc("tasks/send", params))
    assert "result" in env, f"rpc error: {env}"
    return env["result"]


def _notice(task_dump):
    return task_dump["status"]["message"]["parts"][0]["text"]


def _hist_texts(task_dump):
    texts = []
    for m in task_dump.get("history", []):
        for p in m.get("parts", []):
            if isinstance(p, dict) and "text" in p:
                texts.append(p["text"])
    return texts


# ── 故障注入 seam：httpx.AsyncClient 按 URL 分派假上游 ──────────────────────────


class _FakeResp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}

    def json(self):
        return self._data


class _FakeHttpx:
    calls = []
    route = None  # async callable(url) -> _FakeResp 或 raise

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        _FakeHttpx.calls.append(url)
        return await _FakeHttpx.route(url)


@pytest.fixture()
def fake_httpx(monkeypatch):
    import httpx

    _FakeHttpx.calls = []
    _FakeHttpx.route = None
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttpx)
    yield _FakeHttpx
    _FakeHttpx.calls = []
    _FakeHttpx.route = None


def _set_llm(monkeypatch, key="test-key", base="http://llm-stub", model="m"):
    from src.config import settings

    monkeypatch.setattr(settings, "llm_api_key", key)
    monkeypatch.setattr(settings, "llm_base_url", base)
    monkeypatch.setattr(settings, "llm_model", model)


def _unset_llm(monkeypatch):
    _set_llm(monkeypatch, key="", base="")


def _patch_search(monkeypatch, **funcs):
    import src.services.search as search_mod

    for name, fn in funcs.items():
        monkeypatch.setattr(search_mod, name, fn)


def _patch_graph(monkeypatch, fn):
    import src.services.graph as graph_mod

    monkeypatch.setattr(graph_mod, "get_graph", fn)


# ── 降级收口：chat 三级链 ─────────────────────────────────────────────────────


class TestChatDegrade:
    @pytest.mark.asyncio
    async def test_llm_error_acp_empty_content_failed(self, monkeypatch, fake_httpx):
        """LLM 500 + ACP 空回复 → failed + 通知进 status.message 不入史（硬反证）。"""
        _set_llm(monkeypatch)

        async def route(url):
            if "/chat/completions" in url:
                return _FakeResp(500)
            return _FakeResp(200, {"content": ""})

        fake_httpx.route = route
        task = await _send("今天天气怎么样")
        assert task["status"]["state"] == "TASK_STATE_FAILED"
        assert "AI服务暂时不可用" in _notice(task)
        # 硬反证：修前降级文本当回复入史标 completed
        assert all("AI服务暂时不可用" not in t for t in _hist_texts(task))
        assert [m["role"] for m in task["history"]] == ["ROLE_USER"]

    @pytest.mark.asyncio
    async def test_llm_error_acp_exception_notice_keeps_cause(self, monkeypatch, fake_httpx):
        """ACP 异常 → failed，通知保留真因（⑲ 保真因纪律）。"""
        _set_llm(monkeypatch)

        async def route(url):
            if "/chat/completions" in url:
                raise OSError("llm-conn-refused")
            raise OSError("acp-conn-refused")

        fake_httpx.route = route
        task = await _send("今天天气怎么样")
        assert task["status"]["state"] == "TASK_STATE_FAILED"
        notice = _notice(task)
        assert "AI服务暂时不可用" in notice
        assert "acp-conn-refused" in notice

    @pytest.mark.asyncio
    async def test_llm_error_acp_timeout_failed(self, monkeypatch, fake_httpx):
        """三级降级全耗尽（LLM 失败→ACP 超时）→ failed，超时通知（预算短缩真计时）。"""
        _set_llm(monkeypatch)
        monkeypatch.setattr(TaskManager, "ACP_FALLBACK_BUDGET_S", 0.1)

        async def route(url):
            if "/chat/completions" in url:
                raise OSError("llm down")
            await asyncio.sleep(5)
            return _FakeResp(200, {"content": "never"})

        fake_httpx.route = route
        task = await _send("今天天气怎么样")
        assert task["status"]["state"] == "TASK_STATE_FAILED"
        assert _notice(task) == "AI服务超时，请检查服务配置。"

    @pytest.mark.asyncio
    async def test_no_llm_acp_degrade_single_attempt(self, monkeypatch, fake_httpx):
        """无 LLM 配置路径 + ACP 空回复 → failed 且 ACP 恰被调 1 次（禁同请求双打）。"""
        _unset_llm(monkeypatch)

        async def route(url):
            return _FakeResp(200, {"content": ""})

        fake_httpx.route = route
        task = await _send("今天天气怎么样")
        assert task["status"]["state"] == "TASK_STATE_FAILED"
        assert _notice(task) == "AI服务暂时不可用，请检查LLM配置或hermes服务。"
        acp_calls = [u for u in fake_httpx.calls if "/acp/send" in u]
        assert len(acp_calls) == 1, f"ACP fallback 双打: {acp_calls}"


# ── 同轴同判：knowledge / search / graph 降级点 ────────────────────────────────


class TestSkillDegrade:
    @pytest.mark.asyncio
    async def test_knowledge_timeout_failed(self, monkeypatch):
        async def boom(*args, **kwargs):
            raise TimeoutError("kb timeout")

        _patch_search(monkeypatch, hybrid_search=boom)
        task = await _send("知识库 森林防火")
        assert task["status"]["state"] == "TASK_STATE_FAILED"
        assert "知识库查询超时" in _notice(task)
        assert all("知识库查询超时" not in t for t in _hist_texts(task))

    @pytest.mark.asyncio
    async def test_knowledge_error_failed_with_cause(self, monkeypatch):
        async def boom(*args, **kwargs):
            raise RuntimeError("db down")

        _patch_search(monkeypatch, hybrid_search=boom)
        task = await _send("知识库 森林防火")
        assert task["status"]["state"] == "TASK_STATE_FAILED"
        notice = _notice(task)
        assert "知识库查询出错" in notice
        assert "db down" in notice

    @pytest.mark.asyncio
    async def test_search_both_legs_failed_no_fabricated_empty(self, monkeypatch):
        """双腿全失败=查询没跑成 → failed（修前 fabricate"未找到匹配结果"标 completed）。"""

        async def boom_sem(*args, **kwargs):
            raise RuntimeError("sem down")

        async def boom_ft(*args, **kwargs):
            raise RuntimeError("ft down")

        _patch_search(monkeypatch, semantic_search=boom_sem, fulltext_search=boom_ft)
        task = await _send("搜索 森林防火")
        assert task["status"]["state"] == "TASK_STATE_FAILED"
        notice = _notice(task)
        assert "均失败" in notice
        assert "sem down" in notice and "ft down" in notice
        assert all("未找到匹配结果" not in t for t in _hist_texts(task))

    @pytest.mark.asyncio
    async def test_graph_timeout_failed(self, monkeypatch):
        async def boom(*args, **kwargs):
            raise TimeoutError("graph timeout")

        _patch_graph(monkeypatch, boom)
        task = await _send("查看知识图谱")
        assert task["status"]["state"] == "TASK_STATE_FAILED"
        assert "图谱查询超时" in _notice(task)

    @pytest.mark.asyncio
    async def test_graph_error_failed_with_cause(self, monkeypatch):
        async def boom(*args, **kwargs):
            raise RuntimeError("graph db down")

        _patch_graph(monkeypatch, boom)
        task = await _send("查看知识图谱")
        assert task["status"]["state"] == "TASK_STATE_FAILED"
        notice = _notice(task)
        assert "图谱查询出错" in notice
        assert "graph db down" in notice


# ── 不变面（锁死）：真实回答恒 completed ──────────────────────────────────────


class TestUnchangedFaces:
    @pytest.mark.asyncio
    async def test_knowledge_empty_result_completed(self, monkeypatch):
        async def empty(*args, **kwargs):
            return []

        _patch_search(monkeypatch, hybrid_search=empty)
        task = await _send("知识库 森林防火")
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert any("未找到相关知识条目" in t for t in _hist_texts(task))

    @pytest.mark.asyncio
    async def test_search_single_leg_failure_empty_completed(self, monkeypatch):
        """单腿失败+单腿真跑空→"未找到"是真实回答，completed 不变（宁窄勿滥边界）。"""

        async def boom_sem(*args, **kwargs):
            raise RuntimeError("sem down")

        async def empty_ft(*args, **kwargs):
            return []

        _patch_search(monkeypatch, semantic_search=boom_sem, fulltext_search=empty_ft)
        task = await _send("搜索 森林防火")
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert any("未找到匹配结果" in t for t in _hist_texts(task))

    @pytest.mark.asyncio
    async def test_graph_empty_completed(self, monkeypatch):
        async def empty(*args, **kwargs):
            return SimpleNamespace(nodes=[], edges=[])

        _patch_graph(monkeypatch, empty)
        task = await _send("查看知识图谱")
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert any("图谱为空" in t for t in _hist_texts(task))

    @pytest.mark.asyncio
    async def test_stub_mode_completed(self):
        """llm_mode=stub 确定性应答非降级，恒 completed。"""
        task = await _send("今天天气怎么样", metadata={"llm_mode": "stub"})
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert any("[stub-llm]" in t for t in _hist_texts(task))

    @pytest.mark.asyncio
    async def test_acp_normal_reply_completed(self, monkeypatch, fake_httpx):
        """无 LLM 配置 + ACP 正常回复 → completed（兜底链成功面零变化）。"""
        _unset_llm(monkeypatch)

        async def route(url):
            return _FakeResp(200, {"content": "真实回答内容"})

        fake_httpx.route = route
        task = await _send("今天天气怎么样")
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert any("真实回答内容" in t for t in _hist_texts(task))


# ── 信号单元 + 真源一致性 ────────────────────────────────────────────────────


class TestSignalAndTruth:
    def test_signal_carries_notice(self):
        e = TaskServiceUnavailable("服务不可用通知")
        assert e.notice == "服务不可用通知"
        assert str(e) == "服务不可用通知"

    @pytest.mark.asyncio
    async def test_degrade_truth_consistent_no_artifacts(self, monkeypatch, fake_httpx):
        """降级任务 GetTask 真源一致 + 零 artifact（无痕可撤，发块前失败路径零变化）。"""
        _set_llm(monkeypatch)

        async def route(url):
            if "/chat/completions" in url:
                return _FakeResp(500)
            return _FakeResp(200, {"content": ""})

        fake_httpx.route = route
        task = await _send("今天天气怎么样")
        env = await _json_call(_rpc("tasks/get", {"id": task["id"]}))
        truth = env["result"]
        assert truth["status"]["state"] == "TASK_STATE_FAILED"
        assert _notice(truth) == _notice(task)
        assert truth.get("artifacts", []) == []
        assert [m["role"] for m in truth["history"]] == ["ROLE_USER"]
