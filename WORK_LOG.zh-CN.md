# Gekko 过程与验证记录

本文件用于复盘和面试准备；任务见 [PROJECT_PLAN.zh-CN.md](PROJECT_PLAN.zh-CN.md)。只把真实发生并验证的事记成完成。

## 2026-10-12：需求分析与 Git 起点

### 目标与范围

用户要求分析项目、拆分任务、注意 Git、保存过程及解题思路，用于实习申请；补充还有一整周。本轮做需求/现状检查、原始 mock 探测、设计建议和任务地图、本地 Git 记录；没有实现业务功能。

### 现状检查

读完原题、Q1/Q2/Q3、两个 mock 合约及源码、CSV、业务/测试/部署骨架、已有环境和披露记录。检查项目及父目录，未发现适用 AGENTS.md。

Git 起点：main...origin/main，HEAD=`d17c972e811d36f92f9dd43506aff836785a7501`；origin 为出题方。进入时暂存区为空，有七个已有环境准备修改/新增。建立 `codex/assessment-plan`，逐项暂存并提交 `f1ab81c`，保留原题和准备工作的先后关系。

执行 `.venv/bin/python -m pytest q1_api/`：`1 passed in 0.21s`。仅证明导入骨架；没有 Q1 业务测试。Docker 本轮未重新验收。

### CSV 检查

一次性 Python `csv.DictReader` 检查六字段必填、日期正则及 `date.fromisoformat`、合成证件号前缀、文件内 case_ref 重复。

| CSV 行号（含表头） | 问题 |
|---:|---|
| 6 | dob 月份为 13 |
| 13 | applicant_name 为空 |
| 18 | case_ref 重复：APPL-2026-0007 |
| 19 | dob 非 YYYY-MM-DD |
| 28 | dob 为 unknown |
| 41 | applicant_name 为空 |
| 48 | case_ref 重复：APPL-2026-0031 |

50 行中 43 可接受、7 拒绝；可接受行中 badscan=5。这是独立诊断，不是尚未存在的上传端点结果。

### Mock 请求实验与复现条件

用 `.venv/bin/python`、httpx、importlib 载入两个未修改的 server.py；各用原始 Handler 启动 `ThreadingHTTPServer(('127.0.0.1', 0), Handler)`，自动分配空闲端口，不触碰已有服务。确认无 OCR_/ONBOARDING_ 环境覆盖。客户端 trust_env=False、诊断 timeout=0.3 秒，所有请求串行。结束时 shutdown/server_close；延迟线程随诊断进程退出，未修改 mock 延迟逻辑。

OCR：seed=42，FAIL_RATE=0.20、HANG_RATE=0.10、HANG_SECONDS=600。向 `/ocr/parse` 发 20 个请求，case_id 依次 PROBE-01…PROBE-20，image_ref 都为 id_scan_0001.jpg；再发 PROBE-BAD，image_ref=ID_BADSCAN_probe.jpg；最后取 `/debug/requests`。

| 结果 | 数量 | 分母/比率 |
|---|---:|---|
| ok | 10 | 20 次可读尝试的 50% |
| error_500 | 6 | 20 次可读尝试的 30% |
| hang | 4 | 20 次可读尝试的 20% |
| unreadable | 1 | 单独一次 badscan，不混入可读尝试概率 |

10 次成功字段一致，成功响应约 1.16–5.09 ms；badscan 返回 422 和 scan quality 理由。小样本观察比率不等于配置概率，更不等于应用最终案件失败率。0.3 秒仅是诊断 timeout，不能直接当作应用配置依据。

开户：seed=7，FAIL_RATE=0.20、LOST_RATE=0.15、LOST_SECONDS=120。两次向 `/ingest` 发相同字节 `case_ref,id_number\nPROBE-001,TEST-ID-000001\n`，相同 Idempotency-Key=analysis-export-20261012-001、X-File-Name=analysis-probe.csv。首个 timeout，第二个 200/deduplicated=true。`/debug/deliveries` 只有 1 条、attempts=2，sha256 与字节一致。两次请求之间未重启/reset。

原始数据：[2026-10-12-contract-probe.json](q2_integration/evidence/2026-10-12-contract-probe.json)，本机副本 `.gekko-setup/analysis-20261012.json`。含配置、HTTP 结果/耗时、debug 原始记录和 CSV 拒绝。按相同配置/顺序复测时 outcome 可比较，时间/端口/耗时不要求相同。

### 工程依据与建议

- OCR 无外部业务副作用、同图字段确定：中断后可以再请求；未完成结果保留 pending，避免 processing 遗留。
- 开户在客户端超时前已经 ingestion：先持久化不可变 export 身份和字节，再发送；重试原 export/key，不能新造一个导出来补偿。
- CSV 申请值与 OCR 合成值不同：分开保留来源，不擅自覆盖或自动拒绝。
- 建议 SQLite 短事务实现导出成员/快照/状态一致性；明确单 worker 的限制。用户尚未最终选择路线。
- 三模块共享同一应用，顺序完成；任务细拆、模块提交，降低接口漂移与理解成本。

### 文档记录过程

首次批量文档补丁因 AI_LOG 上下文不匹配被整体拒绝；确认没有部分写入后，拆开新增文档与披露追加重新处理。这是文档编辑问题，未涉及业务代码。

### 完成范围与局限

完成：需求分析、合约探测、建议方案、任务/验收地图、Git 准备基线、过程与 AI 披露。

未完成：技术路线确认、具体代码实施计划、Q1/Q2/Q3 业务、应用真实故障率、中断恢复、Compose 验收、备份恢复、CI、公开 fork、邮件提交。原始业务代码/mocks/部署骨架/REFLECTION 保持不变；环境准备通过不代表上述功能通过。

### 文档验收

提交前自动检查通过：两份新文档所有本地 Markdown 链接均存在、代码围栏成对；原始 JSON 可读取且统计吻合；`git diff --check` 无错误。对照 d17c972，原题 README、REFLECTION、requirements、应用/测试、两 mock 和 q3_deploy 均无改动。保留的 pytest.ini 环境修复属于此前准备提交。

## 后续追加格式

每个任务记：日期/ID；目标；修改文件；选择及理由；命令和实际结果；证据路径；失败及修复；未理解/未验证事项；模块提交。状态使用“待做 / 进行中 / 已验证 / 有限验证 / 未通过”。


## 2026-10-12：提高标准并逐项复核评分

用户要求用较高标准完成，同时注意打分事项并审查规划是否足够。重新对照原题所有九个核心小题（合计 100）及 CI（10），新增 ACCEPTANCE.zh-CN.md；同步修改 PROJECT_PLAN，避免两份标准冲突。未开始业务实现。

修正：seeded 从“文件在镜像中”提高为默认首次启动已有样本批次、事务性一次初始化；OCR 普通 process 支持可重试失败，真实恢复对比未通过不能只作备注；取消未经测量的 4 次重试默认合理假设；交付 timeout/耗尽/可查询状态及 CSV 来源明确；CI 纳入计划交付。保留 SQLite/单 worker 建议，不根据未给出的评分偏好猜测 MongoDB 加分。

计算复核：假定独立、每次瞬态失败概率为配置值 0.3，执行 Python 计算 1-(1-0.3**n)**38，n=4 得 0.26585841，n=6 得 0.02733164；属于理论估算，不是新 mock 实测。提高尝试次数仍不能保证成功，需有界恢复和明确的持续失败验收。

新矩阵区分原题要求与自设加强项，规定每项有可复现命令、被测 commit、实际结果和证据。T4/T7/T8 预留额外验证缓冲；重点投入真实故障、备份恢复、干净克隆和面试修改能力，不增加无评分依据的系统复杂度。方案仍待审阅，不表示现有功能已经通过。

文档验收：自动对照原题逐小题标题，确认 9 项分值相符、核心合计 100、CI=10；新/改文档本地链接及围栏有效，git diff --check 通过；对照原题确认业务、mock、部署骨架与 REFLECTION 未改。本轮仅文档修订，没有重复运行业务测试或声称恢复实验通过。
