# 验证记录

日期：2026-10-27，当前源码版本 0.1.2。测试平台：macOS。本次更新全局安装的 unode，保留 `/etc/unode/config.json`；没有升级真实用户的 Node、修改业务 PM2 或重启真实系统服务。此前对用户真实 PM2 仅做了直接 RPC 只读检查，确认 13 个 online、4 个普通 stopped 应用；没有对它们执行 update/save/stop/start/kill。

## restartup 与 nvm 全局包交互（0.1.2）

- 新增独立 `unode restartup`、只读 `unode restartup --check`；普通升级在执行计划前询问是否 restartup，默认 y，选择写入 plan，完成后按选择执行、不再询问。独立命令不重复确认，sudo 授权照常保留。
- 选择 nvm、源版本有效且与目标不同时，增加默认 y 的 npm 全局包迁移询问，提示明确“不包含 pnpm add -g 安装的包”；使用原生 `--reinstall-packages-from`，不自行复制包目录。
- 基于 nvm v0.40.8 官方源码确认：源包列表来自 `npm list -g --depth=0`，pnpm 独立全局目录不在其迁移范围；通过 npm 安装的 pnpm 命令本身可以被迁移。
- 修复目标命令环境中的 NVM_BIN、NVM_INC，使其与目标 nvm Node 一致；父终端环境仍保持不变。
- 87 项 Python 测试全部通过，包含显式启用的真实 PM2 迁移和新增恢复测试。新增测试使用独立 HOME/PM2_HOME，真实执行 save、kill、resurrect，确认原 stopped cluster 和 fork 实例未短暂执行入口；系统服务的卸载、生成、加载由替身控制。
- 模拟 systemd/launchd 验证首次配置、unstartup/startup 顺序、权限失败、unstartup 失败不继续、startup 失败恢复、实际 Label 卸载、其他用户 Label 冲突、自定义 drop-in 拒绝覆盖、目标环境传递、版本不符，以及交互调用目标与确认次数。
- Python 3.8 语法检查及 Bash 安装脚本语法检查通过。源码 `restartup --check` 在本机正确显示目标 22.23.4、现有启动 Node 22.20.0，返回 2。
- 随后增加“systemd 磁盘配置未加载时拒绝覆盖”的保护；专项 19 项测试中 18 项通过、1 项真实 PM2 测试未在该专项重复启用而跳过。
- 已安装最终源码到 `/usr/local/bin/unode`，运行文件为 `/usr/local/lib/unode/db61980f2c661f245bc0/unode`，确认命令及 helper 与仓库源码逐字节一致。已安装命令的 `restartup --check` 和实际终端红色差异提示验证通过。
- 0.1.2 真实 nvm 安装复测通过：父环境仍保持 22.20.0，临时目标 22.23.4 安装成功、默认版本更新成功；内部 PATH 和 NVM_BIN 已同步到新版本。另用真实 Node 子进程验证 NVM_INC 同步，结果位于 `work/target-node-env-probe.json`。用户原始启动配置和 nvm 默认版本的哈希保持不变。
- 尚未对真实 launchd/systemd 注册执行自动重建；本次没有重启真实业务后台或服务。
- 调整 restartup 确认时机与默认值后，回归共 89 项，87 项通过、2 项真实 PM2 测试未在本次重复启用。验证了 restartup 选择位于最终执行确认前、默认 y、计划持久化、取消总计划不写入状态或 PM2_HOME；真实终端流程已按新顺序更新。
- 安装后用真实伪终端直接回车选择 restartup，确认 `[Y/n]` 默认 y，执行计划显示重建，且只询问一次；随后取消总计划，状态目录及 PM2_HOME 均未创建。日志：`work/restartup-before-upgrade.log`。
- 所有是/否确认统一为 `[Y/n]` 默认 Y，包括 PM2 处理、默认 Node 设置、最终执行确认和 helper 的条件性 kill/resurrect。Python 回归 92 项中 90 项通过、2 项真实 PM2 测试未重复启用；21 项 Node helper 测试全部通过。真实伪终端验证 helper 的回车确实允许条件性 kill/resurrect，并用隔离替身确认完整 save/update/kill/resurrect/save 顺序。
- 已安装命令的真实终端验证覆盖五处 `[Y/n]`：全局 npm 包、PM2、默认 Node、restartup、最终执行。前四处直接回车选择 Y，最终输入 n 取消，状态目录与 PM2_HOME 均未创建。日志：`work/all-confirmations-default-yes.log`。

## 启动配置查看与 Node 版本校验（本次变更）

- `unode show startup [--service 名称]` 已接入；独立于升级流程，不读取公共配置、不要求交互终端。
- 最新 Python 回归共 69 项，68 项通过、1 项真实 PM2 集成测试未启用而跳过；此前 21 项 Node helper 测试通过，helper 未再修改。Python 3.8 语法兼容检查通过。
- macOS 实机只读执行 `./unode show startup`，成功读取用户 LaunchAgent，解析到启动 Node 22.20.0；当前终端 Node 为 22.23.4。launchd 用户域未查到已加载的同名服务，输出明确提示状态未验证。
- 真实伪终端中使用临时 HOME 和启动配置，验证版本一致标绿、版本不一致标红并返回 2，结果写入运行记录；未配置服务时只提示未验证。版本不一致场景使用隔离的版本查询替身，没有修改真实 Node 或 PM2。
- 模拟 Linux/systemd 验证 unit/drop-in 展示、inactive 状态、配置 PATH 的 Node 解析、缺失服务、EnvironmentFile 和尚未 daemon-reload 的处理；尚未进行真实 Linux/systemd 验证。
- 覆盖自定义服务名、二进制 plist、真实 launchd Label 与文件名不同、绝对 shebang 优先于 PATH、复杂 shell 命令拒绝执行、损坏/缺失配置，以及跳过 PM2 迁移后仍校验本次安装版本。
- 修复 `show startup` 只显示启动版本却不比较的问题：优先读取最近完成记录中的目标版本，无有效记录时回退到当前终端 Node，明确显示比较来源；没有可比较版本时明确提示。
- 通过 macOS 管理员授权更新 `/usr/local/bin/unode`，已验证运行文件与源码一致。对已安装命令进行真实终端测试，确认显示最近安装目标 22.23.4、启动配置 Node 22.20.0，差异提示为红色并返回退出码 2。
- 没有升级真实 Node、修改业务 PM2、重建启动配置或重启服务。

## 真实 nvm 安装后的环境检查

最初使用 0.1.1 的 `/usr/local/bin/unode 22.23.4 --m nvm`，在临时 HOME、NVM_DIR、UNODE_HOME 中进行真实下载安装。初始 PATH 和 NVM_BIN 指向已有的 22.20.0，目标目录最初不存在；选择跳过 PM2 迁移、更新测试 nvm 默认版本并确认安装。没有增加新的子 shell 实现，使用 unode 已有的 nvm 安装流程。0.1.2 的复测结果见上节。

- 官方 Node 22.23.4 macOS arm64 压缩包下载、校验及安装完成；测试目录中的 nvm default 更新为 22.23.4。
- 调用 unode 的父测试进程安装前后 `node` 均为 22.20.0，PATH 和 NVM_BIN 均未改变。
- unode 现有 `node_env(target)` 构造的内部命令环境中，PATH 首项和实际 Node 已为 22.23.4；但继承的 NVM_BIN 仍指向 22.20.0，尚未同步。
- 测试启动配置仍指向 22.20.0，版本检查正确报告 mismatch，命令返回 2；未创建 PM2_HOME 或启动 PM2。
- 已通过哈希确认真实用户的启动 plist 和 nvm default 没有变化。临时安装已清理。
- 最新复测结果：`work/install-env-result.json`；终端日志：`work/install-env-terminal.log`；安装日志：`work/install-env-manager.log`；复现脚本：`work/test-install-env.py`。这些文件已更新为 0.1.2 复测内容；上面的 NVM_BIN 未同步问题是此前 0.1.1 的结果。

## 自动检查（此前记录）

- Python 3.8 语法兼容检查、Bash 语法检查、Node helper 语法检查通过。
- **48 项 Python 测试通过**，包括真实终端和显式启用的真实 PM2 集成测试。
- **21 项 Node helper 测试通过**，包括允许/禁止条件性 kill、update 失败、RPC 失败、应用版本异常和健康检查失败。

覆盖的主要行为：

- 单选菜单默认 nvm，方向键移动即选中，直接 Enter 确认；选择“输入具体版本”后出现输入提示，输入大版本 22 能进入解析流程。退出后恢复终端光标。
- 使用 Clack 风格的导引线、短标题、留白、菜单完成后折叠；当前 Node 和目标版本高亮；非终端或 NO_COLOR 保持纯文本。
- 交互回答 n 完全跳过 PM2，连 PM2_HOME 都不创建；没有执行 PM2 包发现、RPC 或安装。
- `--pm2` 可以在确认后准备原本未安装的 PM2，但没有应用时不创建空后台。
- 明确版本离线解析、部分版本选择最新匹配版本、恶意/无效选择器拒绝。
- nvm 参数作为独立参数传入；apt 只选择已有源中的匹配包，不添加软件源。
- 临时 prefix、带空格路径、重复安装、替换旧软链接均通过；旧链接目标文件保持不变。
- 公共配置新建目录 755、文件 644；重装保留已有配置内容。
- 自定义公共配置路径随安装保存，命令可以不依赖环境变量找到它。
- 两个独立用户状态目录读取同一个公共配置；执行记录不回写公共配置，也不写到其他用户目录。
- 私有备份目录 700、文件 600，进程锁防止重复执行。
- systemd 属性检查的模拟测试覆盖用户/PID 不符、临时启用、需 daemon-reload、inactive 和损坏 dump。

### PM2 node path 显示修复

unode 的 Node 子进程环境和独立 helper 均显式设置 `_` 为实际目标 Node 路径。新增真实 Node 子进程回归检查，并在 helper 命令测试中注入错误的 `_=/usr/local/bin/unode`，核验传递给 PM2 的路径。

使用本机 PM2 6.0.13，在独立临时 PM2_HOME 中从 Node 20.20.0 迁移到 22.23.4，确认后台 `getReport.node_path` 为目标 Node 绝对路径；独立 helper 再次更新全部 stopped 的测试进程表后，路径也保持正确。48 项 Python 测试（真实 PM2 测试单独显式启用）、21 项 Node 测试及 Bash/Node 语法检查通过。未重启真实业务后台。全局安装目录需要管理员权限，本次未更新已安装命令。

## 真实 PM2 迁移

使用 PM2 **6.0.13**（与用户当前版本一致）、**6.0.14**、**7.1.1**，以及已有的 Node **20.20.0**、**22.20.0**，在独立临时 HOME、PM2_HOME、UNODE_HOME 和 npm prefix 中验证。最新的完整回归包含：

1. Node 20 启动 PM2，管理两个 cluster 实例、一个固定 Node 20 的 fork 应用，以及一个将要停止的普通 fork 应用。
2. 停止其中一个 cluster 实例和普通 fork 应用，记录应用入口执行标记；保留另外两个实例在线。
3. 在真实终端中执行 unode，选择已有 Node 22 的路径，回答 y 处理 PM2，并确认计划。
4. 执行 update 后，后台和在线 cluster 实例实际报告 Node 22；两个 stopped 实例仍保持停止、入口执行标记不增加，证明不是先拉起再停回去。
5. 固定解释器的在线 fork 应用仍使用 Node 20；helper 确认后台已符合目标，跳过 kill。
6. 原始实例数量、身份、online/stopped 状态和稳定观察通过；更新前进程表备份位于用户私有 UNODE_HOME。
7. 在临时环境另行执行原生 kill/resurrect，确认两个 stopped 实例仍未执行入口。随后将四个实例全部停止，再次 update，确认没有任何应用入口被执行。
8. macOS 开机配置标记待人工复核，不伪报开机验证通过。

测试结束停止临时 PM2 并删除临时目录。没有用真实业务制造“update 后仍为旧后台”的异常；该条件分支通过模拟接口覆盖，kill/resurrect 的状态保留则在真实临时 PM2 中验证。

## 尚未实机覆盖

- Ubuntu 自动安装 Python/curl、真实 apt/dnf/Homebrew Node 更新，以及 fnm/n 的真实安装流程。
- 首次下载并安装 nvm/Node 的网络流程；真实迁移测试使用已经安装的目标 Node。
- 真实 Linux `/proc/PID/exe` 和 systemd 配置检查。systemd 解析逻辑目前使用模拟输出验证。
- 业务全部接口、每个 worker 的独立健康检查和长时间稳定性。

按需求不执行机器重启验证；当前程序也不会自动重启系统服务或重建开机配置。
