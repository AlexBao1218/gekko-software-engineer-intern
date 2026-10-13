# Q1 数据模型与导入 Implementation Plan

2026-10-13。用户已批准高标准方案并明确开始 Q1；本轮交付 T1（数据模型、CSV 导入及查询读回），T2 审核随后完成。不重复要求确认已授权工作。

**Goal:** 将有效申请原子保存为批次和案件，通过 HTTP 给出准确拒绝理由并可查询。

**Architecture:** SQLite 两表，批次包含统计与 JSON 拒绝记录，案件以 batch_id 外键关联。纯 CSV 解析与数据库事务分开，HTTP 层只负责上传/状态码/响应。

**Tech Stack:** Python 3.12（兼容题目 3.10+），FastAPI、标准库 sqlite3/csv、pytest、httpx；不新增依赖。

**Spec:** [PROJECT_PLAN](PROJECT_PLAN.zh-CN.md)、[ACCEPTANCE](ACCEPTANCE.zh-CN.md)。执行方式为本会话逐项实现、TDD 验证；模块完成前不创建部分 Q1 提交，记录持久保存在本文件和 WORK_LOG。

## 全局约束

- 入口 app.main:app；业务代码在 q1_api/app，测试在 q1_api/tests。
- Q2/Q3 功能不提前实现；本轮不加 OCR、导出表或审批端点。
- DATABASE_PATH 在应用启动时读取，默认项目 data/verification.sqlite3；导入模块不创建数据库。
- 接收 UTF-8/BOM；六列精确匹配（允许重排）；拒绝重复/缺失/多余表头，结构错误不写库。
- 空白物理行忽略；非空数据行计入 total_rows；多行引号字段记录起始物理行号。
- 首次出现非空 case_ref 占位，后续重复拒绝；唯一范围为批次内，重复上传创建新批次。
- response 含 id/created_at/total_rows/accepted_count/rejected_count/rejected_rows/status_counts；所有状态计数含零。
- 错误 detail 含 code/message，文件错误可含 row；拒绝项为 row/case_ref/errors，errors 为 field/message 列表。

## Review Focus

1. 文件尾部列数错误不得留下前半批次（任务 1/2）。
2. 多行字段和空白行不得把纠错行号指错（任务 1）。
3. 同批次唯一性/外键/非空须由 DB 保底，写到一半异常须回滚（任务 2）。
4. 读取未知批次/非法分页必须返回清楚的 404/422，不产生空假批次（任务 2）。
5. 上传体积与 csv 单字段上限须转换成 4xx，不能内存无限增长或返回内部堆栈（任务 1/2）。

## 任务 1：HTTP 导入与校验（RED → GREEN）

Files: 新增 q1_api/app/models.py、imports.py、db.py；修改 main.py；新增 tests/conftest.py、test_imports.py。

Interfaces: `parse_csv(content: bytes) -> ParsedImport`，包含 accepted（行号和六字段）/rejected；`Database.import_batch(parsed: ParsedImport) -> dict` 在单事务内保存后返回详情。状态枚举只定义八个合法状态，不实现审核转换。

- [x] 编写原样本上传测试，断言 201、43/7、拒绝行和字段；对旧 app 实测预期 404。
- [x] 参数化 UTF-8/BOM/空格、日期、空字段、重复、空/缺列/列数损坏/坏编码/坏引号/过长字段/超限文件用例；真实临时 DB，无数据库替身。
- [x] 实现解析：csv.reader(strict=True)，逐行收集错误，合格行归一化；先解析完成才调用 DB。业务日期检查正则后 date.fromisoformat。
- [x] 实现表：batches 的 total=accepted+rejected CHECK；cases 外键、唯一(batch_id,case_ref)、合法状态 CHECK。初态 pending。
- [x] 实现 multipart file 端点，最多读取 2 MiB+1，超限 413；文件错误 400；缺少上传 422。同步路由由 FastAPI 工作线程执行阻塞 SQLite/解析。
- [x] 运行 `.venv/bin/python -m pytest q1_api/ -q`，预期新导入测试与骨架均通过。

首个测试合约：
```python
response = client.post('/api/batches', files={'file': ('applications.csv', sample, 'text/csv')})
assert response.status_code == 201
assert response.json()['accepted_count'] == 43
assert response.json()['rejected_count'] == 7
assert [r['row'] for r in response.json()['rejected_rows']] == [6, 13, 18, 19, 28, 41, 48]
```

## 任务 2：持久化读回、列表与事务保护

Files: 扩展 db.py/main.py；新增 tests/test_batches.py、test_database.py。

Interfaces: `Database.get_batch(batch_id: str) -> dict | None`；`Database.list_cases(batch_id: str, status: CaseStatus | None, page: int, page_size: int) -> dict | None`。GET 返回稳定 row_number 排序、过滤后 total 和分页元信息。

- [x] 写失败测试：批次详情等于创建响应；过滤状态含零；跨页无重复；缺批次 404；非法状态/分页 422；重新启动应用仍能读回。
- [x] 实现两 GET 路由和 SQL；COUNT/SELECT 使用一个短读事务保证同一快照；参数全部绑定，不拼接用户值。
- [x] 使用独立测试 DB 注入 SQLite trigger 在第二个案件 INSERT abort，验证 batches/cases 都为 0；另测唯一/外键/必填 CHECK，不在应用添加测试入口。
- [x] 用真实 SQLite 写锁验证有界失败返回 503（含 Retry-After）；结构损坏失败不创建批次。
- [x] 运行整套 pytest，保存 RED/GREEN 数量与原因，再独立自审一次差异和边界。

持久化验收示例：
```python
batch_id = response.json()['id']
assert client.get(f'/api/batches/{batch_id}').json()['status_counts']['pending'] == 43
page = client.get(f'/api/batches/{batch_id}/cases?page=2&page_size=10').json()
assert page['total'] == 43
assert len(page['items']) == 10
```

## 收尾

- [x] 原生 localhost 启动隔离实例，HTTP 上传原样本和查询；实际进程重启后读回。
- [x] 写入 q1_api/README 的配置、API、行号/重复/限制约定；REFLECTION 只填已实施事实，保留未理解项待 Alex 核对。
- [x] 更新 PROJECT_PLAN/WORK_LOG/AI_LOG/TOOLS；保留完整测试证据与当前 Git 状态。
- [x] 本轮不声明 Q1 完成；T2 通过后合并本轮改动一起作一次 Q1 模块提交。


## 2026-10-13 执行结果

已按用户明确授权在本会话实施 T1，未另启代理或创建新 worktree。
初始测试存在一处 helper 重复 case_ref 参数错误，先修正测试；正确 RED 为 37 failed / 1 passed（端点缺失）。实现后 38 passed，补充实际 DB 约束/回滚/锁/并发检查后 47 passed，加原生进程重启后 48 passed。补充存储保护检查是验证现有实现，不将它们宣称为逐个 RED→GREEN。

局部自审发现 quoted-empty 被误跳过，以及 NUL 触发 SQLite CHECK 返回 500；新增三例明确 RED，再分别修正，最终 51 passed / 1 dependency warning。真实 API 两进程重启检查通过。证据见 q1_api/evidence；没有独立代理审查，不冒称已有独立审查。

决定：本轮只完成 T1 并保留工作区，待 T2 完成后按原题做一次 Q1 模块提交。现有 Starlette/httpx 弃用提示保留，不为消除提示引入暂未使用的依赖。明确本轮尚未做干净克隆/CI验收，完整 Q1 仍待审核状态机。

## T2：两级审核与 Q1 模块收尾（2026-10-13）

用户明确要求继续并统一提交 Q1。以下扩展先前仅 T1 的范围，不重复请求授权。

Files: 新增 app/workflow.py 和 tests/test_review.py；扩展 models.py、db.py、main.py；更新 README/REFLECTION/证据与过程文档。

接口：`next_review_status(current: CaseStatus, stage: int, decision: str) -> CaseStatus` 仅允许四个转换，其余抛 `InvalidTransition`。`Database.review_case(case_id, stage, decision)` 在一个 BEGIN IMMEDIATE 事务内读取当前状态、判定、按旧状态 UPDATE、追加 review 记录并返回案件及该次审核信息；未知案件返回 None。

数据：新增 reviews 表保存 case_id/stage/decision/previous_status/new_status/reviewed_at；与案件状态原子提交。历史记录不强加“每个 stage 终身只能一条”的约束，以免未来返回一级审核的修改需要抹掉历史；目前重复审批由状态规则阻止。

- [x] 写 8 个状态 × 2 个 stage × 2 个 decision 的独立预期矩阵测试，实际执行确认缺少路由时失败。
- [x] 加成功两级路径、两类拒绝终止、404、严格类型/未知字段 422、同阶段重复/改判、审计持久化测试。
- [x] 加 barrier 同时发起的同意/同意、同意/拒绝并发测试（两级都测），要求一个 200、一个 409、仅一条审核记录；审核记录 INSERT 故障必须回滚状态变化。
- [x] 实现规则表、请求/响应模型、事务性存储及 POST /api/cases/{id}/review；失败原因可见。
- [x] 全套根目录 pytest 通过，补真实原生进程审核及重启验证；独立审查 Q1 全部变化并修复实质问题。
- [x] 对照 Q1.1/Q1.2/Q1.3 逐项留证；由将提交的文件构建干净快照并验证，再统一提交 Q1，最后对实际 commit 做干净克隆测试。

本轮 Q1 成功生命周期在测试中用明确数据库 fixture 建立 ocr_done 前置状态；OCR 实际 HTTP 和导出仍是 Q2，不增加生产跳过 OCR 的入口。


### T2 结果与审查

56 个初始审核测试失败（缺少端点及 reviews 表），实现后全套 107 passed；新增真实进程审核检查后 108 passed。独立审查复现 NaN/Infinity/非法 Unicode 的框架错误响应序列化问题（P2），四项回归先失败，修复后全套 **112 passed**。保留一个 Starlette/httpx 依赖弃用提示。

独立 reviewer 也实测旧 T1 数据库增建 reviews 后案件不丢失、可继续审核；无 Critical 问题。修正过时 fixture 注释。审核状态机、审计回滚和并发测试通过；P2 修复通过新回归和全套验证，不以第二次审查替代测试。

Pydantic 2 API 是直接依赖，requirements 补充 pydantic>=2.0，防止依赖环境仍保留 v1；它原已随 FastAPI 安装，未另加新技术栈。新建隔离目录和 Python 环境，仅从仓库 requirements 安装，最终同一代码快照 **112 passed, 1 warning in 3.83s**。正式 Q1 提交后再克隆实际 commit 验证并记录本机结果，避免为记录 commit hash 本身新增 Q1 提交。

审查范围决定：Q2/Q3 和未来状态迁移留到对应模块；本轮已额外完成旧 T1 DB 兼容性和新环境快照验证。不能用这些验证宣称 Alex 已理解代码，也不能宣称 Linux CI/整个系统验收完成。详细评分核对见 q1_api/evidence/Q1-ACCEPTANCE-20261013.md。
