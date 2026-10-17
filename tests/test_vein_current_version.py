"""T3-⑥ vein GET /versions current_version 同族截断收口。

修前（T3-⑤ 诚实边界②留档件）：api/vein.py GET /files/{id}/versions 响应
current_version=len(versions)，versions 被 limit（缺省 50）截断——超 limit 文件
「当前版本数」虚低（T3-⑤ version_count 同族截断件）。收口：current_version 走
count 语义（file_store.get_version_count，与 update 响应 version_count 同一读真源）。

键名复核（复核即切片本体）：version_number=MAX+1 连续分配、行只增不单删
（delete_history 整删）→ COUNT(*)==MAX(version_number) 恒等，「版本行数」与
「当前版本号」两读同值，键名 current_version 保留（既有契约零破坏）。
TestVersionNumberEquivalence 锁死该恒等式（未来行裁剪/空洞出现必炸=留档判据）。

不变面（锁死）：≤limit 文件 current_version 与修前 len(versions) 逐值一致；
首传 0 行 current_version==0；列表面 versions 截断是 limit 语义（既有）零变化。
"""

from src.vein.file_store import FileStore


def _make_store(tmp_path) -> FileStore:
    return FileStore(root=str(tmp_path / "files"), db_path=str(tmp_path / "vein_meta.db"))


def _upload(vein_client, name="cv.txt", data=b"v0"):
    r = vein_client.post("/api/vein/upload", files={"file": (name, data, "text/plain")})
    assert r.status_code == 200
    return r.json()["file_id"]


def _update(vein_client, file_id, name, data):
    r = vein_client.put(
        f"/api/vein/files/{file_id}/content",
        files={"file": (name, data, "text/plain")},
    )
    assert r.status_code == 200
    return r.json()


class TestCurrentVersionNotTruncated:
    def test_current_version_beyond_default_limit(self, vein_client):
        # 55 次内容变更=55 版本行：current_version 必须 55，修前 len(versions)=50 虚低
        file_id = _upload(vein_client)
        for i in range(1, 56):
            _update(vein_client, file_id, "cv.txt", f"v{i}".encode())
        data = vein_client.get(f"/api/vein/files/{file_id}/versions").json()
        # 对照硬证：列表面缺省 limit 仍回 50 行（截断只在列表面=limit 语义既有）
        assert len(data["versions"]) == 50
        assert data["current_version"] == 55, data["current_version"]
        vein_client.delete(f"/api/vein/files/{file_id}")

    def test_current_version_beyond_explicit_limit(self, vein_client):
        # ?limit=1 时列表恰 1 行、current_version 仍全量计数（计数面不随 limit）
        file_id = _upload(vein_client, name="cv2.txt")
        for i in (1, 2, 3):
            _update(vein_client, file_id, "cv2.txt", f"v{i}".encode())
        data = vein_client.get(f"/api/vein/files/{file_id}/versions?limit=1").json()
        assert len(data["versions"]) == 1
        assert data["current_version"] == 3
        vein_client.delete(f"/api/vein/files/{file_id}")

    def test_current_version_small_contract_unchanged(self, vein_client):
        # ≤limit 文件逐值与修前 len 口径一致（既有契约零变化）：首传 0、1、2
        file_id = _upload(vein_client, name="cv3.txt")
        data = vein_client.get(f"/api/vein/files/{file_id}/versions").json()
        assert data["current_version"] == 0
        assert data["versions"] == []
        _update(vein_client, file_id, "cv3.txt", b"v1")
        data = vein_client.get(f"/api/vein/files/{file_id}/versions").json()
        assert data["current_version"] == 1
        _update(vein_client, file_id, "cv3.txt", b"v2")
        data = vein_client.get(f"/api/vein/files/{file_id}/versions").json()
        assert data["current_version"] == 2
        assert len(data["versions"]) == 2
        vein_client.delete(f"/api/vein/files/{file_id}")

    def test_current_version_same_content_not_inflated(self, vein_client):
        # 同内容更新不记版本行（store 既有设计），计数不虚增
        file_id = _upload(vein_client, name="cv4.txt", data=b"same")
        _update(vein_client, file_id, "cv4.txt", b"changed")
        _update(vein_client, file_id, "cv4.txt", b"changed")
        data = vein_client.get(f"/api/vein/files/{file_id}/versions").json()
        assert data["current_version"] == 1
        vein_client.delete(f"/api/vein/files/{file_id}")

    def test_current_version_matches_update_version_count(self, vein_client):
        # 同一读真源：GET current_version 与 PUT version_count 逐值一致（禁双源漂移）
        file_id = _upload(vein_client, name="cv5.txt")
        last = None
        for i in (1, 2, 3):
            last = _update(vein_client, file_id, "cv5.txt", f"v{i}".encode())
        data = vein_client.get(f"/api/vein/files/{file_id}/versions").json()
        assert data["current_version"] == last["version_count"] == 3
        vein_client.delete(f"/api/vein/files/{file_id}")


class TestVersionNumberEquivalence:
    """键名复核证据：COUNT(*)==MAX(version_number) 恒等（两读同值）。

    行号 MAX+1 连续分配（record_version）、行只增不单删（delete_history 整删）、
    回滚记新行（rollback_to_version→record_version）——恒等式在全部可达状态成立。
    """

    def _assert_equivalence(self, store, fid):
        rows = store.get_version_history(fid, limit=200)
        count = store.get_version_count(fid)
        assert count == len(rows)
        if rows:
            assert count == max(v.version_number for v in rows)
        return count

    def test_equivalence_across_upload_update_rollback(self, tmp_path):
        store = _make_store(tmp_path)
        store.store(data=b"v0", name="t.txt", file_id="fid-eq")
        assert self._assert_equivalence(store, "fid-eq") == 0  # 首传 0 行
        for i in (1, 2, 3):
            store.store(data=f"v{i}".encode(), name="t.txt", file_id="fid-eq")
        assert self._assert_equivalence(store, "fid-eq") == 3
        # 回滚记新版本行（既有设计）：恒等式仍成立、计数随之增长
        assert store.rollback_to_version("fid-eq", 1) is not None
        assert self._assert_equivalence(store, "fid-eq") == 4

    def test_equivalence_beyond_default_limit(self, tmp_path):
        # 超 limit 状态下恒等式仍成立（count 不被 get_history 缺省 50 截断）
        store = _make_store(tmp_path)
        store.store(data=b"v0", name="t.txt", file_id="fid-eq2")
        for i in range(1, 61):
            store.store(data=f"v{i}".encode(), name="t.txt", file_id="fid-eq2")
        rows = store.get_version_history("fid-eq2", limit=200)
        assert store.get_version_count("fid-eq2") == 60 == max(v.version_number for v in rows)
