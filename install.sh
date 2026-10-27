#!/usr/bin/env bash
set -euo pipefail

die() { printf 'unode 安装失败：%s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
用法：./install.sh [--prefix DIR] [--config-dir DIR] [--skip-deps]

支持 macOS 和 Ubuntu；自动安装缺失的 Python 3.8+ 和 curl。
  --prefix DIR   安装命令到 DIR/bin/unode（默认：/usr/local）
  --config-dir DIR  公共配置目录（默认：/etc/unode）
  --skip-deps    只检查依赖，不调用包管理器
  -h, --help     显示帮助

不要求预装 Node、nvm 或 PM2，不会启动或重启任何服务。
nvm 在首次运行确认升级计划后按需安装；PM2 可以选择完全跳过。
公共配置所有用户可读；已有配置保留不覆盖。
私有运行记录：${UNODE_HOME:-$HOME/.unode}，不包含在公共配置目录。

示例：
  ./install.sh
  ./install.sh --prefix /usr/local
  ./install.sh --prefix "$HOME/.local" --config-dir "$HOME/.config/unode"
EOF
}

prefix=/usr/local
config_dir=/etc/unode
skip_deps=false
while (($#)); do
    case "$1" in
        --prefix)
            [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || die '--prefix 需要目录参数'
            prefix="$2"
            shift 2
            ;;
        --skip-deps) skip_deps=true; shift ;;
        --config-dir)
            [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || die '--config-dir 需要目录参数'
            config_dir="$2"
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数：$1（使用 --help 查看用法）" ;;
    esac
done
[[ "$prefix" == /* ]] || die '--prefix 必须是绝对路径'
[[ "$config_dir" == /* ]] || die '--config-dir 必须是绝对路径'
config_file="${config_dir%/}/config.json"
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for file in unode pm2-helper.cjs config.example.json; do
    [[ -f "$source_dir/$file" ]] || die "请保留 install.sh、unode、pm2-helper.cjs 和 config.example.json 在同一目录（缺少 $file）"
done

case "$(uname -s)" in
    Darwin) platform=macos ;;
    Linux)
        [[ -r /etc/os-release ]] || die '无法识别 Linux 发行版，仅支持 Ubuntu 自动安装'
        . /etc/os-release
        [[ "${ID:-}" == ubuntu ]] || die '安装脚本的 Linux 自动安装目前仅支持 Ubuntu'
        platform=ubuntu
        ;;
    *) die '安装脚本仅支持 macOS 和 Ubuntu' ;;
esac

as_root() {
    if [[ "$(id -u)" == 0 ]]; then
        "$@"
    else
        command -v sudo >/dev/null 2>&1 || die '需要管理员权限，但找不到 sudo'
        sudo "$@"
    fi
}
python_ready() {
    command -v python3 >/dev/null 2>&1 &&
        python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1
}

# Mirror sp2: discover an existing Homebrew before deciding dependencies are absent.
if [[ "$platform" == macos ]]; then
    if ! command -v brew >/dev/null 2>&1; then
        for brew_bin in /opt/homebrew/bin/brew /usr/local/bin/brew; do
            if [[ -x "$brew_bin" ]]; then
                export PATH="$(dirname -- "$brew_bin"):$PATH"
                break
            fi
        done
    fi
    if command -v brew >/dev/null 2>&1; then
        brew_prefix="$(brew --prefix)"
        export PATH="$brew_prefix/bin:$PATH"
    fi
fi
packages=()
if ! python_ready; then
    if [[ "$platform" == macos ]]; then packages+=(python); else packages+=(python3); fi
fi
command -v curl >/dev/null 2>&1 || packages+=(curl)
if ((${#packages[@]})); then
    "$skip_deps" && die '缺少 Python 3.8+ 或 curl；移除 --skip-deps 可自动安装'
    if [[ "$platform" == macos ]]; then
        command -v brew >/dev/null 2>&1 || die '请先安装 Homebrew：https://brew.sh，然后重新运行脚本'
        brew install "${packages[@]}"
    else
        as_root apt-get update
        as_root apt-get install -y "${packages[@]}"
    fi
    hash -r
fi
python_ready || die '需要 Python 3.8+；请升级 Python 后重新运行'
command -v curl >/dev/null 2>&1 || die '未找到 curl，请检查安装结果及 PATH'
python3 "$source_dir/unode" --help >/dev/null || die '命令自检失败'

bin_dir="${prefix%/}/bin"
lib_dir="${prefix%/}/lib/unode"
[[ ! -d "$bin_dir/unode" ]] || die "$bin_dir/unode 已存在且指向目录"
digest="$(python3 - "$source_dir" "$config_file" <<'PY'
import hashlib
from pathlib import Path
import sys
root = Path(sys.argv[1])
digest = hashlib.sha256()
for name in ('unode', 'pm2-helper.cjs'):
    digest.update(name.encode() + b'\0' + (root / name).read_bytes())
digest.update(b'config-path\0' + sys.argv[2].encode())
print(digest.hexdigest()[:20])
PY
)"
runtime_dir="$lib_dir/$digest"

# Keep each complete runtime immutable, then replace only the command symlink.
# Never install through an existing symlink (it could point into the checkout).
elevate=false
for target in "$bin_dir" "$lib_dir" "$config_dir"; do
    ancestor="$target"
    while [[ ! -e "$ancestor" ]]; do ancestor="$(dirname -- "$ancestor")"; done
    [[ -d "$ancestor" && -w "$ancestor" ]] || elevate=true
done
install_run() {
    if "$elevate"; then as_root "$@"; else "$@"; fi
}
stage=''
link_stage=''
config_stage=''
cleanup() {
    [[ -z "$stage" ]] || install_run rm -rf -- "$stage"
    [[ -z "$link_stage" ]] || install_run rm -f -- "$link_stage"
    [[ -z "$config_stage" ]] || install_run rm -f -- "$config_stage"
    return 0
}
trap cleanup EXIT
install_run mkdir -p -- "$bin_dir" "$lib_dir"
if [[ ! -d "$config_dir" ]]; then
    install_run install -d -m 755 "$config_dir"
fi
if [[ -e "$config_file" || -L "$config_file" ]]; then
    [[ -f "$config_file" && -r "$config_file" ]] || die "已有公共配置不可读或不是文件：$config_file"
    printf '保留已有公共配置：%s\n' "$config_file"
else
    config_stage="$(install_run mktemp "$config_dir/.config.XXXXXX")"
    install_run install -m 644 "$source_dir/config.example.json" "$config_stage"
    # Hard-link creation is exclusive: never overwrite another install's config.
    if ! install_run ln -- "$config_stage" "$config_file"; then
        [[ -f "$config_file" && -r "$config_file" ]] || die '公共配置创建失败'
    fi
    install_run rm -f -- "$config_stage"
    config_stage=''
fi
if [[ -e "$runtime_dir" || -L "$runtime_dir" ]]; then
    [[ -d "$runtime_dir" && ! -L "$runtime_dir" ]] || die '运行目录被非预期文件占用'
    cmp -s "$source_dir/unode" "$runtime_dir/unode" &&
        cmp -s "$source_dir/pm2-helper.cjs" "$runtime_dir/pm2-helper.cjs" &&
        [[ -f "$runtime_dir/config-path" && "$(cat "$runtime_dir/config-path")" == "$config_file" ]] || die '已有运行目录内容异常，请检查后重装'
else
    stage="$(install_run mktemp -d "$lib_dir/.install.XXXXXX")"
    install_run chmod 755 "$stage"
    install_run install -m 755 "$source_dir/unode" "$stage/unode"
    install_run install -m 644 "$source_dir/pm2-helper.cjs" "$stage/pm2-helper.cjs"
    config_stage="$(mktemp)"
    printf '%s\n' "$config_file" > "$config_stage"
    install_run install -m 644 "$config_stage" "$stage/config-path"
    rm -f -- "$config_stage"
    config_stage=''
    install_run mv -- "$stage" "$runtime_dir"
    stage=''
fi
"$runtime_dir/unode" --help >/dev/null || die '安装后的命令自检失败'
link_stage="$(install_run mktemp "$bin_dir/.unode.XXXXXX")"
install_run rm -f -- "$link_stage"
install_run ln -s -- "$runtime_dir/unode" "$link_stage"
install_run mv -f -- "$link_stage" "$bin_dir/unode"
link_stage=''

printf '已安装：%s\n运行文件：%s\n公共配置：%s\n' "$bin_dir/unode" "$runtime_dir" "$config_file"
case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) printf '请将以下配置加入 shell 配置文件（如 ~/.zshrc 或 ~/.bashrc），并重新打开终端：\nexport PATH=%q:"$PATH"\n' "$bin_dir" ;;
esac
printf '开始使用：unode\n只处理 PM2：unode --pm2\n私有运行记录：${UNODE_HOME:-$HOME/.unode}\n'
