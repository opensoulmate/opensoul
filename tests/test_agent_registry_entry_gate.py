"""agent_registry.get_entry by-ID 归属闸测试（DEC-⑦-⑮：agents get_entry 非活泄露面收口）。

修前（HEAD 源码实证）：get_entry 是 acp_agent_registry 的 by-ID 读唯一入口，零租户
闸——任何持有 agent_id 的调用方可读任意租户行全文（cmd/probe_detail/session_id）。
全仓生产消费=0（既有定性，潜在非活断点=非活泄露面）；收口=⑦-⑫ get_template 同款
单点归属闸：显式租户=跨租户回 None 与 not-found 同契约不差分泄露行存在性，None=
系统视图字节不变（既有唯一调用面 tests/test_search_agents_tenant_axis.py 的
get_entry("legacy") 系统视图用法零变化）。闸收在单一读源本体=未来消费方构造上
无法绕闸（⑦-⑩ get_meta 单点闸同款）。

本文件 tmp 全隔离（AGENT_REGISTRY_DB 归 tmp，绝不碰生产 data/agent_registry.db）。
"""

import sqlite3

import pytest

OLD_REGISTRY_DDL = (
    "CREATE TABLE acp_agent_registry ("
    "agent_id TEXT PRIMARY KEY, cmd TEXT NOT NULL DEFAULT '[]', "
    "registered INTEGER NOT NULL DEFAULT 0, session_ready INTEGER NOT NULL DEFAULT 0, "
    "probe_ok INTEGER, probe_detail TEXT, session_id TEXT, last_probe_at TEXT, "
    "probe_count INTEGER NOT NULL DEFAULT 0)"
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_REGISTRY_DB", str(tmp_path / "agent_registry.db"))
    import src.services.agent_registry as reg

    return {"reg": reg, "db": str(tmp_path / "agent_registry.db")}


def _seed(env, agent_id, tenant_id=None):
    """经真写点 _record 落行（行归属/劫持闸语义与生产写点同源）。"""
    return env["reg"]._record(
        agent_id,
        ["fake"],
        probe_ok=True,
        probe_detail="seeded ok",
        session_ok=True,
        session_id="s-" + str(agent_id),
        tenant_id=tenant_id,
    )


def _raw_row(env, agent_id):
    with sqlite3.connect(env["db"]) as c:
        c.row_factory = sqlite3.Row
        rows = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM acp_agent_registry WHERE agent_id=?", (str(agent_id),)
            )
        ]
    return rows[0] if rows else None


# ── 归属闸三向 / 不差分泄露 ─────────────────────────────────


class TestEntryGate:
    def test_gate_three_way(self, env):
        _seed(env, "gate-x", tenant_id="tenant-a")
        reg = env["reg"]
        own = reg.get_entry("gate-x", tenant_id="tenant-a")
        assert own is not None and own["agent_id"] == "gate-x"
        assert reg.get_entry("gate-x", tenant_id="tenant-b") is None
        assert reg.get_entry("gate-x", tenant_id="default") is None
        assert reg.get_entry("gate-x") is not None  # 系统视图字节不变

    def test_cross_tenant_same_contract_as_not_found(self, env):
        """跨租户回 None 与缺行同契约——不差分泄露行存在性（③c-1 纪律）。"""
        _seed(env, "gate-y", tenant_id="tenant-a")
        reg = env["reg"]
        assert reg.get_entry("gate-y", tenant_id="tenant-b") is None
        assert reg.get_entry("no-such", tenant_id="tenant-b") is None
        assert reg.get_entry("gate-y", tenant_id="tenant-a") is not None
        assert reg.get_entry("no-such", tenant_id="tenant-a") is None

    def test_gate_applies_to_unregistered_rows(self, env):
        """探测失败行（registered=0）同样受闸（⑦-⑤ 边界③ 语义延续）。"""
        env["reg"]._record(
            "gate-fail",
            ["fake"],
            probe_ok=False,
            probe_detail="probe failed",
            session_ok=False,
            tenant_id="tenant-a",
        )
        reg = env["reg"]
        entry = reg.get_entry("gate-fail", tenant_id="tenant-a")
        assert entry is not None and entry["registered"] is False
        assert reg.get_entry("gate-fail", tenant_id="tenant-b") is None

    def test_cross_tenant_read_zero_side_effect(self, env):
        """归属闸=纯读：跨租户拒绝后行一字不动（probe_count 不递增）。"""
        _seed(env, "gate-ro", tenant_id="tenant-a")
        before = _raw_row(env, "gate-ro")
        assert env["reg"].get_entry("gate-ro", tenant_id="tenant-b") is None
        assert _raw_row(env, "gate-ro") == before

    def test_system_view_shape_and_additive_key(self, env):
        """None=系统视图字节不变：行 dict 形状照旧+加性 tenant_id 归一值。"""
        _seed(env, "gate-shape", tenant_id="tenant-a")
        entry = env["reg"].get_entry("gate-shape")
        assert entry is not None
        assert entry["tenant_id"] == "tenant-a"  # 归一值=agent_row_tenant
        assert entry["agent_id"] == "gate-shape"
        assert entry["cmd"] == ["fake"]  # json 反序列化照旧
        assert entry["registered"] is True and entry["session_ready"] is True


# ── 兜底判法 / 迁移面 / 单一源消费 ─────────────────────────


class TestJudgmentAndMigration:
    def test_unkeyed_row_judged_default(self, env):
        """空白/缺失行键→'default' 兜底判法在闸内同源（写读同一判法）。"""
        assert env["reg"].get_entry("warmup") is None  # 触发 _conn DDL 自愈建表
        with sqlite3.connect(env["db"]) as c:
            c.execute(
                "INSERT INTO acp_agent_registry "
                "(agent_id, registered, session_ready, probe_ok, probe_detail, tenant_id) "
                "VALUES ('gate-unkeyed', 1, 1, 1, 'raw unkeyed', '')"
            )
            c.commit()
        reg = env["reg"]
        assert reg.get_entry("gate-unkeyed", tenant_id="default") is not None
        assert reg.get_entry("gate-unkeyed", tenant_id="tenant-a") is None
        assert reg.get_entry("gate-unkeyed")["tenant_id"] == "default"

    def test_legacy_backfill_row_gate(self, env, tmp_path, monkeypatch):
        """老库 ALTER 自愈回填 'default' 行照样受闸（存量行语义一致）。"""
        monkeypatch.setenv("AGENT_REGISTRY_DB", str(tmp_path / "legacy.db"))
        with sqlite3.connect(tmp_path / "legacy.db") as c:
            c.execute(OLD_REGISTRY_DDL)
            c.execute("INSERT INTO acp_agent_registry (agent_id, probe_count) VALUES ('legacy', 3)")
            c.commit()
        import src.services.agent_registry as reg

        entry = reg.get_entry("legacy")  # 触发 _conn 自愈（既有调用面字节不变）
        assert entry is not None and entry["tenant_id"] == "default"
        assert reg.get_entry("legacy", tenant_id="default") is not None
        assert reg.get_entry("legacy", tenant_id="tenant-a") is None

    def test_gate_consumes_single_source_judgment(self, env, monkeypatch):
        """闸判法真消费 agent_row_tenant 单一源（Gate2 接线硬证）：
        打桩判法回 'tenant-x' 时闸跟着桩走=消费单一判法而非行内私抄
        entry.get('tenant_id')。"""
        import src.services.agent_registry as reg

        _seed(env, "gate-src", tenant_id="tenant-a")
        seen: list = []

        def _spy(entry):
            seen.append(dict(entry))
            return "tenant-x"

        monkeypatch.setattr(reg, "agent_row_tenant", _spy)
        assert reg.get_entry("gate-src", tenant_id="tenant-x") is not None
        assert reg.get_entry("gate-src", tenant_id="tenant-a") is None
        assert seen  # 单一判法被真实调用

    def test_agent_id_str_coercion(self, env):
        """SQLite UUID/ID str() 铁律：非 str agent_id 往返一致。"""
        _seed(env, 123, tenant_id="tenant-a")
        reg = env["reg"]
        entry = reg.get_entry(123, tenant_id="tenant-a")
        assert entry is not None and entry["agent_id"] == "123"
        assert reg.get_entry(123, tenant_id="tenant-b") is None
        assert reg.get_entry("123", tenant_id="tenant-a") is not None
