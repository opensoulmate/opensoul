"""Long-term Learning — 从经验中提取可复用的行为模式。

不是记住所有事，而是提取模式：
- 哪类任务用什么策略最有效？
- 哪些文件组合经常一起修改？
- 什么时间段用户活跃？
- 哪些操作容易失败？怎么避免？
"""

import json
import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── intent准入 gatekeeper（pattern_key唯一归一化/准入真源）──────────────
# 调研来源：LobeChat gatekeeper（feature-matrix/26-lobe-chat-source.md：
# "守门员：判断这条该不该进长期记忆……不加过滤的记忆=垃圾堆积"）。
# 生产实证（2026-10-05）：learned_patterns.strategy 被四类噪声占据——
# prompt模板片段（"## 角色"×65/"## 任务"×57）、寒暄（"你好"×15）、
# 占位符（"unknown"）、原样用户话术直提key（措辞不归并：同一任务的
# "帮我查看当前目录"/"请帮我查看一下当前目录"是两个pattern，不泛化）。
# 契约：写入（_extract_strategy_patterns）与查询（get_recommendations）
# 必须走同一组函数——否则"查得到写不进/写进查不到"。

# 口语填充词：仅在字符串首/尾剥离（最长优先）。不收单字"帮"/"请"：
# "帮助X"/"请求X"会被误伤成"助X"/"求X"。
_FILLER_PREFIXES = (
    "能不能帮我",
    "可不可以帮我",
    "能不能",
    "可不可以",
    "麻烦帮我",
    "麻烦你",
    "麻烦",
    "帮我",
    "请你帮",
    "请你",
    "请帮我",
    "请问",
    "我想请",
    "我想",
    "我要",
)
_FILLER_SUFFIXES = (
    "一下",
    "谢谢",
    "感谢",
    "多谢",
    "辛苦了",
    "辛苦",
    "吧",
    "呢",
    "啊",
    "哦",
    "呀",
    "嘛",
    "哈",
    "呗",
)
# 占位符（collector对无用户消息回合的默认值等）——不是意图
_PLACEHOLDER_KEYS = {"unknown", "none", "null", "undefined", "n/a", "na", "nil", "-", "未知"}
# 寒暄/应答话术（归一化后全等匹配）——无任务内容，没有"策略"可学
_PHATIC_KEYS = {
    "你好",
    "您好",
    "你们好",
    "哈喽",
    "嗨",
    "嗨嗨",
    "在吗",
    "在么",
    "在不在",
    "好的",
    "好",
    "好哒",
    "好呀",
    "好嘞",
    "好叭",
    "行",
    "行的",
    "可以",
    "没问题",
    "嗯",
    "嗯嗯",
    "嗯呐",
    "嗯嗯嗯",
    "哦",
    "噢",
    "哦哦",
    "额",
    "呃",
    "谢谢",
    "感谢",
    "多谢",
    "谢谢了",
    "谢谢你",
    "辛苦",
    "辛苦了",
    "辛苦你了",
    "拜拜",
    "再见",
    "晚安",
    "早安",
    "早上好",
    "中午好",
    "下午好",
    "晚上好",
    "新年好",
    "对不起",
    "抱歉",
    "不好意思",
    "没关系",
    "没事",
    "收到",
    "明白",
    "了解",
    "知道",
    "知道了",
    "hi",
    "hello",
    "hey",
    "yo",
    "sup",
    "ok",
    "okay",
    "okk",
    "kk",
    "yes",
    "yep",
    "yeah",
    "no",
    "nope",
    "thanks",
    "thank you",
    "thankyou",
    "ty",
    "thx",
    "thanx",
    "sorry",
    "my bad",
    "bye",
    "goodbye",
    "good morning",
    "good night",
    "gm",
    "gn",
    "nice",
    "cool",
    "great",
    "wow",
}
_TEMPLATE_RE = re.compile(r"^#{1,6}(\s|$)")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
# 保留：CJK/字母数字/下划线 + 路径与结构化key需要的 / . : - + @ 与空格；其余（emoji/标点）→ 空格
_ILLEGAL_CHARS_RE = re.compile(r"[^\w/.:\-+@ ]", re.UNICODE)


# ── 系统注入回合标注（非用户话术，不进策略模式）────────────────────────
# 调研/治理背景（2026-10-05轮遗留#1销账）："schedulewakeup 到点唤醒 系统自动
# 回合 非用户消息"×8 以 confidence=1.0 占据 learned_patterns.strategy——它是
# hermes唤醒注解不是用户话术，给它推荐patch策略语义荒谬。
# 产生侧标注（jobs.source provenance同款哲学"从产生侧标注，不靠字符串猜"）：
# wakeup等系统自动回合由 acp-proxy SoulMateAgent._merge_turn_origin 落库时带
# metadata.turn_origin（wakeup_executor.set_turn_origin窗口打标），ExperienceCollector
# 据此把回合意图落成结构化 provenance key "system_turn:{origin}"
# （background_job:{name}同款结构化key约定）。
# 旧数据回退：turn_origin标记之前落库的存量行 intent_summary 是唤醒契约原文
# （wakeup_executor.WAKEUP_PROMPT_MARKER 自述"系统自动回合，非用户消息"）——
# 只认这份契约自述短语，不做模糊字符串猜测。
_SYSTEM_TURN_KEY_PREFIX = "system_turn:"


def is_system_turn_intent(raw: str) -> bool:
    """是否系统注入回合的标注/结构化provenance key（非用户话术）。

    两条确定性判据（都是显式契约，不是模糊猜测）：
    1) 结构化provenance key "system_turn:{origin}"（collector对系统回合的落库约定）；
    2) 唤醒契约自述文本（WAKEUP_PROMPT_MARKER及其归一key变体）：
       "系统自动回合"+"非用户消息"同现，或 "schedulewakeup"+"到点唤醒"同现。
    """
    text = (raw or "").strip()
    if not text:
        return False
    if text.casefold().startswith(_SYSTEM_TURN_KEY_PREFIX):
        return True
    low = text.casefold()
    if "schedulewakeup" in low and "到点唤醒" in text:
        return True
    return "系统自动回合" in text and "非用户消息" in text


def normalize_intent_key(raw: str) -> str:
    """原样话术 → 可泛化的意图key（写/查同一真源）。

    去emoji标点 → 剥口语填充词（首/尾，最长优先）→ 折叠空白 → casefold。
    结构化key（background_job:*/eval_case:*）不经填充词误伤、原样保留。
    已知边界（保守取舍，宁可分裂不误合并）："帮我X一下O"型内嵌"一下"
    只在尾部剥（"查看一下"→"查看"），中间不剥——"查一下午日志/看一下天气"
    等时间/宾语用法会被误伤成错key，错合并比不合并更坏；残余分裂由
    pattern_data.variants计数可观测。
    """
    text = _ILLEGAL_CHARS_RE.sub(" ", raw or "")
    text = re.sub(r"\s+", " ", text).strip()
    changed = True
    while changed and text:
        changed = False
        low = text.casefold()
        for prefix in _FILLER_PREFIXES:
            if low.startswith(prefix):
                text = text[len(prefix) :].strip()
                changed = True
                break
        if not text:
            break
        for suffix in _FILLER_SUFFIXES:
            if text.casefold().endswith(suffix):
                text = text[: len(text) - len(suffix)].strip()
                changed = True
                break
    return text.casefold()


def admit_intent(raw: str) -> tuple[bool, str]:
    """准入判定（LobeChat gatekeeper范式）→ (是否准入, 原因码)。

    原因码：ok / empty / template / placeholder / phatic / too_short / system_turn。
    判定顺序：模板/占位在原文上判（归一会剥掉'#'等证据），系统注入标注在原文
    上判（契约自述短语不被归一改变），寒暄/长度在归一key上判。
    寒暄双路判：去标点原文（"谢谢！"→"谢谢"）+ 归一key（"你好啊"→"你好"）
    ——语气词/标点剥净后为空的寒暄不能退化成"empty"，原因码要指认真因。
    """
    text = (raw or "").strip()
    if not text:
        return False, "empty"
    if is_system_turn_intent(text):
        return False, "system_turn"
    low = text.casefold()
    if (
        _TEMPLATE_RE.match(text)
        or text.startswith("```")
        or low.startswith(("system:", "user:", "assistant:", "tool:", "human:", "<!--"))
    ):
        return False, "template"
    collapsed = re.sub(r"\s+", " ", _ILLEGAL_CHARS_RE.sub(" ", text)).strip().casefold()
    key = normalize_intent_key(raw)
    if collapsed in _PHATIC_KEYS or key in _PHATIC_KEYS:
        return False, "phatic"
    if not key:
        return False, "empty"
    if key in _PLACEHOLDER_KEYS:
        return False, "placeholder"
    if len(_CJK_RE.findall(key)) < 2 and len(key.replace(" ", "")) < 4:
        return False, "too_short"
    return True, "ok"


# ── F2b-② 推荐回流执行：字面相关度检索 + prompt 纯函数格式化 ──────────────
# 契约：与 get_recommendations 同一读真源（learned_patterns 表）、同一准入
# （admit_intent/normalize_intent_key）——不造第二套查询/准入语义。
# 精确命中优先（直接复用 get_recommendations）；无精确命中时按字符二元组
# 重合率（**字面**相关度，非语义 embedding，如实标注）取相关候选 top-k。
# 宁缺勿滥：无关策略注入 prompt 是噪声还会误导执行，重合率低于门槛=零注入。

_RELEVANCE_THRESHOLD = 0.15
_RELEVANCE_CANDIDATES = 200


def _char_bigrams(text: str) -> set[str]:
    """字符二元组集合（去空白）——中文无分词依赖的字面相关度原子。"""
    s = re.sub(r"\s+", "", text or "")
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def format_advisory_for_prompt(recs: list | None, top_k: int = 3) -> str:
    """推荐 → 执行 prompt 建议块（纯函数；空推荐返回空串=零注入）。

    语义（软上下文非硬指令）：建议块自带"仅供参考、与任务要求冲突时以任务
    要求为准"免责——学习建议不覆盖任务本体要求。类型未知如实标注不硬造。
    """
    lines = []
    for i, rec in enumerate((recs or [])[:top_k], 1):
        rtype = rec.get("type") or "unknown"
        key = rec.get("key") or ""
        data = rec.get("data") or {}
        samples = rec.get("samples") or 0
        if rtype == "strategy":
            rate = data.get("success_rate")
            rec_mode = data.get("recommendation") or ""
            if rec_mode == "patch":
                advice = "历史多采用小步补丁式修改并成功，建议保持最小改动"
            elif rec_mode == "stepwise":
                advice = "历史成功率偏低，建议分步执行、逐步验证，避免一次性大改"
            else:
                advice = f"历史策略={rec_mode or '无'}"
            rate_txt = (
                f"成功率{round(rate * 100)}%" if isinstance(rate, (int, float)) else "成功率未知"
            )
            lines.append(f"{i}. 任务「{key}」历史样本{samples}次、{rate_txt}：{advice}")
        elif rtype == "file_combo":
            files = data.get("files") or []
            combo = "+".join(str(f) for f in files) if files else key
            co = data.get("co_occurrence", samples)
            lines.append(
                f"{i}. 文件组合 {combo} 历史上常一起修改（共现{co}次），改动时注意联动检查"
            )
        elif rtype == "failure":
            fixes = [str(f) for f in (data.get("common_fixes") or []) if f]
            fix_txt = "；".join(fixes[:2]) if fixes else "注意规避同类错误"
            lines.append(f"{i}. 易错点「{key}」（历史{data.get('count', samples)}次）：{fix_txt}")
        else:
            lines.append(f"{i}. 「{key}」（{rtype}，样本{samples}）")
    if not lines:
        return ""
    return (
        "【历史经验建议】来自本系统对该类任务的历史学习，仅供参考；"
        "与任务要求冲突时以任务要求为准：\n" + "\n".join(lines)
    )


class LongTermLearning:
    """长期学习：从经验中提取模式，优化Agent行为。"""

    def __init__(self, db_pool=None, tenant_id: str = "default", agent_id: str = "default"):
        self.db = db_pool
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self._initialized = False

    async def _ensure_table(self):
        if self._initialized:
            return
        if self.db:
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS learned_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    pattern_type TEXT NOT NULL,
                    pattern_key TEXT NOT NULL,
                    pattern_data TEXT NOT NULL,
                    confidence REAL DEFAULT 0.5,
                    sample_count INTEGER DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(tenant_id, agent_id, pattern_type, pattern_key)
                )
            """)
        self._initialized = True

    async def extract_patterns(self) -> dict:
        """从经验表中提取模式（定期运行）。返回清理/提取统计（可观测）。"""
        await self._ensure_table()
        if not self.db:
            return {}
        # 存量自愈先行（compact_duplicate_logs同款"自动化优先"）：过不了
        # 准入门的旧噪声行、未归一的陈旧key行删掉，本轮extract从experiences
        # （唯一事实源）按归一key重建。
        pruned = await self._prune_inadmissible_patterns()
        strategy = await self._extract_strategy_patterns()

        # 2. 提取"文件组合"模式
        await self._extract_file_combo_patterns()

        # 3. 提取"失败规避"模式
        await self._extract_failure_patterns()
        return {"pruned": pruned, "strategy": strategy}

    async def _prune_inadmissible_patterns(self) -> dict:
        """存量strategy行自愈（幂等）：
        1) key过不了admit_intent（模板/寒暄/占位符/过短）→ 删除；
        2) key≠normalize_intent_key(key) 的未归一旧行 → 删除——本轮
           _extract_strategy_patterns按归一key重建（experiences是唯一
           事实源，pattern行只是缓存视图）；
        3) 无任何经验背书的行 → 删除（缓存视图语义的兑现）：经验行被清账
           （测试垃圾/探针污染治理）后，派生pattern行不许留——"请回复ok两个字
           即可"这类探针话术在证据删掉后自动出清，不再需要逐行人工删。
           保守边界：key还剩任意背书（哪怕聚合<3不触发重建）就保留，只驱逐
           零背书行，防证据分裂（变体各自<3）时误删。experiences表缺失时跳过
           本规则（fail-safe：不知道证据不乱删）。
        """
        rows = await self.db.fetch(
            "SELECT id, pattern_key FROM learned_patterns "
            "WHERE tenant_id = ? AND agent_id = ? AND pattern_type = 'strategy'",
            (self.tenant_id, self.agent_id),
        )
        stats = {
            "scanned": len(rows),
            "removed_inadmissible": 0,
            "removed_stale_key": 0,
            "removed_unbacked": 0,
        }
        for row in rows:
            key = row["pattern_key"]
            ok, _reason = admit_intent(key)
            if not ok:
                await self.db.execute("DELETE FROM learned_patterns WHERE id = ?", (row["id"],))
                stats["removed_inadmissible"] += 1
            elif normalize_intent_key(key) != key:
                await self.db.execute("DELETE FROM learned_patterns WHERE id = ?", (row["id"],))
                stats["removed_stale_key"] += 1
        # 规则3：零背书驱逐（重查剩余行，只看没被规则1/2删掉的）
        backed = await self._backed_pattern_keys()
        if backed is not None:
            remaining = await self.db.fetch(
                "SELECT id, pattern_key FROM learned_patterns "
                "WHERE tenant_id = ? AND agent_id = ? AND pattern_type = 'strategy'",
                (self.tenant_id, self.agent_id),
            )
            for row in remaining:
                if row["pattern_key"] not in backed:
                    await self.db.execute("DELETE FROM learned_patterns WHERE id = ?", (row["id"],))
                    stats["removed_unbacked"] += 1
        return stats

    async def _backed_pattern_keys(self) -> set | None:
        """当前有经验背书的归一key集合（experiences表缺失/查询失败→None跳过规则3）。"""
        try:
            exp_rows = await self.db.fetch(
                "SELECT DISTINCT intent_summary FROM experiences "
                "WHERE tenant_id = ? AND agent_id = ?",
                (self.tenant_id, self.agent_id),
            )
        except Exception:
            return None  # experiences表缺失等：不知道证据就不乱删（fail-safe）
        backed: set = set()
        for r in exp_rows:
            intent = r["intent_summary"]
            ok, _reason = admit_intent(intent)
            if ok:
                backed.add(normalize_intent_key(intent))
        return backed

    async def _extract_strategy_patterns(self) -> dict:
        """提取：什么意图用什么策略最成功。

        准入门（admit_intent单一真源）：模板片段/寒暄/占位符/过短话术不进
        pattern_key，按原因码计数可观测；原样用户话术按normalize_intent_key
        归并（"帮我查看当前目录"≡"请帮我查看当前目录"→"查看当前目录"），
        措辞变体聚成同一意图、cnt求和（变体数入pattern_data.variants，
        内嵌"一下X"类不剥的残余分裂可见可观测，见normalize_intent_key）。
        """
        rows = await self.db.fetch(
            """
            SELECT intent_summary, outcome, COUNT(*) as cnt
            FROM experiences
            WHERE tenant_id = ? AND agent_id = ?
            GROUP BY intent_summary, outcome
            HAVING cnt >= 3
        """,
            (self.tenant_id, self.agent_id),
        )

        strategy_map: dict[str, dict] = {}
        variants: dict[str, set] = {}
        skipped: dict[str, int] = {}
        for row in rows:
            intent = row["intent_summary"]
            ok, reason = admit_intent(intent)
            if not ok:
                skipped[reason] = skipped.get(reason, 0) + row["cnt"]
                continue
            key = normalize_intent_key(intent)
            if key not in strategy_map:
                strategy_map[key] = {"success": 0, "failure": 0}
                variants[key] = set()
            variants[key].add(intent)
            counts = strategy_map[key]
            counts[row["outcome"]] = counts.get(row["outcome"], 0) + row["cnt"]

        for intent, counts in strategy_map.items():
            total = counts["success"] + counts["failure"]
            confidence = counts["success"] / total if total > 0 else 0.5
            await self._upsert_pattern(
                "strategy",
                intent,
                {
                    "success_rate": confidence,
                    "total": total,
                    "recommendation": "patch" if confidence > 0.7 else "stepwise",
                    "variants": len(variants[intent]),
                },
                confidence,
                total,
            )
        return {
            "considered": len(rows),
            "admitted": len(strategy_map),
            "skipped": skipped,
        }

    async def _extract_file_combo_patterns(self):
        """提取：哪些文件经常一起修改"""
        rows = await self.db.fetch(
            """
            SELECT action FROM experiences
            WHERE tenant_id = ? AND agent_id = ? AND outcome = 'success'
            ORDER BY created_at DESC LIMIT 200
        """,
            (self.tenant_id, self.agent_id),
        )

        # 简单的文件共现分析
        import re

        file_combos: dict[str, int] = {}
        for row in rows:
            files = re.findall(r"[\w/\\.-]+\.\w+", row["action"])
            if len(files) >= 2:
                files.sort()
                key = "+".join(files[:3])  # 最多3个文件的组合
                file_combos[key] = file_combos.get(key, 0) + 1

        for combo, count in file_combos.items():
            if count >= 3:
                await self._upsert_pattern(
                    "file_combo",
                    combo,
                    {"files": combo.split("+"), "co_occurrence": count},
                    min(1.0, count / 10),
                    count,
                )

    async def _extract_failure_patterns(self):
        """提取：什么操作容易失败，怎么避免"""
        rows = await self.db.fetch(
            """
            SELECT action, error, fix FROM experiences
            WHERE tenant_id = ? AND agent_id = ? AND outcome = 'failure'
            AND error IS NOT NULL
            ORDER BY created_at DESC LIMIT 100
        """,
            (self.tenant_id, self.agent_id),
        )

        failure_types: dict[str, dict] = {}
        for row in rows:
            error = row["error"] or "unknown"
            # 归类错误
            if "syntax" in error.lower():
                error_type = "syntax_error"
            elif "timeout" in error.lower():
                error_type = "timeout"
            elif "permission" in error.lower():
                error_type = "permission"
            elif "not found" in error.lower():
                error_type = "not_found"
            else:
                error_type = "other"

            if error_type not in failure_types:
                failure_types[error_type] = {"count": 0, "fixes": []}
            failure_types[error_type]["count"] += 1
            if row["fix"]:
                failure_types[error_type]["fixes"].append(row["fix"])

        for error_type, data in failure_types.items():
            if data["count"] >= 2:
                await self._upsert_pattern(
                    "failure",
                    error_type,
                    {"count": data["count"], "common_fixes": list(set(data["fixes"]))[:3]},
                    min(1.0, data["count"] / 5),
                    data["count"],
                )

    async def _upsert_pattern(
        self,
        pattern_type: str,
        pattern_key: str,
        pattern_data: dict,
        confidence: float,
        sample_count: int,
    ):
        await self.db.execute(
            """
            INSERT INTO learned_patterns (tenant_id, agent_id, pattern_type, pattern_key, pattern_data, confidence, sample_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, agent_id, pattern_type, pattern_key)
            DO UPDATE SET pattern_data = ?, confidence = ?, sample_count = ?, updated_at = ?
        """,
            (
                self.tenant_id,
                self.agent_id,
                pattern_type,
                pattern_key,
                json.dumps(pattern_data),
                confidence,
                sample_count,
                time.time(),
                time.time(),
                json.dumps(pattern_data),
                confidence,
                sample_count,
                time.time(),
            ),
        )

    async def get_recommendations(self, intent_summary: str = "") -> list[dict]:
        """获取学习到的推荐。

        入参intent与pattern_key同走normalize_intent_key归一（写/查同一
        真源——"帮我查看当前目录"也能命中"查看当前目录"的模式）；寒暄/
        模板/占位符类入参过不了admit_intent → 不配推荐，返回空。
        """
        await self._ensure_table()
        if not self.db:
            return []

        conditions = "tenant_id = ? AND agent_id = ?"
        params: list = [self.tenant_id, self.agent_id]
        if intent_summary:
            ok, _reason = admit_intent(intent_summary)
            if not ok:
                return []
            conditions += " AND pattern_key = ?"
            params.append(normalize_intent_key(intent_summary))

        rows = await self.db.fetch(
            f"""SELECT pattern_type, pattern_key, pattern_data, confidence, sample_count
                FROM learned_patterns
                WHERE {conditions}
                ORDER BY confidence DESC, sample_count DESC
                LIMIT 10""",
            params,
        )

        return [
            {
                "type": row["pattern_type"],
                "key": row["pattern_key"],
                "data": json.loads(row["pattern_data"]),
                "confidence": row["confidence"],
                "samples": row["sample_count"],
            }
            for row in rows
        ]

    async def get_recommendations_for_prompt(self, intent_summary: str, top_k: int = 3) -> list:
        """执行 prompt 回流用推荐（F2b-②）——行为优化闭环的执行侧消费口。

        语义（与 get_recommendations 同一读真源/同一准入，不造第二套）：
        1) 精确命中优先：直接复用 get_recommendations（normalize 同 key）；
        2) 无精确命中 → 字面相关度兜底：全量候选按字符二元组重合率（Jaccard）
           排序，达 _RELEVANCE_THRESHOLD 才算相关（宁缺勿滥，无关=零注入）。
        如实标注：相关度是**字面**匹配（字符二元组），非语义 embedding。
        """
        if not intent_summary:
            return []
        ok, _reason = admit_intent(intent_summary)
        if not ok:
            return []
        precise = await self.get_recommendations(intent_summary)
        if precise:
            return precise[:top_k]

        await self._ensure_table()
        if not self.db:
            return []
        rows = await self.db.fetch(
            f"""SELECT pattern_type, pattern_key, pattern_data, confidence, sample_count
                FROM learned_patterns
                WHERE tenant_id = ? AND agent_id = ?
                ORDER BY confidence DESC, sample_count DESC
                LIMIT {_RELEVANCE_CANDIDATES}""",
            [self.tenant_id, self.agent_id],
        )
        target = _char_bigrams(normalize_intent_key(intent_summary))
        if not target:
            return []
        scored = []
        for row in rows:
            key_bigrams = _char_bigrams(row["pattern_key"])
            if not key_bigrams:
                continue
            overlap = len(target & key_bigrams) / len(target | key_bigrams)
            if overlap >= _RELEVANCE_THRESHOLD:
                scored.append((overlap, row["confidence"] or 0.0, row))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [
            {
                "type": row["pattern_type"],
                "key": row["pattern_key"],
                "data": json.loads(row["pattern_data"]),
                "confidence": row["confidence"],
                "samples": row["sample_count"],
            }
            for _overlap, _conf, row in scored[:top_k]
        ]

    async def get_stats(self) -> dict:
        await self._ensure_table()
        if not self.db:
            return {"patterns": 0}
        count = await self.db.fetchval(
            "SELECT COUNT(*) FROM learned_patterns WHERE tenant_id = ? AND agent_id = ?",
            (self.tenant_id, self.agent_id),
        )
        return {"patterns": count}
