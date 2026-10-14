# Gekko 项目启动指南

以下命令从**你自己的克隆目录根目录**开始执行，不依赖作者电脑路径或未提交的辅助脚本。Q1、Q2、Q3 和 CI 已实现；候选人个人反思与理解核验仍待本人完成。原始题目及评分以 [README](README.md) 为准。

## 1. 先看项目做什么

系统把 CSV 申请导入批次，后台调用 OCR，依次进行两级审核；只有二级通过的案件能导出，并用同一个幂等键重试开户交付，避免丢响应后重复入账。Docker 版本提供 nginx、持久化、健康检查及备份恢复。

| 入口 | 内容 |
|---|---|
| [Q1](q1_api/README.md) | CSV 校验、批次查询、审核规则与错误响应 |
| [Q2](q2_integration/README.md) | OCR 配置、重试恢复、导出交付 |
| [Q3](q3_deploy/README.md) / [Runbook](q3_deploy/RUNBOOK.md) | Docker 启动、日志、故障排查及恢复 |
| [验收对照](ACCEPTANCE.zh-CN.md) | 评分项与证据 |
| [证据脱敏说明](EVIDENCE_PRIVACY.md) | 原始证据、路径替换及哈希的含义 |

环境初始化的历史经过保留在 [ENVIRONMENT_REPORT](ENVIRONMENT_REPORT.zh-CN.md) 和按日期的 [WORK_LOG](WORK_LOG.zh-CN.md)，它们不是当前启动指令。

## 2. 原生运行：Python + 两个 mock

已验证的 Python 版本是 3.12。第一次在根目录创建隔离环境并安装依赖；已有环境可直接复用：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest q1_api/
```

分别打开三个终端，**每个终端都进入同一个项目根目录**：

```bash
# 终端 1：原题 OCR mock，默认端口 9100
.venv/bin/python mock_ocr/server.py
```

```bash
# 终端 2：原题开户 mock，默认端口 9200
.venv/bin/python mock_onboarding/server.py
```

```bash
# 终端 3：单 worker API，端口 8000
.venv/bin/python -m uvicorn app.main:app --app-dir q1_api --host 127.0.0.1 --port 8000
```

原始 mocks 默认监听所有网卡；这里只作为本地开发服务使用。故障概率与种子保持原题默认值，因此 OCR 失败不一定是部署错误。API 的 OCR/交付地址默认指向本机 9100/9200。不要加多个 worker；需要覆盖配置时见 Q2 文档。

另开终端，从根目录验证：

```bash
curl --max-time 5 -f http://127.0.0.1:9100/online
curl --max-time 5 -f http://127.0.0.1:9200/online
curl --max-time 5 -f http://127.0.0.1:8000/api/ready
curl --max-time 5 -f -X POST http://127.0.0.1:8000/api/batches \
  -F 'file=@data/account_applications.csv;type=text/csv'
```

样本结果应为 50 行、43 接收、7 拒绝。打开 `http://127.0.0.1:8000/docs` 查看 API；用返回的批次 ID 按 Q2 指南继续 OCR → 两级审核 → 导出。原生 API 默认不自动 seed，数据库存放在项目 `data/verification.sqlite3`；可通过 `DATABASE_PATH` 覆盖。三个服务分别按 Ctrl+C 停止，数据库保留。

可自动执行原生完整流程、丢响应及强制终止恢复实测（脚本自行管理独立服务/端口，输出目录必须不存在）：

```bash
.venv/bin/python q1_api/tests/delivery_experiment.py --output .gekko-setup/native-check-1
```

这里 `.gekko-setup` 只是运行时生成的证据目录；执行脚本本身已经在 Git 中。

## 3. Docker 运行：不需要本机 Python

需要可工作的 Docker Engine 和 Compose。在本项目原开发 Mac 使用专用 context：`export DOCKER_CONTEXT=colima-gekko`；其他机器使用自己的 Docker 配置，不必创建同名 context。

```bash
cd q3_deploy
cp .env.example .env   # 仅首次；已有 .env 时保留自己的配置
docker compose up --build -d --wait
curl --max-time 5 -f http://localhost:8080/api/ready
bash scripts/smoke_test.sh
```

应有 `api`、`mock_ocr`、`mock_onboarding`、`nginx` 四个 healthy 服务。readiness 返回 seed 批次 ID；smoke 上传另一个批次并走完整交付流程，最后输出 `"pass": true`。默认只向本机暴露 nginx 的 8080 端口。端口有占用时，修改 `.env` 的 `NGINX_PORT`，并调整 curl 地址。

```bash
docker compose ps
docker compose logs --tail=100 api mock_ocr mock_onboarding nginx
bash scripts/backup.sh
docker compose down   # 停止服务，保留数据库和备份卷
```

恢复前必须按 [Runbook](q3_deploy/RUNBOOK.md) 停止 API 并核对快照后的远端交付。不要对需要的数据执行 `down -v`。备份保留最近七次成功操作；时钟回拨时文件名时间戳会继续递增，可能暂时领先墙上时间。

## 4. 当前交付状态

当前保持私有和发布冻结，只做本地提交；不要按旧过程记录中的 push 计划执行。测试与 CI 的历史通过不代表将来修改后的新 SHA 已通过。最终个人反思、解释/修改代码的核验及重新公开/邮件提交仍由候选人决定。

`.env`、虚拟环境、数据库、备份和本机原始记录都不提交。公开可读的历史日志采用标明规则的路径脱敏副本，详见证据说明；Git 旧历史未被改写。
