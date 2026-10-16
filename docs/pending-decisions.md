# 待拍板汇总决策材料件（DEC-①）

> 2026-10-14。用途：roadmap 全部切片已勾选，剩余候选全部标注"需拍板/需用户定"且散落在 ~20 条切片的"诚实边界/下一步候选"里。本件把它们收拢成**一张决策单**——每项：现状证据（file:line 已逐条核验）、选项、权衡、建议默认、可逆性。拍板方式：回一句"①取B ②取C ③维持"即可，逐项独立互不阻塞。本件零行为改动（决策材料件先落：TL-③ / TL-⑭ / TL-⑯ / HEAL-② 先例），拍板前不动任何代码。

## 决策单总览

| # | 事项 | 出处 | 建议 | 一句话拍板 |
|---|------|------|------|-----------|
| ① | F3b-⑨b-③ 非 HTTP 面事件身份归属（9 调用点，4 子面；1c 已落 2026-10-14=F3b-⑨b-③-1c） | F3b-⑨ 审计③ | 1a/1b/1d 取"系统事件"——**已按建议默认 A 先行执行 2026-10-14（F3b-⑨b-③-1abd）** | ~~"①aA ①bA ①dA"~~ 已落，异议回「①a改B ①b改B ①d改B」即改道 |
| ② | wakeup executor 认证轴 | F3b-⑧c 边界① | 取 C（executor=系统组件按行租户点火）——**已按建议默认 C 先行执行 2026-10-14（F3b-⑧c-①）** | ~~"②取C"~~ 已落，异议回「②改A」即 revert |
| ③ | episodic 经验 prompt 回流消费点 | F4b-㉜ 边界③ | 维持不注入，等⑤落地——**⑤ 接收面已落（2026-10-15 DEC-⑤-①）而 fix 真数据 0 行（仓外回传方未接入），已按 doc 明示试水形态 B 先行执行 2026-10-15（DEC-③-①：仅注入 failure 行 error 字段）** | ~~"③维持"~~ 已落试水 B，异议回「③改A」（revert 回不注入）/「③改C」（等 fix 数据改注入 fix）即改道 |
| ④ | proactive 推荐回流面消费点 | F2b-②c-1 复核留档 | 取 B（新增 advisory_review 定期行为）——**已按建议默认 B 先行执行 2026-10-14（DEC-④-①）** | ~~"④取B"~~ 已落，形态自决异议回「④改B2」（自动跑任务链）/「④改B3」（周期重提）/「④改A」（revert）即改道 |
| ⑤ | Reflector 写侧触发面（执行结果回传协议） | F4b-㉚ ⑤ | 取 B（定执行结果回传协议），生态层大件——**OpenSoul 接收面已按建议默认 B 先行执行 2026-10-15（DEC-⑤-①）** | ~~"⑤取B/暂不做"~~ 已落接收面，异议回「⑤改A」即 revert；仓外回传方待接入 |
| ⑥ | a2a 取消语义扩展 | F4b-㉔ 锁死语义 | 取 B（显式 /cancel 指令）——**已按建议默认 B 先行执行 2026-10-14** | ~~"⑥取B"~~ 已落，异议回「⑥改A」（revert）/「⑥维持」即改道 |
| ⑦ | unified 其余子搜索存储层加轴 | F3b-⑥ 边界 | 取 B 分批（events 面最小件可先行）——**events 面已按建议默认 B 先行执行 2026-10-14（DEC-⑦-①）、echo 面已落 2026-10-14（DEC-⑦-②）、gene 面已落 2026-10-14（DEC-⑦-③）、files 面已落 2026-10-14（DEC-⑦-④）、agents 面已落 2026-10-15（DEC-⑦-⑤）、trajectory 面已落 2026-10-15（DEC-⑦-⑥）、cron 面已落 2026-10-15（DEC-⑦-⑦）** | ~~"⑦取B"~~ **七面全数已落（DEC-⑦-①~⑦）B 分批清单执行完毕**，异议回「⑦改A」（revert 回系统视图） |
| ⑧ | healer 两小项（死路(a) 修复动作定义 / immune keep_last 缺省） | HEAL-② / ②b-④ | 维持现状 | "⑧维持" |
| ⑨ | TL-⑰ B 类"三删一留"异议窗口（信息项，非决策） | TL-⑰ | 无异议即结 | 异议才回 |
| ⑩ | builtin starter 目录跨租户共享语义 | DEC-⑦-③ 边界② | 取 B（共享只读目录）——**已按建议默认 B 先行执行 2026-10-15（DEC-⑩-①）** | ~~"⑩取B"~~ 已落，异议回「⑩改A」（revert 回严格过滤）即改道 |
| ⑪ | 执行结果回传幂等键语义（DEC-⑤-① 重复回传不幂等） | DEC-⑤-① 诚实边界 | 取 B（显式 result_id 优先+证据指纹派生兜底）——**已按建议默认 B 先行执行 2026-10-15（DEC-⑤-①b）** | ~~"⑪取B"~~ 已落，异议回「⑪改C」（只认显式 key）/「⑪改A」（revert）即改道 |
| ⑫ | /api/link/events·/stats·/health 路由遮蔽收口·归属 | 1c-③ 边界② | 取 B（归属拆分：OpenLink 留 canonical、gateway 三读面迁 /api/link/gateway/*）——**已按建议默认 B 先行执行 2026-10-16（F3b-⑨b-③-1c-⑥）** | ~~"⑫取B"~~ 已落，异议回「⑫改A」（回遮蔽死路由态）/「⑫改C」（三路径归 gateway）即改道 |
| ⑬ | 外部回调入口鉴权化统一拍板（heartbeat+incoming） | 1c-④ 边界锁死自注留档 | 取 B（register 签发回调 token，回调必须持证）——**已按建议默认 B 先行执行 2026-10-16（DEC-⑬）** | ~~"⑬取B"~~ 已落，异议回「⑬改A」（revert 回 id 即凭证）/「⑬改C」（改全局共享 secret）即改道 |
| ⑭ | /ws/{user_id} 等其余长连接面身份轴（terminal WS 无鉴权=RCE 面） | 1c-⑤ 边界③留档+最新周期日志候选首件 | 取 B（握手凭据必持 fail-closed：有效活跃用户 JWT 才给 shell）——**已按建议默认 B 先行执行 2026-10-16（DEC-⑭-①）** | ~~"⑭取B"~~ 已落，异议回「⑭改A」（revert 回无鉴权）/「⑭改C」（本地回环免鉴权）即改道 |
| ⑮ | websocket/handler.py 幽灵模块处置（挂载 or 删除） | DEC-⑭-① 边界③留档+DEC-⑭-③ 候选首件 | 取 B（删除纯死资产模块：零 import 零挂载零消费方，C3-③ 死资产处置先例）——**已按建议默认 B 先行执行 2026-10-16（DEC-⑮）** | ~~"⑮取B"~~ 已落，异议回「⑮改A」（备份/git 恢复模块）/「⑮改C」（挂载+身份轴做成真实通道，另拍实现）即改道 |
| ⑯ | terminal 路径对齐（OpenMate 面板期望 ws://host:8090/ws/terminal vs 本仓 /api/terminal/ws/terminal） | DEC-⑭-① 边界④留档+DEC-⑮ 候选首件 | 取 B（本仓加挂顶层别名挂同一 handler）——**已按建议默认 B 先行执行 2026-10-16（DEC-⑯）** | ~~"⑯取B"~~ 已落，异议回「⑯改A」（撤下别名回面板连不上态）/「⑯改C」（改 openmate URL，跨仓改动需用户授权）即改道 |
| ⑰ | ws_chat connected 帧 user_id 载荷收口（dict repr 怪形→纯用户 id） | DEC-⑭-③ 边界④留档+DEC-⑯ 候选首件 | 取 B（载荷=纯用户 id，向生态参照形 acp-proxy ws_chat.py 对齐）——**已按建议默认 B 先行执行 2026-10-16（DEC-⑰）** | ~~"⑰取B"~~ 已落，异议回「⑰改A」（回 dict repr 怪形）/「⑰改C」（整键移除 connected 帧 user_id）即改道 |
| ⑱ | WS 连接审计面（terminal 先拍，拆件 ⑱-②/③/④） | DEC-⑭-① 边界⑤留档 | 取 B（terminal WS 面先拍）——**已按建议默认 B 先行执行 2026-10-16（DEC-⑱+⑱-②/⑱-③/⑱-④ 四面全落）** | ~~"⑱取B"~~ 四面全落，异议回「⑱改A」（revert 撤审计面）/「⑱改C」（organ 归口改独立安全 organ）即改道 |
| ⑲ | link.py receive_webhook token 化（OpenLink connector webhook 入口凭据门） | DEC-① 1a 契约锁死后另拍（DEC-⑬ 边界④留档） | 取 B（register 签发回调 token，X-Callback-Token 或 X-Signature HMAC 等效证明，缺/错 401 零副作用）——**已按建议默认 B 先行执行 2026-10-16（DEC-⑲）** | ~~"⑲取B"~~ 已落，异议回「⑲改A」（revert 回 id 即凭证）/「⑲改C」（改全局共享 secret）即改道 |

---

## ① F3b-⑨b-③ 非 HTTP 面事件身份归属语义（9 调用点）

**状态：已按建议默认 A 先行执行（2026-10-14 F3b-⑨b-③-1abd）**——依据：上拍（F3b-⑧c-①）周期日志已把「1a/1b/1d 拍板」列为下一候选 + 用户「权限/审批阻塞时别停工等批」铁律 + ② 同款自决先例；执行=维持 'default' 兜底（零行为改动）+ 三调用面注释文档化 + tests/test_system_event_attribution.py 10 例契约锁死（归属方向：真实租户 JWT 在场事件仍落 'default'；通道方向：外部 webhook 绝不因 Authorization 401），**若拍板改判（1a/1b/1d 任取 B/C）回一句即改道——改道需先改契约测试再动代码**。1c 已另行收口（F3b-⑨b-③-1c）。

**现状**（F3b-⑨ census：46 调用点中 37 个 HTTP 点已由 ②b-1~②b-8 接 bind_identity 派生；余 9 点全归本项）：9 个调用点的事件 dict 无 tenant_id 键 → record() 落 `'default'`（timeline/store.py 兜底）。生产单租户下零行为影响；多租户下这些事件会落错租户（自我不可见）。

### 1a link receive_webhook 1 点（外部 webhook 入口）
- **状态补充（2026-10-16 DEC-⑲）**：凭据轴已 token 化（见 ⑲ 详档）——归属轴 A 语义逐字保留（事件 'default' 兜底+Authorization 绝不 401 绝不改归属），但「匿名无证 200」面由 ⑲ 刻意变更为缺证 401 零副作用（connector 自有凭据与用户 JWT 轴分开，契约测试已随更新）。
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

**状态：已按 doc 明示试水形态 B 先行执行（2026-10-15 DEC-③-①）**——依据：上拍（DEC-⑤-①b）候选「③ episodic 回流（等 fix 真数据）」的 C 形态解锁条件实测未满足（生产 experiences 1,537 行 fix 列 0 行、result_key 0 行=仓外回传方至今零回传），A（维持）=无限期停滞；doc 原文已明示「若要先行试水可拍 B 且仅注入 failure 行 error 字段（软建议块语义沿用 F2b-②）」+用户「别停工等批」铁律+DEC 自决先例。实现=**试水形态 B**：episodic_advisory_for 取数口（ExperienceMemory.get_similar_failures F4b-㉚ 相关度真源：字面相关门槛，无关零注入宁缺勿滥）+format_episodic_advisory_for_prompt 纯函数（**仅 failure 行 error 字段**——intent_summary/fix 等一律不进 prompt，测试锁死；软建议块自带免责）+_execute_task_impl 与 F2b-② learned 块并列第二块注入（故障不阻断但 learned-context-error 可见）。**若拍板改判回一句即改道：「③改A」=revert 本拍（回不注入）、「③改C」=等 fix 真数据后改注入 fix**。诚实边界：①相关度=字面字符二元组非语义 embedding（F4b-㉚ 既有口径）②对话面已补齐（DEC-③-①b 2026-10-16：chat/ws_chat 面已接同款试水 B 注入）③命中失败经验会同时抬升风险评估（F4b-㉚ 既有设计）→确认门禁，与注入同源同刻发生。

**现状**：brain think 响应 `experience` 观测键（src/api/brain.py:199-215，count+top-3 items）只观测不决策；生产 experiences 1537 行 fix 列全空、error 非空 22 行（真数据=试水 B 的注入源）、action 多为不透明 ID（chat_turn:*/job:*）→经验行独有信息=失败错误原文+成败观测，注入执行 prompt=噪声风险（F4b-㉜ 已评估）。
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

## ⑫ /api/link/events·/stats·/health 路由遮蔽收口·归属（1c-③ 边界② 留档）

**状态：已按建议默认 B 先行执行（2026-10-16 F3b-⑨b-③-1c-⑥）**——依据：F3b-④b 周期日志候选首件「路由遮蔽收口（需拍板：/api/link/events·/stats·/health 归属）」+ 用户「权限/审批阻塞时别停工等批」铁律 + ①②③④⑤⑥⑦⑩⑪ 自决先例。实现=**归属拆分**：/api/link/events·/stats·/health 归 OpenLink（link.py 活消费方：admin-ui link 页/event_stream 轮询/7 处健康监测/admin_actions——活面契约逐键不变），link_gateway 三读面迁 **/api/link/gateway/events·/stats·/health**（修前被同前缀先挂的 link_router 遮蔽=生产死路由，live 修前实证三路径全回 OpenLink、gateway 面不可达；1c-③ 刚落的租户过滤事件面「单测已验活面不可达」一并收口）；gateway 其余活面（/webhooks /systems /push /broadcast /stream /ws）路径字节不变。回归锁死 tests/test_link_route_shadowing.py 4 例（main.py 挂载序静态/canonical 归属逐键不变/gateway 三读面真可达/link 命名空间 (method,path) 零重复）。**若拍板回一句即改道：「⑫改A」=gateway 三读面回 /api/link/* 且接受遮蔽（回修前死路由态）、「⑫改C」=三路径归 gateway、OpenLink 面迁走（需同步改 admin-ui/监测等 10+ 活消费方）**。

**现状**（1c-③ 边界② 留档）：main.py 两 router 同挂 /api/link、link_router 先挂→OpenLink /events /stats /health 与 LinkGateway 同名三读面被遮蔽=生产死路由（gateway 读面 wired-but-not-functional：1c-③ 租户过滤事件面单测已验而活面不可达）；公共 API 归属未定。
- 选项：**A** 维持遮蔽（归属 OpenLink，gateway 三读面留死/删）/ **B** 归属拆分（OpenLink 留 canonical、gateway 三读面迁 /api/link/gateway/*）/ **C** 三路径归 gateway（OpenLink 面迁走）。
- 权衡：C 需改 admin-ui link 页+event_stream 轮询+7 处健康监测等 10+ 活消费方（破坏活契约）；A 留死路由=静默断点持续（违背「写了≠接线了」）；B 零活契约变化（OpenLink 面逐键不变）+死面复活（1c-③ 租户过滤面生产可达）+两组件同名读面各归各（gateway 面自带 component 判别键）。
- 建议：**B**。
- 诚实边界：①若有外部调用方曾按 /api/link/events 期望 gateway 数据——修前该期望本就落空（拿到的是 OpenLink 数据），迁移后须改调 /api/link/gateway/*；本仓内调用点已全数随更新（3 测试文件 11 处+2 e2e 探针）②遮蔽根因以「路径拆分」收口而非「同名合并」——两读面数据面不同（connector 事件 vs 集成事件）不可合并③FastAPI 新版 include_router 惰性挂载（app.routes 只见 _IncludedRouter 不展开子路由）——重复路由检测真源=router.routes 自身 route 表（测试注释记档，防未来误用 app.routes 写出空转断言：首版断言在旧代码 3P 假绿被控制组逼出后修真）。

## ⑬ 外部回调入口鉴权化统一拍板（heartbeat+incoming，1c-④ 边界锁死自注留档）

**状态：已按建议默认 B 先行执行（2026-10-16 DEC-⑬）**——依据：F3b-⑨b-③-1c-⑥ 周期日志候选首件「外部回调入口鉴权化统一拍板（heartbeat+incoming，可按 DEC 自决先例下拍）」+ 用户「权限/审批阻塞时别停工等批」铁律 + ①~⑫ 自决先例。实现=**register 签发回调 token**（F3b-④/⑤「claim 注册身份必须持 register 签发 token」同族同哲学）：WebhookEndpoint/ExternalSystem 增 callback_token（register 签发 uuid4().hex，明文只回注册响应一次、list 面不回吐）+ `_callback_token_ok` 单一判定口（hmac 恒时比较，heartbeat/incoming 两入口共用禁两套判法漂移）——两回调入口必须持 **X-Callback-Token**（incoming 另收既有 wh.secret HMAC 签名为等效证明：发方持 secret 即持证，签名面契约零变化）；缺/错=401 零副作用（trigger_count/事件落库/heartbeat_count/status 一字不动）；fail-closed 无证可验=拒（register 恒签发 token=生产不可达"无证"行）。既有契约零变化（测试锁死）：缺行 404/disabled 503/413 载荷上限、回调入口非用户 JWT 轴（1a 语义延续：跨租户用户 JWT + 行 token 照收、垃圾 Authorization 不打断——401 只来自回调凭据轴，与 Authorization 两条身份轴互不顶替 F3b-⑤ 同款）。**若拍板回一句即改道：「⑬改A」=revert 本拍（回 id 即凭证无鉴权）、「⑬改C」=改全局共享 secret（env 配置所有回调共用）**。

**现状**（1c-④ 边界锁死测试自注「鉴权化需与 incoming 入口统一拍板，1a 待拍面同类」+ live 修前实证 200）：两外部回调入口零鉴权——`POST /api/link/webhooks/{webhook_id}/incoming`（webhook_id 即凭证，secret HMAC 可选=不配即全开）、`POST /api/link/systems/{system_id}/heartbeat`（system_id 即凭证）——任意人可伪造心跳把 offline 顶成 online/抬 heartbeat_count、伪造 webhook 注入事件/改 trigger_count。
- 选项：**A** 维持（id 即凭证）/ **B** register 签发回调 token（回调必须持证）/ **C** 全局共享 secret（env 配置，所有回调共用一把）。
- 权衡：A=伪造面持续（心跳/webhook 均可被任意人伪造，1c-④ 已实证跨租户借用面）；C 共享秘密一漏全漏、且与 register 签发语义不同族（F3b-④/⑤ 哲学=每注册身份各持各证）；B 与 agent 注册 token 同族，每注册行独立凭据、泄漏半径单行、发方凭据=注册时交付（与 webhook secret/agent token 交付方式同款）。
- 建议：**B**。
- 诚实边界：①凭据是 bearer 型共享秘密非请求签名（incoming 的 HMAC 签名面保留为等效证明，未强制签名）②token 无轮换/撤销端点（重注册=新行新 token；_webhooks/_systems 内存注册表重启即清=行本身无持久撤销面）③admin-ui 不展示 callback_token（API 注册响应可见，UI 面另拍）④link.py receive_webhook（OpenLink connector webhook 入口）不在本拍——① 1a 已拍 A（外部 webhook 绝不因 Authorization 401，契约锁死），如需同款 token 化另拍（已落 DEC-⑲ 2026-10-16，见 ⑲）⑤soma_connector 心跳（X-Component-Token）/agent 心跳（X-Agent-Token）各有自有凭据轴不在本拍⑥404/503 契约先于凭据检查=入口存在性可被探知（既有契约不变优先，如需凭据前置另拍）。

## ⑭ /ws/{user_id} 等其余长连接面身份轴（terminal WS 无鉴权=RCE 面）

**状态：已按建议默认 B 先行执行（2026-10-16 DEC-⑭-①）**——依据：最新周期日志候选首件「/ws/{user_id} 等其余长连接面身份轴（需拍板）」+ 用户「权限/审批阻塞时别停工等批」铁律 + ①~⑬ 自决先例（异议回「⑭改A」即 revert）。「一拍一面」先例拆件：本拍=terminal WS（活面最高险），ws_chat 连接期重校验、websocket/handler.py 幽灵面处置留待拍。**后续（2026-10-16 DEC-⑭-②）**：terminal 连接期凭据周期重校验已落（失效凭据 close 1008+看门狗进 wait 集撤销即拆会话杀 shell，60s 有界滞后；ws_chat 连接期重校验仍留待拍）。实现=**握手凭据必持（fail-closed，DEC-⑬ 同款「无证可验=拒」）**：`_ws_presented_token` 双源取参（?token= query 主口——浏览器 WS 设不了头，OpenMate 终端面板现状即带 token；Authorization: Bearer 头兜底）→ `_terminal_identity`（bind_identity 单一派生口，F3b-② 家族禁第二套判法）——缺 token / 坏 token / 未知用户 / 停用身份 = 握手拒绝（accept 前 close 1008，F3b-④ 同语义），无证不给 shell 不 spawn 进程；有效活跃用户 JWT 放行，真 spawn bash 语义零变化（D1 契约）。

**现状**（live 修前探针 8 项 3 FAIL 硬证，scripts/e2e_terminal_ws_auth.py 修前/修后同探针）：`/api/terminal/ws/terminal` 零鉴权——任意网络可达方连接即真 spawn bash（无 token SHELL_OK=远程代码执行面）、坏 token/停用用户 token 同给 shell。长连接面盘点：agent WS（F3b-④/④b 已收）、link_gateway WS/SSE（1c/1c-⑤ 已收）、ws_chat /chat（已有 decode_token 握手轴）、terminal WS（本拍）、websocket/handler.py `/ws/{user_id}`（**幽灵模块：全仓零 import 零挂载，/ws/terminal 与 /ws/{user_id} 均不在活路由表**）。
- 选项：**A** 维持无鉴权（本地单机信任）/ **B** 握手凭据必持（有效活跃用户 JWT，fail-closed）/ **C** 本地回环免鉴权（仅非 loopback 要凭据）。
- 权衡：A=RCE 面持续（8090 对局域网开放即任意代码执行）；C 需信任边界判定（反向代理/XFF 伪造面，loopback 判法=另发明第二套语义且与 F3b-② 派生哲学不一致）；B 与 ws_chat（用户 WS 面同款持 JWT）同轴，OpenMate 终端面板已带 token=真消费方零破坏（token 为 /api/user/login 签发的 opensoul JWT，bind_identity 直接可验）。
- 建议：**B**。
- 诚实边界：①连接期凭据不周期重校验（F3b-④b/1c-⑤ 型看门狗另拍——token 过期后已建立的 shell 会话延续至断开）②匿名本地访问从此须带 token=既有行为变更（单机用户经 OpenMate/带 token 连接不受影响；回「⑭改C」即改道）③websocket/handler.py `/ws/{user_id}` 幽灵模块处置（挂载 or 删除）留待拍——未挂载=无泄露面④OpenMate terminal-panel 期望路径 `ws://host:8090/ws/terminal`（非 /api/terminal/ws/terminal）live 实证 404/403=面板现状连不上终端（跨仓消费方期望与本仓路由不齐），路径对齐（本仓加挂 or openmate 改 URL）需拍板留档⑤连接不落 timeline/审计事件（仅 logger 行，审计面另拍）⑥ws_chat /chat 已有握手轴但无连接期重校验（同①留档）。

## ⑮ websocket/handler.py 幽灵模块处置（挂载 or 删除）

**状态：已按建议默认 B 先行执行（2026-10-16 DEC-⑮）**——依据：DEC-⑭-③ 周期日志候选首件「websocket/handler.py 幽灵面处置（需拍板）」+ 用户「权限/审批阻塞时别停工等批」铁律 + ①~⑭ 自决先例。实现=**删除纯死资产模块**（C3-③ 遗留旧 SPA 死资产处置先例：备份+git rm）：`src/websocket/`（handler.py 58 行 + __init__.py）整目录删除，删前 tar 备份 `data/backups/websocket_ghost_pre_delete_20261016_101326.tar.gz`（sha256 e12fabea…，解包即整体回滚）+git 历史双保险。**若拍板回一句即改道：「⑮改A」=从备份/git 恢复模块（回幽灵态）、「⑮改C」=挂载+身份轴做成真实每用户通道（需另拍实现——现协议 user_id 自报无鉴权，直接挂载=重开 DEC-⑭ 刚收口的身份顶号面）**。

**现状（处置依据，逐条实证）**：`src/websocket/handler.py` 是 init 脚手架（commit d14f1731「init: OpenSoul服务端代码结构」）遗留的 `/ws/{user_id}` 端点——①全仓零 import 零挂载（src/tests grep `src.websocket|ws_router|ConnectionManager` 仅命中自身定义；main.py 无 include_router）②live 路由表实证 4 个真 WS 面（/api/collab/ws/agent/{agent_id}、/api/link/ws、/api/terminal/ws/terminal、/api/ws/chat）中无 /ws/{user_id}（1073 路由 census，`data/dec15_ghost_evidence.json`）③零消费方（openmate 全部 WS 用法=/ws/group、/ws/acp、ws://:8090/ws/terminal，无一指向 /ws/{user_id}）④此后仅被 lint/format 提交触碰，从未接线=「写了≠接线了」典型。
- 选项：**A** 维持现状（留幽灵模块）/ **B** 删除（死代码清除，git 可回滚）/ **C** 挂载+身份轴（做成真实每用户通知通道）。
- 权衡：A 留死代码=后续周期反复误读（DEC-⑭-① 过程自纠已实证：测试作者把本模块 ping/pong 协议误当 terminal 协议）；C 需发明事件源（ConnectionManager.send_to_user/broadcast 零调用方）+身份轴改造（user_id 自报与 DEC-⑭ 家族哲学相悖），且通知需求已有 /api/events/sse 事件流底座覆盖=无消费方的 speculative feature（画蛇添足）；B 零行为变化（从未挂载=从未有此面）+消除误读源。
- 建议：**B**。
- 诚实边界：①删除的是从未挂载的死代码，零活契约变化（活面 4 个 WS 端点零触碰，回归锁死）②若未来真需要每用户通道，按「⑮改C」另拍实现（身份轴=DEC-⑭ 家族 bind_identity 派生）③备份 tar 与证据工件为 data/ gitignore 本地件（`data/backups/websocket_ghost_pre_delete_20261016_101326.tar.gz`、`data/dec15_ghost_evidence.json`），回滚=git revert 或 tar 解包④src/static 无对应前端（admin-ui 零消费）⑤「terminal 路径对齐」（OpenMate 期望 ws://host:8090/ws/terminal）是另一候选未动——本拍只处置幽灵模块，不越界改路径。

## ⑯ terminal 路径对齐（OpenMate 面板期望路径 vs 本仓路由）

**状态：已按建议默认 B 先行执行（2026-10-16 DEC-⑯）**——依据：DEC-⑮ 周期日志候选首件「terminal 路径对齐（需拍板）」+ 用户「权限/审批阻塞时别停工等批」铁律 + ①~⑮ 自决先例。实现=**本仓加挂顶层别名 `/ws/terminal`**（`alias_router` 挂**同一 handler 函数对象** `terminal_ws`——零第二实现=零鉴权/看门狗/PTY 漂移面，测试锁死 endpoint is 同一对象），canonical `/api/terminal/ws/terminal` 契约零变化；DEC-⑭-① fail-closed 握手+DEC-⑭-② 连接期重校验因同一 handler 自动对别名生效（鉴权零放宽）。**若拍板回一句即改道：「⑯改A」=撤下别名（回面板连不上态）、「⑯改C」=改 openmate/src/components/terminal-panel.tsx:49 URL 指向 /api/terminal/ws/terminal（跨仓改动，动 ~/openmate 需用户明示授权）**。

**现状（live 修前同探针实证非推断）**：openmate/src/components/terminal-panel.tsx:49 连 `ws://${window.location.hostname}:8090/ws/terminal?token=...`（顶层路径），本仓只挂 /api/terminal/ws/terminal（main.py prefix="/api/terminal"）→ 面板连不上终端（DEC-⑭-① live 实证 404/403；本拍修前探针 P2 REJECTED(InvalidStatus) 复证，修后同探针 SHELL_OK）。
- 选项：**A** 维持（面板连不上）/ **B** 本仓加挂顶层别名（挂同一 handler）/ **C** 改 openmate URL 指向 canonical。
- 权衡：C 是正根修（消费方期望与 canonical 对齐）但触碰 ~/openmate（「改动只限 ~/opensoul」铁律禁止，需用户授权）；A 持续=终端面板功能死面（用户实测直接撞上）；B 零跨仓改动、同一 handler 零漂移、canonical 不动（C 的根修窗口保留），代价=双路径并存（路由 census 1073→1074 +1，(method,path) 零新重复——既有 2 重复键 GET /api/health、/api/gene/health 为 HEAD 既有非本拍引入）。
- 建议：**B**（最小可执行件）。
- 诚实边界：①别名与 canonical 双路径并存（两形并档；若未来拍 C 改 openmate 后别名可撤）②鉴权/看门狗与 canonical 同源自动一致（同一函数对象非复制实现）③live 探针 REJECTED(InvalidStatus) 形态不区分「鉴权拒绝 403」与「路径不存在 404」（websockets 异常名同形）——鉴权拒绝的精确判别在单测 close code 1008 vs 1000（TestClient 404 形态）+ forkpty 零 spawn 哨兵；P2（修前 REJECTED→修后 SHELL_OK）为双向硬证主轴④别名不挂 /health（顶层只加 WS 面，canonical health 契约零变化）。

## ⑰ ws_chat connected 帧 user_id 载荷收口（dict repr 怪形）

**状态：已按建议默认 B 先行执行（2026-10-16 DEC-⑰）**——依据：DEC-⑯ 周期日志候选首件「connected 帧 user_id 载荷收口（需拍板）」+ 用户「权限/审批阻塞时别停工等批」铁律 + ①~⑯ 自决先例。实现=**载荷=纯用户 id**：`_connected_user_id` 单一判法（判定口回包 user_id 键→str() 纯 id；缺失/None→"" 空串如实禁 fabricate dict repr）+ connected 发送点真消费（ws_chat.py:560 修前 `str(user)` 直接序列化判定口回包 dict）。**若拍板回一句即改道：「⑰改A」=回 dict repr 怪形（revert 本拍）、「⑰改C」=整键移除 connected 帧 user_id（或改名，需同步消费方）**。

**现状（live 修前同探针实证非推断）**：`/api/ws/chat` 握手成功后 `{"type":"connected","user_id":"{'user_id': UUID('36c7c3a0-…'), 'role': 'user'}"}`——user_id 载荷是 Python dict repr（含 UUID() repr），非纯用户 id，客户端不可直接当 id 用（scripts/e2e_ws_chat_connected_frame.py --pre 实证）；且与生态参照形漂移：acp-proxy/ws_chat.py:661 同名 connected 帧 user_id=纯 UUID 字符串。
- 选项：**A** 维持怪形 / **B** 载荷=纯用户 id（向生态参照形对齐）/ **C** 整键移除或改名。
- 权衡：A 持续=载荷不可用+与参照形漂移（DEC-⑭-③ 边界④明示留档另拍）；C 破坏帧形状且无收益（消费方需要身份宣告）；B 与 acp-proxy 参照形同款、消费方拿得到真 id；代价=既有契约变更（test_ws_chat_token_revalidation 契约随更新，dict repr 硬反证锁死禁回退）。消费面核实：~/openmate 全仓无 /api/ws/chat connected 帧 user_id 消费方（grep 实证零命中）=变更零已知破坏面。
- 建议：**B**（纯 id）。
- 诚实边界：①载荷只含纯 id 不带 role 等附加键（宁窄勿滥，需要另拍加性键）②键缺失/None→"" 空串如实（键派生故障不捏造身份；decode_token 契约恒带 user_id 键=生产不可达）③帧键集恰 {type,user_id} 不变④a2a/agent_proxy 等其余帧面零触碰。

## ⑱ WS 连接审计面（terminal WS 面先拍）

**状态：已按建议默认 B 先行执行（2026-10-16 DEC-⑱）**——依据：DEC-⑭-⑤ 周期日志候选首件「WS 连接审计面（需拍板）」+ 用户「权限/审批阻塞时别停工等批」铁律 + ①~⑰ 自决先例。实现=**terminal WS 连接生命周期事件落 timeline/活动流（organ=immune 🛡 安全归口）**：`_audit` 同步口（push_event=活动流缓冲+持久 timeline 双落，nerve 总线既有 fire-and-forget）+ 三类事件（terminal_ws_rejected 握手拒绝/terminal_ws_connected 真 spawn 后/terminal_ws_closed 会话结束含 reason+duration_s）。**若拍板回一句即改道：「⑱改A」=revert 回仅 logger 行（撤审计面）、「⑱改C」=全 WS 面一次收（ws_chat/link/collab 继续补件）**。**【2026-10-16 ⑱-② 已落】ws_chat 面连接审计同款三事件（ws_chat_rejected（cause 单列真因 missing_token/invalid_token）/ws_chat_connected/ws_chat_closed（reason∈{client_disconnect,error,credential_revoked}+duration_s））已按 B 拆件纪律落地（commit 见周期日志 DEC-⑱-②）；link_gateway/collab 面拆 ⑱-③/⑱-④ 仍留待拍**。

**现状（--pre 修前同探针实证非推断）**：terminal WS（RCE 面）连接生命周期零审计——握手拒绝/连接建立/凭据撤销/会话结束只打 logger 行，timeline/活动流零记录（scripts/e2e_terminal_ws_audit.py --pre 实证：三类审计行全缺 rows=[]）；DEC-⑭-① 诚实边界⑤「连接不落 timeline/审计事件（仅 logger 行，审计面另拍）」即此件。
- 选项：**A** 不落审计（维持仅 logger）/ **B** terminal WS 面先拍（一拍一面先例，ws_chat 面拆 ⑱-②）/ **C** 全 WS 面一次收（terminal+ws_chat+link_gateway+collab）。
- 权衡：A 持续=RCE 面谁开过 shell 不可回放（安全审计缺核心面）；C 违「一拍一面」拆件纪律（⑳/㉑/DEC-⑭-②③ 先例）且各面身份轴语义不同（ws_chat=用户对话、link=集成、collab=agent）；B 最小可执行件+活面最高险先行（DEC-⑭-②「shell RCE 面先拍」同序）；代价=其余长连接面仍无审计（⑱-② 候选）。
- 建议：**B**（terminal 先拍）。
- 诚实边界：①审计走 push_event 同步口，不进通知中心（emit 的 await 型面在可取消收尾路径下实测被取消吞掉=静默丢审计，同步口=取消免疫是硬要求；nerve 总线 fire-and-forget 照常）②ws_chat 面已落（⑱-②，2026-10-16 DEC-⑱-②）；link_gateway/collab 长连接面审计留待拍（⑱-③/⑱-④）③rejected 行 tenant 恒 'default'（被拒无派生身份，③b-2 兜底纪律）④事件按连接粒度（input/resize 按键不落审计——隐私+噪声，需要另拍）⑤organ 归口=immune（安全事件既有归口 intrusion_detected/permission_denied 同流），若主张独立 organ 可改道。

## ⑲ link.py receive_webhook token 化（OpenLink connector webhook 入口凭据门）

**状态：已按建议默认 B 先行执行（2026-10-16 DEC-⑲）**——依据：DEC-⑱-④ 周期日志候选首件「link.py receive_webhook token 化（① 1a 契约锁死后另拍）」+ 用户「权限/审批阻塞时别停工等批」铁律 + ①~⑱ 自决先例。实现=**register 签发回调 token**（DEC-⑬ heartbeat/incoming 同轴同哲学，F3b-④/⑤ 同族「claim 注册身份必须持 register 签发 token」）：create_connector 恒签发 callback_token（uuid4().hex，明文只回注册响应一次、get/list 只回 has_callback_token）+ receive_webhook 必持证——**X-Callback-Token**（注册签发凭据）或既有 **X-Signature** HMAC（connector.secret 等效证明，发方持 secret 即持证，签名面契约零变化）双证取或；两证全无/全错=401 零副作用（record_event 不落、trigger_count 不动、零通知）；fail-closed 空行 token 拒。判定语义单一源=src/services/callback_token.callback_token_ok（DEC-⑬ link_gateway._callback_token_ok 改委托同口，禁两套判法漂移）。**若拍板回一句即改道：「⑲改A」=revert 回 id 即凭证（伪造面回归）、「⑲改C」=改全局共享 secret（env 一把，泄漏半径全局）**。

**现状（live --pre 同探针 6/6 实证非推断）**：`POST /api/link/webhook/{connector_id}` 零凭据门——connector_id 即凭证、secret HMAC 可选（不配即全开）：任意人可伪造 webhook 注入 timeline 事件/改 trigger_count/发通知（DEC-⑬ heartbeat/incoming 同型伪造面）。DEC-① 1a 曾拍 A（维持 'default' 兜底+「外部 webhook 绝不因 Authorization 401」契约锁死）；DEC-⑬ 边界④明示「如需同款 token 化另拍」即本件。
- 选项：**A** 维持（id 即凭证）/ **B** register 签发回调 token（X-Callback-Token 或 X-Signature 等效证明，缺/错 401）/ **C** 全局共享 secret（env 配置）。
- 权衡：A=伪造面持续；C 共享秘密一漏全漏且与 register 签发语义不同族（F3b-④/⑤ 哲学=每注册身份各持各证）；B 与 DEC-⑬ heartbeat/incoming 完全同轴，每 connector 独立凭据、泄漏半径单行、发方凭据=注册时交付。与 DEC-① 1a 契约的分界（刻意保留）：凭据轴≠用户身份轴——Authorization 头仍绝不触发 401、绝不改事件归属（持证匿名发方事件照旧 'default'），401 只可能来自 connector 凭据门；1a 的「真回归」顾虑（第三方无用户 JWT）由「凭据=注册时交付的 connector 自有凭据」化解（不需要用户 JWT）。
- 建议：**B**。
- 诚实边界：①DEC-① 1a 的「匿名无证 200」面是刻意行为变更（缺证 401），test_system_event_attribution 1a 三例+test_remaining_emit_tenant third_party_boundary 契约随更新（归属轴断言逐字保留、持证跑）②凭据是 bearer 型共享秘密非请求签名（X-Signature HMAC 保留为等效证明，未强制签名）③token 无轮换/撤销 API 端点（重注册=新 connector 新 token；manager.update_connector 程序内可换=既有通用更新语义；connector 注册表进程内存重启即清）④admin-ui 不展示 callback_token（API 注册响应可见，UI 面另拍）⑤404/503 契约先于凭据检查=入口存在性可被探知（DEC-⑬ 边界⑥ 同序，既有契约不变优先）⑥connector.secret 由调用方自配（弱 secret 的 HMAC 等效证明强度=secret 强度，如实）。



## 附：锚点核验

本件引用的全部 file:line 锚点已程序化核验（逐条对当前 HEAD 源码回读，结果存 `data/dec1_anchor_verification.json`）；核验不通过的锚点不上墙。核验方式见周期日志 DEC-① 行。
