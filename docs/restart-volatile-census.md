# 重启即丢状态面普查报告（RV-①，2026-10-21）

> 目的：MEM-④⑤⑥ 把记忆面（brain/hippo）持久化后，系统性盘点全仓还有哪些状态只活在
> 进程内存——service 重启即丢。本拍=审计即切片本体（G0/H0/CD/TR 先例）：零行为改动、
> 零删除、零生产写入，只落事实与定性，建设件按最小件排序待拍。
> 工件：`data/restart_volatile_census_20261021.json`（124 条发现，291 文件静态扫描）。
> 工具：`scripts/census_restart_volatile_state.py`（只读 AST 枚举）+ `tests/test_census_restart_volatile_state.py`（8 例）。

## 一、普查口径与总账

发现形态两类：`instance_registry`（类内 `self.<attr> = {}/[]/set()/defaultdict/deque`）
123 条 + `module_global`（模块级小写可变全局）1 条 = 124 条，去重键 (module, class, attr)。

每条附落盘/回载形态旁证（write_signals/load_signals，同类体内调用名命中即记证据行），
`auto_verdict` 粗判四档（只看信号在场、不判状态语义，报告定性为准）：

| auto_verdict | 条数 | 含义 |
|---|---|---|
| volatile | 82 | 类体内无任何落盘/回载形态调用（最大嫌疑=纯内存） |
| persisted_evidence | 27 | 落盘+回载信号均在场（大概率已持久化） |
| write_only | 12 | 只见落盘形态（写穿外部 store/委托） |
| load_only | 3 | 只见回载形态（写走委托对象或启动扫描） |

## 二、人工复核定性（本拍逐面查源码，file:line 以工件为准）

### A 组——活事实状态，重启即丢（建设候选本体）

| 器官 | 状态面 | 定性依据（本拍实查） |
|---|---|---|
| `gland/token_meter.py` TokenMeter | `_records/_by_model/_by_provider/_by_user` 用量记录 | 类体零落盘（record() 只 append）；**gland 用量 UI 真源**（DEC-㉖-①/㉖B2-① 建的显示面消费 recent_records）重启归零=用量面显示空 |
| `immune/access_control.py` IPAccessControl | `_blacklist/_whitelist` 封禁名单 | 类体零落盘零回载（blacklist_add/whitelist_add 只写内存）——**安全面**：运行时拉黑的 IP 重启即解封 |
| `gland/key_manager.py` KeyManager | `_slots/_indices` API 注册钥 | 仅 `load_from_env`（环境变量引导）；API add 注册的钥无落盘——重启后钥丢、provider 调用 401（env 钥随引导回种） |
| `a2a/task_manager.py` TaskManager | `tasks/push_configs/push_delivery_log/_stream_subs` | 纯内存（F4b-⑧ 已知边界「history 纯内存无持久化（重启即清）」同面）——重启后 a2a 任务/推送配置/投递日志全丢（tasks 含 history/status） |
| `echo/dispatcher.py` MessageDispatcher | `_history/_queue/_channels` | 纯内存；echo 子搜索读真源已收（DEC-⑦-②）但真源本身内存——重启后消息历史丢、echo /history /stats 归零 |
| `api/native_downloader.py` + `api/download_plugins.py` | `_tasks/_running` 下载任务注册表 | 纯内存（H1-②b-⑮ /api/download/results 面另存结果；进行中任务行重启丢） |
| `vein/chunked.py` ChunkedUploader | `_sessions` 上传会话行 | 会话行内存（分片字节在盘）——重启后 received/complete 状态丢、孤儿分片目录（㉕ 既有清理面） |
| `limb/executor.py` RPAExecutor | 任务/历史注册表 6 处 | 纯内存（㉒ 留档已述「纯内存面零持久化」同面）——RPA 任务/执行历史重启丢 |

### B 组——派生/可重建/运行时态（重启丢=无害或随启动重建，零建设）

> ⚠️ 2026-10-22 RV-①c 逐行深挖复核后本组拆分：**15 条藏活事实扩 A 组**（本组名下不再
> 「零建设」，见 §五）；余 47 条派生/可重建 + 7 条在途运行态维持零建设。

- 缓存族：`reflex/cache.py`、`vein/cache.py`、`soma_discovery._Cache`、`cortex/token_attribution` deque（观测缓冲）。
- 启动重建族：`heredity/migration._schemas`、`heredity/version_registry`、`api/download_plugins._plugins`（扫描重建）、`plugin_loader.loaded_plugins`（load_only=启动扫描）。
- 运行时守卫/节流族：`cortex/loop_guard`（重复检测窗口）、`immune/rate_limiter`、`immune/intrusion`、`immune/moderator`、`pulse/timer`、`vital/collector._*`、`vital/alert`。
- 连接/派生态：`nerve/client`、`api/nerve.EventBus`（活动流缓冲——timeline 持久双落已收 DEC-⑱，丢的只是 SSE 活动流尾巴）、`trajectory/session_fsm`、`intelligence/analyzer`、`intelligence/intent`、`mind/emotion`、`voice/tts_engine`、`vision/dag_visualizer`、`vision/mindmap`、`link/connector`、`will/dag_planner`、`nest/isolation`（运行时引擎态；审计日志已持久化 F3b-①）。

### C 组——已持久化（旁证在场，零动作）

persisted_evidence 27 条：`will/engine._executions/_workflows/_running_tasks`、`will/job_queue._jobs` 等（will_checkpoints/jobs 表，F1/F3b 系）、`nest/tenant._tenants`（F3b-① NestStore）、`marrow/backup._manifests/_schedules`（schedules.json）、`mind/personality._user`、`voice/voice_profiles._user`、`learn/course_engine._courses`（learn.db，A2）、`gene/templates._templates`、`gene/skill_learner`、`hippo/long_term_memory`、`cortex/project_memory`（H1-②b-⑩ save/load_cache）、`config_manager._config`、`benchmark/engine`、`mirror/sandbox`；
load_only 3 条含 `hippo/memory_store._memories`、`hippo/session._sessions`（MEM-④⑤ persist 写穿/懒回载——本普查正是其收口后的余量盘点）。

## 三、诚实边界

1. 静态枚举不追跨模块委托链——真源在独立 Store 类的只看到委托调用点；auto_verdict 是
   形态粗判，「重启真丢不丢」以本报告人工定性为准，建设件落地前须逐件 live 重启实证。
2. A/B 组划分=本拍人工复核 8 个器官的定性结论；B 组其余条目按形态归组未逐行深挖，
   若建设件复核发现 B 组藏活事实，按「复核即切片本体」先例扩 A 组（宁宽勿漏方向已留白）。
3. 重启行为本拍不实测（live 重启探针留建设件逐件拍）；数据面旁证=data/ 现存 26 个
   *.db（agent_collaboration/agent_registry/ai_groups/benchmark/brain_memory/cron_tasks/
   enterprise/evolution/hippo_ltm/hippo_memory/job_queue/knowledge/learn/long_term_memory/
   nest/opensoul/permission/proactive/semantic_cache/timeline/tool_failures/users/wakeup/
   wechat_bridge/wechat/will_checkpoints）与 C 组落盘面对应。
4. 装饰器/property 内赋值、per-call 局部容器不进候选（宁漏勿滥，见工具 docstring）。

## 四、建设候选待拍（按最小件排序，一候选一拍）

1. **RV-①b-① gland TokenMeter 用量记录持久化**——用量 UI（DEC-㉖/㉖B2-①）真源落盘
   （SQLite 单表或 jsonl，最小件：record() 写穿+启动回载，UI 面零改动）。
2. **RV-①b-② immune access_control 黑白名单持久化**——安全面运行时封禁重启不丢
   （最小件：名单落盘+启动回载，TTL 语义随行）。
3. **RV-①b-③ gland KeyManager API 注册钥持久化**——重启不丢钥（涉凭据落盘形态
   需拍板：明文 json vs 环境变量引导回归——候选中唯一涉凭据件）。
4. **RV-①b-④ echo dispatcher._history 持久化**——echo /history /stats 真源落盘。
5. **RV-①b-⑤ a2a TaskManager tasks/push_configs/push_delivery_log 持久化**（大件
   可再拆：任务行/推送配置/投递日志三面）。
6. **RV-①b-⑥ vein 上传会话行/下载任务面持久化**（含孤儿分片目录既有清理面联动）。
7. **RV-①b-⑦ limb RPA 任务/执行历史持久化**。

B 组~~零建设~~（2026-10-22 RV-①c 复核：15 条藏活事实扩 A 组 → 新增候选 8~15 见 §五；
余 54 条派生/在途零建设）；C 组零动作。㉚/㉔/㉙ 拍板件与用户实测反馈（无输入）不属本清单。

## 五、B 组逐行深挖复核（RV-①c，2026-10-22；审计即切片本体零行为改动）

> 依据 §三 诚实边界#2「B 组其余条目按形态归组未逐行深挖，若复核发现 B 组藏活事实，
> 按『复核即切片本体』先例扩 A 组」。判定表单一源=`scripts/review_restart_volatile_b_group.py`
> （类级粗判+逐条细判），工件 `data/restart_volatile_b_group_review_20261022.json`
> （124 条全覆盖：A_rv1b 24 / B_fact_hidden 15 / B_derived 47 / B_runtime 7 / C_persisted 31），
> 契约测试 `tests/test_rv1_b_group_review.py` 14 例（覆盖完备/前提标记锁/确定性）。

### 5.1 藏活事实 15 条（扩 A 组，按建设候选归组）

| 候选 | 状态面 | 重启丢失的事实（本拍实查定性） |
|---|---|---|
| RV-①b-⑧ | `immune/audit.py` AuditLogger._log | 安全审计留痕 ring（maxlen=10000）：permission_denied/intrusion 等安全动作史，api/immune.py 单例 3+ 写点真消费；与 a2a push_delivery_log（RV-①b-⑤-3）同族留痕。**（已落 2026-10-23：immune_audit_log 追加表写穿+首触懒回载+计数对账行聚合，前提标记锁已契约随更新）** |
| RV-①b-⑨ | `pulse/timer.py` PulseEngine._signals | 用户创建脉冲信号（POST /api/pulse/signals + by-ID/tick/pause/resume 契约）；bootstrap 种子信号也只在**首次** bootstrap 建（main.py `is_bootstrapped` 一次性）=重启后定时脉冲静默消失 |
| RV-①b-⑩ | `gland/router.py` ModelRouter.providers | API 注册 provider 配置（name/base_url/models/priority）——内置备份 provider 随构造重建、API 注册零重建；钥面=KeyManager（RV-①b-③ 拍板件）另件 |
| RV-①b-⑪ | `immune/intrusion.py` _threat_history/_login_attempts | 威胁事件史 + 失败登录时间窗（爆破检测证据）：auto-block 结果已持久化（RV-①b-②）但过程史/计数窗不落=诱导重启即清爆破计数 |
| RV-①b-⑫ | `will/dag_planner.py` DAGPlanner._plans | API 创建执行计划（api/will.py get_plan by-ID :588/:626 消费）——plan_id 契约跨重启 404 |
| RV-①b-⑬ | `healer/diagnose.py` OrganHealer._history | 修复史 ring（500，heal stats 聚合真源）=自愈审计断档 |
| RV-①b-⑭ | `heredity/version_registry.py`+`migration.py` 5 面 | API 注册组件版本/迁移计划（含 execute/rollback 状态）/changelog/注册 schema/迁移脚本（applied 状态）——**SEED 28 组件仅 import 期重建，API 注册与执行状态零重建**（§二「启动重建族」归组勘误） |
| RV-①b-⑮ | `link/connector.py` _connectors + `api/soma_connector.py` _components/_tokens | API 创建集成连接器（secret+callback_token DEC-⑲ 随行）+ 外部组件注册事实/token——重启丢注册+凭据，DEC-⑲ fail-closed 下外部 webhook 永久拒直到重注册重签。**涉凭据件：凭据随行落盘形态与 RV-①b-③ 同拍板面 → 登记 pending-decisions ㉛，拍板后代跑** |

同器官派生面显式拆出不随判（audit._counters 计数随留痕重建、intrusion._ip_suspicion/_stats/_request_counts 派生窗口、
pulse._tick_history 有界 ring、link._events 有界缓冲、healer._custom_handlers 代码注册重建、dag_planner._stats 计数）。

### 5.2 勘误（对 §二 归组，如实记）

1. §二「启动重建族：heredity/migration._schemas、heredity/version_registry」**误归组**——
   实查两引擎写点全在 API（POST /components /migrations /schemas），仅 version_registry 的
   SEED 28 组件是 import 期种子；API 注册面/迁移执行状态无任何重建路径（→5.1 RV-①b-⑭）。
2. `nest/tenant.TenantManager._tenants` auto_verdict=write_only（形态粗判）而 §二 C 组已按
   F3b-① NestStore 人工定性为已持久化——本复核以报告定性为准（RV-① 纪律），工件
   verdict=C_persisted 记录该覆盖。

### 5.3 余量维持 B（47 派生 + 7 在途，零建设）

缓存族（reflex/vein/soma_discovery._Cache）、检测窗口族（loop_guard/rate_limiter）、有界观测
（token_attribution ring 50/nerve EventBus 5000/soma_discovery change_log 500/vital collector）、
学习态（intelligence/analyzer 学习态冷启动如实）、计数/统计（mind/emotion、intent、gatekeeper、
audit._counters）、配置编译（moderator）、构造配置（RestAdapter._config）、语言词素常量
（graphrag）、对象图/布局（mindmap/dag_visualizer）、运行时策略覆盖（nest/isolation，F3b-① 记档
DEFAULT_POLICIES=代码真源）、启动扫描（download_plugins._plugins）、namespace 索引（nest 逆查轴，
随 _tenants 回载重建）；在途态 7（a2a 流发射器/nerve 订阅·流队列/tts 任务/trajectory 会话机/
nerve client 订阅/healer 自定义 handler）。

### 5.4 观察件（不属重启域，归增长域另查）

- `hippo/dream_distiller._dream_history`、`vital/alert._history`：append 无裁剪**疑似无界**
  （本拍只记观察不下判）——「无界增长面普查」（越跑越涨：无老化/无上限表与注册表）可作
  后续新域审计拍（RV-① 对偶域）。

### 5.5 诚实边界

1. 判定=源码静态实查（写点/重建路径/消费面 grep+sed 逐面），**未做 live 重启实证**——建设件
   落地前逐件 live 重启探针纪律延续（RV-① §三-3）。
2. 前提标记锁（tests 14 例）在源码形态变化时会破——**建设件落地须按契约随更新复核判定**
   （F4b-⑯f 先例：改判定先改契约测试）。
3. `_nodes`（api/nerve）判定 B_derived 但重建依赖**外部**组件重注册（heartbeat 对缺行
   fail-closed）非仓内重建——若外部组件不重注册则拓扑视图空，留观察不建候选（宁窄勿滥）。
4. 候选排序=按最小件粗判（⑧→⑮），同级可调序；⑮ 涉凭据严格随 ㉛ 拍板不自决（RV-①b-③ 同纪律）。
