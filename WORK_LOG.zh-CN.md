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

## 2026-10-13：Q2 T3/T4 OCR 接入、审批保护与故障恢复

用户授权继续Q2 OCR/恢复，合理使用子agent，并强调不覆盖已审批状态。本轮在`c6b8f9d`干净基线上实施Q2.1/Q2.2；不实现Q2.3、不调整既有审批规则、不另开用户对话。主线程集成，三个有明确文件边界的子agent分别做合同核对/最终只读审查、OCR客户端及测试、原生故障实验。新建Q2_OCR_IMPLEMENTATION.zh-CN.md记录接口与进度。

### 实现及理由

- OCRClient只做一次有界请求；显式阶段timeout和总deadline，64KiB响应上限，严格校验五字段；400/422/坏成功响应不自动重试，5xx/429/超时/网络故障可重试。结果和原申请字段分开。
- OCRManager生命周期拥有任务和HTTPclient；注册锁防重复同批任务，全局串行且最多四个活动/排队批次，超量429。同步SQLite工作放到线程，网络等待不持有事务。
- cases追加OCR结果/错误/次数/token；ocr_runs、ocr_attempts持久保存进度与已观测历史。旧Q1数据库幂等增列，保留原案件及审核记录。
- 仅pending/可重试ocr_failed可领取。回写时在事务中同时匹配原状态和run token；结果、状态、尝试历史原子提交。旧成功、旧失败、取消后仍运行的SQLite线程不能覆盖人工审批或新运行结果。
- 保持案件pending而非引入processing；重启把未结束运行标interrupted。任务异常标可见错误和可重试失败；错误落库遇DB锁时保留进程内待协调信息，下一次成功GET/process落库。存储恢复和单worker是明确前提。

### 测试和修复过程

1. 客户端初始91项因模块缺失RED，实施后GREEN；补理由净化1项RED→GREEN；主线程审查发现异常Unicode结果会污染API响应、Python3.10异步timeout异常类别不同，7项新回归RED（含正常Unicode对照），修复后客户端100项通过。
2. 主线程18项工作流初始RED（缺少调度/端点），实施后18通过；追加旧token、事务回滚、八路触发、队列容量、生命周期取消等验证。
3. DB持锁且后台异常时，错误落库本身失败，测试复现运行一直显示running；补延后协调后转绿。
4. 补真实旧Q1结构迁移、取消后仍执行DB线程、全拒绝空批次、真实退避间隔等。独立审查P2复现“取消触发领取但DB线程继续提交”的running孤儿；回归先失败，shield领取并等待/标中断后通过。工作流最终29项。
5. 原生脚本初次有URL缺斜线、按case_ref映射CSV误选被拒重复行两处测试工具错误，agent修正后2项原生测试通过；这些工具修正不假称业务TDD。
6. 最终本机标准根目录命令：**288 passed, 1 warning in 17.43s**。全新临时源快照/全新Python3.12.14环境，仅requirements安装：**288 passed, 1 warning in 21.60s**。保留旧TestClient/httpx弃用提示；不把Python3.10异常兼容单测等同于实际3.10环境验收。

### 实际测量与恢复

使用未修改mock，默认500=0.2、hang=0.1、延迟600s；预选seed42/7/99各测四/六次预算。每组新mock，普通POSTprocess，不使用隐藏恢复参数。另做实际SIGKILL，保持mock/DB，再启动API和有限重跑；以case_ref比完整OCR字段/最终状态。

最终冻结源码实验在ocr-final-20261013，前后23个源文件哈希一致。三个恢复比较全部通过：seed42干净/恢复各两轮，7/99各一轮。每种预算可读请求：seed42=61（37ok/14error500/10hang），7=60（38/16/6），99=53（38/8/7）；另各有5不可读。seed42四/六次都保留1个可读耗尽，不隐藏单轮失败。

独立20请求时延样本成功min/median/max=0.939/1.128/3.384ms；选择1s本地mock预算，默认六次且退避0.1/0.2/0.4/0.5/0.5，网络/退避最多7.7s每案。六次是模型支持的保守选择，本次没有实测出相对四次的终态改善；最坏多3s/案。非真实厂商SLA；排队/SQLite/调度不在网络预算内。

### 证据、范围和Git

`q2_integration/evidence/OCR-ACCEPTANCE.zh-CN.md`映射Q2.1/Q2.2、完整统计、复现命令和限制。最终report/source-manifest/每轮完整案件/debug请求均保留；冻结前试验也保留为历史。测试临时数据库/进程日志已被.gitignore排除，所有实验进程清理。未修改原题README、两个mock或Q3骨架，未加依赖。

同步使用说明、REFLECTION、ACCEPTANCE、PROJECT_PLAN、AI_LOG/TOOLS。基线仍是c6b8f9d，Q2工作树已保存但未提交；按原题每模块一次提交，待T5导出/交付完成统一提交Q2。不push、不发布。当前不声明Q2整体完成、Linux CI、分布式任务或用户个人理解通过。

## 2026-10-13：Q2 T5 导出/交付与完整模块验收

用户授权开始 Q2.3，并允许自行管理上下文/按需开新对话。本轮继续原对话，保留 OCR 既有未提交工作；三个子 agent 分别负责仅 delivery_client 和其测试、仅原生交付测试/实验、只读完整 Q2 审查。主线程负责导出事务、dispatcher、API、整合测试和文档。未建立额外用户对话，未让子 agent 提交或交叉修改文件。

### 设计与实现

exports 保存不可变 CSV BLOB、文件名、SHA、UUID和固定幂等键；成员唯一约束+状态变更同事务。只选二审通过，空导出200不创建文件，新审批另建新键。下载返回保存字节，11列UTF-8/LF按case_ref排序；缺完整OCR字段返回409整批保持原状态。exported与交付状态分开，失败不回退审批。

delivery_client 一次请求、1s总deadline、严格回执身份/哈希/类型/64KiB上限。dispatcher单worker串行领取持久pending，每轮四次且指数退避；成功需真实匹配回执。尝试意图先入库，结果用token/status条件回写。未知收件结果一直复用原键和字节。SIGKILL后自动重排未完交付；正常关闭/任务失败置failed，明确deliver恢复。数据库不可写时先协调失败再开新网络请求。详细理由在Q2_DELIVERY_IMPLEMENTATION.zh-CN.md。

### 测试驱动与缺陷记录

1. 导出六项测试先缺模块RED；事务实现后五项通过，空路由404仍失败；接入API后转绿。验证八路并发、新增批准、快照稳定、事务回滚和缺OCR。
2. 客户端子agent99项RED→GREEN，再以三项输入字典在I/O中变动测试复现身份漂移，固定入口快照后共102通过。
3. 工作流七项初始缺模块RED；接入调度后通过。再补五项：claim取消、旧record线程、真实DB锁、回执保存失败、八路deliver。重复cancel先留下delivering导致真实失败，增加stopping幂等close后12通过。没有把原本通过的补强测试冒称RED。
4. 三项原生测试先实际404；接入后POST丢失null receipt违反约定，改response_model_exclude_unset保留显式null。三项随后通过。全生命周期真实sample、故意lostresponse、实际SIGKILL，两个mock不修改。
5. 独立只读审查未发现剩余P1/P2；另独立执行115项客户端/导出/初版工作流、三项原生，最后12项补强工作流。审查提醒正常关闭与SIGKILL恢复路径不同、attempts是意图计数，已写入使用说明和REFLECTION。

### 最终验证与评分

本机 `.venv/bin/python -m pytest q1_api/ -q`：**411 passed, 1 warning in 23.63s**。全新uv创建Python3.12.14环境，仅安装根requirements，临时源码快照清除PYTHONPATH后跑标准 `python -m pytest q1_api/`：**411 passed, 1 warning in 26.24s**。已有Starlette/httpx弃用提示保留；测试通过不等于已经实跑Python3.10/Linux。依赖实际版本已留证，最终固定仍随Q3验收处理。

业务源码冻结后delivery_experiment三项均PASS：正常两份导出2/1行，各一次ingestion；lostresponse（延迟.5s、timeout.1s）两次请求仅一次ingestion；SIGKILL在远端收件、本地receipt空时returncode=-9，原mock保持运行，重启自动恢复，两次请求仍一次ingestion。耗时约.920/1.019/1.185s。CSV字节/哈希/receipt/debug均一致，33个源码文件实验前后哈希相同；测试子进程全部退出。OCR旧实测证据保留，整套回归再次覆盖OCR。

Q2三个评分项10/12/13分已逐项关联证据；不代替出题方给分。正式表在q2_integration/evidence/Q2-ACCEPTANCE-20261013.md，原始delivery报告/CSV/trace和pytest/环境日志同目录。同步README、REFLECTION、规划、验收矩阵、AI_LOG和TOOLS。明确记录Q3/CI/个人理解尚待完成。

### Git和后续

将全部Q2代码、测试、记录和合成数据证据作为一次完整模块提交，沿用codex/assessment-implementation，不改原题README/两个mock/Q3，不改系统Python或Docker。git diff --check和暂存审阅后提交，不amend/squash/force-push。提交后实际本地clone再跑标准pytest，真实hash/日志保存在.gekko-setup/q2-final-clone-verification.json，最终答复报告。无push、公开fork或申请提交。

下一步Q3：先部署/持久化/自动seed与nginx两路代理，再readiness，再在线备份保留七份/真实恢复和完整smoke；最后CI及本人讲解演练。本轮不提前宣称这些工作完成。

## 2026-10-14：Q3 T6 Docker/nginx、持久化与健康检查

用户授权先做Q3部署、持久化和健康检查，继续讲解设计、对齐评分、测试和Git记录，并合理使用子agent/上下文。基线为完整Q2提交1a51d4a，工作区起始干净。没有改变上游原题或mock源代码，沿用既有功能分支和colima-gekko专用context，不改系统Python/Docker默认值。本阶段对应Q3.1的12分和Q3.2的8分；Q3.3备份/恢复暂不冒称完成。

### 分工与设计

主线程集成main/readiness、固定七项已验证依赖、真实Docker运行/故障与记录；三个明确文件边界的子agent负责原子seed+共享import、容器配置、完整smoke。第四个新上下文agent只读审查；容器运行操作由主线程统一控制，避免并行修改同一Compose环境。

SQLite保存到api_data命名卷/data，API非root10001、单worker；只暴露nginx本机端口。镜像构建allowlist排除env、db、Git、测试/证据。Compose以真实healthcheck和service_healthy安排启动，不使用固定等待猜测服务就绪。nginx保留两路URI/query、用DockerDNS解析替换容器、3MiB请求体留出multipart余量、2s连接/5s读写空闲超时，并禁止POST代理重放。

首次启动通过SEED_CSV_PATH复用导入逻辑：marker、哈希、批次、拒绝记录和案件同事务；重启返回原ID，保留审核和交付；来源变更/缺失/格式坏拒绝启动。原生未设置seed变量时维持原行为。ready响应提供seed_batch_id，smoke另上传新批次。

health只报存活；ready用mode=rw打开现有库、不创建缺失文件，查工作流表，再实际写probe表并回滚验证journal目录可写，最多等锁0.2s；错误503不泄漏路径，不留探测表或改变业务数据。Docker每5秒检查，六次失败标unhealthy；运行期健康变化不等于自动重启。

### 测试与修复

readiness九项404 RED→九项GREEN。main启动集成两项RED（未seed/缺seed仍启动）→GREEN。seed子agent十五项缺模块RED，实现后连同原导入/DB共50项通过。smoke最初九项缺可执行脚本失败；实现后九项通过，再加“哈希正确但案件错误”检查，最终十项通过。

独立审查实际复现三张辅助表ocr_attempts/export_cases/delivery_attempts缺失仍ready。主线程新增三项先看到错误200，再补查询验证后14项readiness全部通过。其余容器配置、种子事务、smoke契约未发现剩余P1/P2。Python3.10语法及依赖Requires-Python检查通过，不代替真正3.10运行测试。

主机完整pytest：450 passed, 1 warning in70.08s；Linux/arm64 Python3.12.15容器非root10001，在独立源码副本中清除PYTHONPATH和runtime端点/seed变量，标准pytest：450 passed, 1 warning in80.76s。保留原有TestClient/httpx弃用提示。Linux重新从固定requirements安装，直接依赖与本机版本一致。

首轮Docker真实build/up四healthy，API id确认10001；ready返回seed43/7。原始默认故障下smoke成功：另上传43/7，OCR37done/6failed（包括5badscan和1可读耗尽），选择成功案件完成两审/导出/交付；onboarding回执显示两次请求去重，只一次ingestion。没有把部分OCR失败说成全部成功。

最终独立源码快照/新项目新卷演练九阶段全部PASS（169.001s）：APIIP172.19.0.4→.6，nginxID未变；API替换和整套重启后九表完整数据/BLOB一致；三档体积返回400/API413/nginx413；DB0444和/data0555均ready503/health200，Docker实际unhealthy后恢复healthy；直接OCR5.07s返回504，debug只有一次hang，无代理POST重放。53个源码文件前后一致，cleanup无错误，原始报告/快照/命令日志和限制已保存到q3_deploy/evidence/T6-ACCEPTANCE-20261014.md及关联目录。首轮和最终演练资源已清理，仅清本次创建的测试项目/卷；未动其他项目。T6工作保留未提交，遵循整Q3一提交，待T7/T8补齐后统一提交。不push、不发布。

## 2026-10-14：T7 在线备份/恢复与 Q3 收尾

用户授权继续 Q3.3、保留七份、真实恢复核对审核及交付记录，完成后统一提交 Q3。保留当前 codex/assessment-implementation 分支和 T6 未提交改动；主线程独占 Docker 运行控制，三个子 agent 分别实现脚本/测试、编写可复现实验、只读审查。

设计：在线备份使用 SQLite backup API 读取已提交内容（包括 WAL），只读打开源文件避免意外创建空库；临时快照先做完整性/外键/实际 schema 校验，再发布并按严格命名保留七份。文件锁覆盖备份与恢复，数据库锁竞争有 deadline。独立备份卷和 UID10001 权限在 Compose/Dockerfile 中声明。恢复要求 API 已停止，保存当前库的安全快照，先 checkpoint 旧 WAL，再替换数据库；不重启 mock。

子 agent 报告的 RED/GREEN：初始缺 helper 为11失败/6通过（其中部分拒绝用例已因缺文件满足非零返回，故不冒充这些测试已验证功能）；seed 测试暴露错误列名 source，改为 seed_key；恢复 guard 的运行/暂停/重启/不存在/inspect失败/未知状态六项先失败；配置启用 seed 却缺 marker 一项先失败。最终独立脚本28项通过。上述 RED 原始输出仅在工具对话内，没有事后制造 RED 日志；可复跑的测试随源码提交。

主线程本机全套478通过（49.92s），保留一条已有 TestClient 弃用警告。原生真实 API+两个原始 mock 的完整交付、响应丢失和 SIGKILL 恢复三项回归通过，入账数分别2/1/1。只读审查未发现 P1/P2，强调 Docker 九次备份时业务为空闲，写事务/WAL证据来自单测，报告必须分开陈述。容器恢复和最终提交的结果在后续验收记录补齐。

T7真实Docker结果：隔离项目gekko-check-832333024207，/data与/backups分别新卷。九阶段全部PASS，总181.308s。真实9次备份，第8/9次各移除最旧1份，最终保留最新7份；每轮独立文件列表、大小、SHA对照脚本输出。停API前恢复被拒绝；损坏快照被拒绝且现库文件不变。实际恢复后启动前/启动后/重复deliver后三次的完整九表+BLOB均等于备份前基线，后加批次404；mock未重启，远端入账仍1次。57个源码前后哈希一致，临时项目/卷清理成功。Linux非root全套478通过（64.21s），已留原始输出。全部评分项映射见T7验收表；这不是已获得分数的声明。

本轮按原计划将T6+T7源码、测试、文档、实测证据统一提交Q3，不改写Q1/Q2历史、不push。提交后将在独立本地clone复验精确hash，结果写本机忽略目录 `.gekko-setup/q3-final-20261014/`，避免将自己的提交hash写入提交而形成循环。CI和候选人本人解释/修改能力仍待验证。

暂存检查发现原始Docker输出自带行尾空格；用.gitattributes仅关闭q3 evidence文本的空白检查，保留原始证据字节，源码仍进行正常diff检查。凭证模式扫描及禁止运行时文件检查通过，未暂存.env/数据库/备份文件。

### 提交后干净克隆发现并修正测试时序问题

Q3统一提交7095290后，重新安装依赖的干净克隆完整备份/恢复九阶段PASS；但pytest出现477通过/1失败：test_smoke的stalled-deadline用例先在/api/ready触发50ms传输超时，尚未进入它要验证的OCR期限分支。原始失败保存在t7-clean-clone-initial-failure-20261014.txt，未以重跑掩盖。用正常ready响应增加100ms延迟稳定复现该问题（t7-smoke-timing-red）。

按系统化排查定位为测试把正常HTTP调度预算与业务deadline混在一起。修正：正常语义测试给HTTP合理预算；专测传输timeout仍使用短期限；停滞OCR用真实HTTP配合仅替换smoke模块的可控时钟，精确验证四次轮询后deadline拒绝且无导出。独立审查补充sleep(0)回归不能无限循环的保护。没有修改业务或运维生产代码。保留7095290历史，新增必要测试修复提交；不amend/squash、不push。修复后完整复验另存证据，最终hash重新克隆验证。

修复后全套：macOS478通过45.30s、非root Linux478通过64.49s；补全防零等待循环断言后针对smoke10项再次通过6.32s。均仅已有弃用警告。最终独立clone将对最终测试源码再次执行整个478项套件。

## 2026-10-14：T9 GitHub Actions CI 加分项

用户授权配置每次push跑测试/Compose build并实际验证最终提交，合理使用子agent。基线732b6ea工作树干净。核对GitHub账号AlexBao1218，origin仍为公开出题方仓库且无写权限，个人同名fork尚不存在。按已批准Git方案准备个人公开fork；不向出题方push，不发送PR/邮件。

主线程负责工作流、文档、GitHub发布及最终SHA实测；子agent只读核对评分条款、Docker上下文、Linux非root权限测试和最终工作流。选两个独立job：Ubuntu24.04/Python3.12全套pytest；Compose配置检查及三个应用镜像build。所有push触发，无分支/路径跳过；只读token、官方action固定SHA、有限job超时。使用官方语法/checkout/setup-python文档和GitHub API验证版本。YAML已解析，独立审查无初步阻塞项；不预称远端已通过。

T9首轮远端实测：公开fork AlexBao1218/software-engineer-intern已创建，parent/visibility/admin权限再次核对。origin重命名为upstream，添加个人fork为origin；fetch后验证origin/main为当前HEAD祖先，再将本地功能分支HEAD正常快进推到fork main，没有force-push。本地codex/assessment-implementation跟踪origin/main，后续使用明确的 `git push origin HEAD:main`。

CI提交b6d07e5由push触发GitHub run38501842241，head_sha完全匹配；两个job及全部步骤success。Python3.12.15 x64，478通过/1既有警告，测试40.43s；Docker Compose真实构建API/两个mock成功。run URL及原始JSON/完整日志已保存CI验收表/evidence。原有Q3最终732b6ea干净克隆478通过与恢复九阶段PASS的本机回执也已复核。CI首轮无失败或重跑。

本次补齐过程记录后将再提交文档并push；必须等新的最终SHA运行通过，不能拿b6d07e5绿灯顶替。最终回执另存.gitignore目录，避免hash自引用循环。未向出题方推送、未开PR、未发邮件；候选人本人理解和最终提交准备仍是下一阶段。

## 2026-10-14：最终交付审查与发布冻结

用户明确要求先停止发布、隐藏仓库并作最高标准的交付核验。此前技术 CI 的公开执行不代表个人反思、解释能力和交付材料已准备完成；本轮以停止发布指令为准。候选版本固定为 d5fa8315b5a24e718d588e181db8306207fd762d，原题基线 d17c972，保留完整 Git bundle，不改写历史。

通过 GitHub CLI 将个人仓库改为私有。操作后 API 初始仍显示 fork，随后后台处理为独立私有仓库；未另外执行删除或 Leave fork network。个人仓库匿名 API 返回404，但匿名通过上游网络按原提交 SHA 仍可读到相同源码 blob。因此只确认入口私有，不能声称此前公开的历史代码已完全撤回；保留两阶段可见性与匿名探测证据，未联系 GitHub 支持或招聘方。

主代理与三个独立子审查核对业务题意、部署恢复及交付材料。新跑71项核心针对性测试和57项部署/备份针对性测试均通过，但发现并由主代理重复确认：时钟回拨时按文件名轮换会删掉新备份且回报成功。此外启动指南引用未提交的本机 helper；本人理解核验与一天优先级仍未完成；公开材料含本机路径，应整理。完整逐项结论、优先级、复现和修复验收条件见 [最终交付审查](FINAL_DELIVERY_REVIEW.zh-CN.md)。没有修改生产代码或代填个人反思。

实时核对最终SHA的CI run38502205548及两个job成功。另从私有GitHub重新clone同一SHA，新建venv；首次pip下载因PyPI读取超时失败并保留日志，uv按同一requirements安装成功。新的全量pytest、Docker及原生恢复结果见审查报告及 `.gekko-setup/final-review-20261014/fresh-verification.json`。审查报告和本段过程记录仅在本地保存，本轮不commit、不push、不重新公开。

## 2026-10-14：按审查结果修复并只做本地提交

用户授权修复备份时钟回拨、更新指南、脱敏材料、补测试记录和本地 Git 提交；明确禁止推送/公开，个人反思保留。沿用 codex/assessment-implementation，保存基线 tar 和脱敏原件。ff03e2f 单独记录审查及77个文件的路径脱敏，未改写历史；本段连同修复在后一个本地提交保存。

TDD：真实 SQLite 回拨/停钟用例先2失败；修复在操作锁内从磁盘最大时间戳继续递增，不新增排序文件，重启也有效。异常时间戳明确失败不轮换。33项备份测试通过（15.65s），覆盖正常九留七、连续回拨、停钟、CLI重启、旧格式、无效快照、极端时间及恢复。README/runbook 说明文件名时间可能暂时领先真实UTC。

首次全套与Docker并行时482通过/1失败：shell测试的外层5秒启动等待超时，全套167.18s。完整失败保留，未改生产deadline或测试上限。Docker停止后串行483通过（43.50s），该shell用例2.30s。资源争用是与现象相符的解释，未当成已证明的系统根因。真实Docker备份恢复9阶段全过（221.056s），九表/BLOB、审批、导出/回执一致，无重复入账，清理无错误；完整原件本地保存，提交明确标注的脱敏输出及派生摘要。

START_HERE 已改用相对路径和已提交脚本；TOOLS 当前摘要更新；REFLECTION 字节保持不变。最终提交后从本地干净clone再次检查精确SHA、483项测试、指南的三个原生启动命令和43/7导入、原生完整/丢响应/SIGKILL交付。回执另存忽略目录，避免hash自引用。未推送故本轮不会有新GitHub Actions结果，不把旧提交CI绿灯移用于新提交。后续逐模块理解顺序见 DELIVERY_FIXES.zh-CN.md。


## 2026-10-20 本地交付收口

按用户要求自主完成技术材料与最终核验，继续遵守不推送、不公开的约束。Codex 使用完成前验证技能、现有 Python/pytest、Git、专用 colima-gekko Docker 和只读 GitHub CLI。更新当前状态入口，区分历史测试与最终 SHA、历史公开 fork 与当前私有非 fork；REFLECTION 只改客观技术状态，保留第 4 节个人理解原文。原题、合约 mock 和数据不改。最终独立克隆验证及源码归档回执保存在被忽略的 `.gekko-setup/final-delivery-20261020/`，实际结果以回执和进程退出码为准；不把计划运行写成通过。

本轮环境异常：旧虚拟环境的 pytest 在收集时读取 FastAPI `.pyc` 长时间阻塞，尚未进入测试；用 macOS sample 保留栈后终止该进程。切换到临时目录的新 Python 环境和 Git 克隆，不调整测试阈值。Git 对象读取亦较慢，准确最终运行结果仍由独立回执给出。

最终 SHA 首轮：`8f6ac04` 干净克隆 483 项测试通过（39.82 秒，1 条已知弃用警告），原生交付正常/丢响应/SIGKILL 三场景通过。Docker 镜像构建成功，但临时克隆位于专用 Colima 未共享的 `/private/tmp`，nginx 配置绑定挂载失败；读取现有 Colima 配置确认仅共享原项目目录。未改虚拟机全局配置，改为共享目录内的独立克隆，补充通用启动指南说明。保留首轮失败和清理结果，新 SHA 重新验收，回执不覆盖旧结果。


## 2026-10-20 公开提交授权与准备

用户明确授权推送、公开及回复 Gekko 邮件。使用 GitHub CLI 新建公开 fork `https://github.com/AlexBao1218/gekko-software-engineer-intern`，保留旧私有仓库。上游更换初始提交，差异核对仅为题目措辞/评分数字/注释，因此使用独立 `assessment-submission` 分支保存原始开发历史，不强推。Outlook 连接器读取原邀请并按原线程准备回复；邮件仅包含验证后的 URL、最终 SHA、测试与 CI 事实，不声称候选人已掌握代码。个人反思原文保留。最终 Actions 和邮件发送回执保存在忽略目录 `.gekko-setup/publication-20261020/`；未有回执前不宣称成功。
