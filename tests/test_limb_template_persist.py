"""RV-①b-⑦-3 RPA 用户模板持久化——写穿/懒回载/纯内存零变化/真实路径接线。

修前（RV-① A 组定性）：RPAExecutor._templates 全在进程内存（dict），用户自建
模板随 service 重启全清。本文件锁死：模板变更写穿单一点（状态快照表 upsert=
当前态）、首触懒回载（读/写双首触——不回载则覆盖劫持闸看不见既有行、删行后旧行
随重启复活）、回载只补缺不覆盖（builtin 种子=代码真源恒不被库行洗掉）、跨租户
劫持闸跨重启仍生效、纯内存缺省零变化、写/回载失败记账旁路、真实路径接线
（src.api.limb 唯一单例）。

数据面全 tmp（conftest LIMB_TEMPLATES_DB 全隔离+本文件显式 db_path），绝不写生产
data/limb_templates.db。
"""

import sqlite3

from src.limb.executor import RPAExecutor
from src.limb.tasks import Action, TaskTemplate, TemplateTenantDenied
from src.limb.template_persist import TemplatePersistence, default_db_path


def _template(template_id="tpl-u0000001", tenant_id="tenant-a", name="用户模板"):
    return TaskTemplate(
        template_id=template_id,
        name=name,
        description="描述·中文",
        category="custom",
        actions=[
            Action.from_dict(
                {
                    "action_type": "navigate",
                    "target": "https://x",
                    "value": "",
                    "description": "打开",
                    "timeout": 30,
                    "retry": 0,
                    "optional": False,
                    "condition": "window.x  1",
                    "metadata": {"note": "meta·中文"},
                }
            )
        ],
        variables=[{"name": "url", "description": "地址", "default": ""}],
        tags=["t1", "自动化"],
        tenant_id=tenant_id,
    )


# ─── 行语义（状态快照表：upsert=当前态；to_state 完整往返形） ─────────────────


def test_state_roundtrip_full_fields():
    """to_state/from_state 完整往返（含 condition/metadata/variables/tags/中文/
    tenant 归一）——展示形 to_dict 丢 condition/metadata，刻意不作落盘形。"""
    tpl = _template()
    state = tpl.to_state()
    back = TaskTemplate.from_state(state)
    assert back.to_state() == state
    assert back.actions[0].condition == "window.x  1"
    assert back.actions[0].metadata == {"note": "meta·中文"}
    assert back.variables == [{"name": "url", "description": "地址", "default": ""}]
    assert back.tags == ["t1", "自动化"]
    assert back.tenant_id == "tenant-a"
    # 展示形丢字段硬反证（禁拿 to_dict 当落盘形回载）
    shown = tpl.to_dict()
    assert "condition" not in shown["actions"][0]
    assert "metadata" not in shown["actions"][0]
    assert shown["action_count"] == 1  # 派生键只在展示形


def test_upsert_current_state(tmp_path):
    persist = TemplatePersistence(db_path=str(tmp_path / "t.db"))
    persist.upsert(template_id="tpl-a", tenant_id="tenant-a", category="custom", payload='{"v": 1}')
    persist.upsert(template_id="tpl-a", tenant_id="tenant-a", category="custom", payload='{"v": 2}')
    rows = persist.load_all()
    assert [r["v"] for r in rows] == [2]  # 当前态=一模板一行，最新覆盖
    assert persist.count() == 1


def test_delete_idempotent(tmp_path):
    persist = TemplatePersistence(db_path=str(tmp_path / "t.db"))
    persist.delete("tpl-missing")  # 缺行=无操作不抛
    persist.upsert(template_id="tpl-a", tenant_id="default", category="c", payload="{}")
    persist.delete("tpl-a")
    persist.delete("tpl-a")  # 幂等
    assert persist.count() == 0


def test_bad_rows_skipped_visible(tmp_path, caplog):
    db = tmp_path / "t.db"
    persist = TemplatePersistence(db_path=str(db))
    persist.upsert(
        template_id="tpl-good",
        tenant_id="default",
        category="c",
        payload='{"template_id": "tpl-good"}',
    )
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO limb_templates (template_id, tenant_id, category, payload) VALUES (?, ?, ?, ?)",
            ("tpl-bad-json", "default", "c", "{not json"),
        )
        conn.execute(
            "INSERT INTO limb_templates (template_id, tenant_id, category, payload) VALUES (?, ?, ?, ?)",
            ("tpl-bad-type", "default", "c", "[1,2]"),
        )
        conn.commit()
    with caplog.at_level("ERROR"):
        rows = persist.load_all()
    assert [r["template_id"] for r in rows] == ["tpl-good"]  # 坏行跳过不丢整库
    assert any("跳过" in r.message for r in caplog.records)  # 禁静默丢：真因可见


def test_load_all_does_not_create_db(tmp_path):
    db = tmp_path / "absent.db"
    persist = TemplatePersistence(db_path=str(db))
    assert persist.load_all() == []
    assert persist.count() == 0
    assert not db.exists()  # 回载只读不建库（写穿才建库）


# ─── 写穿（create/覆盖/delete 变更点落行） ──────────────────────────────────


def test_create_writes_row(tmp_path):
    db = tmp_path / "t.db"
    ex = RPAExecutor(template_persist=TemplatePersistence(db_path=str(db)))
    tpl = ex.create_template(_template().to_state(), tenant_id="tenant-a")
    assert TemplatePersistence(db_path=str(db)).count() == 1
    rows = TemplatePersistence(db_path=str(db)).load_all()
    assert rows[0]["template_id"] == tpl.template_id
    assert rows[0]["tenant_id"] == "tenant-a"  # 行归属落列
    assert rows[0]["name"] == "用户模板"


def test_overwrite_same_tenant_updates_row(tmp_path):
    db = tmp_path / "t.db"
    persist = TemplatePersistence(db_path=str(db))
    ex = RPAExecutor(template_persist=persist)
    ex.create_template(_template(name="v1").to_state(), tenant_id="tenant-a")
    ex.create_template(_template(name="v2").to_state(), tenant_id="tenant-a")
    assert persist.count() == 1
    assert persist.load_all()[0]["name"] == "v2"  # 当前态最新覆盖


def test_delete_template_drops_row(tmp_path):
    db = tmp_path / "t.db"
    persist = TemplatePersistence(db_path=str(db))
    ex = RPAExecutor(template_persist=persist)
    ex.create_template(_template().to_state(), tenant_id="tenant-a")
    assert ex.delete_template("tpl-u0000001", tenant_id="tenant-a") is True
    assert persist.count() == 0  # 删模板即删行
    assert ex.delete_template("tpl-u0000001", tenant_id="tenant-a") is False  # 幂等缺行


# ─── 重启回载（双实例=同库重启） ────────────────────────────────────────────


def test_restart_reloads_user_template(tmp_path):
    db = tmp_path / "t.db"
    ex1 = RPAExecutor(template_persist=TemplatePersistence(db_path=str(db)))
    ex1.create_template(_template().to_state(), tenant_id="tenant-a")
    ex2 = RPAExecutor(template_persist=TemplatePersistence(db_path=str(db)))
    tpl = ex2.get_template("tpl-u0000001", tenant_id="tenant-a")
    assert tpl is not None  # 重启模板不丢
    assert tpl.to_state() == _template().to_state()  # 逐键 parity（完整往返形）
    listed = [t["name"] for t in ex2.list_templates(tenant_id="tenant-a")]
    assert "用户模板" in listed  # 列表序=builtin 种子在前，不断言位置


def test_builtin_seeds_never_persisted_not_overwritten(tmp_path):
    db = tmp_path / "t.db"
    persist = TemplatePersistence(db_path=str(db))
    ex = RPAExecutor(template_persist=persist)
    # builtin 种子不进表（代码=真源）
    assert persist.count() == 0
    assert ex.get_template("tpl-form-fill") is not None
    # 库里伪造 builtin id 行也不洗掉代码种子（只补缺不覆盖=诚实边界④）
    forged = TaskTemplate(
        template_id="tpl-form-fill",
        name="伪造种子",
        description="",
        category="custom",
        actions=[],
        tenant_id="tenant-a",
    )
    persist.upsert(
        template_id="tpl-form-fill",
        tenant_id="tenant-a",
        category="custom",
        payload=__import__("json").dumps(forged.to_state(), ensure_ascii=False),
    )
    ex2 = RPAExecutor(template_persist=TemplatePersistence(db_path=str(db)))
    code_truth = ex2.get_template("tpl-form-fill")
    assert code_truth is not None
    assert code_truth.name == "表单填写"  # 代码真源胜出
    # builtin 写保护语义零变化（共享≠可写）
    try:
        ex2.create_template(_template(template_id="tpl-form-fill").to_state(), tenant_id="tenant-a")
        raise AssertionError("builtin 覆盖必须被拒")
    except TemplateTenantDenied:
        pass
    assert ex2.delete_template("tpl-form-fill", tenant_id="tenant-a") is False


def test_cross_tenant_recreate_gate_survives_restart(tmp_path):
    """回载先行于既有行判定：不回载则跨租户重建劫持闸看不见既有行（修前真断点）。"""
    db = tmp_path / "t.db"
    ex1 = RPAExecutor(template_persist=TemplatePersistence(db_path=str(db)))
    ex1.create_template(_template().to_state(), tenant_id="tenant-a")
    ex2 = RPAExecutor(template_persist=TemplatePersistence(db_path=str(db)))
    try:
        ex2.create_template(_template(name="劫持").to_state(), tenant_id="tenant-b")
        raise AssertionError("跨租户重建必须被拒")
    except TemplateTenantDenied:
        pass
    rows = TemplatePersistence(db_path=str(db)).load_all()
    assert rows[0]["name"] == "用户模板"  # 行零改写
    assert rows[0]["tenant_id"] == "tenant-a"


def test_lazy_load_once_and_row_does_not_wash_memory(tmp_path):
    db = tmp_path / "t.db"
    persist = TemplatePersistence(db_path=str(db))
    persist.upsert(
        template_id="tpl-u0000001",
        tenant_id="tenant-a",
        category="custom",
        payload=__import__("json").dumps(_template(name="v1").to_state(), ensure_ascii=False),
    )
    calls = []
    orig = persist.load_all

    def spy():
        calls.append(1)
        return orig()

    persist.load_all = spy
    ex = RPAExecutor(template_persist=persist)
    seen = ex.get_template("tpl-u0000001")
    assert seen is not None
    assert seen.name == "v1"
    ex.list_templates()
    ex.stats()
    assert len(calls) == 1  # 懒回载恰一次
    # 库行事后被改写（绕过实例直写）不洗内存在册态（只补缺不覆盖）
    persist.upsert(
        template_id="tpl-u0000001",
        tenant_id="tenant-a",
        category="custom",
        payload=__import__("json").dumps(_template(name="v2").to_state(), ensure_ascii=False),
    )
    ex.list_templates()
    kept = ex.get_template("tpl-u0000001")
    assert kept is not None
    assert kept.name == "v1"


def test_read_surfaces_see_reloaded_row(tmp_path):
    db = tmp_path / "t.db"
    ex1 = RPAExecutor(template_persist=TemplatePersistence(db_path=str(db)))
    ex1.create_template(_template().to_state(), tenant_id="tenant-a")
    ex2 = RPAExecutor(template_persist=TemplatePersistence(db_path=str(db)))
    names = [t["name"] for t in ex2.list_templates(tenant_id="tenant-a")]
    assert "用户模板" in names  # 公开读面触发回载可见
    assert ex2.stats(tenant_id="tenant-a")["templates"] == 5  # builtin 4 + 用户 1


# ─── 纯内存零变化 / 故障旁路 ────────────────────────────────────────────────


def test_pure_memory_zero_io(tmp_path, monkeypatch):
    monkeypatch.setenv("LIMB_TEMPLATES_DB", str(tmp_path / "never.db"))
    ex = RPAExecutor()  # 缺省 None=纯内存
    tpl = ex.create_template(_template().to_state(), tenant_id="tenant-a")
    assert ex.get_template(tpl.template_id, tenant_id="tenant-a") is not None
    assert ex.delete_template(tpl.template_id, tenant_id="tenant-a") is True
    assert not (tmp_path / "never.db").exists()  # 纯内存零落盘零建库


def test_write_fail_does_not_block(tmp_path):
    class Boom(TemplatePersistence):
        def upsert(self, **kwargs):
            raise RuntimeError("disk full")

    ex = RPAExecutor(template_persist=Boom(db_path=str(tmp_path / "t.db")))
    tpl = ex.create_template(_template().to_state(), tenant_id="tenant-a")
    assert tpl.name == "用户模板"  # 模板本体不受阻（记账旁路纪律）
    assert ex.get_template(tpl.template_id) is not None


def test_reload_fail_empty_start(tmp_path):
    class Boom(TemplatePersistence):
        def load_all(self):
            raise RuntimeError("db corrupt")

    db = tmp_path / "t.db"
    TemplatePersistence(db_path=str(db)).upsert(
        template_id="tpl-u0000001", tenant_id="tenant-a", category="c", payload="{}"
    )
    ex = RPAExecutor(template_persist=Boom(db_path=str(db)))
    assert ex.get_template("tpl-u0000001") is None  # 空模板起步
    tpl = ex.create_template(_template(template_id="tpl-new").to_state(), tenant_id="tenant-a")
    assert tpl is not None  # 新录照常


# ─── 真实路径接线（src.api.limb 唯一单例） ──────────────────────────────────


def test_api_singleton_wired(tmp_path):
    import src.api.limb as limb_api

    inst = limb_api.executor
    assert isinstance(inst._template_persist, TemplatePersistence)
    assert inst._templates_loaded is False or inst._templates_loaded is True  # 标记在场
    # 静态接线锁：生产单例构造真带 template_persist= 关键字
    import inspect

    src = inspect.getsource(limb_api)
    assert "template_persist=TemplatePersistence()" in src
    # env 解析真源（conftest LIMB_TEMPLATES_DB 指 tmp）
    assert default_db_path().endswith("limb_templates.db")
    # 真实路径：经生产单例建模板→行落 tmp 真源（绝不写生产库）
    tpl = inst.create_template(
        _template(template_id="tpl-wired001").to_state(), tenant_id="tenant-a"
    )
    assert TemplatePersistence().count() >= 1
    assert inst.get_template(tpl.template_id, tenant_id="tenant-a") is not None
