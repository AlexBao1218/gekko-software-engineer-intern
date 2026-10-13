# Q2 完整模块验收（2026-10-13）

基线 `c6b8f9da0087826c9a640526bcf1acd744581f3f` 加本次 Q2 实现。业务代码冻结后验证；Git 最终统一提交 OCR、恢复、导出和交付。这里报告覆盖证据，不预判出题方得分。

| 原题项 | 分值 | 实际证据 | 结果 |
|---|---:|---|---|
| Q2.1 后台 OCR | 10 | 202 不等待 OCR、进度、五字段、5 个 badscan 原因；真实 HTTP 完整样本 | 通过 |
| Q2.2 超时/重试/恢复 | 12 | 明确 1s/六次/有界退避；三种 seed 默认故障实测；真实 SIGKILL 后按 case_ref 比完整字段及终态；审批保护 | 通过；seed42 保留单轮耗尽记录，不声称总能单轮成功 |
| Q2.3 导出/交付 | 13 | 精确成员/事务回滚/并发；固定 CSV/key；真实回执丢失及入账后 SIGKILL，debug 每 export 只有 1 ingestion | 通过 |

OCR 历史冻结证据见 [OCR-ACCEPTANCE.zh-CN.md](OCR-ACCEPTANCE.zh-CN.md)；最后整模块回归再次运行全部 OCR 测试，交付加入后未修改 OCR 客户端、调度和存储规则。

## 最终回归和环境

- 本机 `.venv/bin/python -m pytest q1_api/ -q`：**411 passed, 1 warning in 23.63s**，退出 0；摘要 [q2-pytest-20261013.txt](q2-pytest-20261013.txt)。
- 全新 Python 3.12.14 venv，仅从根目录 requirements 安装；临时源码快照中清除 PYTHONPATH，执行 `python -m pytest q1_api/`：**411 passed, 1 warning in 26.24s**，退出 0。
- [干净快照完整输出](q2-clean-snapshot-pytest-20261013.txt)、[安装命令和实际依赖版本](q2-clean-environment-20261013.txt)。版本尚未最终锁定；最终项目依赖固定/容器兼容检查留到 Q3 交付验证。
- 157 项原 Q1 + 131 项 OCR + 123 项导出/交付。测试数量不是穷尽证明，故障边界见下表。
- 原有 Starlette/TestClient 的 httpx 弃用警告保留，没有屏蔽，也没有为了消除警告添加未使用依赖。
- 提交后对实际 commit 做本地干净 clone、复用本轮新建依赖环境再执行标准测试；精确 hash/结果保存于忽略目录 `.gekko-setup/q2-final-clone-verification.json` 及日志，在最终答复报告，避免为记录自身 hash 再提交。

## 原始 mock 原生交付证据

命令：`.venv/bin/python q1_api/tests/delivery_experiment.py --output q2_integration/evidence/delivery-final-20261013`。

两个 mock 均未改源代码。OCR 故障率在本组设 0，隔离交付故障；OCR 默认随机故障另有前述独立实测。每个场景启动新 mock，场景内 API 重试/重启时一直保留该 mock，不 reset。

| 场景 | 注入/过程 | 本地尝试 / 远端尝试 / ingestion | 实际结果 |
|---|---|---|---|
| full-flow | 导入 50 行 → 43 接受/7 拒绝 → OCR → 批准 2 案 → 导出；空再导出；另批 1 案再导出 | 第一份 1/1/1；第二份 1/1/1 | 两份文件分别 2/1 行、不同键；都 delivered，0.920s |
| lost-response | 远端已保存后延迟 0.5s，客户端 timeout 0.1s | 2/2/**1** | 第二次同键拿回回执；原 CSV/SHA 不变，1.019s |
| sigkill | 远端保存后延迟 2s；客户端 timeout 3s；确认本地 delivering/receipt=null 时真实 SIGKILL | 2/2/**1** | returncode=-9；API 重启自动恢复，未调用 deliver，1.185s |

[report.json](delivery-final-20261013/report.json) 包含所有场景 HTTP 请求/响应、原始 debug/deliveries 与 OCR 请求、初始/最终状态、回执和崩溃边界。各子目录保留对应 result.json 和 CSV。全部 CSV 字节与期望列/成员一致；下载内容 SHA 与保存值、回执、debug 一致。

[source-manifest.json](delivery-final-20261013/source-manifest.json) 覆盖 33 个 app/tests/依赖/pytest 配置/原始 mock 源文件；实验前后哈希一致，exit=0。正式实验全部通过；初期 RED、接口 null 字段修复、关闭回归记录见实施/工作日志。临时 DB 和进程日志按既有规则忽略，服务子进程已经退出。

## 特殊情况及对应测试

| 特殊情况 | 预期结果 | 测试位置 |
|---|---|---|
| 包含待处理、OCR失败、一级通过、两级拒绝、既有exported | 只取 stage2_approved | test_exports / native |
| 无新批准、全拒绝或未知批次 | 空200不造文件；未知404 | test_exports |
| 首次后新增二审批准 | 第二份只有新成员、键不同 | test_exports / native |
| 8 路并发 export | 仅一份包含该批案件 | test_exports |
| 导出写入中失败 / 缺OCR数据 | 全事务回滚，审批保留 | test_exports |
| 原申请变化后再下载 | 下载仍是最初快照 | test_exports |
| 回执 key/文件名/SHA错、字段类型/Unicode坏、过大响应 | 失败且不误标 delivered | test_delivery_client |
| 发起后输入字典被修改 | 请求/回执核对仍对应发起时的固定身份 | test_delivery_client |
| 400 / 坏回执 | 首次后停止自动重试 | test_delivery_workflow |
| 一直超时 / 5xx / 网络错误 | 有界尝试，保留可见失败，明确重试复用原键 | client / workflow |
| 8 路重复 deliver | 在途只发一个请求；delivered不重发 | test_delivery_workflow |
| API任务异常 / 关闭中再次关闭 | 可查询失败，SQLite领取不遗留无主任务 | test_delivery_workflow |
| SQLite写锁导致错误也无法保存 | 存储恢复后补记失败，不自动重复网络调用 | test_delivery_workflow |
| 远端成功但本地回执写入失败 | failed/unknown；明确重试用原键 | test_delivery_workflow |
| 取消后的SQLite线程晚到 | 旧 token 不能覆盖新回执 | test_delivery_workflow |
| 收件后超时 / 收件后杀API | 同一个活mock里，重复请求仅一份 ingestion | test_delivery_native |
| 已审批后再process / 迟到OCR结果 | 审批保持，不重选、不覆盖 | test_ocr_workflow / native |

## 使用与验证边界

- `exported` 代表已保存快照；`delivered` 才代表本地保存了验证通过的回执。
- 正常 shutdown 将当前交付置 failed，需明确 deliver；SIGKILL 留下 delivering，在重启自动恢复。pending 始终是持久队列。
- delivery attempts 是网络调用前持久化的**尝试意图**，不是每次都能证明已发送；unknown 不能被解释为未收件。
- 支持单 API worker、一个持久 SQLite 文件。mock 去重只存在于进程内，重启/reset后不保证跨期去重。
- 测过进程中断、短暂锁、注入写失败；未声称物理断电、数据库损坏、多实例生产调度或持续负载已验证。
- Q3、CI、最终公开个人 fork/发布和申请人亲自理解/修改演练仍待完成。
