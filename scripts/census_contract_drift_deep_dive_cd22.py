#!/usr/bin/env python3
"""CD-㉒ unresolved_shape 109 条逐项深挖裁决——CD-⑨ 普查（scripts/census_contract_drift.py）
未裁决层 unresolved_shape 类（末类）的裁决单一源。

背景：CD-⑩ 已裁决 drift_hard 13 + unresolved_endpoint 11，CD-⑯ 已裁决 drift_suspect 18，
CD-⑲ 已裁决 unresolved_path 7，均留档明示「unresolved_shape 109 仍留待裁决层」。
本工具是 unresolved_shape 109 条的**裁决单一源**（CD-⑩/CD-⑯/CD-⑲ 同哲学、同纪律）：

- key 级 verdict 三级：resolved_ok（深挖定型=消费键对真源在场、消费路径可达）/
  confirmed_drift（真断点：键真缺席或消费路径不可达，登记 repair_candidate）/
  ruled_false_positive（消费点非缺陷：容错分支/本地构造形/普查归属误挂，无显示行为损失）。
- 每条裁决必须带 evidence（源码真源 file:line 锚点+推理链），禁无据断言。
- anchors（file, line, substring）逐条程序化核验：裁决说在场的键必须真在当前源码
  的锚点行在场（防无据断言+防证据与源码漂移），核验不过=拒出工件。
- build_artifact() 从 post_cd9 工件消费 unresolved_shape 逐条 join 裁决；任何 finding
  无裁决=拒绝出工件，反向多出的裁决同样报错（完整性门）。
- 深挖扩面发现（普查未列但同根）以 EXPANDED_FINDINGS 登记，不进 findings join。

unresolved_shape 的普查 note（「形状提取失败」/「元素形状未解析」）=census 静态形状
提取边界自述（handler 返回体是变量/dict(r)/LLM JSON/Pydantic 模型，静态提取不出键集）；
裁决逐条把返回体追到生产层（service/engine/DDL/Pydantic 模型/prompt 声明面）定型。

用法：
    python3 scripts/census_contract_drift_deep_dive_cd22.py          # 重新生成工件
    python3 scripts/census_contract_drift_deep_dive_cd22.py --check  # 只校验不写

诚实边界（与 CD-①/CD-③/CD-⑨/CD-⑩/CD-⑯/CD-⑲ 工具边界互补）：
- 裁决依据=源码真源静态读取（生产层返回字面量/DDL/模型字段/UI 消费点），evidence 中
  引用的 live/控制组证据为裁决时点记录（2026-10-19），本工具不重跑 live。
- 范围纪律：只裁决 unresolved_shape 一类（工件 109 条）；本类为 CD-⑨ 未裁决层末类。
- cortex.think 返回体是 LLM JSON 直通（动态形状）：按 SYSTEM_PROMPT 声明面定型，
  实际 LLM 输出可能偏离 prompt（如实记，非本裁决可证伪）。
- repair_candidate 只登记不执行（一候选一拍，执行纪律#7）；recent_count 一条的
  修复已由 CD-⑮ 先行落地，以 repair_status 如实标注；agent recall 一条的修复已由
  CD-㉓ 落地、benchmark results 延迟键一条的修复已由 CD-㉔ 落地，同款标注（锚点改锁修复后状态）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POST_CD9 = ROOT / "data" / "census_contract_drift_post_cd9.json"
OUT = ROOT / "data" / "census_contract_drift_deep_dive_cd22.json"

TARGET_CENSUS_VERDICTS = ("unresolved_shape",)

# ── 裁决单一源 ──────────────────────────────────────────────────────────
# key: (census handler 串, ".".join(consumed_key_path))；value: {verdict, evidence, ...}

_V = "resolved_ok"
_D = "confirmed_drift"
_F = "ruled_false_positive"

AP = "src/api/agent_proxy.py"
AG = "src/api/ai_groups.py"
BE = "src/benchmark/engine.py"
CA = "src/api/config_api.py"
CT = "src/cortex/chain_of_thought.py"
DP = "src/services/deduplication.py"
ES = "src/api/event_stream.py"
GR = "src/gland/router.py"
EN = "src/services/entity.py"
MN = "src/main.py"
HB = "src/api/hermes_bridge.py"
AC = "src/immune/access_control.py"
RL = "src/immune/rate_limiter.py"
KN = "src/services/knowledge.py"
CE = "src/learn/course_engine.py"
LC = "src/link/connector.py"
NT = "src/nest/tenant.py"
PT = "src/pulse/timer.py"
SD = "src/api/soma_discovery.py"
TP = "src/api/topology.py"
WA = "src/api/workspace_api.py"
RG = "src/services/rag.py"

U_AN = "admin-ui/app/agent-nodes/page.tsx"
U_BP = "admin-ui/app/benchmark/page.tsx"
U_CP = "admin-ui/app/cortex/page.tsx"
U_CX = "admin-ui/app/connectors/page.tsx"
U_KG = "admin-ui/app/knowledge-graph/page.tsx"
U_LP = "admin-ui/app/learn/page.tsx"
U_PP = "admin-ui/app/pulse/page.tsx"
U_SP = "admin-ui/app/system/page.tsx"


def _bench_shape() -> dict:
    """benchmark.run_benchmark / quick_benchmark 共用返回面（两 handler 同一执行体）。"""
    return {
        "results": {
            "verdict": _V,
            "evidence": "engine.run_benchmark 返回 dict 含 results（src/benchmark/engine.py:171）"
            "，handler 透传同一执行体（api/benchmark.py:94 run/:114 quick）；UI "
            "lastResult.results 消费（benchmark/page.tsx:235/:243）可达",
            "anchors": [(BE, 171, '"results": [self._result_to_dict(r) for r in results],')],
        },
        "concurrency": {
            "verdict": _V,
            "evidence": "返回 dict 含 concurrency（src/benchmark/engine.py:170）；UI "
            "lastResult.concurrency 消费（benchmark/page.tsx:238）可达",
            "anchors": [(BE, 170, '"concurrency": concurrency,')],
        },
        "iterations": {
            "verdict": _V,
            "evidence": "返回 dict 含 iterations（src/benchmark/engine.py:169）；UI "
            "lastResult.iterations 消费（benchmark/page.tsx:238）可达",
            "anchors": [(BE, 169, '"iterations": iterations,')],
        },
        "organs": {
            "verdict": _D,
            "repair_candidate": "已收口：CD-㉕ benchmark 运行结果目标列表收编真源（2026-10-19）",
            "repair_status": "landed@CD-㉕",
            "evidence": "真源顶层键是 organs_benchmarked（计数非数组，src/benchmark/"
            "engine.py:168）无 organs 数组——UI lastResult.organs?.join(', ')（benchmark/"
            "page.tsx:238）恒 undefined→目标列表段恒空渲染=显示行为损失（真源 results[].organ "
            "可得目标名无人拼）；同根扩面：历史行 run.organs 是 JSON 串（benchmark_runs.organs "
            "列 json.dumps(targets)）被当 string[] 调 .join=渲染必炸 TypeError——CD-㉕ 已收口"
            "（UI 目标列表改从真源 results[].organ 派生拼接、BenchmarkRun 按两真源形拆分"
            "BenchmarkRunResult/BenchmarkRunRow，历史行 organs JSON 串收编 runOrgansLabel，"
            "2026-10-19），当前 benchmark/page.tsx 已无 lastResult.organs/.organs?.join 消费="
            "修复真落地；锚点改锁修复后状态（lastResult.organs 漂移复发先炸）",
            "anchors": [
                (BE, 168, '"organs_benchmarked": len(results),'),
                (U_BP, 281, "lastResult.results.map((r) => r.organ)"),
            ],
        },
        "results.avg_ms": {
            "verdict": _D,
            "repair_candidate": "已收口：CD-㉔ benchmark results 元素延迟键收编真源（2026-10-19）",
            "repair_status": "landed@CD-㉔",
            "evidence": "结果元素 _result_to_dict 平铺键恰 organ/label/iterations/success/"
            "errors/latency{...}/throughput_rps/total_duration_ms/timestamp（src/benchmark/"
            "engine.py:356-374），avg_ms 在 latency 嵌套内（:366）无平铺 avg_ms——UI r.avg_ms"
            "（benchmark/page.tsx:246）恒 undefined（数值着色分支恒假+formatMs(undefined) "
            "假渲染），真值 latency.avg_ms 无人读（CD-⑳ 回落遮蔽同型）；同根平铺漂移 "
            "p50/p95/p99/min/success_rate 以 EXPANDED_FINDINGS 登记——CD-㉔ 已收口（UI "
            "元素消费收编 latency 嵌套键、success_rate 真源不产出改 success/iterations "
            "派生，2026-10-19），当前 benchmark/page.tsx 运行结果块已无平铺 r.avg_ms 消费="
            "修复真落地；锚点改锁修复后状态（r.latency.avg_ms 漂移复发先炸）",
            "anchors": [
                (BE, 363, '"latency": {'),
                (BE, 366, '"avg_ms": r.avg_ms,'),
                (U_BP, 289, "r.latency.avg_ms"),
            ],
        },
        "results.organ": {
            "verdict": _V,
            "evidence": "结果元素 _result_to_dict 含 organ（src/benchmark/engine.py:358）；UI "
            "r.organ 消费（benchmark/page.tsx:245）可达",
            "anchors": [(BE, 358, '"organ": r.organ,')],
        },
    }


SHAPE_VERDICTS = {
    # ── agent recall：rag_query 返回体（answer/sources）与 UI 契约（query/results）漂移 ──
    ("agent.recall:450", "results"): {
        "verdict": _D,
        "repair_candidate": "已收口：CD-㉓ agent recall 响应面消费键收编（2026-10-19）",
        "repair_status": "landed@CD-㉓",
        "evidence": "真源 rag_query 返回恰 {answer,sources}（src/services/rag.py:53，空结果/"
        "无 key 分支 :14/:37 同形）无 results 键——UI recallResults.results（agent-nodes/"
        "page.tsx:515 .length ?? 0、:517 (results||[]).map、:530）恒回落空列表=检索结果面板"
        "恒显示 0 条（显示行为损失），真源 sources[{id,score}] 无人读；UI 另读 recallResults"
        ".query（:515）真源亦不产出、answer 无人读（同根扩面 EXPANDED_FINDINGS）——CD-㉓ 已"
        "收口（UI 收编真源 answer/sources、死键 query/results 清零，2026-10-19），当前 "
        "agent-nodes/page.tsx 已无 recallResults.results/.query 消费=修复真落地；锚点改锁"
        "修复后状态（recallResults.sources 漂移复发先炸）",
        "anchors": [
            (RG, 53, 'return {"answer": answer, "sources": sources}'),
            (U_AN, 520, "recallResults.sources"),
        ],
    },
    # ── agent-proxy CLI Agent 面：handler 内联构造 ──
    ("agent_proxy.list_agents:71", "id"): {
        "verdict": _V,
        "evidence": "handler 内联构造列表元素（src/api/agent_proxy.py:77-85），键 id 真产出"
        "（:79）；UI data.id 消费（agent-proxy/page.tsx:286）可达",
        "anchors": [(AP, 79, '"id": agent_id,')],
    },
    ("agent_proxy.list_agents:71", "name"): {
        "verdict": _V,
        "evidence": "元素键 name 真产出（src/api/agent_proxy.py:80）；UI 消费（agent-proxy/"
        "page.tsx:286）可达",
        "anchors": [(AP, 80, '"name": config["name"],')],
    },
    ("agent_proxy.list_agents:71", "description"): {
        "verdict": _V,
        "evidence": "元素键 description 真产出（src/api/agent_proxy.py:81）；UI 消费"
        "（agent-proxy/page.tsx:286）可达",
        "anchors": [(AP, 81, '"description": config["description"],')],
    },
    ("agent_proxy.list_agents:71", "available"): {
        "verdict": _V,
        "evidence": "元素键 available 真产出（src/api/agent_proxy.py:82）；UI 消费"
        "（agent-proxy/page.tsx:286）可达",
        "anchors": [(AP, 82, '"available": binary_path is not None,')],
    },
    ("agent_proxy.send_to_agent:89", "response"): {
        "verdict": _V,
        "evidence": "AgentResponse 构造点键 response 真产出（src/api/agent_proxy.py:120）；"
        "UI data.response 消费（agent-proxy/page.tsx:262）可达",
        "anchors": [(AP, 120, "response=response,")],
    },
    ("agent_proxy.send_to_agent:89", "success"): {
        "verdict": _V,
        "evidence": "AgentResponse 键 success 真产出（src/api/agent_proxy.py:121）；UI 消费"
        "（agent-proxy/page.tsx:263）可达",
        "anchors": [(AP, 121, "success=proc.returncode == 0,")],
    },
    ("agent_proxy.send_to_agent:89", "error"): {
        "verdict": _V,
        "evidence": "AgentResponse 键 error 真产出（src/api/agent_proxy.py:122，成功路径值 "
        "None=键在场语义）；UI 消费（agent-proxy/page.tsx:264）可达",
        "anchors": [(AP, 122, "error=error,")],
    },
    # ── ai_groups get_group：组行 row_to_dict 直出 + agents/tasks 注入 ──
    ("ai_groups.get_group:806", "id"): {
        "verdict": _V,
        "evidence": "组行 row_to_dict(_load_gated_group(...)) 直出（src/api/ai_groups.py:814），"
        "ai_groups 表列 id 真在场（建表 DDL :59）；UI data.id 消费（ai-groups/page.tsx:181）可达",
        "anchors": [(AG, 814, "row_to_dict(_load_gated_group(")],
    },
    ("ai_groups.get_group:806", "name"): {
        "verdict": _V,
        "evidence": "组行直出（src/api/ai_groups.py:814），表列 name 真在场（DDL :60）；UI 消费"
        "（ai-groups/page.tsx:438）可达",
        "anchors": [(AG, 814, "row_to_dict(_load_gated_group("), (AG, 60, "name TEXT NOT NULL,")],
    },
    ("ai_groups.get_group:806", "description"): {
        "verdict": _V,
        "evidence": "组行直出（src/api/ai_groups.py:814），表列 description 真在场（DDL :61）；"
        "UI 消费（ai-groups/page.tsx:439）可达",
        "anchors": [(AG, 61, "description TEXT DEFAULT '',")],
    },
    ("ai_groups.get_group:806", "created_at"): {
        "verdict": _V,
        "evidence": "组行直出（src/api/ai_groups.py:814），表列 created_at 真在场（DDL :64）；"
        "UI 消费（ai-groups/page.tsx:441）可达",
        "anchors": [(AG, 64, "created_at TEXT NOT NULL")],
    },
    ("ai_groups.get_group:806", "agents"): {
        "verdict": _V,
        "evidence": "handler 注入 group[\"agents\"]（src/api/ai_groups.py:815）真产出；UI 消费"
        "（ai-groups/page.tsx:460）可达",
        "anchors": [(AG, 815, 'group["agents"] = rows_to_list(')],
    },
    ("ai_groups.get_group:806", "tasks"): {
        "verdict": _V,
        "evidence": "handler 注入 group[\"tasks\"]（src/api/ai_groups.py:821）真产出；UI 消费"
        "（ai-groups/page.tsx:464）可达",
        "anchors": [(AG, 821, 'group["tasks"] = rows_to_list(')],
    },
    # ── benchmark 两 handler：_bench_shape() 共用 ──
    **{
        ("benchmark.quick_benchmark:100", k): v for k, v in _bench_shape().items()
    },
    **{
        ("benchmark.run_benchmark:81", k): v for k, v in _bench_shape().items()
    },
    # ── config_api organs 元素：恰 key/enabled/config 三键 ──
    ("config_api.list_organs_config:47", "organs.enabled"): {
        "verdict": _V,
        "evidence": "元素键 enabled 真产出（src/api/config_api.py:57）；UI organs.filter(o => "
        "o.enabled) 消费（system/page.tsx:202）可达",
        "anchors": [(CA, 57, '"enabled": cfg.get("enabled", True),')],
    },
    ("config_api.list_organs_config:47", "organs.key"): {
        "verdict": _V,
        "evidence": "元素键 key 真产出（src/api/config_api.py:56）；UI organ.key 消费"
        "（system/page.tsx:387）可达",
        "anchors": [(CA, 56, '"key": organ,')],
    },
    ("config_api.list_organs_config:47", "organs.status"): {
        "verdict": _F,
        "evidence": "census 归属误挂（提取边界）：o.status 消费点是 organHealth 量（system/"
        "page.tsx:203 organHealth.filter(o => o.status==='ok')），organHealth 取自 GET "
        "/api/diagnostics/check-all 的 data.organs（system/page.tsx:175→setOrganHealth，"
        "OrganHealth 接口含 status）——与 GET /api/organs 的 data.organs（fetchOrgans）是两个"
        "同名 data.organs 别名链被普查合并归属；真源元素恰 key/enabled/config 三键"
        "（src/api/config_api.py:56-58）且 UI 的 /api/organs 元素只消费 enabled/key，无显示"
        "行为损失（消费点非缺陷：普查归属误挂）",
        "anchors": [
            (CA, 58, '"config": cfg,'),
            (U_SP, 226, "organHealth.filter(o => o.status"),
            (U_SP, 198, "/api/diagnostics/check-all"),
        ],
    },
    # ── cortex think：LLM JSON 直通，按 SYSTEM_PROMPT 声明面定型 ──
    ("cortex.think:128", "answer"): {
        "verdict": _V,
        "evidence": "返回体=LLM JSON 直通（chain_of_thought._call_llm json.loads，src/cortex/"
        "chain_of_thought.py:88）+reflections；SYSTEM_PROMPT 声明 JSON 键含 answer"
        "（:19）——prompt 声明面产出（动态形状，普查「形状提取失败」=提取边界）；UI "
        "thinkResult.answer 消费（cortex/page.tsx:404）可达",
        "anchors": [(CT, 19, '"answer": "your final answer",')],
    },
    ("cortex.think:128", "confidence"): {
        "verdict": _V,
        "evidence": "SYSTEM_PROMPT 声明 JSON 键含 confidence（src/cortex/chain_of_thought.py:20）"
        "，think() 亦以 result.get(\"confidence\") 驱动反思循环（:55）=生产面真实依赖此键；UI "
        "thinkResult.confidence 消费（cortex/page.tsx:410）可达",
        "anchors": [(CT, 20, '"confidence": 0.0 to 1.0')],
    },
    ("cortex.think:128", "reasoning_steps"): {
        "verdict": _D,
        "repair_candidate": "已收口：CD-㉖ cortex 推理步骤收编真源（reasoning_steps↔reasoning 漂移）（2026-10-19）",
        "repair_status": "landed@CD-㉖",
        "evidence": "SYSTEM_PROMPT 声明键是 reasoning（字符串，src/cortex/chain_of_thought.py:17）"
        "无 reasoning_steps（数组）——UI thinkResult.reasoning_steps && .length>0"
        "（cortex/page.tsx:391）恒 falsy=推理步骤块永不渲染（显示行为损失），真源 reasoning "
        "字符串无人读；动态形状按 prompt 声明面定型（实际 LLM 输出可能偏离 prompt，诚实边界）"
        "——CD-㉖ 已收口（UI 推理步骤块改收编真源 reasoning 字符串逐字渲染（whitespace-pre-wrap "
        "不 fabricate 步骤结构）、死声明键 reasoning_steps/question 双清零，2026-10-19），当前 "
        "cortex/page.tsx 已无 thinkResult.reasoning_steps 消费=修复真落地；锚点改锁修复后状态"
        "（reasoning_steps 漂移复发先炸）",
        "anchors": [
            (CT, 17, '"reasoning": "step-by-step logical deduction",'),
            (U_CP, 390, "{thinkResult.reasoning && ("),
        ],
    },
    # ── dedup：get_duplicate_report 元素 + deduplicate_knowledge 返回面 ──
    # 契约随更新（2026-10-19 DEC-㉕-①/DEDUP-① 真源补现取 B）：元素 item 内补
    # created_at/content_type（真列直出），元素字面量随 +18 行位移 133-139→136-155、
    # similarity 锚点 137→151 同步（断言意图零丢失：similarity 真产出锁不变）。
    ("dedup.list_duplicates:27", "duplicates.similarity"): {
        "verdict": _V,
        "evidence": "report 元素恰 {item_a,item_b,similarity}（src/services/deduplication.py:"
        "136-155），similarity 真产出（:151）；handler 包装 {total_pairs,duplicates}"
        "（api/dedup.py:34）后 UI 消费（dedup/page.tsx）可达",
        "anchors": [(DP, 151, '"similarity": round(sim, 2),')],
    },
    ("dedup.run_deduplication:17", "total"): {
        "verdict": _V,
        "evidence": "deduplicate_knowledge result 字面量键 total 真产出（src/services/"
        "deduplication.py:85）；UI 消费（dedup/page.tsx:298）可达",
        "anchors": [(DP, 85, '"total": len(items),')],
    },
    ("dedup.run_deduplication:17", "duplicates_found"): {
        "verdict": _V,
        "evidence": "result 键 duplicates_found 真产出（src/services/deduplication.py:86）；"
        "UI 消费（dedup/page.tsx:290）可达",
        "anchors": [(DP, 86, '"duplicates_found": len(duplicates),')],
    },
    ("dedup.run_deduplication:17", "duplicates_removed"): {
        "verdict": _V,
        "evidence": "result 键 duplicates_removed 真产出（src/services/deduplication.py:87）；"
        "UI 消费（dedup/page.tsx:294）可达",
        "anchors": [(DP, 87, '"duplicates_removed": len(removed_ids),')],
    },
    ("dedup.run_deduplication:17", "details"): {
        "verdict": _V,
        "evidence": "result 键 details 真产出（src/services/deduplication.py:89）；UI 消费"
        "（dedup/page.tsx:302）可达",
        "anchors": [(DP, 89, '"details": duplicates,')],
    },
    ("dedup.run_deduplication:17", "details.id"): {
        "verdict": _V,
        "evidence": "details 元素键 id 真产出（src/services/deduplication.py:57）；UI 消费"
        "（dedup/page.tsx:304）可达",
        "anchors": [(DP, 57, '"id": group[j]["id"],')],
    },
    ("dedup.run_deduplication:17", "details.title"): {
        "verdict": _V,
        "evidence": "details 元素键 title 真产出（src/services/deduplication.py:58）；UI 消费"
        "（dedup/page.tsx:304）可达",
        "anchors": [(DP, 58, '"title": group[j]["title"],')],
    },
    ("dedup.run_deduplication:17", "details.duplicate_of"): {
        "verdict": _V,
        "evidence": "details 元素键 duplicate_of 真产出（src/services/deduplication.py:59）；"
        "UI 消费（dedup/page.tsx:304）可达",
        "anchors": [(DP, 59, '"duplicate_of": group[i]["id"],')],
    },
    ("dedup.run_deduplication:17", "details.similarity"): {
        "verdict": _V,
        "evidence": "details 元素键 similarity 真产出（src/services/deduplication.py:60）；"
        "UI 消费（dedup/page.tsx:304）可达",
        "anchors": [(DP, 60, '"similarity": round(sim, 2),')],
    },
    # ── event_stream get_summary：修前恰 5 键（DEC-㉗a-① 补现 recent_count 后 6 键，
    #    CD-⑮ 已裁决 recent_count 同根；行号锚点随补现位移同步=断言意图零丢失）──
    ("event_stream.get_summary:822", "total_events"): {
        "verdict": _V,
        "evidence": "alias 端点 get_summary 委托 get_stream_summary（src/api/event_stream.py:"
        "839），返回键 total_events 真产出（:816）；UI 消费（logs/page.tsx:250）可达"
        "（勘误：修前记「返回恰 5 键（:801-805）」，DEC-㉗a-① 补现后 6 键、行号位移）",
        "anchors": [(ES, 816, '"total_events": len(events),')],
    },
    ("event_stream.get_summary:822", "by_organ"): {
        "verdict": _V,
        "evidence": "返回键 by_organ 真产出（src/api/event_stream.py:817）；UI 消费"
        "（logs/page.tsx:271）可达",
        "anchors": [(ES, 817, '"by_organ": by_organ,')],
    },
    ("event_stream.get_summary:822", "by_type"): {
        "verdict": _V,
        "evidence": "返回键 by_type 真产出（src/api/event_stream.py:818）；UI 消费"
        "（logs/page.tsx:264，DEC-㉗b-① 起过 problemEventCount 词表计数）可达",
        "anchors": [(ES, 818, '"by_type": by_type,')],
    },
    ("event_stream.get_summary:822", "recent_count"): {
        "verdict": _D,
        "repair_candidate": "已收口：DEC-㉗a-① 真源补现 recent_count（B1 近 24h 窗口计数）+"
        "最近事件卡恢复（2026-10-20）；此前 CD-⑮ 收口=死显示卡移除（2026-10-19，commit "
        "049af5b5）已按拍板改道（异议回「㉗a改A/改B2」即 revert/改道）",
        "repair_status": "landed@DEC-㉗a-①",
        "evidence": "修前真源恰 5 键零 recent_count 产出——census 时点 UI 最近事件卡恒假 0；"
        "CD-⑮ 已裁决 confirmed_drift 并按死显示面收口（卡移除，commit 049af5b5）；"
        "DEC-㉗a-① 按 pending-decisions 自决先例取 B1 真源补现（get_stream_summary 加性键 "
        "recent_count=近 24h 窗口计数，事件时刻解释 _event_ts 单一源）+UI 卡恢复。"
        "本条=CD-⑨ 未裁决层同根登记，confirmed_drift 定性（修前形态）不翻。",
        "anchors": [
            (ES, 820, '"recent_count": recent_count,'),
            (ES, 716, "RECENT_WINDOW_S = 24 * 3600"),
            ("admin-ui/app/logs/page.tsx", 257, "summary?.recent_count"),
        ],
    },
    # ── gland test_connection：test_provider 成功形 4 键（错误形 handler 已升 502 不出线）──
    # 锚点随位移同步（DEC-㉖B2-①：router.py +4 行（chat/embed record 调用点带 task）、
    # gland/page.tsx +3 行（接口 task 声明+task 列）——断言意图零丢失，历史裁决文本不改写）。
    # 锚点随位移同步（DEC-㉖B2-① 先例；RV-①b-①：router.py +5 行（usage_persist
    # import + persist_usage 接线）——断言意图零丢失，历史裁决文本不改写）。
    ("gland.test_connection:175", "status"): {
        "verdict": _V,
        "evidence": "gateway.test_provider 成功返回含 status（src/gland/router.py:1009）；错误形"
        "（:987/:992/:1016）被 handler 升 502（api/gland.py:188-189）不出线；UI 消费"
        "（gland/page.tsx:185）可达",
        "anchors": [(GR, 1012, '"status": "ok",')],
    },
    ("gland.test_connection:175", "provider"): {
        "verdict": _V,
        "evidence": "成功返回键 provider 真产出（src/gland/router.py:1010）；UI 消费"
        "（gland/page.tsx:184）可达",
        "anchors": [(GR, 1013, '"provider": provider_name,')],
    },
    ("gland.test_connection:175", "model"): {
        "verdict": _V,
        "evidence": "成功返回键 model 真产出（src/gland/router.py:1011）；UI 消费"
        "（gland/page.tsx:186）可达",
        "anchors": [(GR, 1014, '"model": model,')],
    },
    ("gland.test_connection:175", "reply"): {
        "verdict": _V,
        "evidence": "成功返回键 reply 真产出（src/gland/router.py:1012）；UI 消费"
        "（gland/page.tsx:186）可达",
        "anchors": [(GR, 1015, '"reply": reply.strip(),')],
    },
    # ── graph get_entity_detail：entities 行直出 + relations 注入 ──
    ("graph.get_entity_detail:71", "id"): {
        "verdict": _V,
        "evidence": "实体行 dict(row) 直出（src/services/entity.py:12 _row_to_entity），"
        "entities 表列 id 真在场（DDL src/main.py:293）；UI selectedEntity.id 消费"
        "（knowledge-graph/page.tsx:530/:611）可达",
        "anchors": [(EN, 12, "d = dict(row)")],
    },
    ("graph.get_entity_detail:71", "name"): {
        "verdict": _V,
        "evidence": "实体行直出（src/services/entity.py:12），表列 name 真在场（DDL src/main.py:"
        "294）；UI selectedEntity.name 消费（knowledge-graph/page.tsx:593）可达",
        "anchors": [(EN, 12, "d = dict(row)"), (MN, 294, "name TEXT NOT NULL, type TEXT DEFAULT '',")],
    },
    ("graph.get_entity_detail:71", "description"): {
        "verdict": _V,
        "evidence": "实体行直出（src/services/entity.py:12），表列 description 真在场（DDL "
        "src/main.py:295）；UI 消费（knowledge-graph/page.tsx:603/:606）可达",
        "anchors": [(MN, 295, "description TEXT DEFAULT '', properties TEXT DEFAULT '{}',")],
    },
    ("graph.get_entity_detail:71", "properties"): {
        "verdict": _V,
        "evidence": "实体行直出+properties 归一为 dict（src/services/entity.py:17-24），表列 "
        "properties 真在场（DDL src/main.py:295）；UI 消费（knowledge-graph/page.tsx:634）可达",
        "anchors": [(MN, 295, "description TEXT DEFAULT '', properties TEXT DEFAULT '{}',")],
    },
    ("graph.get_entity_detail:71", "relations"): {
        "verdict": _V,
        "evidence": "get_entity_with_relations 注入 entity[\"relations\"]（src/services/entity.py:"
        "68）真产出；UI selectedEntity.relations 消费（knowledge-graph/page.tsx:613）可达",
        "anchors": [(EN, 68, 'entity["relations"] = [dict(r) for r in relations]')],
    },
    ("graph.get_entity_detail:71", "relations.id"): {
        "verdict": _V,
        "evidence": "relations 元素=dict(r)（r.* 行直出，src/services/entity.py:68），relations "
        "表列 id 真在场（DDL src/main.py:298）；UI rel.id 消费（knowledge-graph/page.tsx:620）可达",
        "anchors": [(MN, 298, "id TEXT PRIMARY KEY, source_id TEXT NOT NULL,")],
    },
    ("graph.get_entity_detail:71", "relations.source_entity_id"): {
        "verdict": _D,
        "repair_candidate": "已收口：CD-㉗ knowledge-graph relations 元素键收编（2026-10-19）",
        "repair_status": "landed@CD-㉗",
        "evidence": "relations 行键恰 id/source_id/target_id/type/properties/created_at+JOIN 别名 "
        "source_name/target_name（DDL src/main.py:298-300+SELECT r.* src/services/entity.py:60-68）"
        "无 source_entity_id——UI rel.source_entity_id（knowledge-graph/page.tsx:618 isSource 判定"
        "恒 false、:626 rel.source_entity_id.slice(0,8) 对 undefined 调 slice=TypeError 渲染崩坏"
        "面）；同根 target_entity_id/relation_type 以 EXPANDED_FINDINGS 登记——CD-㉗ 已收口（UI "
        "元素接口收编真源行键形 {id,source_id,target_id,type}+isSource/徽章/切片三消费点收编真源"
        "键，2026-10-19），当前 knowledge-graph/page.tsx 已无 rel.source_entity_id/"
        "rel.target_entity_id/rel.relation_type 消费=修复真落地；锚点改锁修复后状态（rel.source_id "
        "漂移复发先炸）",
        "anchors": [
            (MN, 298, "id TEXT PRIMARY KEY, source_id TEXT NOT NULL,"),
            (U_KG, 617, "rel.source_id === selectedEntity.id"),
        ],
    },
    ("graph.get_entity_detail:71", "type"): {
        "verdict": _D,
        "repair_candidate": "已收口：CD-㉘ knowledge-graph 实体类型收编真源（2026-10-19）",
        "repair_status": "landed@CD-㉘",
        "evidence": "_row_to_entity 把行键 type 重命名为 entity_type（src/services/entity.py:13 "
        "d[\"entity_type\"] = d.pop(\"type\", \"\"))——响应面无 type 键；UI selectedEntity.type"
        "（knowledge-graph/page.tsx:592/:594/:595 三处）恒 undefined=类型徽章/着色恒空"
        "（显示行为损失）——CD-㉘ 已收口（UI EntityDetail 接口 type→entity_type 收编真源键+"
        "12 消费点（列表过滤/搜索/列表渲染×3/详情图标/详情徽章×2/关系表单选项×2/接口声明）"
        "收编真源键，2026-10-19）；同根 e.type 列表/选项面以 EXPANDED_FINDINGS 登记——当前 "
        "knowledge-graph/page.tsx 已无 selectedEntity.type/entity.type/e.type 消费=修复真落地；"
        "锚点改锁修复后状态（selectedEntity.type 漂移复发先炸）",
        "anchors": [
            (EN, 13, 'd["entity_type"] = d.pop("type", "")'),
            (U_KG, 591, "selectedEntity.entity_type"),
        ],
    },
    # ── hermes_bridge list_sessions：_list_sessions_sync 返回 {sessions,total} ──
    ("hermes_bridge.list_sessions:125", "sessions"): {
        "verdict": _V,
        "evidence": "_list_sessions_sync 返回 {sessions,total}（src/api/hermes_bridge.py:119），"
        "handler 透传（:136）；UI data.sessions 消费（hermes/page.tsx:170）可达",
        "anchors": [(HB, 119, '"sessions": sessions, "total": len(sessions)')],
    },
    ("hermes_bridge.list_sessions:125", "total"): {
        "verdict": _V,
        "evidence": "返回键 total 真产出（src/api/hermes_bridge.py:119）；UI 消费"
        "（hermes/page.tsx:171）可达",
        "anchors": [(HB, 119, '"sessions": sessions, "total": len(sessions)')],
    },
    # ── immune：ip 列表元素 + rate_limiter stats ──
    ("immune.list_ip_lists:265", "blacklist.ip"): {
        "verdict": _V,
        "evidence": "list_blacklist 元素含 ip（src/immune/access_control.py:72），handler 组装 "
        "{blacklist,whitelist}（api/immune.py:268-271）；UI 消费（immune/page.tsx:343）可达",
        "anchors": [(AC, 141, '"ip": e.ip,')],
    },
    ("immune.list_ip_lists:265", "blacklist.reason"): {
        "verdict": _V,
        "evidence": "list_blacklist 元素含 reason（src/immune/access_control.py:73）；UI 消费"
        "（immune/page.tsx:343）可达",
        "anchors": [(AC, 142, '"reason": e.reason,')],
    },
    ("immune.list_ip_lists:265", "whitelist.ip"): {
        "verdict": _V,
        "evidence": "list_whitelist 元素含 ip（src/immune/access_control.py:84）；UI 消费"
        "（immune/page.tsx:365）可达",
        "anchors": [(AC, 153, '"ip": e.ip,')],
    },
    ("immune.list_ip_lists:265", "whitelist.reason"): {
        "verdict": _V,
        "evidence": "list_whitelist 元素含 reason（src/immune/access_control.py:84）；UI 消费"
        "（immune/page.tsx:365）可达",
        "anchors": [(AC, 153, '"reason": e.reason,')],
    },
    ("immune.rate_limit_stats:169", "config"): {
        "verdict": _V,
        "evidence": "rate_limiter.stats() 返回含 config（src/immune/rate_limiter.py:97），handler "
        "透传（api/immune.py:172）；UI 消费（immune/page.tsx:518）可达",
        "anchors": [(RL, 97, '"config": {')],
    },
    # ── knowledge list_knowledge：行直出（k.* + tags）──
    ("knowledge.list_knowledge:131", "map.content"): {
        "verdict": _V,
        "evidence": "列表元素=_row_out(dict(r)) 行直出（src/services/knowledge.py:260），行含 "
        "knowledge 表列 content（DDL src/main.py:282），_row_out 只归一 metadata/tags 不改键名"
        "（:24-39）；UI 消费（patterns/page.tsx:241）可达",
        "anchors": [
            (KN, 260, "result.append(_row_out(dict(r)))"),
            (MN, 282, "title TEXT NOT NULL, content TEXT DEFAULT '',"),
        ],
    },
    ("knowledge.list_knowledge:131", "map.metadata"): {
        "verdict": _V,
        "evidence": "行含表列 metadata（DDL src/main.py:284），_row_out 解析 JSON 为 dict 保留键名"
        "（src/services/knowledge.py:33-37）；UI 消费（patterns/page.tsx:241）可达",
        "anchors": [(MN, 284, "content_type TEXT DEFAULT '', metadata TEXT DEFAULT '{}',")],
    },
    ("knowledge.list_knowledge:131", "map.title"): {
        "verdict": _V,
        "evidence": "行含表列 title（DDL src/main.py:282）；UI 消费（patterns/page.tsx:241）可达",
        "anchors": [(MN, 282, "title TEXT NOT NULL, content TEXT DEFAULT '',")],
    },
    # ── learn learn_stats：course_engine.stats 返回面 ──
    ("learn.learn_stats:425", "total_courses"): {
        "verdict": _V,
        "evidence": "engine.stats 返回含 total_courses（src/learn/course_engine.py:579），handler "
        "透传（api/learn.py:432）；UI stats?.total_courses 消费（learn/page.tsx:599）可达",
        "anchors": [(CE, 579, '"total_courses": len(courses),')],
    },
    ("learn.learn_stats:425", "total_chapters"): {
        "verdict": _V,
        "evidence": "返回键 total_chapters 真产出（src/learn/course_engine.py:580）；UI 消费"
        "（learn/page.tsx:603）可达",
        "anchors": [(CE, 580, '"total_chapters": total_chapters,')],
    },
    ("learn.learn_stats:425", "completed_chapters"): {
        "verdict": _V,
        "evidence": "返回键 completed_chapters 真产出（src/learn/course_engine.py:581）；UI 消费"
        "（learn/page.tsx:607）可达",
        "anchors": [(CE, 581, '"completed_chapters": completed_chapters,')],
    },
    ("learn.learn_stats:425", "total_quizzes"): {
        "verdict": _D,
        "repair_candidate": "已收口：CD-㉙ learn 测验统计卡死显示移除（2026-10-19）",
        "repair_status": "landed@CD-㉙",
        "evidence": "engine.stats 返回键恰 total_courses/total_chapters/completed_chapters/"
        "pending_chapters/reviewing_courses/by_status（src/learn/course_engine.py:578-588）无 "
        "total_quizzes（quiz 无统计轴，仅 get_quiz 逐课口）——UI stats?.total_quizzes ?? 0"
        "（learn/page.tsx:611）恒回落 0=假 0 遮蔽（「测验题」卡恒 0，CD-⑱ 回落假 0 同型）"
        "——CD-㉙ 已收口（死显示卡移除+死声明键 total_quizzes 清零+grid 收 3 列，恢复显示="
        "真源补现候选 pending-decisions ㉙ 不自决，2026-10-19）；当前 learn/page.tsx 已无 "
        "total_quizzes 消费=修复真落地（残留「测验题」字样为章节测验空态/生成提示独立面"
        "不动）；锚点按 CD-⑮ 先例只留真源锚点（死显示移除无修复后消费点可锁，UI 假 0 消费"
        "复发由 test_cd29_repair 源码面锁死）",
        "anchors": [
            (CE, 584, '"by_status": {'),
        ],
    },
    # ── link test_connector：send_webhook 全分支含 success ──
    ("link.test_connector:145", "success"): {
        "verdict": _V,
        "evidence": "manager.send_webhook 全分支返回含 success（src/link/connector.py:220/:222/"
        ":224/:245/:250，成功分支 :245），handler 透传（api/link.py:150）；UI 消费"
        "（link/page.tsx:666）可达",
        "anchors": [(LC, 245, '"success": True, "status_code": resp.status')],
    },
    # ── nest get_tenant：Tenant.to_dict 全键 ──
    ("nest.get_tenant:104", "tenant_id"): {
        "verdict": _V,
        "evidence": "handler 返回 tenant.to_dict()（src/api/nest.py:110），to_dict 含 tenant_id"
        "（src/nest/tenant.py:161）；UI 消费（nest/page.tsx:216）可达",
        "anchors": [(NT, 161, '"tenant_id": self.tenant_id,')],
    },
    ("nest.get_tenant:104", "name"): {
        "verdict": _V,
        "evidence": "to_dict 含 name（src/nest/tenant.py:162）；UI 消费（nest/page.tsx:286）可达",
        "anchors": [(NT, 162, '"name": self.name,')],
    },
    ("nest.get_tenant:104", "tier"): {
        "verdict": _V,
        "evidence": "to_dict 含 tier（src/nest/tenant.py:163）；UI 消费（nest/page.tsx:287）可达",
        "anchors": [(NT, 163, '"tier": self.tier.value,')],
    },
    ("nest.get_tenant:104", "description"): {
        "verdict": _V,
        "evidence": "to_dict 含 description（src/nest/tenant.py:167）；UI 消费（nest/page.tsx:288）可达",
        "anchors": [(NT, 167, '"description": self.description,')],
    },
    ("nest.get_tenant:104", "owner_user_id"): {
        "verdict": _V,
        "evidence": "to_dict 含 owner_user_id（src/nest/tenant.py:166）；UI 消费（nest/page.tsx:289）可达",
        "anchors": [(NT, 166, '"owner_user_id": self.owner_user_id,')],
    },
    ("nest.get_tenant:104", "tags"): {
        "verdict": _V,
        "evidence": "to_dict 含 tags（src/nest/tenant.py:168）；UI 消费（nest/page.tsx:290）可达",
        "anchors": [(NT, 168, '"tags": self.tags,')],
    },
    ("nest.get_tenant:104", "status"): {
        "verdict": _V,
        "evidence": "to_dict 含 status（src/nest/tenant.py:164）；UI 消费（nest/page.tsx:733）可达",
        "anchors": [(NT, 164, '"status": self.status.value,')],
    },
    ("nest.get_tenant:104", "namespace"): {
        "verdict": _V,
        "evidence": "to_dict 含 namespace（src/nest/tenant.py:165）；UI 消费（nest/page.tsx:748）可达",
        "anchors": [(NT, 165, '"namespace": self.namespace,')],
    },
    ("nest.get_tenant:104", "quota"): {
        "verdict": _V,
        "evidence": "to_dict 含 quota（src/nest/tenant.py:171，quota.to_dict()）；UI 消费"
        "（nest/page.tsx:763）可达",
        "anchors": [(NT, 171, '"quota": self.quota.to_dict(),')],
    },
    ("nest.get_tenant:104", "usage"): {
        "verdict": _V,
        "evidence": "to_dict include_usage 缺省 True 含 usage（src/nest/tenant.py:174）；UI 消费"
        "（nest/page.tsx:769）可达",
        "anchors": [(NT, 174, 'd["usage"] = self.usage.to_dict()')],
    },
    # ── pulse get_stats：PulseEngine.get_stats 返回面 ──
    ("pulse.get_stats:160", "total_signals"): {
        "verdict": _V,
        "evidence": "PulseEngine.get_stats 返回含 total_signals（src/pulse/timer.py:198），handler "
        "透传（api/pulse.py:163）；UI stats?.total_signals 消费（pulse/page.tsx:198）可达",
        "anchors": [(PT, 198, '"total_signals": len(signals),')],
    },
    ("pulse.get_stats:160", "total_ticks"): {
        "verdict": _V,
        "evidence": "返回键 total_ticks 真产出（src/pulse/timer.py:197）；UI 消费（pulse/page.tsx:"
        "206）可达",
        "anchors": [(PT, 197, '"total_ticks": self._total_ticks,')],
    },
    ("pulse.get_stats:160", "active_signals"): {
        "verdict": _F,
        "evidence": "真源不产出 active_signals（get_stats 键恰 uptime_seconds/total_ticks/"
        "total_signals/by_type/by_status/precision，src/pulse/timer.py:195-206）但 UI 是本地构造"
        "回落：stats?.active_signals ?? signals.filter(s => s.status==='active').length"
        "（pulse/page.tsx:202）——回落从页面自身信号列表真算活跃数，无显示行为损失"
        "（消费点非缺陷：容错分支+本地构造形）",
        "anchors": [
            (PT, 198, '"total_signals": len(signals),'),
            (U_PP, 202, "stats?.active_signals ?? signals.filter"),
        ],
    },
    ("pulse.get_stats:160", "avg_drift_ms"): {
        "verdict": _D,
        "repair_candidate": "已收口：CD-㉚ pulse 平均漂移收编真源（2026-10-19）",
        "repair_status": "landed@CD-㉚",
        "evidence": "真源 avg_drift_ms 在 precision 嵌套内（src/pulse/timer.py:201-205 "
        "precision{avg_drift_ms,max_drift_ms,sample_size}）无平铺 avg_drift_ms——UI "
        "stats?.avg_drift_ms ? ... : '—'（修前 pulse/page.tsx:206）恒回落 '—'=回落遮蔽真值"
        "（真值 precision.avg_drift_ms 无人读，CD-⑳ elapsed 同型；live 实证真值 "
        "59943.918 被 '—' 遮蔽）——CD-㉚ 已收口（UI 收编 stats.precision.avg_drift_ms "
        "嵌套键+typeof number 判法真 0 显示禁回落遮蔽，2026-10-19），当前 pulse/page.tsx "
        "已无 stats?.avg_drift_ms 平铺消费=修复真落地；锚点改锁修复后状态"
        "（平铺漂移复发先炸）",
        "anchors": [
            (PT, 201, '"precision": {'),
            (U_PP, 210, "stats?.precision?.avg_drift_ms"),
        ],
    },
    # ── soma_discovery database tables：_do_tables→scan() 恒 list 形 ──
    ("soma_discovery.api_database_tables:1694", "tables"): {
        "verdict": _F,
        "evidence": "真源 adapter.execute('tables')→_do_tables→scan() 恒返回 list 形（表项 "
        "[{name,type,columns}] 或 [{error}]，src/api/soma_discovery.py:1071-1072/:863-905）非 "
        "{tables:...} dict；UI 消费是双形容错分支 Array.isArray(dbTables) ? dbTables : "
        "dbTables.tables || []（connectors/page.tsx:600）——数组形走第一分支正常渲染表名 chip"
        "（元素 name 真产出 :904），tables 键只在容错分支读=无显示行为损失（消费点非缺陷："
        "容错分支形）",
        "anchors": [
            (SD, 1072, "        return await self.scan()"),
            (U_CX, 600, "Array.isArray(dbTables)"),
        ],
    },
    # ── soma_discovery full_scan：ScanResult=Pydantic 模型字段面 ──
    ("soma_discovery.api_full_scan:1472", "processes.cpu"): {
        "verdict": _V,
        "evidence": "processes 元素=ProcessInfo 模型（src/api/soma_discovery.py:165-172），字段 "
        "cpu 真在场（:168）；UI p.cpu 消费（soma/page.tsx:82、connectors/page.tsx:432）可达",
        "anchors": [(SD, 168, "    cpu: float")],
    },
    ("soma_discovery.api_full_scan:1472", "processes.mem"): {
        "verdict": _V,
        "evidence": "ProcessInfo 字段 mem 真在场（src/api/soma_discovery.py:169）；UI p.mem 消费"
        "（soma/page.tsx:83、connectors/page.tsx:433——CD-32 收编）可达",
        "anchors": [(SD, 169, "    mem: float")],
    },
    ("soma_discovery.api_full_scan:1472", "processes.pid"): {
        "verdict": _V,
        "evidence": "ProcessInfo 字段 pid 真在场（src/api/soma_discovery.py:166）；UI p.pid 消费"
        "（connectors/page.tsx:429/:430）可达",
        "anchors": [(SD, 166, "    pid: int")],
    },
    ("soma_discovery.api_full_scan:1472", "processes.name"): {
        "verdict": _D,
        "repair_candidate": "已收口：CD-32 connectors 扫描进程面收编真源（name→command/executable）"
        "（2026-10-19）",
        "repair_status": "landed@CD-32",
        "evidence": "ProcessInfo 字段恰 pid/user/cpu/mem/command/executable/description"
        "（src/api/soma_discovery.py:165-172）无 name——UI p.name（修前 connectors/page.tsx:"
        "431）恒 undefined=进程名列恒空渲染（显示行为损失）；同根 p.memory（真源 mem，修前 "
        ":433 p.memory.toFixed 对 undefined 调 toFixed=TypeError 渲染崩坏面）以 EXPANDED_FINDINGS "
        "登记——CD-32 已收口（UI 收编 p.command||p.executable+p.mem.toFixed，同根 p.memory "
        "崩坏面随主件同拍清零，2026-10-19），锚点改锁修复后状态（name/memory 漂移复发先炸）",
        "anchors": [
            (SD, 170, "    command: str"),
            (U_CX, 431, "p.command || p.executable"),
        ],
    },
    ("soma_discovery.api_full_scan:1472", "cli_tools.name"): {
        "verdict": _V,
        "evidence": "cli_tools 元素=CLITool 模型（src/api/soma_discovery.py:175-179），字段 name "
        "真在场（:176）；UI tool.name 消费（connectors/page.tsx:446/:447）可达",
        "anchors": [(SD, 176, "    name: str")],
    },
    ("soma_discovery.api_full_scan:1472", "cli_tools.version"): {
        "verdict": _V,
        "evidence": "CLITool 字段 version 真在场（src/api/soma_discovery.py:178）；UI 消费"
        "（connectors/page.tsx:448、soma/page.tsx:148）可达",
        "anchors": [(SD, 178, "    version: str | None = None")],
    },
    ("soma_discovery.api_full_scan:1472", "services.protocol"): {
        "verdict": _V,
        "evidence": "services 元素=ServiceInfo 模型（src/api/soma_discovery.py:182-189），字段 "
        "protocol 真在场（:183）；UI s.protocol 消费（connectors/page.tsx:464、soma/page.tsx:85）可达",
        "anchors": [(SD, 183, "    protocol: str")],
    },
    ("soma_discovery.api_full_scan:1472", "services.state"): {
        "verdict": _V,
        "evidence": "ServiceInfo 字段 state 真在场（src/api/soma_discovery.py:186）；UI s.state "
        "消费（soma/page.tsx:84）可达",
        "anchors": [(SD, 186, "    state: str")],
    },
    ("soma_discovery.api_full_scan:1472", "services.host"): {
        "verdict": _D,
        "repair_candidate": "已收口：CD-31 connectors 扫描服务面收编真源（name/host/port→"
        "process_name/local_address/local_port）（2026-10-19）",
        "repair_status": "landed@CD-31",
        "evidence": "ServiceInfo 字段恰 protocol/local_address/local_port/state/process_name/pid/"
        "description（src/api/soma_discovery.py:182-189）无 host（真源 local_address :184）——UI "
        "s.host（修前 connectors/page.tsx:462 s.name||s.host、:463 {s.host}:{s.port}）恒 undefined="
        "地址段恒空/裸冒号（显示行为损失）——CD-31 已收口（UI 两消费点收编真源键 s.local_address/"
        "s.local_port，2026-10-19），锚点改锁修复后状态（host/port 漂移复发先炸）",
        "anchors": [(SD, 184, "    local_address: str"), (U_CX, 463, "{s.local_address}:{s.local_port}")],
    },
    ("soma_discovery.api_full_scan:1472", "services.name"): {
        "verdict": _D,
        "repair_candidate": "已收口：CD-31 connectors 扫描服务面收编真源（name/host/port→"
        "process_name/local_address/local_port）（2026-10-19）",
        "repair_status": "landed@CD-31",
        "evidence": "ServiceInfo 无 name（真源 process_name，src/api/soma_discovery.py:187）——UI "
        "s.name||s.host（修前 connectors/page.tsx:462）双回落全 undefined=服务名恒空渲染"
        "（显示行为损失）——CD-31 已收口（UI 收编 s.process_name||s.local_address（process_name "
        "缺省回落地址，回落语义保留），2026-10-19），锚点改锁修复后状态（name/host 漂移复发先炸）",
        "anchors": [(SD, 187, "    process_name: str | None = None"), (U_CX, 462, "s.process_name || s.local_address")],
    },
    ("soma_discovery.api_full_scan:1472", "services.port"): {
        "verdict": _D,
        "repair_candidate": "已收口：CD-31 connectors 扫描服务面收编真源（name/host/port→"
        "process_name/local_address/local_port）（2026-10-19）",
        "repair_status": "landed@CD-31",
        "evidence": "ServiceInfo 无 port（真源 local_port，src/api/soma_discovery.py:185）——UI "
        "{s.host}:{s.port}（修前 connectors/page.tsx:463）端口段恒 undefined（显示行为损失）"
        "——CD-31 已收口（UI 收编 {s.local_address}:{s.local_port}，2026-10-19），锚点改锁"
        "修复后状态（port 漂移复发先炸）",
        "anchors": [(SD, 185, "    local_port: int"), (U_CX, 463, "{s.local_address}:{s.local_port}")],
    },
    # ── topology：handler 内联构造 ──
    ("topology.get_topology_graph:313", "nodes.id"): {
        "verdict": _V,
        "evidence": "nodes 元素内联构造含 id（src/api/topology.py:333）；UI 消费（topology/"
        "page.tsx:103）可达",
        "anchors": [(TP, 333, '"id": node["id"],')],
    },
    ("topology.get_topology_graph:313", "nodes.health"): {
        "verdict": _V,
        "evidence": "nodes 元素含 health（src/api/topology.py:338）；UI 消费（topology/page.tsx:"
        "187）可达",
        "anchors": [(TP, 338, '"health": h.get("status", "unknown"),')],
    },
    ("topology.get_topology_graph:313", "edges.from"): {
        "verdict": _V,
        "evidence": "edges 元素内联构造 {from,to}（src/api/topology.py:343）；UI 消费（topology/"
        "page.tsx:170）可达",
        "anchors": [(TP, 343, 'edges = [{"from": f, "to": t} for f, t in _EDGES]')],
    },
    ("topology.get_topology_graph:313", "edges.to"): {
        "verdict": _V,
        "evidence": "edges 元素 {from,to} 同源（src/api/topology.py:343）；UI 消费（topology/"
        "page.tsx:170）可达",
        "anchors": [(TP, 343, 'edges = [{"from": f, "to": t} for f, t in _EDGES]')],
    },
    # ── workspace list_directory：entries 元素内联构造 ──
    ("workspace_api.list_directory:58", "entries.name"): {
        "verdict": _V,
        "evidence": "entries 元素内联构造含 name（src/api/workspace_api.py:72）；UI 消费"
        "（workspace/page.tsx:179）可达",
        "anchors": [(WA, 72, '"name": entry.name,')],
    },
    ("workspace_api.list_directory:58", "entries.type"): {
        "verdict": _V,
        "evidence": "entries 元素含 type（src/api/workspace_api.py:74）；UI 消费（workspace/"
        "page.tsx:179）可达",
        "anchors": [(WA, 74, '"type": "directory" if entry.is_dir() else "file",')],
    },
    ("workspace_api.list_directory:58", "entries.size"): {
        "verdict": _V,
        "evidence": "entries 元素含 size（src/api/workspace_api.py:75）；UI 消费（workspace/"
        "page.tsx:179）可达",
        "anchors": [(WA, 75, '"size": stat.st_size if entry.is_file() else None,')],
    },
    ("workspace_api.list_directory:58", "entries.hidden"): {
        "verdict": _V,
        "evidence": "entries 元素含 hidden（src/api/workspace_api.py:77）；UI 消费（workspace/"
        "page.tsx:179）可达",
        "anchors": [(WA, 77, '"hidden": entry.name.startswith("."),')],
    },
}

# 深挖扩面发现（普查未列、随裁决同根登记，不进 findings join）
EXPANDED_FINDINGS = [
    {
        "page": "knowledge-graph/page.tsx",
        "keys": ["type"],
        "verdict": _D,
        "repair_candidate": "已收口：CD-㉘ knowledge-graph 实体类型收编真源（2026-10-19）",
        "note": "与 graph.get_entity_detail type 同根（entities 行键 type 被 _row_to_entity 重命名"
        "为 entity_type，src/services/entity.py:13，列表 GET /api/graph/entities 同走该口）：UI "
        "e.type 列表过滤/搜索（knowledge-graph/page.tsx:422/:425——e.type.toLowerCase() 对 "
        "undefined 调 toLowerCase=搜索即页崩 TypeError 崩坏面）/列表渲染（:550/:555/:556）/"
        "关系表单选项（:664/:677）恒 undefined——CD-㉘ 随主件同拍收口",
    },
    {
        "page": "knowledge-graph/page.tsx",
        "keys": ["relation_type", "target_entity_id"],
        "verdict": _D,
        "repair_candidate": "已收口：CD-㉗ knowledge-graph relations 元素键收编（2026-10-19）",
        "note": "与 relations.source_entity_id 同根（真源 relations 行键 id/source_id/target_id/"
        "type/properties/created_at+source_name/target_name，src/main.py:297-299+src/services/"
        "entity.py:60-68）：UI rel.relation_type（knowledge-graph/page.tsx:623）恒 undefined="
        "关系类型徽章恒空、rel.target_entity_id（:626）同型 undefined.slice TypeError"
        "（isSource 恒 false 时 :626 先走 source_entity_id 分支炸，两者同型）",
    },
    {
        "page": "connectors/page.tsx",
        "keys": ["memory"],
        "verdict": _D,
        "repair_candidate": "已收口：CD-32 connectors 扫描进程面收编真源（name→command/executable）"
        "（2026-10-19）",
        "note": "与 processes.name 同根（ProcessInfo 字段 mem 非 memory，src/api/soma_discovery.py:"
        "169）：UI p.memory.toFixed(1)（修前 connectors/page.tsx:433）对 undefined 调 toFixed="
        "TypeError 渲染崩坏面（扫描结果进程列表一渲染即炸，比 name 恒空更重）——CD-32 随主件"
        "同拍收口（p.mem.toFixed 崩坏面清零，2026-10-19）",
    },
    {
        "page": "benchmark/page.tsx",
        "keys": ["p50_ms", "p95_ms", "p99_ms", "min_ms", "success_rate"],
        "verdict": _D,
        "repair_candidate": "已收口：CD-㉔ benchmark results 元素延迟键收编真源（2026-10-19）",
        "note": "与 results.avg_ms 同根（结果元素 _result_to_dict 延迟键全在 latency 嵌套"
        "（src/benchmark/engine.py:363-370），成功率是 success 计数非 success_rate 率）：UI 平铺"
        "读 r.p50_ms/r.p95_ms/r.p99_ms/r.min_ms/r.success_rate（benchmark/page.tsx:247-251）恒 "
        "undefined=formatMs(undefined) 假渲染+成功率着色分支恒假",
    },
    {
        "page": "agent-nodes/page.tsx",
        "keys": ["query", "answer"],
        "verdict": _D,
        "repair_candidate": "已收口：CD-㉓ agent recall 响应面消费键收编（2026-10-19）",
        "note": "与 results 同根（真源 rag_query 返回恰 {answer,sources}，src/services/rag.py:53）"
        "：UI recallResults.query（agent-nodes/page.tsx:515）真源不产出=查询回显行恒空、answer"
        "真源产出无人读（检索面板只读 results=整面契约漂移）",
    },
    {
        "page": "pulse/page.tsx",
        "keys": ["last_fired_at", "next_fire_at", "created_at", "timestamp"],
        "verdict": _D,
        "repair_candidate": "已收口：CD-㉚ pulse 时间戳 epoch 秒单位收编（2026-10-19）",
        "note": "CD-㉚ 修复深挖扩面（普查未列、非 CD-㉒ 裁决同根——单位漂移非键漂移，"
        "随 CD-㉚ 修复同拍收口=CD-⑤ gland timestamp 同款先例）：真源时间戳=epoch 秒"
        "float（time.time()，live 实证 created_at=1792406287.12），UI new Date(x) 按毫秒"
        "解析→渲染 1970-01-22（node 实证）——信号卡下次（pulse/page.tsx:260）/详情"
        "最后触发·下次触发·创建时间（:305-307）/触发历史行（:376）5 消费点全 1970 假渲染；"
        "接口 PulseSignal/TickRecord 时间戳声明 string 对真源 float number 同根声明漂移"
        "（LSP 逼出）——CD-㉚ 随主件同拍收口（fmtTs 单一源 ×1000+接口收编 number）",
    },
]


def verify_anchors(anchors) -> None:
    """真源锚点程序化核验：锚点行必须真含声明子串（防无据断言+防证据漂移）。"""
    for path, line, sub in anchors:
        text = (ROOT / path).read_text(encoding="utf-8", errors="replace").splitlines()
        if not (1 <= line <= len(text)):
            raise SystemExit(f"真源锚点核验失败：{path}:{line} 越界（文件 {len(text)} 行）")
        if sub not in text[line - 1]:
            raise SystemExit(
                f"真源锚点核验失败：{path}:{line} 不含 {sub!r}（实际行：{text[line - 1]!r}）"
            )


def build_artifact(check_only: bool = False) -> dict:
    post = json.loads(POST_CD9.read_text(encoding="utf-8"))
    findings = [f for f in post["findings"] if f["verdict"] in TARGET_CENSUS_VERDICTS]
    rows = []
    seen = set()
    missing = []
    for f in findings:
        key = (f["page"], f["line"], tuple(f["key"]))
        seen.add(key)
        v = SHAPE_VERDICTS.get((f.get("handler"), ".".join(f["key"])))
        if v is None:
            missing.append((f.get("handler"), ".".join(f["key"]), f["page"], f["line"]))
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
    joined = {(r["census_handler"], ".".join(r["consumed_key"])) for r in rows}
    extra = [k for k in SHAPE_VERDICTS if k not in joined]
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
        verify_anchors(r.get("anchors", []))

    artifact = {
        "source_artifact": str(POST_CD9.relative_to(ROOT)),
        "scope": "unresolved_shape（CD-⑨ 未裁决层 109 条；本类为末类，CD-⑨ 未裁决层至此"
        "三类全数收口）",
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
        "CD-22 裁决 join ok：findings {} 条 counts={} repair_candidates={}".format(
            artifact["counts_input_findings"],
            artifact["counts"],
            artifact["repair_candidates"],
        )
    )
    if not args.check:
        print("工件已写：{}".format(OUT.relative_to(ROOT)))


if __name__ == "__main__":
    main()
