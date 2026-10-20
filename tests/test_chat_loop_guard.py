"""P0 cortex循环guard接线缺陷修复验证 — api/chat.py的LoopGuard持久化

修复内容：_check_loop此前每次请求在函数体内 new LoopGuard() —— 滑窗/去重集合
每次请求清零，重复文本检测永远不触发（guard接线了但检测是死的）。修复为
按session_key持久化实例（_CHAT_LOOP_GUARDS registry，256上限防泄漏），
两条SSE流式路径补齐检测（此前仅非流式路径有guard），调用点传入user_id。

核心回归证明：同一session连续8次相同回答在旧实现下永远返回None（无状态），
新实现第8次返回loop_warning（状态跨请求累积）。
"""

from pathlib import Path

from src.api import chat as chat_mod
from src.api.chat import _CHAT_LOOP_GUARDS, _CHAT_LOOP_GUARDS_MAX, _check_loop, _get_chat_loop_guard
from src.cortex.loop_guard import LoopGuard

REPEAT_TEXT = "This is a repetitive answer that the model keeps producing verbatim every time."


class TestGuardPersistence:
    """registry持久化语义：同key同实例、状态跨请求累积、跨key隔离"""

    def test_same_key_same_instance(self):
        _CHAT_LOOP_GUARDS.clear()
        g1 = _get_chat_loop_guard("persist-x")
        g2 = _get_chat_loop_guard("persist-x")
        assert g1 is g2

    def test_different_keys_isolated(self):
        _CHAT_LOOP_GUARDS.clear()
        g1 = _get_chat_loop_guard("iso-a")
        g2 = _get_chat_loop_guard("iso-b")
        assert g1 is not g2

    def test_state_accumulates_across_calls(self):
        """修复核心：guard状态跨请求累积（旧实现每请求新建实例，round永远=1）"""
        _CHAT_LOOP_GUARDS.clear()
        _check_loop("first distinct answer here ....", session_key="state-y")
        _check_loop("second distinct answer here ...", session_key="state-y")
        g = _get_chat_loop_guard("state-y")
        assert g._round == 2  # 同一实例承载两次请求的状态

    def test_registry_bounded_evicts_oldest(self):
        _CHAT_LOOP_GUARDS.clear()
        for i in range(_CHAT_LOOP_GUARDS_MAX + 44):
            _get_chat_loop_guard(f"bulk-{i}")
        assert len(_CHAT_LOOP_GUARDS) <= _CHAT_LOOP_GUARDS_MAX
        assert "bulk-0" not in _CHAT_LOOP_GUARDS  # 最旧被淘汰
        assert f"bulk-{_CHAT_LOOP_GUARDS_MAX + 43}" in _CHAT_LOOP_GUARDS


class TestDetection:
    """检测行为：同session跨请求重复→告警；跨session隔离→不告警"""

    def test_repetition_detected_same_session(self):
        """回归证明：8次相同回答第8次触发告警——旧无状态实现永远返回None"""
        _CHAT_LOOP_GUARDS.clear()
        results = [_check_loop(REPEAT_TEXT, session_key="detect-A") for _ in range(8)]
        assert all(r is None for r in results[:7])
        assert results[7] is not None
        assert results[7]["type"] == "loop_warning"
        assert results[7]["severity"] == "force_stop"  # anything-llm: 文本重复→break_loop级
        assert results[7]["message"]  # mem0失败可见：必须携带原因

    def test_no_false_positive_different_sessions(self):
        """不同用户各自偶发相同回答（如'我不知道'）不互相污染"""
        _CHAT_LOOP_GUARDS.clear()
        results = [_check_loop(REPEAT_TEXT, session_key=f"iso-{i % 2}") for i in range(8)]
        assert all(r is None for r in results)

    def test_short_text_not_flagged(self):
        """<30字符的短文本不做shingle相似度（避免'好的'这类正常短回答误报）"""
        _CHAT_LOOP_GUARDS.clear()
        results = [_check_loop("ok", session_key="short-A") for _ in range(10)]
        assert all(r is None for r in results)

    def test_distinct_answers_never_flagged(self):
        """正常多轮不同回答零误报（内容真正不同，非模板微调）"""
        _CHAT_LOOP_GUARDS.clear()
        distinct = [
            "The weather forecast shows scattered thunderstorms across the coastal region tomorrow.",
            "Python asyncio schedules coroutines cooperatively on a single-threaded event loop.",
            "Photosynthesis converts light energy into chemical energy stored within glucose.",
            "The stock market closed mixed today as tech shares rallied and banks slipped.",
            "Regular expression backtracking can cause catastrophic exponential time complexity.",
            "Mountains form primarily through tectonic plate collision and volcanic activity.",
            "The new regulation requires companies to report emissions quarterly starting 2027.",
            "Quantum entanglement allows correlated measurement outcomes across large distances.",
            "Bread dough rises because yeast produces carbon dioxide during fermentation.",
            "The database index improved query latency from 340ms down to roughly 12ms.",
            "Historical trade routes connected the Mediterranean with South Asia over centuries.",
            "Compiler optimizations include inlining, loop unrolling, and dead code elimination.",
        ]
        for answer in distinct:
            r = _check_loop(answer, session_key="normal-A")
            assert r is None


class TestCallSiteWiring:
    """源码级接线证据：import顶部、调用点传session_key、两条流式路径补齐"""

    def _src(self) -> str:
        return (Path(__file__).parent.parent / "src" / "api" / "chat.py").read_text(
            encoding="utf-8"
        )

    def test_top_level_import(self):
        assert "from src.cortex.loop_guard import LoopGuard" in self._src()

    def test_call_sites_pass_session_key(self):
        text = self._src()
        # 非流式：档案键作session_key（契约随更新：F3b-⑬ 身份轴收口后档案键=
        # _resolve_user_id(identity.user_id) 派生 uid，断言意图零丢失——调用点仍以
        # 归一档案键作 session_key，修前 str(user_id) 自报裸键形不再回潮）
        assert "_check_loop(answer, session_key=str(uid))" in text
        assert "_check_loop(answer, session_key=str(user_id))" not in text
        # 两条流式路径（RAG降级路径 + 正常路径）都接了guard
        assert text.count('_check_loop("".join(answer_parts)') == 2

    def test_stream_paths_accumulate_answer_parts(self):
        text = self._src()
        assert text.count("answer_parts.append(content)") == 2  # 两处content yield都累积

    def test_stateless_pattern_gone(self):
        """旧缺陷模式必须消失：函数体内 new LoopGuard() 不再出现"""
        text = self._src()
        assert "        guard = LoopGuard()\n        result = guard.check" not in text


class TestGuardInstanceSemantics:
    """registry里的实例就是cortex LoopGuard（不是降级/占位实现）"""

    def test_registry_holds_real_loop_guard(self):
        _CHAT_LOOP_GUARDS.clear()
        g = _get_chat_loop_guard("type-check")
        assert isinstance(g, LoopGuard)
        # 检测能力在registry实例上真实可用
        call = {"name": "terminal", "arguments": {"command": "x"}}
        for _ in range(3):
            r = g.check(tool_calls=[call])
        assert r.is_looping
