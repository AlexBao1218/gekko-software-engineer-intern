# Q3.3 备份/恢复验收 — 2026-10-14

基线 HEAD `1a51d4a`，本次统一 Q3 提交前工作树。源码指纹和命令输出随报告保存；不是仅凭子 agent 宣称成功。最终提交后将执行独立克隆复验，记录将保存在本机 `.gekko-setup/q3-final-20261014/`，避免为写入自身 hash 无限追加提交。CI 不属于本次已完成项。

## 评分核对

| 原题 | 分值 | 实际结果与证据 |
|---|---:|---|
| Q3.1 Docker/Compose/nginx/持久化/seed | 12 | T6 九阶段通过；T7 再从独立源码快照、新项目、两个新命名卷 build/up，四服务 healthy，UID10001，完整 smoke 通过 |
| Q3.2 health/readiness/配置/启动顺序 | 8 | T6 数据库权限/锁/缺表等验证；当前 Compose config 通过；T7 备份期间 ready 保持，恢复后 healthy、seed 身份不变 |
| Q3.3 在线时间戳备份、七份保留、runbook | 10 | 实际调用 backup.sh 九次，最新七份保留；坏备份/运行中恢复拒绝；实际 restore.sh 后九表全部行及 BLOB 相同，审批/交付回执/下载无损；启动、日志、OCR 诊断及恢复指令已执行 |

这是要求覆盖情况，不是评审已授予分数。T6 详情见 [T6 验收](T6-ACCEPTANCE-20261014.md)。

## 可复现命令与结果

```bash
.venv/bin/python -m pytest q1_api/
.venv/bin/python q1_api/tests/backup_experiment.py --context colima-gekko --output NEWDIR
.venv/bin/python q1_api/tests/delivery_experiment.py --output ANOTHER_NEWDIR
```

- macOS Python3.12.14：**478 passed**，49.92s；[原始输出](t7-pytest-macos-20261014.txt)。
- Linux arm64 Python3.12.15、UID10001：**478 passed**，64.21s；[原始输出](t7-pytest-linux-20261014.txt)、[环境](t7-linux-environment-20261014.json)。两者均有一条原有 TestClient 弃用警告。Linux 使用相同已构建 API image，源码只读挂载、network none、pytest cache 关闭。
- 原生 API+两个未修改 mock：完整交付、响应丢失、SIGKILL 恢复 **3/3 PASS**，入账分别2/1/1。[报告](t7-native-regression-20261014/report.json)。
- Docker 备份/恢复 **9阶段 PASS**，181.308s（含122.167s新镜像启动），资源清理错误为空。工作树和独立快照57个源文件前后哈希一致。[报告/全部命令](t7-backup-20261014/report.json)。

28项备份测试覆盖真实 SQLite WAL已提交与未提交边界、缺失/空/损坏/缺表/外键错误、写锁deadline、共享操作锁、九份保留且无关文件/符号链接不受影响、恢复前安全副本、旧sidecar、路径拒绝、seed marker、运行/暂停/重启/缺失/未知 API 状态和 Docker inspect失败。初始 RED 记录在对话工具输出，未伪造或补写原始 RED 文件。Docker九次备份时 API 运行但业务空闲；活跃写事务/WAL证据来自 SQLite 测试，不能混称负载压测。

## 真实恢复核对

备份基线：batches=2, cases=86, delivery_attempts=2, export_cases=1, exports=1, ocr_attempts=66, ocr_runs=1, reviews=2, seed_imports=1。

先备份已审批且已交付的批次，再通过 HTTP 新增一个43/7批次。停API后坏备份恢复被拒绝，DB文件哈希不变；选定好备份恢复后，**启动前、启动后、重复deliver后九张表完全相同**。新增批次恢复后404，证明实际发生时间点回滚，而非空操作。两个mock实例全程保持原ID。

CSV SHA-256：`3b427834411424d44890de0942371008c18a4ba61d1dfb018788ff09fcd6a642`；原始receipt `DLV-0001`及其key/文件名保留。恢复后又请求deliver三次，远端仍只有**1次入账**且debug记录完全不变。可下载CSV逐字节一致。

[恢复前](t7-backup-20261014/business-before-backup.json) · [恢复后启动前](t7-backup-20261014/business-restored-offline.json) · [恢复后启动后](t7-backup-20261014/business-restored-online.json) · [重复交付后](t7-backup-20261014/business-final.json)。

## 保留策略的前后文件列表

九次均是真实生成，没有伪造历史时间。第8次删除第1份，第9次删除第2份；以下是第9次运行的独立目录列表。所有9个文件名见报告 `created`，每轮前后列表、字节数和SHA另有原始JSON。

```text
Before ninth backup (7 files):
backup-20261014T021546554150Z-2604db31.sqlite3
backup-20261014T021547462249Z-7164b96d.sqlite3
backup-20261014T021548245087Z-9987fa4d.sqlite3
backup-20261014T021549509703Z-7adbb1bd.sqlite3
backup-20261014T021550553206Z-db053429.sqlite3
backup-20261014T021551377638Z-f80548b0.sqlite3
backup-20261014T021552140186Z-03ea8db8.sqlite3

After ninth backup (7 files):
backup-20261014T021547462249Z-7164b96d.sqlite3
backup-20261014T021548245087Z-9987fa4d.sqlite3
backup-20261014T021549509703Z-7adbb1bd.sqlite3
backup-20261014T021550553206Z-db053429.sqlite3
backup-20261014T021551377638Z-f80548b0.sqlite3
backup-20261014T021552140186Z-03ea8db8.sqlite3
backup-20261014T021552886860Z-7f77edc5.sqlite3
```

## 运行边界

- 每次备份都是时间点快照，不是同步副本。恢复不撤销远端 ingestion；若备份中缺少后来产生的 export identity，必须先对账再启动。mock 的幂等记录只在进程内保存。
- safety副本位于pre-restore子目录，不属于七份日常备份，恢复确认后需人工清理。演练额外创建一份故意损坏的旧名文件用于拒绝测试，发生在九份保留验收之后，随隔离卷清理。
- shell守卫不阻止人为并发启动另一个writer；停止所有writer是恢复前提。数据库I/O的操作系统级卡死不在Python deadline保证内。
- 独立备份卷不等于异地备份；无自动定时任务。Python3.10、远端CI及候选人理解尚未验证。
