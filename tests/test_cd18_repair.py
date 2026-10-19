#!/usr/bin/env python3
"""CD-⑱ 常驻横幅/统计面消费键深挖+收口（UI 收编真源键，真源零改动）回归锁死。

背景（CD-⑰ 诚实边界①留档候选「常驻横幅 SystemInfo 显示键深挖（'undefined/undefined'
疑同型漂移）」=最新周期日志候选首件）：system 页 tab 条上方常驻横幅（System Status
Banner）+Quick Stats 统计条逐键深挖，同根扩面到 overview tab 运行时信息/数据统计
两面板（CD-⑥「同页消费键深挖扩面」先例）。真源三面（源码+live 双实证）：
- GET /api/health（src/main.py health）恰 6 键 {component, database:{type,status},
  knowledge_entries, status, uptime_check_ms, version}；
- GET /api/system/overview 恰 9 键（organs{healthy_count,total_count,...}、
  metrics{cpu_percent, memory{used_mb,...}}、knowledge{total_entries}、...）；
- GET /api/system/quick（system_overview.system_quick_status）恰 4 键
  {status, db, version, timestamp}——超轻量状态口非统计口。

修前（confirmed_drift 全体=QuickOverview 11 键打在 /api/system/quick 上）：
- organs_healthy/organs_total → 「器官 undefined/undefined」（CD-⑰ e2e 观测症状）；
- knowledge_entries/active_agents/recent_sessions → undefined 假渲染；
- cpu_percent/memory_mb → （quick.x || 0）假 0（'0.0%'/'0 MB'）；
- SystemInfo.database = （o as any)?.database || 'SQLite'——overview 无 database 键，
  硬编码 fabrication 遮蔽打错面（真源键=h.database.type）；
- SystemInfo.uptime 双真源恒不产出=死显示键恒 '—'（uptime_check_ms=健康检查耗时，
  非运行时间，禁冒充）。

收口（真源零改动=CD 系列 wire 权威纪律；CD-⑥/CD-⑪「改指真源」+CD-④/⑮「死显示面
清零」先例按「真源键在场与否」二分）：
- 真源键在场→改指真源：器官→organs.healthy_count/total_count、知识条目→
  knowledge.total_entries、CPU→metrics.cpu_percent、内存→metrics.memory.used_mb、
  数据库→health.database.type（fabrication 清零）；
- 真源不产出→死显示清零（活跃Agent/近期会话卡与行、运行时间行、banner 运行 span、
  QuickOverview/formatUptime/Clock/Globe 死声明/helper/import 双清零，grid 8→6 列）+
  恢复显示=真源补现候选 pending-decisions ㉘a（uptime 语义）/㉘b（活跃Agent/近期
  会话语义）不自决；
- /api/system/quick 死消费面清零（修后 4 键零显示消费，端点真源零改动保留）。

【勘误/契约随更新（DEC-㉘a-①，2026-10-20）】㉘a 按 DEC 自决先例取 B1（OpenSoul 进程
运行时长）真源补现先行执行：/api/system/overview 加性键 uptime_seconds（与 /metrics
opensoul_uptime_seconds 同走 metrics_api.uptime_seconds() 单一源）+UI 复原 banner「运行」
span/运行时信息「运行时间」行/formatUptime（真 0 显示 0 分钟，CD-⑳/㉚ 纪律）/Clock import
（CD-⑱ 移除件复原）。证据锁纪律（㉕/㉗）：原文「双真源恒不产出 uptime」勘误为「修前恒
不产出」——uptime/formatUptime/「运行时间」/banner/Clock 从死键锁退出（复活=真消费），
活跃Agent/近期会话/Globe 仍死（㉘b 待拍）锁维持；health 恒不产出 uptime 锁维持（uptime
落 overview 非 health，uptime_check_ms 禁冒充语义零变化）。

断言四层（禁自嗨恒真断言）：
①源码消费面：死键/死显示/死 helper/死 import/假 0/硬编码 fabrication 清零 + 真源键
  真消费 + 消费访问⊆真源键（防再发明键）；
②行为桥：三真源真跑形状锁死（quick 恒不产出统计键、health 恒不产出 uptime、overview
  消费键⊆产出键+uptime_seconds 真产出、metrics 降级形状恰 {error}='—' 占位非假 0 的
  真源依据）；
③真源锚点：quick/health/overview 端点源码锚点（单一源）；
④census 真跑真仓：死键 findings 全消失（工具边界如实记：Promise.allSettled 解构形
  三调用点 census 不提取=CD-⑨ 同族既有工具边界，非本拍引入）。
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "system" / "page.tsx"

_TOOL = REPO / "scripts" / "census_contract_drift.py"
_spec = importlib.util.spec_from_file_location("cdc_cd18_repair", _TOOL)
assert _spec is not None and _spec.loader is not None
cdc = importlib.util.module_from_spec(_spec)
sys.modules["cdc_cd18_repair"] = cdc
_spec.loader.exec_module(cdc)

# 真源 GET /api/health 产出键（src/main.py health，live 实证一致）
TRUE_HEALTH_KEYS = (
    "component",
    "database",
    "knowledge_entries",
    "status",
    "uptime_check_ms",
    "version",
)
# 真源 GET /api/system/quick 产出键（system_overview.system_quick_status，live 实证一致）
TRUE_QUICK_KEYS = ("status", "db", "version", "timestamp")
# 修前死显示/死声明键（/api/system/quick 恒不产出的统计键 + SystemInfo/QuickOverview 死键）
# 【DEC-㉘a-① 契约随更新】uptime/formatUptime 退出死键锁（真源补现 overview.uptime_seconds
# 后复活为真消费/真 helper）；QuickOverview 与活跃Agent/recent_sessions 等 ㉘b 待拍死键维持。
DEAD_KEYS = (
    "organs_healthy",
    "organs_total",
    "knowledge_entries",
    "active_agents",
    "recent_sessions",
    "memory_mb",
    "disk_usage",
    "QuickOverview",
)
# 修后 UI 对 /api/system/overview 的合法消费访问（前缀形，stats.<prefix>）
_ALLOWED_STATS_ACCESS = (
    "organs.healthy_count",
    "organs.total_count",
    "knowledge.total_entries",
    "metrics.cpu_percent",
    "metrics.memory.used_mb",
)


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（历史记述/裁决注释里的键名不算消费形）。"""
    return re.sub(r"//.*$", "", _src(), flags=re.M)


# ── ① 源码消费面 ───────────────────────────────────────────────


def test_dead_keys_zero_residue():
    """死键消费形/声明形/死 helper 双清零（CD-⑦ 纪律）。"""
    code = _code()
    for key in DEAD_KEYS:
        assert key not in code, f"死键残留: {key}"


def test_dead_display_labels_removed():
    """死显示面（假 0/undefined 载体）整体移除禁假渲染（CD-④ 纪律）。

    【DEC-㉘a-① 契约随更新】「运行时间」行与 banner「运行」span 随真源补现复活
    （修复面锚点=回归先炸）；活跃Agent/近期会话仍死（㉘b 待拍）锁维持。
    """
    code = _code()
    for label in ("活跃Agent", "活跃 Agent", "近期会话"):
        assert label not in code, f"死显示行/卡残留: {label}"
    # ㉘a 修复面锚点：banner「运行」span + 运行时信息「运行时间」行复活（真消费）
    assert re.search(r"运行 \{formatUptime\(info\?\.uptime_seconds\)\}", code), (
        "banner 运行 span 未复活（㉘a 恢复面回退先炸）"
    )
    assert "{ label: '运行时间', value: formatUptime(info?.uptime_seconds) }" in code, (
        "运行时信息「运行时间」行未复活（㉘a 恢复面回退先炸）"
    )


def test_dead_icon_imports_cleared():
    """死卡/死 span 图标 Globe import+消费清零（CD-⑰ Zap 死 import 清零纪律）。

    【DEC-㉘a-① 契约随更新】Clock 随 banner「运行」span 复活为真消费（修复面锚点）；
    Globe 仍死（近期会话卡 ㉘b 待拍）锁维持。
    """
    code = _code()
    assert not re.search(r"\bGlobe\b", code), "死 import Globe 残留"
    assert re.search(r"\bClock\b", code), "Clock import 未复活（㉘a banner 修复面回退先炸）"


def test_hardcoded_fabrication_cleared():
    """'SQLite' 硬编码 fabrication 清零 + 假 0（||0/??0）清零。"""
    code = _code()
    assert "'SQLite'" not in code, "database 硬编码 fabrication 残留"
    assert "|| 0" not in code, "假 0 回落残留"
    assert "?? 0" not in code, "假 0 回落残留"


def test_true_source_keys_really_consumed():
    """真源键真消费（改指真源的落点）：health.database.type 与 overview 四组键。"""
    code = _code()
    assert "database?.type" in code, "health.database.type 未消费（数据库显示打错面未收）"
    for chain in (
        "stats.organs.healthy_count",
        "stats.organs.total_count",
        "stats.knowledge.total_entries",
        "stats.metrics.cpu_percent",
        "stats.metrics.memory?.used_mb",
    ):
        assert chain in code, f"真源键未消费: {chain}"


def test_uptime_true_source_key_consumed():
    """【DEC-㉘a-①】overview.uptime_seconds 真源加性键真消费（两显示恰 2 消费点）。"""
    code = _code()
    assert "uptime_seconds: (o as any)?.uptime_seconds" in code, "setInfo 装配未收 uptime_seconds"
    assert code.count("formatUptime(info?.uptime_seconds)") == 2, (
        "uptime_seconds 消费点数漂移（banner+运行时间行恰 2 处）"
    )


def test_stats_consumed_access_subset_of_true_source():
    """stats.* 消费访问 ⊆ 真源产出键路径（防未来再发明键=此桥先炸）。"""
    code = _code().replace("?.", ".")
    hits = [m.start() for m in re.finditer(r"stats\.", code)]
    assert hits, "stats 消费点为空（提取失效=断言空转）"
    for i in hits:
        tail = code[i + len("stats.") :]
        assert any(tail.startswith(a) for a in _ALLOWED_STATS_ACCESS), (
            f"stats 消费访问真源不产出: {tail[:40]!r}"
        )


def test_info_consumed_keys_subset_of_assembly():
    """info.* 消费键恰 {status,version,database,uptime_seconds}（uptime 死键复活后
    【DEC-㉘a-① 契约随更新】=真源补现键，再发明键先炸）。"""
    code = _code()
    consumed = set(re.findall(r"info\??\.([A-Za-z_$][\w$]*)", code))
    assert consumed, "info 消费键为空（提取失效=断言空转）"
    assert consumed == {"status", "version", "database", "uptime_seconds"}, (
        f"info 消费键集漂移: {consumed}"
    )


def test_fetch_wiring_only_true_sources():
    """取数接线：/api/health + /api/system/overview 双真源，/api/system/quick 死消费面清零。"""
    code = _code()
    assert "/api/health" in code, "/api/health 取数缺失"
    assert "/api/system/overview" in code, "/api/system/overview 取数缺失"
    assert "/api/system/quick" not in code, "/api/system/quick 死消费面残留（4 键零显示消费）"


# ── ② 行为桥（真源实跑，真源零改动） ────────────────────────────


def test_quick_shape_lock_never_produces_stat_keys():
    """/api/system/quick 真跑形状锁死：恰 4 键、恒不产出统计键。

    未来真源补现统计键（㉘ 拍板件）此桥先炸=CD-③「真源形状变了→裁决失效须复挖」
    证据锁；改道须显式改本锁。
    """
    from src.api import system_overview

    resp = asyncio.run(system_overview.system_quick_status())
    assert set(resp) == set(TRUE_QUICK_KEYS), f"真源形状变了须复挖: {sorted(resp)}"
    for key in DEAD_KEYS:
        assert key not in resp, f"{key} 出现=真源形状变了须复挖"


def test_health_shape_lock_never_produces_uptime():
    """/api/health 真跑形状锁死：恰 6 键、恒不产出 uptime（uptime_check_ms 禁冒充）。"""
    from src.main import health as soul_health

    resp = asyncio.run(soul_health())
    assert set(resp) == set(TRUE_HEALTH_KEYS), f"真源形状变了须复挖: {sorted(resp)}"
    assert "uptime" not in resp, "uptime 出现=真源形状变了须复挖"
    assert "uptime_check_ms" in resp, "uptime_check_ms 锚点漂移（健康检查耗时≠运行时间）"
    assert isinstance(resp["database"], dict) and "type" in resp["database"], (
        "health.database 嵌套形锚点漂移（database.type 消费依据）"
    )


def test_overview_consumed_keys_subset_of_produced():
    """/api/system/overview 真跑：UI 消费键⊆产出键（行为桥）+【DEC-㉘a-①】uptime_seconds
    加性键真产出（真源补现锚点=键消失先炸）。"""
    from src.api import system_overview

    resp = asyncio.run(system_overview.system_overview())
    assert "healthy_count" in resp["organs"] and "total_count" in resp["organs"], (
        "organs 消费依据键缺席（形状变了须复挖）"
    )
    assert "cpu_percent" in resp["metrics"], "metrics.cpu_percent 消费依据键缺席"
    assert "used_mb" in resp["metrics"].get("memory", {}), "metrics.memory.used_mb 消费依据键缺席"
    assert "total_entries" in resp["knowledge"], "knowledge.total_entries 消费依据键缺席"
    assert "uptime_seconds" in resp, "uptime_seconds 加性键缺席（㉘a 真源补现回退先炸）"
    assert isinstance(resp["uptime_seconds"], (int, float)), (
        "uptime_seconds 非数值（形状变了须复挖）"
    )


def test_overview_interface_subset_of_produced():
    """OverviewStats/SystemInfo 声明键 ⊆ 真源产出键（禁声明假键）。"""
    src = _src()
    ov = re.search(r"interface OverviewStats \{([^}]*(?:\{[^}]*\}[^}]*)*)\}", src)
    assert ov, "OverviewStats 接口未找到（提取失效=断言空转）"
    declared = set(re.findall(r"([A-Za-z_$][\w$]*):", ov.group(1)))
    assert declared, "接口键提取为空（提取失效=断言空转）"

    from src.api import system_overview

    resp = asyncio.run(system_overview.system_overview())

    def _flatten(d, acc):
        for k, v in d.items():
            acc.add(k)
            if isinstance(v, dict):
                _flatten(v, acc)
        return acc

    produced = _flatten(resp, set())
    for k in declared:
        assert k in produced, f"OverviewStats 声明键真源不产出: {k}"

    si = re.search(r"interface SystemInfo \{([^}]*)\}", src)
    assert si, "SystemInfo 接口未找到（提取失效=断言空转）"
    si_keys = set(re.findall(r"^\s+([A-Za-z_$][\w$]*)\??:", si.group(1), re.M))
    assert si_keys, "SystemInfo 键提取为空（提取失效=断言空转）"
    for k in si_keys:
        assert k in resp or k in TRUE_HEALTH_KEYS, f"SystemInfo 声明键真源不产出: {k}"


def test_metrics_degraded_shape_justifies_placeholder_not_fake_zero(monkeypatch):
    """metrics 降级形状恰 {error}：cpu_percent/memory 可缺席→'—' 占位非假 0 的真源依据。"""
    from src.api import system_overview

    async def _boom():
        raise RuntimeError("cd18 probe")

    monkeypatch.setattr(system_overview, "_get_system_metrics", _boom)
    resp = asyncio.run(system_overview.system_overview())
    assert set(resp["metrics"]) == {"error"}, f"metrics 降级形状漂移须复挖: {resp['metrics']}"
    code = _code()
    assert "!= null ?" in code, "缺席='—' 占位守卫不在场（降级路径会回假渲染）"


# ── ③ 真源锚点（单一源） ───────────────────────────────────────


def test_true_source_anchors():
    """quick/health/overview 端点源码锚点：真源形状变更=先炸此锁再改 UI。"""
    overview_src = (REPO / "src" / "api" / "system_overview.py").read_text(encoding="utf-8")
    assert '@router.get("/quick")' in overview_src, "quick 端点锚点漂移"
    assert "async def system_quick_status" in overview_src, "quick 端点锚点漂移"
    main_src = (REPO / "src" / "main.py").read_text(encoding="utf-8")
    assert '@app.get("/api/health")' in main_src, "health 端点锚点漂移"
    assert 'stats["uptime_check_ms"]' in main_src, "uptime_check_ms 锚点漂移"
    assert '@router.get("/overview")' in overview_src, "overview 端点锚点漂移"


# ── ④ census 工具真跑真仓 ─────────────────────────────────────


@pytest.fixture(scope="module")
def census_report() -> dict:
    return cdc.compare(REPO / "admin-ui" / "app", REPO / "src")


def test_census_dead_keys_findings_gone(census_report):
    """修后 findings：死键从 system 页 findings 全消失。

    工具边界如实记：fetchOverview 的 Promise.allSettled 解构形三调用点（health/
    overview/quick）census 不提取（CD-⑨ 同族既有工具边界，修前修后同边界非本拍
    引入）——本锁覆盖 census 能看到的调用点面；三调用点的消费形由①②层源码/行为
    桥锁死。
    """
    findings = [f for f in census_report["findings"] if f["page"].endswith("system/page.tsx")]
    assert findings, "system 页 findings 为空（工具失效=断言空转）"
    for f in findings:
        for k in f["key"]:
            for dead in DEAD_KEYS:
                assert k != dead, f"死键仍在 findings: {f['path']} {f['key']}"


def test_census_tool_still_indexes_true_endpoints(census_report):
    """工具仍索引三真源端点（工具活性锁，防 compare 空转）。"""
    blob = str(census_report.get("endpoints_indexed", ""))
    assert "system" in blob or census_report["counts"], "census 工具空转（提取失效=断言空转）"
