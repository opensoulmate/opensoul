"""记忆/会话行归属判定单一源（F3b-㉛）。

F3b-⑳ 在 api/hippo.py 以面内 helper 定义「记忆行归属=父会话行继承」；brain
记忆面（/remember·/recall + think/verify/feedback 写点，独立 store/sessions
实例）收口时若再抄一份判定=两套判定漂移面（F3b-⑳「归属判法单一源」纪律）。
本模块把判定收编为参数化纯函数：调用方传入各自 SessionManager 真源，判法
只此一份（api/hippo.py 与 api/brain.py 共用）。

判定语义（与 F3b-⑳ 原文逐字一致）：
- 记忆行无 user 轴，归属唯一来源=父会话行 user_id（③b-2 _task_tenant 同权威）；
- 父会话缺失（孤儿记忆）或归属空白→DEFAULT_OWNER 兜底（键派生故障绝不吃掉
  落库，③b-2 旁路纪律）。
"""

DEFAULT_OWNER = "default"


def memory_owner(sessions, mem) -> str:
    """记忆行归属=父会话行继承；孤儿记忆/归属空白→'default' 兜底。"""
    parent = sessions.peek(mem.session_id)
    if parent is None:
        return DEFAULT_OWNER
    return parent.user_id or DEFAULT_OWNER


def session_owner(sessions, session_id: str) -> str | None:
    """会话行归属；会话缺失→None（闸语义/兜底由调用方决定）。"""
    parent = sessions.peek(session_id)
    if parent is None:
        return None
    return parent.user_id or DEFAULT_OWNER
