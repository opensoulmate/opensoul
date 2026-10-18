#!/usr/bin/env python3
"""CD-⑩ 新浮出候选逐项深挖裁决——CD-⑨ 普查（scripts/census_contract_drift.py）
新浮出候选层的裁决单一源。

背景：CD-⑨ 提取精度收口后真仓普查 402 findings（data/census_contract_drift_post_cd9.json），
其中新浮出候选=drift_hard 13 + unresolved_endpoint 11 共 24 条（CD-⑨ 记档
「drift_hard 12」=剔除 CD-③ 已裁决 users 误报后的候选口径，以工件 13 为准）。
本工具是这 24 条的**裁决单一源**（CD-③ census_contract_drift_deep_dive.py 同哲学）：

- key 级 verdict 三级：resolved_ok（深挖定型=消费键对真源在场、消费路径可达）/
  confirmed_drift（真断点：键真缺席或消费路径不可达，登记 repair_candidate）/
  ruled_false_positive（消费点非缺陷：容错分支/本地构造形，无显示行为损失）。
- endpoint 级 ENDPOINT_VERDICTS：unresolved_endpoint 类的端点/方法级裁决
  （key 在场而调用方法漂移的真断点在此登记），每条独立带 repair_candidate。
- 每条裁决必须带 evidence（源码真源 file:line 锚点+推理链），禁无据断言。
- build_artifact() 从 post_cd9 工件消费 drift_hard + unresolved_endpoint 逐条
  join 裁决；任何 finding 无裁决=拒绝出工件，反向多出的裁决同样报错（完整性门）；
  endpoint 面按 (page, method, path) 双向对账同款完整性门。

用法：
    python3 scripts/census_contract_drift_deep_dive_cd10.py          # 重新生成工件
    python3 scripts/census_contract_drift_deep_dive_cd10.py --check  # 只校验不写

诚实边界（与 CD-①/CD-③/CD-⑨ 工具边界互补）：
- 裁决依据=源码真源静态读取（response_model/pydantic 字段/返回字面量/路由装饰器/
  UI 调用点方法）+CD-⑨ 既有 live 双 405 实证沿用；本工具不重跑 live 探针。
- 范围纪律：只裁决 drift_hard + unresolved_endpoint 两类（CD-⑨ 候选点名 12+11）；
  drift_suspect 18 / unresolved_shape 109 / unresolved_path 7 仍留待裁决层不动。
- 深挖扩面发现（普查未列但同根）以 EXPANDED_FINDINGS 登记，不进 findings join。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POST_CD9 = ROOT / "data" / "census_contract_drift_post_cd9.json"
OUT = ROOT / "data" / "census_contract_drift_deep_dive_cd10.json"

TARGET_CENSUS_VERDICTS = ("drift_hard", "unresolved_endpoint")

# ── 裁决单一源 ──────────────────────────────────────────────────────────
# key: (page, line, tuple(consumed_key_path))；value: {verdict, evidence, ...}

_V = "resolved_ok"
_D = "confirmed_drift"
_F = "ruled_false_positive"

VERDICTS = {
    # ── feedback extract entries 元素：response_model list[Model] 间接（普查提取边界）──
    ("feedback/page.tsx", 303, ("confidence",)): {
        "verdict": _V,
        "evidence": "真源=FeedbackExtractResponse（response_model src/api/feedback.py:22、"
        "构造 :47-51）entries:list[FeedbackEntry]，FeedbackEntry.confidence 字段在场"
        "（src/models/feedback.py:22）——census 元素形状提取看不穿 response_model "
        "list[Model] 间接=提取边界假阳",
    },
    ("feedback/page.tsx", 303, ("type",)): {
        "verdict": _V,
        "evidence": "同根：FeedbackEntry.type 字段在场（src/models/feedback.py:19），"
        "wire 形状由 pydantic 序列化保证",
    },
    # ── immune moderate findings 元素：list comprehension 内 dict 字面量（普查提取边界）──
    ("immune/page.tsx", 298, ("findings", "label")): {
        "verdict": _V,
        "evidence": "src/api/immune.py:98-100 返回字面量 findings=[{type,label,risk}] "
        "元素三键全在场——census 元素形状提取未解析 list comprehension 内 dict 字面量"
        "=提取边界假阳",
    },
    ("immune/page.tsx", 298, ("findings", "risk")): {
        "verdict": _V,
        "evidence": "同根：immune.py:99 元素 dict 字面量含 risk",
    },
    # ── logs stream summary：三键真源面错层（真断点，打错面）──
    ("logs/page.tsx", 348, ("buffer_size",)): {
        "verdict": _D,
        "repair_candidate": "logs stream summary 消费键收编",
        "evidence": "get_stream_summary 恰 5 键（src/api/event_stream.py:800-806）无 "
        "buffer_size——真源在 GET /api/events/stream/health（:831-837 "
        "buffer_size/probes_configured/sse_subscribers）=UI 打错面；修前页脚"
        "「缓冲: undefined」假渲染",
    },
    ("logs/page.tsx", 348, ("organ_probes",)): {
        "verdict": _D,
        "repair_candidate": "logs stream summary 消费键收编",
        "evidence": "同根（event_stream.py:800-806 无 organ_probes）：真源键名="
        "probes_configured（/stream/health :835）同义不同名，修前「探针: undefined」",
    },
    ("logs/page.tsx", 240, ("sse_clients",)): {
        "verdict": _D,
        "repair_candidate": "logs stream summary 消费键收编",
        "evidence": "同根：真源键名=sse_subscribers（/stream/health :836）或 clients"
        "（/api/events/sse/clients :902-907）；修前 SSE 客户端卡恒 `|| 0` 假 0",
    },
    # ── nest stats：响应恰 {tenants,isolation}，UI 平铺读（真断点，嵌套错层+真缺席）──
    ("nest/page.tsx", 345, ("total_tenants",)): {
        "verdict": _D,
        "repair_candidate": "nest stats 消费键收编",
        "evidence": "GET /api/nest/stats 响应恰 {tenants,isolation}（src/api/nest.py:"
        "260-266）；total_tenants 在嵌套层 tenants.total_tenants（src/nest/tenant.py:"
        "492-498 stats()）非平铺——UI 平铺读恒回落 filteredTenants.length",
    },
    ("nest/page.tsx", 349, ("active_tenants",)): {
        "verdict": _D,
        "repair_candidate": "nest stats 消费键收编",
        "evidence": "同根且键全层缺失：tenant.py:492-498 只出 total_tenants/by_tier/"
        "by_status/total_storage_bytes/total_documents，无 active_tenants（可由 "
        "tenants.by_status 派生）——修前恒回落 '-'",
    },
    ("nest/page.tsx", 353, ("suspended_tenants",)): {
        "verdict": _D,
        "repair_candidate": "nest stats 消费键收编",
        "evidence": "同根：全层缺失无 suspended_tenants（by_status 可派生），修前恒回落 '-'",
    },
    ("nest/page.tsx", 357, ("total_policies",)): {
        "verdict": _D,
        "repair_candidate": "nest stats 消费键收编",
        "evidence": "全层缺失：isolation.stats() 只出 total_access_checks/blocked_attempts 等"
        "（src/nest/isolation.py:294-300）无 total_policies；修前靠 UI 侧 "
        "policies.length 兜底显示非真源键",
    },
    # ── users list：真源裸数组，UI 三元容错（CD-③ 已裁决误报，随新工件复核维持）──
    ("users/page.tsx", 38, ("users",)): {
        "verdict": _F,
        "evidence": "CD-③ 已裁决 ruled_false_positive（census_contract_drift_deep_dive."
        "py 同键条目）随新工件复核维持：API 回裸数组（response_model=list[UserListItem] "
        "src/api/enterprise.py:542），UI 三元容错 Array.isArray(data) ? data : "
        "data.users || []（admin-ui/app/users/page.tsx:37）数组分支真消费，users 只是"
        "旧形回落访问=无显示损失",
    },
    # ── voice synthesize/json error：本地 catch 构造形非 wire 消费 ──
    ("voice/page.tsx", 201, ("error",)): {
        "verdict": _F,
        "evidence": "error 键=本地 catch 构造（admin-ui/app/voice/page.tsx:127-128 "
        "setTestResult({ error: e.message })）非 wire 消费——synthesize/json 成功形 8 键"
        "（src/api/voice.py:150-158）无 error，失败走 HTTPException（:122-125）非 body "
        "error 键；`testResult?.error` 判本地构造形=无显示损失",
    },
    # ── hippo decay/simulate：键在场但唯一调用点方法漂移=消费路径死（真断点）──
    ("hippo/page.tsx", 477, ("retention",)): {
        "verdict": _D,
        "repair_candidate": "hippo decay/simulate 方法漂移修复",
        "evidence": "键本体在场（simulate 响应 retention src/api/hippo.py:278-279）但"
        "消费路径死：唯一生产调用点 admin-ui/app/hippo/page.tsx:207 apiFetch 默认 GET 而"
        "路由 @router.post（hippo.py:267）恒 405（CD-⑨ live 双证），setSimResult(:208) "
        "永不置位=:477 模拟面板（simResult &&）死——根因=端点方法漂移（见 "
        "ENDPOINT_VERDICTS hippo 条）",
        "note": "键级 resolved 的部分=retention/should_archive/should_forget/strategy "
        "四键真源全在场（hippo.py:278-284）；缺陷在方法不在键",
    },
    # ── soma discovery/scan：ScanResult 键全在场，初始 GET 真消费可达（方法漂移在端点级）──
    ("soma/page.tsx", 122, ("adapters",)): {
        "verdict": _V,
        "note": "端点级方法漂移见 ENDPOINT_VERDICTS soma 条",
        "evidence": "ScanResult.adapters:list[str] 在场（src/api/soma_discovery.py:192-197）"
        "——初始 GET 调用点 :506 真消费可达（api_full_scan 每次全量扫描 :1474-1487）",
    },
    ("soma/page.tsx", 106, ("cli_tools",)): {
        "verdict": _V,
        "note": "端点级方法漂移见 ENDPOINT_VERDICTS soma 条",
        "evidence": "ScanResult.cli_tools:list[CLITool] 在场（soma_discovery.py:195），"
        "消费路径可达同上",
    },
    ("soma/page.tsx", 148, ("cli_tools", "version")): {
        "verdict": _V,
        "note": "端点级方法漂移见 ENDPOINT_VERDICTS soma 条",
        "evidence": "CLITool.version 字段在场（soma_discovery.py:178），消费路径可达同上",
    },
    ("soma/page.tsx", 82, ("processes",)): {
        "verdict": _V,
        "note": "端点级方法漂移见 ENDPOINT_VERDICTS soma 条",
        "evidence": "ScanResult.processes:list[ProcessInfo] 在场（soma_discovery.py:194），"
        "消费路径可达同上",
    },
    ("soma/page.tsx", 82, ("processes", "cpu")): {
        "verdict": _V,
        "note": "端点级方法漂移见 ENDPOINT_VERDICTS soma 条",
        "evidence": "ProcessInfo.cpu 字段在场（soma_discovery.py:168），消费路径可达同上",
    },
    ("soma/page.tsx", 82, ("processes", "mem")): {
        "verdict": _V,
        "note": "端点级方法漂移见 ENDPOINT_VERDICTS soma 条",
        "evidence": "ProcessInfo.mem 字段在场（soma_discovery.py:169），消费路径可达同上",
    },
    ("soma/page.tsx", 133, ("scan_time",)): {
        "verdict": _V,
        "note": "端点级方法漂移见 ENDPOINT_VERDICTS soma 条",
        "evidence": "ScanResult.scan_time 字段在场（soma_discovery.py:193），消费路径可达同上",
    },
    ("soma/page.tsx", 84, ("services",)): {
        "verdict": _V,
        "note": "端点级方法漂移见 ENDPOINT_VERDICTS soma 条",
        "evidence": "ScanResult.services:list[ServiceInfo] 在场（soma_discovery.py:196），"
        "消费路径可达同上",
    },
    ("soma/page.tsx", 84, ("services", "protocol")): {
        "verdict": _V,
        "note": "端点级方法漂移见 ENDPOINT_VERDICTS soma 条",
        "evidence": "ServiceInfo.protocol 字段在场（soma_discovery.py:183），消费路径可达同上",
    },
    ("soma/page.tsx", 84, ("services", "state")): {
        "verdict": _V,
        "note": "端点级方法漂移见 ENDPOINT_VERDICTS soma 条",
        "evidence": "ServiceInfo.state 字段在场（soma_discovery.py:186），消费路径可达同上",
    },
}

# ── 端点级裁决（unresolved_endpoint 类的真断点归属面）──────────────────────
# key: (page, callsite_line, method, path)；与 key 级裁决互为补充不重复计数

ENDPOINT_VERDICTS = {
    ("hippo/page.tsx", 207, "GET", "/api/hippo/decay/simulate"): {
        "verdict": _D,
        "repair_candidate": "hippo decay/simulate 方法漂移修复",
        "evidence": "路由=@router.post(\"/decay/simulate\")（src/api/hippo.py:267）而调用点 "
        "apiFetch 默认 GET（admin-ui/app/hippo/page.tsx:207，无 method 参数）——CD-⑨ live "
        "双证 GET 405；修前「模拟衰减」必炸（catch 置 error，simResult 永不置位，"
        "响应键 retention/should_archive/should_forget/strategy 本体全在场 hippo.py:278-284）",
    },
    ("soma/page.tsx", 521, "POST", "/api/soma/discovery/scan"): {
        "verdict": _D,
        "repair_candidate": "soma discovery/scan 重扫方法漂移收口",
        "evidence": "路由=@router.get(\"/scan\")（src/api/soma_discovery.py:1472）而重扫调用点 "
        "method:'POST'（admin-ui/app/soma/page.tsx:521「Force rescan via POST」）——CD-⑨ "
        "live 双证 POST 405 恒空转；勘误如实记：CD-⑨ 记档「fallback GET 不重扫=假功能」"
        "措辞不实——catch fallback（page.tsx:523-525）走 GET fetchScan，而 api_full_scan "
        "每次全量扫描（soma_discovery.py:1474-1487）=fallback 真重扫真刷新，显示面非死；"
        "真断点=POST 调用恒 405 空转（方法漂移）",
    },
}

# 深挖扩面发现（普查未列、随裁决同根登记，不进 findings join）
EXPANDED_FINDINGS = [
    {
        "page": "logs/page.tsx",
        "keys": ["probes_configured", "sse_subscribers"],
        "verdict": _D,
        "repair_candidate": "logs stream summary 消费键收编",
        "note": "真源键名的同义异名对（organ_probes←probes_configured、"
        "sse_clients←sse_subscribers，/api/events/stream/health event_stream.py:831-837）——"
        "修复件收编时按真源名对齐，禁再手抄同义名",
    },
    {
        "page": "nest/page.tsx",
        "keys": ["tenants.total_tenants", "tenants.by_status"],
        "verdict": _D,
        "repair_candidate": "nest stats 消费键收编",
        "note": "嵌套真源可用面（tenant.py:492-498）：total_tenants 直取嵌套层、"
        "active_tenants/suspended_tenants 由 by_status 派生；total_policies 无真源需另裁"
        "（补现或移除显示）",
    },
]


def build_artifact(check_only: bool = False) -> dict:
    post = json.loads(POST_CD9.read_text(encoding="utf-8"))
    findings = [f for f in post["findings"] if f["verdict"] in TARGET_CENSUS_VERDICTS]
    rows = []
    seen = set()
    missing = []
    for f in findings:
        key = (f["page"], f["line"], tuple(f["key"]))
        seen.add(key)
        v = VERDICTS.get(key)
        if v is None:
            missing.append(key)
            continue
        rows.append(
            {
                "page": f["page"],
                "line": f["line"],
                "consumed_key": list(f["key"]),
                "census_verdict": f["verdict"],
                "census_handler": f.get("handler"),
                **v,
            }
        )
    extra = [k for k in VERDICTS if k not in seen]
    if missing or extra:
        raise SystemExit(
            "verdict 覆盖不完整：missing={!r} extra={!r}（完整性门拒出工件）".format(missing, extra)
        )

    # endpoint 面双向对账：unresolved_endpoint findings 的 (page, method, path) 集合
    # 必须与 ENDPOINT_VERDICTS 的 (page, method, path) 集合精确相等
    ep_from_findings = {
        (f["page"], f["method"], f["path"])
        for f in findings
        if f["verdict"] == "unresolved_endpoint"
    }
    ep_from_verdicts = {(p, m, path) for (p, _line, m, path) in ENDPOINT_VERDICTS}
    if ep_from_findings != ep_from_verdicts:
        raise SystemExit(
            "endpoint verdict 覆盖不完整：missing={!r} extra={!r}（完整性门拒出工件）".format(
                sorted(ep_from_findings - ep_from_verdicts),
                sorted(ep_from_verdicts - ep_from_findings),
            )
        )
    for (p, line, m, path), v in ENDPOINT_VERDICTS.items():
        if not v.get("evidence"):
            raise SystemExit("endpoint verdict 缺 evidence：{!r}".format((p, line, m, path)))
        if v["verdict"] == _D and not v.get("repair_candidate"):
            raise SystemExit(
                "confirmed_drift 缺 repair_candidate：{!r}".format((p, line, m, path))
            )

    for r in rows:
        if not r.get("evidence"):
            raise SystemExit("verdict 缺 evidence：{!r}".format((r["page"], r["line"])))
        if r["verdict"] not in (_V, _D, _F):
            raise SystemExit("verdict 值越界：{!r}".format(r))
        if r["verdict"] == _D and not r.get("repair_candidate"):
            raise SystemExit(
                "confirmed_drift 缺 repair_candidate：{!r}".format((r["page"], r["line"]))
            )

    ep_rows = [
        {
            "page": p,
            "callsite_line": line,
            "method": m,
            "path": path,
            **v,
        }
        for (p, line, m, path), v in sorted(ENDPOINT_VERDICTS.items())
    ]
    artifact = {
        "source_artifact": str(POST_CD9.relative_to(ROOT)),
        "scope": "drift_hard + unresolved_endpoint（CD-⑨ 新浮出候选 12+11 口径；"
        "drift_hard 工件 13 含 CD-③ 已裁决 users 误报 1 条随新工件复核）",
        "counts_input_findings": len(findings),
        "counts": {
            "resolved_ok": sum(1 for r in rows if r["verdict"] == _V),
            "confirmed_drift": sum(1 for r in rows if r["verdict"] == _D),
            "ruled_false_positive": sum(1 for r in rows if r["verdict"] == _F),
        },
        "endpoint_counts": {
            "confirmed_drift": sum(1 for r in ep_rows if r["verdict"] == _D),
        },
        "repair_candidates": sorted(
            {
                r["repair_candidate"]
                for r in rows
                if "repair_candidate" in r
            }
            | {r["repair_candidate"] for r in ep_rows if "repair_candidate" in r}
        ),
        "verdicts": rows,
        "endpoint_verdicts": ep_rows,
        "expanded_findings": EXPANDED_FINDINGS,
    }
    if not check_only:
        OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只校验不写工件")
    args = parser.parse_args()
    artifact = build_artifact(check_only=args.check)
    print(
        "CD-⑩ 裁决 join ok：findings {} 条 counts={} endpoint={} repair_candidates={}".format(
            artifact["counts_input_findings"],
            artifact["counts"],
            artifact["endpoint_counts"],
            artifact["repair_candidates"],
        )
    )
    if not args.check:
        print("工件已写：{}".format(OUT.relative_to(ROOT)))


if __name__ == "__main__":
    main()
