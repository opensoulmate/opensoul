"""F3b-⑨b-②b-5 pipeline 3 调用点接派生——push_event 带 bind_identity 派生租户键。

F3b-⑨b-② 留档候选「其余 HTTP 调用点分 organ 拆拍」第五件=pipeline 模块全 3 点
（F3b-⑨ census：pipeline 3 全在 src/api/pipeline.py，
upload blocked/upload completed/run completed 各 1，两个 handler）。

修前：3 调用点不传 tenant_id→事件 dict 无键→record() 'default' 兜底
（timeline/store.py record tenant_val），非 default 租户身份用户的 pipeline
动作事件落 'default'→被自己的租户过滤查询挡掉=自动作自我不可见
（F3b-⑨ live 硬证同型、②b-1 vein/②b-2 immune/②b-3 mirror/②b-4 healer 同型）。

数据面全 tmp（USERS_DB/ENTERPRISE_DB/tmp TimelineStore/死端口 _BASE/
FileStore 与 sense/immune 步骤桩），绝不写生产 opensoul.db/enterprise.db/
timeline.db/vein 存储、不打 live :8090。
"""

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from jose import jwt

from src.config import settings
from src.nerve import event_bridge

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


def _insert_ent_user(db, username, tenant, is_active=1):
    from src.services import tenant_binding as tb

    tb.ensure_user_tenant_columns(str(db))
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password, is_active, tenant_id) VALUES (?, ?, ?, ?)",
            (username, "hashed", is_active, tenant),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _rows(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute("SELECT * FROM events ORDER BY timestamp, event_id")]
    con.close()
    return out


def _auth(token):
    return {"Authorization": f"Bearer {token}"} if token else {}


class _StubFileStore:
    """FileStore 桩——handler→push_event 接线是本拍被测物，vein 存储本体不进测试面。

    契约随更新（DEC-⑦-④）：store() 签名对齐真实 FileStore.store（含 file_id/
    change_summary/tenant_id 写点派生形参），handler 新写点接线不被桩吞掉。
    """

    def store(
        self,
        data,
        name,
        mime_type="application/octet-stream",
        tags=None,
        file_id=None,
        change_summary="",
        tenant_id=None,
    ):
        return SimpleNamespace(
            file_id="f_stub_1",
            name=name,
            size=len(data),
            content_hash="hash-stub",
            mime_type=mime_type,
            tenant_id=tenant_id or "default",
        )

    def retrieve(self, file_id):
        meta = SimpleNamespace(
            file_id=file_id,
            name="note.txt",
            size=11,
            content_hash="hash-stub",
            mime_type="text/plain",
        )
        return b"hello world", meta


async def _fake_sense(data, mime_type, filename, pipeline_type):
    return {"step": "sense-text", "status": "ok", "text": "hello world"}


async def _fake_immune_low(text):
    return {
        "step": "immune",
        "status": "ok",
        "is_safe": True,
        "risk_level": "low",
        "findings_count": 0,
        "redacted_available": False,
    }


async def _fake_immune_high(text):
    return {
        "step": "immune",
        "status": "ok",
        "is_safe": False,
        "risk_level": "high",
        "findings_count": 1,
        "redacted_available": True,
    }


@pytest.fixture()
def pipeline_env(tmp_path, monkeypatch):
    """pipeline router + tmp 身份库/TimelineStore（绝不碰生产数据面）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.api.pipeline as pipeline_api
    import src.vein.file_store as vein_fs
    from src.timeline.store import TimelineStore

    # 流水线重步骤换桩（FileStore/sense/immune 本体不进测试面）
    monkeypatch.setattr(vein_fs, "FileStore", _StubFileStore)
    monkeypatch.setattr(pipeline_api, "_step_sense_extract", _fake_sense)
    monkeypatch.setattr(pipeline_api, "_step_immune_scan", _fake_immune_low)

    # 身份库隔离（bind_identity 真源绝不碰生产）
    monkeypatch.setenv("USERS_DB", str(tmp_path / "opensoul.db"))
    monkeypatch.setenv("ENTERPRISE_DB", str(tmp_path / "enterprise.db"))
    ent_db = tmp_path / "enterprise.db"
    conn = sqlite3.connect(ent_db)
    conn.execute(ENTERPRISE_DDL)
    conn.commit()
    conn.close()

    # timeline 行归属真源=tmp（push_event._timeline 注入，绝不写生产 timeline.db）
    tl_db = str(tmp_path / "timeline.db")
    store = TimelineStore(tl_db)
    monkeypatch.setattr(event_bridge.push_event, "_timeline", store, raising=False)
    monkeypatch.setattr(event_bridge.emit, "_timeline", store, raising=False)
    # 死端口：nerve publish 快速连接失败（被 push_event 自身吞掉），绝不打 live
    monkeypatch.setattr(event_bridge, "_BASE", "http://127.0.0.1:9")

    from src.api import event_stream

    event_stream._event_buffer.clear()

    app = FastAPI()
    app.include_router(pipeline_api.router, prefix="/api/pipeline")
    with TestClient(app) as c:
        yield c, tl_db, ent_db, event_stream, pipeline_api
    event_stream._event_buffer.clear()


def _upload(c, headers=None, extra=None):
    data = {"skip_knowledge": "true"}
    if extra:
        data.update(extra)
    return c.post(
        "/api/pipeline/upload",
        files={"file": ("note.txt", b"hello world", "text/plain")},
        data=data,
        headers=headers or {},
    )


def _run(c, headers=None):
    return c.post(
        "/api/pipeline/run",
        json={"file_id": "f_stub_1", "skip_knowledge": True},
        headers=headers or {},
    )


# ── ① 3 调用点真接派生 → record 行归属 ───────────────────────────


class TestPipelineEmitTenantWiring:
    def test_upload_completed_event_row_tenant_derived(self, pipeline_env):
        """认证派生租户→upload completed 事件行落派生租户（修前恒 'default' 自我不可见）。"""
        c, tl_db, ent_db, _es, _m = pipeline_env
        uid = _insert_ent_user(ent_db, "alice", "tenant-pipe-a")
        r = _upload(c, _auth(_ent_token(uid, "alice")))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "pipeline_completed"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-pipe-a"  # 非 'default'=真接派生
        assert rows[0]["organ"] == "pipeline"

    def test_run_completed_event_row_tenant_derived(self, pipeline_env):
        c, tl_db, ent_db, _es, _m = pipeline_env
        uid = _insert_ent_user(ent_db, "bob", "tenant-pipe-b")
        r = _run(c, _auth(_ent_token(uid, "bob")))
        assert r.status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "pipeline_completed"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-pipe-b"

    def test_upload_blocked_event_row_tenant_derived(self, pipeline_env):
        """高风险拦截事件（pipeline_blocked）同样落派生租户。"""
        c, tl_db, ent_db, _es, m = pipeline_env
        uid = _insert_ent_user(ent_db, "carol", "tenant-pipe-c")
        monkey_high = _fake_immune_high

        async def high(text):
            return await monkey_high(text)

        m._step_immune_scan = high
        try:
            r = _upload(c, _auth(_ent_token(uid, "carol")))
            assert r.status_code == 200
            assert r.json()["status"] == "blocked"
        finally:
            m._step_immune_scan = _fake_immune_low
        rows = [x for x in _rows(tl_db) if x["event_type"] == "pipeline_blocked"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "tenant-pipe-c"

    def test_buffer_dict_carries_tenant_key(self, pipeline_env):
        """事件 dict 自带键（bus/缓冲/record 同源注入）——⑨b-① 三处注入真流经调用点。"""
        c, _tl, ent_db, es, _m = pipeline_env
        uid = _insert_ent_user(ent_db, "iris", "tenant-pipe-i")
        _upload(c, _auth(_ent_token(uid, "iris")))
        buffered = [e for e in es._event_buffer if e.get("type") == "pipeline_completed"]
        assert len(buffered) == 1
        assert buffered[0]["tenant_id"] == "tenant-pipe-i"

    def test_dual_tenant_rows_isolated(self, pipeline_env):
        """双租户各自落行互不串键。"""
        c, tl_db, ent_db, _es, _m = pipeline_env
        uid_a = _insert_ent_user(ent_db, "u_a", "tenant-dual-a")
        uid_b = _insert_ent_user(ent_db, "u_b", "tenant-dual-b")
        time.sleep(0.02)  # evt id 含 ms 时间戳，防同 ms 去重假阴（②b-1 坑延续）
        assert _upload(c, _auth(_ent_token(uid_a, "u_a"))).status_code == 200
        time.sleep(0.02)
        assert _run(c, _auth(_ent_token(uid_b, "u_b"))).status_code == 200
        rows = [x for x in _rows(tl_db) if x["event_type"] == "pipeline_completed"]
        assert sorted(x["tenant_id"] for x in rows) == ["tenant-dual-a", "tenant-dual-b"]

    def test_bad_token_upload_401_first_zero_event(self, pipeline_env):
        """身份证明先于一切（F3b-⑤ 纪律）：坏 token 在文件存储/任何副作用之前即 401，零事件。"""
        c, tl_db, _ent, _es, _m = pipeline_env
        r = _upload(c, _auth("not-a-jwt-token"))
        assert r.status_code == 401
        assert _rows(tl_db) == []

    def test_bad_token_run_401_zero_event(self, pipeline_env):
        c, tl_db, _ent, _es, _m = pipeline_env
        r = _run(c, _auth("not-a-jwt-token"))
        assert r.status_code == 401
        assert _rows(tl_db) == []


# ── ② 不变面（契约锁死） ─────────────────────────────────────────


class TestPipelineEmitTenantInvariants:
    def test_anonymous_default_contract_unchanged(self, pipeline_env):
        """匿名='default' 行值不变+upload 响应契约逐键不变（接派生不改响应面）。"""
        c, tl_db, _ent, _es, _m = pipeline_env
        r = _upload(c)
        assert r.status_code == 200
        data = r.json()
        assert sorted(data.keys()) == [
            "elapsed_ms",
            "file_id",
            "finished_at",
            "pipeline_id",
            "pipeline_type",
            "started_at",
            "status",
            "steps",
        ]
        rows = [x for x in _rows(tl_db) if x["event_type"] == "pipeline_completed"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"

    def test_run_anonymous_default_contract(self, pipeline_env):
        """/run 匿名契约逐键不变+行 'default'。"""
        c, tl_db, _ent, _es, _m = pipeline_env
        r = _run(c)
        assert r.status_code == 200
        assert sorted(r.json().keys()) == [
            "elapsed_ms",
            "file_id",
            "finished_at",
            "pipeline_id",
            "pipeline_type",
            "started_at",
            "status",
            "steps",
        ]
        rows = [x for x in _rows(tl_db) if x["event_type"] == "pipeline_completed"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"

    def test_blocked_response_contract(self, pipeline_env):
        """blocked 响应契约逐键不变+匿名行 'default'。"""
        c, tl_db, _ent, _es, m = pipeline_env

        async def high(text):
            return await _fake_immune_high(text)

        m._step_immune_scan = high
        try:
            r = _upload(c)
            assert r.status_code == 200
            assert sorted(r.json().keys()) == ["error", "file_id", "pipeline_id", "status", "steps"]
        finally:
            m._step_immune_scan = _fake_immune_low
        rows = [x for x in _rows(tl_db) if x["event_type"] == "pipeline_blocked"]
        assert len(rows) == 1
        assert rows[0]["tenant_id"] == "default"

    def test_all_three_call_sites_wired(self):
        """静态接线普查：2 handler 全带派生、调用点全传租户键（写了必须接线）。

        契约随更新（DEC-⑦-④）：修前 3（emit 面）；本拍 store.store 写点接派生
        租户（+1 落行键），emit 面 3 处零变化，总数 4。
        """
        pipe = chr(124)
        src = (Path(__file__).parent.parent / "src" / "api" / "pipeline.py").read_text(
            encoding="utf-8"
        )
        assert src.count("identity = bind_identity(authorization)") == 2
        assert src.count("tenant_id=identity.tenant_id") == 4
        assert src.count("push_event(") == 3
        assert src.count("authorization: str " + pipe + " None = Header(None)") == 2  # 竖线完好探针
        starts = []
        i = 0
        while True:
            j = src.find('"organ": "pipeline"', i)
            if j < 0:
                break
            starts.append(j)
            i = j + 1
        assert len(starts) == 3
        for j in starts:
            # 每个 push_event 块尾部都带租户键注入（禁只改声明不接线）
            assert "tenant_id=identity.tenant_id" in src[j : j + 900]
