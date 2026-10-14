# Q3 部署、持久化与健康检查实施记录

2026-10-14，基线 `1a51d4a`，分支 `codex/assessment-implementation`。用户本轮授权先做 Docker/nginx、持久化和健康检查，对应 T6、Q3.1（12 分）和 Q3.2（8 分）。T7 备份/恢复及 T8 最终整模块验收另行继续，不把本轮当成整个 Q3 完成。

## 设计与分工

- 主线程：readiness、生命周期集成、依赖固定、Docker 实际启动/故障验证、文档与 Git。
- seed 子 agent：独立 seed 模块、共享导入事务入口、样本初始化测试。
- containers 子 agent：Dockerfile、Compose、nginx、环境样例和 .dockerignore，禁止同时操作 Docker 运行环境。
- smoke 子 agent：smoke shell/Python 及测试，完整 HTTP 流程由主线程再在容器验证。

继续使用现有功能分支和专用 `colima-gekko` Docker context，不切换全局 Docker 默认设置。按原计划每模块一次提交，T6 先保存源码与验证记录，T7/T8 完成后统一提交 Q3；不重写 Q1/Q2 历史、不推送。

## 规则与理由

1. SQLite 持久卷 `/data` 保存业务库和交付快照；只对外发布 nginx 的本机端口。API 使用 UID/GID 10001、一个 worker。应用代码、样本和运维脚本进入镜像，.env/本机数据库/凭证/.git/实验证据不进入构建上下文。
2. API 首次启动按 `SEED_CSV_PATH` 导入样本。seed marker、批次、拒绝原因和案件同事务；重复启动返回原批次，不能重置审批或交付。来源 SHA 改变时拒绝启动，避免把另一个样本悄悄当成已处理数据。原生模式未设置该变量就不自动导入。
3. `/api/health` 仅表示进程活着；`/api/ready` 打开现有数据库（不允许创建），读取业务 schema，实际写入一张探测表并立即回滚，验证数据库和 journal 目录可写。短暂拿写锁，等待最多 0.2s；失败 503，不泄漏路径。返回 ready 前已完成初始化/seed；启用 seed 时响应含 seed_batch_id。
4. mock 用 `/online` healthcheck；API 等待两个 mock healthy；nginx 等待 API ready 和 OCR healthy。不是靠固定 sleep 碰运气。依赖仅控制启动顺序，不是运行时自动修复机制。
5. nginx 不改写 `/api/*`、`/ocr/*` 路径；通过 Docker DNS 重新解析替换的 API 容器。请求体上限 3 MiB 给 2 MiB CSV 留 multipart 开销；API 自己仍执行 2 MiB 文件上限。连接超时 2s，读/写空闲超时 5s；后台 process 快速202与直接 OCR 代理等待是不同场景。禁止代理自动重放 POST。
6. 用 Q2 已验证的实际版本固定七项直接依赖；在 Linux 容器重新安装、验证，避免仅固定却未经测试。
7. smoke 必须先查首启 seed，另上传新批次，经 nginx 完整走 OCR/两级审核/导出/交付，核对文件SHA与远端唯一收件，不能把 nginx 欢迎页或单个health 200当成验收。

## 测试记录

- readiness 九项先因404失败，实施后九项通过：正常且无业务改变、缺失/空/损坏/缺表、写锁、只读文件、不可写目录、含 URI 特殊字符的路径。
- 生命周期两项先失败：seed未初始化、missing seed仍启动。接入后26项 readiness+seed 全通过。
- seed 子 agent十五项先缺模块失败；实现后连同导入/DB共50项通过：43/7、并发初始化、事务回滚、源变更、保留审核/导出/回执。
- 独立审查发现遗漏 ocr_attempts/export_cases/delivery_attempts 三表：实际缺表却200，三项RED后补检查转绿，readiness最终14项。
- 全套本机450通过（70.08s），Linux/arm64 Python3.12.15非root容器450通过（80.76s），均保留1条既有弃用警告。
- 独立源码快照+新卷真实部署九阶段全部PASS：强制API换IP且nginx不重启，整个stack重启后九表数据相同，3档体积限制、只读DB/目录引发503且Docker unhealthy/healthy，直接OCR在5.07s返回504且仅一请求。53源文件前后哈希不变，清理无错误。
- 验收及原始证据见 [T6-ACCEPTANCE](q3_deploy/evidence/T6-ACCEPTANCE-20261014.md)。Q3.3仍未实施，T6工作树保存未提交，整模块完成后统一提交。

## T7 在线备份与恢复（2026-10-14 续作）

用户本轮授权完成 Q3.3 和统一 Q3 提交。备份实现/边界测试、独立审查、容器恢复实验分别交给三个有明确文件边界的子 agent；主线程负责配置、真实 Docker 运行、评分核对、文档和提交。

- 不直接复制运行中 DB。采用 SQLite online backup API，包含已提交 WAL 数据，生成自包含快照；先写临时文件、校验，再发布。这样失败不会把半份文件当成可恢复备份。
- `/data` 和 `/backups` 使用两个命名卷，由非 root UID10001 操作。按 UTC 时间戳命名，附随机后缀防碰撞；共享文件锁覆盖备份/清理/恢复，成功后才保留最新七份。只清理脚本拥有的严格文件名，忽略其他文件和符号链接。
- 恢复属于维护操作，先停止 API，保留两个 mock；校验备份后才替换数据库，并保存恢复前安全副本。安全副本单独存放，不伪装成日常七份备份。
- 恢复验证比较九张表的所有行（含 CSV BLOB），不是只查案件数量；再通过 HTTP 检查 seed、审核、export 内容、幂等键、receipt 和远端收件次数。
- 恢复是回到快照时间，无法撤销下游已收件；旧快照中若还没有后来创建的 export，必须先人工对账，不能承诺任意时间回滚都保持端到端恰好一次。备份卷也不等于异地备份。

实现参考官方 [SQLite Online Backup API](https://www.sqlite.org/backup.html) 与 [Python sqlite3.Connection.backup](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)，没有引入新依赖或复制第三方代码。实际测试结果另记于 T7 验收表，未执行的检查不预标通过。

T7结果：28项备份/恢复针对测试、本机和非root Linux各478项全套通过；原生交付回归3/3通过；真实Docker恢复九阶段PASS。连续9次在线备份保留7份，真实回滚后九表及BLOB、审核、key/receipt完全相同，三次重复deliver仍仅一次入账。API运行期间的备份和活跃WAL写事务单测分别记录。详见 [T7验收](q3_deploy/evidence/T7-ACCEPTANCE-20261014.md)。本轮完成T6+T7统一Q3提交，精确hash提交后克隆复验另存本机忽略目录；CI和候选人理解验证仍未完成。
