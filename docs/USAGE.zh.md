# snapz 中文使用手册

本文是一份面向日常使用的完整手册，覆盖本地快照、恢复、对比、清理、迁移、
远程同步和排错。快速示例可以先看仓库根目录的 `README.zh.md`。

## 1. snapz 是什么

`snapz` 是一个目录快照工具。它把某个目录在某一刻的内容保存成一个可命名、
可恢复的快照，默认存放在 `~/.snapz-all/`。它适合这些场景：

- 修改项目代码前保存一个可回退点。
- 做危险脚本、批量重构、迁移文件前保存工作目录状态。
- 在多个快照之间查看差异、恢复单个文件或子目录。
- 把某个目录的历史打成 bundle 搬到另一台机器。
- 通过 `snapz-server` 在多台机器之间同步快照历史。

snapz 的新格式使用内容寻址存储（CAS）：相同文件内容只保存一次，所以对未变更
目录重复保存通常只增加少量元数据。

## 2. 安装

### 2.1 从 release 产物安装

如果已有构建产物：

```bash
install -m 0755 dist/snapz.pyz ~/.local/bin/snapz
install -m 0755 dist/snapz-server.pyz ~/.local/bin/snapz-server
```

或安装 wheel：

```bash
pipx install "dist/snapz_cli-*.whl[zstd]"
```

`[zstd]` 可选依赖会安装 `zstandard`，用于更快、更小的压缩格式。没有它时，
snapz 会自动退回 gzip。

### 2.2 从源码安装开发版

```bash
git clone <repo-url>
cd snapz
python3 -m venv .venv
.venv/bin/pip install -e .[dev]
.venv/bin/python -m snapz --help
```

如果希望全局可用：

```bash
ln -sf "$PWD/.venv/bin/snapz" ~/.local/bin/snapz
ln -sf "$PWD/.venv/bin/snapz-server" ~/.local/bin/snapz-server
```

### 2.3 构建发布产物

```bash
./scripts/build.sh all
./scripts/build.sh smoke
```

常用构建命令：

| 命令 | 作用 |
|---|---|
| `./scripts/build.sh all` | 构建 wheel、sdist、客户端/服务端 zipapp |
| `./scripts/build.sh wheel` | 只构建 wheel 和 sdist |
| `./scripts/build.sh pyz` | 只构建 zipapp |
| `./scripts/build.sh smoke` | 对已有产物做启动冒烟测试 |
| `./scripts/build.sh --clean` | 清理构建目录 |
| `./scripts/build.sh --lang zh all` | 构建默认中文输出的产物 |

## 3. 核心概念

### 3.1 源目录

源目录是你要保护的目录，比如 `/home/me/project`。snapz 会把传入路径解析成绝对
路径，并为它建立一个独立的存储目录。

```bash
snapz save /home/me/project -n before-refactor -y
```

### 3.2 快照名

快照名用于后续恢复、删除、对比。你可以通过 `-n/--name` 指定：

```bash
snapz save . -n baseline -y
```

不指定时会生成 `auto-...` 风格名称。交互式保存会提示输入名称。

### 3.3 存储根目录

默认存储根目录是：

```text
~/.snapz-all/
```

可以通过环境变量覆盖：

```bash
SNAPZ_ALL_ROOT=/data/snapz-store snapz list .
export SNAPZ_ALL_ROOT=/data/snapz-store
```

常见存储文件：

| 文件/目录 | 说明 |
|---|---|
| `registry.json` | 已记录源目录索引 |
| `<key>/_meta.json` | 单个源目录的摘要元数据 |
| `<key>/<name>.meta.json` | 单个快照的元数据 |
| `<key>/snapshots/*.manifest.json` | CAS 快照清单 |
| `<key>/snapshots/*.manifest.json.zst` | 压缩后的 CAS 快照清单 |
| `objects/` | 全局内容寻址 blob 池 |
| `_refs.index` | 全局 blob 引用计数索引 |
| `<key>/_filecache.json` | 保存加速用的文件哈希缓存 |
| `<key>/_local_excludes` | 本机独有的排除规则 |
| `<key>/_events.log` | 操作日志 |

### 3.4 自动安全快照

`restore` 和 `revert` 默认会先创建一个 `auto-pre-*` 快照，再执行写回操作。
如果恢复后发现结果不对，可以用 `snapz undo` 回到恢复前状态。

`auto-*` 快照默认在列表和选择器中隐藏。加 `--all` 可以显示。

## 4. 全局选项和环境变量

### 4.1 全局选项

```bash
snapz --help
snapz --version
snapz --json list
snapz list --json
snapz --no-zstd save . -n gzip-test -y
```

| 选项 | 说明 |
|---|---|
| `--version` | 打印版本 |
| `--json` | 输出机器可读 JSON，适合管道到 `jq` |
| `--no-zstd` | 本次调用禁用 zstd，强制 gzip |
| `--minimal` | 为后续 minimal UI 流程预留的全局开关；当前脚本中更建议显式使用各命令的 `--text`、`-y` |

### 4.2 环境变量

| 环境变量 | 说明 |
|---|---|
| `SNAPZ_ALL_ROOT` | 覆盖本地快照存储根目录 |
| `SNAPZ_LANG=zh` | 强制中文输出 |
| `SNAPZ_LANG=en` | 强制英文输出 |
| `SNAPZ_SAVE_WORKERS` | 默认保存并发 worker 数 |
| `SNAPZ_ZSTD_LEVEL` | zstd 压缩等级，默认 `3` |
| `SNAPZ_GZIP_LEVEL` | gzip 压缩等级，默认 `6` |
| `SNAPZ_SERVER_DATA` | snapz-server 默认数据目录 |
| `SNAPZ_SERVER_HOST` | snapz-server 默认监听地址 |
| `SNAPZ_SERVER_PORT` | snapz-server 默认监听端口 |
| `SNAPZ_SERVER_ADMIN_TOKEN` | snapz-server 管理接口 token |
| `SNAPZ_SERVER_MAX_BUNDLE_MB` | snapz-server 单次上传 bundle 上限，单位 MiB |
| `SNAPZ_SERVER_CORS_ORIGIN` | snapz-server 允许的跨域管理前端 Origin，多个用逗号分隔 |
| `SNAPZ_SERVER_TLS_CERT` | snapz-server HTTPS 证书路径 |
| `SNAPZ_SERVER_TLS_KEY` | snapz-server HTTPS 私钥路径 |
| `SNAPZ_SERVER_TLS_CLIENT_CA` | snapz-server mTLS 客户端 CA 路径 |

示例：

```bash
SNAPZ_LANG=zh snapz --help
SNAPZ_SAVE_WORKERS=4 snapz save . -n v1 -y
SNAPZ_ALL_ROOT=/tmp/snapz-test snapz list .
SNAPZ_ZSTD_LEVEL=10 snapz save . -n smaller -y
```

## 5. 第一次上手

### 5.1 交互式保存当前目录

```bash
cd /path/to/project
snapz
```

这会进入交互式保存流程：显示现有快照、提示快照名、扫描文件、确认后写入快照。

也可以显式写：

```bash
snapz .
snapz /path/to/project
```

### 5.2 脚本化保存

```bash
snapz save /path/to/project -n baseline -y
```

常用参数：

| 参数 | 说明 |
|---|---|
| `-n, --name NAME` | 指定快照名 |
| `-y, --yes` | 跳过确认 |
| `--overwrite` | 覆盖同名快照 |
| `--include-large` | 包含超过默认上限的大文件 |
| `--no-cache` | 本次保存禁用文件哈希缓存 |
| `--workers N` | 本次保存使用 N 个 worker |
| `-m, --message NOTE` | 给快照写备注 |

示例：

```bash
snapz save . -n before-upgrade -m "升级依赖前" -y
snapz save . -n latest --overwrite -y
snapz save . -n full --include-large -y
snapz save . -n serial --workers 1 -y
```

### 5.3 查看当前目录快照

```bash
snapz list
snapz list --text
snapz list --all --text
```

`list` 默认查看当前目录，也可指定目录：

```bash
snapz list /path/to/project
```

### 5.4 查看所有目录快照

```bash
snapz alist
snapz alist --text
snapz alist --all --text
```

`alist` 会跨所有已记录源目录展示快照。

## 6. 管理快照

### 6.1 查看快照详情

```bash
snapz show baseline
snapz show baseline --path /path/to/project
snapz show baseline --path /path/to/project --json
```

不提供名称且 stdout 是 TTY 时，会打开选择器：

```bash
snapz show --path /path/to/project
```

### 6.2 改名

```bash
snapz mv old-name new-name --path /path/to/project
```

交互式选择旧名称：

```bash
snapz mv --path /path/to/project
```

### 6.3 删除

```bash
snapz rm old-name --path /path/to/project
snapz rm old-name --path /path/to/project -y
```

删除只删除快照元数据和对应 manifest/legacy archive。内容寻址 blob 可能还被其他
快照引用，真正回收空间请运行：

```bash
snapz gc --all
```

### 6.4 保护和取消保护

保护后的快照不能被普通删除或 prune 删除。

```bash
snapz protect release-1.0 --path /path/to/project
snapz unprotect release-1.0 --path /path/to/project
```

### 6.5 标签

```bash
snapz tag add release-1.0 stable prod --path /path/to/project
snapz tag rm release-1.0 prod --path /path/to/project
snapz tag list --path /path/to/project
```

标签可用于 `prune --keep-tag` 保留某类快照。

### 6.6 操作日志

```bash
snapz log --path /path/to/project
snapz log --path /path/to/project -n 20
snapz log --path /path/to/project --kind save,restore
snapz log --all
snapz log --all --json
```

日志用于审计保存、恢复、删除、重命名、保护、tag 等操作。

## 7. 恢复、导出、回滚

### 7.1 恢复整个源目录

```bash
snapz restore baseline --path /path/to/project
```

默认行为：

1. 计算恢复预览。
2. 创建 `auto-pre-restore-*` 兜底快照。
3. 把快照内容写回源目录。
4. 默认不删除快照中不存在的额外文件。

脚本化恢复：

```bash
snapz restore baseline --path /path/to/project -y
```

清理额外文件：

```bash
snapz restore baseline --path /path/to/project --clean
```

跳过自动兜底快照：

```bash
snapz restore baseline --path /path/to/project --no-auto-save -y
```

`--no-auto-save` 会降低安全性；只建议在已有外部备份或 CI 临时目录中使用。

### 7.2 导出到其他目录

`export` 不会修改源目录，也不会创建 auto-pre 快照。

```bash
snapz export baseline /tmp/project-baseline --path /path/to/project
snapz export baseline /tmp/project-baseline --path /path/to/project --overwrite
```

适合检查历史版本、复制一份旧版本内容、做临时比较。

### 7.3 只回滚部分文件或子目录

```bash
snapz revert baseline src/main.py --path /path/to/project
snapz revert baseline src docs --path /path/to/project
```

删除目标子树下快照中不存在的额外文件：

```bash
snapz revert baseline src --path /path/to/project --delete-extras
```

交互式选择文件：

```bash
snapz revert baseline --path /path/to/project
```

脚本化：

```bash
snapz revert baseline src/main.py --path /path/to/project -y
```

### 7.4 撤销最近一次 restore/revert

```bash
snapz undo --path /path/to/project
snapz undo --path /path/to/project -y
```

默认 `undo` 会按最近的 `auto-pre-*` 快照恢复，并清理恢复后多出来的文件。
保留新增文件：

```bash
snapz undo --path /path/to/project --no-clean
```

`undo` 可以连续执行，逐层回到更早的恢复/回滚前状态。

## 8. 对比、查找和浏览内容

### 8.1 对比快照和当前目录

```bash
snapz diff baseline --path /path/to/project
snapz diff baseline --path /path/to/project --text
```

### 8.2 对比两个快照

```bash
snapz diff baseline after-refactor --path /path/to/project
snapz diff baseline after-refactor --path /path/to/project --text
```

不传参数时，TTY 下会打开选择器：

```bash
snapz diff --path /path/to/project
```

### 8.3 查找包含某路径或 glob 的快照

```bash
snapz find src/main.py --path /path/to/project
snapz find src --path /path/to/project
snapz find '**/*.py' --path /path/to/project
snapz find '**/*.py' --path /path/to/project --json
```

包含 auto 快照：

```bash
snapz find src/main.py --path /path/to/project --all
```

### 8.4 打印快照中的单个文件

```bash
snapz cat baseline src/main.py --path /path/to/project
snapz cat baseline README.md --path /path/to/project --raw > README.old.md
```

二进制文件默认不会直接往终端写入。确认要输出二进制：

```bash
snapz cat baseline image.png --path /path/to/project --raw > image.png
snapz cat baseline image.png --path /path/to/project --binary-ok > image.png
```

### 8.5 浏览快照内容

```bash
snapz browse baseline --path /path/to/project
snapz browse baseline --path /path/to/project --filter main.py
```

非 TTY 下会输出路径列表。

## 9. 容量分析、清理和维护

### 9.1 查看容量统计

当前目录：

```bash
snapz stats
snapz stats . --text
```

所有目录：

```bash
snapz stats --all
snapz stats --all --json
```

注意：全局 `stats --all` 会优先使用 `_meta.json` 中的缓存摘要，以便大量源目录
时快速返回；这种快速路径重点保证 `SNAPS`、`ON_DISK`、`NEWEST`。如果需要完整
`LOGICAL`、`DEDUP`、最大快照等细节，请对具体目录运行 `snapz stats <path>`。

字段含义：

| 字段 | 说明 |
|---|---|
| `SNAPS` | 快照数量 |
| `ON_DISK` | 当前源目录在 snapz 存储中占用的估算空间 |
| `LOGICAL` | 快照中原始文件大小总和 |
| `DEDUP` | 去重倍率 |
| `NEWEST` | 最新快照时间 |

### 9.2 按保留策略清理快照

只保留最新 5 个：

```bash
snapz prune --path /path/to/project --keep-last 5
```

保留最近 30 天和每周 4 个代表快照：

```bash
snapz prune --path /path/to/project --keep-within-days 30 --keep-weekly 4
```

保留某个标签：

```bash
snapz prune --path /path/to/project --keep-last 5 --keep-tag stable
```

预览，不删除：

```bash
snapz prune --path /path/to/project --keep-last 5 --dry-run --text
```

脚本化执行：

```bash
snapz prune --path /path/to/project --keep-last 5 -y
```

不在 prune 后运行 GC：

```bash
snapz prune --path /path/to/project --keep-last 5 -y --no-gc
```

### 9.3 自动 prune

可以把保留规则写入配置，让每次保存后自动清理：

```bash
snapz config set retention.keep_last 10
snapz config set retention.keep_weekly 4
snapz config set retention.auto_prune_after_save true
```

关闭：

```bash
snapz config set retention.auto_prune_after_save false
```

### 9.4 回收孤儿 blob

删除快照后，全局 blob 池里可能有不再被引用的内容。运行：

```bash
snapz gc --path /path/to/project
snapz gc --all
snapz gc --all --dry-run
snapz gc --all --rebuild-index
```

`--rebuild-index` 会先重建引用计数索引，再执行 GC。

### 9.5 检查和修复存储

```bash
snapz check /path/to/project
snapz check --all
snapz check --all --deep
snapz check --all --fix
```

| 参数 | 说明 |
|---|---|
| `--all` | 检查所有源目录 |
| `--deep` | 深度校验 blob 内容 |
| `--fix` | 执行安全修复，如删除临时文件、重建引用索引、移除文件缓存 |

### 9.6 迁移旧存储格式

早期版本可能把 blob 放在每个源目录自己的 `objects/` 下。迁移到 v3 全局池：

```bash
snapz migrate /path/to/project --to v3
snapz migrate --all --to v3
snapz migrate --all --to v3 --dry-run
```

## 10. 配置和本地排除

### 10.1 查看配置

```bash
snapz config list
snapz config get color
snapz config get save_picker
```

### 10.2 设置配置

```bash
snapz config set color never
snapz config set color always
snapz config set save_picker true
snapz config set ui_mode minimal
snapz config set update_check.enabled false
```

取消覆盖，回到默认值：

```bash
snapz config unset color
snapz config unset save_picker
```

当前配置项：

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `ui_mode` | `tui` | UI 模式，取值 `tui` 或 `minimal` |
| `save_picker` | `false` | 保存时是否打开选择器，帮助把条目加入本地排除 |
| `color` | `auto` | 彩色输出：`auto`、`always`、`never` |
| `update_check.enabled` | `true` | 每天第一次启动是否后台检查 GitHub 更新；有更新时下次启动提示 |
| `retention.keep_last` | `0` | prune 默认保留最新 N 个 |
| `retention.keep_daily` | `0` | prune 默认按天保留 N 个 |
| `retention.keep_weekly` | `0` | prune 默认按周保留 N 个 |
| `retention.keep_within_days` | `0` | prune 默认保留最近 N 天 |
| `retention.auto_prune_after_save` | `false` | 保存后自动应用保留策略 |

### 10.3 排除规则来源

snapz 构建快照时会考虑：

- 内置默认排除规则。
- 项目里的 `.gitignore`。
- 项目里的 `.snapzignore`。
- 存储目录里的 `_local_excludes`。

`_local_excludes` 是每个源目录独有的本机规则，位于：

```text
~/.snapz-all/<key>/_local_excludes
```

它适合排除只在本机出现、但不想提交到项目仓库的文件，比如临时目录、下载缓存、
IDE 生成物。

启用保存选择器：

```bash
snapz config set save_picker true
snapz
```

也可以通过 diff TUI 把选中的路径加入本地排除。

## 11. 源目录生命周期

### 11.1 初始化源目录标记

```bash
snapz init /path/to/project
```

这会在源目录写入 `.snapz-id`，帮助 snapz 在目录重命名或跨盘移动后识别它。

强制重写：

```bash
snapz init /path/to/project --force
```

### 11.2 手动迁移目录绑定

如果目录从旧路径移动到新路径：

```bash
snapz relocate /old/project /new/project
```

### 11.3 自动扫描迁移

```bash
snapz relocate --auto /home/me --dry-run
snapz relocate --auto /home/me -y
```

自动迁移只处理精确命中的候选，避免误绑定。

### 11.4 归档源目录

如果原目录不存在，或者同一路径被删除后重新创建，旧快照会作为归档源保留。

查看归档源：

```bash
snapz archive list
```

把归档快照恢复到指定目录：

```bash
snapz archive restore <archive-key> baseline /tmp/out
snapz archive restore <archive-key> baseline /tmp/out --overwrite
```

把归档源重新绑定到当前目录：

```bash
snapz adopt <archive-key> /path/to/project
```

`adopt` 常用于远程 pull 后，把远端归档历史绑定到本机目录。

## 12. 可迁移 bundle

### 12.1 导出 bundle

```bash
snapz bundle /path/to/project /tmp/project.snapz
snapz bundle /path/to/project /tmp/project.snapz --overwrite
```

导出归档源：

```bash
snapz bundle <archive-key> /tmp/project.snapz --archive
```

bundle 包含某个源目录的快照元数据、manifest 和所需 blob，适合离线搬运。

### 12.2 导入 bundle

导入为归档历史：

```bash
snapz import /tmp/project.snapz
```

导入并绑定到现有目录：

```bash
snapz import /tmp/project.snapz --path /path/to/project
```

覆盖同名快照：

```bash
snapz import /tmp/project.snapz --path /path/to/project --overwrite
```

## 13. 远程同步

远程同步由 `snapz-server` 提供。客户端通过 `login` 保存 token，然后用
`push all` / `pull all` 同步所有源。

### 13.1 初始化服务端

```bash
snapz-server --data /srv/snapz setup
snapz-server --data /srv/snapz tenant add acme
snapz-server --data /srv/snapz user add acme alice
```

非交互设置密码：

```bash
snapz-server --data /srv/snapz user add acme alice --password 'change-me'
```

### 13.2 启动服务端

推荐用 `snapz-server init` 从头初始化服务端配置和 systemd 自启动：

```bash
sudo snapz-server init --data /srv/snapz --host 0.0.0.0 --port 8765
sudo editor /etc/default/snapz-server
```

`init` 会创建 `/etc/default/snapz-server`、初始化数据目录、写入
`/etc/systemd/system/snapz-server.service`，并启用/启动服务。已有配置默认保留；
需要重写时加 `--force`。之后用 `sudo snapz-server update` 升级程序，配置不会被覆盖。

本机测试：

```bash
snapz-server --data /srv/snapz run --host 127.0.0.1 --port 8765
```

启用管理界面 token：

```bash
snapz-server --data /srv/snapz run \
  --host 0.0.0.0 \
  --port 8765 \
  --admin-token "$(openssl rand -hex 32)"
```

使用 HTTPS：

```bash
snapz-server --data /srv/snapz run \
  --host 0.0.0.0 \
  --port 8765 \
  --tls-cert /etc/snapz/tls/fullchain.pem \
  --tls-key /etc/snapz/tls/privkey.pem \
  --admin-token "$SNAPZ_SERVER_ADMIN_TOKEN"
```

启用 mTLS：

```bash
snapz-server --data /srv/snapz run \
  --host 0.0.0.0 \
  --port 8765 \
  --tls-cert /etc/snapz/tls/fullchain.pem \
  --tls-key /etc/snapz/tls/privkey.pem \
  --tls-client-ca /etc/snapz/tls/client-ca.pem
```

允许跨域管理前端：

```bash
snapz-server --data /srv/snapz run \
  --cors-origin https://admin.example
```

### 13.3 客户端登录

```bash
snapz login http://127.0.0.1:8765 --tenant acme --username alice
```

带密码参数：

```bash
snapz login http://127.0.0.1:8765 \
  --tenant acme \
  --username alice \
  --password 'change-me'
```

HTTPS 自定义 CA：

```bash
snapz login https://server.example \
  --tenant acme \
  --username alice \
  --tls-ca /etc/snapz/tls/server-ca.pem
```

mTLS：

```bash
snapz login https://server.example \
  --tenant acme \
  --username alice \
  --tls-ca /etc/snapz/tls/server-ca.pem \
  --tls-client-cert ~/.config/snapz/client.pem \
  --tls-client-key ~/.config/snapz/client-key.pem
```

登录信息保存在本地存储根目录的 `_remote.json`。

退出登录：

```bash
snapz logout
```

### 13.4 推送和拉取

推送全部本地源：

```bash
snapz push all
```

拉取全部远端源：

```bash
snapz pull all
```

拉取结果默认作为归档源保存；绑定到本机目录：

```bash
snapz archive list
snapz adopt remote-src_xxx /path/to/project
```

### 13.5 服务端管理

```bash
snapz-server --data /srv/snapz doctor
snapz-server --data /srv/snapz user reset-password acme alice
snapz-server --data /srv/snapz device revoke <device-id>
```

如果启动时设置了 `--admin-token`，浏览器访问：

```text
http://server:8765/admin
```

## 14. JSON 和脚本集成

大多数读类命令和部分写类命令支持 `--json`：

```bash
snapz list --json | jq
snapz alist --json | jq
snapz show baseline --json
snapz stats --all --json | jq
snapz log --all --json | jq
```

写类命令通常需要 `-y` 才会在 JSON 模式下执行破坏性操作：

```bash
snapz rm old --path /path/to/project --json -y
snapz restore baseline --path /path/to/project --json -y
snapz prune --path /path/to/project --keep-last 5 --json -y
```

脚本中建议：

- 显式写 `--path`，不要依赖当前目录。
- 显式写 `-y`，避免卡在确认提示。
- 对列表输出用 `--json`，不要解析彩色文本。
- 在 CI 中设置独立 `SNAPZ_ALL_ROOT`，避免污染用户真实快照库。

## 15. TUI 按键

### 15.1 list / alist

| 键 | 作用 |
|---|---|
| `j` / `k`、`↑` / `↓` | 移动光标 |
| `PgUp` / `PgDn` | 翻页 |
| `Home` / `End` | 跳到首尾 |
| `Enter` | 查看详情或选择 |
| `r` | 恢复 |
| `d` | 删除 |
| `n` | 改名 |
| `/` | 过滤 |
| `Esc` | 清除过滤或退出 |
| `q` | 退出 |

### 15.2 diff / revert / browse

| 键 | 作用 |
|---|---|
| `↑` / `↓` | 移动 |
| `Enter` | 打开/确认当前项 |
| `Space` | 勾选或切换 |
| `a` | 全选 |
| `n` | 清空选择 |
| `/` | 过滤 |
| `q` / `Esc` | 返回或退出 |

实际可用按键以当前界面底部提示为准。

## 16. 推荐工作流

### 16.1 重构前保存，失败后回滚

```bash
snapz save . -n before-refactor -m "大重构前" -y
# 修改代码
snapz diff before-refactor --text
snapz restore before-refactor -y
```

如果恢复后又后悔：

```bash
snapz undo -y
```

### 16.2 只拿回一个旧文件

```bash
snapz find src/main.py
snapz cat baseline src/main.py > /tmp/main.py.old
snapz revert baseline src/main.py -y
```

### 16.3 每天保留若干快照

```bash
snapz config set retention.keep_last 20
snapz config set retention.keep_daily 14
snapz config set retention.keep_weekly 8
snapz config set retention.auto_prune_after_save true
snapz save . -n work-$(date +%Y%m%d-%H%M) -y
```

### 16.4 临时隔离测试

```bash
export SNAPZ_ALL_ROOT=/tmp/snapz-demo-store
snapz save /tmp/demo -n v1 -y
snapz list /tmp/demo --text
rm -rf "$SNAPZ_ALL_ROOT"
```

## 17. 排错

### 17.1 找不到快照

检查是否传了正确源目录：

```bash
snapz list /path/to/project
snapz alist --text
```

如果目录移动过：

```bash
snapz archive list
snapz relocate /old/path /new/path
```

### 17.2 删除后空间没有明显变小

删除快照不会立刻删除仍可能被引用的 blob。运行：

```bash
snapz gc --all --dry-run
snapz gc --all
```

### 17.3 怀疑存储损坏

```bash
snapz check --all
snapz check --all --deep
snapz check --all --fix
```

### 17.4 快照过慢

可以尝试：

```bash
snapz save . -n test -y --workers 8
snapz save . -n no-cache-test -y --no-cache
snapz config set save_picker true
```

也应检查 `.gitignore`、`.snapzignore` 或 `_local_excludes` 是否排除了大型无关目录，
比如 `node_modules/`、`.venv/`、`build/`、`dist/`。

### 17.5 文案语言不对

```bash
SNAPZ_LANG=zh snapz --help
SNAPZ_LANG=en snapz --help
```

### 17.6 命令名冲突

某些系统可能已有 `/usr/bin/snapz`。确认当前执行的是哪个：

```bash
command -v snapz
snapz --version
```

把 `~/.local/bin` 放到 `PATH` 前面，或给本工具安装成其他名称。

## 18. 安全建议

- 恢复真实项目时，保留默认 auto-save，除非你明确知道后果。
- 删除、prune、restore、revert 等破坏性命令先用交互预览或 `--dry-run`。
- 远程服务端建议启用 HTTPS；公网服务建议同时设置强密码和 admin token。
- `_remote.json` 保存远程 token，默认 chmod 为 `0600`；不要提交到仓库。
- bundle 文件包含可恢复内容，按备份数据同等级别保护。
- 对重要数据，snapz 应作为快速本地保护层，不应替代异地备份。
