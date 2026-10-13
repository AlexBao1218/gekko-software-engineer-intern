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


## 2026-10-13：Q1 T1 数据模型、CSV 导入与查询

用户批准高标准方案，明确从数据模型/CSV 导入开始，要求边做边解释、测试和记录。本轮范围是 T1：SQLite 持久模型、multipart 上传、批次详情、过滤分页及相应验证；T2 审核尚未实现。

### 设计及依据

- batches 保存总行数/接收/拒绝统计和 JSON 拒绝详情，cases 保存有效案件；外键关联，批次内 case_ref/原行号唯一，初始 pending。SQL 参数绑定，读统计与列表使用短读事务保证一致快照。
- 先解析整个 CSV，再开始短写事务。行内容无效仅拒绝行，结构损坏拒绝整文件；数据库中途失败整批回滚，防止工作人员误以为半份文件已完整处理。
- 纯解析 imports.py 不访问数据库；db.py 管连接和事务；models.py 定义可见响应/合法状态；main.py 定义上传/HTTP错误边界。新增三文件与扩展入口约 330 行，未提前搭建 Q2/导出表。
- UTF-8/BOM、真实严格日期、首尾空白、按起始物理行报错；仅忽略物理空白行，带引号的空字段仍需六列。NUL 作为损坏文件在解析前拒绝。约定与体积限制已写 q1_api/README。
- DATABASE_PATH 在 lifespan 读取，默认仓库 data/verification.sqlite3；模块导入不建库。每次操作各自创建和关闭连接，避免跨线程连接；测试用临时文件。

### RED/GREEN 与修复事实

1. 写业务测试。第一次试跑暴露测试 helper 的重复 case_ref 关键字错误，修正 helper 用法，并给大文件参数设置短测试 ID，避免输出庞大内容。
2. 修正后的 RED：37 failed、1 passed，因上传/查询功能尚不存在（404及缺少返回字段），保留骨架导入通过。
3. 实现导入/查询后 38 passed；补充真实 DB 触发器故障、约束、锁冲突、并发与状态读回后 47 passed；这些后补检查属于验证，不声称它们分别经过 RED。
4. 用 uvicorn 子进程绑定专属 localhost 临时端口，真实 HTTP 上传原样本，终止进程后以同一数据库重新启动并读回：通过。测试保留在 test_native_process.py，可由 pytest 重复执行，不依赖本机 setup 辅助脚本。
5. 局部自审发现 quoted-empty/quoted-space 被当成空白行，以及 NUL 输入导致 SQLite length CHECK 抛出 500。新增三例 RED：前两例实际 201（应 400）、NUL 实际 500（应 400）。根据原始 CSV 片段识别物理空行，解析前拒绝 NUL；先分别验证 2 passed、1 passed，再运行全套。
6. 最终 `.venv/bin/python -m pytest q1_api/ -q --tb=short`：**51 passed, 1 warning in 2.29s**。这是 T1 的全套检查，不是整个 Q1 完成。唯一 warning 是已安装 Starlette TestClient/httpx 弃用提示，未屏蔽、未为消除提示额外安装依赖。

### 证据与边界

- 完整输出：`q1_api/evidence/t1-pytest-20261013.txt`。
- 被测代码/测试 SHA-256 与 Git 基线：`q1_api/evidence/t1-source-manifest-20261013.json`。基线为 85d2347，实际测试对象是该基线上的未提交 T1 工作树，不能把基线误当实现 commit。
- 样本实际 43 接收/7 拒绝，拒绝行 6/13/18/19/28/41/48；真实进程重启后仍能读回统计、拒绝详情和 43 个唯一案件。
- 事务故障注入只在临时数据库 trigger 内；第二个案件失败后 batches/cases 均为零。并发两次上传得到两个完整批次。写锁返回有 Retry-After 的 503，释放后可成功重试。
- 无新增依赖，无原始 mock/原题 README/Q3 改动；没有打开端口供外网访问或操作用户已有服务。测试自启进程均清理。
- 本轮完成局部自审，尚未做独立审查、正式干净克隆或 CI。REFLECTION 填写已实现事实，Alex 理解程度待本人核对，未代写 None。

### Git 与后续

在原干净分析分支基础上创建 codex/assessment-implementation；没有 push。遵照原题每模块一次提交，本轮文件已保存但不做半个 Q1 提交；完成 T2 状态机及其测试后合并提交 Q1。下一步覆盖正常两级通过、两级拒绝终止、重复/跳级/失败案件审核 409 及并发审核。


## 2026-10-13：Q1 T2 两级审核及统一模块交付

用户明确要求正常通过、拒绝、跳级、重复、并发测试，核对评分、更新记录并统一提交 Q1。沿用 codex/assessment-implementation，保留先前未提交 T1 改动。

### 实现与设计依据

- workflow.py 的四条显式规则是唯一审核策略；其余 28 个状态/stage/decision 组合均 409。请求类型和字段由严格模型校验（stage 为整数 1/2，禁止 bool/字符串/小数，decision 为 approve/reject，禁止未知字段）；未知案件 404。
- db.review_case 先 BEGIN IMMEDIATE 再读取状态，UPDATE 同时匹配旧状态；成功状态变化和 reviews 记录一起提交。竞争请求在锁释放后读到已更新状态，返回 409；持续外部锁仍可能返回可重试 503，不承诺消除所有存储不可用。
- 审核记录保存级别/决定/前后状态/UTC 时间，API 成功响应附该次记录。不给每阶段历史加终身唯一限制，为将来的“返回一级”保留真实历史；当前仍严格禁止重复和拒绝后继续。
- Q1 不伪造 OCR 路由。测试显式提供 ocr_done DB fixture，再实际调用两级审核；全部真实 OCR/导出生命周期仍属 Q2。

### 测试与独立审查

1. TDD 初始 56 个审核测试失败，原因是审核路由/reviews 表未实现；规则/模型/事务落地后全套 107 passed。
2. 补原生 Uvicorn HTTP 审核/实际进程重启，完整测试达到 108。两级各 approve/approve、approve/reject 的四组 barrier 并发均恰好一成功一冲突，数据库只有一条匹配审计。
3. 使用 requesting-code-review 技能要求的只读独立代理审查完整未提交 Q1。审查通过核心事务/状态机，额外验证 T1 旧库新建 reviews 后案件保留；报告一处 P2：框架错误响应回显 NaN/Infinity/lone surrogate 可造成 500，以及一处旧 fixture 注释。
4. 新增四项 malformed JSON 回归测试全部 RED；在 RequestValidationError handler 中仅保留 type/loc/msg 并安全处理异常 Unicode，不回显原输入/ctx；4 项与全套转 GREEN。修正注释。最终本机 112 passed，1 条既有 TestClient/httpx 弃用提示。
5. 新建临时目录复制仓库可提交源文件，排除 .venv/.gekko-setup 和 PYTHONPATH/DATABASE_PATH；新建 Python 3.12.14 环境，仅 requirements 安装。补声明实际使用的 pydantic>=2.0 后再次解析安装，无额外技术栈；快照最终 **112 passed, 1 warning in 3.83s**。

### 评分核对

Q1.1（15）：导入 43/7、逐行原因、状态数量、过滤/分页、持久性已测；Q1.2（10）：规则集中、四条允许路径、28 组冲突、两级拒绝终止、并发和审核原子性已测；Q1.3（10）：根目录完整 pytest、坏行、Q1 范围成功生命周期、至少三个不同 409 均覆盖。核对表是证据覆盖，不预报得分。真实 OCR/导出及拒绝案件的真实导出排除在 Q2 继续验收。

证据：q1_api/evidence/Q1-ACCEPTANCE-20261013.md、q1-clean-snapshot-pytest-20261013.txt、q1-clean-environment-20261013.txt、q1-source-manifest-20261013.json。manifest 记录源文件哈希与原基线，测试对象是将统一提交的工作树，不假称 85d2347 含实现。旧 t1-* 证据是历史中间结果。

### 交付与限制

本轮将 T1/T2 代码、测试、使用说明、REFLECTION、AI_LOG 和证据统一作一次 Q1 提交，不改上游原题/mocks/Q3，不 push。提交后的实际 hash 通过本机新 clone 再执行根目录标准测试命令，输出与 hash 保存在忽略目录 .gekko-setup/q1-final-clone-verification.json 及对应日志，并在最终答复报告；避免为 hash 自引用再造第二个 Q1 commit。

Q2/Q3/CI/最终公开 fork 尚未完成。Alex 仍需亲自解释或修改状态机、事务、输入错误边界；独立审查与通过测试不等同于个人理解。下一模块是 OCR 接入与恢复，本轮未开始。

## 2026-10-13：Q1 审批特殊情况复审与补强

用户要求判断审批是否合格、实测各种特殊情况并逐项列明。在已提交 `6ee40ef` 上复验，原 112 项通过；不把原测试数量当成穷尽所有情况。

新增 `test_review_edges.py`，共 45 项：请求体/类型/编码/案件标识边界，查询参数不能覆盖正文，8 请求同阶段竞争、跨阶段同时决定、相同 case_ref 的跨批次隔离、两真实 Uvicorn 进程竞争、更新失败、写锁与读锁阻止提交、真实写进程在提交前后被 kill。临时 SQLite/触发器/子进程在测试内创建并清理；无生产故障开关。

发现并修复：默认 JSON 解码将重复字段覆盖为最后值，严格模型无法发现原始审批意图冲突。探测 `reject` 后重复 `approve` 得到 200；从本地 Starlette Request.json 和 FastAPI 解析调用确认根因。先补六个测试（含同值和转义字段名），六项都因实际 200、预期 422 失败；增加审核专用重复字段依赖后，六项转绿。其余 39 项属加强验证，未冒称 RED。规则表、DB 事务和依赖清单不变。

完整命令 `.venv/bin/python -m pytest q1_api/`：**157 passed, 1 warning in 8.07s**。一条旧 TestClient/httpx 弃用提示保留。十轮重复运行 `-k 'eight_concurrent or concurrent_different_stages'`：每轮 8 个场景，共 80 场景/400 次审批全部通过；这不是性能容量测试。

解释要点：同阶段竞争只能一成功，但跨阶段同时提交允许一级先提交后、二级读取新状态成功。SQLite 写锁是数据库级，持续占用可以 503。提交前 kill 回滚；提交后 kill 保留结果，重试 409；没有原响应重放或归属识别。kill 演练使用真实 DB 方法子进程切点，不声称测过断电/真实丢包。

完整特殊情况、32 组合表、HTTP 结果、权限/审计/OCR/导出/部署限制见 `q1_api/evidence/Q1-REVIEW-EDGE-CASES.zh-CN.md`；原始全套输出、并发重复输出、前后探测和源文件指纹在相同目录。认证/角色/两人分离非核心 Q1 范围，目前确实缺失；同一客户端可以顺序完成两级。未开始 Q2/Q3，也未做本轮独立代理审查。

Git：保留原 Q1 模块提交，按原计划“必要修复如实保留”追加一次修复/验证提交，不 amend/squash、不 push。提交后实际克隆验证记录到 `.gekko-setup/q1-edge-final-clone-verification.json`，避免文档 hash 自引用。结论只限本轮核验的 Q1 审批行为，不宣称生产级完全合格。
