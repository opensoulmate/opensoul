"""healer 健康分支测面 — 真 socket 桩覆盖「目标真响应」的成功分支。

TL-②-⑥ 留档候选「healer 健康分支测面（可另拍）」：healer_client fixture
（OrganHealer(base_url=死端口)）只走真实失败分支——探测恒 ConnectError、
修复/通知/审计 POST 死端口失败被既有 try/except 如实吞；目标真响应的健康
分支此前零测面：探测 200 ok/degraded、HTTP 非 200、修复真执行成功→RECOVERED、
notify 真投递成功、audit 真落审计、diagnose_and_heal 修复后复查。

本文件用真 socket HTTP 桩（ThreadingHTTPServer 真监听端口 + 请求记录器 +
可编程响应序列）让这些分支真实执行 HTTP 往返，零生产接触（不打 :8090、
timeline 事件面走 TIMELINE_DB autouse 隔离）。

发现留档（如实记，修复需拍板）：_REPAIR_STRATEGIES 的 symptom 模式
（"cache miss or expired" 等）与 diagnose 产出的症状串（"connection refused"
/"timeout"/"HTTP {code}"/"service reports degraded status"/"slow response: …"）
互不为子串——_matches_symptom 为「症状串包含模式」方向，故内置修复策略从
diagnose→_recommend_action→heal 生产链上不可达（wired-but-not-functional，
F4b-㉒ 同型）；生产可达的修复动作只剩超时症状→generic _try_generic_clear_cache
（本文件 /cycle、/heal e2e 走的就是这条真链）。策略真执行分支在 heal() 公共缝
上以显式症状结果覆盖（见 TestHealRepairStrategies 注释）。
"""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.healer.diagnose import Action, DiagnosisResult, OrganHealer, Severity

# ── 真 socket HTTP 桩 ─────────────────────────────────────────
# ThreadingHTTPServer 真监听 127.0.0.1:0，请求记录器记 (method, path, body)，
# 响应按 (method, path) 的脚本序列出（末条重复），支持 sleep 模拟慢响应。


class _StubState:
    def __init__(self):
        self.requests: list[tuple[str, str, bytes]] = []
        self.scripts: dict[tuple[str, str], list[dict]] = {}
        self.lock = threading.Lock()

    def script(self, method, path, *responses):
        """注册响应序列：{"status": 200, "json": {...}, "sleep": 11.0}。"""
        self.scripts[(method, path)] = list(responses)

    def recorded(self, method=None, path=None):
        with self.lock:
            return [
                (m, p, b)
                for (m, p, b) in self.requests
                if (method is None or m == method) and (path is None or p == path)
            ]


class _StubServer(ThreadingHTTPServer):
    stub_state: "_StubState"  # handler 经 self.server.stub_state 取记录器


class _StubHandler(BaseHTTPRequestHandler):
    server: _StubServer

    def log_message(self, *args):  # 静音
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


def _healer(base):
    return OrganHealer(base_url=base)


def _unhealthy(organ, symptoms, action, severity=Severity.CRITICAL):
    """显式症状结果：覆盖 heal() 公共缝上 diagnose 产不出的症状形态（见模块注释）。"""
    return DiagnosisResult(
        organ=organ,
        healthy=False,
        severity=severity,
        symptoms=list(symptoms),
        root_cause="crafted for heal() seam coverage",
        recommended_action=action,
    )


# ── 探测成功分支（目标真响应） ─────────────────────────────────


class TestDiagnoseResponding:
    def test_healthy_200_ok(self, healer_stub):
        state, base = healer_stub
        state.script(
            "GET",
            "/api/vein/health",
            {"status": 200, "json": {"status": "ok", "component": "vein"}},
        )
        h = _healer(base)
        r = asyncio.run(h.diagnose("vein", "/api/vein/health"))
        assert r.healthy is True
        assert r.severity == Severity.INFO
        assert r.detail == {"status": "ok", "component": "vein"}
        assert r.recommended_action == Action.NONE
        assert r.response_time_ms >= 0
        assert r.symptoms == []
        # 历史真落内存实例
        assert len(h.history) == 1 and h.history[0]["healthy"] is True
        assert h.stats()["recent_healthy_rate"] == 100.0
        # 真 HTTP 往返：探针 GET 真达桩
        assert [(m, p) for m, p, _ in state.recorded()] == [("GET", "/api/vein/health")]

    def test_degraded_200_not_healthy(self, healer_stub):
        state, base = healer_stub
        state.script("GET", "/api/reflex/health", {"status": 200, "json": {"status": "degraded"}})
        r = asyncio.run(_healer(base).diagnose("reflex", "/api/reflex/health"))
        assert r.healthy is False  # 200 但 degraded=不健康（既有语义）
        assert r.severity == Severity.WARNING
        assert r.symptoms == ["service reports degraded status"]
        assert r.root_cause == "component is running but degraded"
        assert r.detail == {"status": "degraded"}

    def test_http_500_critical(self, healer_stub):
        state, base = healer_stub
        state.script("GET", "/api/pulse/health", {"status": 500, "json": {"error": "boom"}})
        r = asyncio.run(_healer(base).diagnose("pulse", "/api/pulse/health"))
        assert r.healthy is False
        assert r.severity == Severity.CRITICAL
        assert r.symptoms == ["HTTP 500"]
        assert r.root_cause == "health endpoint returned 500"
        assert r.detail == {"status_code": 500}
        assert r.recommended_action == Action.NONE  # 无 timeout/slow/connection 症状


# ── heal 成功/失败分支（修复真执行） ────────────────────────────


class TestHealBranches:
    def test_healthy_result_noop(self, healer_stub):
        state, base = healer_stub
        h = _healer(base)
        r = DiagnosisResult(organ="vein", healthy=True, severity=Severity.INFO)
        out = asyncio.run(h.heal(r))
        assert out.action_taken == Action.NONE
        assert out.action_success is False
        assert out.severity == Severity.INFO
        assert state.recorded() == []  # 健康结果零请求

    def test_custom_handler_success_recovered(self, healer_stub):
        state, base = healer_stub
        h = _healer(base)

        async def _ok():
            return True

        h.register_handler("cortex", _ok)
        r = _unhealthy("cortex", ["connection refused"], Action.RESTART)
        out = asyncio.run(h.heal(r))
        assert out.action_taken == Action.CUSTOM
        assert out.action_success is True
        assert out.severity == Severity.RECOVERED
        assert state.recorded() == []  # 自定义 handler 不走 HTTP

    def test_custom_handler_false_not_recovered(self, healer_stub):
        state, base = healer_stub
        h = _healer(base)

        async def _fail():
            return False

        h.register_handler("cortex", _fail)
        r = _unhealthy("cortex", ["connection refused"], Action.RESTART)
        out = asyncio.run(h.heal(r))
        assert out.action_taken == Action.CUSTOM
        assert out.action_success is False
        assert out.severity == Severity.CRITICAL  # 不谎报 RECOVERED

    @pytest.mark.parametrize(
        "organ,symptom,expected_action,expected_path",
        [
            (
                "vein",
                "cache miss or expired entries in view",
                Action.CLEAR_CACHE,
                "/api/vein/cache/clear",
            ),
            ("vein", "expired entries pile up", Action.CLEANUP, "/api/vein/cache/cleanup"),
            ("mirror", "expired sandbox residue", Action.CLEANUP, "/api/mirror/cleanup"),
        ],
    )
    def test_builtin_strategy_repair_success(
        self, healer_stub, organ, symptom, expected_action, expected_path
    ):
        """策略真执行分支：_execute_repair 真 HTTP POST 修复端点 200→RECOVERED。

        症状串显式含策略模式（模块注释：diagnose 生产链产不出这些症状）。
        """
        state, base = healer_stub
        state.script("POST", expected_path, {"status": 200, "json": {"status": "ok"}})
        r = _unhealthy(organ, [symptom], expected_action)
        out = asyncio.run(_healer(base).heal(r))
        assert out.action_taken == expected_action
        assert out.action_success is True
        assert out.severity == Severity.RECOVERED
        assert [(m, p) for m, p, _ in state.recorded()] == [("POST", expected_path)]

    def test_repair_endpoint_failure_not_recovered(self, healer_stub):
        state, base = healer_stub
        state.script("POST", "/api/vein/cache/clear", {"status": 500, "json": {"error": "no"}})
        r = _unhealthy("vein", ["cache miss or expired entries"], Action.CLEAR_CACHE)
        out = asyncio.run(_healer(base).heal(r))
        assert out.action_taken == Action.CLEAR_CACHE
        assert out.action_success is False
        assert out.severity == Severity.CRITICAL  # 修复失败不谎报 RECOVERED

    def test_generic_clear_cache_success(self, healer_stub):
        """生产可达的修复链：超时症状→CLEAR_CACHE→generic POST /api/{organ}/cache/clear。"""
        state, base = healer_stub
        state.script("POST", "/api/pulse/cache/clear", {"status": 200, "json": {"status": "ok"}})
        r = _unhealthy("pulse", ["timeout"], Action.CLEAR_CACHE)
        out = asyncio.run(_healer(base).heal(r))
        assert out.action_taken == Action.CLEAR_CACHE
        assert out.action_success is True
        assert out.severity == Severity.RECOVERED
        assert [(m, p) for m, p, _ in state.recorded()] == [("POST", "/api/pulse/cache/clear")]


# ── notify / audit 成功分支（真投递） ───────────────────────────


class TestNotifyAudit:
    def test_notify_all_healthy_short_circuit(self, healer_stub):
        state, base = healer_stub
        results = [DiagnosisResult(organ="vein", healthy=True, severity=Severity.INFO)]
        out = asyncio.run(_healer(base).notify(results))
        assert out == {"notified": False, "reason": "all healthy"}
        assert state.recorded() == []  # 全健康零投递

    def test_notify_success_payload(self, healer_stub):
        state, base = healer_stub
        state.script("POST", "/api/echo/send", {"status": 200, "json": {"ok": True}})
        failed = _unhealthy("pulse", ["HTTP 500"], Action.NONE)
        recovered = _unhealthy("vein", ["timeout"], Action.CLEAR_CACHE)
        recovered.severity = Severity.RECOVERED
        recovered.action_taken = Action.CLEAR_CACHE
        recovered.action_success = True
        out = asyncio.run(_healer(base).notify([failed, recovered]))
        assert out == {
            "notified": True,
            "status": 200,
            "failed_count": 1,
            "recovered_count": 1,
        }
        rec = state.recorded("POST", "/api/echo/send")
        assert len(rec) == 1
        body = json.loads(rec[0][2])
        assert body["channel"] == "console"
        assert body["title"] == "[Healer] ⚠️ 1 organ(s) unhealthy | ✅ 1 recovered"
        assert body["priority"] == 1  # 有失败=高优先
        assert "pulse" in body["content"] and "vein" in body["content"]
        assert "Action: none → ❌ failed" in body["content"]
        assert "recovered via clear_cache" in body["content"]

    def test_audit_success_payload(self, healer_stub):
        state, base = healer_stub
        state.script("POST", "/api/nerve/event", {"status": 200, "json": {"ok": True}})
        acted = _unhealthy("vein", ["timeout"], Action.CLEAR_CACHE)
        acted.action_taken = Action.CLEAR_CACHE
        acted.action_success = True
        untouched = _unhealthy("pulse", ["HTTP 500"], Action.NONE)
        asyncio.run(_healer(base).audit_log([acted, untouched]))
        rec = state.recorded("POST", "/api/nerve/event")
        assert len(rec) == 1
        body = json.loads(rec[0][2])
        assert body["organ"] == "healer" and body["type"] == "healing_cycle"
        assert body["summary"] == "Healing cycle: 1 action(s) taken"
        actions = body["detail"]["actions"]
        assert len(actions) == 1  # 只记有动作的行
        assert actions[0]["organ"] == "vein"
        assert actions[0]["action"] == "clear_cache"
        assert actions[0]["success"] is True
        assert "timestamp" in actions[0]

    def test_audit_no_actions_zero_request(self, healer_stub):
        state, base = healer_stub
        results = [
            DiagnosisResult(organ="vein", healthy=True, severity=Severity.INFO),
            _unhealthy("pulse", ["HTTP 500"], Action.NONE),
        ]
        asyncio.run(_healer(base).audit_log(results))
        assert state.recorded() == []  # 无动作不落审计（既有语义）


# ── API 全链（真 router → OrganHealer → 真 HTTP） ───────────────
# 慢响应两例各 ~10s：diagnose 超时（httpx timeout=10.0 硬编码）是真实诊断链上
# 产出非 NONE 推荐动作的唯一形态（模块注释），走真超时不缩时钟。


def _api_client(monkeypatch, base):
    import src.api.healer as healer_api

    tmp = OrganHealer(base_url=base)
    monkeypatch.setattr(healer_api, "healer", tmp)
    app = FastAPI()
    app.include_router(healer_api.router, prefix="/api/healer")
    return TestClient(app), tmp


class TestApiHealthyChain:
    def test_cycle_notify_success_all_healthy_and_failed(self, healer_stub, monkeypatch):
        """/cycle 快路径：真探测（1 健康+1 失败）→notify 真投递成功→audit 无动作零请求。"""
        state, base = healer_stub
        state.script("GET", "/api/vein/health", {"status": 200, "json": {"status": "ok"}})
        state.script("GET", "/api/pulse/health", {"status": 500, "json": {"error": "boom"}})
        state.script("POST", "/api/echo/send", {"status": 200, "json": {"ok": True}})
        client, _ = _api_client(monkeypatch, base)
        resp = client.post(
            "/api/healer/cycle", json={"organs": ["vein", "pulse"], "auto_heal": True}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["cycle_complete"] is True
        assert data["total_organs"] == 2
        assert data["healthy"] == 1 and data["unhealthy"] == 1
        assert data["healed"] == 0  # pulse 无 timeout/slow/connection 症状→推荐 none 不修
        assert data["notified"] == {
            "notified": True,
            "status": 200,
            "failed_count": 1,
            "recovered_count": 0,
        }
        assert state.recorded("POST", "/api/echo/send") and not state.recorded(
            "POST", "/api/nerve/event"
        )

    def test_cycle_heal_recover_notify_audit(self, healer_stub, monkeypatch):
        """/cycle 全链：真超时→generic 修复真执行→复查健康→RECOVERED→notify+audit 真投递。"""
        state, base = healer_stub
        state.script(
            "GET",
            "/api/reflex/health",
            {"status": 200, "json": {"status": "degraded-ish"}, "sleep": 11.0},  # 首查真超时
            {"status": 200, "json": {"status": "ok"}},  # 修复后复查=快健康
        )
        state.script("POST", "/api/reflex/cache/clear", {"status": 200, "json": {"ok": True}})
        state.script("POST", "/api/echo/send", {"status": 200, "json": {"ok": True}})
        state.script("POST", "/api/nerve/event", {"status": 200, "json": {"ok": True}})
        client, _ = _api_client(monkeypatch, base)
        resp = client.post("/api/healer/cycle", json={"organs": ["reflex"], "auto_heal": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["healed"] == 1
        assert data["healthy"] == 1 and data["unhealthy"] == 0
        organ = data["organs"][0]
        assert organ["healthy"] is True
        assert organ["severity"] == "recovered"
        assert organ["action_taken"] == "clear_cache" and organ["action_success"] is True
        assert data["notified"] == {
            "notified": True,
            "status": 200,
            "failed_count": 0,
            "recovered_count": 1,
        }
        # 真 HTTP 证据链：探测→修复→复查→通知→审计 五段请求全到桩
        kinds = [(m, p) for m, p, _ in state.recorded()]
        assert kinds.count(("GET", "/api/reflex/health")) == 2  # 首查+复查
        assert ("POST", "/api/reflex/cache/clear") in kinds
        assert ("POST", "/api/echo/send") in kinds
        audit = state.recorded("POST", "/api/nerve/event")
        assert json.loads(audit[0][2])["detail"]["actions"][0]["organ"] == "reflex"

    def test_heal_organ_recheck_recovery(self, healer_stub, monkeypatch):
        """/heal/{organ}=diagnose_and_heal 真链：超时→修复→复查健康→RECOVERED。"""
        state, base = healer_stub
        state.script(
            "GET",
            "/api/pulse/health",
            {"status": 200, "json": {"status": "ok"}, "sleep": 11.0},  # 首查真超时
            {"status": 200, "json": {"status": "ok"}},
        )
        state.script("POST", "/api/pulse/cache/clear", {"status": 200, "json": {"ok": True}})
        client, _ = _api_client(monkeypatch, base)
        resp = client.post("/api/healer/heal/pulse")
        assert resp.status_code == 200
        data = resp.json()
        assert data["healthy"] is True and data["severity"] == "recovered"
        assert data["action_taken"] == "clear_cache" and data["action_success"] is True
        kinds = [(m, p) for m, p, _ in state.recorded()]
        assert kinds.count(("GET", "/api/pulse/health")) == 2
        assert ("POST", "/api/pulse/cache/clear") in kinds
