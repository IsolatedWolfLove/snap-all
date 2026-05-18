# snapz

轻量的目录快照命令行工具。`snapz <path>` 把指定目录打包成 `tar.zst`
归档放进 `~/.snapz-all/`，自带命名管理、`ncdu` 风格的交互界面，
所有破坏性操作都先 dry-run 再二次确认。

> **状态：正式版（v2.0.0）。** 已实现并测试：保存、查看、还原（含自动
> 预还原快照与 `--clean`）、`ncdu` 风格 curses 界面、改名／删除、
> **stats（容量分析）**、**prune（保留策略）**、**revert（按需回
> 滚）**、**undo（一直回到最初）**、**find（跨快照定位文件）**、
> **init/archive/relocate（源目录生命周期与自动迁移）**、**bundle/import（迁移快照）**、
> 多租户 **snapz-server** 远程同步、
> TUI 内 `/` 过滤、所有读类命令的机器可读 **`--json`** 输出，以及
> 一键出包流水线（`scripts/build.sh` → wheel + sdist + `.pyz` + `.deb`）。
> **快照是内容寻址（CAS）**，所以未变更目录再次快照几乎不占空间，
> `snapz gc` 可在删除后回收孤儿 blob。共 248 个单元测试，在普通
> 笔记本上 ~2 秒跑完。

[**English README**](./README.md) · [**详细中文使用手册**](./docs/USAGE.zh.md)

## 为什么用它

现成的快照工具要么偏重（`restic` / `borg` / `kopia` 都为仓库式备份
设计，需要 init + 远端），要么绑死 git（`git stash`、`git-snapshot`）。
`snapz` 想填的空缺是：在任意目录上敲一条命令，几秒后得到一个有名字、
可还原的归档，且**完全不动目录本身**。

## 安装

按场景挑一种：

| 模式 | 文件 | 大小 | 适用 |
|---|---|---|---|
| Debian 包 | `dist/snapz-cli_*_all.deb` | ~10 MB | Ubuntu/Debian 安装；提供 `/usr/bin/snapz` 和 `/usr/bin/snapz-server` |
| Zipapp | `dist/snapz.pyz`、`dist/snapz-server.pyz` | 单个 ~5 MB | 单文件可执行；目标机器需要 `python3 ≥ 3.10` |
| Wheel | `dist/snapz_cli-*.whl` | ~30 KB | `pip install`、当作库用 |

### 用 release 产物安装

```bash
# 1. Debian / Ubuntu
sudo apt install ./dist/snapz-cli_*_all.deb
# 或
sudo dpkg -i dist/snapz-cli_*_all.deb

# 2. Zipapp —— 自包含可执行（zstandard 已内嵌）
install -m 0755 dist/snapz.pyz ~/.local/bin/snapz
install -m 0755 dist/snapz-server.pyz ~/.local/bin/snapz-server

# 3. Wheel
pipx install "dist/snapz_cli-*.whl[zstd]"
# 或
pip install --user "dist/snapz_cli-*.whl[zstd]"
```

### 从源码安装（开发）

```bash
git clone <本仓库>
cd snapz
python3 -m venv .venv
.venv/bin/pip install -e .[dev]
ln -sf "$PWD/.venv/bin/snapz" ~/.local/bin/snapz             # 全 shell 可用
ln -sf "$PWD/.venv/bin/snapz-server" ~/.local/bin/snapz-server
```

> ⚠️ **冲突提醒**：Ubuntu/Debian 在 `/usr/bin/snapz` 自带 `snapd`。
> 请确保 `~/.local/bin`（或你的安装目录）在 `PATH` 中位于 `/usr/bin`
> 之前；不愿覆盖的话也可以给本工具改个名。

## 构建发布产物

`scripts/build.sh` 一把梭：

```bash
./scripts/build.sh all              # wheel + sdist + 客户端/服务端 .pyz + .deb
./scripts/build.sh wheel            # 仅 PEP 517 wheel + sdist
./scripts/build.sh pyz              # 仅 shiv zipapp
./scripts/build.sh deb              # 仅 Debian 包；会先重建 .pyz
./scripts/build.sh smoke            # 对产物跑一次 --version
./scripts/build.sh --clean          # 清空 dist/、build/、.build-venv/
./scripts/build.sh --lang zh all    # 把中文烘进产物，使 --help 默认中文
                                    # （运行时 SNAPZ_LANG 仍可覆盖）
```

脚本会建一个隔离的 `.build-venv/`，装好 `build`、`shiv`、`zstandard`，
最后把产物丢进 `dist/`。它会先清掉 `PYTHONPATH`，避免 ROS 等环境
污染。需要换 Python 时用
`PYTHON=/path/to/python3 ./scripts/build.sh all`。

GitHub Release 由 tag 触发。提交版本号后，先推分支，再推匹配的
`vX.Y.Z` tag：

```bash
git tag v2.0.0
git push origin main v2.0.0
```

发布 workflow 会校验 tag 与 `pyproject.toml` 版本一致，跑测试，
构建 `dist/`，并把 `.deb`、`.pyz`、wheel 和 sdist 上传到 GitHub
Release。

## 速查上手

```bash
$ snapz                              # 交互式快照当前目录
$ snapz .                            # 同上
$ snapz ../some/relative/path        # 任意路径，自动转绝对路径
$ snapz /abs/path

$ snapz save /tmp/proj -n baseline -y  # 脚本化，无交互
$ snapz list                          # 当前目录的 ncdu 风格界面
$ snapz list --text                   # 纯文本表格
$ snapz alist                         # 跨所有目录的全局界面
$ snapz show baseline --path /tmp/proj
$ snapz mv baseline v0.1 --path /tmp/proj
$ snapz rm v0.1 --path /tmp/proj -y
$ snapz restore v0.1 --path /tmp/proj           # dry-run + 两步确认
$ snapz restore v0.1 --path /tmp/proj --clean   # 同时删除多余文件
$ snapz restore v0.1 --path /tmp/proj --no-auto-save -y   # 脚本化

# 带备注的快照
$ snapz save /tmp/proj -n release-1 -y -m "重构 fooBar 之前"
$ snapz show release-1 --path /tmp/proj         # 'note' 行高亮显示

# Diff 与本地排除
$ snapz diff release-1 --path /tmp/proj         # 快照 vs 当前目录（默认 TUI）
$ snapz diff v0.1 v0.2 --path /tmp/proj         # 两个快照对比
$ snapz diff --path /tmp/proj                   # 交互式：先选 A，再选 B
                                               # （B 选择器里包含 [当前目录] 一行）
$ snapz diff release-1 --path /tmp/proj --text  # 不进 curses，直接打印文本
# Diff TUI 里：
#   ↑↓     上下移动
#   ⏎      打开光标行的 unified diff（文件级 diff）
#   space  勾选文件（q/⏎ 退出 unified diff）
#   d      勾选父目录
#   a/n    全选 / 清空
#   e      应用（把已勾选的项追加进本地排除规则）

# 快照名都可省略 —— 不带 name 直接走交互选择器（默认查当前目录）：
$ snapz rm --path /tmp/proj
$ snapz show
$ snapz restore
$ snapz export /tmp/scratch
$ snapz revert
$ snapz mv             # 先挑老名字，再提示输入新名字

# 导出到任意目录（不会触发 auto-pre-restore，绝不动源目录）
$ snapz export v0.1 /tmp/scratch --path /tmp/proj

# 可迁移 bundle（在机器/存储之间搬运快照历史）
$ snapz bundle /tmp/proj /tmp/proj.snapz        # 打包 /tmp/proj 的全部快照
$ snapz import /tmp/proj.snapz                  # 导入为归档历史
$ snapz import /tmp/proj.snapz --path /tmp/proj # 绑定到一个已存在的当前目录

# 通过独立多租户服务端同步
$ snapz-server --data /srv/snapz setup
$ snapz-server --data /srv/snapz user add acme alice
$ snapz-server --data /srv/snapz run \
    --host 0.0.0.0 \
    --port 8765 \
    --tls-cert /etc/snapz/tls/fullchain.pem \
    --tls-key /etc/snapz/tls/privkey.pem \
    --admin-token "$(openssl rand -hex 32)"
# 管理界面：https://server:8765/admin
# 跨域管理应用需要显式加 --cors-origin https://admin.example
# 可选 mTLS 加固：服务端加 --tls-client-ca /etc/snapz/tls/client-ca.pem
# Vben Admin 接入文件：web/vue-vben-admin-snapz/
$ snapz login https://server:8765 --tenant acme --username alice
# 如果启用了 mTLS：
$ snapz login https://server:8765 --tenant acme --username alice \
    --tls-ca /etc/snapz/tls/server-ca.pem \
    --tls-client-cert ~/.config/snapz/client.pem \
    --tls-client-key ~/.config/snapz/client-key.pem
$ snapz push all                                # 上传全部当前/归档 source
$ snapz pull all                                # 拉取全部远端 source 到本地归档
$ snapz adopt remote-src_xxx /tmp/proj          # 把拉下来的归档绑定到目录

# 源目录生命周期
$ snapz init /tmp/proj                          # 写入 .snapz-id，支持跨盘移动识别
$ mv /tmp/proj /tmp/proj-renamed
$ snapz list /tmp/proj-renamed                  # 精确命中时，使用时自动绑定
$ snapz relocate /tmp/proj /tmp/proj-renamed    # 手动绑定到改名后的目录
$ snapz relocate --auto /tmp -y                 # 自动迁移精确 inode/.snapz-id 命中
$ snapz relocate --auto ~ --dry-run             # 只预览，不改动
$ snapz archive list                            # 查看被删除/重建后归档的源目录
$ snapz archive restore <key> baseline /tmp/out # 把归档快照恢复到指定位置

# 容量分析（默认 TUI；--text 输出纯文本）
$ snapz stats                                    # 当前目录
$ snapz stats --all                              # 全部已记录的源目录
$ snapz stats /tmp/proj --text                   # 单目录 + 去重比

# 保留策略：清理旧快照（默认 curses 预览，可调可改）
$ snapz prune --keep-last 5 --path /tmp/proj
$ snapz prune --keep-daily 7 --keep-weekly 4 --path /tmp/proj
$ snapz prune --keep-within-days 30 --protect release-1.0 --path /tmp/proj
$ snapz prune --keep-last 5 --dry-run --text     # 仅报告，不删除

# 按需回滚（只还原指定文件/子树，其它原样保留）
$ snapz revert v0.1 src/main.py --path /tmp/proj          # 一个文件
$ snapz revert v0.1 src docs --path /tmp/proj             # 两个子树
$ snapz revert v0.1 src --delete-extras --path /tmp/proj  # 同时清掉新增文件
$ snapz revert v0.1 --path /tmp/proj                      # 打开多选 TUI

# 撤销 —— 弹出最近一次 restore/revert，可一直 undo 到最初
$ snapz undo                                     # 二次确认后回退最近一次
$ snapz undo -y                                  # 不再确认；连按回退到最初
$ snapz undo --no-clean                          # 保留兜底快照之后新增的文件
# 每次 restore / revert 之前都会自动建一个 auto-pre-* 兜底快照；
# undo 弹出最近的那个并删除它，再次 undo 就走到上一个，依此类推。
# auto-* 默认对 list / 选择器都不可见。

# Find —— 跨所有 CAS 快照查找路径 / 通配
$ snapz find src/main.py                         # 字面路径：哪些快照里包含
$ snapz find src --path /tmp/proj                # 目录前缀 → 整个子树
$ snapz find '**/*.py'                           # 递归 glob（记得用引号防 shell 展开）
$ snapz find src/main.py --json | jq             # 结构化结果，方便脚本处理

# 持久化偏好（~/.snapz-all/_config.json）
$ snapz config list                              # 列出默认值与覆盖
$ snapz config set save_picker true              # 开启交互 save 的事后选择器
$ snapz config get save_picker
$ snapz config unset save_picker
```

### 本地排除（local excludes）

每个目录的额外排除规则保存在
`~/.snapz-all/<key>/_local_excludes`（gitignore 语法，每行一条）。
和 `.snapzignore` / `.gitignore` 不同的是，这个文件**不会进 git**，
只附在本地存储上。可以手改，也可以让 `snapz diff --tui` 或
save picker 帮你追加条目。

### Shell 补全

可选依赖 `argcomplete`：

```bash
pip install argcomplete
# bash:
eval "$(register-python-argcomplete snapz)"
# zsh: 把 shell 改成 zsh
register-python-argcomplete --shell zsh snapz
```

子命令、选项、以及 **快照名**（`rm`、`mv`、`show`、`restore`、
`export`、`diff` 都支持）都会按当前目录的存储动态补全。

### 各 TUI 的按键

`snapz list` / `snapz alist`：

| 键 | 作用 |
|---|---|
| `j` / `k`、`↑` / `↓` | 移动光标 |
| `PgUp` / `PgDn`、`Home` / `End` | 翻页 / 跳到首尾 |
| `Enter` | 弹出快照详情 |
| `r` | 还原（先挂起 TUI，跑常规确认流程） |
| `d` | 删除（就地 yes/no 弹窗） |
| `n` | 改名（就地输入框） |
| `/` | 按名称 + 备注做子串过滤（Esc 清除） |
| `q`、`Esc` | 退出（如果有过滤，Esc 先清过滤而不是退出） |

同样的 `/` 过滤在快照选择器（`rm` / `mv` / `show` / `restore` /
`export` / `diff` / `revert`）里也能用。

stdout 不是 TTY（被管道、被捕获、被重定向）时，`list` / `alist`
会自动退化为纯文本表格；`--text` 显式强制这个行为。

`snapz stats`：

| 键 | 作用 |
|---|---|
| `j` / `k`、`↑` / `↓` | 移动光标 |
| `Enter`、`d`、`→` | 进入该源目录的快照详情视图 |
| `b`、`Esc`、`←`、`Backspace` | 返回顶层概览 |
| `q` | 退出 |

`snapz prune`：

| 键 | 作用 |
|---|---|
| `j` / `k`、`↑` / `↓` | 移动光标 |
| `Space` | 当前行 keep / drop 翻转 |
| `a` / `n` | 全部 drop / 全部 keep |
| `r` | 重置回策略给出的方案 |
| `Enter`、`e` | 应用（删除当前标记 drop 的行） |
| `q`、`Esc` | 取消，不删除 |

`snapz revert`（命令行未给 `paths` 时）：

| 键 | 作用 |
|---|---|
| `j` / `k`、`↑` / `↓` | 移动光标 |
| `Space` | 勾选/取消当前文件 |
| `d` | 勾选/取消当前文件所在父目录（递归） |
| `a` / `n` | 全选 / 清空 |
| `Enter`、`e` | 应用 —— 进入确认提示 |
| `q`、`Esc` | 跳过 / 中止 |

### 国际化（i18n）

`snapz` 自带英文与中文（`zh`）两套 CLI 文案 —— argparse 的 `--help`、
交互提示、确认问句、运行时状态行全部覆盖。运行时、构建时都可切换：

```bash
SNAPZ_LANG=zh snapz --help            # 临时:本次调用走中文
SNAPZ_LANG=zh snapz save .            # 影响 snapz 的所有打印输出
export SNAPZ_LANG=zh                 # 整个 shell 会话

./scripts/build.sh --lang zh all    # 把中文烘进产物，使 --help 默认中文
                                    # （运行时 SNAPZ_LANG 仍可覆盖）
```

优先级顺序：`SNAPZ_LANG` 环境变量 → `snapz/i18n.py` 中烘焙的
`DEFAULT_LANG`（默认 `"en"`） → 英文。任何缺失或未知翻译都会静默
回落到英文，绝不会让 CLI 崩。

### 颜色与视觉风格

`snapz` 自带语义化 ANSI 着色（青色路径、加粗快照名、灰色元信息、
绿色成功、黄色警告、红色错误）。stdout 不是 TTY 时自动关闭，
所以管道到 `grep`、`less`、CI 日志都不会插入控制字符。

| 环境变量 | 效果 |
|---|---|
| `NO_COLOR=1` | 全部强制单色 |
| `FORCE_COLOR=1` | 即使非 TTY 也强制上色（如 `snapz list \| less -R`） |
| `TERM=dumb` | 视为单色 |
| `SNAPZ_LANG=zh` | 把 CLI 文案（--help、提示、运行时输出）切到中文 |
| `SNAPZ_LANG=en` | 即使产物被构建为默认中文，也强制走英文 |

curses TUI 通过 curses 颜色对使用同一套调色板。

交互流程示例（这里没法显示色彩——真实终端里你会看到青色路径、
加粗的名字、绿色进度条）：

```
$ snapz .
📂 /path/to/topics-bot
existing 2 snapshots:
  NAME                       CREATED            SIZE   FILES
  before-refactor            2026-04-28 16:30  124 MB  14,823
  auto-20260428-141200       2026-04-28 14:12  119 MB  14,801

snapshot name [auto-20260428-172500] my-baseline

planning...
  files        14,823
  total size   487 MB
  ignored      312
  large skip   9 file(s) over cap  (use --include-large to keep them)

create snapshot? [y/N] y
████████████████████░░░░  78%  (11567/14823)
✓ saved my-baseline
  archive     ~/.snapz-all/a3f1b2c4d5e6-topics-bot/my-baseline.tar.zst
  size        132 MB  ←  487 MB  (3.7× ratio)
  files       14,823  ·  9.3s  ·  zstd
```

## auto-* 兜底快照与 `snapz undo`

每次破坏性操作（`restore` / `revert`）开始前，snapz 都会先按 CAS
打一个工作目录的兜底快照，命名为 `auto-pre-restore-<时间>` /
`auto-pre-revert-<时间>`。这些快照**默认对你不可见**：
`snapz list`、`snapz alist`、所有交互选择器都会过滤掉它们 —— 它们
是 `snapz undo` 的私货，不是命名历史的一部分。当存在被隐藏的兜底
快照时，`snapz list` 末尾会提示一行：

```
…
（已隐藏 N 个 auto-* —— 加 --all 可显示）
```

`snapz undo` 弹出最近一次兜底快照、用 `auto_save=False, --clean`
还原它，**还原成功后把这个兜底快照也删掉**，于是再 undo 一次就走
到上一步。一直 undo 直到没有兜底快照剩下，你就回到最初状态：

```bash
$ snapz restore release-1.0    # 自动 auto-pre-restore-T1（捕获了 restore 之前的状态）
$ snapz revert release-1.0 src # 自动 auto-pre-revert-T2（捕获了 T1 之后的状态）
$ snapz undo                   # 回到 T2 时刻的状态（并消费这个兜底）
$ snapz undo                   # 再回一步：回到最初状态
$ snapz undo                   # 报错：没有可回退的兜底快照了
```

如果想看 / 手工清理这些兜底快照：`snapz list --all`、`snapz rm --all`
都会把它们暴露出来。

## `snapz find` —— 跨快照定位文件

```bash
snapz find src/main.py            # 字面路径
snapz find src                    # 目录前缀 → 整个子树
snapz find '**/*.py'              # 递归 glob（一定要加引号防 shell 展开）
snapz find docs/intro.md --json   # 结构化输出：by_path → [hits…]
```

输出按源相对路径分组，最新的快照排最前；如果某个快照里这条路径的
内容和它「下一新」快照里不同，会标 `← 已变化`。底层就是查每个
manifest 里的 `path → sha256` 表，几百个快照也能秒级返回，**不需要
解归档**。

## JSON 输出（`--json`）

`save`、`list`、`alist`、`show`、`stats`、`gc`、`find`、`undo` 在
带 `--json` 时把结果以 JSON 写到 stdout。这个开关位置无关
（`snapz --json list` 和 `snapz list --json` 等价）；进度条、ANSI
样式仍然走 stderr。

```bash
snapz list --json | jq '.snapshots[] | select(.size_bytes > 1e6) | .name'
snapz find 'src/**/*.py' --json | jq '.by_path | keys'
snapz undo --json -y          # 脚本里安全的回退，必须显式 -y，JSON 模式不会提示
```

`snapz undo --json` 不带 `-y` 时返回
`{"undone": false, "reason": "needs-confirmation", "target": …}`
并以非零退出码结束，方便 CI 在真正回退前先 dry-run 看一眼。

## stats、prune、revert

这三个子命令是日常维护的“收尾三件套”，攒了几个月快照之后会用得着：

- **`snapz stats`** — 按源目录拆解存储用量：快照数、磁盘占用、逻辑
  尺寸（去重前的累计大小）、以及由此算出的去重比。TUI 默认按磁盘
  占用从大到小排序；按 `Enter` 进入某个目录看它名下的全部快照。
  加 `--all` 可把顶层视图扩展到所有已记录的目录。

- **`snapz prune`** — 按保留策略删快照。规则是 *并集*（满足任一即
  保留）：

  | 选项 | 含义 |
  |---|---|
  | `--keep-last N` | 保留最新的 N 个快照 |
  | `--keep-within-days D` | 保留最近 D 天内创建的全部快照 |
  | `--keep-daily N` | 最近 N 天里每天保留一份（取当天最新） |
  | `--keep-weekly N` | 最近 N 个 ISO 周里每周保留一份 |
  | `--protect NAME` | 永远不删这个快照（可重复传） |

  默认会进入 curses TUI，先展示 keep/drop 划分，让你逐行翻转，
  再统一应用。`-y` 跳过 TUI；`--dry-run` 只报告不删除；`--no-gc`
  暂时保留孤儿 blob 不回收。**至少要给一条规则**（或 `--protect`），
  纯空规则会报错——避免手抖一把抹平所有快照。

- **`snapz revert`** — 把某个快照里指定的路径写回当前目录，其它
  文件原封不动。命令行可以直接传相对路径（文件或目录），也可以省略
  让 picker 弹出多选界面。写入前会先做一份 `auto-pre-revert-*` 兜底
  快照（用 `--no-auto-save` 关闭），所以这个操作随时可逆。
  `--delete-extras` 会顺手把指定路径下、快照里没有的多余文件清掉，
  适合“让这一颗子树和某快照一字不差地对齐”的场景。**仅支持 CAS
  格式快照**，对老的 `.tar.zst` 归档会直接报错（请用 `restore` /
  `export`）。

## 存储结构

`snapz` 走 **内容寻址存储（CAS）**：每个唯一文件内容（按 sha256
索引）只存一份 zstd 压缩 blob，被所有引用它的快照共享；快照本身
就是一份很小的 manifest。

```
~/.snapz-all/
├── registry.json                       # 路径 <-> key 的反查表
└── <sha1[:12]>-<basename>/             # 每个被快照的源目录一个文件夹
    ├── _meta.json                      # { abspath, first_seen, last_used, snapshot_count }
    ├── objects/                        # blob 池（按 sha256 前两位分片）
    │   └── ab/
    │       └── abcdef1234...           # zstd 压缩后的文件内容
    ├── snapshots/
    │   ├── before-refactor.manifest.json   # path -> sha256 + mode + mtime
    │   └── auto-20260428.manifest.json
    ├── before-refactor.meta.json       # { name, source, created, size_bytes, file_count, ... }
    └── auto-20260428.meta.json
```

**实际收益**：对一个 500 MB 的项目重新快照、内容没变，几乎是 0 字节
开销（只多一份 manifest，KB 级别）；改了一个文件就只多一个 blob。
本格式上线前用 `.tar.zst` 留下的快照仍然可还原，与新格式并存。

存储根目录默认是 `~/.snapz-all/`。设置 `SNAPZ_ALL_ROOT` 可覆盖（测试用）。

文件夹权限被收紧到 `700`，blob/manifest 收紧到 `600`。

### 回收孤儿 blob

删一个快照只删它的 manifest + meta，被其它快照仍在引用的 blob 不动。
当一个 blob 不再被任何快照引用时，它会留着直到你跑：

```bash
snapz gc                  # 回收当前目录的孤儿 blob
snapz gc --path /a/b      # 指定目录
snapz gc --all            # 跨所有已记录目录
snapz gc --dry-run        # 只报告，不删
```

`snapz save -y` / `snapz restore -y` 已经把别的事都做了；`gc` 是唯一
偶尔需要主动跑的命令（删了老快照之后再跑就好，不删就不必跑）。

## 忽略规则

扫描时默认合并三类来源：

1. 内置默认值：`__pycache__/`、`node_modules/`、`.venv/`、`venv/`、
   `*.pyc`、`.DS_Store`、`dist/`、`build/` 等。
2. 源根目录下的 `.gitignore`、`.git/info/exclude`。
3. 源根目录下的 `.snapzignore`。

匹配器是 gitignore 风格但故意做得更简洁：`!negation`、嵌套
`.gitignore` 文件**暂未实现**。

超过 100 MiB 的文件会被跳过并打 warning。需要一并打包请加
`--include-large`。

## 压缩

如果有装 `zstandard`，`snapz` 会用 `.tar.zst`；否则退回 `.tar.gz`
（标准库）。要强制用 gzip：`snapz --no-zstd ...`。可迁移 `.snapz`
bundle 和远程 push 也走同样的 zstd 优先策略，服务端会在接收前校验
bundle SHA-256。

## 当库用

所有命令都有等价的 Python 函数，方便嵌进 chatops 或脚本：

```python
from snapz import api

outcome = api.save("/path/to/proj", "before-refactor")
print(outcome.snapshot.size_bytes)

for snapz in api.list_snapshots("/path/to/proj"):
    print(snapz.name, snapz.created, snapz.size_bytes)

# 还原（auto-pre-restore + clean 在 api 里默认关闭，按需开）
estimate = api.restore_estimate("/path/to/proj", "before-refactor")
print(len(estimate.new_files), len(estimate.overwritten_files))
api.restore("/path/to/proj", "before-refactor", auto_save=True, clean=False)

api.rename("/path/to/proj", "before-refactor", "v0.1")
api.delete("/path/to/proj", "v0.1")

# 容量分析
for entry in api.stats():                  # 全部源目录，按磁盘占用排序
    print(entry.abspath, entry.snapshot_count,
          entry.on_disk_bytes, f"{entry.dedup_ratio:.1f}x")

# 保留策略
plan = api.plan_prune("/path/to/proj", keep_last=10, keep_weekly=4)
print(len(plan.keep), "保留,", len(plan.drop), "待删")
outcome = api.execute_prune(plan, dry_run=False)
print(outcome.deleted, outcome.bytes_freed)

# 按需回滚（默认会先做 auto-pre-revert 兜底快照）
result = api.revert("/path/to/proj", "v0.1", ["src/main.py"])
print(result.reverted_count, "个文件已写回, pre-revert =",
      result.pre_revert.name if result.pre_revert else None)
```

`api.estimate(path)` 只跑 dry-run walker，返回预计的文件数与字节数。
`api.restore_estimate(path, name)` 把归档与现状做 diff，给出会新增、
覆盖、多余的文件列表。

## 路线图

- **M1 ✅** — 非 TUI 命令面
- **M2 ✅** — `snapz list` / `snapz alist` 的 curses TUI（`d` / `n` 键）
- **M3 ✅** — `snapz restore <name>` + 自动预还原 + TUI 的 `r` 键
- **M5 ✅** — 自动出包（wheel/sdist/zipapp/standalone binary）
- **M6 ✅** — `snapz stats` / `snapz prune` / `snapz revert` + curses 选择器
- **M4** — 完整 `.snapzignore` 语义（取反、嵌套）、`gc` 策略、TUI 内排序与过滤

## 开发

```bash
env -u PYTHONPATH .venv/bin/python -m pytest tests/
```

`env -u PYTHONPATH` 是为了挡掉 ROS 等环境注入的 pytest 插件；
干净 shell 可以省略。

只做语法检查：

```bash
.venv/bin/python -m py_compile snapz/*.py
```

## 许可证

MIT。
