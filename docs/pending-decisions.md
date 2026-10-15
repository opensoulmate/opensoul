# 待拍板汇总决策材料件（DEC-①）

> 2026-10-14。用途：roadmap 全部切片已勾选，剩余候选全部标注"需拍板/需用户定"且散落在 ~20 条切片的"诚实边界/下一步候选"里。本件把它们收拢成**一张决策单**——每项：现状证据（file:line 已逐条核验）、选项、权衡、建议默认、可逆性。拍板方式：回一句"①取B ②取C ③维持"即可，逐项独立互不阻塞。本件零行为改动（决策材料件先落：TL-③ / TL-⑭ / TL-⑯ / HEAL-② 先例），拍板前不动任何代码。

## 决策单总览

| # | 事项 | 出处 | 建议 | 一句话拍板 |
|---|------|------|------|-----------|
| ① | F3b-⑨b-③ 非 HTTP 面事件身份归属（9 调用点，4 子面；1c 已落 2026-10-14=F3b-⑨b-③-1c） | F3b-⑨ 审计③ | 1a/1b/1d 取"系统事件"——**已按建议默认 A 先行执行 2026-10-14（F3b-⑨b-③-1abd）** | ~~"①aA ①bA ①dA"~~ 已落，异议回「①a改B ①b改B ①d改B」即改道 |
| ② | wakeup executor 认证轴 | F3b-⑧c 边界① | 取 C（executor=系统组件按行租户点火）——**已按建议默认 C 先行执行 2026-10-14（F3b-⑧c-①）** | ~~"②取C"~~ 已落，异议回「②改A」即 revert |
| ③ | episodic 经验 prompt 回流消费点 | F4b-㉜ 边界③ | 维持不注入，等⑤落地 | "③维持" |
| ④ | proactive 推荐回流面消费点 | F2b-②c-1 复核留档 | 取 B（新增 advisory_review 定期行为）——**已按建议默认 B 先行执行 2026-10-14（DEC-④-①）** | ~~"④取B"~~ 已落，形态自决异议回「④改B2」（自动跑任务链）/「④改B3」（周期重提）/「④改A」（revert）即改道 |
| ⑤ | Reflector 写侧触发面（执行结果回传协议） | F4b-㉚ ⑤ | 取 B（定执行结果回传协议），生态层大件——**OpenSoul 接收面已按建议默认 B 先行执行 2026-10-15（DEC-⑤-①）** | ~~"⑤取B/暂不做"~~ 已落接收面，异议回「⑤改A」即 revert；仓外回传方待接入 |
| ⑥ | a2a 取消语义扩展 | F4b-㉔ 锁死语义 | 取 B（显式 /cancel 指令）——**已按建议默认 B 先行执行 2026-10-14** | ~~"⑥取B"~~ 已落，异议回「⑥改A」（revert）/「⑥维持」即改道 |
| ⑦ | unified 其余子搜索存储层加轴 | F3b-⑥ 边界 | 取 B 分批（events 面最小件可先行）——**events 面已按建议默认 B 先行执行 2026-10-14（DEC-⑦-①）、echo 面已落 2026-10-14（DEC-⑦-②）、gene 面已落 2026-10-14（DEC-⑦-③）、files 面已落 2026-10-14（DEC-⑦-④）、agents 面已落 2026-10-15（DEC-⑦-⑤）、trajectory 面已落 2026-10-15（DEC-⑦-⑥）、cron 面已落 2026-10-15（DEC-⑦-⑦）** | ~~"⑦取B"~~ **七面全数已落（DEC-⑦-①~⑦）B 分批清单执行完毕**，异议回「⑦改A」（revert 回系统视图） |
| ⑧ | healer 两小项（死路(a) 修复动作定义 / immune keep_last 缺省） | HEAL-② / ②b-④ | 维持现状 | "⑧维持" |
| ⑨ | TL-⑰ B 类"三删一留"异议窗口（信息项，非决策） | TL-⑰ | 无异议即结 | 异议才回 |
| ⑩ | builtin starter 目录跨租户共享语义 | DEC-⑦-③ 边界② | 取 B（共享只读目录）——**已按建议默认 B 先行执行 2026-10-15（DEC-⑩-①）** | ~~"⑩取B"~~ 已落，异议回「⑩改A」（revert 回严格过滤）即改道 |
| ⑪ | 执行结果回传幂等键语义（DEC-⑤-① 重复回传不幂等） | DEC-⑤-① 诚实边界 | 取 B（显式 result_id 优先+证据指纹派生兜底）——**已按建议默认 B 先行执行 2026-10-15（DEC-⑤-①b）** | ~~"⑪取B"~~ 已落，异议回「⑪改C」（只认显式 key）/「⑪改A」（revert）即改道 |

---

## ① F3b-⑨b-③ 非 HTTP 面事件身份归属语义（9 调用点）

**状态：已按建议默认 A 先行执行（2026-10-14 F3b-⑨b-③-1abd）**——依据：上拍（F3b-⑧c-①）周期日志已把「1a/1b/1d 拍板」列为下一候选 + 用户「权限/审批阻塞时别停工等批」铁律 + ② 同款自决先例；执行=维持 'default' 兜底（零行为改动）+ 三调用面注释文档化 + tests/test_system_event_attribution.py 10 例契约锁死（归属方向：真实租户 JWT 在场事件仍落 'default'；通道方向：外部 webhook 绝不因 Authorization 401），**若拍板改判（1a/1b/1d 任取 B/C）回一句即改道——改道需先改契约测试再动代码**。1c 已另行收口（F3b-⑨b-③-1c）。

**现状**（F3b-⑨ census：46 调用点中 37 个 HTTP 点已由 ②b-1~②b-8 接 bind_identity 派生；余 9 点全归本项）：9 个调用点的事件 dict 无 tenant_id 键 → record() 落 `'default'`（timeline/store.py 兜底）。生产单租户下零行为影响；多租户下这些事件会落错租户（自我不可见）。

### 1a link receive_webhook 1 点（外部 webhook 入口）
- 证据：src/api/link.py:152 `receive_webhook`（:183 push_event，不带租户键）。
- 选项：**A** 维持 `'default'` 兜底并文档化=外部系统事件 / **B** 按 connector owner 归属（connector 行有归属即继承）/ **C** 接 bind_identity（401 拒垃圾 Authorization）。
- 权衡：C 已被 ②b-8 边界实证=**会打断外部 webhook**（第三方调用方无用户 JWT，按用户端点语义接 401 是真回归）；B 需 connector 归属模型先行。
- 建议：**A**（外部调用方非用户身份轴，"connector 系统事件"语义如实）；若拍 B 则连带 connector 归属模型一起定。

### 1b soma_connector 4 点（component token 身份轴）
- 证据：src/api/soma_connector.py:225/:297 等 4 处 push_event；鉴权走 `_verify_token(component_id, x_component_token)`（:259/:289/:344）=component 自有 token 体系，非用户 JWT。
- 选项：**A** 维持 `'default'`（组件=系统资源）/ **B** components 表加 tenant 列+token 派生租户 / **C** 组件改挂用户 JWT。
- 权衡：B 牵动组件注册面+存量组件回填；C 重构鉴权体系。组件本体（心跳/数据推送）语义上是系统器官而非用户代理。
- 建议：**A**；如未来要"用户私有组件"，走 B（先给 components 定归属模型）。

### 1c link_gateway 3 点（helper 汇聚点 + HTTP + WS）
- **状态：已落（2026-10-14 F3b-⑨b-③-1c）**——按建议 B 收口（HTTP 面 bind_identity+WS 凭据双源+helper 形参 1 改盖 9 fan-out），本项退出拍板清单；1a/1b/1d 仍待拍。
- 证据：src/api/link_gateway.py:157 `_record_event`（helper，fan-out 9 站点 :221/:236/:251/:282/:357/:456/:598/:678/:694，改一处覆盖九点）；HTTP 面 + WS `websocket_proxy`（:667，:703 push_event）。当前 handler 无 authorization 形参=零身份轴。
- 选项：**A** 维持 `'default'` / **B** HTTP 面补 bind_identity+WS 握手/query 取参双源（F3b-④ 先例），helper 形参带入（1 改覆盖 9 fan-out）。
- 权衡：与 ②b-1~②b-8 已收的 37 个 HTTP 点同型——link_gateway 若是用户可触发的 HTTP/WS 面，B 与既有收口哲学一致（"自动作自我不可见"在多租户下是真断点）；B 是 9 点里唯一**零语义争议、纯接线**的子面。
- 建议：**B**（可独立先行，不等 1a/1b/1d 裁决）。

### 1d intrusion 中间件 1 点（安全事件归属）
- 证据：src/middleware/intrusion_middleware.py:111 push_event（安全拦截事件，dispatch 内无 actor 语义）。
- 选项：**A** 系统事件 `'default'`（安全防护史归系统）/ **B** 按请求身份派生（有 Authorization 时）否则 `'default'`。
- 权衡：安全拦截史（同类=TL-⑯ 已裁决保留的 nerve 风暴 22,030 行真实安全日志）语义上是系统防护记录；B 会让"攻击者视角的拦截记录"挂到受害者租户名下，反而失真。
- 建议：**A**（系统事件，文档化）。

## ② wakeup executor 认证轴

**状态：已按建议默认 C 先行执行（2026-10-14 F3b-⑧c-①）**——依据：上拍（F3b-⑨b-③-1c）周期日志已把本项列为下一候选（未带「需拍板」标）+用户「权限/审批阻塞时别停工等批」铁律+F1 先例（停问未获回复后按建议默认下拍一件）；实现=凭据轴+系统视图（选项 C 语义），**若拍板取 A/B 回一句即 revert/改道**（轴未配置时现网行为字节不变，零迁移成本）。实现摘要：settings.wakeup_executor_token 配置轴（空=轴关闭）+ _executor_token_verdict 单一判定口（hmac 恒时比较，带头而坏/轴未配置=401 fail-closed 禁静默降级）+ due/claim/fire-result 三口 executor 凭据过=系统视图（due 跨租户全返回逐事件 tenant_id 键=点火落行租户键、claim/ack 只写行本身=行租户键不漂移）。仓外件如实记：acp-proxy agent/wakeup_executor.py 需带 X-Executor-Token 头（值=运维配置的 WAKEUP_EXECUTOR_TOKEN）才真启用跨租户点火，现网 executor 不带头=行为字节不变。

**现状**：due/claim/ack 三 API 已带 bind_identity（src/api/will.py:1101/:1115/:1138），但共享 executor 匿名轮询→派生 `'default'`（F3b-⑧c 边界①）：**非 default 租户的到点事件没有 executor 去点火**（生产全 'default' 行=零影响，多租户=真断点）。
- 选项：**A** 维持（等多租户真实出现再说）/ **B** executor 持 service token 按租户逐一枚举点火 / **C** executor 走系统视图 claim 各租户到点事件、点火动作落**行租户**键。
- 权衡：B 要维护租户清单+service token 体系；C 需要给 executor 开系统旁路——与 F3b-⑦"execute 恒过闸无系统旁路"哲学形成对偶，故需拍板而非自决；但 executor 本质是调度器（与 proactive 心跳同族系统组件），事件行本身已带租户键，C 的行归属天然准确。
- 建议：**C**（executor=系统组件，按行租户点火，事件归属不漂移）。

## ③ episodic 经验 prompt 回流消费点（F4b-㉜ 边界③）

**现状**：brain think 响应 `experience` 观测键（src/api/brain.py:199-215，count+top-3 items）只观测不决策；生产 experiences 980 行 fix 列全空、action 多为不透明 ID（chat_turn:*/job:*）→经验行独有信息=失败错误原文+成败观测，注入执行 prompt=噪声风险（F4b-㉜ 已评估）。
- 选项：**A** 维持不注入 / **B** 失败错误原文 top-k 注入执行 prompt / **C** 先落⑤（Reflector 补 fix 采集）再注入。
- 权衡：B 立刻可做但注入的是噪声（无 fix 的错误原文对执行帮助有限）；C 依赖⑤。
- 建议：**A**（维持，等⑤）；若要先行试水可拍 B 且仅注入 failure 行 error 字段（软建议块语义沿用 F2b-②）。

## ④ proactive 推荐回流面消费点（F2b-②c-1 复核留档）

**状态：已按建议默认 B 先行执行（2026-10-14 DEC-④-①）**——依据：上拍周期日志已把本项列为下一候选（首件）+用户「权限/审批阻塞时别停工等批」铁律+①② 同款自决先例；实现=advisory_review 定期行为（get_recommendations 同一读真源/ADVISORY_SCOPE 同一口径 top_k=3→改进任务上板+回群聊 B0 同面，ai_groups.submit_advisory_task）；形态自决两点（可一句改道）：**B1 提案上板不自动跑任务链**（回「④改B2」即接 _run_task_chain 自动执行）、**at-most-once 同组同 goal 含终态不重复上**（回「④改B3」即改为仅对未终结行去重=周期重提）、全 revert=「④改A」。诚实边界：生产当前零群组=行为休眠至用户建组（无群组 reason=no_groups 空转可观测零副作用）。

**现状**：proactive 固定四行为（experience_collect / pattern_extract / …，src/will/proactive_behaviors.py）无"按推荐调整行为"接缝——learned_patterns 产出的推荐只有任务执行/对话/evolve 三面消费（F2b-②②b②c-1 已落），主动行为面零消费（复核定性=无消费接缝，非断点漏修）。
- 选项：**A** 不做 / **B** 新增 `advisory_review` 定期行为（读推荐→生成改进类任务回群/看板）/ **C** 心跳 prompt 注入推荐。
- 权衡：B 需要定"改进任务"的消费形态（回群聊？进任务链？）；C 让心跳 prompt 变动=行为漂移风险。
- 建议：**B**（消费点=AI 群组改进任务，与 B0 任务链回群聊同面），具体形态需用户拍。

## ⑤ Reflector 写侧触发面（F4b-㉚ ⑤）

**状态：OpenSoul 接收面已按建议默认 B 先行执行（2026-10-15 DEC-⑤-①）**——依据：DEC-⑦-⑳ 周期日志候选首件「⑤ Reflector 执行结果回传协议」+ 用户「权限/审批阻塞时别停工等批」铁律 + ①②④⑥⑦ 自决先例。实现=**执行结果回传协议 OpenSoul 半边**：`POST /api/brain/execution-result` 接收面（agent 回传执行证据 test_output/exit_code/exception/file_written/diff→Reflector 汇总真 Verification，成败由检查派生禁客户端自报 success→ExperienceMemory.record 落 experiences 含 fix 列）+ Reflector exit_code 证据检查（退出码→真 Verification，pending-decisions 原文「test_output/exit code」两证据全收）+ task_id 溯源进 metadata。**仓外回传方（acp-proxy/OpenMate 侧在执行完真回传）如实记=待生态层接入**（F3b-⑧c-① wakeup executor 同款「仓外件」边界：接收面已就绪、现网行为零变化=无人回传则无新行）；③（episodic 经验 prompt 回流）的解锁条件=fix 列有真数据，回传方接入后自然解锁。**若拍板改判回一句即改道：「⑤改A」=revert 本拍（接收面移除）**。

**现状**：src/cortex/reflector.py:22 `Reflector.reflect()` 全仓零生产调用（grep 实证），`Verification → ExperienceMemory.record` 整链零调用=experiences 的 fix 列恒空的根因（连带③）。硬接的障碍：本仓执行模型=外部 agent 代理，**无 file_written / test_output / diff 结果形态**，伪造 outcome 违 ExperienceCollector"绝不伪造 outcome"纪律。
- 选项：**A** 不接（维持）/ **B** 定义执行结果回传协议（agent 执行完回传 test_output/exit code→真 Verification）/ **C** 从 job_queue 结果推断成败。
- 权衡：B 是生态层协议件（涉及 acp-proxy/OpenMate 侧回传），大件但根因修；C 只有 exit code 无 checks 明细，产出的 Verification 信息量低。
- 建议：**B**（生态层排期）；短期维持 A。此项决定③的解锁时机。

## ⑥ a2a 取消语义扩展（F4b-㉔ 锁死语义冲突）

**状态：已按建议默认 B 先行执行（2026-10-14，显式 /cancel 指令）**——依据：PROACT-④ 周期日志候选首件 +「别停工等批」铁律 + ①②④ 自决先例。执行=**显式指令面**（消息以 `/cancel` 开头=取消承载本消息的任务，_explicit_cancel_command 命令 token 全等判定，_process_message 路由入口先于 skill/普通/pending 三路路由判定）；**㉔ 锁死语义零触碰**（闭集取消短语仍只在 §6.3 pending 回答分支生效、无 pending 的「不用了」照常 completed——本拍只加显式指令面，不打破锁死）。若拍板改判回一句即改道：「⑥改A」=revert 本拍（显式指令移除）、「⑥维持」=同 revert；选项 C（闭集扩普通路由）未做（与锁死语义正面冲突，需另行拍板）。

**现状**：`_is_cancel_intent` 只在 §6.3 pending 回答分支生效（src/a2a/task_manager.py 定义 / 唯一消费=pending 回答分支）；无 pending 的「不用了」走普通路由照常 completed（F4b-㉔ 明确锁死"不越权改状态"，测试锁死）。扩展=打破该锁死语义，需拍板。
- 选项：**A** 维持（宁窄勿滥）/ **B** 增显式取消指令（如消息以 `/cancel` 开头=取消当前任务）/ **C** 闭集扩到普通路由（「不用了」也可取消任意任务）。
- 权衡：C 与锁死语义正面冲突且误杀风险高（"如何取消订单"类真查询）；B 无歧义、向后兼容；A 零风险。
- 建议：**B**（显式指令），或 A 维持。

## ⑦ unified 其余子搜索存储层加轴（F3b-⑥ 边界）

**状态：events 面已按建议默认 B 先行执行（2026-10-14 DEC-⑦-①）、echo 面已落（2026-10-14 DEC-⑦-②）、gene 面已落（2026-10-14 DEC-⑦-③）、files 面已落（2026-10-15 DEC-⑦-④）、agents 面已落（2026-10-15 DEC-⑦-⑤）、trajectory 面已落（2026-10-15 DEC-⑦-⑥）、cron 面已落（2026-10-15 DEC-⑦-⑦）**——依据：DEC-⑥-① 周期日志候选「⑦ unified 子搜索加轴」+用户「权限/审批阻塞时别停工等批」铁律+①②④⑥ 自决先例；实现=各面 *_row_tenant 行租户键单一判法（自带键缺失→'default' 兜底，timeline record() 同款）+子搜索恒按 bind_identity 派生租户过滤（tenant_id 必选形参禁 silent fallback）；echo 面同拍收口读真源断点（修前 new MessageDispatcher() 空历史=echo 子搜索恒空集）；**若拍板回「⑦改A」即 revert 回系统视图**。⑦ 七面全数收口（2026-10-15 DEC-⑦-⑦ cron 面=最后一面），B 分批清单执行完毕。

**现状**：unified 的 files/events/agents/trajectory/cron/gene/echo 七个子搜索存储层加轴已全数落地（2026-10-14~15 DEC-⑦-①~⑦，本段为决策历史记录）。注：events 子搜索查的是 **event_stream._event_buffer 全局内存缓冲**（非 timeline.db；timeline 面租户过滤 ③c-2① 另落），内存缓冲面已于 DEC-⑦-① 加租户键过滤。
- 选项：**A** 维持系统视图 / **B** 分批加轴（逐存储面拆件，F3b-③ 系同型）/ **C** 只收 events（缓冲 dict 加租户键）。
- 权衡：B 是跨模块大件；C 最小但缓冲是全局内存面（重启即清），隔离收益有限。
- 建议：**B**（分批，events/echo 面最小件可先行）；或 A 维持到多租户真实出现。

## ⑧ healer 两小项

- **HEAL-② 死路(a)**：degraded/HTTP/exception 症状→无对症维护动作→如实 NONE（HEAL-②b-② 已裁决"宁缺勿滥"维持）。要真修需**逐器官定义 degraded 修复动作**（维护策略拍板）。建议：**维持**。
- **immune audit cleanup keep_last 缺省**：=容量之半（src/immune/audit.py:107，策略值）。建议：**维持**（或拍一个固定值）。

## ⑨ TL-⑰ B 类"三删一留"异议窗口（信息项）

TL-⑰ 已按 TL-⑯ 建议执行：storm 22,030 行（OpenImmune 开发验证流量=真实安全日志史）**保留**、exec/pipeline/brain 1,098 行（夹具探针，出处逐字钉）**已删**。备份 `data/backups/timeline_b_pre_delete_*` 可整体回滚。无需决策，有异议回一句即可。

---

## ⑩ builtin starter 目录跨租户共享语义（DEC-⑦-③ 边界②）

**状态：已按建议默认 B 先行执行（2026-10-15 DEC-⑩-①）**——依据：DEC-⑤-① 周期日志候选首件「builtin 共享语义（需拍板）」+ 用户「权限/审批阻塞时别停工等批」铁律 + ①②④⑥⑦⑤ 自决先例。实现=**共享只读目录**：`template_visible_to` 读面可见性单一判法（builtin 对全租户可见，其余行恒按行归属 template_row_tenant 单一源）+ 七读面真消费（list/get/categories/tags/search/export_all/stats）+ 目录列表面 GET /api/gene/templates 漏网点同拍收口（修前零鉴权恒系统视图=任意调用方可见全部租户行，⑦ 系列漏网）。**写面零变化**（测试锁死）：delete builtin 恒拒、创建劫持闸/import 覆盖仍按行归属（共享≠可写）。**若拍板取 A 回一句即改道：「⑩改A」=revert 本拍（回严格过滤，非 default 租户不见 starter 目录）**；选项 C（每租户复制）未做（管理复杂度高，需另行拍板）。

**现状**（DEC-⑦-③ 边界② 留档）：builtin 模板（author='system'、builtin=True、引擎启动注册 22 条 starter）行键='default'（缺省兜底），严格过滤下非 default 租户的 gene 搜索/列表/categories/tags/stats/export/get/instantiate 全部不见 starter 目录=多租户下新租户目录空、"starter 目录"语义不成立。严格过滤是 ⑦ 租户轴机械化的暂行边界（"暂与其他面同款严格过滤宁窄勿滥"）。
- 选项：**A** 维持严格过滤（builtin 归 'default' 独享）/ **B** 共享只读目录（builtin 对全租户可见可 instantiate/clone，写面保护不变）/ **C** 每租户复制一份。
- 权衡：A 让每个新租户从空目录起步，starter 语义不成立；C 需复制管理+漂移风险。B 共享的是系统内容非租户数据=零泄露面，与「用户行恒不跨租户」并存（测试双向锁死）。
- 建议：**B**（builtin=系统 starter 目录，非任何租户的数据；严格过滤是机械副作用）。

## ⑪ 执行结果回传幂等键语义（DEC-⑤-① 诚实边界「重复回传不幂等」）

**状态：已按建议默认 B 先行执行（2026-10-15 DEC-⑤-①b）**——依据：DEC-⑦-㉓ 周期日志候选首件「DEC-⑤-① 重复回传幂等（需定 key 语义）」+ 用户「权限/审批阻塞时别停工等批」铁律 + ①②④⑤⑥⑦⑩ 自决先例。实现=**幂等键单一派生口 execution_result_key**（显式 result_id 非空白→"explicit:"+id=客户端声明报告身份；未给→"derived:"+sha256(证据字段规范 JSON) 指纹）+ experiences.result_key 列（DDL+ALTER 幂等自愈 + UNIQUE(tenant_id, agent_id, result_key) 唯一索引=at-most-once 结构保证）+ record() 同键第二次回传不落新行、如实回报首行 outcome/error/fix（duplicate=True、recorded=False 禁谎报）。去重作用域=(tenant_id, agent_id) 行键（跨租户/跨 agent 同键互不去重，防去重面变跨租户信息通道）。**若拍板改判回一句即改道：「⑪改C」=只认显式 result_id（去派生兜底，未给 key 一律各记一行）、 「⑪改A」=revert（回不幂等）**。

**现状**（DEC-⑤-① 诚实边界留档）：POST /api/brain/execution-result 同份证据重复回传（网络重试/agent 重试）落多行 experiences=经验面重复计数；幂等键语义未定（客户端自报？证据派生？）。
- 选项：**A** 维持不幂等 / **B** 显式 result_id 优先+证据指纹派生兜底 / **C** 只认显式 result_id（未给 key 不去重）。
- 权衡：C 完全依赖回传方配合（仓外回传方未接入=现网重试仍重复落行）；A 保持重复计数缺陷；B 兼顾"无人配合也幂等"（同份证据=同键）与"客户端可显式声明报告身份"，证据变化（exit_code 改变等=另一次观测）异键各记一行不吞真实观测。
- 建议：**B**。
- 诚实边界：①90 天老化淘汰（既有 _aging_prune）删行后同键可再记一行（键随行释放）②并发同键由 UNIQUE 索引兜底（撞键回读首行如实 duplicate，非吞错）③键指纹=字面证据字段哈希非语义等价判定（"语义相同"但字面不同的两份报告=两行）④显式 result_id 同 id 不同证据=同一报告（客户端声明即身份，要区分两次上报就给不同 id）。

## 附：锚点核验

本件引用的全部 file:line 锚点已程序化核验（逐条对当前 HEAD 源码回读，结果存 `data/dec1_anchor_verification.json`）；核验不通过的锚点不上墙。核验方式见周期日志 DEC-① 行。
