# Gekko 考核启动指南

工作目录：`/Users/asuna/Desktop/项目/Gekko`。2026-10-12 已复核现有环境，可启动 API 骨架。后续按“全部底层配置”的要求补齐 pytest 导入路径；没有安装新工具或实现考核功能。

## 1. 先读当前题目

当前题目是**证券账户身份核验**，不是旧版驾照续期题目。官方来源：[software-engineer-intern](https://github.com/Anthony-Gekko/software-engineer-intern/tree/d17c972e811d36f92f9dd43506aff836785a7501)，本次修订 `d17c972e811d36f92f9dd43506aff836785a7501`。

依次读 `README.md`、`mock_ocr/README.md`、`mock_onboarding/README.md`。原文与完整仓库快照保存在 `.gekko-setup/README.original.md` 和 `.gekko-setup/upstream-d17c972.tar`。

- Q1（35 分）：CSV 校验、批次/案件 API、两级审核状态机及测试。
- Q2（35 分）：后台 OCR、超时/有限重试/恢复、导出和幂等开户交付。
- Q3（30 分）：容器、nginx、数据库 readiness、配置、备份和 runbook。
- 加分（10 分）：外部 API 或 CI，选一个。建议投入 8–12 小时。

保留固定入口 `q1_api/app/main.py`、固定服务名称及部署文件布局。Q2 扩展同一个 API。样本是 `data/account_applications.csv`，仅使用合成数据。SQLite 可接受，MongoDB 推荐；当前 SQLite 已验证，MongoDB 未安装，最终选择和理由由 Alex 写入 `REFLECTION.md`。

## 项目目录

| 目录 | 用途 |
|---|---|
| `q1_api/app/` | API 入口和待实现的业务模块；Q2 继续扩展这里 |
| `q1_api/tests/` | 测试，当前只有上游导入检查 |
| `q2_integration/` | Q2 集成说明，不是第二个 API 服务 |
| `q3_deploy/` | Docker/Compose、nginx、环境示例、备份、smoke test 和 runbook 模板 |
| `mock_ocr/` | 官方 OCR mock，包含刻意设置的故障 |
| `mock_onboarding/` | 官方开户交付 mock，包含幂等键合约 |
| `data/` | 50 条合成申请 CSV，包括脏数据 |
| `.venv/` | 本项目隔离 Python 环境，不提交 |
| `.gekko-setup/` | 本机环境辅助入口、依赖锁、原题快照和验证证据，不提交 |

## 2. 最简单的开始方式

在 Terminal 执行：

```bash
cd '/Users/asuna/Desktop/项目/Gekko'
.venv/bin/python -m uvicorn app.main:app --app-dir q1_api --host 127.0.0.1 --port 8000 --reload
```

打开 `http://127.0.0.1:8000/docs`；健康检查是 `http://127.0.0.1:8000/api/health`。按 Ctrl+C 停止。骨架目前只有健康端点。

## 3. 本机 mocks 和测试

另开两个终端，各自先进入上述项目目录：

```bash
.venv/bin/python .gekko-setup/run_mock.py ocr
```

```bash
.venv/bin/python .gekko-setup/run_mock.py onboarding
```

这是环境辅助入口：加载原始 mock 代码，仅将监听限制到 localhost；原始故障概率、种子和环境变量仍有效。直接运行上游 `server.py` 会监听所有网卡。端口为 9100 / 9200，Ctrl+C 停止。

先用有超时的请求观察 mock：

```bash
curl --max-time 5 -sS http://127.0.0.1:9100/online
curl --max-time 5 -sS -X POST http://127.0.0.1:9100/ocr/parse \
  -H 'Content-Type: application/json' \
  -d '{"case_id":"SETUP-TEST-0001","image_ref":"id_scan_0001.jpg"}'
```

在项目根目录测试：

```bash
.venv/bin/python -m pytest q1_api/
```

已在清除 `PYTHONPATH` 的环境中验证 **1 passed**。`q1_api/pytest.ini` 增加 `pythonpath = .`，不再需要临时变量。当前仅有上游 import 测试；真实业务测试仍待开始作业时编写。原始配置保留在题目快照中。

一键重复本机环境验证（8000/9100/9200 需空闲，验证进程会自动停止）：

```bash
.venv/bin/python .gekko-setup/verify_native.py
```

这只检查骨架和 mock 合约，不是最终作业端到端测试，也不能代替真实故障率测量。

## 4. Docker 使用方式

实际使用 **Colima → macOS Virtualization.Framework 的 ARM64 Linux VM → Docker Engine**；Mac 端是 Docker CLI 和 Compose，没有使用 Docker Desktop。专用 profile 为 `gekko`，2 CPU / 3 GiB RAM / 10 GiB 数据盘。本轮结束时 VM 仍运行，容器已停止。

使用独立 Colima profile，避免改变其他项目的默认 Docker context。在需要容器的终端执行：

```bash
colima -p gekko start --activate=false
export DOCKER_CONTEXT=colima-gekko
docker version
docker compose version
```

可重复环境 Compose 检查：

```bash
docker compose -p gekko-env-smoke -f .gekko-setup/compose.smoke.yml up --build -d --wait
curl --max-time 5 -sS http://127.0.0.1:19100/online
curl --max-time 5 -sS http://127.0.0.1:19200/online
curl --max-time 5 -sS http://127.0.0.1:18080/
docker compose -p gekko-env-smoke -f .gekko-setup/compose.smoke.yml down
```

`18080` 是测试 nginx，`19100` / `19200` 是测试 mocks，均只绑定 `127.0.0.1`。nginx 测试的是默认欢迎页和配置语法，两个 mock 没有 healthcheck；它们的可用性由实际 HTTP 请求确认。这份 Compose 只验证环境，**不能作为 Q3 提交**。原 `q3_deploy/` 仍含 TODO，完成后才使用其正式启动命令。正式开发时可将 nginx 端口写为 `127.0.0.1:8080:80`。

不用容器时：`colima -p gekko stop`。没有配置开机启动。关闭本终端的 Docker 选择：`unset DOCKER_CONTEXT`。

## 5. 依赖与提交准备

`.python-version` 固定 Python 3.12.14；`.venv` 使用独立解释器，原有默认 Python 3.13.4 不变。依赖已安装，日常启动无需重建虚拟环境或重复安装。原 `requirements.txt` 未修改，本机锁文件是 `.gekko-setup/requirements.lock`（含 PyPI 哈希）。该锁按 macOS arm64/Python 3.12 生成；Linux/容器的完整应用依赖需另行验证。

检查依赖兼容性：`uv pip check --python .venv/bin/python`。如希望使用普通 `python` 命令，可先执行 `source .venv/bin/activate`，结束时执行 `deactivate`。

AI 可以辅助，但 Alex 必须能解释、修改所有提交代码。`TOOLS.md`、`AI_LOG.md` 已附本次环境协助事实；`REFLECTION.md` 保留模板，不能把环境检查当成已实现功能或自己的设计反思。提交前亲自填写三份记录，每模块清楚提交一次，不 force-push。

题目要求最终 public fork、干净克隆验证、邮件发送仓库 URL 与最终 commit hash。本轮没有 fork、commit、push 或邮件。上游没有 `.gitignore`，已补最小忽略文件，避免本地环境/数据库/日志及 `.env` 被提交。

题目写一周内提交；具体起算时间以招聘方通知为准。本轮仅环境交接，不执行提交流程。

本轮证据：`.gekko-setup/final-check-20261012/`。通过、失败、未运行的完整清单及文件改动见 `ENVIRONMENT_REPORT.zh-CN.md`。没有管理员密码、新协议、付费或敏感权限阻塞。
