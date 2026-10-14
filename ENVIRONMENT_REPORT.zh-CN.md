# Gekko 开发环境验证报告

初次核验：2026-10-12 21:33:08 HKT；续轮运行状态核验：21:42:39 HKT。项目路径：`<WORKSPACE>`。续轮复用现有安装，只完成环境与交接，考核业务尚未实现。

## 作业与原始文件

- 官方仓库：<https://github.com/Anthony-Gekko/software-engineer-intern>。
- 本机 HEAD 与核验时远端 HEAD 均为 `d17c972e811d36f92f9dd43506aff836785a7501`；当前题目为证券账户身份核验。
- 已读取完整主 README、Q1/Q2/Q3 说明及两个 mock 合约。仓库及所检查的父目录未发现适用 `AGENTS.md`、`.agents` 或仓库技能文件。
- 原主文档：`.gekko-setup/README.original.md`；完整原始仓库：`.gekko-setup/upstream-d17c972.tar`。
- 原始 tar 的 SHA-256：`5986874efc1fa30e4bc78f8e69068c08ecbfa83e2a1942092ffe0e46c4177c01`。
- 已逐字节对照 Git 验证 README、requirements、REFLECTION、API/测试骨架、原始 mocks 及 Q3 主要配置未改。详见 `.gekko-setup/source-verification.json`。
- `origin` 仍指向官方仓库。没有 fork、提交、push、邮件、账号或付费服务配置。

## 工具状态

| 工具 | 实测版本与状态 |
|---|---|
| Mac | Apple Silicon arm64，macOS 26.5.1，16 GiB RAM，8 CPU |
| Git | 2.50.1（Apple Git-155），复用 |
| 原有 Python | 3.13.4，保持原安装与默认命令 |
| 项目 Python | 3.12.14，uv 管理，`.venv/bin/python`；`.python-version` 固定 |
| uv | 0.12.17，Homebrew |
| Docker CLI | 29.8.1 |
| Docker Engine | 29.5.2，Linux arm64，已运行及验证 |
| Docker Compose | 5.5.1，CLI 插件可用 |
| Docker Buildx | 0.37.1，实际 mock 构建成功 |
| Colima / Lima | 0.10.3 / 2.2.0，专用 `gekko` profile |
| 容器 Python | 原始 mock 镜像内实测 3.12.15，与本机同属 Python 3.12 系列 |
| SQLite | 项目解释器内 3.53.1；合成数据内存事务读写通过 |
| MongoDB | 未安装、未运行、未验证；最终数据库选择留给 Alex |

本机依赖通过原 `requirements.txt` 解析，未修改原要求；带哈希锁文件位于 `.gekko-setup/requirements.lock`，完整实装版本在 `.gekko-setup/installed-packages.txt`。以下为主要依赖：

| 依赖 | 版本 |
|---|---|
| fastapi | 0.143.0 |
| uvicorn | 0.54.0 |
| httpx | 0.28.1 |
| python-multipart | 0.0.32 |
| pytest | 9.1.1 |
| pytest-asyncio | 1.4.0 |
| pydantic | 2.14.1 |
| starlette | 1.7.0 |

`uv pip check` 返回兼容。实际导入通过。Starlette TestClient 使用 httpx 时出现弃用提示，但当前 API 健康检查通过；没有为消除提示添加未使用依赖。锁文件按当前 macOS arm64/Python 3.12 解析，其他平台须另行验证。

## 实际 Docker 运行环境

使用开源 [Colima](https://github.com/abiosoft/colima) 在 macOS Virtualization.Framework 中运行 Linux VM，VM 内是 Docker Engine，Mac 端使用 Docker CLI 和 Compose；没有安装 Docker Desktop。选择此方式便于控制资源，并复用 Homebrew。

配置：2 CPU、3 GiB RAM、8 GiB 系统盘、10 GiB 数据盘；磁盘为按实际写入增长，容量上限不是立即占用。仅将 Gekko 目录共享给 VM，使用 ARM 镜像，没有安装 Rosetta、启用 Kubernetes、转发 SSH agent、修改 `.zshrc` 或配置登录自动启动。未生成主机 `~/.ssh/config` 条目；Lima 自身有内部 VM 通信密钥，没有创建外部账号或保存外部访问凭据。

默认 Docker context 仍为 `default`。本项目明确使用 `--context colima-gekko`，或在当前终端设置 `export DOCKER_CONTEXT=colima-gekko`。socket 是 `~/.colima/gekko/docker.sock`。

首次镜像拉取失败原因：VM 的 `/etc/resolv.conf` 是失效链接，dnsmasq 未配置，符合 [Colima #1611](https://github.com/abiosoft/colima/issues/1611)。依据 [v0.10.3 官方 setupDNS 源码](https://github.com/abiosoft/colima/blob/v0.10.3/environment/vm/lima/dns.go) 在专用 VM 内补上内部 DNS 配置，原链接另存 `/etc/resolv.conf.gekko-original`。另限制 DNS 不监听 loopback，避免额外主机 53 端口转发。没有改 Mac DNS、代理设置、TLS 验证或防火墙。VM 重启后解析及 hello-world 通过。

## 检查结果

| 检查 | 结果 | 证据或限制 |
|---|---|---|
| 虚拟环境、依赖导入与兼容性 | 通过 | Python 导入 FastAPI/Uvicorn/httpx/pytest/multipart 等；`uv pip check` |
| 原 API 健康端点 | 通过 | HTTP 200，`{"status":"ok"}` |
| 本机 OCR mock | 通过 | `/online` 200、合成解析 200、刻意故障 500、badscan 422 |
| 本机开户 mock | 通过 | 默认 seed 7 首次 2 秒超时后重发同键，返回去重回执；唯一 ingestion，2 attempts |
| SQLite 基础事务 | 通过 | 合成案件键写入、提交、读回 |
| Docker daemon / 最小容器 | 通过 | 续轮 DNS、Docker Hub 查询及 hello-world 成功；上轮重启后证据保留，本轮未重启 VM |
| 原 mock Dockerfile 构建 | 通过 | Buildx 构建两个原始 Dockerfile，未修改 |
| 环境 Compose | 通过 | 原始 mock Dockerfile 用现有缓存构建；三个 localhost HTTP 均 200；nginx healthy 且 `nginx -t` 成功；容器内 mock 源码逐字节一致 |
| 根目录 `python -m pytest q1_api/` | **现已通过** | 原始配置 1 failed；按后续底层配置要求增加 `pythonpath = .` 后，清除环境变量验证 1 passed |
| 显式 `PYTHONPATH` 的骨架测试 | 通过 | 1 passed；仅导入测试，不代表 Q1 完成 |
| 正式 Q3 / 完整工作流 / 备份恢复 / 真正失败率测量 | **未运行** | 模板尚未实现；不以环境烟雾测试替代作业验证 |

初次证据保存在 `.gekko-setup/`；续轮以 `.gekko-setup/final-check-20261012/` 为准：`source-verification.json`、`native-results.json`、`runtime-results.json`、`pytest-original.txt`、`pytest-with-path.txt`、`compose-up-build.txt`、`compose-down-recheck.txt`、`hello-world-pull.txt`、`hello-world.txt`、`final-state.json`、`changes.json`。续轮再次从 Docker Hub 查询 hello-world，返回 image up to date，随后运行成功。Python 基础镜像实际构建摘要仍为 `sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`。

nginx 检查仅覆盖默认欢迎页、配置语法及容器健康，不证明正式 Q3 `/api/*`、`/ocr/*` 代理或 readiness 已完成。两个 mock 无 healthcheck，Compose 输出不能当作其健康证据；本轮使用实际 HTTP 200 和容器 OCR 解析确认可用。续轮首次证据汇总代码误将 Compose 逐行 JSON 当成单个 JSON，已修正并重跑，见 `runtime-results.json`。未改原始作业代码。

## 磁盘与最终运行状态

续轮实测：Mac 可用约 22 GiB；项目约 45 MiB，其中 `.venv` 约 44 MiB；独立 Python 约 72 MiB；专用 VM 目录实际约 1.4 GiB。Docker 镜像共享计量约 279.7 MB，构建缓存约 202.8 MB，两项不能重复加到 VM 占用上。VM 数据盘尚有约 9.0 GiB 可用。本轮没有新安装、系统 Python 替换、磁盘扩容或清理其他项目数据。

测试用 Compose 容器、网络和本机 API/mocks 均已停止；`gekko` VM 与 Docker daemon 保持运行。端口 8000、9100、9200、18080、19100、19200 没有本次测试监听。停止 VM：`colima -p gekko stop`。启动及测试命令见 `START_HERE.zh-CN.md`。

## 文件改动与剩余事项

初次新增 `.python-version`、`.gitignore`、`START_HERE.zh-CN.md`、本报告，并修改 `TOOLS.md`、`AI_LOG.md`。续轮以开始时 SHA-256 为基线，仅更新两份交接文档和两份工具/AI 记录，补充忽略目录中的验证证据与运行日志；没有修改 `.python-version`、`.gitignore`、requirements、任何题目源码或 `REFLECTION.md`。精确清单见 `changes.json`。

上游此版本没有 `.gitignore`，初次新增最小忽略规则。原始题目快照和 tar 都已再次核对：tar 中全部 26 个文件与 Git HEAD 一致；当前 24 个非工具记录文件与 HEAD 一致。未访问或修改 Half Year Application 目录。

`REFLECTION.md` 完全保留待填写，未替 Alex 编造设计、测量或个人理解。最初尝试通过标准 UI 关联侧栏项目未成功：项目列表没有 Gekko，Codex UI 自动操作不被该工具允许；未改私有数据库或会话状态。当前会话标题为“Gekko 考核环境准备”。可手动在 Codex 添加该目录为项目。

没有管理员密码、新协议、费用或敏感权限的待办阻塞。开始开发所需环境可用；Q3 及全部业务需求仍待开始作业时实现。

默认 context 保留 `default`；在这台机器上直接执行 `docker version` 会因默认 socket 不存在而失败。本项目使用 `docker --context colima-gekko ...`，或只在当前终端 `export DOCKER_CONTEXT=colima-gekko`。这不是 Docker daemon 故障。

第一步：按启动指南激活 `.venv` 并启动 API 骨架，然后打开 `/docs` 阅读现有入口，接着开始 Q1 CSV 校验与状态机设计。

## 用户要求全部底层配置后的补充核验

2026-10-12 21:50 后：用户已自行启动 API，实测 `GET http://127.0.0.1:8000/api/health` 为 HTTP 200、`{"status":"ok"}`。保留该用户启动的服务运行；专用 Docker VM 无容器运行。

仅修改 `q1_api/pytest.ini` 的测试导入路径，新增一行 `pythonpath = .`。执行 `env -u PYTHONPATH .venv/bin/python -m pytest q1_api/` 得到 **1 passed in 0.17s**，证据为 `.gekko-setup/pytest-config-fixed-20261012.txt`。没有业务代码或测试用例改动；这个成功仅表示原有骨架能导入。原始配置仍在 upstream tar 中。

本次同时同步两份交接文档及 `TOOLS.md`、`AI_LOG.md`。此前“原始配置失败、源码不变”的记录属于对应时间的历史状态；当前测试入口已修复，详细文件核对见 `.gekko-setup/environment-followup-20261012.json`。`REFLECTION.md`、题目 README、依赖声明和业务代码继续保持原样。用户明确先不开始作业，因此暂不执行第一步所述业务设计。
