# CI 加分项验收 — 2026-10-14

原题加分项要求：每次push执行测试和`docker compose build`，最终提交必须通过。
工作流：[ci.yml](../../.github/workflows/ci.yml)。这是要求核对，不是评审已授予10分。

## 首次真实push验收

- 仓库：[AlexBao1218/software-engineer-intern](https://github.com/AlexBao1218/software-engineer-intern)，公开个人fork；parent为Anthony-Gekko/software-engineer-intern。
- 事件：`push`；分支：`main`；SHA：`b6d07e5f463f5e54750181bd7f26c99cee35bf15`。
- GitHub运行：[38501842241](https://github.com/AlexBao1218/software-engineer-intern/actions/runs/38501842241)，`completed / success`。
- [Python tests](https://github.com/AlexBao1218/software-engineer-intern/actions/runs/38501842241/job/115650373877)：Ubuntu24.04、Python3.12.15 x64，**478 passed, 1 warning，40.43s**。已执行requirements安装和根目录完整pytest；既有Starlette/TestClient弃用警告未隐瞒。
- [Docker Compose build](https://github.com/AlexBao1218/software-engineer-intern/actions/runs/38501842241/job/115650374150)：配置检查成功，API、mock_ocr、mock_onboarding三个镜像均实际构建成功。nginx引用现成镜像，不在构建目标中。
- 两个job及其所有步骤conclusion均为success；无continue-on-error，无替代性本地模拟绿灯。

原始证据：[运行与步骤JSON](ci-first-run-20261014.json)、[完整GitHub job日志](ci-first-run-20261014.txt)。源码/测试/依赖未因CI改变，首轮没有失败或重跑。

## 对照表

| 要求 | 实施与核验 |
|---|---|
| 每次push执行 | 无分支或路径过滤的push事件；首个实际push触发运行已确认 |
| 跑测试 | `python -m pytest q1_api/`，478项全部通过 |
| 跑docker compose build | 在q3_deploy执行，三个构建服务全部成功 |
| 最终提交通过 | 本记录提交后也必须观察新的push运行；只有head_sha等于最终提交且两个job均成功才宣布完成 |
| 过程/工具披露 | CI_IMPLEMENTATION、WORK_LOG、AI_LOG、TOOLS、REFLECTION已更新；Git保留原历史 |

本记录会形成新的文档提交，所以不把上面的旧SHA结果当成最终SHA结果。
最终提交的原始运行回执与日志将存于本机`.gekko-setup/ci-20261014/`，最终答复给出该运行URL；
也可在[main分支Actions](https://github.com/AlexBao1218/software-engineer-intern/actions/workflows/ci.yml?query=branch%3Amain)核对HEAD对应运行。
这样不需要为了记录自己的hash不断追加提交。

## 边界

CI验证Python3.12的完整测试和Linux镜像构建。每次push不额外启动整套Compose做备份恢复；
真实启动/故障/恢复证据见Q3 T6/T7。未测试Python3.10、生产部署或多架构发布。
公开fork与push已完成，但没有向出题方提交PR或发送提交邮件。候选人理解和面试修改练习仍需本人完成。
