"""RV-①b-⑩ gland provider 注册配置持久化 — 行语义/写穿/回载/纯内存/故障/真实路径接线。

修前（RV-①c §5.1 藏活事实定性）：ModelRouter.providers 全进程内存，API 注册的
name/base_url/models/priority 零重建=重启后路由配置丢。本文件锁死：注册/修改/删除
随写落盘、首触懒回载、重启注册配置不丢；settings 补位三件套不落行；钥面刻意不落。

控制组容缺导入（家族纪律）：旧代码无 provider_persist 模块/无 persist_providers
形参时降级跑，让失败暴露在行为断言上而非 ImportError。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

try:
    from src.gland.provider_persist import ProviderPersistence, default_db_path
except ImportError:  # 控制组容缺：旧代码无本模块
    ProviderPersistence = None  # type: ignore[assignment]
    default_db_path = None  # type: ignore[assignment]

from src.gland.router import ModelRouter, ProviderConfig

# ── helpers ─────────────────────────────────────────────────────


def _persist(tmp_path, name="gland_providers.db"):
    return ProviderPersistence(str(tmp_path / name))


def _router(tmp_path, name="gland_providers.db") -> ModelRouter:
    persist = _persist(tmp_path, name)
    try:
        return ModelRouter(persist_providers=persist)
    except TypeError:  # 控制组容缺：旧代码无 persist_providers 形参
        return ModelRouter()


def _rows(db_file) -> list[dict]:
    """raw SQL 直读（不经 persist 代码=独立复算）。"""
    conn = sqlite3.connect(str(db_file))
    try:
        cur = conn.execute(
            "SELECT name, base_url, models, enabled, priority FROM gland_providers ORDER BY rowid"
        )
        return [
            {
                "name": r[0],
                "base_url": r[1],
                "models": json.loads(r[2]) if r[2] else {},
                "enabled": bool(r[3]),
                "priority": int(r[4]),
            }
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


# ── ① 行语义（快照表）──────────────────────────────────────────


class TestRowSemantics:
    def test_upsert_load往返逐键含models与bool(self, tmp_path):
        p = _persist(tmp_path)
        p.upsert(
            {
                "name": "prov-a",
                "base_url": "http://a.example/v1",
                "models": {"chat": "m1", "embedding": "e1"},
                "enabled": True,
                "priority": 3,
            }
        )
        p.upsert(
            {
                "name": "prov-b",
                "base_url": "http://b.example/v1",
                "models": {},
                "enabled": False,
                "priority": 0,
            }
        )
        rows = p.load_all()
        assert [r["name"] for r in rows] == ["prov-a", "prov-b"]  # rowid 序=注册序
        assert rows[0] == {
            "name": "prov-a",
            "base_url": "http://a.example/v1",
            "models": {"chat": "m1", "embedding": "e1"},
            "enabled": True,
            "priority": 3,
        }
        assert rows[1]["enabled"] is False and rows[1]["models"] == {}

    def test_upsert当前态一provider一行(self, tmp_path):
        p = _persist(tmp_path)
        p.upsert(
            {"name": "x", "base_url": "http://old", "models": {}, "enabled": True, "priority": 0}
        )
        p.upsert(
            {
                "name": "x",
                "base_url": "http://new",
                "models": {"chat": "m"},
                "enabled": False,
                "priority": 9,
            }
        )
        rows = p.load_all()
        assert len(rows) == 1  # 状态快照：一 provider 一行
        assert rows[0]["base_url"] == "http://new" and rows[0]["priority"] == 9

    def test_delete幂等缺行无异常(self, tmp_path):
        p = _persist(tmp_path)
        p.upsert(
            {"name": "x", "base_url": "http://x", "models": {}, "enabled": True, "priority": 0}
        )
        p.delete("x")
        p.delete("x")  # 缺行=同一效果
        assert p.load_all() == []

    def test坏行_models不可解跳过可见(self, tmp_path, caplog):
        p = _persist(tmp_path)
        p.upsert(
            {"name": "good", "base_url": "http://g", "models": {}, "enabled": True, "priority": 0}
        )
        db = tmp_path / "gland_providers.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO gland_providers (name, base_url, models, enabled, priority) VALUES (?,?,?,?,?)",
            ("bad", "http://b", "{not-json", 1, 0),
        )
        conn.commit()
        conn.close()
        with caplog.at_level("ERROR"):
            rows = p.load_all()
        assert [r["name"] for r in rows] == ["good"]  # 坏行跳过不吞整段
        assert "bad" in caplog.text  # 禁静默丢：真因可见

    def test回载只读不建库(self, tmp_path):
        db = tmp_path / "never.db"
        p = _persist(tmp_path, "never.db")
        assert p.load_all() == []
        assert p.count() == 0
        assert not db.exists()  # 写穿才建库（防 import 期建空生产库）

    def test_env解析与显式路径优先(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GLAND_PROVIDERS_DB", str(tmp_path / "env.db"))
        assert default_db_path() == str(tmp_path / "env.db")
        p = _persist(tmp_path, "explicit.db")
        p.upsert(
            {"name": "x", "base_url": "http://x", "models": {}, "enabled": True, "priority": 0}
        )
        assert (tmp_path / "explicit.db").exists()  # 显式路径优先
        assert not (tmp_path / "env.db").exists()


# ── ② 写穿（add/update/remove 变更点）──────────────────────────


class TestWriteThrough:
    def test_add_provider落行逐键(self, tmp_path):
        r = _router(tmp_path)
        r.add_provider("prov-a", "http://a.example/v1/", {"chat": "m1"}, priority=2)
        rows = _rows(tmp_path / "gland_providers.db")
        assert rows == [
            {
                "name": "prov-a",
                "base_url": "http://a.example/v1",  # rstrip 归一随落
                "models": {"chat": "m1"},
                "enabled": True,
                "priority": 2,
            }
        ]

    def test_persist_rowFalse不落行_bootstrap三件套语义(self, tmp_path):
        r = _router(tmp_path)
        r.add_provider("openai", "http://seed", {"chat": "m"}, priority=0, persist_row=False)
        assert not (tmp_path / "gland_providers.db").exists()  # 补位名不建行
        assert "openai" in r.providers  # 内存注册照常

    def test_update落行随更(self, tmp_path):
        r = _router(tmp_path)
        r.add_provider("prov-a", "http://a", {"chat": "m"}, priority=0)
        r.update_provider("prov-a", {"priority": 7, "enabled": False, "models": {"chat": "m2"}})
        rows = _rows(tmp_path / "gland_providers.db")
        assert rows[0]["priority"] == 7 and rows[0]["enabled"] is False
        assert rows[0]["models"] == {"chat": "m2"}

    def test_update补位名不落行_临时改语义(self, tmp_path):
        r = _router(tmp_path)
        r.add_provider("openai", "http://seed", {"chat": "m"}, priority=0, persist_row=False)
        r.update_provider("openai", {"priority": 5})
        assert not (tmp_path / "gland_providers.db").exists()  # settings 真源名 PATCH=临时

    def test_update未落盘名_撞名补位行随内存影子(self, tmp_path):
        """外部注册名被补位 upsert 撞名：行=内存当前态（防双源漂移，add_provider
        elif 分支）。"""
        r = _router(tmp_path)
        r.add_provider("openai", "http://external", {"chat": "m"}, priority=1)
        r.add_provider("openai", "http://seed", {"chat": "m"}, priority=0, persist_row=False)
        rows = _rows(tmp_path / "gland_providers.db")
        assert rows[0]["base_url"] == "http://seed"  # 行随内存（补位胜），非双源

    def test_remove删行不复活(self, tmp_path):
        r = _router(tmp_path)
        r.add_provider("prov-a", "http://a", {"chat": "m"}, priority=0)
        assert r.remove_provider("prov-a") == 0  # 命中（0=该 provider 本无钥，注册照删）
        assert _rows(tmp_path / "gland_providers.db") == []
        r2 = _router(tmp_path)  # 模拟重启
        r2.ensure_providers_loaded()
        assert "prov-a" not in r2.providers

    def test_remove补位名零行副作用(self, tmp_path):
        r = _router(tmp_path)
        r.add_provider("openai", "http://seed", {"chat": "m"}, priority=0, persist_row=False)
        r.remove_provider("openai")
        assert not (tmp_path / "gland_providers.db").exists()


# ── ③ 回载（重启不丢）──────────────────────────────────────────


class TestReload:
    def test_重启回载逐键parity_运行态不落(self, tmp_path):
        r = _router(tmp_path)
        r.add_provider("prov-a", "http://a", {"chat": "m1", "embedding": "e1"}, priority=3)
        r.providers["prov-a"]._consecutive_failures = 2  # 运行时熔断态
        r.update_provider("prov-a", {"enabled": False})
        r2 = _router(tmp_path)  # 模拟重启
        r2.ensure_providers_loaded()  # 生产定序：bootstrap 显式触发（防裸属性访问跳过懒回载）
        cfg = r2.providers.get("prov-a")
        assert cfg is not None
        assert cfg.base_url == "http://a"
        assert cfg.models == {"chat": "m1", "embedding": "e1"}
        assert cfg.enabled is False and cfg.priority == 3
        assert cfg._consecutive_failures == 0  # 熔断计数不落盘（重启清零=既有语义）

    def test_懒回载恰一次_先载后录(self, tmp_path):
        base = _persist(tmp_path)
        base.upsert(
            {"name": "seeded", "base_url": "http://s", "models": {}, "enabled": True, "priority": 0}
        )

        class _Spy:
            def __init__(self, inner):
                self.inner = inner
                self.loads = 0

            def load_all(self):
                self.loads += 1
                return self.inner.load_all()

            def upsert(self, row):
                return self.inner.upsert(row)

            def delete(self, name):
                return self.inner.delete(name)

        spy = _Spy(base)
        r = ModelRouter(persist_providers=spy)  # type: ignore[arg-type]  # duck-typed 观察桩
        r.add_provider("added", "http://d", {"chat": "m"}, priority=0)  # 先载后录
        r.list_providers()
        r.has_provider("x")
        assert spy.loads == 1  # 懒回载恰一次
        assert set(r.providers) == {"seeded", "added"}  # 回载行+新注册行并存

    def test_只补缺不覆盖_内存新态不被回载洗(self, tmp_path):
        base = _persist(tmp_path)
        base.upsert(
            {"name": "a", "base_url": "http://stale", "models": {}, "enabled": True, "priority": 0}
        )
        r = ModelRouter(persist_providers=base)
        r.providers["a"] = ProviderConfig(name="a", base_url="http://fresh", models={}, priority=0)
        r.ensure_providers_loaded()
        assert r.providers["a"].base_url == "http://fresh"  # 内存新态胜，不被回载洗

    def test_回载失败空起步_下次触碰重试(self, tmp_path, caplog):
        base = _persist(tmp_path)
        base.upsert(
            {"name": "a", "base_url": "http://a", "models": {}, "enabled": True, "priority": 0}
        )

        class _Flaky:
            def __init__(self, inner):
                self.inner = inner
                self.calls = 0

            def load_all(self):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("db locked")
                return self.inner.load_all()

            def upsert(self, row):
                return self.inner.upsert(row)

            def delete(self, name):
                return self.inner.delete(name)

        flaky = _Flaky(base)
        r = ModelRouter(persist_providers=flaky)  # type: ignore[arg-type]  # duck-typed 故障桩
        with caplog.at_level("ERROR"):
            r.list_providers()
        assert r.providers == {}  # 空起步
        assert "db locked" in caplog.text  # 真因可见
        r.list_providers()  # 下次触碰重试
        assert "a" in r.providers


# ── ④ 纯内存零变化 + 故障可见不阻断 ─────────────────────────────


class TestPureMemoryAndFailures:
    def test纯内存零变化零落盘(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GLAND_PROVIDERS_DB", str(tmp_path / "never.db"))
        r = ModelRouter()
        r.add_provider("x", "http://x", {"chat": "m"}, priority=0)
        r.update_provider("x", {"priority": 1})
        r.remove_provider("x")
        assert not (tmp_path / "never.db").exists()  # 缺省纯内存既有语义字节不变
        assert r.providers == {}

    def test_modelrouter_persist缺省None(self):
        assert ModelRouter()._provider_persist is None
        assert ModelRouter(persist_providers=ProviderPersistence())._provider_persist is not None

    def test写失败旁路不阻断注册动作(self, tmp_path, caplog):
        class _Boom:
            def load_all(self):
                return []

            def upsert(self, row):
                raise RuntimeError("disk on fire")

            def delete(self, name):
                raise RuntimeError("disk on fire")

        r = ModelRouter(persist_providers=_Boom())  # type: ignore[arg-type]  # duck-typed 故障桩
        with caplog.at_level("ERROR"):
            cfg = r.add_provider("x", "http://x", {"chat": "m"}, priority=0)
        assert cfg.name == "x" and "x" in r.providers  # 注册本体不被落盘失败炸掉
        assert "disk on fire" in caplog.text  # 禁静默吞


# ── ⑤ 真实路径接线（写了≠接线了）──────────────────────────────


class TestWiring:
    def test_gateway_singleton真挂持久化(self):
        from src.api import gland

        assert gland.gateway._provider_persist is not None

    def _client(self, gland_api, monkeypatch, gw):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        monkeypatch.setattr(gland_api, "gateway", gw)
        monkeypatch.setattr(gland_api, "_ensure_bootstrapped", lambda: None)
        app = FastAPI()
        app.include_router(gland_api.router, prefix="/api/gland")
        return TestClient(app)

    def test_API全链_写穿回载_模拟重启后可见(self, tmp_path, monkeypatch):
        from src.api import gland as gland_api

        gw = _router(tmp_path)
        client = self._client(gland_api, monkeypatch, gw)
        db = tmp_path / "gland_providers.db"
        # POST 注册（带钥）→ 落行
        resp = client.post(
            "/api/gland/providers",
            json={
                "name": "rv1b10-x",
                "base_url": "http://x.example/v1",
                "models": {"chat": "m1"},
                "priority": 1,
                "api_key": "sk-secret-123",
            },
        )
        assert resp.status_code == 200
        rows = _rows(db)
        assert rows[0]["name"] == "rv1b10-x" and rows[0]["priority"] == 1
        raw = (tmp_path / "gland_providers.db").read_bytes()
        assert b"sk-secret-123" not in raw  # 钥面刻意不落（诚实边界硬证）
        # PATCH → 行随更
        resp = client.patch("/api/gland/providers/rv1b10-x", json={"priority": 7})
        assert resp.status_code == 200
        assert _rows(db)[0]["priority"] == 7
        # 模拟重启：新路由实例同库回载 → 配置在、钥不随行
        gw2 = _router(tmp_path)
        client2 = self._client(gland_api, monkeypatch, gw2)
        resp = client2.get("/api/gland/providers")
        names = [p["name"] for p in resp.json()["providers"]]
        assert "rv1b10-x" in names  # 重启注册配置不丢（修前=丢）
        assert gw2.key_manager.status().get("rv1b10-x", []) == []  # 钥不落如实
        # 同名再注册 = 409（回载行真进冲突判定）
        resp = client2.post(
            "/api/gland/providers",
            json={"name": "rv1b10-x", "base_url": "http://y", "models": {}},
        )
        assert resp.status_code == 409
        # DELETE → 删行 → 再模拟重启 → 不复活
        resp = client2.delete("/api/gland/providers/rv1b10-x")
        assert resp.status_code == 200
        assert _rows(db) == []
        gw3 = _router(tmp_path)
        gw3.ensure_providers_loaded()
        assert "rv1b10-x" not in gw3.providers  # 删行后重启不复活

    def test_bootstrap旗标_外部行不吞settings补位(self, tmp_path, monkeypatch):
        """本切片根因修的缺陷面：providers dict 非空（外部行回载）不得跳过 settings
        补位——修前 dict-非空代理会丢失 openai 默认注册。"""
        from src.api import gland as gland_api
        from src.config import settings

        gw = _router(tmp_path)
        gw.add_provider("myprov", "http://my", {"chat": "m"}, priority=2)  # 外部注册行
        monkeypatch.setattr(gland_api, "gateway", gw)
        monkeypatch.setattr(gland_api, "_BOOTSTRAPPED", False)
        monkeypatch.setattr(settings, "llm_base_url", "http://seed.example/v1")
        monkeypatch.setattr(settings, "llm_model", "seed-model")
        monkeypatch.setattr(settings, "embedding_model", "seed-embed")
        monkeypatch.setattr(settings, "llm_api_key", "")
        monkeypatch.setattr(settings, "embedding_api_key", "")
        gland_api._ensure_bootstrapped()
        assert "myprov" in gw.providers  # 外部行不丢
        assert "openai" in gw.providers  # settings 补位照常（修前被吞）
        assert gw.providers["openai"].base_url == "http://seed.example/v1"
        # 补位三件套不落行：只有外部行进落盘面
        assert [r["name"] for r in _rows(tmp_path / "gland_providers.db")] == ["myprov"]

    def test接线锁_静态(self):
        import src.api.gland as gland_api
        import src.api.llm as llm_api
        import src.gland.router as router_mod

        gland_src = open(gland_api.__file__, encoding="utf-8").read()
        llm_src = open(llm_api.__file__, encoding="utf-8").read()
        router_src = open(router_mod.__file__, encoding="utf-8").read()
        assert "persist_providers=ProviderPersistence()" in gland_src  # 单例真挂
        assert "ensure_providers_loaded()" in gland_src  # bootstrap 定序触发
        assert gland_src.count("persist_row=False") >= 1  # openai 补位不落行
        assert llm_src.count("persist_row=False") >= 2  # variant/local 备胎不落行
        assert "def _ensure_providers_loaded(self) -> None:" in router_src
        assert "self._sync_provider_row(cfg)" in router_src  # 写穿单一点真消费
