#!/usr/bin/env python3
"""CD-⑲ unresolved_path 7 条逐项深挖裁决——CD-⑨ 普查（scripts/census_contract_drift.py）
未裁决层 unresolved_path 类的裁决单一源。

背景：CD-⑩ 已裁决新浮出候选（drift_hard 13 + unresolved_endpoint 11），CD-⑯ 已裁决
drift_suspect 18，均留档明示「unresolved_shape 109 / unresolved_path 7 仍留待裁决层」。
本工具是 unresolved_path 7 条的**裁决单一源**（CD-⑩/CD-⑯ 同哲学、同纪律）：

- key 级 verdict 三级：resolved_ok（深挖定型=消费键对真源在场、消费路径可达）/
  confirmed_drift（真断点：键真缺席或消费路径不可达，登记 repair_candidate）/
  ruled_false_positive（消费点非缺陷：容错分支/本地构造形，无显示行为损失）。
- 每条裁决必须带 evidence（源码真源 file:line 锚点+推理链），禁无据断言。
- build_artifact() 从 post_cd9 工件消费 unresolved_path 逐条 join 裁决；任何 finding
  无裁决=拒绝出工件，反向多出的裁决同样报错（完整性门）。
- 深挖扩面发现（普查未列但同根）以 EXPANDED_FINDINGS 登记，不进 findings join。

unresolved_path 的普查 note（「路径非字面量（变量/拼接）——端点无法静态映射」）=
census 静态提取边界自述；裁决逐条把 url 变量解析回字面端点再核真源定型：
- marketplace/page.tsx:82：三元变量 endpoint → 两个字面端点（skills/agents sources）。
- vision/page.tsx：handleGenerate 的 if/else 链 url 变量 → 五个字面端点
  （bar/line/pie/scatter/mindmap 的 /json 形），其中三个端点真源根本不存在=404
  死路径（EXPANDED_FINDINGS 端点级登记，键级裁决只对可达端点定型）。

用法：
    python3 scripts/census_contract_drift_deep_dive_cd19.py          # 重新生成工件
    python3 scripts/census_contract_drift_deep_dive_cd19.py --check  # 只校验不写

诚实边界（与 CD-①/CD-③/CD-⑨/CD-⑩/CD-⑯ 工具边界互补）：
- 裁决依据=源码真源静态读取（返回字面量/路由装饰器/UI 消费点），evidence 中的
  live 证据（openapi/真 POST 探针，2026-10-19）为裁决时点记录，本工具不重跑 live。
- 范围纪律：只裁决 unresolved_path 一类（工件 7 条）；unresolved_shape 109 仍留待
  裁决层不动。
- 键级裁决只对**可达**端点（bar/json、mindmap/json）定型；line/pie/scatter 三分支
  端点缺席（404）属端点级缺陷，以 EXPANDED_FINDINGS 独立登记（一候选一拍），
  键级不重复背锅。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POST_CD9 = ROOT / "data" / "census_contract_drift_post_cd9.json"
OUT = ROOT / "data" / "census_contract_drift_deep_dive_cd19.json"

TARGET_CENSUS_VERDICTS = ("unresolved_path",)

# ── 裁决单一源 ──────────────────────────────────────────────────────────
# key: (page, line, tuple(consumed_key_path))；value: {verdict, evidence, ...}

_V = "resolved_ok"
_D = "confirmed_drift"
_F = "ruled_false_positive"

VERDICTS = {
    # ── marketplace sources：三元变量 endpoint（普查提取边界）──
    ("marketplace/page.tsx", 82, ("sources",)): {
        "verdict": _V,
        "evidence": "census note「路径非字面量」=提取边界（三元变量）：endpoint=tab==="
        "'skills'?'/api/marketplace/skills/sources':'/api/marketplace/agents/sources'"
        "（admin-ui/app/marketplace/page.tsx:80）——两端点均在真源注册（src/api/"
        "marketplace.py @router.get(\"/skills/sources\"):294→list_skill_sources、"
        "@router.get(\"/agents/sources\"):634→list_agent_sources）且均 return "
        "{\"sources\": sources}（:321/:655）键真在场；UI data.sources Array.isArray "
        "消费（marketplace/page.tsx:82）消费路径可达",
    },
    # ── vision previewMeta：url 变量 if/else 链（普查提取边界）+ 兄弟键漂移 1 条 ──
    ("vision/page.tsx", 155, ("image_base64",)): {
        "verdict": _V,
        "evidence": "census note=提取边界（url 变量 if/else 链五分支，vision/page.tsx:"
        "103-145）；可达真源两端点均产出 image_base64（bar_chart_json 返回字面量 "
        "src/api/vision.py:210、mindmap_json :256），UI data.image_base64 消费"
        "（vision/page.tsx:155）可达；line/pie/scatter 三分支端点缺席 404=端点级"
        "扩面（EXPANDED_FINDINGS）另票，键级不背",
    },
    ("vision/page.tsx", 156, ("format",)): {
        "verdict": _V,
        "evidence": "同根 url 变量提取边界；可达真源两端点均产出 format"
        "（src/api/vision.py:204 bar_chart_json、:251 mindmap_json），UI data.format "
        "消费（vision/page.tsx:156 `data.format === 'png' ? ...`）可达",
    },
    ("vision/page.tsx", 368, ("engine",)): {
        "verdict": _V,
        "evidence": "同根；可达真源两端点均产出 engine（src/api/vision.py:205/:252），"
        "UI previewMeta.engine 消费（vision/page.tsx:368 预览元信息行）可达",
    },
    ("vision/page.tsx", 368, ("elapsed_ms",)): {
        "verdict": _D,
        "repair_candidate": "vision preview elapsed 收编（mindmap/json elapsed_seconds"
        "↔UI elapsed_ms 兄弟键漂移）",
        "evidence": "兄弟端点契约漂移：bar_chart_json 产出 elapsed_ms（src/api/"
        "vision.py:208 int(result.elapsed_seconds*1000)）而 mindmap_json 产出 "
        "elapsed_seconds（:254）无 elapsed_ms——mindmap tab 生成后 UI "
        "previewMeta.elapsed_ms || 0（vision/page.tsx:368）恒回落 0=假 0 遮蔽真值"
        "（elapsed_seconds 真在场却无人读，CD-⑱「回落遮蔽假 0」同型）；live 双证"
        "（2026-10-19 POST /api/vision/chart/bar/json 200 键含 elapsed_ms、POST "
        "/api/vision/mindmap/json 200 键含 elapsed_seconds 无 elapsed_ms）",
    },
    ("vision/page.tsx", 369, ("size_bytes",)): {
        "verdict": _V,
        "evidence": "同根；可达真源两端点均产出 size_bytes（src/api/vision.py:207/"
        ":253），UI previewMeta.size_bytes 消费（vision/page.tsx:369 KB 显示）可达",
    },
    ("vision/page.tsx", 383, ("output_file",)): {
        "verdict": _V,
        "evidence": "同根；可达真源两端点均产出 output_file（src/api/vision.py:209/"
        ":255），UI previewMeta?.output_file 条件渲染（vision/page.tsx:383 「已保存"
        "」徽章）可达（save_output:false 时为空串=条件渲染恒假属真源语义非漂移）",
    },
}

# 深挖扩面发现（普查未列、随裁决同根登记，不进 findings join）
EXPANDED_FINDINGS = [
    {
        "page": "vision/page.tsx",
        "keys": ["/api/vision/chart/line/json"],
        "verdict": _D,
        "repair_candidate": "vision chart json 端点面补齐（line/pie/scatter 三分支 404）",
        "note": "折线图 tab handleGenerate url='/api/vision/chart/line/json'（vision/"
        "page.tsx:113）——真源无此路由（src/api/vision.py 路由全集只有 /chart/line "
        "流式二进制 :109 无 /json 变体；live openapi+POST 2026-10-19 双证 404）→"
        "折线图生成必 404 死路径（setError 可见非静默，但功能死）",
    },
    {
        "page": "vision/page.tsx",
        "keys": ["/api/vision/chart/pie/json"],
        "verdict": _D,
        "repair_candidate": "vision chart json 端点面补齐（line/pie/scatter 三分支 404）",
        "note": "饼图 tab url='/api/vision/chart/pie/json'（vision/page.tsx:128）——"
        "真源只有 /chart/pie 流式二进制（src/api/vision.py:139），/json 变体缺席"
        "（live 404）→饼图生成必 404 死路径",
    },
    {
        "page": "vision/page.tsx",
        "keys": ["/api/vision/chart/scatter/json"],
        "verdict": _D,
        "repair_candidate": "vision chart json 端点面补齐（line/pie/scatter 三分支 404）",
        "note": "散点图 tab url='/api/vision/chart/scatter/json'（vision/page.tsx:136）"
        "——真源只有 /chart/scatter 流式二进制（src/api/vision.py:159），/json 变体"
        "缺席（live 404）→散点图生成必 404 死路径",
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
        "scope": "unresolved_path（CD-⑨ 未裁决层 7 条；unresolved_shape 109 仍留待"
        "裁决层）",
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
        "CD-⑲ 裁决 join ok：findings {} 条 counts={} repair_candidates={}".format(
            artifact["counts_input_findings"],
            artifact["counts"],
            artifact["repair_candidates"],
        )
    )
    if not args.check:
        print("工件已写：{}".format(OUT.relative_to(ROOT)))


if __name__ == "__main__":
    main()
