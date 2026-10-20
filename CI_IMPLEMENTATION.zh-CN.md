# CI 加分项实施与核验

## 发布状态（2026-10-20）

用户已明确授权公开、推送及回复 Gekko。提交仓库为 [https://github.com/AlexBao1218/gekko-software-engineer-intern](https://github.com/AlexBao1218/gekko-software-engineer-intern)，已核对为公开 fork，父仓库为 `Anthony-Gekko/software-engineer-intern`。原同名私有仓库保留。默认提交分支使用 `assessment-submission`，完整保留本地开发历史，不强推覆盖上游 main。

最终技术基线 `4f4e044` 的干净克隆通过 483 项测试、3 个原生交付场景、9 阶段部署及 9 阶段备份恢复；本次仅更新交付状态文档。最终提交的 GitHub Actions 必须在发送邮件前通过，实际 run URL 和发送回执另存本地，不在成功前宣称已发送。个人理解与反思仍由候选人本人确认，未伪造已理解声明。

上游现为新的初始提交 `c5fe924`，已对比原题基线 `d17c972`：变化为表述、评分数字与注释，没有新增可执行业务规则。这里的分数映射保留原题版本含义，不声称是最新评分承诺。原始 README 与 mock 不改。

以下是发布授权前的本地交付记录，历史“禁止推送”状态已被上述授权取代。

## 当前交付状态（2026-10-20，香港）

Q1、Q2、Q3 已实现，备份时钟回拨修复已在 `db82592` 本地提交。此前完整套件为 483 项；本轮对最终提交重新进行干净克隆验证，准确 SHA、命令、退出码及报告保存在独立本地交付回执，不用历史通过结果代替最终结果。

GitHub 仓库本轮只读核对为 **PRIVATE，isFork=false**；不推送、不公开。历史 `d5fa831` 的 Actions 通过不代表本轮最终提交通过。公开 fork、最终 SHA 的远端 CI、邮件提交仍待后续授权执行。个人反思与理解由候选人本人完成。交付入口见 [交付清单](DELIVERY_PACKAGE.zh-CN.md)。

以下保留按日期记录的历史规划和实施证据；其中旧测试数量、公开状态不代表当前状态。

2026-10-14；基线 `732b6ea`，分支 `codex/assessment-implementation`。
原题要求：每次 push 执行测试和 `docker compose build`，最终提交必须在 GitHub Actions 上通过。配置文件存在、本机通过或旧提交绿灯均不能替代最终 SHA 的实际结果。

## 设计

工作流位于 `.github/workflows/ci.yml`，监听所有 push、pull_request 和手动触发，不用分支/路径过滤，也不跳过文档提交。两个独立 job 都要通过：

| Job | 环境 | 实际命令 | 为什么这样做 |
|---|---|---|---|
| Python tests | GitHub hosted Ubuntu24.04、Python3.12、普通用户 | `python -m pip install -r requirements.txt`；根目录 `python -m pytest q1_api/` | 从checkout安装依赖，覆盖完整套件；普通用户才能实际验证只读文件权限 |
| Docker Compose build | GitHub hosted Ubuntu24.04、Docker Compose | 在q3_deploy执行 `docker compose config --quiet` 和 `docker compose build` | 验证配置及API/两个mock镜像构建；nginx使用现成镜像，不伪称构建了nginx |

测试10分钟、构建15分钟超时；任务失败会让整个工作流失败，没有continue-on-error或忽略退出码。官方checkout/setup-python固定完整commit SHA（分别v7.0.1/v7.0.0，已通过官方GitHub API核对release、tag、commit及Node24配置）；token只有contents:read，checkout不保留凭证。pip缓存按requirements.txt建立，仍实际执行安装。Compose已有全部非秘密默认值，不需要上传本地.env或设置Secrets。

这里验证的是CI评分要求，不把build当成实际运行/恢复测试。真实Docker启动、持久化、故障与备份恢复已有Q3证据；手动实验脚本没有放进每次push的job。Python3.10、多架构镜像发布或部署不属于本次已验证范围。

## 历史 Git 与公开运行（2026-10-14，之后已转为私有）

GitHub CLI核对账号为AlexBao1218；出题方Anthony-Gekko/software-engineer-intern为公开仓库且当前账号无push权限；创建前个人同名仓库不存在。现已遵循已批准计划建立个人公开fork，保留出题方为upstream，个人fork为origin，保留已有提交历史。已完成配置审查与凭证/运行时文件检查后正常快进推送到个人fork的main。本地功能分支跟踪origin/main，当时使用`git push origin HEAD:main`；当前禁止继续推送。不会向出题方提交PR、发送邮件或改变其仓库。

GitHub运行必须同时检查repository、event、branch和head_sha；完成后读取两个job的conclusion及步骤日志，保留run URL和原始结果。首次运行结果可随记录提交；记录提交也必须再跑CI。最终SHA的运行回执保存在本机`.gekko-setup/ci-20261014/`并在最终答复给出GitHub链接，避免为了把自身hash写进提交而产生无限追加提交。

## 来源与当前状态

参考官方[工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)、[checkout](https://github.com/actions/checkout)、[setup-python](https://github.com/actions/setup-python)，并由独立子agent检查评分覆盖和配置风险。没有添加应用依赖或修改业务代码。工作流已配置，首轮push运行38501842241实际通过：478项测试及三个服务镜像构建成功。详见[CI验收](q3_deploy/evidence/CI-ACCEPTANCE-20261014.md)。本次记录提交后还要核对最终SHA的运行，不能把首轮SHA当成最终结果。
