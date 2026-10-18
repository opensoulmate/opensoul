#!/usr/bin/env python3
"""CD-⑯ drift_suspect 18 条逐项深挖裁决——CD-⑨ 普查（scripts/census_contract_drift.py）
未裁决层 drift_suspect 类的裁决单一源。

背景：CD-⑩ 已裁决新浮出候选 drift_hard 13 + unresolved_endpoint 11，留档明示
「drift_suspect 18 / unresolved_shape 109 / unresolved_path 7 仍留待裁决层不动」。
本工具是 drift_suspect 18 条的**裁决单一源**（CD-⑩ census_contract_drift_deep_dive_cd10.py
同哲学、同纪律）：

- key 级 verdict 三级：resolved_ok（深挖定型=消费键对真源在场、消费路径可达）/
  confirmed_drift（真断点：键真缺席或消费路径不可达，登记 repair_candidate）/
  ruled_false_positive（消费点非缺陷：容错分支/本地构造形，无显示行为损失）。
- 每条裁决必须带 evidence（源码真源 file:line 锚点+推理链），禁无据断言。
- build_artifact() 从 post_cd9 工件消费 drift_suspect 逐条 join 裁决；任何 finding
  无裁决=拒绝出工件，反向多出的裁决同样报错（完整性门）。
- 深挖扩面发现（普查未列但同根）以 EXPANDED_FINDINGS 登记，不进 findings join。

用法：
    python3 scripts/census_contract_drift_deep_dive_cd16.py          # 重新生成工件
    python3 scripts/census_contract_drift_deep_dive_cd16.py --check  # 只校验不写

诚实边界（与 CD-①/CD-③/CD-⑨/CD-⑩ 工具边界互补）：
- 裁决依据=源码真源静态读取（response_model/pydantic 字段/返回字面量/dataclass 字段/
  dict 构造/动态赋值/UI 消费点），本工具不重跑 live 探针。
- 范围纪律：只裁决 drift_suspect 一类（工件 18 条）；unresolved_shape 109 /
  unresolved_path 7 仍留待裁决层不动。
- drift_suspect 的普查 note（「元素形状未解析」「键缺席（形状含动态操作）」）=
  census 静态提取边界自述；裁决逐条核真源定型，边界假阳与真断点分开。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POST_CD9 = ROOT / "data" / "census_contract_drift_post_cd9.json"
OUT = ROOT / "data" / "census_contract_drift_deep_dive_cd16.json"

TARGET_CENSUS_VERDICTS = ("drift_suspect",)

# ── 裁决单一源 ──────────────────────────────────────────────────────────
# key: (page, line, tuple(consumed_key_path))；value: {verdict, evidence, ...}

_V = "resolved_ok"
_D = "confirmed_drift"
_F = "ruled_false_positive"

VERDICTS = {
    # ── agents detect：`**_detect_cache` 模块级缓存变量扩展（普查提取边界）──
    ("agents/page.tsx", 57, ("agents",)): {
        "verdict": _V,
        "evidence": "真源 detect_agents 返回 {**_detect_cache, session_agents} "
        "（src/api/agents.py:856-859），_detect_cache 构造含 agents:result "
        "（:853 {\"os\",\"agents\",\"total\"}）——census 只见 return 字面量直接键 "
        "session_agents，`**_detect_cache` 变量扩展看不穿=提取边界假阳；UI "
        "data.agents Array.isArray 容错消费（admin-ui/app/agents/page.tsx:56-57）"
        "消费路径可达",
    },
    # ── downloader tasks 元素：DownloadTask.to_dict() 方法间接（普查提取边界）──
    ("downloader/page.tsx", 408, ("tasks", "id")): {
        "verdict": _V,
        "evidence": "tasks=[t.to_dict() for t in dl.list_tasks()]（src/api/download_api.py:"
        "573），DownloadTask.to_dict 含 id（src/api/native_downloader.py:65）——"
        "dataclass 方法间接看不穿=提取边界假阳；UI task.id 消费（admin-ui/app/downloader/"
        "page.tsx:408 key={task.id}）",
    },
    ("downloader/page.tsx", 199, ("tasks", "status")): {
        "verdict": _V,
        "evidence": "同根：DownloadTask.to_dict 含 status（native_downloader.py:68），"
        "UI t.status 活跃判定（downloader/page.tsx:199）消费路径可达",
    },
    # ── downloader cache 元素：for 循环 append dict 字面量（普查提取边界）──
    ("downloader/page.tsx", 577, ("files", "name")): {
        "verdict": _V,
        "evidence": "list_cache for 循环 append dict 字面量 {name,path,size,modified}"
        "（src/api/download_api.py:527-535，name :530），files=sorted(files,...)（:538）"
        "——for-loop append dict 字面量非 list comprehension=提取边界假阳；UI "
        "file.name 消费（downloader/page.tsx:577 key={file.name}）",
    },
    # ── healer cycle org 元素：_result_to_dict() 函数间接（普查提取边界）──
    ("healer/page.tsx", 288, ("organs", "healthy")): {
        "verdict": _V,
        "evidence": "cycle 返回 organs=[_result_to_dict(r) for r in results]"
        "（src/api/healer.py:293），_result_to_dict 含 healthy（:62）——函数间接"
        "看不穿=提取边界假阳；UI organ.healthy 消费（admin-ui/app/healer/page.tsx:288）",
    },
    ("healer/page.tsx", 146, ("organs", "organ")): {
        "verdict": _V,
        "evidence": "同根：_result_to_dict 含 organ（healer.py:61）；勘误如实记：census "
        "归属端点 /api/healer/cycle 而该消费点 handleDiagnoseOne 实打 /api/healer/"
        "diagnose/{organ}（模板路径，healer/page.tsx:141）——消费对象=lastCycle.organs "
        "元素（cycle 响应态）o.organ===organ 匹配（:146），键双源同形（单器官响应同 "
        "_result_to_dict，healer.py:119）",
    },
    ("healer/page.tsx", 288, ("organs", "severity")): {
        "verdict": _V,
        "evidence": "同根：_result_to_dict 含 severity（healer.py:63），UI "
        "SEVERITY_CONFIG[organ.severity]（healer/page.tsx:289）消费路径可达",
    },
    # ── learn get_course：动态补键 data[\"chapters\"]（普查提取边界）──
    ("learn/page.tsx", 401, ("chapters",)): {
        "verdict": _V,
        "evidence": "get_course 在 _course_dict(course) 后动态补键 data[\"chapters\"]="
        "[_chapter_dict(ch) for ch in course.chapters]（src/api/learn.py:248-249）"
        "——键由动态赋值产出（census note「键缺席（形状含动态操作）」即此）=提取边界，"
        "键真在场；UI selectedCourse.chapters || [] 消费（admin-ui/app/learn/page.tsx:401）",
    },
    # ── diagnostics config-diff 元素：嵌套函数 append dict 字面量（普查提取边界）──
    ("monitoring/page.tsx", 548, ("changes", "key")): {
        "verdict": _V,
        "evidence": "_diff 嵌套函数 append dict 字面量 {key,default,current}"
        "（src/api/diagnostics.py:274-280，key :276），changes=diffs（:286）——"
        "嵌套函数内 dict 字面量=提取边界假阳；UI c.key 消费（admin-ui/app/monitoring/"
        "page.tsx:548）",
    },
    # ── system bootstrap/status：真源无 initialized/steps（真断点，键全层缺席）──
    ("system/page.tsx", 453, ("initialized",)): {
        "verdict": _D,
        "repair_candidate": "system bootstrap status 消费键收编",
        "evidence": "真源 bootstrap_status 返回 {status,component,**bootstrap.state}"
        "（src/api/system_overview.py:331-337），bootstrap.state={bootstrapped,"
        "bootstrapped_at,version,items}（src/system/bootstrap.py:78-84）——initialized "
        "全层缺席；UI BootstrapStatus 接口声明 initialized（admin-ui/app/system/page.tsx:"
        "51）+消费 :453 `bootstrap.initialized ? 'ok' : 'uninitialized'` 恒走 "
        "uninitialized 分支=已初始化系统也显「未初始化」假渲染（真源真键=bootstrapped）",
    },
    ("system/page.tsx", 464, ("steps",)): {
        "verdict": _D,
        "repair_candidate": "system bootstrap status 消费键收编",
        "evidence": "同根 steps 全层缺席（真源 items:dict[str,bool] 无 steps 形，"
        "bootstrap.py:83）；UI `bootstrap?.steps && bootstrap.steps.length > 0`"
        "（system/page.tsx:464）恒假=初始化步骤清单块（:464-478）永不渲染死显示面",
    },
    ("system/page.tsx", 466, ("steps", "status")): {
        "verdict": _D,
        "repair_candidate": "system bootstrap status 消费键收编",
        "evidence": "同根步骤元素死（steps 恒缺席→块 :464-478 整体不可达，step.status "
        "三态判定 :468-469/:475 永不执行）——step.name/message 同死（EXPANDED_FINDINGS）",
    },
    # ── diagnostics check-all organs 元素：嵌套 _check dict 字面量（普查提取边界）──
    ("system/page.tsx", 313, ("organs", "key")): {
        "verdict": _V,
        "evidence": "check_all_detailed 嵌套 _check 返回 dict 字面量含 key"
        "（src/api/diagnostics.py:219-224/:229-236，key :220/:230），organs=results"
        "（:254）——嵌套函数 dict 字面量=提取边界假阳；UI organ.key 消费可达",
    },
    ("system/page.tsx", 213, ("organs", "status")): {
        "verdict": _V,
        "evidence": "同根：_check dict 字面量含 status（diagnostics.py:223/"
        "「\"status\": \"ok\" if r.status_code == 200 else \"error\"」:223/:233），"
        "UI organ.status 消费可达",
    },
    # ── trajectory search events 元素：TrajectoryEvent dataclass asdict（普查提取边界）──
    ("trajectory/page.tsx", 361, ("events", "agent_id")): {
        "verdict": _V,
        "evidence": "events=[e.to_dict() for e in events]（src/api/trajectory.py:339），"
        "TrajectoryEvent.to_dict=asdict(self)（src/trajectory/store.py:69-72）=dataclass "
        "字段全出，agent_id 字段在场（store.py:43）——dataclass 间接看不穿=提取边界假阳",
    },
    ("trajectory/page.tsx", 361, ("events", "event_type")): {
        "verdict": _V,
        "evidence": "同根：TrajectoryEvent.event_type 字段在场（store.py:42）",
    },
    ("trajectory/page.tsx", 361, ("events", "id")): {
        "verdict": _V,
        "evidence": "同根：TrajectoryEvent.id 字段在场（store.py:39）",
    },
    ("trajectory/page.tsx", 361, ("events", "status")): {
        "verdict": _V,
        "evidence": "同根：TrajectoryEvent.status 字段在场（store.py:48）",
    },
}

# 深挖扩面发现（普查未列、随裁决同根登记，不进 findings join）
EXPANDED_FINDINGS = [
    {
        "page": "system/page.tsx",
        "keys": ["steps[].name", "steps[].message"],
        "verdict": _D,
        "repair_candidate": "system bootstrap status 消费键收编",
        "note": "步骤块元素 step.name（:472）/step.message（:473）同根死显示（steps "
        "恒缺席块永不渲染）——修复件收编时整块处置（:464-478），禁只修 census 点名 3 键",
    },
    {
        "page": "system/page.tsx",
        "keys": ["BootstrapStatus 声明键 initialized/steps[{name,status,message}]"],
        "verdict": _D,
        "repair_candidate": "system bootstrap status 消费键收编",
        "note": "接口死声明键（page.tsx:50-53）——CD-⑦ 死声明键清零纪律属修复件；"
        "真源可用面=bootstrapped/bootstrapped_at/version/items（bootstrap.py:78-84），"
        "initialized→bootstrapped 收编、steps 无真源（死显示面清零或按 items 派生）"
        "修复拍另裁",
    },
    {
        "page": "healer/page.tsx",
        "keys": ["organs[].organ（:146 归属勘误）"],
        "verdict": _V,
        "note": "census 归属端点 /api/healer/cycle 而调用点 handleDiagnoseOne 实打 "
        "/api/healer/diagnose/{organ}（模板路径）；消费对象=lastCycle.organs 元素，"
        "键双源同形（_result_to_dict healer.py:59-71/:119/:293）——归属漂移不影响键级定型",
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
                "census_note": f.get("note", ""),
                **v,
            }
        )
    extra = [k for k in VERDICTS if k not in seen]
    if missing or extra:
        raise SystemExit(
            "verdict 覆盖不完整：missing={!r} extra={!r}（完整性门拒出工件）".format(missing, extra)
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

    artifact = {
        "source_artifact": str(POST_CD9.relative_to(ROOT)),
        "scope": "drift_suspect（CD-⑨ 未裁决层 18 条；unresolved_shape 109 / "
        "unresolved_path 7 仍留待裁决层）",
        "counts_input_findings": len(findings),
        "counts": {
            "resolved_ok": sum(1 for r in rows if r["verdict"] == _V),
            "confirmed_drift": sum(1 for r in rows if r["verdict"] == _D),
            "ruled_false_positive": sum(1 for r in rows if r["verdict"] == _F),
        },
        "repair_candidates": sorted(
            {r["repair_candidate"] for r in rows if "repair_candidate" in r}
            | {
                e["repair_candidate"]
                for e in EXPANDED_FINDINGS
                if "repair_candidate" in e
            }
        ),
        "verdicts": rows,
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
        "CD-⑯ 裁决 join ok：findings {} 条 counts={} repair_candidates={}".format(
            artifact["counts_input_findings"],
            artifact["counts"],
            artifact["repair_candidates"],
        )
    )
    if not args.check:
        print("工件已写：{} 工件".format(OUT.relative_to(ROOT)))


if __name__ == "__main__":
    main()
