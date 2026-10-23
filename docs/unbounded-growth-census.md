# 增长域普查（GR-①）——无界增长/无老化面审计报告

> 开域依据：RV-①c 观察件「dream_distiller._dream_history / vital / alert._history
> append 无裁剪疑似无界（归增长域=RV-① 对偶新域另查）」+ RV-①b 多件诚实边界
> 「表只增不减无老化面（retention 另拍）」同族；RV-①b「可续无需拍板」车道清空后
> 按 MEM-⑥「G0 先例」开新域定性。零行为改动拍（审计即切片本体）。
>
> 工件（判定单一源）：`scripts/census_unbounded_growth.py` →
> `data/unbounded_growth_census_20261023.json`；契约测试 `tests/test_census_unbounded_growth.py`。

## 一、口径（详见工具 docstring）

两轴静态枚举（291 文件）：

- **轴一 内存增长面**：类内 `self.<attr>`/模块级小写全局容器（Assign+AnnAssign 双形），
  按「增长算子（append/extend/insert/add/update/setdefault/setitem/iadd）vs 裁剪算子
  （pop/popleft/remove/clear/popitem/discard/delitem/reslice/filter 滤除重赋值）」对账。
- **轴二 DB 追加面**：全仓 SQL 字符串 `INSERT INTO` vs `DELETE FROM` 表级对账（跨模块聚合）。

verdict 单一源（classify_memory/classify_db 纯函数，未归类如实抛）：
`bounded_ring`（deque maxlen 直配）/`bounded_values`（dict 值有界、键面独立）/
`grow_only`/`grow_with_trim`/`trim_only`/`no_growth`；
`db_append_only`/`db_has_delete`/`db_delete_only`。

## 二、统计（工件回读，--check 两跑逐键相等）

- 内存面 126 条：grow_only **39**、grow_with_trim 62、no_growth 11、bounded_ring 9、
  bounded_values 3、trim_only 2。
- DB 面 89 张表：db_append_only **39**、db_has_delete 47、db_delete_only 3。

## 三、内存面人工定性（形态学粗判宁可多报，定性逐条复核为准）

### ① 事件流型·真无界面（每事件一行、零裁剪、计数派生耦合）——增长域核心缺陷面

| 面 | 事实 | 定性 |
|---|---|---|
| `hippo/dream_distiller.py` DreamDistiller.`_dream_history` | append×5，零裁剪（RV-①c 观察件）；`total_dreams=len(...)` 计数派生；展示 `recent_dreams=[-5:]` | 真无界；裁剪即破坏总计数→须计数真源化先行 |
| `vital/alert.py` AlertManager.`_history` | append@131，零裁剪（观察件） | 真无界（告警史每告警一行） |
| `gland/token_meter.py` TokenMeter.`_records` | append@83/143 每 LLM 调用一行；`call_count=len(_records)` 计数派生；`recent_records` 展示窗缺省 50；DB 真源已落（gland_usage，RV-①b-①） | 真无界且与自身 DB 真源不对称（DB 全量 load_all、内存永不清） |
| `a2a/task_manager.py` TaskManager.`push_delivery_log` | append@733/838 每投递一行；DB 真源已落（a2a_push_delivery_log，RV-①b-⑤-3） | 真无界（留痕语义但内存面无界+DB 面无删口） |
| `a2a/task_manager.py` TaskManager.`tasks`/`push_configs` | setdefault/setitem 每任务一行，零驱逐（A2A 无 delete-task RPC）；DB 真源已落（RV-①b-⑤-1/⑤-2）；live 实证 a2a_tasks 208 行持续增长 | 真无界（任务生命周期无终态驱逐） |

### ② 键基数型（界=键域基数，事件不入行）

`immune/intrusion.py` `_ip_suspicion`（每 IP 一键，IP 域无界=本组唯一中风险）、
`gland/token_meter.py` `_by_model`/`_by_user`、`intelligence/intent.py` `_stats`、
`trajectory/session_fsm.py` `_stats`、`will/dag_planner.py` `_stats`、
`mind/emotion.py` `_emotion_counts`、`limb/executor.py` `_tenant_counters`、
`immune/audit.py` `_counters`、`hippo/extractors/gatekeeper.py` `rejected_by_rule` 等。
——低速增长（键=租户/模型/规则数），IP 轴除外。`bounded_values` 3 条
（`_login_attempts`/`_request_counts`/`_metrics_history`）：值为 maxlen 有界 deque，
但键无界（每 IP/组件一键）——禁当「已裁剪」洗白，键面归 ⑥。

### ③ 注册面型（界=注册实体数，删除口在生命周期方法）

`heredity` `_components`/`_migrations`/`_changelog`/`_schemas`/`_scripts`（changelog 无删口
=低速史无界）、`will/dag_planner.py` `_plans`、`plugin_loader.py` `loaded_plugins`、
`healer` `_custom_handlers`、`immune/moderator` `_compiled`/`patterns`、
`will/job_queue` `_handlers`、`will/proactive` `_watchers`、`trajectory/session_fsm` `_machines`、
`marrow/migrator` `_jobs`、`vision/dag_visualizer` `_layouts`、`config_manager` `_config`、
`echo/dispatcher` `_channels` 等。——非事件流，量级=配置/注册实体数。

### ④ 有界/良构 + 判法盲区反例（grow_only 粗判误报，人工定性非缺陷）

- `bounded_ring` 9 + `grow_with_trim` 62：裁剪面在场（reslice 保尾/pop by id/clear 重置），
  窗口语义各异逐条良构；`nest access_log` 10,000 行 `_max_log` 裁剪=live 有界反例。
- **反例 1** `LongTermMemoryStore.last_context_memory_ids`（grow_only）：per-call 重置界
  （`self.last_context_memory_ids = []` 每次 build 重置）——口径#3「重置即裁剪不计」
  判法盲区如实呈形，非缺陷（前提锁 test_前提_重置不计裁剪口径锁死）。
- **反例 2** `SlidingWindowCounter._timestamps`（首版误判 grow_only）：`allow()` 内
  `self._timestamps = [t for t in self._timestamps if t > cutoff]` 滑窗滑动 + append 受
  `len < max_requests` 上限门=有界。**同拍收口判法**：trim 算子补「filter 滤除重赋值」形态
  （观察即缺陷即修），收口后判 grow_with_trim（前提锁 test_前提_判法收口面_filter滑动认得）。

## 四、DB 面定性（39 张 db_append_only=有 INSERT 无 DELETE）

by-id DELETE ≠ 老化（db_has_delete 47 张里多数也只有 by-id 删口）；本轴只答「有没有删口」。
live 单机真态快照（2026-10-23）：

| 表 | 行数 | 定性 |
|---|---|---|
| timeline.events | 29,079（11.4MB） | 事件史，无老化 |
| opensoul.trajectory_events | 22,684 | 轨迹事件史，无老化 |
| proactive_events | 4,152（12.4MB） | 事件史，无老化 |
| opensoul.agent_messages / experiences / tags | 3,766 / 2,518 / 2,466 | 增长面（experiences 有采集调度持续滚动） |
| enterprise.audit_log | 968 | 审计留痕（append-only by design） |
| job_queue.jobs | 381 行但 **21.6MB** | payload 重表，体积增长快于行数 |
| nest.access_log | 10,000 | **有界反例**（`_max_log` 裁剪） |

39 张无删口表按语义分两组（全清单见工件 db_entries）：
- **审计/留痕语义**（「不可抹除」=安全审计语义）：audit_log、decision_audit、
  perm_decision_audit、memory_audit、trajectory_v2_events、task_execution_events、
  discussion_messages、evolution_log/ledger/proposals/triggers/breaker、
  proactive_events、benchmark_runs、agent_reports、eval_* 系、pipeline_runs。
- **业务状态/消息面**（无删口=行只增不减，含孤儿行风险）：ai_group_tasks、users、
  user_roles、roles、permissions、perm_rules、perm_meta、relations、agent_scores、
  mcp_consumers、acp_agent_registry、gland_usage、learned_preferences、user_feedback、
  user_memory、memory_versions、kb_sharing_requests、agent_coord_messages。

## 五、建设候选 GR-①b（按最小件排序；一候选一拍）

| # | 切片 | 车道 | 依据 |
|---|---|---|---|
| ① | TokenMeter._records 内存窗+计数真源化 | **可续无需拍板** | DB=全量真源（usage_persist.load_all），读面回落 DB 后内存窗=纯缓存尺寸（零可观测变化=零新阈值）；`call_count` 恒对账 gland_usage 行聚合（MEM-⑥）；API 键形零变化 |
| ② | a2a push_delivery_log 内存窗+计数真源化 | **可续无需拍板** | 同款：DB=全量真源（delivery_log_persist.load_all），读面回落 DB 后内存窗=纯缓存尺寸，零新阈值 |
| ③ | a2a tasks/push_configs 终态驱逐+按需回查 | 待拍板（A 终态保留 N 条 / B TTL / C 维持无界） | A2A GetTask 保留语义+驱逐阈值=产品语义 |
| ④ | DreamDistiller._dream_history 裁窗+计数真源化 | 待拍板（total_dreams=历史总量 vs 窗内量+窗阈值） | 计数派生耦合，语义取舍=产品语义 |
| ⑤ | AlertManager._history 裁窗 | 待拍板（告警史保留阈值） | 阈值=产品口味 |
| ⑥ | 键基数面老化（_ip_suspicion per-IP 优先） | 待拍板（TTL） | IP 域无界=中风险；TTL=阈值 |
| ⑦ | DB retention 统一拍板（39 张无删口表） | 待拍板（审计「不可抹除」vs 生长治理；A 分级保留 B 归档 C 不动） | 涉审计可删性=治理语义，禁自决 |

③~⑦ 阈值/语义涉产品口味（用户记忆「阈值留拍板」），不自决；①② DB 全量真源已在场、
内存改窗+读面回落 DB=零可观测变化（窗口尺寸是纯缓存内部量），可续无需拍板。

## 六、诚实边界

1. 形态学粗判宁可多报：委托链裁剪不追（裁剪在别的类=如实判 grow_only）；
   滤除只认「filter 推导式/reslice」两收口形态（复杂表达式裁剪不认）；
   `len < max` 上限门 append 不认=可能误报（④反例 2 即此类，人工定性复核兜底）。
2. 「重置即裁剪」不计（口径#3，防 __init__ 初始赋值误判）——反例 1 如实呈形。
3. SQL 判定只认字符串常量 INSERT INTO/DELETE FROM 字面（动态拼 SQL 不认）；
   INSERT OR REPLACE 计 INSERT 不算删口。
4. 本拍零行为改动（纯审计）；建设件逐件 live 探针纪律延续（RV-①b 先例）。
5. live 数据=单机真态一个时点快照，行数随运行继续增长；内存键数与 DB 行数两域不对账。
6. 前提标记锁（观察件 grow_only/判法收口/重置口径/事件流候选/DB 无删口面）随建设件
   落地须契约随更新复核判定（F4b-⑯f 先例）。
