"""T3-⑦ api/vein.py update 摘要版本号双调收口——单次读取+读面带行租户键。

修前（T3-④ 留档件「api/vein.py:273 双调 cleanup」）：PUT /files/{id}/content 的
push_event 摘要在 f-string 条件表达式里重复调用 store.get_version_history
(file_id, limit=1)——同一行两次 SQL 读；且两读均零租户键（系统视图，与同
handler 其余 DEC-⑦-⑩ 带键读面不对称）。

收口：单次读取 (get_version_history(...) or [None])[0] + 读面带行租户键
（identity.tenant_id）。文案契约零变化：有版本行 "(vN)"、无版本行 "(v?)"。
"""

from datetime import UTC, datetime, timedelta

from jose import jwt

from src.config import settings

ENTERPRISE_DDL = """CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')))"""


def _ent_token(uid, username="u"):
    payload = {
        "sub": str(uid),
        "username": username,
        "scope": "enterprise",
        "exp": datetime.now(UTC) + timedelta(minutes=10),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _insert_ent_user(db, username, tenant):
    import sqlite3

    from src.services import tenant_binding as tb

    tb.ensure_user_tenant_columns(str(db))
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password, is_active, tenant_id) VALUES (?, ?, ?, ?)",
            (username, "hashed", 1, tenant),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _spy_history(monkeypatch, store):
    """包一层 get_version_history 计数（调用次数+实参回读）。"""
    calls = []
    orig = store.get_version_history

    def spy(file_id, limit=50, tenant_id=None):
        calls.append({"file_id": file_id, "limit": limit, "tenant_id": tenant_id})
        return orig(file_id, limit, tenant_id=tenant_id)

    monkeypatch.setattr(store, "get_version_history", spy)
    return calls


def _recorder(monkeypatch, vein_api):
    """push_event 记录器（summary 字节契约+调用租户键直读）。"""
    events = []

    def record(event, *, tenant_id=None):
        events.append({"event": event, "tenant_id": tenant_id})

    monkeypatch.setattr(vein_api, "push_event", record)
    return events


def _upload(vein_client, name="sr.txt", data=b"v0", headers=None):
    r = vein_client.post(
        "/api/vein/upload",
        files={"file": (name, data, "text/plain")},
        data={"tags": ""},
        headers=headers or {},
    )
    assert r.status_code == 200
    return r.json()["file_id"]


def _update(vein_client, file_id, name, data, headers=None):
    r = vein_client.put(
        f"/api/vein/files/{file_id}/content",
        files={"file": (name, data, "text/plain")},
        data={"change_summary": ""},
        headers=headers or {},
    )
    assert r.status_code == 200
    return r.json()


class TestUpdateSummarySingleRead:
    def test_update_summary_reads_version_row_once(self, vein_client, monkeypatch):
        """摘要版本号单次读取：修前同一行双调（2 次 SQL），修后恰 1 次。"""
        import src.api.vein as vein_api

        store = vein_client.app.state.vein_client_store
        events = _recorder(monkeypatch, vein_api)
        file_id = _upload(vein_client)
        calls = _spy_history(monkeypatch, store)
        _update(vein_client, file_id, "sr.txt", b"v1")
        # 恰一次读取（修前条件表达式双调=2 次，硬反证）
        assert len(calls) == 1, calls
        assert calls[0]["file_id"] == file_id
        assert calls[0]["limit"] == 1
        summaries = [e["event"]["summary"] for e in events if e["event"]["type"] == "file_updated"]
        assert summaries == ["📝 File updated: sr.txt (v1)"]

    def test_summary_label_contract_unchanged(self, vein_client, monkeypatch):
        """文案契约零变化（不变面）：有版本行 '(vN)' 逐字节与修前一致。"""
        import src.api.vein as vein_api

        events = _recorder(monkeypatch, vein_api)
        file_id = _upload(vein_client, name="sr2.txt")
        _update(vein_client, file_id, "sr2.txt", b"v1")
        _update(vein_client, file_id, "sr2.txt", b"v2")
        summaries = [e["event"]["summary"] for e in events if e["event"]["type"] == "file_updated"]
        assert summaries == [
            "📝 File updated: sr2.txt (v1)",
            "📝 File updated: sr2.txt (v2)",
        ]

    def test_summary_no_version_row_question_mark(self, vein_client, monkeypatch):
        """无版本行='?' 分支契约零变化（不变面）：同内容更新不记版本行。"""
        import src.api.vein as vein_api

        events = _recorder(monkeypatch, vein_api)
        file_id = _upload(vein_client, name="sr3.txt", data=b"same")
        resp = _update(vein_client, file_id, "sr3.txt", b"same")
        assert resp["version_count"] == 0  # 同内容不记版本行（既有设计）
        summaries = [e["event"]["summary"] for e in events if e["event"]["type"] == "file_updated"]
        assert summaries == ["📝 File updated: sr3.txt (v?)"]

    def test_summary_read_carries_derived_tenant(self, vein_client, monkeypatch, tmp_path):
        """读面带行租户键：认证派生租户真流入单次读取（修前系统视图零键）。"""
        import sqlite3

        import src.api.vein as vein_api

        monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
        monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
        ent_db = tmp_path / "enterprise.db"
        conn = sqlite3.connect(ent_db)
        conn.execute(ENTERPRISE_DDL)
        conn.commit()
        conn.close()
        uid = _insert_ent_user(ent_db, "carol", "tenant-vein-t3g")
        headers = {"Authorization": f"Bearer {_ent_token(uid, 'carol')}"}

        store = vein_client.app.state.vein_client_store
        events = _recorder(monkeypatch, vein_api)
        file_id = _upload(vein_client, name="sr4.txt", headers=headers)
        calls = _spy_history(monkeypatch, store)
        _update(vein_client, file_id, "sr4.txt", b"v1", headers=headers)
        assert len(calls) == 1, calls
        # 派生租户键真流入读面（修前 tenant_id=None 系统视图，硬反证）
        assert calls[0]["tenant_id"] == "tenant-vein-t3g"
        summaries = [e["event"]["summary"] for e in events if e["event"]["type"] == "file_updated"]
        assert summaries == ["📝 File updated: sr4.txt (v1)"]
        assert events[-1]["tenant_id"] == "tenant-vein-t3g"
