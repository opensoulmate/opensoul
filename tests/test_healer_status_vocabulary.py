"""HEAL-②b-① 状态词表收口测面 — diagnose 识别 down 等非 ok 状态。

HEAL-② 审计③（live 硬证）：生产 vital 器官真报 {"status": "down"}（qdrant
Connection refused，HTTP 200）被诊断 healthy=True 零症状 = 真不健康器官被判
健康（「200 即健康」谎报）。收口后：状态词表单一源判定，down/error 等非健康
自报如实判不健康 + 症状 + severity，_recommend_action 链随之可达（修前
healthy=True 短路恒不进）。

修前（控制组实证）：status=="degraded" 之外的一切状态词被判 healthy=True
零症状、recommend 链零调用。

零回归契约锁死（test_healer_healthy_branch 同款逐字）：
  200 ok → healthy/INFO/零症状；200 degraded → WARNING + "service reports
  degraded status" + "component is running but degraded"；HTTP 500 → CRITICAL。
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.healer.diagnose import (
    _STATUS_HEALTHY,
    _STATUS_VERDICTS,
    Action,
    OrganHealer,
    Severity,
    _classify_status,
)

# ── 真 socket HTTP 桩（test_healer_healthy_branch 同款最小版） ──


class _StubState:
    def __init__(self):
        self.requests = []
        self.scripts = {}

    def script(self, method, path, response):
        self.scripts[(method, path)] = response


class _StubServer(ThreadingHTTPServer):
    stub_state: "_StubState"  # handler 经 self.server.stub_state 取记录器


class _StubHandler(BaseHTTPRequestHandler):
    server: _StubServer

    def log_message(self, *args):
        pass

    def do_GET(self):
        state = self.server.stub_state
        state.requests.append(("GET", self.path))
        step = state.scripts.get(("GET", self.path))
        if step is None:
            status, payload = 404, {"error": "no script"}
        else:
            status, payload = step.get("status", 200), step.get("json")
        data = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(status)
        if payload is not None:
            self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def healer_stub():
    server = _StubServer(("127.0.0.1", 0), _StubHandler)
    server.stub_state = _StubState()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield server.stub_state, base
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


# ── 词表纯函数 ─────────────────────────────────────────────────


class TestClassifyStatus:
    @pytest.mark.parametrize("word", ["ok", "healthy", "up", "OK", "Healthy", " UP ", "ok "])
    def test_healthy_words(self, word):
        assert _classify_status(word) is None

    def test_degraded_legacy_contract(self):
        v = _classify_status("degraded")
        assert v is not None
        assert v.severity == Severity.WARNING
        assert v.symptom == "service reports degraded status"  # 既有契约逐字
        assert v.root_cause == "component is running but degraded"  # 既有契约逐字

    @pytest.mark.parametrize("word", ["down", "error", "DOWN", "Error"])
    def test_down_words_critical(self, word):
        v = _classify_status(word)
        assert v is not None
        assert v.severity == Severity.CRITICAL
        assert v.symptom == f"service reports {word.lower()} status"
        assert v.root_cause == f"component reports {word.lower()} status"

    @pytest.mark.parametrize(
        "value", ["banana", "failed", "unhealthy", "skipped", "", None, 123, True]
    )
    def test_unrecognized_not_healthy_not_convicted(self, value):
        """识别不了的状态：如实不判健康（禁谎报），WARNING 不过度定罪 CRITICAL。"""
        v = _classify_status(value)
        assert v is not None  # 绝不判健康
        assert v.severity == Severity.WARNING
        assert v.symptom.startswith("service reports unrecognized status")

    def test_vocabulary_single_source_locked(self):
        """词表单一源锁死：健康词恰 {ok,healthy,up}、非健康词恰 {degraded,down,error}。"""
        assert _STATUS_HEALTHY == frozenset({"ok", "healthy", "up"})
        assert set(_STATUS_VERDICTS) == {"degraded", "down", "error"}


# ── diagnose 全链（真 socket 桩） ──────────────────────────────


class TestDiagnoseStatusVocabulary:
    def test_down_status_not_healthy(self, healer_stub):
        state, base = healer_stub
        body = {"status": "down", "components": [{"name": "qdrant", "status": "down"}]}
        state.script("GET", "/api/vital/health", {"status": 200, "json": body})
        h = OrganHealer(base_url=base)
        r = asyncio.run(h.diagnose("vital", "/api/vital/health"))
        assert r.healthy is False  # 修前 healthy=True（谎报健康）
        assert r.severity == Severity.CRITICAL
        assert r.symptoms == ["service reports down status"]
        assert r.root_cause == "component reports down status"
        assert r.detail == body  # 原始回包透传不变

    def test_recommend_chain_reachable_for_down(self, healer_stub):
        """recommend 链可达硬证：修前 healthy=True 短路，_recommend_action 恒 0 调用。

        注：状态类症状的推荐结果可能是 Action.NONE（策略词表对齐归 HEAL-②b-②），
        本拍只锁「链真被走到」不锁结果值。
        """
        state, base = healer_stub
        state.script("GET", "/api/vital/health", {"status": 200, "json": {"status": "down"}})
        h = OrganHealer(base_url=base)
        calls = []
        orig = h._recommend_action

        def spy(organ, result):
            calls.append((organ, tuple(result.symptoms)))
            return orig(organ, result)

        h._recommend_action = spy
        asyncio.run(h.diagnose("vital", "/api/vital/health"))
        assert calls == [("vital", ("service reports down status",))]

    def test_up_status_healthy_vital_vocabulary(self, healer_stub):
        """vital 健康自报是 "up" 而非 "ok"——词表收口零误伤。"""
        state, base = healer_stub
        state.script("GET", "/api/vital/health", {"status": 200, "json": {"status": "up"}})
        h = OrganHealer(base_url=base)
        r = asyncio.run(h.diagnose("vital", "/api/vital/health"))
        assert r.healthy is True
        assert r.severity == Severity.INFO
        assert r.symptoms == []
        assert r.recommended_action == Action.NONE

    def test_error_status_not_healthy(self, healer_stub):
        """C2 错误回包形态（200 带 status=error）如实判不健康。"""
        state, base = healer_stub
        state.script(
            "GET",
            "/api/vital/health",
            {"status": 200, "json": {"status": "error", "error": "db exploded"}},
        )
        h = OrganHealer(base_url=base)
        r = asyncio.run(h.diagnose("vital", "/api/vital/health"))
        assert r.healthy is False
        assert r.severity == Severity.CRITICAL
        assert r.symptoms == ["service reports error status"]

    def test_unrecognized_status_warning(self, healer_stub):
        state, base = healer_stub
        state.script("GET", "/api/vital/health", {"status": 200, "json": {"status": "banana"}})
        h = OrganHealer(base_url=base)
        r = asyncio.run(h.diagnose("vital", "/api/vital/health"))
        assert r.healthy is False
        assert r.severity == Severity.WARNING
        assert r.symptoms == ["service reports unrecognized status: banana"]

    def test_missing_status_key_default_ok(self, healer_stub):
        """缺失 status 键按 "ok" 缺省（既有契约不变）。"""
        state, base = healer_stub
        state.script("GET", "/api/vital/health", {"status": 200, "json": {"ts": 1}})
        h = OrganHealer(base_url=base)
        r = asyncio.run(h.diagnose("vital", "/api/vital/health"))
        assert r.healthy is True
        assert r.symptoms == []


# ── 既有契约零回归（逐字锁死） ─────────────────────────────────


class TestLegacyContracts:
    def test_200_ok_zero_change(self, healer_stub):
        state, base = healer_stub
        body = {"status": "ok", "component": "vein"}
        state.script("GET", "/api/vein/health", {"status": 200, "json": body})
        h = OrganHealer(base_url=base)
        r = asyncio.run(h.diagnose("vein", "/api/vein/health"))
        assert r.healthy is True
        assert r.severity == Severity.INFO
        assert r.symptoms == []
        assert r.detail == body
        assert r.recommended_action == Action.NONE

    def test_200_degraded_zero_change(self, healer_stub):
        state, base = healer_stub
        state.script("GET", "/api/reflex/health", {"status": 200, "json": {"status": "degraded"}})
        h = OrganHealer(base_url=base)
        r = asyncio.run(h.diagnose("reflex", "/api/reflex/health"))
        assert r.healthy is False
        assert r.severity == Severity.WARNING
        assert r.symptoms == ["service reports degraded status"]
        assert r.root_cause == "component is running but degraded"
        assert r.detail == {"status": "degraded"}

    def test_http_500_zero_change(self, healer_stub):
        state, base = healer_stub
        state.script("GET", "/api/pulse/health", {"status": 500, "json": {"error": "boom"}})
        h = OrganHealer(base_url=base)
        r = asyncio.run(h.diagnose("pulse", "/api/pulse/health"))
        assert r.healthy is False
        assert r.severity == Severity.CRITICAL
        assert r.symptoms == ["HTTP 500"]
        assert r.root_cause == "health endpoint returned 500"
        assert r.recommended_action == Action.NONE
