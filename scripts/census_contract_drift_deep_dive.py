#!/usr/bin/env python3
"""CD-③ 待确认层深挖——CD-① 契约漂移普查「待确认」层逐项裁决单一源。

背景：CD-① 普查（scripts/census_contract_drift.py）把静态不可定型的消费键
留在待确认层（drift_suspect / unresolved_shape / unresolved_path / 残留
drift_hard），CD-② 修复 4 真断点后重跑得到 data/census_contract_drift_post_fix.json
（43 条非 produced findings）。本工具是这 43 条的**裁决单一源**：

- verdict 三级：resolved_ok（深挖定型=真源键在场，普查提取边界假阳）/
  confirmed_drift（真源键缺席=真断点，登记修复候选）/
  ruled_false_positive（CD-① 人工复核已排除，随附证据不复挖）。
- 每条裁决必须带 evidence（源码真源位置/推理链），禁无据断言。
- build_artifact() 从 post-fix 工件消费 findings 逐条 join 裁决，
  任何 finding 无裁决 = 拒绝出工件（完整性门），反向多出的裁决同样报错。

用法：
    python3 scripts/census_contract_drift_deep_dive.py          # 重新生成工件
    python3 scripts/census_contract_drift_deep_dive.py --check  # 只校验不写

诚实边界（与 CD-① 工具边界互补）：
- 裁决依据=源码真源静态读取（dict 字面量/response_model/dataclass 字段/
  to_dict 字面量/asdict）+调用点上下文归属复核；未做逐页 live 探针
  （live 证据在 CD-①/CD-② 既有探针与各修复件 e2e）。
- 工具归属漂移（census 把消费键挂错 endpoint）以 note 记录，verdict 按
  消费键对**真 endpoint** 真源判定。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POST_FIX = ROOT / "data" / "census_contract_drift_post_fix.json"
OUT = ROOT / "data" / "census_contract_drift_deep_dive.json"

# ── 裁决单一源 ──────────────────────────────────────────────────────────
# key: (page, line, tuple(consumed_key_path))；value: {verdict, evidence, ...}

_V = "resolved_ok"
_D = "confirmed_drift"
_F = "ruled_false_positive"

VERDICTS: dict[tuple[str, int, tuple], dict] = {
    # ── agent-proxy send：AgentResponse（response_model）字段全解析 ──
    ("agent-proxy/page.tsx", 264, ("error",)): {
        "verdict": _V,
        "evidence": "api/agent_proxy.py:60-66 AgentResponse(response_model) 字段 "
        "agent_id/agent_name/response/success/error——error 在场",
    },
    ("agent-proxy/page.tsx", 262, ("response",)): {
        "verdict": _V,
        "evidence": "api/agent_proxy.py:60-66 AgentResponse.response 字段在场",
    },
    ("agent-proxy/page.tsx", 263, ("success",)): {
        "verdict": _V,
        "evidence": "api/agent_proxy.py:60-66 AgentResponse.success 字段在场",
    },
    # ── agents detect：spread 真源在场（CD-① 已排除）──
    ("agents/page.tsx", 57, ("agents",)): {
        "verdict": _F,
        "evidence": "CD-① 误报排除：agents.py:857 _detect_cache={os,agents,total} 经 "
        "return {**_detect_cache, …} spread 真源在场，spread=动态操作致普查降级 drift_suspect",
    },
    # ── dedup duplicates 元素：真源={item_a:{id,title},item_b:{id,title},similarity} ──
    ("dedup/page.tsx", 198, ("duplicates", "similarity")): {
        "verdict": _V,
        "evidence": "services/deduplication.py:130-136 get_duplicate_report 元素字面量含 "
        "similarity；同元素 title_a/title_b 缺席=同元素兄弟键漂移（见 confirmed_drift 两条）",
    },
    ("dedup/page.tsx", 198, ("duplicates", "title_a")): {
        "verdict": _D,
        "repair_candidate": "①dedup 元素键收编",
        "evidence": "真源元素={item_a:{id,title},item_b:{id,title},similarity}"
        "（deduplication.py:130-136 dict 字面量全解析）无 title_a——UI "
        "DuplicatePair 接口（dedup/page.tsx:16-23）按 id_a/title_a/content_type/"
        "created_at_a 平铺形消费，7 键缺席（深挖扩面：id_a/id_b/content_type/"
        "created_at_a/created_at_b 5 键普查未列，同元素同根）",
    },
    ("dedup/page.tsx", 198, ("duplicates", "title_b")): {
        "verdict": _D,
        "repair_candidate": "①dedup 元素键收编",
        "evidence": "同 title_a：真源为 item_b.title 嵌套形，UI 消费平铺 title_b 缺席",
    },
    # ── downloader tasks/cache 元素：DownloadTask.to_dict / list_cache 字面量 ──
    ("downloader/page.tsx", 408, ("tasks", "id")): {
        "verdict": _V,
        "evidence": "api/native_downloader.py:63-80 DownloadTask.to_dict 字面量含 id",
    },
    ("downloader/page.tsx", 199, ("tasks", "status")): {
        "verdict": _V,
        "evidence": "api/native_downloader.py:63-80 DownloadTask.to_dict 字面量含 status",
    },
    ("downloader/page.tsx", 577, ("files", "name")): {
        "verdict": _V,
        "evidence": "api/download_api.py:516-540 list_cache files 元素字面量含 name"
        "（for-loop dict 字面量，普查提取边界——CD-② 留档同款，源码读取定型）",
    },
    # ── gland：工具归属漂移（挂错 endpoint）+真源判定 ──
    ("gland/page.tsx", 532, ("input",)): {
        "verdict": _D,
        "repair_candidate": "②gland 页消费键收编",
        "tool_misattribution": "census 归属 POST /api/gland/test 不实——该消费点在 "
        "Object.entries(usage?.by_provider) 元素上（gland/page.tsx:532），真 endpoint="
        "GET /api/gland/usage",
        "evidence": "真源 token_meter.summary()['by_provider']=usage_by_provider()="
        "dict[str,int]（gland/token_meter.py:38 _by_provider: dict[str,int]、:118-119）"
        "——元素值是 int 非对象，data.input 恒 undefined（UI :532 (data.input||0) 恒 0）",
    },
    ("gland/page.tsx", 532, ("output",)): {
        "verdict": _D,
        "repair_candidate": "②gland 页消费键收编",
        "tool_misattribution": "同 input：真 endpoint=GET /api/gland/usage（by_provider 元素）",
        "evidence": "同 input：by_provider 元素为 int，data.output 恒 undefined",
    },
    ("gland/page.tsx", 538, ("requests",)): {
        "verdict": _D,
        "repair_candidate": "②gland 页消费键收编",
        "tool_misattribution": "同 input：真 endpoint=GET /api/gland/usage（by_provider 元素）",
        "evidence": "同 input：by_provider 元素为 int，data.requests 恒 undefined"
        "（UI :538 formatNumber(data.requests||0) 恒 0）",
    },
    ("gland/page.tsx", 149, ("message",)): {
        "verdict": _D,
        "repair_candidate": "②gland 页消费键收编",
        "evidence": "真源 gland/router.py:979-1012 test_provider 返回 {status,provider,"
        "model,reply}（成功）/{status,provider,detail}（失败）无 message 键——UI "
        "data.message || '连接成功' 回落恒吃默认文案，模型 reply 永不显示（key drift 被 "
        "|| 回落遮蔽=低危但同根）",
    },
    # ── hermes sessions：_list_sessions_sync 返回 {"sessions","total"} ──
    ("hermes/page.tsx", 170, ("sessions",)): {
        "verdict": _V,
        "evidence": "api/hermes_bridge.py:119 _list_sessions_sync return "
        '{"sessions": sessions, "total": len(sessions)}——键在场（跨函数 bare return，'
        "普查提取边界）",
    },
    ("hermes/page.tsx", 171, ("total",)): {
        "verdict": _V,
        "evidence": "同上：total 键在场",
    },
    # ── limb windows：response_model=list[WindowInfo] 裸数组 ──
    ("limb/page.tsx", 130, ("windows",)): {
        "verdict": _D,
        "repair_candidate": "④limb windows 元素容器收编",
        "evidence": "api/limb_rpa.py:678 list_windows response_model=list[WindowInfo]"
        " 返回裸数组非 {windows:[…]}——UI setWindows(Array.isArray(data.windows)?…:[]) "
        "对裸数组取 .windows 恒 undefined 恒回落 []（Windows 列表永不渲染；"
        "WindowInfo 字段 id/title/x/y/width/height/focused 与 UI 元素消费一致无元素级漂移）",
    },
    # ── marketplace：endpoint 变量双值均 {"sources": …} ──
    ("marketplace/page.tsx", 82, ("sources",)): {
        "verdict": _V,
        "evidence": "endpoint 变量解析=tab==='skills'?/api/marketplace/skills/sources:"
        "/api/marketplace/agents/sources（marketplace/page.tsx:79）双 endpoint 均 return "
        '{"sources": sources}（api/marketplace.py:321/:agents 段同形）——键在场',
    },
    # ── nest get_tenant：Tenant.to_dict 字段全在 ──
    ("nest/page.tsx", 286, ("name",)): {
        "verdict": _V,
        "evidence": "nest/tenant.py:159-175 Tenant.to_dict 字面量含 name",
    },
    ("nest/page.tsx", 287, ("tier",)): {
        "verdict": _V,
        "evidence": "nest/tenant.py:159-175 Tenant.to_dict 含 tier",
    },
    ("nest/page.tsx", 288, ("description",)): {
        "verdict": _V,
        "evidence": "nest/tenant.py:159-175 Tenant.to_dict 含 description",
    },
    ("nest/page.tsx", 289, ("owner_user_id",)): {
        "verdict": _V,
        "evidence": "nest/tenant.py:159-175 Tenant.to_dict 含 owner_user_id",
    },
    ("nest/page.tsx", 290, ("tags",)): {
        "verdict": _V,
        "evidence": "nest/tenant.py:159-175 Tenant.to_dict 含 tags",
    },
    # ── knowledge 元素：KnowledgeResponse/KnowledgeBase 字段 ──
    ("patterns/page.tsx", 241, ("map", "content")): {
        "verdict": _V,
        "evidence": "models/knowledge.py:31-60 KnowledgeBase.content 字段（response_model="
        "list[KnowledgeResponse] 基类并入）",
    },
    ("patterns/page.tsx", 241, ("map", "metadata")): {
        "verdict": _V,
        "evidence": "models/knowledge.py:36 KnowledgeBase.metadata 字段在场",
    },
    ("patterns/page.tsx", 241, ("map", "title")): {
        "verdict": _V,
        "evidence": "models/knowledge.py:32 KnowledgeBase.title 字段在场",
    },
    # ── system organs / check-all：元素字面量 + 工具归属漂移 ──
    ("system/page.tsx", 212, ("organs", "enabled")): {
        "verdict": _V,
        "evidence": "api/config_api.py:47-62 list_organs_config 元素字面量 {key,enabled,"
        "config} 含 enabled",
    },
    ("system/page.tsx", 393, ("organs", "key")): {
        "verdict": _V,
        "evidence": "api/config_api.py:47-62 元素字面量含 key",
    },
    ("system/page.tsx", 212, ("organs", "status")): {
        "verdict": _V,
        "tool_misattribution": "census 归属 GET /api/organs 不实——消费点是 "
        "organHealth.filter(o => o.status==='ok')（system/page.tsx:213），organHealth 来自 "
        "GET /api/diagnostics/check-all（setOrganHealth(data.organs)，:186）",
        "evidence": "check_all_detailed 元素字面量 {key,label,category,status,status_code,"
        "response_time_ms}（api/diagnostics.py:208-238）含 status——键在场",
    },
    ("system/page.tsx", 313, ("organs", "key")): {
        "verdict": _V,
        "evidence": "api/diagnostics.py:208-238 check-all 元素字面量含 key",
    },
    ("system/page.tsx", 213, ("organs", "status")): {
        "verdict": _V,
        "evidence": "api/diagnostics.py:208-238 check-all 元素字面量含 status",
    },
    # ── traces get_session：session.to_dict() 无 events 键 ──
    ("traces/page.tsx", 252, ("events",)): {
        "verdict": _D,
        "repair_candidate": "③traces events 端点改指真端点",
        "evidence": "GET /api/trajectory/sessions/{id} 返回 session.to_dict()"
        "（trajectory/store.py:76-104 TrajectorySession 字段 id/agent_id/task_description/"
        "status/… 无 events 键）——UI data.events || [] 恒 []（会话详情展开永不显示事件；"
        "真源=GET /api/trajectory/sessions/{id}/events（api/trajectory.py:268-291）返回 "
        "{session_id,events,count}）",
    },
    # ── trajectory search 元素：TrajectoryEvent asdict 字段 ──
    ("trajectory/page.tsx", 361, ("events", "agent_id")): {
        "verdict": _V,
        "evidence": "trajectory/store.py:35-72 TrajectoryEvent dataclass 字段 agent_id"
        "（to_dict=asdict）",
    },
    ("trajectory/page.tsx", 361, ("events", "event_type")): {
        "verdict": _V,
        "evidence": "TrajectoryEvent.event_type 字段在场",
    },
    ("trajectory/page.tsx", 361, ("events", "id")): {
        "verdict": _V,
        "evidence": "TrajectoryEvent.id 字段在场",
    },
    ("trajectory/page.tsx", 361, ("events", "status")): {
        "verdict": _V,
        "evidence": "TrajectoryEvent.status 字段在场",
    },
    # ── users：三元防御（CD-① 已排除）──
    ("users/page.tsx", 38, ("users",)): {
        "verdict": _F,
        "evidence": "CD-① 误报排除：API 回裸数组（response_model=list[UserListItem]），UI "
        "Array.isArray(data) ? data : data.users || [] 三元防御——数组分支真消费，users 只是"
        "回落访问",
    },
    # ── vision：endpoint 变量双值均含 image_base64/format ──
    ("vision/page.tsx", 155, ("image_base64",)): {
        "verdict": _V,
        "evidence": "api/vision.py 图表/散点/思维导图 json 端点返回字面量含 image_base64"
        "（:210/:256；mindmap txt 分支如实无图=既有语义）",
    },
    ("vision/page.tsx", 156, ("format",)): {
        "verdict": _V,
        "evidence": "api/vision.py:204/:251 返回字面量含 format",
    },
    # ── workspace dir entries 元素：for-loop dict 字面量（普查提取边界）──
    ("workspace/page.tsx", 179, ("entries", "hidden")): {
        "verdict": _V,
        "evidence": "api/workspace_api.py:58-92 list_directory entries 元素字面量 "
        "{name,path,type,size,modified,hidden} 含 hidden（for-loop dict 字面量=普查"
        "提取边界，源码读取定型）",
    },
    ("workspace/page.tsx", 179, ("entries", "name")): {
        "verdict": _V,
        "evidence": "同上：元素字面量含 name",
    },
    ("workspace/page.tsx", 179, ("entries", "size")): {
        "verdict": _V,
        "evidence": "同上：元素字面量含 size",
    },
    ("workspace/page.tsx", 179, ("entries", "type")): {
        "verdict": _V,
        "evidence": "同上：元素字面量含 type",
    },
}

# 深挖扩面发现（普查未列、随 confirmed_drift 同根同拍登记，不进 findings join）
EXPANDED_FINDINGS = [
    {
        "page": "dedup/page.tsx",
        "keys": ["id_a", "id_b", "content_type", "created_at_a", "created_at_b"],
        "verdict": _D,
        "repair_candidate": "①dedup 元素键收编",
        "note": "DuplicatePair 接口平铺形消费键普查未逐键列出（消费面启发式只抓到 "
        "similarity/title_a/title_b），与 title_a/title_b 同元素同根：真源元素 "
        "{item_a:{id,title},item_b:{id,title},similarity} 无此 5 键（id_a→item_a.id、"
        "created_at_* 与 content_type 真源根本不产出=删除按钮/过滤/时间显示全部失效）",
    },
]


def build_artifact(check_only: bool = False) -> dict:
    post = json.loads(POST_FIX.read_text(encoding="utf-8"))
    findings = [f for f in post["findings"] if f["verdict"] != "produced"]
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
            f"verdict 覆盖不完整：missing={missing!r} extra={extra!r}（完整性门拒出工件）"
        )
    artifact = {
        "source_artifact": str(POST_FIX.relative_to(ROOT)),
        "counts_input_findings": len(findings),
        "counts": {
            "resolved_ok": sum(1 for r in rows if r["verdict"] == _V),
            "confirmed_drift": sum(1 for r in rows if r["verdict"] == _D),
            "ruled_false_positive": sum(1 for r in rows if r["verdict"] == _F),
        },
        "repair_candidates": sorted({r["repair_candidate"] for r in rows if "repair_candidate" in r}),
        "verdicts": rows,
        "expanded_findings": EXPANDED_FINDINGS,
        "tool_misattribution_notes": [
            {"page": r["page"], "line": r["line"], "note": r["tool_misattribution"]}
            for r in rows
            if "tool_misattribution" in r
        ],
        "honest_boundary": [
            "裁决=源码真源静态读取+调用点归属复核，非逐页 live 探针",
            "普查消费面启发式漏收（如 dedup 平铺键只抓到 3/8）→深挖扩面登记，非普查缺陷定罪",
            "工具归属漂移（gland/system 两处挂错 endpoint）按真 endpoint 判定并记 note",
        ],
    }
    if not check_only:
        OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验覆盖完整性不写工件")
    args = ap.parse_args()
    artifact = build_artifact(check_only=args.check)
    print(json.dumps({k: artifact[k] for k in ("counts_input_findings", "counts", "repair_candidates")}, ensure_ascii=False))
    if not args.check:
        print(f"written: {OUT}")


if __name__ == "__main__":
    main()
