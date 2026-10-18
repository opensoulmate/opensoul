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
