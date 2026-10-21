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

B 组零建设（派生可重建）；C 组零动作。㉚/㉔/㉙ 拍板件与用户实测反馈（无输入）不属本清单。
