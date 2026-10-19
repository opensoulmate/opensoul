#!/usr/bin/env python3
"""DEC-㉘a-① system 页「运行时间」显示恢复——真源补现 uptime_seconds（B1 进程 uptime）
＋UI 复原（banner「运行」span／运行时信息「运行时间」行／formatUptime）回归锁死。

背景（pending-decisions ㉘a，CD-⑱ 深挖裁决留档件）：CD-⑱ 按 CD 系列 wire 权威纪律把
「运行时间」死显示清零（修前 formatUptime(parseInt(quick.uptime) || 0) 恒「0 分钟」=
死显示面；双真源修前恒不产出 uptime），恢复显示=真源补现候选。本拍按「别停工等批」
铁律＋DEC-①~㉗a 自决先例自决取 B1（OpenSoul 进程运行时长）先行执行：
- 真源补现：GET /api/system/overview 加性键 uptime_seconds（round(_,1)），与 /metrics
  opensoul_uptime_seconds 同走 metrics_api.uptime_seconds() 单一源（禁两套解释漂移）；
- UI 复原（CD-⑱ 移除件复原语义）：banner「运行」span＋运行时信息「运行时间」行＋
  formatUptime helper＋Clock import（复活=真消费非死 helper 回潮）；
- formatUptime 语义收口：缺席/非法='—' 占位、真 0 显示「0 分钟」（CD-⑳/㉚ 真 0 禁遮蔽，
  与移除前 helper 的 `!seconds` 把 0 折 '—' 刻意不同，如实记）。

断言四层（禁自嗨恒真断言）：
①真源面：overview.uptime_seconds 真产出数值（≈metrics_api.uptime_seconds() 同源对账）+
  health/quick 零变化面（uptime_check_ms 禁冒充、uptime 落 overview 非 health）；
②UI 消费面：两显示恰 2 消费点＋setInfo 装配无假 0 回落＋接口声明；
③行为桥：formatUptime helper 源码逐字提取经 node 真执行（缺席='—'/真 0='0分钟'/
  分钟/小时/天四形态）＋overview 真跑两次单调不减（进程时长真值非常数 fabrication）；
④真源锚点：metrics_api 单一源（定义+两消费点，`now - _start_time` 裸形清零）＋
  system_overview 装配锚点。
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "admin-ui" / "app" / "system" / "page.tsx"
METRICS_SRC = REPO / "src" / "api" / "metrics_api.py"
OVERVIEW_SRC = REPO / "src" / "api" / "system_overview.py"


def _src() -> str:
    return PAGE.read_text(encoding="utf-8")


def _code() -> str:
    """剥行注释后的真实代码（历史记述/裁决注释里的键名不算消费形）。"""
    return re.sub(r"//.*$", "", _src(), flags=re.M)


# ── ① 真源面 ───────────────────────────────────────────────────


def test_overview_produces_uptime_seconds_same_source():
    """overview 真跑 uptime_seconds 加性键：数值型、≈ metrics_api.uptime_seconds()（同源对账）。"""
    from src.api import metrics_api, system_overview

    resp = asyncio.run(system_overview.system_overview())
    assert "uptime_seconds" in resp, "uptime_seconds 加性键缺席（㉘a 真源补现回退先炸）"
    assert isinstance(resp["uptime_seconds"], (int, float)), "uptime_seconds 非数值"
    assert resp["uptime_seconds"] >= 0, "进程时长为负=形状坏了须复挖"
    assert abs(resp["uptime_seconds"] - metrics_api.uptime_seconds()) < 2.0, (
        "overview.uptime_seconds 与 metrics_api.uptime_seconds() 不同源（两套解释漂移）"
    )


def test_overview_uptime_monotonic_not_fabricated():
    """overview 真跑两次：uptime 单调不减（真进程时长非常数 fabrication）。"""
    from src.api import system_overview

    a = asyncio.run(system_overview.system_overview())["uptime_seconds"]
    b = asyncio.run(system_overview.system_overview())["uptime_seconds"]
    assert b >= a, f"uptime 回退（fabricated 常数或倒流）: {a} -> {b}"


def test_health_zero_change_no_uptime():
    """零变化面：/api/health 仍恰 6 键恒不产出 uptime（uptime_check_ms 禁冒充语义零变化）。"""
    from src.main import health as soul_health

    resp = asyncio.run(soul_health())
    assert set(resp) == {
        "component",
        "database",
        "knowledge_entries",
        "status",
        "uptime_check_ms",
        "version",
    }, f"health 形状变了须复挖: {sorted(resp)}"
    assert "uptime" not in resp, "uptime 打进 health=打错面（uptime_check_ms 禁冒充）"


def test_quick_zero_change_four_keys():
    """零变化面：/api/system/quick 仍恰 4 键（超轻量状态口非统计口，㉘ 恢复走 overview）。"""
    from src.api import system_overview

    resp = asyncio.run(system_overview.system_quick_status())
    assert set(resp) == {"status", "db", "version", "timestamp"}, (
        f"quick 形状变了须复挖: {sorted(resp)}"
    )


# ── ② UI 消费面 ─────────────────────────────────────────────────


def test_ui_two_display_points_consume_true_key():
    """banner「运行」span＋运行时信息「运行时间」行恰 2 消费点（复原面=回归先炸）。"""
    code = _code()
    assert re.search(r"运行 \{formatUptime\(info\?\.uptime_seconds\)\}", code), (
        "banner 运行 span 未复活"
    )
    assert "{ label: '运行时间', value: formatUptime(info?.uptime_seconds) }" in code, (
        "运行时信息「运行时间」行未复活"
    )
    assert code.count("formatUptime(info?.uptime_seconds)") == 2, (
        "uptime_seconds 消费点数漂移（恰 2 处）"
    )


def test_ui_assembly_and_declaration():
    """setInfo 装配 uptime_seconds（无假 0 回落）＋SystemInfo 声明＋Clock 复活为真消费。"""
    code = _code()
    assert "uptime_seconds: (o as any)?.uptime_seconds" in code, "setInfo 装配未收 uptime_seconds"
    assert "uptime_seconds?: number" in code, "SystemInfo 声明键缺席"
    assert re.search(r"\bClock\b", code), "Clock import 未复活（banner 修复面）"
    # 禁假 0 回落（CD-⑱ 假 0 纪律；缺席=formatUptime '—' 占位非 0）
    assert "?? 0" not in code and "|| 0" not in code, "假 0 回落残留"


def test_formatuptime_zero_honest_not_masked():
    """formatUptime 源码锚点：真 0 判定不得走 '—'（CD-⑳/㉚ 真 0 禁遮蔽）。"""
    code = _code()
    m = re.search(r"function formatUptime\([^)]*\)\s*\{", code)
    assert m, "formatUptime helper 缺席（复活面回退先炸）"
    # 判空判法=显式 null/undefined/非有限/负数，禁 `!seconds` 把 0 折 '—'
    assert "!seconds" not in code, "`!seconds` 形回来了（真 0 被折 '—' 遮蔽）"


# ── ③ 行为桥（node 真执行 + 真源真跑） ──────────────────────────


def test_formatuptime_helper_real_node_execution():
    """formatUptime helper 源码逐字提取经 node 真执行：缺席='—'/真 0/分钟/小时/天四形态。"""
    src = _src()
    m = re.search(r"(function formatUptime\(.*?\n\})", src, re.S)
    assert m, "formatUptime 提取失败（断言空转）"
    helper = m.group(1)
    probe = (
        helper
        + "\nconst cases = ["
        + "[undefined, '—'], [null, '—'], [-1, '—'], [NaN, '—'], "
        + "[0, '0分钟'], [90, '1分钟'], [59, '0分钟'], "
        + "[3600, '1小时 0分钟'], [90061, '1天 1小时']];"
        + "\nconst out = cases.map(([v, e]) => [formatUptime(v), e]);"
        + "\nconsole.log(JSON.stringify(out));"
    )
    r = subprocess.run(["node", "-e", probe], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"node 执行失败: {r.stderr[:300]}"
    import json

    out = json.loads(r.stdout.strip())
    for got, expect in out:
        assert got == expect, f"formatUptime 形态漂移: got={got!r} expect={expect!r}"


def test_overview_real_run_matches_formatuptime_render():
    """真源真跑→helper 渲染对账：overview 真值过 node formatUptime==展示语义（行为桥）。"""
    from src.api import system_overview

    resp = asyncio.run(system_overview.system_overview())
    val = resp["uptime_seconds"]
    src = _src()
    m = re.search(r"(function formatUptime\(.*?\n\})", src, re.S)
    assert m
    probe = m.group(1) + f"\nconsole.log(formatUptime({val}))"
    r = subprocess.run(["node", "-e", probe], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"node 执行失败: {r.stderr[:300]}"
    rendered = r.stdout.strip()
    assert rendered not in ("", "—"), f"真值 {val} 被渲染成占位（真值遮蔽）: {rendered!r}"
    assert ("分钟" in rendered) or ("小时" in rendered) or ("天" in rendered), (
        f"渲染形非运行时长语义: {rendered!r}"
    )


# ── ④ 真源锚点（单一源） ───────────────────────────────────────


def test_metrics_api_single_source_anchor():
    """metrics_api 单一源：uptime_seconds() 定义在场＋两消费点（prometheus 行+装配）同走本口。"""
    ms = METRICS_SRC.read_text(encoding="utf-8")
    assert "def uptime_seconds() -> float:" in ms, "uptime_seconds() 定义缺席"
    assert 'f"opensoul_uptime_seconds {uptime_seconds():.1f}"' in ms, (
        "prometheus 行未收编单一源（两套解释漂移）"
    )
    assert "now - _start_time" not in ms, "`now - _start_time` 裸形残留（单一源收编失效）"


def test_overview_assembly_anchor():
    """system_overview 装配锚点：import 单一源＋加性键装配行（禁自算 time.time() 双源）。"""
    os_ = OVERVIEW_SRC.read_text(encoding="utf-8")
    assert "from src.api.metrics_api import uptime_seconds" in os_, "单一源 import 缺席"
    assert '"uptime_seconds": round(uptime_seconds(), 1),' in os_, "加性键装配行缺席"
