# OpenSoul 开发流水线路线图（并行车道）

使命：把 OpenSoul 建成 OpenSoulmate 生态的**认知层+服务端**（大脑）：记忆、认知、调度、管理后台。

## 执行纪律（每次周期必守）
1. 挑下面**下一个未完成的最小切片**，做完勾选 `[x]` 并追加周期日志
2. 增量修改：已有文件禁全量重写，用精确替换
3. 不测试不勾选；失败如实记录，禁谎报
4. 完成即 `git commit`（提交信息说明改了什么/为什么）
5. 路由陷阱：新 router 必须在 `src/main.py` include_router + 验证非404
6. SQLite 陷阱：UUID 手动 str()、metadata 要 json.dumps、migrations 是 PG 语法不能直接跑

## Phase A — 认知层接入执行（对齐 docs/roadmap.md 最紧迫问题）
- [x] A1 SoulBrain `/api/brain/think|verify` 接入实际执行流程（被调用而非仅存在）（2026-10-10 已落）
- [ ] A2 记忆存储迁 SQLite（与 hippo 统一，替换 JSON 文件存储）
- [ ] A3 主动行为能力（心跳驱动的定期任务，不只被动响应）

## Phase B — AI 群组完善（用户实测反馈驱动）
- [x] B0 任务链自动执行+结果回群聊（2026-10-10 已落）
- [ ] B1 旧任务/子任务状态治理（历史 pending 清理或补跑，看板数据可信）
- [ ] B2 群组讨论（start_discussion）与任务链联动：任务执行完可发起讨论
- [ ] B3 探测即注册：/api/agents/detect 与 acp-proxy /agents/probe 打通（检测成功=必能开会话）

## Phase C — 服务端健壮性
- [ ] C1 captures/schema 漂移自愈推广到其余表
- [ ] C2 统一错误响应（不谎报ok）审计全 API
- [ ] C3 管理后台（8090 单页SPA）与 OpenMate 同设计风格对齐

## 周期日志（倒序追加，一行一拍）
- 2026-10-10 22:14 | A1落：brain/think|verify接入真实执行流程_execute_task_impl（execute/retry/任务链共用路径）——think在执行前（意图+风险+决策，决策真生效：deny即拒执行落failed不静默跳过），verify在执行后（结果入认知记忆，失败加重），brain自身故障不阻断执行但必发brain-error事件。三重校验：①git diff +84行真实落盘②grep调用链_run_task_background:709/retry:1188/任务链:906→_execute_task_impl→brain，main.py:616已挂router③pytest test_ai_groups_brain_wiring 3 passed（含deny门禁/故障不阻断）+test_ai_groups回归10 passed + live :8090 echo桩真e2e：POST execute→事件流brain-think(goal=fix_bug risk=low mode=auto)→started→output→finished→scored→brain-verify，/api/brain/recall?session_id=task回读[think]/[verify]双记忆。验证组a1proof已清理
- 2026-10-10 21:58 | B0收尾（上拍遗落未提交改动，实测发现两处假接线）：①_notify_group_chat推送不存在的:8092/internal/group-message（httpx收404不抛异常→静默丢弃）改为直写共享库discussion_messages（ws_group._persist_message同款schema）+_emit_exec_event供WS尾随广播 ②_run_soulmate_turn线协议对齐live :8092/ws/acp实测：session/new取result.sessionId→session/prompt(sessionId,messageId,prompt)→session/update流式params.update.content.text。实测SoulMate真回'收到'+群聊落库回读✓、task_execution_events广播事件✓、pytest test_ai_groups 10 passed
