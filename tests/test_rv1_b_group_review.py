"""RV-①c B 组逐行深挖复核——判定表契约测试（审计即切片本体，零行为改动）。

锁三件事：
1. 判定覆盖完备：任一条目必有判定（缺项如实抛，禁静默默认）；
2. 判定前提在场：B_fact_hidden 的「活事实」定性依赖的源码标记（注册面/凭据面/无重建面）
   真在源码里——前提变化（如建设件落地持久化）须按契约随更新复核判定（F4b-⑯f 先例）；
3. 判定确定性：同输入两跑逐键相等。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_TOOL = ROOT / "scripts" / "review_restart_volatile_b_group.py"
spec = importlib.util.spec_from_file_location("rv1c_tool", _TOOL)
assert spec is not None and spec.loader is not None
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

REVIEW_PATH = ROOT / "data" / "restart_volatile_b_group_review_20261022.json"


def _entry(module: str, cls: str | None, attr: str, auto: str = "volatile") -> dict:
    return {
        "kind": "instance_registry",
        "module": module,
        "class": cls,
        "attr": attr,
        "container": "dict",
        "line": 1,
        "write_signals": [],
        "load_signals": [],
        "auto_verdict": auto,
    }


def test_未归类条目如实抛禁静默默认():
    with pytest.raises(ValueError, match="未归类条目"):
        tool.classify(_entry("src/nonexistent/mod.py", "Ghost", "_x"))


def test_A组规则判法单一源():
    a = tool.classify(_entry("src/gland/token_meter.py", "TokenMeter", "_records"))
    assert a["verdict"] == "A_rv1b"
    # download_plugins._plugins 显式排除（启动扫描重建=B）
    p = tool.classify(_entry("src/api/download_plugins.py", "DownloadManager", "_plugins"))
    assert p["verdict"] == "B_derived"
    # 同模块其余 attr 仍 A（RV-①b-⑥-3 插件任务面）
    r = tool.classify(_entry("src/api/download_plugins.py", "DownloadPluginX", "_running"))
    assert r["verdict"] == "A_rv1b"


def test_C组规则_persisted与load_only旁证():
    for auto in ("persisted_evidence", "load_only"):
        row = tool.classify(_entry("src/any/mod.py", "Cls", "_state", auto=auto))
        assert row["verdict"] == "C_persisted"


FACT_KEYS = [
    ("src/immune/audit.py", "AuditLogger", "_log", "RV-①b-⑧", False),
    ("src/pulse/timer.py", "PulseEngine", "_signals", "RV-①b-⑨", False),
    ("src/gland/router.py", "ModelRouter", "providers", "RV-①b-⑩", False),
    ("src/immune/intrusion.py", "IntrusionDetector", "_threat_history", "RV-①b-⑪", False),
    ("src/immune/intrusion.py", "IntrusionDetector", "_login_attempts", "RV-①b-⑪", False),
    ("src/will/dag_planner.py", "DAGPlanner", "_plans", "RV-①b-⑫", False),
    ("src/healer/diagnose.py", "OrganHealer", "_history", "RV-①b-⑬", False),
    ("src/heredity/version_registry.py", "VersionRegistry", "_components", "RV-①b-⑭", False),
    ("src/heredity/version_registry.py", "VersionRegistry", "_migrations", "RV-①b-⑭", False),
    ("src/heredity/version_registry.py", "VersionRegistry", "_changelog", "RV-①b-⑭", False),
    ("src/heredity/migration.py", "MigrationEngine", "_schemas", "RV-①b-⑭", False),
    ("src/heredity/migration.py", "MigrationEngine", "_scripts", "RV-①b-⑭", False),
    ("src/healer/diagnose.py", "OrganHealer", "_custom_handlers", None, False),  # runtime 对照
    ("src/link/connector.py", "IntegrationManager", "_connectors", "RV-①b-⑮", True),
    ("src/api/soma_connector.py", "ComponentRegistry", "_components", "RV-①b-⑮", False),
    ("src/api/soma_connector.py", "ComponentRegistry", "_tokens", "RV-①b-⑮", True),
]


def test_fact矩阵逐条判定与候选挂载():
    for mod, cls, attr, candidate, credential in FACT_KEYS:
        row = tool.classify(_entry(mod, cls, attr))
        if candidate is None:
            assert row["verdict"] != "B_fact_hidden", f"{mod} {attr} 不应是 fact"
            continue
        assert row["verdict"] == "B_fact_hidden", f"{mod} {attr} 应为 B_fact_hidden"
        assert row["candidate"] == candidate
        assert row["credential"] is credential
        assert row["candidate"] in tool.CANDIDATES


def test_凭据面恰两条():
    cred = sorted(k for k, v in tool.ENTRY_OVERRIDES.items() if v[3])
    assert cred == sorted(
        [
            ("src/link/connector.py", "IntegrationManager", "_connectors"),
            ("src/api/soma_connector.py", "ComponentRegistry", "_tokens"),
        ]
    )


def test_非fact条目禁挂候选_fat_hidden必挂有效候选(monkeypatch):
    bad = dict(tool.ENTRY_OVERRIDES)
    bad[("src/will/dag_planner.py", "DAGPlanner", "_stats")] = (
        "B_derived",
        "违规注入",
        "RV-①b-⑧",
        False,
    )
    monkeypatch.setattr(tool, "ENTRY_OVERRIDES", bad)
    with pytest.raises(ValueError, match="非 fact 条目禁挂候选"):
        tool.classify(_entry("src/will/dag_planner.py", "DAGPlanner", "_stats"))
    monkeypatch.undo()
    # 判定表自身完整性：fact 必有候选、非 fact 必无候选（classify 内联校验，遍历全表）
    for key, (verdict, _reason, candidate, _cred) in tool.ENTRY_OVERRIDES.items():
        if verdict == "B_fact_hidden":
            assert candidate in tool.CANDIDATES, key
        else:
            assert candidate is None, key


def test_判定确定性两跑逐键相等():
    census = {
        "entries": [
            _entry("src/immune/audit.py", "AuditLogger", "_log"),
            _entry("src/vein/cache.py", "CacheManager", "_cache"),
            _entry("src/nest/tenant.py", "TenantManager", "_tenants", auto="write_only"),
            _entry(
                "src/will/engine.py", "WorkflowEngine", "_executions", auto="persisted_evidence"
            ),
        ]
    }
    a = tool.build_review(census)
    b = tool.build_review(census)
    assert a == b
    assert a["total"] == 4
    assert a["stats"]["B_fact_hidden"] == 1
    assert a["stats"]["C_persisted"] == 2  # 报告人工定性 _tenants 覆盖 write_only 粗判


def test_类级粗判余量覆盖():
    for key, (verdict, reason) in tool.CLASS_VERDICTS.items():
        mod, cls = key
        # download_plugins 整模块属 A 规则（除 _plugins），用 _plugins 触达类级判定
        attr = (
            "_plugins"
            if (mod, cls) == ("src/api/download_plugins.py", "DownloadManager")
            else "_whatever"
        )
        row = tool.classify(_entry(mod, cls, attr))
        assert row["verdict"] == verdict
        assert reason


# ── 判定前提源码标记锁（前提变化→按契约随更新复核判定）────────────────────────


def _src(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_前提_版本注册面仅种子import期重建_API注册零重建():
    src = _src("src/api/heredity.py")
    assert "SEED_COMPONENTS" in src
    # API 注册面在场（用户/外部注册的事实写点）
    assert '@router.post("/components")' in src
    # RV-①b-⑭-1 已落地（2026-10-23 契约随更新 F4b-⑯f 先例）：API 注册面已
    # 持久化（VersionRegistry(persist=...) 随写落盘+首触懒回载），前提「API 注册
    # 零重建」已收口；SEED 28 组件从 import 期无条件播种改随首触懒播只补缺
    # （seed_components=SEED_COMPONENTS 单一源），「仅种子 import 期重建」前提
    # 同步收口（种子跨重启由行回载+只补缺非 import 重建）。
    assert "registry = VersionRegistry(" in src
    assert "seed_components=SEED_COMPONENTS" in src
    assert "persist=RegistryPersistence()" in src


def test_前提_脉冲信号API创建面与bootstrap一次性语义():
    assert '@router.post("/signals")' in _src("src/api/pulse.py")
    assert "def create_signal" in _src("src/api/pulse.py")
    main_src = _src("src/main.py")
    assert "if not bs.is_bootstrapped:" in main_src  # bootstrap 一次性=信号无重启重建
    # RV-①b-⑨ 已落地（2026-10-23 契约随更新 F4b-⑯f 先例）：api 接线从
    # `engine = PulseEngine()` 改为带 persist（信号随变更落盘+懒回载），前提
    # 「信号无重启重建」已收口（跨重启由行回载非 bootstrap 重建）；bootstrap
    # 一次性标记本体前提不变。
    assert "engine = PulseEngine(persist=" in _src("src/api/pulse.py")


def test_前提_凭据随行与审计留痕环():
    assert "callback_token" in _src("src/link/connector.py")
    assert "secret_token" in _src("src/api/soma_connector.py")
    # RV-①b-⑧ 已落地（2026-10-23 契约随更新 F4b-⑯f 先例）：api 接线从
    # `audit = AuditLogger()` 改为带 persist（留痕随写落盘），前提「留痕只活
    # 内存」已收口；环本体前提（deque ring 仍纯内存回填）不变。
    assert "audit = AuditLogger(persist=" in _src("src/api/immune.py")
    assert "deque(maxlen=max_entries)" in _src("src/immune/audit.py")


def test_前提_计划by_ID消费与providers注册写点():
    assert "self._plans[plan_id] = plan" in _src("src/will/dag_planner.py")
    assert "get_plan(" in _src("src/api/will.py")
    # RV-①b-⑫ 已落地（2026-10-23 契约随更新 F4b-⑯f 先例）：api 接线从
    # `dag_planner = DAGPlanner()` 改为带 persist（计划注册表随写落盘+懒回载），
    # 前提「计划只活内存」已收口；by-ID 消费前提（get_plan 闸门仍真源）不变。
    assert "dag_planner = DAGPlanner(persist=" in _src("src/api/will.py")
    assert "self.providers[name] = cfg" in _src("src/gland/router.py")


def test_前提_检测留痕写点与heartbeat缺行fail_closed():
    assert "self._login_attempts[ip].append" in _src("src/immune/intrusion.py")
    # RV-①b-⑪ 已落地（2026-10-23 契约随更新 F4b-⑯f 先例）：api 接线从
    # `intrusion = IntrusionDetector()` 改为带 persist（威胁史/爆破证据窗随写
    # 落盘+懒回载），前提「过程史/证据窗只活内存」已收口；检测窗本体前提
    # （_login_attempts 内存 deque 窗仍为判定真源）不变。
    assert "intrusion = IntrusionDetector(persist=" in _src("src/api/immune.py")
    nerve = _src("src/api/nerve.py")
    hb = nerve[nerve.index("def heartbeat") : nerve.index("def heartbeat") + 200]
    assert "return False" in hb


@pytest.mark.skipif(not REVIEW_PATH.exists(), reason="复核工件不在（data/ 本地件）")
def test_工件回读对账_与判定表逐键一致():
    on_disk = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    census = json.loads(
        (ROOT / "data" / "restart_volatile_census_20261021.json").read_text(encoding="utf-8")
    )
    rebuilt = tool.build_review(census)
    assert on_disk == rebuilt
    assert on_disk["total"] == sum(on_disk["stats"].values()) == 124
    assert on_disk["stats"]["B_fact_hidden"] == 15
    assert len(on_disk["fact_keys"]) == 15
    assert len(on_disk["credential_keys"]) == 2
