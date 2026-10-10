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
- [x] A2 记忆存储迁 SQLite（与 hippo 统一，替换 JSON 文件存储）（2026-10-10 已落）
- [x] A3 主动行为能力（心跳驱动的定期任务，不只被动响应）（2026-10-10 已落）

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
- 2026-10-10 22:58 | A3落：ProactiveEngine从"只存在"补到"真调度"（此前src/will/proactive.py零调用点）。心跳30s一拍只跑到期watcher（各行为独立interval：memory_decay 6h/experience_collect 1h/heredity_triggers 30min），三默认行为全是已有器官真实代码路径的周期化（hippo run_decay_cycle衰减、learn ExperienceCollector采集、heredity evaluate_triggers熔断巡检auto_propose=False）；每拍留痕data/proactive.db proactive_events（result json.dumps可回读，失败也留痕禁静默）；新增/api/proactive status|events|run/{name}观测面板（手动触发与心跳共用run_watcher单路径），main.py lifespan注册启动+关停取消。三重校验：①git diff +205行落盘（proactive.py）+3新文件②grep接线main.py:400-409 register_default_watchers→start→stop、api/proactive.py→同一单例、observe_loop:97→run_watcher:104③pytest test_proactive 9 passed（心跳到期/失败隔离/留痕回读/三行为真效果/路由200·404）+全量2607 passed+ live :8090重启真e2e：/api/proactive/status running=true三watcher首拍全ok（experience_collect真采45条experiences），POST run/heredity_triggers真返回、run/nope 404、proactive_events表4行回读一致
- 2026-10-10 22:40 | A2落：记忆层JSON文件存储清零——course_engine（learn课程/章节/测验/进度，记忆层唯一JSON写方）迁SQLite。storage_dir/learn.db两表（courses+chapters，tags/topics/knowledge_ids/quiz列json.dumps），公开API零改动（api/learn.py、api/search.py调用面原样）；遗留course_*.json首载一次性迁移入库（INSERT OR IGNORE幂等）后改名*.json.migrated；delete_course同步清DB两表。存量核查：user_memory/experience/long_term/hippo早已SQLite，唯一残留cortex/project_memory.save_cache为死代码（grep零调用点、从未落盘project_memory.json）已记档不属记忆层。三重校验：①git diff +174行落盘②调用点接线api/learn.py:13 engine=CourseEngine()→create/add_chapter→_save_course×7处→SQLite，search.py:195同③pytest全量2598 passed（含4新测：跨实例持久化/不写JSON/遗留迁移幂等+DB行数回读/删除清库）+ live :8090重启真实迁移4个JSON→learn.db（stats 4课程8章节与迁移前一致）+ API建课建章→DB行回读（quiz correctIndex往返一致）→删除后0行、无新*.json
- 2026-10-10 22:14 | A1落：brain/think|verify接入真实执行流程_execute_task_impl（execute/retry/任务链共用路径）——think在执行前（意图+风险+决策，决策真生效：deny即拒执行落failed不静默跳过），verify在执行后（结果入认知记忆，失败加重），brain自身故障不阻断执行但必发brain-error事件。三重校验：①git diff +84行真实落盘②grep调用链_run_task_background:709/retry:1188/任务链:906→_execute_task_impl→brain，main.py:616已挂router③pytest test_ai_groups_brain_wiring 3 passed（含deny门禁/故障不阻断）+test_ai_groups回归10 passed + live :8090 echo桩真e2e：POST execute→事件流brain-think(goal=fix_bug risk=low mode=auto)→started→output→finished→scored→brain-verify，/api/brain/recall?session_id=task回读[think]/[verify]双记忆。验证组a1proof已清理
- 2026-10-10 21:58 | B0收尾（上拍遗落未提交改动，实测发现两处假接线）：①_notify_group_chat推送不存在的:8092/internal/group-message（httpx收404不抛异常→静默丢弃）改为直写共享库discussion_messages（ws_group._persist_message同款schema）+_emit_exec_event供WS尾随广播 ②_run_soulmate_turn线协议对齐live :8092/ws/acp实测：session/new取result.sessionId→session/prompt(sessionId,messageId,prompt)→session/update流式params.update.content.text。实测SoulMate真回'收到'+群聊落库回读✓、task_execution_events广播事件✓、pytest test_ai_groups 10 passed
