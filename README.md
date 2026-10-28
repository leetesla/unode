# unode

交互式升级 Node，并选择是否迁移 PM2。默认使用 **nvm**；在单选菜单中可以切换 fnm、n、系统包管理器或已有 Node 路径。

## 安装

在本仓库目录执行：

```bash
./install.sh
```

安装方式参考相邻 sp2 项目的 `install.sh`：支持 macOS 和 Ubuntu，缺少 Python 3.8+ 或 curl 时可通过 Homebrew/apt 安装。**不要求预装 Node、nvm 或 PM2，安装命令不会升级 Node 或操作服务。**

```bash
./install.sh --skip-deps             # 仅检查安装依赖
./install.sh --prefix /opt/unode     # 自定义命令安装位置
./install.sh --config-dir /etc/unode # 自定义公共配置目录
```

默认命令为 **`/usr/local/bin/unode`**，供所有用户调用；公共配置为 **`/etc/unode/config.json`**，目录 755、文件 644，所有用户可读，通常由 root 管理。安装脚本仅对需要权限的写入调用 sudo，**已有公共配置保留，不覆盖**。

如果 PATH 尚未包含 `/usr/local/bin`，按安装输出添加。运行文件放在 `/usr/local/lib/unode/<内容摘要>/`，命令软链接在最后一步替换，避免安装通过旧软链接写回仓库。重复安装复用相同内容的运行目录；更新时保留旧运行文件。自定义 `--config-dir` 会被记入该安装的运行目录，不需要每个用户再次设置环境变量。

也可显式做个人安装：`./install.sh --prefix "$HOME/.local" --config-dir "$HOME/.config/unode"`。

请用原业务用户运行 `unode`，不要为方便而对整个命令加 sudo。仅系统包安装或人工维护开机服务时可能需要管理员权限。

## 使用

```bash
unode                        # 单选管理方式、目标版本，然后确认计划
unode 24                     # 预选 Node 24 系列最新版本
unode 24 --m fnm              # 预选管理方式，可在其他运行中重新选择
unode --pm2                  # 仅处理 PM2，以当前 Node 为目标
unode --pm2 --check           # 只读取 PM2/启动配置，不更新或拉起后台
unode 24.14.0 --check         # 仅显示该明确目标的计划，跳过 PM2
unode --m path --node-path /opt/node/bin/node
unode --config /etc/unode/config.json --pm2
unode show startup           # 查看 PM2 启动配置和服务状态
unode restartup              # 使用当前 Node 重建 PM2 开机启动配置
unode restartup --check      # 只查看重建目标和版本检查
```

单选菜单使用 `◉ / ○`：↑↓ 切换选项，Enter 确认当前项（不需要先按空格）；空格也可选择；q/Esc/Ctrl+C 取消。初始默认选中 nvm，管理员可以修改公共配置中的默认方式。`--m` 预选管理方式，不需要输入完整的 `--manager`。

交互分段参考 [Clack](https://bomb.sh/docs/clack/packages/prompts/) 的导引线和 intro/outro，以及 [Inquirer](https://github.com/SBoudrias/Inquirer.js) 的单选确认方式：用 `┌ / ◇ / │ / └`、留白和短步骤标题区分阶段；已完成的菜单收成一行答案。当前 Node 和明确的目标版本高亮显示，非终端和 `NO_COLOR` 环境保持纯文本。

正常执行会依次显示：

1. 当前 Node 版本及实际路径。
2. 管理方式和明确的目标版本；如输入 `22`，先解析成 22 系列最新稳定版本再显示确认。只有与该完整版本一致才跳过安装，不按大版本相同直接跳过。输入 `22.20.0` 则固定该版本。系统包管理器以现有软件源可提供的版本为准。
3. **“是否处理 PM2？[Y/n]”**。直接回车处理 PM2，回答 `n` 跳过 PM2 后台迁移。nvm 全局包重装和后续 restartup 分别由各自选项控制；完成后仍会校验启动配置对应的 Node 版本。
4. nvm/fnm 可选择更新其默认版本；此设置要求新终端本身已经加载该管理工具。
5. 升级前询问“是否 restartup（重建 PM2 开机启动配置，可能重启应用）？[Y/n]”，默认 y。此选择独立于是否迁移 PM2。
6. 展示包含 restartup 选择的执行计划和共享安装的影响，确认后才修改 Node/PM2。升级完成后按预先选择执行，不再次询问。

所有是/否确认统一为 `[Y/n]`，默认 Y：PM2 处理、全局 npm 包重装、更新默认 Node、restartup、最终执行确认，以及后台仍为旧 Node 时的 kill/resurrect 确认。直接回车选择是，输入 n 选择否。非交互环境不会因这些默认值自动执行升级或 kill。

选择 nvm 且当前版本与目标不同、当前 Node 属于该 nvm 时，会询问“是否从当前 Node 重装全局 npm 包？（不包含 pnpm add -g 安装的包）[Y/n]”，默认 y。选 y 使用 nvm 原生参数 `--reinstall-packages-from=源版本`；目标已安装时同样可以补装源版本的包。选 n 不加此参数；没有可迁移的源版本、目标与当前相同或 `--check` 时不询问。pnpm 通过 `pnpm add -g` 管理的全局包目录不在迁移范围内；若 pnpm 命令本身是 npm 全局安装的，它可能随 npm 包一起迁移。包重装可能包含 PM2 软件包；“是否处理 PM2”控制后台迁移。

已经安装的目标版本会直接使用；版本和来源都与当前一致时跳过 Node 安装与切换，仍按选择决定是否处理 PM2。**脚本不能修改父终端的环境**，运行结束会输出当前终端需要执行的 `nvm use` / `fnm use` 或 PATH 提示。PM2 会明确通过目标 Node 执行，不依赖父终端是否已切换。

`--pm2` 不显示 Node 管理方式/版本选择，也不再次询问是否处理 PM2；仍先展示并确认实际操作。如果未安装 PM2，计划会明确说明将安装。没有原应用时，选择 restartup 会初始化空清单并由系统服务启动 PM2；跳过 restartup 时仅准备软件包，不创建后台。已有非空或损坏的 dump 不会被覆盖或自动恢复。

普通 `--check` 默认不触碰 PM2；要只读检查 PM2 请使用 `--pm2 --check`。正式执行必须在交互终端进行，没有 `--yes` 自动批准模式。独立 `restartup` 命令展示目标后直接执行，sudo 授权照常保留。

## 管理方式

| 方式 | 行为 |
|---|---|
| nvm（默认） | 显式加载 nvm.sh；未安装时将固定版本 nvm 的安装纳入确认计划；不自动改 shell 配置 |
| fnm | 使用已安装 fnm 的 install/exec/default；未安装时提示安装或重新选择 |
| n | 使用已安装 n 和 N_PREFIX；切换会更新共享 prefix，可能影响其他 Node 服务 |
| system | macOS 使用 Homebrew；Linux 使用已有 apt/dnf 软件源，只选择源中提供的明确版本，不自动添加仓库或切换模块流 |
| path | 使用已安装的绝对 Node 路径，不下载、不安装 |

安装脚本对 Linux 自动安装依赖的支持限定 Ubuntu；运行命令包含 dnf 适配，但尚未在真实 RPM 系统验证。系统软件源、Homebrew 配方提供的版本可能比官方发布滞后，计划显示的是该方式实际提供的版本。

目标 Node 目前要求 >=18。nvm/fnm 可以保留旧 Node 并行安装；n 和系统包管理器可能原位替换共享 Node，不能承诺完整保留旧环境。所有方式均不自动删除旧 Node，也不自动升级其他全局包或业务依赖。

## PM2 处理与验收

选择处理 PM2 后：

- 先读取正在运行的原后台，核实用户，保存原进程列表和现有 dump；最终确认后再次核对进程表，避免使用过期计划。
- 在目标 Node 环境安装/选择 PM2，已有 PM2 默认保持软件版本；只有原环境没装 PM2 时才首次安装当前可用版本。
- 先执行 `pm2 update`，再读取后台实际报告的 Node 版本。Linux 额外比较 `/proc/PID/exe` 对应的实际 Node 文件。
- 后台已符合目标：跳过 kill。确实仍未符合目标时，才交互询问是否 kill/resurrect。`--allow-kill` 可以预先允许这一后备步骤，仍不跳过整体确认。
- 报告读取失败、update 失败、原进程表异常、仅应用版本异常时，不把“不确定”当作 kill 的理由。
- 核实原始应用身份、实例数量和每个实例的 online/stopped 状态。原 online 应用恢复在线；原 stopped 应用保持 stopped、无运行 PID，不能先拉起再停止。仅运行中的 cluster 实例检查目标 Node；已停止实例保留的 node_version 是历史运行信息。fork 保留配置，不强制迁移解释器。
- 默认要求 PID/restart 计数连续稳定 30 秒；`--settle` 可调整。恢复总等待上限约为该时间的两倍。
- 不会修改业务的 package.json、node_modules、构建产物；原生扩展和应用对目标 Node 的兼容性须先在业务环境验证。

`pm2 update` 和 kill/resurrect 会重启原来运行的应用，请在维护窗口执行。普通 stopped 应用通过 PM2 保存的 status 恢复，保持停止，不要求手动启动它们。errored/launching/stopping 等异常或过渡状态会阻止迁移；没有应用时跳过 update，按 restartup 选择初始化后台及自启。已停止的 PM2 插件（pmx_module）另需人工处理，因为原生 update 会自动加载插件；普通应用不受此限制。

支持自定义目录、服务和业务健康检查：

```bash
unode --pm2 \
  --pm2-home /srv/app/.pm2 \
  --pm2-dir /opt/node/lib/node_modules/pm2 \
  --service pm2-app.service \
  --health-url http://127.0.0.1:3000/health
```

PM2 包必须能通过目标 Node 的 npm 定位。遇到 npm 自定义包装器时会拒绝猜测；标准 nvm/fnm/n 分发的 npm-cli.js 支持明确用目标 Node 执行。自定义 npm prefix 请保留其环境配置。

没有健康地址时只报告进程层面的检查通过，**不报告业务已经验证**。健康检查以 HTTP 2xx 为成功，不能证明每个 worker 和全部业务功能正常。

PM2 报告使用内部 getReport RPC，已对 PM2 6/7 测试；不支持的历史版本会停止，不解析模糊结果。检查通过直接 RPC 连接现有 socket，不调用会自动拉起后台的 `pm2.connect()`。

PM2 的 `node path` 字段读取环境变量 `_`，并非真实可执行文件路径。unode 和独立 helper 会在启动 Node 子进程时将它设为目标 Node 路径，避免报告显示 `/usr/local/bin/unode`。已运行后台的环境不会因更新工具而改变，需在下一次正常 PM2 更新时生效；无需为这一显示问题单独重启业务。

## 开机启动

直接查看 `pm2 startup` 生成的配置：

```bash
unode show startup
unode show startup --service my-pm2  # 自定义服务/LaunchAgent 文件名
```

此命令可以在非交互环境运行，无需预先安装 Node、PM2 或公共配置文件。macOS 默认读取 `~/Library/LaunchAgents/pm2.用户名.plist`，根据文件中的实际 Label 查询 launchd 用户会话状态；`--service` 可带 `.plist` 后缀。Linux/systemd 使用 `systemctl cat` 显示 unit 路径、配置及 drop-in，并显示服务状态；默认服务名 `pm2-用户名.service`，`--service` 可省略 `.service`。同时显示启动配置使用的 Node 版本及实际路径；Node 缺失时明确标记未验证。服务未运行时仍可查看配置；查看配置成功不代表服务已启用或机器重启恢复已经验证。

macOS 会单独显示“开机自启：已启用/已禁用/未配置登录时立即启动/无法确认启用状态”。判断依据包括现有 LaunchAgent 配置、`RunAtLoad` 等加载时启动条件，以及 `launchctl print-disabled gui/UID` 的持久启用状态；显式启用/禁用覆盖 plist 中的 `Disabled` 默认值。用户 LaunchAgent 的启动时机是当前用户登录后；输出格式无法解析、权限查询失败或特殊会话限制时明确提示无法确认，返回退出码 2。

PM2 的 macOS 模板包含 `LaunchOnlyOnce=true`：`pm2 resurrect` 命令退出后，launchd 会移除一次性任务的注册，PM2 后台仍可继续运行。因此 `launchctl print` 返回 `Bad request / Could not find service` 并不一定是故障。`show startup` 会将这种情况说明为“一次性启动任务当前未注册”，与自启启用状态分别显示；普通服务未加载和真正的权限/会话查询失败分别提示，查询失败返回退出码 2。此命令不会重新加载任务或重启应用。自启已启用表示当前配置会在登录时触发，不能代替一次真实重启后的业务恢复验收。

`show startup` 优先与 `~/.unode/last-run.json`（或 `UNODE_HOME`）记录的最近一次完成的目标版本比较，标注为“最近安装目标”。没有有效完成记录时才与当前终端的 Node 比较，并明确标注来源；两者都不可用时提示无法比较。版本不一致标红并返回退出码 2，避免仅显示旧版本却没有差异提示。

每次正式完成 Node 安装或选择已有版本后，都会校验启动配置中的 Node 与本次目标版本是否一致，包括选择跳过 PM2 迁移的情况。版本高亮显示，一致标绿、不一致标红、配置缺失或无法解析标黄；`NO_COLOR` 和非终端输出保持纯文本。版本不一致或已有配置无法校验时，保留安装结果并以退出码 2 提示待处理；未选择 restartup 时，缺失配置只提示，不创建服务。结果写入 `startup-node-check.json` 和 `last-run.json` 的 `startup_node` 字段。

版本检查解析启动命令、PM2 文件的 shebang 和配置中的 PATH，再对定位到的 Node 执行版本查询，不执行启动脚本。绝对 Node shebang 优先于 PATH；复杂 shell 表达式、未知包装程序和 systemd EnvironmentFile 标记待人工复核，不自动重建配置。版本一致只说明配置指向的 Node 版本匹配，不代表启动服务或机器重启已验证。

`show startup` 和 `--check` 只检查配置。Linux/systemd 会检查服务用户、PM2_HOME、PATH 对应 Node、ExecStart、启用状态、MainPID、NeedDaemonReload、dump 中的入口/工作目录/解释器。默认服务名 `pm2-用户名.service`，自定义名称使用 `--service`。

macOS 的 LaunchAgent 与用户登录会话有关。未执行 restartup 时，PM2 迁移中的完整开机配置检查仍标记待人工复核；restartup 会检查实际 Label 和应用状态，并在应用稳定性验收后重新查询注册状态。一次性任务已退出时会记录当前未注册；如果整个检查期间都未观察到注册，标记待复核并返回退出码 2。普通任务意外消失仍报错，不宣称已验证机器重启恢复。

独立重建使用当前终端的 Node，先切换到你要使用的版本：

```bash
nvm use 22.23.4
unode restartup
unode restartup --service my-pm2 --pm2-home /srv/app/.pm2
```

重建前备份配置及 dump，有应用时执行 `pm2 save`。有旧配置时执行 `pm2 unstartup`，再执行 `pm2 startup`；首次配置跳过 unstartup。注册新配置前结束仍存活的原 PM2 后台，避免 systemd 拒绝接管终端启动的进程。明确使用目标 Node/PM2、目标 PATH 和同步后的 NVM_BIN/NVM_INC；sudo 命令显式保留原 HOME、用户和 PM2_HOME，不解析执行 PM2 打印的 shell 文本，不引入额外的 shell 环境切换方案。

**重建可能中断应用。** Linux 的 unstartup 会停止服务，默认 ExecStop 执行 pm2 kill；startup 注册后会 daemon-reload、清除旧失败状态并启动服务。macOS 按 plist 的实际 Label 卸载旧服务，检查新服务的加载结果。随后验证应用数量、原 online/stopped 状态、连续稳定性、启动 Node 版本及路径；有 `--health-url` 时再次检查业务。

没有后台或后台应用表为空时，允许初始化自启：仅当 `dump.pm2` 和 `dump.pm2.bak` 均不存在或为 `[]`，才写入空清单，由系统服务启动后台并验收。先以当前用户构造 PM2 API（不连接或启动后台），初始化日志、PID、模块目录及配置文件并检查归属，防止 sudo 注册时生成 root 所有的运行文件。已有应用清单、损坏清单、RPC socket 存在但 PID 无法核实的情况会在修改前报错。初始化失败时不在服务外重新创建空后台；成功后可用 `pm2 start ...` 添加应用，再执行 `pm2 save` 保存。

未知的自定义启动命令、用户/PM2_HOME 不符、systemd drop-in/EnvironmentFile 不直接覆盖；PM2 生成器无法正确引用的带空格或特殊字符的启动文件/PM2 路径会在修改前报错。失败时尝试恢复原应用，并保存 `restartup-recovery.json`；启动配置仍可能需要根据备份人工修复，不伪报成功。不执行机器重启。

原迁移检查仍会生成 `startup-commands.txt` 供维护参考，程序不会执行该文本文件。

## 配置和状态目录

**公共配置与用户私有运行记录分开存放。** `/etc/unode/config.json` 供所有用户读取，运行时永远不回写；只有管理员维护它：

```json
{
  "schema": 1,
  "manager": "nvm",
  "settle_seconds": 30
}
```

可选 `nvm_dir` 路径模板（如 `$HOME/.nvm`）会按当前用户展开；不要把某一个用户的目录固定成全体用户共用的 nvm 位置。配置选择优先级为 `--config`、`UNODE_CONFIG`、安装时指定的公共配置路径（初始 `/etc/unode/config.json`）。

运行记录参考 PM2 的 `~/.pm2/`，使用各用户独立的 `~/.unode/`：

```text
~/.unode/
  last-run.json           # 最近一次实际执行结果
  unode.lock              # 防止同一目录并行运行，退出时自动释放锁
  backups/
    时间-随机标识/
      plan.json
      result.json
      pm2-before.json
      dump.pm2.pre-node
      startup-before.json
      startup-check.json
      startup-node-check.json
      startup-config.original
      restartup-before.json
      restartup-after.json
      restartup.log
      restartup-recovery.json
      startup-commands.txt
      recovery.txt
      manager.log / npm.log
      pm2/                # PM2 更新前后状态、dump 和命令日志
```

仅选择 PM2 时才会生成 PM2 相关文件。公共配置由安装脚本首次创建，重装和运行均不覆盖已有配置。私有记录仅在确认执行后创建，安装、取消和 `--check` 不写私有状态。可通过 `UNODE_HOME=/绝对路径` 修改私有记录位置。私有目录权限 700、状态文件 600，因为 PM2 进程表及日志可能含密钥；这些不应放入所有用户可读的 `/etc/unode`。

PM2 自己的实时数据仍属于 `PM2_HOME`：unode 不接管其目录，不把业务 dump 移到 `.unode` 运行。选择处理 PM2 后会在 PM2_HOME 中使用 `.unode.lock`，配合 unode 自己的锁防止迁移并发；正常操作仍要求没有其他部署同时修改进程表。

失败时保留旧信息和诊断，不盲目回滚 Node、依赖或服务。恢复前先确认实际在线进程；自动杀进程、复制旧 dump 或替换运行时都可能扩大中断。

退出码：0 为所选运行步骤完成；1 为错误；2 为运行步骤已通过但开机配置待复核，或只检查发现待处理项；130 为用户取消/中断。

## 开发验证

```bash
python3 -m unittest discover -s tests -v
node --test tests/helper-unit.cjs tests/helper-fallback.cjs
```

真实 PM2 测试默认跳过，以避免接触现有服务。显式提供测试包和旧 Node 后，测试只使用独立的临时 HOME、PM2_HOME、UNODE_HOME 和 npm prefix，结束时销毁：

```bash
UNODE_TEST_PM2_DIR=/绝对路径/测试用/pm2 \
UNODE_TEST_OLD_NODE=/绝对路径/旧版/bin/node \
python3 -m unittest discover -s tests -v
```

详见 [VALIDATION.md](VALIDATION.md)。

## 参考

- [sp2 安装脚本](../sp2/install.sh)（本地相邻项目）
- [nvm](https://github.com/nvm-sh/nvm)
- [fnm commands](https://github.com/Schniz/fnm/blob/master/docs/commands.md)
- [n](https://github.com/tj/n)
- [PM2 更新](https://pm2.keymetrics.io/docs/usage/update-pm2/)
- [PM2 开机启动](https://pm2.keymetrics.io/docs/usage/startup/)
