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
