"""project_memory 缓存增量调度（H1-②b-⑩）测试。

修前（H1-② 定性「project_memory 缓存增量无调度（think 每请求全仓重扫）」）：
①brain think 带 repo_root 每请求构造 ProjectMemory=每请求全仓重扫（__init__ 恒
  全量 _build_index），refresh_incremental 全仓零消费且实现带 4 处根缺陷
  （迭代中 del 必炸/新增文件永不发现/变更 import 边不同步/删除幽灵边）。
②save_cache 全仓零消费=缓存落盘/回载面休眠（从未落盘）。

本文件 tmp 全隔离（PROJECT_MEMORY_CACHE_DIR autouse fixture→tmp），绝不写生产
缓存目录。控制组兼容：新符号经模块属性访问（旧代码 AttributeError=测试失败，
不炸收集）。
"""

import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.cortex.project_memory as pm_mod


def _make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True)
    (repo / "main.py").write_text("import util\nprint(1)\n", encoding="utf-8")
    (repo / "extra.py").write_text("import util\ne = 1\n", encoding="utf-8")
    (repo / "util.py").write_text("import helper\nx = 1\n", encoding="utf-8")
    (repo / "helper.py").write_text("y = 1\n", encoding="utf-8")
    (repo / "notes.md").write_text("# notes\n", encoding="utf-8")
    return repo


def _bump_mtime(path: Path, seconds: float = 5.0) -> None:
    t = time.time() + seconds
    os.utime(path, (t, t))


def _count_calls(monkeypatch, name: str) -> list[int]:
    """类方法调用计数桩（真身照跑）。"""
    counter = [0]
    orig = getattr(pm_mod.ProjectMemory, name)

    def counted(self, *args, **kwargs):
        counter[0] += 1
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(pm_mod.ProjectMemory, name, counted)
    return counter


class TestRefreshIncremental:
    def test_incremental_discovers_new_file(self, tmp_path, monkeypatch):
        """新增文件必须被发现（修前只遍历既有 file_index=新文件永不入索引）。"""
        repo = _make_repo(tmp_path)
        pm = pm_mod.ProjectMemory(str(repo))
        (repo / "new_mod.py").write_text("import helper\nz = 2\n", encoding="utf-8")
        asyncio.run(pm.refresh_incremental())
        assert "new_mod.py" in pm.file_index
        assert pm.import_graph["new_mod.py"] == ["helper.py"]
        assert "new_mod.py" in pm.dependency_graph["helper.py"]

    def test_incremental_deletion_no_runtimeerror_no_ghost(self, tmp_path):
        """删除文件：不抛 RuntimeError（修前迭代中 del 必炸）+幽灵行/幽灵边全清。"""
        repo = _make_repo(tmp_path)
        pm = pm_mod.ProjectMemory(str(repo))
        (repo / "util.py").unlink()
        asyncio.run(pm.refresh_incremental())  # 修前此处 RuntimeError
        assert "util.py" not in pm.file_index
        assert "util.py" not in pm.import_graph
        assert "util.py" not in pm.dependency_graph
        # 幽灵入边硬反证：任何 import 边不得再指向已删文件
        for imports in pm.import_graph.values():
            assert "util.py" not in imports

    def test_incremental_new_file_links_prior_unresolved_import(self, tmp_path):
        """拓扑回落全量的根因语义：新增文件补上既有文件早前解析不出的 import 边
        （禁漏边——增量补边构造上补不出，故拓扑变更如实回落 rebuild）。"""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "main.py").write_text("import util\nprint(1)\n", encoding="utf-8")
        pm = pm_mod.ProjectMemory(str(repo))
        assert pm.import_graph["main.py"] == []  # util.py 不存在=解析不出
        (repo / "util.py").write_text("u = 1\n", encoding="utf-8")
        asyncio.run(pm.refresh_incremental())
        assert pm.import_graph["main.py"] == ["util.py"]  # 修前漏边

    def test_incremental_content_change_updates_edges(self, tmp_path, monkeypatch):
        """内容变更：行数+import 边同步更新，且只重读变更文件（_build_index 零调用）。"""
        repo = _make_repo(tmp_path)
        pm = pm_mod.ProjectMemory(str(repo))
        assert pm.import_graph["main.py"] == ["util.py"]
        build = _count_calls(monkeypatch, "_build_index")
        (repo / "main.py").write_text("import helper\nprint(1)\nprint(2)\n", encoding="utf-8")
        _bump_mtime(repo / "main.py")
        asyncio.run(pm.refresh_incremental())
        assert build[0] == 0  # 增量非全量硬证
        assert pm.import_graph["main.py"] == ["helper.py"]  # 修前恒旧边 ['util.py']
        assert pm.file_index["main.py"].lines == 3
        assert "main.py" in pm.dependency_graph["helper.py"]
        assert "main.py" not in pm.dependency_graph.get("util.py", [])

    def test_incremental_no_change_noop(self, tmp_path, monkeypatch):
        """零变更：零内容重读零重建（_build_index 零调用），索引逐键不变。"""
        repo = _make_repo(tmp_path)
        pm = pm_mod.ProjectMemory(str(repo))
        before = {k: (v.lines, v.size_bytes, v.last_modified) for k, v in pm.file_index.items()}
        build = _count_calls(monkeypatch, "_build_index")
        asyncio.run(pm.refresh_incremental())
        asyncio.run(pm.refresh_incremental())
        assert build[0] == 0
        after = {k: (v.lines, v.size_bytes, v.last_modified) for k, v in pm.file_index.items()}
        assert after == before

    def test_core_flag_resets_on_recompute(self, tmp_path):
        """is_core 标志随重算复位（修前只置 True 永不清=陈旧核心标志残留）。"""
        repo = _make_repo(tmp_path)
        pm = pm_mod.ProjectMemory(str(repo))
        assert pm.file_index["util.py"].is_core  # 初始 util.py 依赖方最多=核心
        # 两个依赖方都不再 import util（内容变更=定点重读路径）
        (repo / "main.py").write_text("print(1)\n", encoding="utf-8")
        (repo / "extra.py").write_text("e = 1\n", encoding="utf-8")
        _bump_mtime(repo / "main.py")
        _bump_mtime(repo / "extra.py")
        asyncio.run(pm.refresh_incremental())
        assert not pm.file_index["util.py"].is_core  # 修前恒 True
        for p, info in pm.file_index.items():
            assert info.is_core == (p in pm.core_files)

    def test_rebuild_clears_stale_rows(self, tmp_path):
        """rebuild 清旧态后重扫（拓扑回落路径的正确性基础）。"""
        repo = _make_repo(tmp_path)
        pm = pm_mod.ProjectMemory(str(repo))
        (repo / "helper.py").unlink()
        pm.rebuild()
        assert "helper.py" not in pm.file_index


class TestCacheRoundTrip:
    def test_save_load_roundtrip(self, tmp_path):
        """save_cache→load_cache 逐键往返（索引/图/核心/_last_scan）。"""
        repo = _make_repo(tmp_path)
        cache = tmp_path / "pm.json"
        pm1 = pm_mod.ProjectMemory(str(repo), cache_path=str(cache))
        pm1.save_cache()
        pm2 = pm_mod.ProjectMemory(str(repo), cache_path=str(cache), autoload=True)
        assert set(pm2.file_index) == set(pm1.file_index)
        for k, v in pm1.file_index.items():
            w = pm2.file_index[k]
            assert (w.language, w.lines, w.size_bytes, w.is_core) == (
                v.language,
                v.lines,
                v.size_bytes,
                v.is_core,
            )
            assert w.last_modified == v.last_modified
        assert pm2.import_graph == pm1.import_graph
        assert pm2.dependency_graph == pm1.dependency_graph
        assert pm2.core_files == pm1.core_files
        assert pm2._last_scan == pm1._last_scan

    def test_autoload_uses_cache_not_rescan(self, tmp_path, monkeypatch):
        """autoload 命中缓存=零扫描（_build_index 零调用硬证，修前恒全量重扫）。"""
        repo = _make_repo(tmp_path)
        cache = tmp_path / "pm.json"
        pm_mod.ProjectMemory(str(repo), cache_path=str(cache)).save_cache()

        def forbid(_self):
            raise AssertionError("缓存回载不应触发全量重扫")

        monkeypatch.setattr(pm_mod.ProjectMemory, "_build_index", forbid)
        pm2 = pm_mod.ProjectMemory(str(repo), cache_path=str(cache), autoload=True)
        assert "main.py" in pm2.file_index

    def test_load_missing_cache_returns_false(self, tmp_path):
        repo = _make_repo(tmp_path)
        pm = pm_mod.ProjectMemory(str(repo), cache_path=str(tmp_path / "nope.json"))
        assert pm.load_cache() is False

    def test_load_corrupt_cache_returns_false(self, tmp_path, monkeypatch):
        """损坏/旧格式缓存如实 False 禁 fabricate（回落实建）。"""
        repo = _make_repo(tmp_path)
        cache = tmp_path / "pm.json"
        pm = pm_mod.ProjectMemory(str(repo), cache_path=str(cache))
        pm.save_cache()
        data = json.loads(cache.read_text())
        # 旧格式：文件行缺 size_bytes/last_modified 对账键
        old = json.loads(cache.read_text())
        for meta in old["files"].values():
            meta.pop("size_bytes")
            meta.pop("last_modified")
        cache.write_text(json.dumps(old))
        assert pm.load_cache() is False
        # 坏 JSON
        cache.write_text("{not json")
        assert pm.load_cache() is False
        # files 非对象
        data["files"] = []
        cache.write_text(json.dumps(data))
        assert pm.load_cache() is False

    def test_save_cache_has_staleness_keys(self, tmp_path):
        """落盘件带对账键（size_bytes/last_modified=变更判定轴，缺则回载不可信）。"""
        repo = _make_repo(tmp_path)
        cache = tmp_path / "pm.json"
        pm_mod.ProjectMemory(str(repo), cache_path=str(cache)).save_cache()
        data = json.loads(cache.read_text())
        for meta in data["files"].values():
            assert "size_bytes" in meta and "last_modified" in meta

    def test_autoload_corrupt_cache_falls_back_to_full_scan(self, tmp_path):
        """回载失败如实回落全量重扫（不 fabricate 空索引）。"""
        repo = _make_repo(tmp_path)
        cache = tmp_path / "pm.json"
        cache.write_text("{broken")
        pm = pm_mod.ProjectMemory(str(repo), cache_path=str(cache), autoload=True)
        assert "main.py" in pm.file_index


class TestAcquireScheduling:
    def test_acquire_reuses_instance_within_ttl(self, tmp_path, monkeypatch):
        """TTL 内同 repo_root 复用单实例：二次获取零扫描零刷新（think 收益本体）。"""
        repo = _make_repo(tmp_path)
        build = _count_calls(monkeypatch, "_build_index")
        refresh = _count_calls(monkeypatch, "refresh_incremental")
        pm1 = asyncio.run(pm_mod.acquire_project_memory(str(repo)))
        pm2 = asyncio.run(pm_mod.acquire_project_memory(str(repo)))
        assert pm1 is pm2
        assert build[0] == 1  # 仅首取构造扫描一次
        assert refresh[0] == 0  # TTL 内零刷新

    def test_acquire_ttl_expiry_triggers_incremental_not_full(self, tmp_path, monkeypatch):
        """TTL 过期=增量刷新（非全量重扫——_build_index 零调用硬证）。"""
        repo = _make_repo(tmp_path)
        pm = asyncio.run(pm_mod.acquire_project_memory(str(repo)))
        pm._last_scan = datetime.now() - timedelta(seconds=9999)
        build = _count_calls(monkeypatch, "_build_index")
        refresh = _count_calls(monkeypatch, "refresh_incremental")
        pm2 = asyncio.run(pm_mod.acquire_project_memory(str(repo), ttl_s=60.0))
        assert pm2 is pm
        assert refresh[0] == 1
        assert build[0] == 0

    def test_acquire_force_refresh_within_ttl(self, tmp_path, monkeypatch):
        """force_refresh 突破 TTL 立即刷新（/api/brain/refresh 语义本体）。"""
        repo = _make_repo(tmp_path)
        pm = asyncio.run(pm_mod.acquire_project_memory(str(repo)))
        build = _count_calls(monkeypatch, "_build_index")
        refresh = _count_calls(monkeypatch, "refresh_incremental")
        pm2 = asyncio.run(pm_mod.acquire_project_memory(str(repo), force_refresh=True))
        assert pm2 is pm
        assert refresh[0] == 1
        assert build[0] == 0

    def test_acquire_first_acquisition_loads_cache(self, tmp_path, monkeypatch):
        """首取走缓存回载（=重启不重扫，save_cache 落盘面真闭环）。"""
        repo = _make_repo(tmp_path)
        cache = pm_mod.project_memory_cache_file(str(repo))
        pm_mod.ProjectMemory(str(repo), cache_path=str(cache)).save_cache()
        pm_mod._SHARED.clear()

        def forbid(_self):
            raise AssertionError("缓存回载不应触发全量重扫")

        monkeypatch.setattr(pm_mod.ProjectMemory, "_build_index", forbid)
        pm = asyncio.run(pm_mod.acquire_project_memory(str(repo)))
        assert "main.py" in pm.file_index

    def test_acquire_writes_cache_under_env_dir(self, tmp_path):
        """落盘=env PROJECT_MEMORY_CACHE_DIR（测试隔离单点，禁写生产 ~/.opensoul）。"""
        repo = _make_repo(tmp_path)
        asyncio.run(pm_mod.acquire_project_memory(str(repo)))
        cache = pm_mod.project_memory_cache_file(str(repo))
        assert cache.parent == Path(os.environ["PROJECT_MEMORY_CACHE_DIR"])
        assert cache.exists()

    def test_cache_file_per_repo_isolated(self, tmp_path):
        """不同 repo_root 缓存文件互不覆盖。"""
        repo_a = _make_repo(tmp_path / "a")
        repo_b = _make_repo(tmp_path / "b")
        fa = pm_mod.project_memory_cache_file(str(repo_a))
        fb = pm_mod.project_memory_cache_file(str(repo_b))
        assert fa != fb

    def test_acquire_save_failure_not_blocking(self, tmp_path, monkeypatch):
        """落盘故障不阻断调用方（旁路纪律：沿用既有索引照常返回）。"""
        repo = _make_repo(tmp_path)

        def boom(_self):
            raise RuntimeError("disk full")

        monkeypatch.setattr(pm_mod.ProjectMemory, "save_cache", boom)
        pm = asyncio.run(pm_mod.acquire_project_memory(str(repo)))
        assert "main.py" in pm.file_index


class TestHttpWiring:
    def _client(self):
        import src.api.brain as brain_api

        app = FastAPI()
        app.include_router(brain_api.router, prefix="/api/brain")
        return TestClient(app)

    def test_think_reuses_shared_memory_across_requests(self, tmp_path, monkeypatch):
        """think 两次=全量重扫恰 1 次（修前每请求 1 次=2 次，共享实例真接线）。"""
        repo = _make_repo(tmp_path)
        build = _count_calls(monkeypatch, "_build_index")
        client = self._client()
        body = {"user_input": "修复 main.py 的 bug", "repo_root": str(repo)}
        r1 = client.post("/api/brain/think", json=body)
        r2 = client.post("/api/brain/think", json=body)
        assert r1.status_code == 200 and r2.status_code == 200
        assert "decision" in r1.json()
        assert build[0] == 1

    def test_refresh_endpoint_updates_shared_instance(self, tmp_path, monkeypatch):
        """/refresh 强制刷新共享实例：新增文件真进 think 用的同一真源+stats 契约不变。"""
        repo = _make_repo(tmp_path)
        client = self._client()
        body = {"user_input": "修复 main.py 的 bug", "repo_root": str(repo)}
        assert client.post("/api/brain/think", json=body).status_code == 200
        (repo / "late.py").write_text("w = 1\n", encoding="utf-8")
        resp = client.post("/api/brain/refresh", params={"repo_root": str(repo)})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "refreshed"
        for key in ("total_files", "core_files", "total_dependencies", "last_scan"):
            assert key in data
        assert data["total_files"] == 6  # 5 + late.py（新增真发现）
        # 同一真源：think 用的共享实例已含 late.py（修前刷新构造一次性实例丢弃）
        key = (str(repo.resolve()), str(pm_mod.project_memory_cache_file(str(repo))))
        assert "late.py" in pm_mod._SHARED[key].file_index

    def test_refresh_no_repo_root_contract(self):
        """既有契约零变化：空 repo_root 回 no_repo_root。"""
        resp = self._client().post("/api/brain/refresh", params={"repo_root": ""})
        assert resp.status_code == 200
        assert resp.json() == {"status": "no_repo_root"}
