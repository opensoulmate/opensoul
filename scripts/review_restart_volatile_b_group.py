#!/usr/bin/env python3
"""RV-①c：重启即丢普查 B 组逐行深挖复核工具（审计即切片本体，零行为改动）。

背景（docs/restart-volatile-census.md 诚实边界#2）：RV-① 的 A/B 组划分只人工复核了
8 个器官，B 组其余条目「按形态归组未逐行深挖，若建设件复核发现 B 组藏活事实，
按『复核即切片本体』先例扩 A 组」。本工具把该深挖复核固化为可重放的判定表：

- 输入：data/restart_volatile_census_20261021.json（RV-① 静态普查工件）
- 判定表：CLASS_VERDICTS（类级粗判）+ ENTRY_OVERRIDES（逐条细判，人工深挖结论）
- 输出：data/restart_volatile_b_group_review_20261022.json（逐条 verdict+理由+证据+候选）

判定口径（与 RV-① 报告语义一致）：
- A_rv1b        = RV-① A 组定性范围（RV-①b 系列已处置/同器官并入）
- B_fact_hidden = B 组藏活事实（用户/外部创建的注册、身份、工件、留痕——重启即丢且
                  仓内无重建路径）→ 扩 A 组，登记建设候选 RV-①b-⑧+
- B_derived     = 派生/可重建/有界观测（重启丢=无害或随启动/事件重建）
- B_runtime     = 在途运行态（连接/任务/流/会话机——设计上就活在进程里）
- C_persisted   = 已持久化（persisted_evidence/load_only 旁证在场）

只读工具：不写生产库、不改 src；--check 模式校验既有工件与判定表一致（确定性）。
判定前提（源码标记）由 tests/test_rv1_b_group_review.py 锁定——前提变化须复核判定。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CENSUS_PATH = ROOT / "data" / "restart_volatile_census_20261021.json"
REVIEW_PATH = ROOT / "data" / "restart_volatile_b_group_review_20261022.json"

# ── A 组规则（RV-① 报告 §二 A 行 8 器官；download_plugins._plugins 是启动扫描
# 重建面，RV-① 报告归 B「启动重建族」，故显式排除在 A 之外）────────────────────
A_RULE: list[tuple[str, str | None]] = [
    ("src/gland/token_meter.py", "TokenMeter"),
    ("src/immune/access_control.py", "IPAccessControl"),
    ("src/gland/key_manager.py", "KeyManager"),
    ("src/a2a/task_manager.py", "TaskManager"),
    ("src/echo/dispatcher.py", "MessageDispatcher"),
    ("src/api/native_downloader.py", None),
    ("src/api/download_plugins.py", None),
    ("src/vein/chunked.py", "ChunkedUploader"),
    ("src/limb/executor.py", "RPAExecutor"),
]
A_EXCLUDED_ATTRS = {("src/api/download_plugins.py", "_plugins")}

# ── 建设候选（B_fact_hidden → 候选，按最小件排序；⑮ 涉凭据随 pending-decisions ㉛）
CANDIDATES = {
    "RV-①b-⑧": "immune/audit.AuditLogger._log 安全审计留痕持久化（最小件：单写点 log()）",
    "RV-①b-⑨": "pulse/timer.PulseEngine._signals 用户创建脉冲信号持久化（POST /signals 工件）",
    "RV-①b-⑩": "gland/router.ModelRouter.providers API 注册配置持久化（无钥；钥另件 RV-①b-③）",
    "RV-①b-⑪": "immune/intrusion 留痕持久化（_threat_history/_login_attempts 检测证据）",
    "RV-①b-⑫": "will/dag_planner.DAGPlanner._plans 计划持久化（by-ID 契约跨重启）",
    "RV-①b-⑬": "healer/diagnose.OrganHealer._history 修复史持久化（ring 500）",
    "RV-①b-⑭": "heredity 注册/迁移状态持久化（version_registry 3 面+migration 2 面，最大件）",
    "RV-①b-⑮": "link/connector._connectors + soma ComponentRegistry 注册面持久化"
    "**涉凭据**（secret/callback_token/component token 随行落盘形态）→ 随 ㉛ 拍板代跑",
}

# ── 逐条细判（人工深挖结论；key=(module, class, attr)）────────────────────────
# value: (verdict, reason, candidate_or_None, credential)
ENTRY_OVERRIDES: dict[tuple[str, str, str], tuple[str, str, str | None, bool]] = {
    # —— B 组藏活事实（扩 A 组）——
    ("src/immune/audit.py", "AuditLogger", "_log"): (
        "B_fact_hidden",
        "安全审计留痕 ring（deque maxlen=10000）——permission_denied/intrusion 等安全动作史；"
        "api/immune.py:27 单例真消费（audit.log 3+ 写点），重启清史=安全审计断档；"
        "与 a2a push_delivery_log（RV-①b-⑤-3 已持久化）同族留痕",
        "RV-①b-⑧",
        False,
    ),
    ("src/pulse/timer.py", "PulseEngine", "_signals"): (
        "B_fact_hidden",
        "用户创建的脉冲信号（POST /api/pulse/signals + by-ID/tick/pause/resume 契约）"
        "——interval/callback_url/payload/max_fires/fire 状态零重建：bootstrap.py:168 的"
        "种子信号也只在首次 bootstrap 建（main.py:398 is_bootstrapped 一次性），重启后"
        "定时脉冲静默消失（bootstrap 信号同样不复活）",
        "RV-①b-⑨",
        False,
    ),
    ("src/gland/router.py", "ModelRouter", "providers"): (
        "B_fact_hidden",
        "API 注册 provider 配置（POST /api/gland/providers → add_provider）——内置备份"
        "provider 随构造重建（register_local/variant_backup），API 注册的 name/base_url/"
        "models/priority 零重建=重启后路由配置丢；钥面=KeyManager（RV-①b-③ 拍板件）另件",
        "RV-①b-⑩",
        False,
    ),
    ("src/immune/intrusion.py", "IntrusionDetector", "_threat_history"): (
        "B_fact_hidden",
        "威胁事件史（deque MAX_THREAT_HISTORY）——检测产出的 ThreatEvent 记录，重启清史"
        "=安全证据丢；auto-block 结果已在 access_control（RV-①b-② 已持久化）但过程史不落",
        "RV-①b-⑪",
        False,
    ),
    ("src/immune/intrusion.py", "IntrusionDetector", "_login_attempts"): (
        "B_fact_hidden",
        "失败登录时间窗（per-IP deque maxlen=100）——爆破检测的证据窗，重启重置计数="
        "攻击者诱导重启即清零爆破计数（auto-block 前置状态）",
        "RV-①b-⑪",
        False,
    ),
    ("src/will/dag_planner.py", "DAGPlanner", "_plans"): (
        "B_fact_hidden",
        "API 创建的执行计划（api/will.py:564 create_from_llm_response → get_plan by-ID "
        ":588/:626 消费）——plan_id 契约跨重启断裂（404）；计划可重规划但 by-ID 语义丢",
        "RV-①b-⑫",
        False,
    ),
    ("src/healer/diagnose.py", "OrganHealer", "_history"): (
        "B_fact_hidden",
        "修复史 ring（maxlen=500 DiagnosisResult）——heal 动作留痕（stats 的 healed/failed"
        " 聚合真源），重启清史=自愈审计断档",
        "RV-①b-⑬",
        False,
    ),
    ("src/heredity/version_registry.py", "VersionRegistry", "_components"): (
        "B_fact_hidden",
        "API 注册组件版本（POST /api/heredity/components）——SEED_COMPONENTS 28 种子仅"
        "import 期重建（api/heredity.py:72 循环），API 注册的组件版本史零重建",
        "RV-①b-⑭",
        False,
    ),
    ("src/heredity/version_registry.py", "VersionRegistry", "_migrations"): (
        "B_fact_hidden",
        "迁移计划+execute/rollback 状态（POST /migrations + execute/rollback 端点）——"
        "执行状态是事实，重启丢=已执行迁移不可考",
        "RV-①b-⑭",
        False,
    ),
    ("src/heredity/version_registry.py", "VersionRegistry", "_changelog"): (
        "B_fact_hidden",
        "变更史（register/迁移记账）——零重建的记录面",
        "RV-①b-⑭",
        False,
    ),
    ("src/heredity/migration.py", "MigrationEngine", "_schemas"): (
        "B_fact_hidden",
        "API 注册 schema 版本（POST /api/heredity/schemas → register_schema）——非启动"
        "扫描（RV-① 报告『启动重建族』归组勘误），重启丢注册 schema",
        "RV-①b-⑭",
        False,
    ),
    ("src/heredity/migration.py", "MigrationEngine", "_scripts"): (
        "B_fact_hidden",
        "迁移脚本+applied 状态（:135 append；list_scripts(applied=) 消费 applied 轴）——"
        "脚本与执行状态均无重启重建",
        "RV-①b-⑭",
        False,
    ),
    ("src/link/connector.py", "IntegrationManager", "_connectors"): (
        "B_fact_hidden",
        "API 创建的集成连接器（create_connector 含 secret+callback_token DEC-⑲ 凭据随行）"
        "——重启丢注册+凭据，外部 webhook 经 DEC-⑲ fail-closed 永久拒直到重注册重签",
        "RV-①b-⑮",
        True,
    ),
    ("src/api/soma_connector.py", "ComponentRegistry", "_components"): (
        "B_fact_hidden",
        "外部组件注册事实（register：身份/能力/元数据）——组件重连可重注册（自愈面）但"
        "注册史/registered_at 零重建，心跳 fail-closed 至重注册",
        "RV-①b-⑮",
        False,
    ),
    ("src/api/soma_connector.py", "ComponentRegistry", "_tokens"): (
        "B_fact_hidden",
        "组件注册凭据（component_id→secret token，_verify_token 消费）——重启丢 token="
        "已发凭据全失效，外部组件须重注册换证",
        "RV-①b-⑮",
        True,
    ),
    # —— 报告人工定性覆盖 auto_verdict 形态粗判 ——
    ("src/nest/tenant.py", "TenantManager", "_tenants"): (
        "C_persisted",
        "F3b-① NestStore SQLite 真源（构造全量回载、五写点随写随落）——报告 C 组人工定性；"
        "auto_verdict=write_only 为形态粗判，以报告定性为准（RV-① 纪律）",
        None,
        False,
    ),
    ("src/nest/tenant.py", "TenantManager", "_namespace_index"): (
        "B_derived",
        "由 _tenants 回载重建的 namespace 逆查索引（F3b-① get_by_namespace 轴）",
        None,
        False,
    ),
    # —— 同类内不随判的派生面（显式拆出防类级误伤）——
    ("src/immune/audit.py", "AuditLogger", "_counters"): (
        "B_derived",
        "计数器随 _log 聚合（留痕持久化后可重建），非独立事实",
        None,
        False,
    ),
    ("src/immune/intrusion.py", "IntrusionDetector", "_ip_suspicion"): (
        "B_derived",
        "嫌疑评分对象（由检测事件累积派生；留痕重建/随事件重算），非独立记录面",
        None,
        False,
    ),
    ("src/immune/intrusion.py", "IntrusionDetector", "_request_counts"): (
        "B_derived",
        "限速窗口（deque maxlen=1000）——设计窗口态",
        None,
        False,
    ),
    ("src/immune/intrusion.py", "IntrusionDetector", "_stats"): (
        "B_derived",
        "统计计数（total_inspected 等），随事件重算",
        None,
        False,
    ),
    ("src/gland/router.py", "ModelRouter", "_last_chain_trace"): (
        "B_derived",
        "最近一次降级链 trace（观测面，record() 写点），最后一次调用的可观测镜像",
        None,
        False,
    ),
    ("src/will/dag_planner.py", "DAGPlanner", "_stats"): ("B_derived", "规划统计计数", None, False),
    ("src/healer/diagnose.py", "OrganHealer", "_custom_handlers"): (
        "B_runtime",
        "代码注册的自定义修复 handler（register_handler 调用方启动重注册）",
        None,
        False,
    ),
    ("src/pulse/timer.py", "PulseEngine", "_tick_history"): (
        "B_derived",
        "tick 记录 ring（10000→5000 自裁剪观测缓冲）",
        None,
        False,
    ),
    ("src/link/connector.py", "IntegrationManager", "_events"): (
        "B_derived",
        "webhook 事件缓冲（_max_events=5000 有界；timeline 持久双落另有真源）",
        None,
        False,
    ),
}

# ── 类级粗判（B 组余量：同类多 attr 共享判定）────────────────────────────────
# value: (verdict, reason)
CLASS_VERDICTS: dict[tuple[str, str], tuple[str, str]] = {
    ("src/a2a/task_manager.py", "_ChoiceChunkStreams"): ("B_runtime", "在途流分块发射器（随任务收尾销毁）"),
    ("src/api/download_plugins.py", "DownloadManager"): ("B_derived", "插件注册表启动扫描重建（RV-① 报告『启动重建族』）"),
    ("src/api/nerve.py", "EventBus"): (
        "B_derived",
        "_events 有界 5000 活动缓冲；_subscribers/_stream_queues 在途客户端；"
        "_nodes 外部节点重注册自愈（register_node 端点在场，heartbeat 对缺行 fail-closed "
        "api/nerve.py:115）——非仓内重建，依赖外部重连重注册，留观察",
    ),
    ("src/api/soma_discovery.py", "FileSystemAdapter"): ("B_derived", "变更日志 ring（_max_change_log=500 有界观测）"),
    ("src/api/soma_discovery.py", "RestAdapter"): ("B_derived", "适配器构造配置（调用方传入）"),
    ("src/api/soma_discovery.py", "_Cache"): ("B_derived", "发现缓存（TTL 淘汰）"),
    ("src/cortex/graphrag.py", "GraphRAGEngine"): ("B_derived", "语言词素常量（ctor 构建 _particles/_suffixes）"),
    ("src/cortex/loop_guard.py", "LoopGuard"): ("B_derived", "重复检测窗口（设计窗口态）"),
    ("src/cortex/token_attribution.py", "ContextAttributor"): ("B_derived", "归因观测 ring（max_records=50 有界）"),
    ("src/hippo/dream_distiller.py", "DreamDistiller"): (
        "B_derived",
        "_dream_history=蒸馏观测镜像（真源=ltm_store 行已持久化；_recalled_memory_ids 每轮"
        "去重集 :258 清空）；观察：_dream_history append 无裁剪（疑似无界，归增长域另查）",
    ),
    ("src/hippo/extractors/gatekeeper.py", "MemoryGatekeeper"): ("B_derived", "拒绝计数统计"),
    ("src/immune/moderator.py", "ContentModerator"): ("B_derived", "审核正则由配置编译"),
    ("src/immune/rate_limiter.py", "RateLimiter"): ("B_derived", "限流窗口（设计窗口态）"),
    ("src/immune/rate_limiter.py", "SlidingWindowCounter"): ("B_derived", "滑窗计数（设计窗口态）"),
    ("src/intelligence/analyzer.py", "SystemIntelligence"): (
        "B_derived",
        "_metrics_history/_insights 有界 deque；_anomaly_baselines 学习态（随指标重学，"
        "冷启动损失如实）；_component_metrics 随组件数有界",
    ),
    ("src/intelligence/intent.py", "IntentClassifier"): ("B_derived", "意图统计计数"),
    ("src/mind/emotion.py", "EmotionAnalyzer"): ("B_derived", "情绪计数统计"),
    ("src/nerve/client.py", "NerveClient"): ("B_runtime", "运行时订阅"),
    ("src/nest/isolation.py", "IsolationEngine"): (
        "B_derived",
        "运行时策略覆盖（重启回落 DEFAULT_POLICIES=代码即真源，F3b-① 记档；审计日志已持久化）",
    ),
    ("src/reflex/cache.py", "ReflexCache"): ("B_derived", "反射缓存"),
    ("src/trajectory/session_fsm.py", "SessionStateMachine"): ("B_runtime", "在途会话状态机+_stats 统计"),
    ("src/vein/cache.py", "CacheManager"): ("B_derived", "LRU 缓存（容量淘汰）"),
    ("src/vision/dag_visualizer.py", "DAGVisualizer"): ("B_derived", "布局缓存（按图重算）"),
    ("src/vision/mindmap.py", "MindMapNode"): ("B_runtime", "渲染对象图"),
    ("src/vital/alert.py", "AlertManager"): (
        "B_derived",
        "_firing=在途告警；_history 告警史（观察：list append 无裁剪疑似无界，归增长域另查）",
    ),
    ("src/vital/collector.py", "MetricsCollector"): ("B_derived", "指标史 deque/自裁剪延迟缓冲（有界）"),
    ("src/voice/tts_engine.py", "TTSEngine"): ("B_runtime", "在途 TTS 任务"),
}

A_REASON = (
    "RV-① A 组定性范围：RV-①b 系列已持久化（或同器官在途态随器官处置），"
    "详见 docs/restart-volatile-census.md §二 A 行"
)
C_REASON = "落盘/回载旁证在场（persisted_evidence/load_only），RV-① C 组"


def _key(entry: dict) -> tuple[str, str, str]:
    return (entry["module"], entry.get("class") or "", entry["attr"])


def is_a_group(entry: dict) -> bool:
    """A 组规则判法（单一源，测试锁定）。"""
    mod, cls, attr = _key(entry)
    if (mod, attr) in A_EXCLUDED_ATTRS:
        return False
    for m, c in A_RULE:
        if mod == m and (c is None or cls == c):
            return True
    return False


def classify(entry: dict) -> dict:
    """单条判定（纯函数）。覆盖顺序：C → A → 逐条细判 → 类级粗判 → 未归类如实报。"""
    mod, cls, attr = _key(entry)
    verdict, reason, candidate, credential = None, None, None, False

    if entry.get("auto_verdict") in ("persisted_evidence", "load_only"):
        verdict, reason = "C_persisted", C_REASON
    elif is_a_group(entry):
        verdict, reason = "A_rv1b", A_REASON
    elif (mod, cls, attr) in ENTRY_OVERRIDES:
        verdict, reason, candidate, credential = ENTRY_OVERRIDES[(mod, cls, attr)]
    elif (mod, cls) in CLASS_VERDICTS:
        verdict, reason = CLASS_VERDICTS[(mod, cls)]
    else:
        raise ValueError(f"未归类条目（判定表缺项，禁静默默认）: {mod} {cls}.{attr}")

    if verdict == "B_fact_hidden":
        if not candidate or candidate not in CANDIDATES:
            raise ValueError(f"B_fact_hidden 必须挂有效候选: {mod} {cls}.{attr}")
    elif candidate is not None:
        raise ValueError(f"非 fact 条目禁挂候选: {mod} {cls}.{attr}")

    return {
        "module": mod,
        "class": cls,
        "attr": attr,
        "container": entry.get("container"),
        "line": entry.get("line"),
        "auto_verdict": entry.get("auto_verdict"),
        "verdict": verdict,
        "reason": reason,
        "candidate": candidate,
        "credential": credential,
    }


def build_review(census: dict) -> dict:
    """整表判定（确定性：按 census 条目序逐条 classify，禁 set 迭代序漂移）。"""
    rows = [classify(e) for e in census["entries"]]
    stats: dict[str, int] = {}
    for r in rows:
        stats[r["verdict"]] = stats.get(r["verdict"], 0) + 1
    facts = [r for r in rows if r["verdict"] == "B_fact_hidden"]
    return {
        "generated_by": "scripts/review_restart_volatile_b_group.py",
        "census": "data/restart_volatile_census_20261021.json",
        "total": len(rows),
        "stats": dict(sorted(stats.items())),
        "candidates": dict(sorted(CANDIDATES.items())),
        "fact_keys": sorted(f"{r['module']}|{r['class']}|{r['attr']}" for r in facts),
        "credential_keys": sorted(
            f"{r['module']}|{r['class']}|{r['attr']}" for r in rows if r["credential"]
        ),
        "entries": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="校验既有工件与判定表一致（不写盘）")
    args = ap.parse_args()

    if not CENSUS_PATH.exists():
        print(f"普查工件缺失: {CENSUS_PATH}", file=sys.stderr)
        return 1
    census = json.loads(CENSUS_PATH.read_text(encoding="utf-8"))
    review = build_review(census)

    if args.check:
        if not REVIEW_PATH.exists():
            print(f"复核工件缺失: {REVIEW_PATH}", file=sys.stderr)
            return 1
        on_disk = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
        if on_disk != review:
            print("复核工件与判定表不一致（确定性破坏/判定表改动未重生成）", file=sys.stderr)
            return 1
        print(f"check ok: {review['total']} 条判定一致")
        return 0

    REVIEW_PATH.write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {REVIEW_PATH} ({review['total']} entries, stats={review['stats']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
