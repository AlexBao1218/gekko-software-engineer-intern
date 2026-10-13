# Q2 OCR 接入与恢复实施记录

基线 `c6b8f9d`，2026-10-13。用户授权继续 Q2.1/Q2.2，使用子 agent，并强调迟到 OCR 结果不得覆盖人工审批。此文保留 OCR 阶段记录；后续 Q2.3 已完成，见 [交付实施记录](Q2_DELIVERY_IMPLEMENTATION.zh-CN.md)。不修改 Q1 审批业务规则。

## 设计和任务

1. 独立 OCR 客户端：一次请求一个总 deadline，HTTP 各阶段也设置 timeout；校验五个结果字段；400/422 不重试，500/网络/超时有限重试；保存可见原因。
2. 存储：cases 追加 OCR 字段，不覆盖原申请字段；ocr_runs 保存批次运行状态，ocr_attempts 保留每次已观测结果。以 UUID run token 防止旧任务回写，最终结果与尝试记录同事务。
3. 后台调度：生命周期管理的 asyncio tasks，注册锁防止同批重复任务；全局串行处理，最多四个活动/排队批次，超出返回 429。单 Uvicorn worker 为支持边界。网络等待在 DB 事务外，SQLite 调用放入工作线程。
4. 恢复：处理中保留 pending 或原可重试 ocr_failed；重启将旧运行标为 interrupted，普通 process 重新领取可处理案件。成功/审批/拒绝/导出状态不重新领取；回写还必须匹配状态及 token。异常退出的活动任务留下可见错误/可重试失败，不能悄悄丢弃。
5. 测试：受控 HTTP 故障、后台响应、重复触发、重试/耗尽、旧响应保护、迁移、任务异常及取消；真实 mock 原生端到端、杀进程重跑和完整字段比较。
6. 测量：默认 mock 的 seeds 42/7/99，比较四/六次总尝试；报告时延、请求级失败、案件级结果和有限恢复轮数，不用配置概率代替观测。

客户端最终默认值为 timeout=1s、六次总尝试、退避0.1s/上限0.5s；测量、选择理由及未观察到的收益写入正式说明。每案网络等待上限为 attempts×deadline 加有上限的退避之和；SQLite 调用另有1s锁等待上限。无无限重试。

## 接口约定

- POST /api/batches/{id}/process → 202，返回批次详情及最新 ocr_run；未知404、容量满429。
- GET批次包含 ocr_run（id/status/error/total/completed/created_at/finished_at）；现有状态数量实时可查。
- GET案件追加 ocr_fields/ocr_error/ocr_error_code/ocr_retryable/ocr_attempts；不修改原六列。
- 普通 process 处理 pending 和可重试 ocr_failed；400/422/无效服务结果不会自动反复请求。
- 记录 HTTP 尝试结果不等于保证记录被 kill 瞬间的在途请求；真实 `/debug/requests` 用来核对差异。

## 分工与进度

- [x] 合同/验收只读 agent 核对：强调 token+原状态、任务异常可见、线程取消不等于 DB 调用取消、同 seed 不保证并发顺序一致。
- [x] 客户端 agent：ocr_client.py 及其独立测试，先 RED 后实现。
- [x] 主线程：存储、迁移、调度、端点、保护审批状态的测试。
- [x] 原生验证 agent：原始 mock 测量及真实杀进程恢复脚本/测试。
- [x] 主线程集成、完整回归、独立审查、修复、更新文档与证据。

## Git 与范围

沿用干净的 codex/assessment-implementation；保留历史和原题/mock。Q2 是一个模块，OCR部分完成后保存工作树和证据，待 Q2.3 完成后统一模块提交，不提前宣称整个 Q2 完成。不 push、不提交真实数据或环境凭证。

## 执行记录

前置接口检查：客户端只负责一次尝试；调度器负责有限循环与历史；存储负责原子校验/回写。三者共享 OCRResult，但不共享网络会话或长事务。不同 agent 只写各自文件，主线程负责集成。

最终结果：客户端100项、调度/存储29项、原生OCR2项，加原Q1的157项，全套288通过；新环境同样288通过。三组默认seed的冻结源码恢复比较通过，seed42需两轮，另外两组一轮；保留seed42单轮耗尽，不声称六次必成功。

独立审查指出触发请求取消时DB线程可能留下running孤儿；回归先失败，再使用shield等待领取并明确标interrupted后通过。主线程另修复DB锁住时无法持久化任务错误的协调路径；客户端补异常Unicode及Python3.10 asyncio.TimeoutError兼容。详见WORK_LOG和q2_integration/evidence/OCR-ACCEPTANCE.zh-CN.md。

决策：六次是有代价的保守有限预算；本次样本没有观察到相对四次的终态改善，不宣传测量证明六次更好。错误落库失败时，下一次成功批次GET/process会协调；进程丢失时由重启标记interrupted。单worker和人工有限重跑是明确边界，不提供分布式任务保证。
