"""F3b-① OpenNest 持久化——租户真源/配额/用量/审计日志重启不丢 + 'default' 租户补位。

修前（F3 审计 live 实证）：TenantManager._tenants dict / IsolationEngine._access_log
list 全内存，建 tenant → systemctl 重启 → 0 tenants；生产数据 tenant_id 全='default'
而管理面无 'default' 租户（access-check → 404）。

隔离测试面：tmp 独立 nest.db（显式 db_path / NEST_DB env 双路覆盖），绝不写生产
data/nest.db（gene_client 同款纪律）。"重启"语义=同库路径新实例（进程重启等价）。
"""

import importlib
import sqlite3

import pytest


@pytest.fixture()
def nest_db(tmp_path):
    return str(tmp_path / "nest.db")


@pytest.fixture()
def nest_client(tmp_path, monkeypatch):
    """OpenNest API 隔离测试面：真实 router + tmp 独立库（gene_client 同款路线）。

    src.api.nest 延迟 import：模块 import 有单例构造副作用（建生产 data/nest.db），
    故 fixture 内先设 NEST_DB 再 import，且单例一律换成 tmp 实例。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("NEST_DB", str(tmp_path / "nest.db"))
    import src.api.nest as nest_api
    from src.nest.isolation import IsolationEngine
    from src.nest.tenant import TenantManager

    db = str(tmp_path / "nest.db")
    app = FastAPI()
    app.include_router(nest_api.router, prefix="/api/nest")
    monkeypatch.setattr(nest_api, "manager", TenantManager(db_path=db))
    monkeypatch.setattr(nest_api, "isolation", IsolationEngine(db_path=db))
    with TestClient(app) as c:
        yield c, db


class TestTenantPersistence:
    def test_create_survives_restart(self, nest_db):
        from src.nest.tenant import TenantManager

        mgr = TenantManager(db_path=nest_db)
        t = mgr.create(
            name="中文租户",
            tier="pro",
            owner_user_id="user-1",
            description="描述",
            tags=["a", "标签"],
            config={"nested": {"k": "值"}},
        )
        mgr2 = TenantManager(db_path=nest_db)  # 重启
        t2 = mgr2.get(t.tenant_id)
        assert t2 is not None
        assert t2.name == "中文租户"
        assert t2.tier.value == "pro"
        assert t2.status.value == "active"
        assert t2.namespace == f"ns-{t.tenant_id}"
        assert t2.owner_user_id == "user-1"
        assert t2.description == "描述"
        assert t2.tags == ["a", "标签"]
        assert t2.config == {"nested": {"k": "值"}}
        assert t2.created_at == pytest.approx(t.created_at)
        # namespace 索引随回载重建
        by_ns = mgr2.get_by_namespace(t2.namespace)
        assert by_ns is not None
        assert by_ns.tenant_id == t.tenant_id

    def test_update_survives_restart(self, nest_db):
        from src.nest.tenant import TenantManager

        mgr = TenantManager(db_path=nest_db)
        t = mgr.create(name="u1", tier="free")
        assert mgr.update(t.tenant_id, name="改名", tier="enterprise", status="suspended")
        mgr2 = TenantManager(db_path=nest_db)
        t2 = mgr2.get(t.tenant_id)
        assert t2 is not None
        assert t2.name == "改名"
        assert t2.tier.value == "enterprise"
        assert t2.status.value == "suspended"
        # tier 非 custom → 配额随 tier 重置并落盘
        assert t2.quota.max_storage_bytes == 107_374_182_400

    def test_delete_survives_restart(self, nest_db):
        from src.nest.tenant import TenantManager

        mgr = TenantManager(db_path=nest_db)
        t = mgr.create(name="d1", tier="free")
        assert mgr.delete(t.tenant_id) is True
        mgr2 = TenantManager(db_path=nest_db)
        assert mgr2.get(t.tenant_id) is None
        assert mgr2.get_by_namespace(f"ns-{t.tenant_id}") is None
        assert mgr2.delete(t.tenant_id) is False

    def test_custom_quota_survives_restart(self, nest_db):
        from src.nest.tenant import TenantManager

        mgr = TenantManager(db_path=nest_db)
        t = mgr.create(name="c1", tier="custom", custom_quota={"max_documents": 7})
        assert t.quota.max_documents == 7
        t2 = TenantManager(db_path=nest_db).get(t.tenant_id)
        assert t2 is not None
        assert t2.quota.max_documents == 7

    def test_usage_and_day_key_survive_restart(self, nest_db):
        from src.nest.tenant import TenantManager

        mgr = TenantManager(db_path=nest_db)
        t = mgr.create(name="u2", tier="free")
        mgr.record_usage(t.tenant_id, storage_delta=10, document_delta=2, api_calls=3, tokens=100)
        mgr2 = TenantManager(db_path=nest_db)  # 重启
        t2 = mgr2.get(t.tenant_id)
        assert t2 is not None
        assert t2.usage.storage_bytes == 10
        assert t2.usage.documents == 2
        assert t2.usage.api_calls_today == 3
        assert t2.usage.tokens_today == 100
        # day_key 落盘回载：同日累加不被 reset_daily 归零（day_key 若丢则得 1）
        mgr2.record_usage(t.tenant_id, api_calls=1)
        t3 = TenantManager(db_path=nest_db).get(t.tenant_id)
        assert t3 is not None
        assert t3.usage.api_calls_today == 4

    def test_quota_check_reads_loaded_usage(self, nest_db):
        from src.nest.tenant import TenantManager

        mgr = TenantManager(db_path=nest_db)
        t = mgr.create(name="q1", tier="free")
        mgr.record_usage(t.tenant_id, tokens=999)
        mgr2 = TenantManager(db_path=nest_db)
        result = mgr2.check_quota(t.tenant_id, "tokens", amount=1)
        assert result["allowed"] is True
        assert result["current"] == 999
        assert result["limit"] == 1_000_000


class TestDefaultTenant:
    def test_ensure_default_idempotent(self, nest_db):
        from src.nest.tenant import TenantManager

        mgr = TenantManager(db_path=nest_db)
        t1 = mgr.ensure_default_tenant()
        t2 = mgr.ensure_default_tenant()
        assert t1.tenant_id == t2.tenant_id == "default"
        assert t1 is t2
        assert mgr.list_tenants(limit=1000) and len(mgr.list_tenants(limit=1000)) == 1

    def test_default_survives_restart(self, nest_db):
        from src.nest.tenant import TenantManager

        mgr = TenantManager(db_path=nest_db)
        t = mgr.ensure_default_tenant()
        assert t.namespace == "ns-default"
        assert t.status.value == "active"
        mgr2 = TenantManager(db_path=nest_db)  # 重启：真源在库，回载即有
        t2 = mgr2.get("default")
        assert t2 is not None
        assert t2.namespace == "ns-default"
        # 修前 F3 审计硬证：access-check default → 404 Tenant not found；补位后真源对齐
        assert mgr2.check_quota("default", "documents", amount=1)["allowed"] is True


class TestAccessLogPersistence:
    def test_access_log_survives_restart(self, nest_db):
        from src.nest.isolation import IsolationEngine, ResourceType

        iso = IsolationEngine(db_path=nest_db)
        iso.check_access("ns-a", ResourceType.DOCUMENT, "doc-1", "read")
        blocked = iso.check_access("ns-a", ResourceType.DOCUMENT, "ns-b:doc-2", "write")
        assert blocked["allowed"] is False

        iso2 = IsolationEngine(db_path=nest_db)  # 重启
        entries = iso2.get_access_log()
        assert len(entries) == 2
        # 倒序：最新（blocked）在前
        assert entries[0]["allowed"] is False
        assert "namespace mismatch" in entries[0]["reason"]
        assert entries[0]["resource_id"] == "ns-b:doc-2"
        assert entries[1]["allowed"] is True
        assert entries[1]["resource_id"] == "doc-1"

        s = iso2.stats()
        assert s["total_access_checks"] == 2
        assert s["blocked_attempts"] == 1
        assert s["block_rate"] == 50.0
        assert s["by_resource_type"] == {"document": 2}

    def test_access_log_filters_after_restart(self, nest_db):
        from src.nest.isolation import IsolationEngine, ResourceType

        iso = IsolationEngine(db_path=nest_db)
        iso.check_access("ns-a", ResourceType.DOCUMENT, "doc-1", "read")
        iso.check_access("ns-a", ResourceType.AGENT, "ag-1", "write")
        iso.check_access("ns-a", ResourceType.DOCUMENT, "ns-b:doc-2", "read")
        iso2 = IsolationEngine(db_path=nest_db)
        assert len(iso2.get_access_log(allowed=False)) == 1
        assert len(iso2.get_access_log(action="read")) == 2
        assert len(iso2.get_access_log(tenant_id="ns-a")) == 3
        assert len(iso2.get_access_log(tenant_id="ns-b")) == 0
        assert {e["resource_type"] for e in iso2.get_access_log(limit=10)} == {
            "document",
            "agent",
        }

    def test_max_log_trim_semantics(self, nest_db):
        from src.nest.isolation import IsolationEngine, ResourceType

        iso = IsolationEngine(db_path=nest_db)
        iso._max_log = 3
        for i in range(5):
            iso.check_access("ns-a", ResourceType.FILE, f"f-{i}", "read")
        iso2 = IsolationEngine(db_path=nest_db)
        assert iso2.stats()["total_access_checks"] == 3
        entries = iso2.get_access_log(limit=10)
        assert [e["resource_id"] for e in entries] == ["f-4", "f-3", "f-2"]

    def test_store_json_fields_roundtrip(self, nest_db):
        from src.nest.store import NestStore

        store = NestStore(nest_db)
        store.upsert_tenant(
            {
                "tenant_id": "tenant-abc12345",
                "name": "n",
                "tier": "free",
                "status": "active",
                "namespace": "ns-tenant-abc12345",
                "owner_user_id": "u",
                "description": "d",
                "tags": ["标签"],
                "config": {"k": "值"},
                "metadata": {"m": {"deep": "元数据"}},
                "quota": {"max_documents": 3},
                "usage": {"documents": 1, "day_key": "2026-10-12"},
                "created_at": 1.0,
                "updated_at": 2.0,
            }
        )
        rows = store.load_tenants()
        assert rows[0]["metadata"] == {"m": {"deep": "元数据"}}
        assert rows[0]["tags"] == ["标签"]
        assert rows[0]["quota"] == {"max_documents": 3}
        # upsert 幂等（同 PK 更新不炸不重复）
        store.upsert_tenant(
            {
                "tenant_id": "tenant-abc12345",
                "name": "n2",
                "tier": "free",
                "status": "active",
                "namespace": "ns-tenant-abc12345",
                "owner_user_id": "u",
                "description": "d",
                "tags": [],
                "config": {},
                "metadata": {},
                "quota": {},
                "usage": {},
                "created_at": 1.0,
                "updated_at": 3.0,
            }
        )
        rows = store.load_tenants()
        assert len(rows) == 1
        assert rows[0]["name"] == "n2"


class TestApiRestartRoundtrip:
    def test_tenant_lifecycle_survives_restart(self, nest_client):
        client, db = nest_client
        import src.api.nest as nest_api
        from src.nest.isolation import IsolationEngine
        from src.nest.tenant import TenantManager

        resp = client.post(
            "/api/nest/tenants",
            json={
                "name": "api_tenant",
                "tier": "pro",
                "tags": ["t1"],
                "config": {"c": 1},
            },
        )
        assert resp.status_code == 200
        tid = resp.json()["tenant_id"]

        def restart():
            # 进程重启等价：同库路径全新单例（真源在库不在内存）
            nest_api.manager = TenantManager(db_path=db)
            nest_api.isolation = IsolationEngine(db_path=db)

        restart()
        resp = client.get(f"/api/nest/tenants/{tid}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "api_tenant"
        assert resp.json()["tier"] == "pro"

        assert (
            client.patch(f"/api/nest/tenants/{tid}", json={"status": "suspended"}).status_code
            == 200
        )
        assert (
            client.post(f"/api/nest/tenants/{tid}/usage", json={"api_calls": 5}).status_code == 200
        )
        restart()
        data = client.get(f"/api/nest/tenants/{tid}").json()
        assert data["status"] == "suspended"
        assert data["usage"]["api_calls_today"] == 5

        assert client.delete(f"/api/nest/tenants/{tid}").status_code == 200
        restart()
        assert client.get(f"/api/nest/tenants/{tid}").status_code == 404

    def test_default_tenant_api_and_audit_persist(self, nest_client):
        client, db = nest_client
        import src.api.nest as nest_api
        from src.nest.isolation import IsolationEngine
        from src.nest.tenant import TenantManager

        # 模拟启动补位（模块级 manager.ensure_default_tenant() 的运行时效果）
        nest_api.manager.ensure_default_tenant()
        resp = client.get("/api/nest/tenants/default")
        assert resp.status_code == 200
        assert resp.json()["namespace"] == "ns-default"

        # 修前 F3 审计硬证：access-check default → 404；补位后 200 真判定
        resp = client.post(
            "/api/nest/tenants/default/access-check",
            json={"resource_type": "document", "resource_id": "doc-x", "action": "read"},
        )
        assert resp.status_code == 200
        assert resp.json()["allowed"] is True

        # 重启：'default' 与审计日志都在库
        nest_api.manager = TenantManager(db_path=db)
        nest_api.isolation = IsolationEngine(db_path=db)
        assert client.get("/api/nest/tenants/default").status_code == 200
        resp = client.get("/api/nest/audit")
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["tenant_id"] == "ns-default"
        assert entries[0]["resource_id"] == "doc-x"

    def test_module_import_ensures_default(self, tmp_path, monkeypatch):
        """接线硬证：src.api.nest 模块级 manager.ensure_default_tenant() 真执行——
        NEST_DB 指向 tmp 后 reload 模块（=启动路径），'default' 必已补位且幂等。"""
        monkeypatch.setenv("NEST_DB", str(tmp_path / "nest.db"))
        import src.api.nest as nest_api

        importlib.reload(nest_api)
        assert nest_api.manager.get("default") is not None
        importlib.reload(nest_api)  # 二次启动幂等
        conn = sqlite3.connect(str(tmp_path / "nest.db"))
        try:
            n = conn.execute("SELECT COUNT(*) FROM tenants WHERE tenant_id = 'default'").fetchone()[
                0
            ]
        finally:
            conn.close()
        assert n == 1
