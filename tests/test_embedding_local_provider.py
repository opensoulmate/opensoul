"""MEM-② embedding provider 本地开源接入——ollama 路径真消费 + openai 路径
字节零变化 + 失败诚实契约。

真 socket 桩（真 httpx 栈，双形态 /api/embed 与 /embeddings），tmp 全隔离，
绝不打真模型/真网络（conftest _isolate_embedding_provider 钉 hermetic 缺省，
本文件按需 monkeypatch 覆盖）。
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.config import settings


def _vec(text: str) -> list:
    t = text.lower()
    if "爬山" in t or "hike" in t:
        return [1.0, 0.0, 0.0]
    if "半" in t:
        return [0.7, 0.7, 0.0]
    return [0.0, 1.0, 0.0]


def _set_provider(monkeypatch, value):
    """monkeypatch provider（控制组容缺：旧代码无该字段时跳过——结构性缺席由
    结构断言咬合，行为断言继续跑，「旧代码+容缺跑同测试」先例）。"""
    try:
        monkeypatch.setattr(settings, "embedding_provider", value)
    except (AttributeError, ValueError):
        pass


class _EmbedStub:
    """真 HTTP 桩：shape="ollama" 回 /api/embed 形 {"embeddings":[...]}；
    shape="openai" 回 {"data":[{"index","embedding"}]}。mode：
    ok / bad_shape（少回一条向量）/ http500。记录 (path, headers, payload)。"""

    def __init__(self, shape="ollama", mode="ok"):
        self.shape = shape
        self.mode = mode
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append((self.path, dict(self.headers), payload))
                if outer.mode == "http500":
                    body = b'{"error":"boom"}'
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                texts = payload.get("input", [])
                if isinstance(texts, str):
                    texts = [texts]
                if outer.shape == "ollama":
                    vecs = [_vec(t) for t in texts]
                    if outer.mode == "bad_shape":
                        vecs = vecs[:-1]  # 少回一条=形状不符
                    body = json.dumps({"embeddings": vecs}).encode()
                else:
                    data = [{"index": i, "embedding": _vec(t)} for i, t in enumerate(texts)]
                    body = json.dumps({"data": data}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # 静音
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def close(self):
        self._server.shutdown()
        self._server.server_close()


def _stub(shape="ollama", mode="ok", provider=None, api_key="", model="bge-m3"):
    @pytest.fixture()
    def fixture(monkeypatch):
        stub = _EmbedStub(shape=shape, mode=mode)
        monkeypatch.setattr(settings, "embedding_base_url", stub.base_url)
        monkeypatch.setattr(settings, "embedding_api_key", api_key)
        monkeypatch.setattr(settings, "embedding_model", model)
        if provider is not None:
            _set_provider(monkeypatch, provider)
        yield stub
        stub.close()

    return fixture


ollama_stub = _stub(shape="ollama", provider="ollama")
openai_stub = _stub(
    shape="openai", provider="openai", api_key="test-key", model="text-embedding-3-small"
)
ollama_bad_shape = _stub(shape="ollama", mode="bad_shape", provider="ollama")
ollama_500 = _stub(shape="ollama", mode="http500", provider="ollama")


# ── ollama provider 真消费 ──────────────────────────────────────────


class TestOllamaProvider:
    def test_single_embedding_hits_api_embed(self, ollama_stub):
        from src.services.embedding import get_embedding

        vec = asyncio.run(get_embedding("周末计划去爬山"))
        assert vec == [1.0, 0.0, 0.0]
        assert len(ollama_stub.requests) == 1
        path, headers, payload = ollama_stub.requests[0]
        assert path == "/api/embed"
        assert set(payload.keys()) == {"model", "input"}
        assert payload["model"] == "bge-m3"
        assert payload["input"] == ["周末计划去爬山"]
        # 本地开源模型零凭据：openai 路径的 Authorization 头不得带进来
        assert "authorization" not in {k.lower() for k in headers}

    def test_batch_without_api_key_order_preserved(self, ollama_stub):
        """本地 provider 无 key 也工作（修前 batch gate「无 key 直接空向量」
        会把本地模型静默饿死=声明可用实际永不工作）。"""
        from src.services.embedding import get_embeddings_batch

        out = asyncio.run(get_embeddings_batch(["周末计划去爬山", "复习概率论"]))
        assert out == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        assert len(ollama_stub.requests) == 1
        path, _headers, payload = ollama_stub.requests[0]
        assert path == "/api/embed"
        assert payload["input"] == ["周末计划去爬山", "复习概率论"]

    def test_batch_degrades_to_empty_without_network_for_unknown_texts(self, ollama_stub):
        from src.services.embedding import get_embeddings_batch

        out = asyncio.run(get_embeddings_batch([]))
        assert out == []
        assert ollama_stub.requests == []  # 空输入零触达


class TestOpenAIPathZeroChange:
    def test_request_shape_byte_zero_change(self, openai_stub):
        """既有 openai 路径契约锁死：/embeddings + payload 恰三键 + Bearer。"""
        from src.services.embedding import get_embeddings_batch

        out = asyncio.run(get_embeddings_batch(["爬山"]))
        assert out == [[1.0, 0.0, 0.0]]
        path, headers, payload = openai_stub.requests[0]
        assert path == "/embeddings"
        assert set(payload.keys()) == {"input", "model", "dimensions"}
        assert payload["model"] == "text-embedding-3-small"
        auth = {k.lower(): v for k, v in headers.items()}.get("authorization")
        assert auth == "Bearer test-key"

    def test_no_key_batch_empty_zero_network(self, openai_stub, monkeypatch):
        from src.services import embedding as emb

        monkeypatch.setattr(settings, "embedding_api_key", "")
        monkeypatch.setattr(settings, "llm_api_key", "")  # _get_api_key 回落源也清空
        out = asyncio.run(emb.get_embeddings_batch(["爬山", "x"]))
        assert out == [[], []]
        assert openai_stub.requests == []  # 既有契约：无 key 零触达


# ── 失败诚实契约（batch 永不 raise；single raise→调用方 503） ────────


class TestOllamaHonestFailure:
    def test_bad_shape_single_raises_not_fabricate(self, ollama_bad_shape):
        from src.services.embedding import get_embedding

        with pytest.raises(RuntimeError, match="unexpected response"):
            asyncio.run(get_embedding("爬山"))

    def test_bad_shape_batch_degrades_never_raises(self, ollama_bad_shape):
        from src.services.embedding import get_embeddings_batch

        out = asyncio.run(get_embeddings_batch(["爬山", "复习概率论"]))
        assert out == [[], []]  # 禁 fabricate 向量、禁静默截断对齐

    def test_http500_single_raises_after_retries(self, ollama_500, monkeypatch):
        from src.services import embedding as emb

        monkeypatch.setattr(emb, "RETRY_BASE_DELAY", 0)
        with pytest.raises(RuntimeError, match="failed after 3 retries"):
            asyncio.run(emb.get_embedding("爬山"))
        assert len(ollama_500.requests) == 3  # 真重试

    def test_http500_batch_degrades_with_visible_warning(self, ollama_500, monkeypatch, caplog):
        from src.services import embedding as emb

        monkeypatch.setattr(emb, "RETRY_BASE_DELAY", 0)
        with caplog.at_level("WARNING"):
            out = asyncio.run(emb.get_embeddings_batch(["爬山"]))
        assert out == [[]]
        assert any("degraded to empty vectors" in r.message for r in caplog.records)


class TestUnknownProvider:
    def test_single_raises_value_error(self, monkeypatch):
        from src.services.embedding import get_embedding

        _set_provider(monkeypatch, "wat")
        with pytest.raises(ValueError, match="unsupported embedding provider"):
            asyncio.run(get_embedding("爬山"))

    def test_batch_degrades_never_raises_with_warning(self, monkeypatch, caplog):
        from src.services.embedding import get_embeddings_batch

        _set_provider(monkeypatch, "wat")
        with caplog.at_level("WARNING"):
            out = asyncio.run(get_embeddings_batch(["爬山"]))
        assert out == [[]]
        assert any("unsupported embedding provider" in r.message for r in caplog.records)


class TestBudgetDispatch:
    def test_total_budget_per_provider(self, monkeypatch):
        from src.services import embedding as emb

        _set_provider(monkeypatch, "ollama")
        assert emb._total_budget() == emb.OLLAMA_TOTAL_BUDGET
        _set_provider(monkeypatch, "openai")
        assert emb._total_budget() == emb.EMBEDDING_TOTAL_BUDGET
        # 本地 CPU 推理预算必须宽于托管口径（否则批量回填=假降级）
        assert emb.OLLAMA_TOTAL_BUDGET > emb.EMBEDDING_TOTAL_BUDGET

    def test_provider_normalizes_case(self, monkeypatch):
        from src.services import embedding as emb

        _set_provider(monkeypatch, "  OLLAMA ")
        assert emb._provider() == "ollama"


# ── /recall 语义面经本地 provider 端到端真跑（semantic_rank 同链） ────


class TestSemanticRankLocalProvider:
    def test_semantic_rank_via_ollama(self, ollama_stub):
        from src.hippo.memory_semantic import semantic_rank
        from src.hippo.memory_store import MemoryStore

        st = MemoryStore()
        st.add(session_id="s", content="周末计划去爬山")
        st.add(session_id="s", content="复习概率论")
        rows = st.search(limit=100)
        rank = asyncio.run(semantic_rank("爬山", rows, store=st))
        assert rank.degraded is False
        assert [m.content for m, _ in rank.ranked] == ["周末计划去爬山", "复习概率论"]
        assert (rank.candidates, rank.embedded, rank.unranked) == (2, 2, 0)
        # 行向量懒算缓存真落（store 真源）
        assert all(m.embedding for m in st.search(limit=100))

    def test_semantic_rank_degraded_on_local_failure(self, ollama_500, monkeypatch):
        from src.hippo.memory_semantic import semantic_rank
        from src.hippo.memory_store import MemoryStore
        from src.services import embedding as emb

        monkeypatch.setattr(emb, "RETRY_BASE_DELAY", 0)
        st = MemoryStore()
        st.add(session_id="s", content="周末计划去爬山")
        rank = asyncio.run(semantic_rank("爬山", st.search(limit=100), store=st))
        assert rank.degraded is True
        assert rank.reason  # 真因可见（调用方 503 带真因）


# ── 静态接线（写了≠接线了） ─────────────────────────────────────────


class TestStaticWiring:
    def test_dispatch_consumed_by_both_call_sites(self):
        src = open("src/services/embedding.py", encoding="utf-8").read()
        assert "_call_embed(client, [text])" in src  # get_embedding 真派发
        assert "_call_embed(client, batch)" in src  # _embed_all 真派发
        assert "_call_ollama_api(client, texts)" in src

    def test_config_provider_field(self):
        src = open("src/config.py", encoding="utf-8").read()
        assert 'embedding_provider: str = "openai"' in src
        assert hasattr(settings, "embedding_provider")
