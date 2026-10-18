# 契约漂移普查（CD-①）——admin-ui 消费键 vs API 真源键

> 2026-10-18 | 审计即切片本体（GLAND-⑤ trajectory UI↔API 契约漂移的同型面普查，零行为改动）
> 工具：`scripts/census_contract_drift.py`（只读静态普查 + live openapi 挂载路由表交叉）
> 工件：`data/contract_drift_census_20261018.json`

## 方法（三层真源，禁推断当实证）

1. **消费面**：admin-ui 全部 `apiFetch` 调用点（77 文件 / 193 调用点）→ 响应键消费
   （直接属性、解构、别名 `const x = data.k`、state setter 别名 `setSessions(data.sessions)`
   → `sessions.map((s) => s.k)`——GLAND-⑤ 原始消费形、元素级 `data.rows.map(r => r.k)`）。
   JS 内建成员（length/map/find…）词表排除；路径非字面量如实标 `unresolved_path` 禁空串匹配。
2. **真源面**：src/main.py `include_router` 挂载面 + 模块 `APIRouter(prefix=...)` 内嵌前缀
   （禁手抄 prefix）→ (method, path) 端点索引 1,010 条 → 返回形状提取：
   dict 字面量键 / SQL SELECT 别名 / `response_model=`（含基类字段并入，wire 契约权威）/
   模型字段（dataclass/BaseModel 注解、to_dict 字面量）/ helper 显式键。
3. **live 交叉**：`:8090/openapi.json` 挂载路由表真源核对路径级存在性（`missing_in_openapi`）。

判法分级（drift_hard ≠ 定罪，须人工复核）：

| verdict | 含义 |
|---|---|
| produced | 消费键在真源键集内 |
| drift_hard | 键缺席 + 形状全解析（零动态操作）→ 高置信候选 |
| drift_suspect | 键缺席 + 形状含动态操作（spread/model_dump/跨模块）→ 待确认 |
| unresolved_endpoint / unresolved_shape / unresolved_path | 端点未映射 / 形状提取失败 / 路径非字面量 → 待确认 |

## 总账（2026-10-18 实跑）

produced 103 / drift_hard 3 / drift_suspect 10 / unresolved_endpoint 6 /
unresolved_shape 25 / unresolved_path 3（路径面：in_openapi 141、missing_in_openapi 6）。

人工复核结论：**真断点 4（trajectory 同型）+ 误报排除 2 + 待确认 38**。

## 确认真断点（4，逐个双端取证）

### CD-①-A hermes 页发送回执键漂移（key drift）
- UI：`hermes/page.tsx:114` `data.status === 'ok' ? '消息已发送' : '状态: ${data.status}'`
- API 真源：`src/api/hermes_bridge.py:245` `return {"ok": True, "content": output, "source": "hermes-cli"}`
  （dict 字面量全解析，零动态）
- 症状：响应无 `status` 键 → 发送成功也显示「状态: undefined」，成功提示永不可达。
- 取证：源码双端逐字 + 工具 drift_hard。未 live 打 send（会真发消息，禁副作用探针）。

### CD-①-B limb 页截图键漂移（key drift）
- UI：`limb/page.tsx:223` `if (data.image) setScreenshotResult(...)` + `/api/limb/rpa/ocr` 的
  `data.text`（对照：OCR 键真源存在零漂移）
- API 真源：`src/api/limb_rpa.py:455` `response_model=ScreenshotResponse` →
  **live openapi 实证** `ScreenshotResponse = {base64, display_server, format, height, width}`
- 症状：响应无 `image` 键（真键名 `base64`）→ 截图永不显示（UI 分支恒假）。

### CD-①-C permissions 页角色列表端点语义错位（wrong-endpoint drift）
- UI：`permissions/page.tsx:49` `apiFetch('/api/permission/roles/all')`，消费 `data.roles`
- 真源：**该路径无注册路由**（live openapi 938 路由中只有 `/api/permission/roles/{username}`），
  FastAPI 把 `all` 当 username 落进 `list_roles`（permission.py:74）→
- **live 硬证据**（真 user token，scripts/e2e_cd1_roles_all_probe.py）：
  `GET /api/permission/roles/all` → 200 `{"username": "all", "roles": []}`；
  对照 `GET /api/permission/roles/<真实用户>` → 200 `{"username": …, "roles": ["user"]}`。
- 症状：角色列表恒空（"全部角色"被当成"用户 all 的角色"）；UI catch 回落 `/api/permission/policies`
  只在抛错时触发，200 不触发故回落失效。真源口径「全部角色」在 `/api/permission/policies` 的 `roles` 键。

### CD-①-D workspace 页目录浏览端点不存在（path drift）
- UI：`workspace/page.tsx:123` `apiFetch('/api/workspace/dir?path=…')`，消费 `data.entries`/`data.path`
- **live 硬证据**：`GET /api/workspace/dir` → **404**；真端点 `GET /api/dir?path=/tmp` → 200
  `{path, entries:[…]}`（workspace_api.py:58 `@router.get("/dir")` + main.py:729
  `include_router(…, prefix="/api")` = 挂载在 `/api/dir`）。
- 症状：workspace 页文件浏览恒失败（错误条），行内导航/文件查看整页不可用。

## 误报排除（2，人工复核后不成立）

- `users/page.tsx:38` `data.users`（drift_hard）：API 回裸数组（response_model=list[UserListItem]），
  UI 为 `Array.isArray(data) ? data : data.users || []` 防御双分支——数组分支真消费，
  `users` 只是回落访问，非断点。工具不识别三元防御分支=已知边界。
- `agents/page.tsx:57` `data.agents`（drift_suspect）：`agents.py:857 _detect_cache={"os","agents","total"}`
  经 `return {**_detect_cache, …}` spread 真源在场，键存在非漂移（spread=动态操作如实降级判定）。

## 待确认层（38，不猜不动）

- drift_suspect 其余 8：downloader tasks/files 元素键、system organs 元素键、
  trajectory search events 元素键 ×4——元素形状均走 `x.to_dict()` 接收者变量/跨模块 dict，
  静态不可定型（诚实边界，见下）。
- unresolved_shape 25：agent-proxy send、gland test、hermes sessions、limb windows、
  nest tenants、trajectory get_session 等——bare return 跨模块结果，形状不可知。
- unresolved_path 3：marketplace `apiFetch(endpoint)`、vision `apiFetch(url)` ×2——路径拼变量，
  端点无法静态映射。
- unresolved_endpoint 6 = CD-①-C/D 两组（已升真断点）。

## 诚实边界

1. 消费面提取是静态启发式（赋值切段+别名链）：漏收=漏报、误收进候选层由人工复核过滤；
   三元防御分支（`Array.isArray(data) ? …`）不识别=users 误报即此因。
2. `s.to_dict()` 接收者变量无法静态定型→形状不可知，禁硬判（测试
   `test_receiver_var_to_dict_is_honest_boundary` 锁死）。
3. drift_hard 须人工复核后才定罪（3 个 hard 中 1 个被排除）。
4. 语义错位类（wrong-endpoint：路径存在但语义不同）工具键层不可见，靠
   `missing_in_openapi`+人工复核浮出（CD-①-C 即此型）。
5. 本普查只覆盖 apiFetch 面（78 文件 582 调用点中带响应消费的 193 个赋值点）；
   裸 fetch/ws 消费面不在范围。
6. 修复=另拍（本拍零行为改动，审计即切片本体）。

## 下一步候选（待拍）

1. CD-① 修复件①：CD-①-A/B/C/D 四断点根因修（UI 消费面收编真源键/端点改指真端点，
   GLAND-⑤「收编真源键非 API 加别名堵漏」纪律）——按最小件可再拆。
2. 待确认层 38 项深挖（跨模块形状推断或逐页 live 探针）。
3. ㉔ 处置拍板（D→B/C/维持，可由 cron 代跑）/temp- 别名行取舍（需拍板）/用户实测反馈（无输入）。

## CD-③ 待确认层深挖（2026-10-18）——43 条逐项裁决（零行为改动，修复件另拍）

> 工具：`scripts/census_contract_drift_deep_dive.py`（裁决单一源+完整性门）
> 工件：`data/census_contract_drift_deep_dive.json`
> 测试：`tests/test_contract_drift_deep_dive.py` 19 例（覆盖完整性门+真源形状证据锁）

深挖口径：对 CD-② 修后工件（`data/census_contract_drift_post_fix.json`）的全部 43 条
非 produced findings 逐条裁决——静态不可定型的跨模块 bare return / `to_dict()` 接收者 /
for-loop 字面量，用**源码真源逐字读取+调用点归属复核**定型（禁推断当实证）。
每条裁决带 evidence（真源位置），`build_artifact` 完整性门=任一 finding 无裁决拒出工件。

**总账：43 条 = resolved_ok 33 + confirmed_drift 8（键级）+ ruled_false_positive 2；
confirmed_drift 归 4 个修复候选；工具归属漂移 4 条（挂错 endpoint，按真 endpoint 判定）。**

### 真断点（confirmed_drift 8 键 → 4 修复候选，一候选一拍）

| 候选 | 消费面 | 真源 | 症状 |
|---|---|---|---|
| ①dedup 元素键收编 | dedup/page.tsx DuplicatePair 接口平铺 8 键 | `get_duplicate_report` 元素=`{item_a:{id,title},item_b:{id,title},similarity}`（deduplication.py:130-136 字面量全解析） | 8 键仅 similarity 在场；title_a/title_b/id_a/id_b/content_type/created_at_a/created_at_b 7 键缺席（普查只抓到 3 键，深挖扩面补 5）→ 重复对卡片标题/ID/时间全 undefined、删除按钮 id 失效、过滤失效 |
| ②gland 页消费键收编 | gland/page.tsx `data.message`（:149）+ usage 面板 `data.input/output/requests`（:532/:538） | test_provider 返回 `{status,provider,model,reply}`（router.py:979-1012）；`by_provider=usage_by_provider()=dict[str,int]`（token_meter.py:38/:118-119 元素=int 非对象） | message 恒 undefined→`\|\|` 回落恒吃「连接成功」模型 reply 永不显示；usage 面板对 int 元素取 .input/.output/.requests 恒 undefined→各 Provider 用量恒显示 0 |
| ③traces events 端点改指真端点 | traces/page.tsx:252 `data.events \|\| []` | GET /api/trajectory/sessions/{id} 返回 `session.to_dict()`（store.py:76-104 无 events 键）；真源=GET /api/trajectory/sessions/{id}/events（trajectory.py:268-291 `{session_id,events,count}`） | 会话详情展开恒空事件列表（有事件也不显示） |
| ④limb windows 元素容器收编 | limb/page.tsx:130 `Array.isArray(data.windows)` | `list_windows() -> list[WindowInfo]` 裸数组（limb_rpa.py:678） | 对裸数组取 .windows 恒 undefined→恒回落 []，Windows 列表永不渲染（WindowInfo 元素字段与 UI 消费一致，仅容器形漂移） |

### resolved_ok 33 键（普查提取边界假阳，源码定型=真源键在场）

- agent-proxy send ×3（AgentResponse response_model 字段 response/success/error）
- dedup `duplicates.similarity`（同元素兄弟键漂移→候选①）
- downloader tasks ×2（DownloadTask.to_dict id/status）+ cache files.name（list_cache 字面量）
- hermes sessions/total ×2（_list_sessions_sync `{"sessions","total"}`）
- marketplace sources（endpoint 变量双值 /skills|agents/sources 均 `{"sources":…}`）
- nest tenants ×5（Tenant.to_dict name/tier/description/owner_user_id/tags）
- knowledge 元素 ×3（KnowledgeBase title/content/metadata，response_model 基类并入）
- system organs ×3 + diagnostics check-all ×2（元素字面量 key/enabled/status）
- trajectory search 元素 ×4（TrajectoryEvent asdict id/agent_id/event_type/status）
- vision ×2（api/vision.py 返回字面量 image_base64/format）
- workspace dir entries ×4（list_directory for-loop 字面量 name/path/type/size/modified/hidden——普查「for-loop 字面量不可静态定型」诚实边界由源码读取闭合）

### ruled_false_positive 2（CD-① 已排除随附证据不复挖）

users `data.users`（Array.isArray 三元防御真消费数组分支）/ agents `data.agents`（_detect_cache spread 真源在场）。

### 工具归属漂移（4 条，census 启发式已知边界新增形态）

消费键挂错 endpoint：gland input/output/requests 归 `/api/gland/test`（真消费在
`/api/gland/usage` by_provider 元素）、system `organs.status` 归 `/api/organs`（真消费在
`/api/diagnostics/check-all` organHealth）——赋值切段把元素消费挂到邻近 apiFetch。
按真 endpoint 判定（3 键落候选②、status=resolved_ok），note 入工件。

### 诚实边界

1. 裁决=源码静态读取+调用点归属复核，非逐页 live 探针（live 证据在 CD-①/② 既有探针）。
2. 普查消费面启发式漏收（dedup 平铺 8 键只抓 3）→扩面键随候选①登记，非普查定罪。
3. 本拍零行为改动；4 修复候选按「一候选一拍」拆件另拍（CD-①→CD-② 先例）。
4. evidence 行号以本拍 HEAD 源码为准，行号漂移不改判（形状由 tests 证据锁把守）。

## CD-④ 修复候选①——dedup 元素键收编（2026-10-18）

> 消费面：`admin-ui/app/dedup/page.tsx` DuplicatePair 平铺 8 键形
> 真源：`services/deduplication.py get_duplicate_report` 元素=`{item_a:{id,title},item_b:{id,title},similarity}`
> 测试：`tests/test_cd4_repair.py` 7 例；live e2e：`scripts/e2e_cd4_dedup_keys_ui.py`

收口（UI 消费面收编真源嵌套元素键，真源零改动=CD 系列 wire 权威纪律）：
- DuplicatePair 收编 `{item_a:{id,title}, item_b:{id,title}, similarity}`，标题/ID/
  删除按钮/搜索过滤/React key 全改嵌套访问——修前标题/ID 全空渲染、onRemove(undefined)
  失效（多 pair 下=整列表全清）、搜索过滤裸 toLowerCase 对 undefined 必炸（输入即页崩）。
- created_at_*/content_type（CD-③ 裁决「真源根本不产出」）→ 死显示面收口（时间行/
  formatTime/'unknown' 假徽章移除），恢复显示=真源补现候选 **pending-decisions ㉕**
  （knowledge 真列在场；涉证据锁「真源形状变了→①裁决失效须复挖」需拍板，不自决）。

验证：新测 7 + 控制组（HEAD 3be46b06 旧 UI）7 failed/0 passed（全行为断言旧代码全炸=
平铺形硬反证）+ live 双向 e2e：--pre 7/15（8 项修复断言全炸=修前缺口硬证：标题空/
'unknown' 徽章在场/展开 id 空白/过滤页崩/删除全清）→ --post 15/15（两 pair 判别：
删甲组对乙组对存活）+ 清痕 remaining=0。census 修后 findings：7 个平铺漂移键全部消失、
duplicates/total_pairs produced 照旧（similarity 仍 unresolved_shape=启发式不识嵌套元素
访问的既有边界，CD-③ 已裁 resolved_ok 由证据锁把守）。

诚实边界：①census 启发式对嵌套 `item_a.id` 形不产 findings（漏报面，CD-③ 诚实边界2
同型；tests/test_cd4_repair.py 消费⊆真源行为桥补位）②DedupResult 面（POST /deduplicate
响应）另有未裁决漂移——UI `merged` 键真源不产出、`details` 元素=dict 渲染必炸 React
child——CD-③ 普查未列，另拍复核（pending-decisions ㉕ 边界③）③搜索占位符「搜索标题、
ID、类型...」随 content_type 过滤收口改「搜索标题、ID...」。

## CD-⑤ 修复候选②——gland 页消费键收编（2026-10-18）

> 消费面：`admin-ui/app/gland/page.tsx` 四组消费键（测试回执/usage 顶层/by_provider
> 元素/recent records 元素 + keys 面板）
> 真源：`src/gland/router.py test_provider` `{status,provider,model,reply}`、
> `src/gland/token_meter.py summary()/recent_records()`、`src/gland/key_manager.py status()`
> 测试：`tests/test_cd5_repair.py` 15 例；live e2e：`scripts/e2e_cd5_gland_keys_ui.py`

收口（UI 消费面收编真源键，真源零改动=CD 系列 wire 权威纪律）：
- CD-③ 裁决两键组：①测试回执 `data.message`（真源无此键）→ 收编
  `{status,provider,model,reply}`（修前 || 回落恒吃「连接成功」模型 reply 永不显示=
  回落遮蔽型 drift）②by_provider 元素按 `{input,output,requests}` 对象消费 → 元素=int
  直接消费（修前三键恒 undefined，各 Provider 用量恒显示 0）。
- 深挖扩面（普查启发式漏收，如实记）：UsageSummary 顶层 4 键（total_input_tokens/
  total_output_tokens/total_requests/budget_used_pct）真源不产出（真源=total_tokens/
  call_count/budget_limit/remaining_budget/by_model/by_user/by_provider）——修前总请求数/
  总Token数恒 0、预算条恒 0%；UsageRecord 元素（input_tokens/output_tokens/task）对
  真源 recent_records 元素（prompt_tokens/completion_tokens/...）漂移 + timestamp 真源=
  epoch 秒被当毫秒解析落 1970；keys 面板 `keyData.keys||keyData.slots` 双回落对真源
  `dict[provider, 槽位列表]` 恒 []=密钥列表恒空（`k.active` 亦非真源键，真源=`is_valid`）。
- 死显示面收口（真源不产出禁编造，CD-④ 同款）：「入: x / 出: y」拆分行、by_provider
  入/出/请求拆分行、record task 列移除；预算使用率=真源键派生（total_tokens/
  budget_limit）。恢复显示=真源补现候选 **pending-decisions ㉖**（需拍板不自决）。

验证：新测 15（源码消费面 9+census 真跑真仓 2+行为桥真源实跑 4）+ 控制组（HEAD
9155ae6e 旧 UI）15 failed/0 passed（全行为断言旧代码全炸=修前硬反证）+ 相关回归 63
（cd5 15+cd4 7+cd1 9+census 13+deep-dive 19）+ 全量 non-a2a 5872 passed 0 failed
（记账闭环 5857+15=5872 精确吻合）+ test_a2a live 20（总账 5892 对平）+ live 双向 e2e：
--pre 8/18（10 项修复断言全炸=修前缺口硬证：请求数/Token 恒 0、入/出假拆分在场、
Provider 用量恒 0、密钥列表恒空、回执恒「连接成功」、时间落 1970、undefined 渲染）→
--post 19/19（真 LLM chat 真记账 call_count=2/total_tokens=65、by_provider `{openai:65}`
int 元素、真 masked_key 2 行、真回执 `openai: mimo-v2.6-pro → pong`、prompt→completion
真数值、时间=当日）+ 零持久写入（探针只进内存 token_meter）。

诚实边界：①census 修后 gland 残留 findings 4 条（status/provider/model/reply 标
unresolved_shape）=census 对变量返回 handler（test_connection `return result`，
dynamic=True 形状提取失败）的启发式盲区，行为桥（test_provider 真跑）证明真产出——
与 CD-④ 边界③同型；②入/出 token 拆分、by_provider 请求计数、record task 列=有意收口
非 bug（真源 summary/元素不产出），恢复=真源补现候选 ㉖；③keys 展平行的 fail_count/
last_checked 已收编进类型但未渲染（真源在场，按需显示另拍）；④HealthData.token_meter
类型随 UsageSummary 同步收编（真源 health 带 token_meter=summary() 同形）。

## CD-⑥ 修复候选③——traces events 端点改指真端点 + 同页消费键深挖扩面（2026-10-18）

> 测试：`tests/test_cd6_repair.py` 12 例（源码消费面 6 + census 真跑真仓 3 + 行为桥真 API 3）
> 探针：`scripts/e2e_cd6_traces_events_ui.py`（真浏览器 CDP + Network 域真请求观测）
> 部署：`scripts/deploy_cd6_admin.py`（dry-run 对账 + traces 专属 chunk 回读）

**候选③ 本体**：traces/page.tsx loadSessionDetail 的 `data.events` 打在
GET /api/trajectory/sessions/{id}（真源 `session.to_dict()` 无 events 键）→
会话详情展开恒空事件列表。收口=取数改指真端点 GET /api/trajectory/sessions/{id}/events
（真源 `{session_id,events,count}`，data.events 真源键在场）。

**深挖扩面（普查启发式漏收如实记，CD-⑤ 同款）**：census 只抓 apiFetch 调用点顶层
响应键，`setSessions(Array.isArray(data.sessions)?data.sessions:[])` 三元回落形
不进元素级消费链 → session 元素键漂移零 findings：
- session 元素 5 键全漂移（session_id/event_count/first_event/last_event/agents
  对真源 id/total_events/created_at/ended_at/agent_id）→ 列表行 React key 全
  undefined（React 对 undefined 渲染为空、不显字面 'undefined'——P7 'undefined'
  探测恒真探不到，逐键断言逼出）、事件数/时间全空、Agent 恒空、搜索过滤对
  undefined toLowerCase 必炸；
- event 元素 timestamp 漂移（真源 created_at）→ 事件时间恒空。

收口（UI 消费面收编真源键，真源零改动=CD 系列 wire 权威纪律，GLAND-⑤ 根因修
非 API 加别名堵漏）：接口收编真源形状+消费点全改真源键；agents 逗号串→真源
agent_id 单值（真源无多 agent 字段禁编造）；ended_at 可空走 ended_at||
created_at 回落（GLAND-⑤ 同款如实妥协）。

三重校验：新测 12 passed + 控制组（HEAD f5f7432e 旧 UI）11F/1P 精确命中（1
不变面=旧端点真无 events 键真源不变量）+ 相关回归 54（CD 系）+ 轨迹域 82 +
全量 non-a2a 5875 passed 0 failed（记账 5872+12=5884=5875+9 deselected 吻合）+
test_a2a live 20（总账 5904）+ live 双向 e2e --pre 13/21（8 修复断言全炸缺口硬证）
→ --post 21/21（Network 真端点恰 1 次+旧详情端点 0 次、展开 3/3 事件内容真渲染）。

诚实边界：①census 对元素级键零 findings=启发式盲区（三元回落 setter），行为桥
逐键对账补位（CD-④ 边界③同型）②census 工具 bug 2 处如实记（本拍以无注解形绕开，
工具修复另拍）：typed 赋值 `const data: T = await apiFetch(...)` 不进 _ASSIGN_RE
提取；_extract_first_arg 括号深度把模板串 `${{...}}` 的 call_end 截断在模板表达式
内→路径归一丢尾段（typed 形下 events 键被错挂 list_sessions）③agent 多值显示
收编单值 agent_id（会话级多 agent=真源补现另拍）④SessionDetail 接口保留作真源形
文档面（不作类型注解=census 提取契约）。

## CD-⑧ DedupResult 面漂移复核+收口——POST /deduplicate 响应面（2026-10-18）

> 复核件：CD-④ 诚实边界②留档「DedupResult 面（POST /deduplicate 响应）另有未裁决
> 漂移——UI `merged` 键真源不产出、`details` 元素=dict 渲染必炸 React child——CD-③
> 普查未列，另拍复核」——复核即切片本体（F4b-㉗ 先例），复核产出两处收口同拍落。
> 消费面：`admin-ui/app/dedup/page.tsx` DedupResult 接口+结果面板
> 真源：`src/services/deduplication.py deduplicate_knowledge` 返回字面量
> `{total, duplicates_found, duplicates_removed, removed_ids, details?}`，details
> 元素=`{id, title, duplicate_of, similarity}`（dict）
> 测试：`tests/test_cd8_repair.py` 12 例；live e2e：`scripts/e2e_cd8_dedup_result_ui.py`

复核定性（源码真源逐字，未裁决漂移坐实非推测）：修前 UI DedupResult 接口
`{duplicates_found, duplicates_removed, merged, details: string[]}`：
① `merged` 死显示键——真源根本不产出（去重只删不合并，无 merge 语义），「已合并」
   统计恒空白（CD-④「真源不产出禁假渲染」同型）；
② `details` 元素按 string 渲染 `{d}`——真源元素是 dict，React 渲染 dict child 必炸
   「Objects are not valid as a React child」=点「自动去重」出结果面板即整页崩
   （硬断点，比 CD-④ 恒空白重：统计卡尚可渲染、明细区一渲染就崩）。

收口（UI 消费面收编真源键，真源零改动=CD 系列 wire 权威纪律）：
① DedupResult 收编真源五键形（details 可选=早期返回分支（≤1 条）无该键）+
   DedupDetail 收编元素 dict 形；
② merged 死显示键收口→真源键 total（扫描条目）显示（CD-⑤「真源键派生显示」同款）；
③ details 元素按 dict 真源键渲染（title||id / duplicate_of / similarity 百分比），
   crash 形 `{d}` 清零。

三重校验：Gate1 git diff admin-ui 1 源文件 25+/5-+新测 12 例+部署脚本+e2e 探针+
构建产物 397 文件真落盘（chr(124) 字节编辑绕 patch 竖线坑，探针零星号损坏）②
Gate2 部署 dedup chunk（159qemgsga_ev.js）旧漂移串（`.merged`/「已合并」）残留=0+
新真源键（「扫描条目」/`.duplicate_of`/「重复，相似度」）真消费③Gate3 四层实证：
新测 12 passed+控制组（HEAD 24b30531 旧 UI）7F/5P 精确命中——7 行为/结构断言旧代码
全炸（含「UI 消费键端点不产出: merged」修前硬反证）、5 不变面（API 行为契约）新旧
皆过非自嗨+CD-④ 既有 7 零回归+全量 non-a2a pytest 5895 passed 0 failed（539s；
记账闭环 CD-⑦ 记档 5892 collected+本拍新测 12=5904=5895+9 deselected 精确吻合）+
test_a2a live 分跑 20 passed+live 9 passed（总账 5924=CD-⑦ 总账 5912+12 对平实测
collected）+live 真浏览器 e2e 19/19（S0 安全前置：全库
pre-existing 重复对=0 才准点「自动去重」——该按钮不带 user_id=全库去重，有他人重复
对在场即 REFUSE 禁误删他人数据→真 seed 1 pair→真点击流「自动去重→确认去重」→
结果面板真渲染：三卡真值 1 发现重复/1 已删除/2 扫描条目（修前第三卡 merged 恒
空白）、明细行真 dict 键「已删除 <title>（与 <id> 重复，相似度 100%）」、window
error 监听零 React child 崩溃、banner 仍在页未崩、'undefined'/「已合并」零出现、
API 回读真删 1 条、清痕 remaining=0）。

诚实边界：① --pre 双 bundle 浏览器 e2e 未跑（修前反证=控制组 7F+CD-④ 留档源码
定性；e2e 首轮一轮页面加载卡死假阴（P1 20s 超时全灭），带自诊断复跑 19/19——
暂定性环境 flake 如实记非代码问题）② 早期返回分支（≤1 条）无 details 键——UI 守卫
`dedupResult.details &&` 在场（测试锁死），未 fabricate 空数组③「已合并」显示恢复
=真源补现候选（真源本无 merge 语义——如需合并能力=功能新增另拍非键名对齐）④
details 只显示前 5 条（既有 slice(0,5) 语义不变）⑤ census 工具未列此面
（CD-③ 普查未列=启发式对响应整体直传 `setDedupResult(data)` 形不提取消费键，
工具 bug 修候选延续留档）。

## CD-⑨ census 工具提取精度收口（2026-10-18）

> 修复件：CD-⑥/⑦/⑧ 留档「census 工具 bug 修」4 件 + 深挖扩面 2 件（归属精度）。
> 零 UI/真源改动——只修普查工具本体（scripts/census_contract_drift.py）；
> 修复后真仓普查覆盖面 148→402 findings（旧工具大面积漏报）。
> 测试：tests/test_cd9_census_tool_fixes.py 23 例；真仓工件：
> data/census_contract_drift_pre_cd9_findings.json（修前）/ post_cd9.json（修后）。

修复面：
① typed 赋值（CD-⑥）：`const data: T = await apiFetch(...)` 不进 _ASSIGN_RE——
   typed callsite 整体不可见，同名变量切段失界把其消费错挂上一 callsite（留档
   「events 键被错挂 list_sessions」根因）→ 注解形进提取 + open_idx 取 match 尾
   括号（类型注解含括号不吞调用点）。
② 模板串截断（CD-⑥）：_extract_first_arg 括号深度不感知字面量——模板表达式内
   字符串的 ")" 截断 call_end 致路径丢尾段、双花括号对象形致杂散尾段 → 字面量
   感知（_mask_code + _literal_end + _template_expr_end）+ _normalize_path 模板
   表达式按花括号深度匹配（内嵌字面量不误判）。
③ 注释盲区（CD-⑦）：注释/字符串字面文本内「data.键」被误提取消费键 → 提取只看
   真代码（注释与字符串内部遮蔽为等长空白=偏移行号不变；模板表达式内是真代码
   不遮蔽，反向守卫锁死；key_lines 行号保真测试锁死）。
④ 整体直传（CD-⑧）：setX(data) 形零消费键 → 状态变量=整个响应对象别名（属性/
   解构/链式元素 `details.slice(0,5).map((d))` → (details,title) 全提取）。
⑤ 深挖扩面·可选链整类漏报：`data?.k` / `data.list?.map` / 别名 ?? 回落——访问符
   单字符吞不下可选链（_ALIAS_RE 旧 [?.] 1-2 字符还把 "??" 当访问符造假键路径）
   → 统一访问符常量 _ACC（`?.` 与 `.` 两分支）。
⑥ 深挖扩面·归属精度（新提取面逼出）：同名变量非 apiFetch 再赋值（`const data:
   T = await res.json()`）不断段=后续消费错挂上一 callsite（pipeline uploadResult
   形）；箭头形参遮蔽（`.then((data) => setX(data))`）建别名=形参当响应变量
   （workspace viewingFile 形）→ 切段边界泛化到任意 keyword 再赋值 + setter 直接
   跟在箭头后不建别名。

新浮出候选（登记不等于裁决，逐项裁决=下一拍深挖件，CD-③ 先例；候选集锁定进
tests/test_cd1_repair.py::test_census_counts_after_fix 契约基线，再变必须重审）：
- drift_hard 12（users 1 例=CD-① 已裁决误报之外）：feedback extract confidence/
  type、immune moderate findings.label / findings.risk、logs summary buffer_size/
  organ_probes / sse_clients、nest stats total_tenants / active_tenants /
  suspended_tenants / total_policies、voice synthesize/json error。抽验 nest/stats
  真漂移坐实：真源 GET /api/nest/stats 只出 tenants/isolation 两键，UI 读
  total_tenants 等 4 键恒回落显示（修复件另拍）。
- unresolved_endpoint 11：hippo GET /api/hippo/decay/simulate（1 键）+ soma POST
  /api/soma/discovery/scan（10 键）。live 实证双 405=UI 方法漂移真断点：GET
  decay/simulate 405（真源 @router.post，「模拟衰减」按钮必炸）、POST
  discovery/scan 405（真源只有 GET /scan，UI「Force rescan via POST」落 fallback
  GET 不重扫=假功能）——修复件另拍。

诚实边界：①提取仍是静态启发式，unresolved_shape 109 / unresolved_path 7 含
误收待裁决层（工具口径不变）②apiFetch 之外的取数（裸 fetch/axios/.then 链）
不在提取面（workspace viewingFile 等消费键随其 callsite 漏报=如实边界）③键名与
JS 内建成员同名（如 entries）被词表滤除=漏报（既有口径）④正则字面量按前一显著
字符启发式识别（return /x/ 形漏判按代码处理）⑤旧口径「unresolved_endpoint 归零
/ drift_hard 只剩已裁决误报」系旧工具漏报面假象，随契约更新如实改写。
