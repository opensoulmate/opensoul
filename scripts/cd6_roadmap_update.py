#!/usr/bin/env python3
"""CD-⑥ roadmap 更新：checklist 条目 + 周期日志倒序追加（字节通道绕显示层竖线坑）。"""
from __future__ import annotations

import re
from pathlib import Path

P = chr(124)
road = Path("/home/climbing/opensoul/docs/dev-roadmap.md")
text = road.read_text(encoding="utf-8")

checklist = (
    "- [x] CD-⑥ 修复候选③ traces events 端点改指真端点 + 同页消费键深挖扩面"
    "（CD-③ 深挖 confirmed_drift 归 4 修复候选「一候选一拍」第③件；㉔/㉕/㉖ 拍板件跳过）"
    "（2026-10-18 已落）——修前（CD-③ 裁决证据+控制组+live --pre 三实证非推断）："
    "①events 取数打错端点（候选③本体）——loadSessionDetail 的 data.events 打在 "
    "GET /api/trajectory/sessions/{id}（真源 session.to_dict() 无 events 键）→会话详情"
    "展开恒空事件列表（有事件也不显示），真源=GET /api/trajectory/sessions/{id}/events "
    "{session_id,events,count}②深挖扩面（普查启发式漏收如实记，CD-⑤ 同款：census 只抓 "
    "apiFetch 调用点顶层响应键、setSessions 三元回落形不进元素级消费链=零 findings）："
    "session 元素 5 键全漂移（session_id/event_count/first_event/last_event/agents 对真源 "
    "id/total_events/created_at/ended_at/agent_id）→列表行 React key 全 undefined（React "
    "对 undefined 渲染为空不显字面 'undefined'=P7 恒真探测不到，逐键断言逼出）、事件数/"
    "时间全空、Agent 恒空、搜索过滤对 undefined toLowerCase 必炸；event 元素 timestamp "
    "漂移（真源 created_at）→事件时间恒空。收口（增量精确替换零既有文件重写，1 源文件 "
    "24+/24-+新测 12+探针 1）：①events 取数改指真端点（data.events 真源键在场）"
    "②TrajectorySession/TrajectoryEvent 接口收编真源形状、消费点全改真源键（ended_at "
    "可空走 ended_at 或 created_at 回落，GLAND-⑤ 同款）③agents 逗号串→真源 agent_id "
    "单值（真源无多 agent 字段禁编造）。tests/test_cd6_repair.py 12 例（源码消费面 6+"
    "census 真跑真仓 3+行为桥真 API 全链 3：tmp 全隔离真 router 真 store，list 元素/"
    "events 元素逐键对账+旧端点真无 events 键硬证）。三重校验：Gate1 git diff admin-ui "
    "1 源文件+新测 12+探针+部署脚本+构建产物真落盘（pnpm build 80/80 零错、chr(124) "
    "探针 23==23 零星号损坏——patch 通道竖线坑绕行走字节脚本精确替换）②Gate2 部署 "
    "traces chunk（3l4shf32uozke.js）真源键（total_events/ended_at/created_at/events "
    "尾缀）真消费+旧漂移串（event_count/first_event/last_event/.session_id/.timestamp）"
    "残留=0③Gate3 四层实证：新测 12 passed+控制组（git worktree HEAD f5f7432e 旧 UI "
    "跑同测试）11 failed/1 passed 精确命中——11 行为/结构断言旧代码全炸（修前硬反证）、"
    "1 不变面（旧端点真无 events 键=真源不变量）新旧皆过非自嗨+相关回归 54 passed"
    "（cd5 15+cd4 7+census 13+deep-dive 19）+轨迹域回归 82 passed+全量 non-a2a pytest "
    "5875 passed 0 failed（623s，-m not live 9 deselected；记账闭环：CD-⑤ 记档 5872 "
    "collected+本拍新测 12=5884=5875+9 精确吻合）+test_a2a live 分跑 20 passed（总账 "
    "5904=5892+12 对平）+live 双向 e2e（scripts/e2e_cd6_traces_events_ui.py 真浏览器 "
    "CDP 点击流+Network 域真请求观测）：--pre（换 HEAD 旧 bundle 同探针）"
    "13/21=8 项修复断言全炸缺口硬证（session id 0/2 不渲染/事件数空/展开不可达/零 "
    "/events 请求/时间空/Agent 空）→--post 21/21（session id 2/2、3 事件、展开 3/3 "
    "事件内容+工具调用徽章+时间=当日、Network 真端点恰 1 次+旧详情端点 0 次、Agent "
    "徽章真渲染、清痕 remaining=0）。过程自纠（如实记）：①测试作者错误 1 处（session "
    "漂移键接口声明全文扫误伤 TrajectoryEvent 真源键 session_id——判定改按接口分段，"
    "测试问题非代码问题）②census 工具盲区/bug 2 处如实记：typed 赋值（const data: T = "
    "await apiFetch）不进 _ASSIGN_RE 提取（本拍改无注解赋值保工具契约，接口保留文档"
    "面）；_extract_first_arg 括号深度把模板串 call_end 截断在表达式内→路径归一丢尾段"
    "（typed 形下 events 键被错挂 list_sessions）——工具修复另拍③e2e 探针首轮 login "
    "wall 假阴（鉴权注入竞态）被探针自带 probe-not-blind 闸抓住，补 P0 登录墙硬闸+token "
    "竞态兜底后复跑稳定。诚实边界：①census 对元素级键零 findings=启发式盲区（三元回"
    "落 setter 不进消费链），行为桥逐键对账补位（CD-④ 边界③同型）②agent 多值显示收编"
    "单值 agent_id（会话级多 agent=真源补现另拍）③SessionDetail 接口保留作真源形文档面"
    "（不作类型注解=census 提取契约）④工作区遗留 src/marrow/backup.py 未提交改动继续"
    "不动不入本拍提交（GLAND-③ 留档纪律延续）。下一步候选待拍：CD-③ 修复候选④ limb "
    "windows 元素容器收编（4 候选末件）/ DedupResult 面漂移复核（㉕ 边界③）/ ㉖ gland "
    "用量拆分恢复（需拍板：建议取 B1 入出两轴）/ ㉔ sessions db 处置拍板（需拍板）/ "
    "census 工具模板串截断 bug 修（工具自身，可另拍）/ 用户实测反馈（无输入）。\n"
)

logline = (
    "- 2026-10-18 18:35 | CD-⑥ 落：修复候选③ traces events 端点改指真端点+同页消费键"
    "深挖扩面（CD-③ 留档「4 修复候选一候选一拍」第③件=最新周期日志候选首件无需拍板"
    "可执行件；㉔/㉕/㉖ 拍板件跳过）——修前（CD-③ 裁决证据+控制组+live --pre 三实证非"
    "推断）：①events 取数打错端点（候选③本体）data.events 打在 GET /api/trajectory/"
    "sessions/{id}（真源 session.to_dict() 无 events 键）→会话详情展开恒空事件列表，"
    "真源=GET /sessions/{id}/events {session_id,events,count}②深挖扩面（普查启发式漏"
    "收如实记）session 元素 5 键全漂移（session_id/event_count/first_event/last_event/"
    "agents 对真源 id/total_events/created_at/ended_at/agent_id，React key 全 undefined、"
    "事件数/时间/Agent 全空、搜索过滤必炸）+event 元素 timestamp 漂移（真源 created_at）。"
    "收口（1 源文件 24+/24-+新测 12+探针）：取数改指真端点+接口/消费点收编真源键+agents "
    "逗号串收编 agent_id 单值。三重校验：新测 12 passed+控制组（HEAD f5f7432e）11F/1P "
    "精确命中（1 不变面=旧端点真无 events 键）+相关回归 54+轨迹域 82+全量 non-a2a 5875 "
    "passed 0 failed（记账 5872+12=5884=5875+9 deselected 吻合）+test_a2a live 20（总账 "
    "5904 对平）+live 双向 e2e --pre 13/21（8 修复断言全炸缺口硬证）→--post 21/21"
    "（Network 真端点恰 1 次+旧详情端点 0 次、展开 3/3 事件内容真渲染、清痕 "
    "remaining=0）。过程自纠：测试接口声明全文扫误伤真源键（改按接口分段）+census 工具"
    "盲区 2 处（typed 赋值不提取/模板串截断丢尾段，工具修复另拍）+e2e 首轮 login wall "
    "假阴被 probe-not-blind 闸抓住（补 P0 硬闸+token 竞态兜底）。下一步候选待拍：候选④ "
    "limb windows 元素容器收编（4 候选末件）/ DedupResult 面漂移复核 / ㉖/㉔ 拍板件 / "
    "census 工具 bug 修 / 用户实测反馈（无输入）。\n"
)

anchor = "## 周期日志（倒序追加，一行一拍）\n"
i = text.index(anchor)
j = i + len(anchor)
while text[j] == "\n":
    j += 1
text = text[:j] + logline + text[j:]

m = re.search(r"^- \[x\] CD-④ ", text, re.M)
assert m, "CD-④ checklist anchor missing"
line_end = text.index("\n", m.start())
text = text[: line_end + 1] + checklist + text[line_end + 1 :]

road.write_text(text, encoding="utf-8")
out = road.read_text(encoding="utf-8")
print("roadmap updated; CD-⑥ occurrences:", out.count("CD-⑥"))
print("pipes:", out.count(P), "bad union:", out.count("string * null"))
assert out.count("- [x] CD-⑥ ") == 1
assert "CD-⑥ 落：" in out
print("ANCHOR-PROBE OK")
