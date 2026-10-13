"""HEAL-②b-④ 幽灵端点收口测面——策略/编排目标全部真存在 + 三口补现行为真执行。

HEAL-② 审计②：_REPAIR_STRATEGIES 3/6 修复端点幽灵（/api/reflex/cache/clear、
/api/hippo/sessions/cleanup、/api/immune/audit/cleanup）+ admin_actions 4 幽灵
目标（/api/reflex/cache/clear、/api/voice/cache/clear、/api/hippo/sessions/cleanup、
/api/reflex/cache/cleanup）——heal() 修复 POST 404、admin 编排半瘫（逐目标如实报
error 不谎报 ok，但目标本身打空）。

收口方向（按目标语义逐个裁决，禁一刀切）：
  补现（声明要的能力真实现）：
    reflex /cache/clear——CLEAR_CACHE=全清减负，与 /cleanup 清过期留有效的
      语义分界（reflex 既有真端点 /api/reflex/cleanup 只清过期，改指它=动作
      语义被静默降级，F1 同型）
    voice /cache/clear——真 TTS 磁盘缓存（~/.opensoul/tts_cache）真清
    immune /audit/cleanup——环形缓冲裁剪原语（keep_last，缺省容量之半）
  改指真端点（不发明语义）：
    hippo sessions/cleanup → sessions/lifecycle-check（hippo 自身过期维护机制）
    admin reflex_cache cleanup → /api/reflex/cleanup（真端点逐字同语义）

路由真源 = src/main.py include_router 挂载面（测试内解析 main.py 而非手抄
prefix，防双源漂移）。零生产接触（不打 :8090、不写生产库）。
"""

import asyncio
import importlib
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.healer.diagnose import _REPAIR_STRATEGIES
from src.immune.audit import AuditAction, AuditLogger
from src.reflex.cache import ReflexCache
from src.voice.tts_engine import TTSEngine

# ── 挂载面真源解析（src/main.py 唯一真源，禁手抄 prefix）─────────────

_MAIN_SRC = (Path(__file__).resolve().parent.parent / "src" / "main.py").read_text(encoding="utf-8")
_IMPORT_RE = re.compile(r"^from (src\.api\.[A-Za-z_]+) import router as ([A-Za-z_]+)$", re.M)
_MOUNT_RE = re.compile(r'app\.include_router\((\w+),\s*prefix="([^"]+)"')


def _mounted_paths() -> set[str]:
    """从 main.py include_router 挂载面派生全部真路由路径。"""
    alias_to_module = {alias: module for module, alias in _IMPORT_RE.findall(_MAIN_SRC)}
    paths: set[str] = set()
    for alias, prefix in _MOUNT_RE.findall(_MAIN_SRC):
        module_name = alias_to_module.get(alias)
        if module_name is None:
            continue  # 非 router 直挂（gateway 等）不在本测面
        router = getattr(importlib.import_module(module_name), "router")
        for route in router.routes:
            paths.add(prefix + route.path)
    return paths


def _url_path(url: str) -> str:
    return "/" + url.split("://", 1)[1].split("/", 1)[1]


# ── 闭② 本体：幽灵归零 ─────────────────────────────────────────────


class TestGhostClosure:
    def test_strategy_endpoints_all_mounted(self):
        """_REPAIR_STRATEGIES 全部 6 条修复端点必须真实存在于挂载面（修前 3 幽灵）。"""
        mounted = _mounted_paths()
        ghosts = []
        for organ, strategies in _REPAIR_STRATEGIES.items():
            for strategy in strategies:
                if strategy["endpoint"] not in mounted:
                    ghosts.append((organ, strategy["endpoint"]))
        assert ghosts == [], f"ghost repair endpoints: {ghosts}"

    def test_admin_orchestration_zero_ghost(self):
        """admin 两个编排函数真跑一遍（记录型 httpx 桩）：请求目标零幽灵。"""
        from src.api import admin_actions

        recorded: list[tuple[str, str]] = []

        class _Resp:
            status_code = 200

            def json(self):
                return {"ok": True}

        class _RecordingClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url):
                recorded.append(("POST", url))
                return _Resp()

            async def get(self, url):
                recorded.append(("GET", url))
                return _Resp()

        orig = admin_actions.httpx.AsyncClient
        admin_actions.httpx.AsyncClient = _RecordingClient
        try:
            r1 = asyncio.run(admin_actions.clear_all_caches())
            r2 = asyncio.run(admin_actions.cleanup_expired())
        finally:
            admin_actions.httpx.AsyncClient = orig

        mounted = _mounted_paths()
        ghosts = [_url_path(u) for _, u in recorded if _url_path(u) not in mounted]
        assert ghosts == [], f"admin orchestration hit ghost targets: {ghosts}"
        # 编排结果全部真达（桩全 200）
        assert r1["cleared"] == r1["total"] == 3
        assert r2["cleaned"] == r2["total"] == 4

    def test_admin_targets_retargeted_contract(self):
        """改指真端点契约锁死：幽灵路径绝不再被请求，真路径顶上。"""
        from src.api import admin_actions

        recorded: list[str] = []

        class _Resp:
            status_code = 200

            def json(self):
                return {"ok": True}

        class _RecordingClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url):
                recorded.append(url)
                return _Resp()

            async def get(self, url):
                recorded.append(url)
                return _Resp()

        orig = admin_actions.httpx.AsyncClient
        admin_actions.httpx.AsyncClient = _RecordingClient
        try:
            asyncio.run(admin_actions.clear_all_caches())
            asyncio.run(admin_actions.cleanup_expired())
        finally:
            admin_actions.httpx.AsyncClient = orig

        assert "http://127.0.0.1:8090/api/hippo/sessions/lifecycle-check" in recorded
        assert "http://127.0.0.1:8090/api/reflex/cleanup" in recorded
        assert not any(u.endswith("/api/hippo/sessions/cleanup") for u in recorded)
        assert not any(u.endswith("/api/reflex/cache/cleanup") for u in recorded)


# ── 补现① reflex cache/clear（clear=全清 vs cleanup=清过期）────────────


class TestReflexCacheClear:
    def test_clear_primitive_drops_all(self):
        cache = ReflexCache()
        cache.put("query one", "resp one")
        cache.put("query two", "resp two")
        out = cache.clear()
        assert out == {"cleared": 2, "remaining": 0}
        assert cache.list_entries() == []
        assert cache.lookup("query one") is None

    def test_cleanup_semantics_boundary(self):
        """clear 与 cleanup 语义分界锁死：cleanup 只清过期、clear 全清。"""
        cache = ReflexCache()
        cache.put("fresh query", "resp", ttl_seconds=3600)
        cache.put("stale query", "resp", ttl_seconds=0.001)
        import time as _t

        _t.sleep(0.05)
        out = cache.cleanup()
        assert out["removed"] == 1  # 过期 1 条
        assert cache.lookup("fresh query") is not None  # 有效条目保留
        cache.clear()
        assert cache.lookup("fresh query") is None

    def test_clear_endpoint(self):
        import src.api.reflex as reflex_api

        fresh = ReflexCache()
        fresh.put("endpoint query", "resp")
        app = FastAPI()
        app.include_router(reflex_api.router, prefix="/api/reflex")
        orig = reflex_api.cache
        reflex_api.cache = fresh
        try:
            client = TestClient(app)
            resp = client.post("/api/reflex/cache/clear")
            assert resp.status_code == 200
            assert resp.json() == {"cleared": 1, "remaining": 0}
            assert client.get("/api/reflex/cache").json()["entries"] == []
        finally:
            reflex_api.cache = orig


# ── 补现② immune audit/cleanup（环形缓冲裁剪）────────────────────────


class TestImmuneAuditCleanup:
    def _logger(self, n: int, max_entries: int = 100) -> AuditLogger:
        log = AuditLogger(max_entries=max_entries)
        for i in range(n):
            log.log(AuditAction.API_CALL, detail=f"entry-{i}")
        return log

    def test_cleanup_keep_last(self):
        log = self._logger(5)
        out = log.cleanup(keep_last=2)
        assert out == {"dropped": 3, "remaining": 2, "keep_last": 2}
        # 保序=留最新
        details = [e["detail"] for e in log.query(limit=10)]
        assert details == ["entry-4", "entry-3"]

    def test_cleanup_default_half_capacity(self):
        log = self._logger(8, max_entries=10)
        out = log.cleanup()
        assert out == {"dropped": 3, "remaining": 5, "keep_last": 5}

    def test_cleanup_rejects_invalid_keep_last(self):
        log = self._logger(3)
        bad_values: list = [-1, True, "2", 2.5]
        for bad in bad_values:
            try:
                log.cleanup(keep_last=bad)
            except ValueError:
                continue
            raise AssertionError(f"keep_last={bad!r} should be rejected")

    def test_cleanup_endpoint(self):
        import src.api.immune as immune_api

        fresh = AuditLogger()
        for i in range(5):
            fresh.log(AuditAction.ACCESS_DENIED, detail=f"e{i}")
        app = FastAPI()
        app.include_router(immune_api.router, prefix="/api/immune")
        orig = immune_api.audit
        immune_api.audit = fresh
        try:
            client = TestClient(app)
            resp = client.post("/api/immune/audit/cleanup", json={"keep_last": 2})
            assert resp.status_code == 200
            assert resp.json() == {"dropped": 3, "remaining": 2, "keep_last": 2}
            # 缺省=容量之半
            resp2 = client.post("/api/immune/audit/cleanup")
            assert resp2.status_code == 200
            assert resp2.json()["keep_last"] == 5000
            # 非法=400 如实拒绝
            resp3 = client.post("/api/immune/audit/cleanup", json={"keep_last": -1})
            assert resp3.status_code == 400
        finally:
            immune_api.audit = orig


# ── 补现③ voice cache/clear（真 TTS 磁盘缓存）────────────────────────


class TestVoiceCacheClear:
    def test_clear_cache_primitive(self, tmp_path):
        engine = TTSEngine(cache_dir=str(tmp_path / "tts_cache"))
        (tmp_path / "tts_cache" / "aaa.mp3").write_bytes(b"x" * 10)
        (tmp_path / "tts_cache" / "bbb.mp3").write_bytes(b"y" * 5)
        (tmp_path / "tts_cache" / "sub").mkdir()
        out = engine.clear_cache()
        assert out == {"removed": 2, "freed_bytes": 15}
        assert list((tmp_path / "tts_cache").iterdir()) == [
            tmp_path / "tts_cache" / "sub"
        ]  # 目录不动，只清缓存文件

    def test_clear_cache_endpoint(self, tmp_path):
        import src.api.voice as voice_api

        fresh = TTSEngine(cache_dir=str(tmp_path / "tts_cache"))
        (tmp_path / "tts_cache" / "zzz.mp3").write_bytes(b"z" * 3)
        app = FastAPI()
        app.include_router(voice_api.router, prefix="/api/voice")
        orig = voice_api.engine
        voice_api.engine = fresh
        try:
            client = TestClient(app)
            resp = client.post("/api/voice/cache/clear")
            assert resp.status_code == 200
            assert resp.json() == {"removed": 1, "freed_bytes": 3}
            assert fresh._check_cache("zzz") is None
        finally:
            voice_api.engine = orig


# ── hippo 策略改指真端点契约锁死 ────────────────────────────────────


class TestHippoStrategyRetarget:
    def test_hippo_strategy_points_to_real_endpoint(self):
        hippo = _REPAIR_STRATEGIES["hippo"][0]
        assert hippo["endpoint"] == "/api/hippo/sessions/lifecycle-check"
        assert hippo["endpoint"] in _mounted_paths()
        # 描述如实（lifecycle-check 是标记 idle/expired，非删除）
        assert "lifecycle" in hippo["description"]

    def test_no_ghost_strings_left_in_strategy_table(self):
        for ghost in (
            "/api/hippo/sessions/cleanup",
            "/api/reflex/cache/cleanup",
        ):
            for strategies in _REPAIR_STRATEGIES.values():
                for strategy in strategies:
                    assert strategy["endpoint"] != ghost
