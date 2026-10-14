# Q3 T6 部署、持久化与健康检查验收

2026-10-14，基线 `1a51d4acbd1fe7572883a767a8f937e23077da2b` 加本轮 T6 源码。Q3.1/Q3.2 已验证；Q3.3 备份、恢复及最终整模块提交尚待完成。下面是行为证据，不代替出题方评分。

| 评分项 | 分值 | 实际通过证据 |
|---|---:|---|
| Q3.1 容器部署 | 12 | 固定四服务构建/启动，非root API，SQLite命名卷，新卷首启seed43/7，两路nginx代理，完整smoke，强制换IP与整套重启后数据一致 |
| Q3.2 健康/配置/顺序 | 8 | liveness与readiness分离，healthcheck/service_healthy真实启动，环境配置，无固定startup sleep，DB只读/目录不可写503及Docker unhealthy→healthy |
| Q3.3 备份/手册 | 10 | **待做**；仅已实现并验证启动/日志/OCR诊断部分，backup.sh仍是占位文件 |

## 测试与平台

- 本机：`python -m pytest q1_api/`，**450 passed, 1 warning in 70.08s**；[输出](t6-pytest-macos-20261014.txt)。
- Linux容器：Python **3.12.15**，Linux/arm64，UID **10001**；同一完整命令 **450 passed, 1 warning in 80.76s**；[输出](t6-pytest-linux-20261014.txt)、[环境](t6-linux-environment-20261014.json)。
- Linux验证把所需源码复制到独立容器目录，清除PYTHONPATH/数据库路径/seed及外部端点覆盖，确保测试使用这份源码及临时数据库，不污染运行中的业务库。
- 411项原Q1/Q2 + 15项seed + 14项readiness + 10项smoke。保留现有Starlette/httpx弃用警告；不把Python3.10语法/依赖元数据检查当成实际3.10执行。
- 七项直接依赖固定到Q2已验证版本，本轮在Linux重新安装通过；未新增应用依赖。镜像仍使用原骨架的Python3.12-slim/nginx1.27-alpine标签，未声称实现全供应链digest锁定。

## 独立快照、全新卷的部署演练

执行：

```bash
.venv/bin/python q1_api/tests/deploy_experiment.py \
  --context colima-gekko \
  --output q3_deploy/evidence/t6-deployment-20261014
```

脚本建立排除本地运行数据/环境的独立源码快照，复制`.env.example`生成当地配置；这是基线加未提交T6工作，不冒称已有Q3提交。生成专属项目 `gekko-check-461270ce27a0`、新卷及本机端口65380，不使用旧部署的数据。完成后清理本次项目/卷/占位容器；未操作其他项目。全部九个阶段通过，整体169.001s，cleanup_errors为空。

| 阶段 | 实际结果 |
|---|---|
| 新卷构建/启动 | 四服务healthy；仅nginx发布127.0.0.1端口；API UID10001；seed43接受/7拒绝 |
| 代理路由 | `/ocr/parse`真正返回mock身份字段；API分页query保留（page2/page_size3） |
| 默认故障smoke | 新批次43/7，OCR37done/6failed；选成功案件走两审/导出。两次交付请求、一份ingestion，回执deduplicated=true |
| 强制更换API IP | 从172.19.0.4改为172.19.0.6，nginx容器ID不变，重新解析恢复访问 |
| 整套down再up | 同一命名卷，seed ID不变，九张业务表完整逐行相同；已delivered文件不重发 |
| 大小边界 | >1MiB坏CSV进入API返回400 invalid_csv；2MiB+1 CSV返回API JSON413；3MiB+1请求体返回nginx HTML413；均不改变业务数据 |
| 数据库故障 | DB0444及目录0555分别ready503/health200；文件故障维持到Docker unhealthy；权限恢复后ready200/healthy，全部数据相同 |
| 代理超时 | mock强制8s延迟，直接OCR请求**5.07s**返回504；debug只一条hang，nginx未重放POST；随后恢复原配置 |
| 清理/源码核对 | 测试资源清理成功，原工作树与快照的53个源文件各自前后SHA一致 |

OCR的6个失败包括5个不可读扫描和1个可读请求耗尽，不掩盖为“全部OCR成功”。smoke验证成功案件的完整生命周期；故障重跑语义另由Q2证据覆盖。

持久化比较涵盖 `batches`、`cases`、`reviews`、`ocr_runs`、`ocr_attempts`、`exports`、`export_cases`、`delivery_attempts`、`seed_imports`：2批、86案、2条审核、1份CSV/回执、2次交付尝试，比较完整字段和BLOB字节，而非只比较行数。

完整原始资料：[report.json](t6-deployment-20261014/report.json)、[smoke](t6-deployment-20261014/smoke.json)、[原始数据](t6-deployment-20261014/business-before.json)、[API换IP后](t6-deployment-20261014/business-after-api-replacement.json)、[整套重启后](t6-deployment-20261014/business-after-full-restart.json)、[故障后](t6-deployment-20261014/business-after-readiness-faults.json)、[单次hang记录](t6-deployment-20261014/ocr-hang-requests.json)。同目录另存构建/服务日志、容器信息、每条命令输出和前后源码清单。首轮构建/smoke记录单独保留，不覆盖正式验收。

## 特殊情况与边界

- seed并发初始化只建一个批次；插入中断整批回滚；源文件变更/不存在/非法明确失败；原审核、导出和回执不重置。
- readiness缺文件不会创建空库；空库、损坏、缺业务表、只读文件、不可写journal目录、写锁均非ready。正常探测回滚，不留表、不改业务记录。
- 独立审查发现少查三张辅助表，实际RED复现后修复，14项readiness现已通过。
- startup `service_healthy`只控制启动依赖；运行中unhealthy不会自动修复或重启服务。外部mock的可用性与DB readiness分别看。
- nginx的5秒是读/写空闲超时，不是任意响应的总耗时上限；更换容器期间可短暂502，DNS刷新后恢复。
- 整套重启会清空原始onboarding mock的内存去重/回执表。该阶段仅证明本地数据保存及已交付不重发，不证明mock跨重启去重；Q2未知结果演练一直保留同一mock。
- 单worker为支持范围。未验证真实断电、数据库损坏后的修复、持续负载或多实例队列。
- 实验资源已清理；运行方法见[README](../README.md)和[RUNBOOK](../RUNBOOK.md)。正式项目使用既定卷时不能执行测试清理的down -v。

## Git和下一阶段

保留Q1/Q2提交历史，T6代码/证据在当前功能分支未提交。按已批准“每完整模块一次提交”，接下来完成在线SQLite备份、至少9份生成/保留最新7份、隔离恢复数据核对和最终Q3干净环境验收，再统一提交。没有push、公开fork或申请提交。
