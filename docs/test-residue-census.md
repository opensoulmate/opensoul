# TL-③ 历史测试残留清点（审计拍，零删除零写入）

日期：2026-10-13（周期日志同日）｜工具：`scripts/census_test_residue.py`（只读，`file:...?mode=ro`）
工件：`data/test_residue_census_20261013.json`（data/ 为 gitignore 本地工件，含 A/B 类全部 event_id 清单）

## 为什么是清点不是清理

TL-①/TL-② 留档件「历史测试残行清点」标注"需拍板"——**盲删风险**（timeline 混有 healer/nerve 真实后台事件）。
按 F2/F3/F4/HEAL-②「审计即切片本体」先例：清点=决策材料件先落，**删除动作一律留待用户拍板**，本拍对生产数据面零写入（全部连接只读 URI）。

## 分类词表（单一源=工具 classify_timeline 纯函数，禁手抄）

- **A=确证测试残留**：对象级硬标识（test_sandbox / var_test / api-* / test.txt / dup1.txt / Test Node / test-push-node / test_schedule / test_backup_integration / Test Message / test_connector / 192.168.99.99 / test_dangerous_tool / testclient / stub-agent / e2e / sentinel 等 fixture 名与探针名）
- **B=疑似测试残留**：形态像测试但无硬标识（单日突发风暴、fixture 通用文件名 readme.md/tagged.txt、探针式 goal 文案），**需人工裁决禁自动删**
- **U=未归类**：默认按真实事件对待，一律不动

## 清点结果

### 1. timeline.db events（38,027 行 = A 8,612 + B 23,128 + U 6,287，三分对平）

A 类按面（去重前逐规则）：

| 规则 | 行数 | 面 |
|---|---|---|
| sandbox_fixture_names | 1,664 | mirror created/exec/fork（test_sandbox/var_test/api-*/live-*） |
| brain_stub_agent | 1,138 | brain verify detail `"source":"stub-agent"`（测试桩 agent 硬标识） |
| soma_fixture_components | 1,481 | Test Node / HB Test / Push Test / test-push-node / test-delete-me |
| file_fixture_names | 2,662 | vein 1,816 + pipeline 846：test.txt/dup1.txt/dup2.txt/test.png/test-promote.txt/timeline-test.txt |
| immune_fixture | 497 | 192.168.99.99 黑名单 275 + test_dangerous_tool 拦截 104 + llm/test/testclient 118 |
| echo_link_fixture | 540 | echo Test Message 274 + link test_connector 266 |
| marrow_fixture | 527 | test_schedule 263 + test_backup_integration 264 |
| brain_probe_goals | 97 | A1认知层接线live验证 / 逐块推送验证目标 / 口算17加25 等 e2e goal |
| timeline_api_probe | 6 | Test event from timeline API + pipeline e2e 标记 |

B 类：nerve_publish_rate_storm **22,030**（全部落在 **2026-08-18 单日**，`rate_anomaly` 127.0.0.1 POST /api/nerve/publish 突发——是真实拦截记录但极可能为当日压测/探针风暴，删=丢当日安全审计史，**建议保留或整体归档**）+ mirror sandbox_exec 无硬标识命令行 666 + pipeline readme.md/tagged.txt 421 + brain 探针式 goal 11。

U 类主分布（不动）：brain/think 1,905（含自动开发 cron 真事件——**brain 面是真实生产活动主产地，盲删即删真史**）、immune request_blocked 1,373（100.76.232.37 等真实外部 IP 安全事件）、cortex graphrag_build 495、healer 诊断/自愈 443（生产 cron 真探针）、pipeline 409、soma 245、system 4。

### 2. 其余数据面

| 面 | A（确证） | B/U | 说明 |
|---|---|---|---|
| opensoul.db users | 4（test/admin2/autobot/uiprobe） | 真 3（admin/default/hermes） | 测试用户可删 |
| opensoul.db knowledge | 391（`[Pipeline] test.txt` 等 fixture 标题） | 真 ~395 | 删需级联 knowledge_chunks/knowledge_tags |
| opensoul.db metacognition_log | 0 | B 1（goal=fix_bug 探针行） | 单行 |
| nest.db tenants | 2（e2e-t8c-a/b） | 真 1（default） | e2e 漏网租户 |
| enterprise.db users | 2（test/admin2） | 真 2（admin/hermes） | 与 opensoul users 同根 |
| ai_groups.db | 组 2（A群组/B群组=探针组）+任务 A 10 | 任务 B 16 / U 40（子任务随父，真伪混） | 消息/事件 70+984 行随任务 |
| permission.db 决策审计 | 612（t1/t2/api-test 567 + p0x-* 探针 35 + py-exfil-probe 7 + e2e 3） | U 1,058（空 session 1,042 真伪混）/ 真 om-* 1,304 | |
| permission.db **活跃规则** | **105 条 test_dangerous_tool + 2 条 p09-probe** | user/session 其余 41 条待辨 | **测试残留在生产权限引擎里仍生效**——本清点最重要发现 |
| job_queue jobs | 0 | B 2（idempotency 含 test） | 其余 194=真实 hippo.dream 等 |
| manifests.json | 5 条悬挂测试条目（test-sense-backup + 4×test_backup_integration，tar 已不在盘） | 真 55（Daily Data Backup） | |
| ~/.opensoul/exports | 0 | B 308（export_export_*.json 齐整同构） | DataMigrator 测试导出 |
| ~/.opensoul/sandboxes | 0 | U 4（随机 id 目录；TL-⑭ 内容级清点：3 空目录 + 1×155B 演示快照，零用户数据） | 内容与出处证据见「sandboxes 内容级清点（TL-⑭）」 |
| echo/mirror templates.db | 0 | 0（tpl-*/stpl-* 均为内置种子内容非残留） | |

### 3. 残留增长闸门（隔离是否真稳）

TL-②-⑥ 收口时刻（2026-10-13 20:30 CST）之后新增 **A 类行 = 0**：pytest 写方全收编后测试残留不再进入生产 timeline（后续周期 live e2e 探针行亦已各自清痕）。闸门指标入工具（`after_tl2_6_cutoff_A`），后续周期可复跑盯回归。

## 独立复算交叉核对（防工具自嗨）

与工具输出逐值对平：stub-agent verify 1,138 ✓ / 192.168.99.99 275 ✓ / nerve 风暴 22,030 ✓ / `[Pipeline] test` 391 ✓ / perm t1+t2+api-test 567 ✓ / A+B+U=38,027=total ✓。

## sandboxes 内容级清点（TL-⑭，2026-10-14）

TL-③ 留档「随机 id 目录无法从名字判」的最后一面，本节以**内容级+出处级**证据补齐决策材料（只读，未删除）：

| 目录 | mtime | 内容 | timeline 引用 | 出处（git 时间戳比对） |
|---|---|---|---|---|
| sandbox-3f79b4e6 | 2026-08-14 10:33:31 | snap-8354c13d.json 155B（name=initial, variables={env:staging}, log_length=1） | 0 | commit `3e51e462`「feat(mirror): OpenMirror 镜像系统 - 沙箱测试环境(创建/销毁/快照/变量/日志)」2026-08-14 10:33:38（提交前 7 秒）——该 commit 的开发验证跑，快照/变量功能验证产物 |
| sandbox-36ab8f8a | 2026-08-15 06:32:27 | 空 | 0 | 2026-08-14~17 开发窗口（该窗口 131 个 commit）；早于 timeline 采集起点 2026-08-17 16:09，零引用是采集前史不是删除痕 |
| sandbox-2ce51cab | 2026-08-16 23:52:43 | 空 | 0 | 同上 |
| sandbox-bcf4e66d | 2026-08-18 06:48:40 | 空 | 1（evt_1787006920738 sandbox_from_template） | commit `1dacb9df`「feat: Echo message templates + Mirror sandbox templates」2026-08-18 06:48:49（提交前 9 秒）——内置模板种子（06:48:29 落 stpl-*）后从 stpl-agent-debug 实例化的功能验证产物 |

**定性**：4 目录全部为**功能开发期验证产物**（目录 mtime 与对应 feature commit 提交时刻精确吻合到秒级），零用户数据（3 空目录 + 1×155B 演示快照，独立复算 find/du 4 目录/1 文件/155B 与工具逐值对平）；名字无硬标识按纪律仍判 U/other（classify_sandbox 宁窄勿滥，内容证据只作决策材料不改判）。**建议处置：删除（零数据损失，删前 tar+sha256 备份可整体回滚）——待用户拍板**。

## 建议处置（**全部待用户拍板，本拍未执行任何删除**）


> 执行记录（2026-10-14，TL-④ 删除执行件①）：第 1 项中 **timeline A 类 8,612 行已按清单精确删**（删前逐行复核单一源判 A、备份 data/backups/timeline_pre_delete_20261014_002822.db（38,027 行全库快照，sha256 见报告）、回读 29,415=38,027−8,612 对平且 B/U 一行不动，报告 data/delete_timeline_test_residue_20261014.json）；其余（knowledge 391 级联/测试用户 4+2/e2e 租户 2/perm 审计 612/manifests 5/探针组 2）仍未执行。
>
> 执行记录（2026-10-14，TL-⑤ perm 测试规则失效化）：第 3 项已执行——perm_rules 105 条 test_dangerous_tool 活跃残留规则全部软删（is_deleted=1 可逆非删除；备份 data/backups/perm_rules_pre_deactivate_20261014_010136.db sha256 7b54a5d52df8f6e3a994c376ee55916872d06514b99e35afac016dde0e5166f6、回读活跃 A=0/非 A 43 条零误伤/105 行仍在可回滚，报告 data/deactivate_test_perm_rules_20261014.json）。第 1 项其余（knowledge 391 级联/测试用户 4+2/e2e 租户 2/perm 审计 612/manifests 5/探针组 2）与第 2 项 B 类仍未执行。
>
> 勘误（2026-10-14，TL-⑤ 复核）：上文「permission.db **活跃规则** 105 条 test_dangerous_tool + 2 条 p09-probe」多计 2 条——p09-probe 两行在清点时已 is_deleted=1（大概率其探针自带 delete_rule 清痕；清点工具恒只数活跃行，工件 rules_A=105 全为 test_dangerous_tool 为准）；该 2 行现仍软删在场（include_deleted 可回读）。第 3 项实际待失效化=105 条，TL-⑤ 已全数收口。
>
> 执行记录（2026-10-14，TL-⑥ 删除执行件②a）：第 1 项中 **knowledge A 类 391 行已级联精确删**（判法单一源 census.classify_knowledge、删前逐行复核、备份 data/backups/opensoul_pre_delete_20261014_013035.db（786 行全库快照 8,507,392B，sha256 62e4d63a3dc35ca2…）、回读：knowledge 786→395=786−391 对平、chunks 734→367、knowledge_tags 2,354→1,181、A 剩 0、非 A 395 含 B 一行不动、orphan 0→0，报告 data/delete_knowledge_test_residue_20261014.json）；外部索引随删（Meilisearch delete-batch，索引 786→395 与 DB 删除逐值吻合；qdrant 零点数=A 行 chunks embedding_id 全空清点实证）。诚实记录：执行时工具的 meili task 轮询自校验步漏带 Authorization 头回 401（报告如实记 ok=False；delete-batch 本体已生效），当拍修复（轮询补头+测试锁死）并对生产重验 ok=True task_status=succeeded。第 1 项其余（测试用户 4+2/e2e 租户 2/perm 审计 612/manifests 5/探针组 2）与第 2 项 B 类仍未执行。
>
> 执行记录（2026-10-14，TL-⑦ 删除执行件②b-1）：第 1 项中 **e2e 租户 2 已按清单精确删**（判法单一源 census.classify_tenant、删前逐行复核+清单名一致、引用面拒删闸（data/*.db 结构化租户引用扫描：身份/绑定类引用=拒删禁悬空绑定、access_log 纯审计表只报告；pre/post 均 0 行）、备份 data/backups/nest_pre_delete_20261014_020426.db（3 行全库快照 860,160B，sha256 a9e7d5e4382b832bfa4ac04ce793bac1f03fa048653fbad44694c2f5cdfb398d 可整体回滚）、回读：tenants 3→1=3−2 对平、A 剩 0、default 一行不动、独立复算引用残留 0，报告 data/delete_nest_tenant_residue_20261014.json；live :8090 重启后 /api/nest/tenants 只剩 default、deleted tenant 404、nest stats total_tenants=1=管理面与 DB 一致）。第 1 项其余（测试用户 4+2/perm 审计 612/manifests 5/探针组 2）与第 2 项 B 类仍未执行。
>
> 执行记录（2026-10-14，TL-⑧ 删除执行件②b-2a）：第 1 项中 **manifests 悬挂测试条目 5 条已按清单精确删**（判法单一源 census.classify_manifest、删前逐行复核+清单名一致+tar 不在盘才删（tar 在盘=仍可用备份拒删禁制造孤儿）、清单外新残留单列不删、写回前 fresh 重读防覆盖生产并发新条目+原子写（临时文件+replace）、备份 data/backups/manifests_pre_delete_20261014_022506.json（60 条全量字节快照 24,755B，sha256 8bda034b8c064dda0f22ba0bc1bb5fd100c4774bf53e3fb898df7a82605da291 可整体回滚）、回读：manifests 60→55=60−5 对平、A 剩 0、非目标 55 条逐条内容指纹不变、独立复算 A 残留 0，报告 data/delete_manifest_test_residue_report.json；live :8090 重启后 /api/marrow/backups 55 条零 A 名、5 个 deleted id 全不可见、55 条 Daily Data Backup 真备份一条不动）。第 1 项其余（测试用户 4+2/perm 审计 612/探针组 2）与第 2 项 B 类仍未执行。
> 补记（2026-10-14）：TL-⑨（perm 决策审计 A 类 717 行删除）、TL-⑩（测试用户 4+2 删除）已按同款纪律执行完毕（详见 dev-roadmap.md 周期日志；此处从 TL-⑧ 后补记，权威=roadmap）。
>
> 执行记录（2026-10-14，TL-⑪ 删除执行件②b-2b-2b）：第 1 项中 **探针组 A群组/B群组 已按清单精确删+delete_group 级联**（判法单一源 census.classify_group、删前逐行复核+组名一致、级联=src delete_group 同款 (group_id, 组行租户) 双键 6 子表+组行、他租户外来行只报数不删、备份 data/backups/ai_groups_pre_delete_20261014_040240.db（471,040B 全库快照，sha256 b34306ab73d3ab23… 可整体回滚）、回读：组 2→0、tasks 66→0、messages 70→37、events 984→694、scores 37→12、caps 20→13、agents 1→0，删除 424 行算术对平、悬挂行一行不动，报告 data/delete_ai_group_probe_residue_20261014.json；live :8090 /api/ai-groups 返回 []、health 200）。勘误如实记：TL-③「消息/事件 70+984 行随任务」不完整——其中 37 消息+694 事件+12 评分+13 能力共 756 行是组行已不在场的**悬挂残留**（含 test-group-123/test-broadcast-*/a1proof 硬标识与 8 个已删探针组 id、2 行 tenant-*/e2e 租户消息），不在清单且无组行可判，本拍只报数不删（禁猜），留作下一切片候选。

1. **低风险可删（A 类）**：timeline 8,612 行、knowledge 391 行（级联 chunks/tags）、测试用户 4+2、e2e 租户 2、perm 决策审计 612 行、manifests 悬挂 5 条、探针组 A群组/B群组（delete_group 级联）。删前工具已留 event_id 全清单，可按清单精确删+备份。
2. **须拍板取舍（B 类）**：nerve 风暴 22,030（删=丢 2026-08-18 安全日志史；建议保留）、mirror exec 666、pipeline 421、exports 308。
3. **修生产行为（非删除）**：permission.db 105 条 test_dangerous_tool 活跃规则 + 2 条 p09-probe——测试残留规则仍在权限引擎生效，建议失效化（is_deleted=1），同样待拍板。
4. **不动（U/真）**：brain/think 真事件、healer/cortex 生产探针、真实安全事件、Daily backups、om-* 会话、内置模板。

## 诚实边界

① 分类是字面规则非语义理解：A 类零假阳设计（对象级硬标识），代价是部分真伪混合面（brain verify 无 source 键 992 行、perm 空 session 1,042 行）如实落 U 不猜。② knowledge 391 行删除需级联 chunks/tags/knowledge_tags 三面，工具只清点主表。③ ai_groups 任务 U 40 行真伪混（子任务 goal 不带父标记），随组处置比逐行删稳。④ 复跑稳定性：两轮清点逐键一致，唯一差异=perm 决策审计真实 om-* 会话 1,304→1,306（生产实时活动持续写入=活库常态，非工具抖动；timeline 面两轮零变化）。⑤ 本拍零 src 行为改动；删除脚本（按清单精确删+备份+回读）待拍板后另拍。

## B 类内容级+出处级清点（TL-⑯，2026-10-14）

TL-③ 留档「B=疑似（无硬标识需人工裁决）」的 23,128 行，本节以**内容级+出处级**证据补齐决策材料（只读零删除；判法零改动——classify_timeline 的 B 归类语义逐字不变，工件仅新增 timeline.b_class_detail 加性键，data/test_residue_census_20261014_post_tl16.json）。逐组证据（工具真跑+独立复算 raw SQL 逐值对平：22,030 / 666 / 421 / 11 全对平）：

1. **nerve_publish_rate_storm 22,030 行**——时间窗 2026-08-18 20:49:00–21:06:15（17 分钟 6 个分钟桶，峰值 5,339 行/分钟），来源 IP 恰一个 127.0.0.1 全量，威胁类型 rate_anomaly 22,029 + sql_injection 1，摘要仅 1 种。出处级证据（git 时间戳比对，TL-⑭ 同法）：窗口起点恰为 commit 630bbe11「feat: OpenImmune intrusion detection — SQL injection, XSS, path traversal, command injection, SSRF, brute force detection with auto-blocking middleware」2026-08-18 20:49 提交时刻，窗口终点恰为 commit 3842c3c1「fix: whitelist localhost from intrusion detection — frontend and cron jobs were blocked by bot UA pattern matching」2026-08-18 21:06 提交时刻（秒级吻合，TL-⑭ mtime↔commit 同型证据）。**定性修正 TL-③「疑似当日压测」：非压测，是 OpenImmune 入侵检测功能的开发验证流量**（本机自打 /api/nerve/publish 触发限流/注入检测），且 21:06 的 localhost 白名单修复正是被该风暴逼出——这 22,030 行=功能首跑记录+修复依据的证据链本身。处置建议：**保留**（安全检测功能史，删除无数据损失但丢功能首跑史料）——待拍板。
2. **sandbox_exec_cmd_no_hard_marker 666 行**——2026-09-28–2026-10-13 全落 TL-②-⑥ 隔离收口前，490 个 sandbox id 只跑归一命令 10 种（摘要形态 12 种；生产 summary 有长度截断，两条 urllib 探针截断后同形合并计 324）：cat /workspace/state.txt 164、test -d /home 逃逸探针 162、urllib 网络策略探针 324+4、CANARY 环境泄漏探针 4、sleep 30 超时探针 2、路径穿越 /workspace/../ 2、超长输出截断探针 2、组合探针 2。**全部是沙箱安全验证探针**（逃逸/网络策略/环境泄漏/超时/截断/路径穿越六型），零真实工作命令。出处级证据：echo VISIBLE（tests/test_sandbox_isolation.py:120/:284）、print('x'*200000)（:167）、sleep 30（:158）、state.txt（:225/:232/:237）逐字钉进测试夹具文件；CANARY=/HTTP_STATUS=/300000/does-not-matter 四组当前仓内无出处（历次开发周期一次性 e2e 探针或已删测试代码，如实记）。处置建议：**可删**（备份+回滚，零真实数据）——待拍板。
3. **pipeline_fixture_generic 421 行**——readme.md 210 / tagged.txt 211（75 种摘要=耗时变体），2026-08-21–2026-10-13 全落收口前，峰值日=开发高频日（10-12 91 行、10-13 82 行）。出处级证据：两文件名逐字=tests/test_pipeline_api.py:47/:73 上传夹具名（夹具内容 "# Test markdown content for auto-detection"/"Content with tags"），live 跑 test_pipeline 系列每次留一对。残余风险如实记：readme.md 是通用名，真实用户上传同名文件无法与夹具区分（421 行×恰 2 文件名×跨 50 天重复=夹具形态显著）。处置建议：**可删**（备份+回滚）——待拍板。
4. **brain_probe_style_goals 11 行**——2026-10-12 单日 7 种摘要：「请分三句话介绍你自己…」×6（会话探针）+「清空生产数据库所有表并格式化备份盘」×5（F4b-⑳ 确认门禁 e2e 破坏性意图探针，同文案 tests/test_ai_group_confirm_gate.py:497）。出处级证据：探针文案与测试/周期 e2e 探针逐字同形。处置建议：**可删**（备份+回滚）——待拍板。

**B 类增长闸门**：after_tl2_6_cutoff_B=0（TL-②-⑥ 收口 2026-10-13 20:30 后 B 类新增 0，与 after_tl2_6_cutoff_A=0 同证隔离真稳；B 组最新行 10-13 17:40 全部早于收口）。

**发现留档（knowledge 面，非本拍对象）**：census 新增 opensoul_knowledge.other_titles 加性键后浮出——当前 395 行 knowledge 里 [Pipeline] data.json 194 + [Pipeline] notes.md 194 + [Pipeline] pipe_test.txt 2 共 390 行文件名逐字=tests/test_pipeline.py:95/:105 上传夹具名（另有 反馈层测试 4、test 1），但 classify_knowledge 词表当前判 other（tests/test_knowledge_residue_delete.py:158 锁死「notes.md=other 不动」契约）=知识面同型疑似夹具残留被词表挡在 A/B 之外。词表扩充与否需拍板（动既有契约测试），本拍零改动。

**判法不一致留档**：classify_timeline 的 brain_probe_style_goals 把「清空生产数据库」判 B，而同文案在 goal/悬挂行词表 A_GOAL_MARKERS 判 A（同为 F4b-⑳ e2e 探针）——同一探针文案两面判法不一致（timeline 面当初刻意保守）。是否升 A 需拍板（改判法=改未来清单口径），本拍零改动。

**诚实边界**：①内容级证据=字面/形态事实非语义理解（探针判定依据=命令形状+夹具出处，非理解用途）②exec 命令清单受生产 summary 长度截断影响（截断后同形命令合并计数，如实记）③storm 定性修正 TL-③「疑似压测」为「功能开发验证」（git 秒级吻合证据），处置建议不变（保留）④全部建议待拍板，本拍零删除零判法改动。

## knowledge 夹具词表扩充（TL-⑱，2026-10-14）

**对象**：TL-⑯ 发现留档——[Pipeline] data.json 194 + notes.md 194 + pipe_test.txt 2 共 390 行
文件名逐字=tests/test_pipeline.py:95/:105 上传夹具名，但 classify_knowledge 词表判 other。

**内容级证据（本拍新增，出处钉死）**：生产 388 行（data.json 194+notes.md 194）内容与
tests/test_pipeline.py 上传夹具**逐字节相同**——data.json 全 194 行=`{"key": "value",
"nested": {"a": 1}}`（:93 `json.dumps({"key": "value", "nested": {"a": 1}})` 产出）、
notes.md 全 194 行=`# Title\n\nSome content here with enough text for processing.`
（:104 字节字面）——标题+内容双逐字钉=对象级硬标识（TL-⑯ 处置 pipeline B 组的同级证据）。

**判法扩充（单一源 A_KNOWLEDGE_FIXTURE_CONTENT，宁窄勿滥）**：仅「标题=[Pipeline] 夹具文件名
**且**内容与夹具逐字节相同」判 A；同名真实用户上传（内容不同）不动；不带 content 的旧调用面
语义逐字不变（tests/test_knowledge_residue_delete.py:158 锁死的「notes.md=other」契约仍在，
无需动契约测试）；近似内容（尾空白/换行/截断）不算命中。删除工具 scan/recheck/census_sqlite
三消费面随接线传 content（写了≠接线了）。

**census 复跑（data/test_residue_census_20261014_post_tl18.json）**：opensoul_knowledge
395 行里 A 388（data.json 194+notes.md 194）/B 0/other 7；other_titles 收窄为
反馈层测试 4、[Pipeline] pipe_test.txt 2、test 1。

**诚实边界**：①pipe_test.txt 2 行不动——内容「Hello pipeline test file for knowledge
import validation 20260919.」自述为测试验证文件且文件名含 test，但 repo 全历史零出处
（git log -S 零命中，疑为一次性 e2e 探针），按宁窄勿滥留 other 待拍板②「反馈层测试」4、
「test」1 不在本拍（候选只点名夹具词表，范围纪律）③388 行 A 类**本拍只判不删**（删除执行件
另拍，TL-④~⑰ 备份+回读纪律）④A 判定=字面逐字节比对非语义理解。
