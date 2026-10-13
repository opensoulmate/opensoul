"""HEAL-②b-② 策略触发词表对齐测面——0/6 可达 → 6/6 真链可达 + restart 假承诺收口。

HEAL-② 审计①：_REPAIR_STRATEGIES 修前 trigger 是自由文本模式（"cache miss or
expired" 等器官内部状态描述），与 diagnose() 产生面词表零交集=0/6 可达（推荐与
修复双双走不到）；连带死路 (b) connection refused 推荐 RESTART 而 heal()/全仓
无 restart 执行路径=推荐即死、(c) "slow response" 推荐分支只在 healthy=True 产生
面出现而 recommend 只跑不健康路径=死分支。

本文件用真 socket HTTP 桩（ThreadingHTTPServer 真监听端口 + 请求记录器 + 可编程
响应序列）从 diagnose() 真产生面驱动全链：症状产生 → 触发归类 → 推荐 → heal()
真执行修复端点，6/6 策略逐条可达（修前 0/6）。零生产接触（不打 :8090）。
"""

import asyncio
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.healer.diagnose import (
    _REPAIR_STRATEGIES,
    _STATUS_VERDICTS,
    Action,
    DiagnosisResult,
    OrganHealer,
    Severity,
    _symptom_kind,
)

# ── 真 socket HTTP 桩（与 test_healer_healthy_branch 同款，独立副本防跨文件耦合）──


class _StubState:
    def __init__(self):
        self.requests = []
        self.scripts = {}
        self.lock = threading.Lock()

    def script(self, method, path, *responses):
        self.scripts[(method, path)] = list(responses)

    def recorded(self, method=None, path=None):
        with self.lock:
            return [
                (m, p, b)
                for (m, p, b) in self.requests
                if (method is None or m == method) and (path is None or p == path)
            ]


class _StubServer(ThreadingHTTPServer):
    stub_state = None


class _StubHandler(BaseHTTPRequestHandler):
    server: _StubServer

    def log_message(self, *args):
        pass

    def _handle(self, method):
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        state = self.server.stub_state
        with state.lock:
            state.requests.append((method, self.path, raw))
            seq = state.scripts.get((method, self.path))
            step = seq.pop(0) if seq and len(seq) > 1 else (seq[0] if seq else None)
        if step is None:
            status, payload = 404, {"error": "no script"}
        else:
            if step.get("sleep"):
                time.sleep(step["sleep"])
            status, payload = step.get("status", 200), step.get("json")
        data = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(status)
        if payload is not None:
            self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def _stub():
    server = _StubServer(("127.0.0.1", 0), _StubHandler)
    server.stub_state = _StubState()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.stub_state, f"http://127.0.0.1:{server.server_address[1]}"


def _dead_port_base():
    """真 connection refused：bind 后立即释放的端口无人监听。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


# ── 触发词表单一源 / 真源一致 ─────────────────────────────────


class TestSymptomKinds:
    def test_status_kinds_derived_from_verdicts(self):
        """状态类归类 == 状态词本身（_STATUS_VERDICTS 反查派生，禁手抄字面）。"""
        for word, verdict in _STATUS_VERDICTS.items():
            assert _symptom_kind(verdict.symptom) == word
        assert _symptom_kind("service reports unrecognized status: banana") == "unrecognized"

    def test_transport_kinds(self):
        assert _symptom_kind("HTTP 500") == "http error"
        assert _symptom_kind("HTTP 503") == "http error"
        assert _symptom_kind("timeout") == "timeout"
        assert _symptom_kind("connection refused") == "connection refused"
        assert _symptom_kind("exception: ValueError") == "exception"
        assert _symptom_kind("slow response: 8000.0ms") == "slow response"

    def test_free_text_not_hard_matched(self):
        """修前自由文本模式不硬凑归类（旧词表已死，禁复活第二词表）。"""
        for s in (
            "cache miss or expired",
            "expired entries",
            "cache full or degraded",
            "expired sandbox",
            "expired session",
            "audit log full",
            "something else entirely",
        ):
            assert _symptom_kind(s) is None

    def test_every_trigger_producible(self):
        """防漂移锁死：每条策略触发类都能由产生面词汇归类得出。"""
        producible = set(_STATUS_VERDICTS) | {
            "timeout",
            "connection refused",
            "http error",
            "exception",
            "slow response",
            "unrecognized",
        }
        total = 0
        for organ, strategies in _REPAIR_STRATEGIES.items():
            for strategy in strategies:
                assert set(strategy["triggers"]) <= producible, (organ, strategy)
                total += 1
        assert total == 6  # 策略条数=审计清单


# ── 6/6 策略真链可达（diagnose 真产生面 → 推荐 → heal 真执行） ──


class TestStrategyReachability:
    def test_degraded_full_chain_all_entries(self):
        """degraded 触发的 5 条策略：真 200 degraded 产生面 → 推荐 → 真 POST 修复端点。"""
        cases = [
            ("vein", "/api/vein/health", Action.CLEANUP, "/api/vein/cache/cleanup"),
            ("reflex", "/api/reflex/health", Action.CLEAR_CACHE, "/api/reflex/cache/clear"),
            ("mirror", "/api/mirror/health", Action.CLEANUP, "/api/mirror/cleanup"),
            ("hippo", "/api/hippo/health", Action.CLEANUP, "/api/hippo/sessions/cleanup"),
            ("immune", "/api/immune/health", Action.CLEANUP, "/api/immune/audit/cleanup"),
        ]
        for organ, health, expected_action, repair_path in cases:
            server, state, base = _stub()
            try:
                state.script("GET", health, {"status": 200, "json": {"status": "degraded"}})
                state.script("POST", repair_path, {"status": 200, "json": {"status": "ok"}})
                h = OrganHealer(base_url=base)
                r = asyncio.run(h.diagnose(organ, health))
                assert r.healthy is False
                assert r.symptoms == ["service reports degraded status"]
                assert r.recommended_action == expected_action, organ
                out = asyncio.run(h.heal(r))
                assert out.action_taken == expected_action
                assert out.action_success is True
                assert out.severity.value == "recovered"
                kinds = [(m, p) for m, p, _ in state.recorded()]
                assert kinds == [("GET", health), ("POST", repair_path)], (organ, kinds)
            finally:
                server.shutdown()
                server.server_close()

    def test_timeout_full_chain_vein(self):
        """timeout 触发的 vein 首条策略：真 10s 超时产生面（不缩时钟）→ 真 POST 清缓存。"""
        server, state, base = _stub()
        try:
            state.script(
                "GET",
                "/api/vein/health",
                {"status": 200, "json": {"status": "ok"}, "sleep": 11.0},
            )
            state.script("POST", "/api/vein/cache/clear", {"status": 200, "json": {"ok": True}})
            h = OrganHealer(base_url=base)
            r = asyncio.run(h.diagnose("vein", "/api/vein/health"))
            assert r.healthy is False
            assert r.symptoms == ["timeout"]
            assert r.recommended_action == Action.CLEAR_CACHE
            out = asyncio.run(h.heal(r))
            assert out.action_taken == Action.CLEAR_CACHE
            assert out.action_success is True
            kinds = [(m, p) for m, p, _ in state.recorded()]
            assert kinds == [("GET", "/api/vein/health"), ("POST", "/api/vein/cache/clear")]
        finally:
            server.shutdown()
            server.server_close()

    def test_no_shadowing_vein_both_strategies(self):
        """vein 两策略按症状类分工，首匹配不遮蔽（两条各自真执行自己的端点）。"""
        server, state, base = _stub()
        try:
            state.script("GET", "/api/vein/health", {"status": 200, "json": {"status": "degraded"}})
            state.script("POST", "/api/vein/cache/cleanup", {"status": 200, "json": {"ok": True}})
            h = OrganHealer(base_url=base)
            r = asyncio.run(h.diagnose("vein", "/api/vein/health"))
            out = asyncio.run(h.heal(r))
            assert out.action_taken == Action.CLEANUP
            assert ("POST", "/api/vein/cache/cleanup") in [(m, p) for m, p, _ in state.recorded()]
            assert not state.recorded("POST", "/api/vein/cache/clear")
        finally:
            server.shutdown()
            server.server_close()


# ── 宁缺勿滥：非对症症状零投机修复 ─────────────────────────────


class TestNoSpeculativeRepair:
    def test_non_trigger_symptoms_none(self):
        """down/error/unrecognized/HTTP/exception 对 playbook 器官不触发维护动作。"""
        symptoms = [
            "service reports down status",
            "service reports error status",
            "service reports unrecognized status: banana",
            "HTTP 500",
            "exception: ValueError",
        ]
        for organ in _REPAIR_STRATEGIES:
            for symptom in symptoms:
                r = DiagnosisResult(
                    organ=organ,
                    healthy=False,
                    severity=Severity.CRITICAL,
                    symptoms=[symptom],
                    root_cause="crafted",
                )
                h = OrganHealer(base_url="http://127.0.0.1:1")
                assert h._recommend_action(organ, r) == Action.NONE, (organ, symptom)
                out = asyncio.run(h.heal(r))
                assert out.action_taken == Action.NONE

    def test_healthy_none(self):
        h = OrganHealer(base_url="http://127.0.0.1:1")
        r = DiagnosisResult(organ="vein", healthy=True, severity=Severity.INFO)
        assert h._recommend_action("vein", r) == Action.NONE


# ── restart 假承诺收口（死路 b）──────────────────────────────


class TestRestartDerivation:
    def test_connection_refused_no_false_restart(self):
        """修前恒推荐 RESTART（heal()/全仓无 restart 执行路径=假承诺）；现如实 NONE。"""
        h = OrganHealer(base_url=_dead_port_base())
        r = asyncio.run(h.diagnose("vein", "/api/vein/health"))
        assert r.healthy is False
        assert r.symptoms == ["connection refused"]
        assert r.recommended_action == Action.NONE  # 修前 Action.RESTART
        out = asyncio.run(h.heal(r))
        assert out.action_taken == Action.NONE
        assert out.action_success is False

    def test_connection_refused_restart_when_handler_registered(self):
        """RESTART 仅在有自定义处理器（restart 的真实实现）时推荐——声明从实现派生。"""
        h = OrganHealer(base_url=_dead_port_base())
        called = []

        async def _restart():
            called.append(True)
            return True

        h.register_handler("vein", _restart)
        r = asyncio.run(h.diagnose("vein", "/api/vein/health"))
        assert r.recommended_action == Action.RESTART
        out = asyncio.run(h.heal(r))
        assert called == [True]  # 处理器真执行
        assert out.action_taken == Action.CUSTOM
        assert out.action_success is True
        assert out.severity.value == "recovered"


# ── 零回归面 + 死分支 (c) 清除锁死 ────────────────────────────


class TestRegressions:
    def test_generic_timeout_non_playbook_unchanged(self):
        """无 playbook 器官 timeout → generic 清缓存（既有生产可达链零变化）。"""
        server, state, base = _stub()
        try:
            state.script("POST", "/api/pulse/cache/clear", {"status": 200, "json": {"ok": True}})
            h = OrganHealer(base_url=base)
            r = DiagnosisResult(
                organ="pulse",
                healthy=False,
                severity=Severity.WARNING,
                symptoms=["timeout"],
                root_cause="crafted",
            )
            assert h._recommend_action("pulse", r) == Action.CLEAR_CACHE
            r.recommended_action = Action.CLEAR_CACHE  # diagnose() 真链同款落推荐
            out = asyncio.run(h.heal(r))
            assert out.action_taken == Action.CLEAR_CACHE
            assert out.action_success is True
            assert [(m, p) for m, p, _ in state.recorded()] == [("POST", "/api/pulse/cache/clear")]
        finally:
            server.shutdown()
            server.server_close()

    def test_slow_response_recommendation_branch_removed(self):
        """死分支 (c) 清除锁死："slow response" 只在 healthy=True 产生面出现，
        修前 recommend 的 "slow" 分支不可达；现如实无推荐，禁复活死分支。"""
        r = DiagnosisResult(
            organ="vein",
            healthy=False,
            severity=Severity.WARNING,
            symptoms=["slow response: 8000.0ms"],
            root_cause="crafted",
        )
        h = OrganHealer(base_url="http://127.0.0.1:1")
        assert h._recommend_action("vein", r) == Action.NONE

    def test_produce_side_strings_unchanged(self):
        """产生串字节零变化（常量收编非改词）：状态面 + 传输面全覆盖。"""
        server, state, base = _stub()
        try:
            state.script("GET", "/api/vital/health", {"status": 200, "json": {"status": "down"}})
            h = OrganHealer(base_url=base)
            r = asyncio.run(h.diagnose("vital", "/api/vital/health"))
            assert r.symptoms == ["service reports down status"]
            assert _symptom_kind(r.symptoms[0]) == "down"
        finally:
            server.shutdown()
            server.server_close()
