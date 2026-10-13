"""timeline 测试隔离护栏——TIMELINE_DB 全测试隔离（timeline 测试隔离拆件①）。

修前（2026-10-13 全量 pytest 实测）：120 行/轮写生产 data/timeline.db——
in-process 74 行（brain think/verify 事件经 event_bridge push_event/emit 懒构造
TimelineStore()，缺省恒 ~/opensoul/data/timeline.db）+ live 测试写方 46 行
（healer/marrow/link/soma/vein/immune/sandbox/pipeline/echo/cortex 等 live 文件，
拆件②清单另拍）。本文件锁死拆件①隔离边界：

- default_db_path() 缺省路径跟 TIMELINE_DB env（NEST_DB/AGENT_REGISTRY_DB 先例）
- push_event/emit 真写 env tmp 库、生产库零测试行（独特哨兵 summary 硬证）
- 懒缓存每用例复位语义（conftest _isolate_timeline_db delattr）
- api.timeline 模块单例构造随 env（不落生产）

生产库只读探针（file:...?mode=ro URI）绝不写。
"""

import asyncio
import importlib
import json
import sqlite3
import sys
import uuid
from pathlib import Path

import httpx

from src.nerve import event_bridge
from src.timeline import store as store_mod
from src.timeline.store import TimelineStore

PROD_DB = str(Path(store_mod.__file__).resolve().parents[2] / "data" / "timeline.db")


def _marker() -> str:
    return f"isolation-guard-{uuid.uuid4().hex}"


def _count_marker(db_path: str, marker: str) -> int:
    """只读数某库中含哨兵 summary 的行数（库不存在=0）。"""
    p = Path(db_path)
    if not p.exists():
        return 0
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM events WHERE summary = ?", (marker,)).fetchone()[0]
    finally:
        con.close()


def _db_file(store: TimelineStore) -> str:
    """store 真实落库文件（PRAGMA database_list 硬证，禁只信构造参数）。"""
    return store._db.execute("PRAGMA database_list").fetchone()[2]


class _NerveSpy:
    """MockTransport 捕获 nerve publish（emit 步骤 1，避免真 HTTP）。"""

    def __init__(self):
        self.bodies: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


# ── 路径解析单一源 ───────────────────────────────────────────


def test_default_db_path_follows_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TIMELINE_DB", str(tmp_path / "t.db"))
    assert store_mod.default_db_path() == str(tmp_path / "t.db")


def test_default_db_path_without_env_is_repo_data(monkeypatch):
    # 纯函数断言（不构造 store=不接触生产库文件）
    monkeypatch.delenv("TIMELINE_DB", raising=False)
    assert store_mod.default_db_path() == PROD_DB


def test_constructor_resolves_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TIMELINE_DB", str(tmp_path / "t.db"))
    s = TimelineStore()
    assert _db_file(s) == str(tmp_path / "t.db")


# ── 真写路径：env tmp 库落行、生产库零测试行 ─────────────────


def test_push_event_writes_env_tmp_not_production():
    marker = _marker()
    event_bridge.push_event(
        {"organ": "guard", "type": "isolation_guard", "summary": marker, "detail": {}}
    )
    import os

    assert _count_marker(os.environ["TIMELINE_DB"], marker) == 1
    assert _count_marker(PROD_DB, marker) == 0


def test_emit_writes_env_tmp_not_production():
    marker = _marker()
    spy = _NerveSpy()

    async def run():
        async with spy.client() as c:
            return await event_bridge.emit("guard", "isolation_guard", marker, {}, _client=c)

    asyncio.run(run())
    import os

    assert _count_marker(os.environ["TIMELINE_DB"], marker) == 1
    assert _count_marker(PROD_DB, marker) == 0


# ── 懒缓存复位语义（conftest delattr）────────────────────────


def test_lazy_cache_reset_per_test():
    # 用例开始时懒缓存已被 conftest 复位（下一构造随本用例 env tmp）
    assert not hasattr(event_bridge.push_event, "_timeline")
    assert not hasattr(event_bridge.emit, "_timeline")


def test_lazy_construct_follows_env_tmp():
    marker = _marker()
    event_bridge.push_event(
        {"organ": "guard", "type": "isolation_guard", "summary": marker, "detail": {}}
    )
    import os

    # 懒构造出的缓存真源=本用例 tmp（PRAGMA 硬证）
    assert _db_file(event_bridge.push_event._timeline) == os.environ["TIMELINE_DB"]


# ── api.timeline 模块单例构造随 env ─────────────────────────


def test_api_timeline_singleton_follows_env(monkeypatch, tmp_path):
    import os

    fresh_path = str(tmp_path / "fresh.db")
    monkeypatch.setenv("TIMELINE_DB", fresh_path)
    # 模块已缓存则强制全新 import（monkeypatch.delitem teardown 恢复原模块对象）
    monkeypatch.delitem(sys.modules, "src.api.timeline", raising=False)
    mod = importlib.import_module("src.api.timeline")
    assert _db_file(mod.store) == fresh_path
    assert _db_file(mod.store) != PROD_DB
    assert os.environ["TIMELINE_DB"] == fresh_path
