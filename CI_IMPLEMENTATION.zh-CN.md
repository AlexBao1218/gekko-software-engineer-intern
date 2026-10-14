# CI 加分项实施与核验

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

## Git 与公开运行

GitHub CLI核对账号为AlexBao1218；出题方Anthony-Gekko/software-engineer-intern为公开仓库且当前账号无push权限；创建前个人同名仓库不存在。现已遵循已批准计划建立个人公开fork，保留出题方为upstream，个人fork为origin，保留已有提交历史。已完成配置审查与凭证/运行时文件检查后正常快进推送到个人fork的main。本地功能分支跟踪origin/main，后续明确使用`git push origin HEAD:main`。不会向出题方提交PR、发送邮件或改变其仓库。

GitHub运行必须同时检查repository、event、branch和head_sha；完成后读取两个job的conclusion及步骤日志，保留run URL和原始结果。首次运行结果可随记录提交；记录提交也必须再跑CI。最终SHA的运行回执保存在本机`.gekko-setup/ci-20261014/`并在最终答复给出GitHub链接，避免为了把自身hash写进提交而产生无限追加提交。

## 来源与当前状态

参考官方[工作流语法](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)、[checkout](https://github.com/actions/checkout)、[setup-python](https://github.com/actions/setup-python)，并由独立子agent检查评分覆盖和配置风险。没有添加应用依赖或修改业务代码。工作流已配置，首轮push运行38501842241实际通过：478项测试及三个服务镜像构建成功。详见[CI验收](q3_deploy/evidence/CI-ACCEPTANCE-20261014.md)。本次记录提交后还要核对最终SHA的运行，不能把首轮SHA当成最终结果。
