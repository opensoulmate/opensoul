"""T3-⑤ vein version_count 截断修复——计数语义走 get_version_count，不被 limit 截断。

修前（T3-④ 留档覆盖缺失活证据）：api/vein.py update 响应
version_count=len(store.get_version_history(file_id)) 缺省 limit=50 截断，
超 50 版文件计数虚低。收口：VersionManager.get_version_count（COUNT(*) 全量）
经 file_store.get_version_count（DEC-⑦-⑩ 归属闸）接线到 update 响应。

不变面（锁死）：≤50 版文件计数值与修前逐值一致；同内容更新不记版本行不虚增。
"""

from src.vein.file_store import FileStore


def _make_store(tmp_path) -> FileStore:
    return FileStore(root=str(tmp_path / "files"), db_path=str(tmp_path / "vein_meta.db"))


class TestCountSemanticsNotTruncated:
    def test_count_beyond_default_limit(self, tmp_path):
        # 60 次内容变更=60 版本行：count 必须 60，修前 len(get_history) 口径=50 虚低
        store = _make_store(tmp_path)
        store.store(data=b"v0", name="t.txt", file_id="fid-count")
        for i in range(1, 61):
            store.store(data=f"v{i}".encode(), name="t.txt", file_id="fid-count")
        assert store.get_version_count("fid-count") == 60
        # 对照硬证：get_history 缺省 limit=50 只回 50 行（len 口径的截断根因）
        assert len(store.get_version_history("fid-count")) == 50

    def test_small_count_contract_unchanged(self, tmp_path):
        # ≤limit 文件：count 与 len(get_history) 逐值一致（既有契约零变化）
        store = _make_store(tmp_path)
        store.store(data=b"v0", name="t.txt", file_id="fid-small")
        for i in (1, 2):
            store.store(data=f"v{i}".encode(), name="t.txt", file_id="fid-small")
        assert store.get_version_count("fid-small") == 2
        assert store.get_version_count("fid-small") == len(store.get_version_history("fid-small"))

    def test_unchanged_content_not_inflated(self, tmp_path):
        # 同内容更新不记版本行（store 既有设计），计数不虚增
        store = _make_store(tmp_path)
        store.store(data=b"same", name="t.txt", file_id="fid-same")
        store.store(data=b"changed", name="t.txt", file_id="fid-same")
        store.store(data=b"changed", name="t.txt", file_id="fid-same")
        assert store.get_version_count("fid-same") == 1

    def test_missing_file_count_zero(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.get_version_count("no-such-file") == 0


class TestCountTenantGate:
    """DEC-⑦-⑩ 归属闸（get_version_history 同款）：跨租户回 0 与 not-found 同契约。"""

    def test_gate_cross_tenant_and_system_view(self, tmp_path):
        store = _make_store(tmp_path)
        store.store(data=b"v0", name="t.txt", file_id="fid-ten", tenant_id="tenant-a")
        for i in (1, 2):
            store.store(
                data=f"v{i}".encode(),
                name="t.txt",
                file_id="fid-ten",
                tenant_id="tenant-a",
            )
        # 首传不记版本行（既有设计）、2 次内容变更=2 版本行
        assert store.get_version_count("fid-ten", tenant_id="tenant-a") == 2
        assert store.get_version_count("fid-ten", tenant_id="tenant-b") == 0
        assert store.get_version_count("fid-ten") == 2  # None=系统视图不闸
        # 缺行同契约（不差分泄露）
        assert store.get_version_count("no-such", tenant_id="tenant-a") == 0


class TestUpdateResponseVersionCount:
    """API 全链：PUT /files/{id}/content 响应 version_count 真跑 count 语义。"""

    def test_update_response_version_count_not_truncated(self, vein_client):
        r = vein_client.post(
            "/api/vein/upload",
            files={"file": ("count.txt", b"v0", "text/plain")},
        )
        assert r.status_code == 200
        file_id = r.json()["file_id"]

        last = None
        for i in range(1, 56):
            r = vein_client.put(
                f"/api/vein/files/{file_id}/content",
                files={"file": ("count.txt", f"v{i}".encode(), "text/plain")},
            )
            assert r.status_code == 200
            last = r.json()
        # 55 次内容变更=55 版本行；修前 len(get_history) 口径=50 虚低
        assert last["version_count"] == 55, last
        # 对照：GET versions 缺省 limit 仍回 50 行（截断只在列表面，计数面不截断）
        hist = vein_client.get(f"/api/vein/files/{file_id}/versions").json()
        assert len(hist["versions"]) == 50
        vein_client.delete(f"/api/vein/files/{file_id}")

    def test_update_response_small_count_unchanged(self, vein_client):
        # e2e 契约逐值不变（首变更=1、二次=2，与 T3-④ 探针同断言）
        r = vein_client.post(
            "/api/vein/upload",
            files={"file": ("small.txt", b"v0", "text/plain")},
        )
        assert r.status_code == 200
        file_id = r.json()["file_id"]
        r1 = vein_client.put(
            f"/api/vein/files/{file_id}/content",
            files={"file": ("small.txt", b"v1", "text/plain")},
        )
        assert r1.json()["version_count"] == 1
        r2 = vein_client.put(
            f"/api/vein/files/{file_id}/content",
            files={"file": ("small.txt", b"v2", "text/plain")},
        )
        assert r2.json()["version_count"] == 2
        vein_client.delete(f"/api/vein/files/{file_id}")
