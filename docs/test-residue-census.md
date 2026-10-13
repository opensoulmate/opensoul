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
| ~/.opensoul/sandboxes | 0 | U 4（随机 id 目录无法从名判） | |
| echo/mirror templates.db | 0 | 0（tpl-*/stpl-* 均为内置种子内容非残留） | |

### 3. 残留增长闸门（隔离是否真稳）

TL-②-⑥ 收口时刻（2026-10-13 20:30 CST）之后新增 **A 类行 = 0**：pytest 写方全收编后测试残留不再进入生产 timeline（后续周期 live e2e 探针行亦已各自清痕）。闸门指标入工具（`after_tl2_6_cutoff_A`），后续周期可复跑盯回归。

## 独立复算交叉核对（防工具自嗨）

与工具输出逐值对平：stub-agent verify 1,138 ✓ / 192.168.99.99 275 ✓ / nerve 风暴 22,030 ✓ / `[Pipeline] test` 391 ✓ / perm t1+t2+api-test 567 ✓ / A+B+U=38,027=total ✓。

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

1. **低风险可删（A 类）**：timeline 8,612 行、knowledge 391 行（级联 chunks/tags）、测试用户 4+2、e2e 租户 2、perm 决策审计 612 行、manifests 悬挂 5 条、探针组 A群组/B群组（delete_group 级联）。删前工具已留 event_id 全清单，可按清单精确删+备份。
2. **须拍板取舍（B 类）**：nerve 风暴 22,030（删=丢 2026-08-18 安全日志史；建议保留）、mirror exec 666、pipeline 421、exports 308。
3. **修生产行为（非删除）**：permission.db 105 条 test_dangerous_tool 活跃规则 + 2 条 p09-probe——测试残留规则仍在权限引擎生效，建议失效化（is_deleted=1），同样待拍板。
4. **不动（U/真）**：brain/think 真事件、healer/cortex 生产探针、真实安全事件、Daily backups、om-* 会话、内置模板。

## 诚实边界

① 分类是字面规则非语义理解：A 类零假阳设计（对象级硬标识），代价是部分真伪混合面（brain verify 无 source 键 992 行、perm 空 session 1,042 行）如实落 U 不猜。② knowledge 391 行删除需级联 chunks/tags/knowledge_tags 三面，工具只清点主表。③ ai_groups 任务 U 40 行真伪混（子任务 goal 不带父标记），随组处置比逐行删稳。④ 复跑稳定性：两轮清点逐键一致，唯一差异=perm 决策审计真实 om-* 会话 1,304→1,306（生产实时活动持续写入=活库常态，非工具抖动；timeline 面两轮零变化）。⑤ 本拍零 src 行为改动；删除脚本（按清单精确删+备份+回读）待拍板后另拍。
