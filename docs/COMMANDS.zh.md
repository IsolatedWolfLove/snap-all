# snapz 命令参考

这是一份紧凑的命令参考。完整使用手册见 [USAGE.zh.md](./USAGE.zh.md)。
英文版见 [COMMANDS.md](./COMMANDS.md)。

全局选项：

| 选项 | 说明 |
|---|---|
| `--version` | 打印已安装版本。 |
| `--json` | 在支持的命令中输出机器可读 JSON；可放在子命令前或后。 |
| `--no-zstd` | 本次调用强制使用 gzip 压缩。 |
| `--minimal` | 本次调用跳过 TUI 提示，优先使用纯文本。 |

语言通过 `SNAPZ_LANG=en` 或 `SNAPZ_LANG=zh` 选择。

## 客户端命令

| 命令 | 作用 |
|---|---|
| `snapz` / `snapz <path>` | 交互式保存当前目录或指定目录。 |
| `snapz save <path>` | 脚本化创建快照。 |
| `snapz list [path]` | 列出一个源目录的快照。 |
| `snapz alist` | 跨所有已记录源目录列出快照。 |
| `snapz show [name]` | 打印快照元数据。 |
| `snapz mv [old] [new]` | 重命名快照。 |
| `snapz rm [name]` | 删除快照。 |
| `snapz protect [name]` | 保护快照，避免被删除或 prune。 |
| `snapz unprotect [name]` | 移除快照保护。 |
| `snapz tag ...` | 添加、删除或列出快照标签。 |
| `snapz log` | 查看操作历史。 |
| `snapz restore [name]` | 把完整快照恢复覆盖源目录。 |
| `snapz export [name] <dst>` | 把快照解出到其他目录。 |
| `snapz revert [name] [paths...]` | 从快照恢复指定文件或子树。 |
| `snapz undo` | 撤销最近一次 restore/revert 兜底快照。 |
| `snapz diff [a] [b]` | 对比两个快照，或快照与当前目录。 |
| `snapz find <pattern>` | 查找包含路径或 glob 的快照。 |
| `snapz cat [name] [relpath]` | 打印快照中的单个文件。 |
| `snapz browse [name]` | 浏览快照中的路径。 |
| `snapz stats [path]` | 查看存储占用和去重效果。 |
| `snapz prune` | 按保留策略删除快照。 |
| `snapz gc` | 回收无人引用的 blob。 |
| `snapz check` | 校验元数据和 blob 可达性。 |
| `snapz migrate` | 把旧版按源目录存放的 CAS blob 迁到 v3 全局池。 |
| `snapz init [path]` | 写入 `.snapz-id`，用于移动检测。 |
| `snapz relocate ...` | 目录改名后迁移源目录绑定。 |
| `snapz archive ...` | 列出归档源或恢复归档快照。 |
| `snapz bundle <source> <dst>` | 把一个源目录的历史打成可迁移 bundle。 |
| `snapz import <bundle>` | 导入可迁移 bundle。 |
| `snapz login <server>` | 保存远端登录凭据。 |
| `snapz logout` | 移除已保存远端凭据。 |
| `snapz push all` | 上传所有本地源到已配置服务器。 |
| `snapz pull all` | 下载所有远端源到本地归档。 |
| `snapz adopt <archive-key> <path>` | 把归档源绑定到现存目录。 |
| `snapz config ...` | 读取或写入持久化偏好。 |
| `snapz completion ...` | 生成或安装 bash/zsh 补全。 |
| `snapz web` | 启动本机客户端 Web UI。 |
| `snapz update` | 安装最新 `snapz-cli` GitHub Release `.deb`。 |
| `snapz uninstall` | 卸载客户端，并可选择删除本地数据。 |

## 常用客户端参数

| 命令 | 重要参数 |
|---|---|
| `save` | `-n/--name`、`-y/--yes`、`--overwrite`、`--include-large`、`--no-cache`、`--workers N`、`-m/--message NOTE` |
| `list`、`alist` | `--text`、`--all`、`list --timeline` |
| `show`、`mv`、`rm`、`protect`、`unprotect` | `--path PATH`、`--all`；破坏性命令还可用 `-y` |
| `restore` | `--path PATH`、`--all`、`-y`、`--no-auto-save`、`--clean` |
| `export` | `--path PATH`、`--all`、`--overwrite`、`-y` |
| `revert` | `--path PATH`、`--all`、`-y`、`--no-auto-save`、`--delete-extras`、`--text` |
| `undo` | `--path PATH`、`-y`、`--no-clean` |
| `diff` | `--path PATH`、`--all`、`--text`、`--tui` |
| `find` | `--path PATH`、`--all`、`--text` |
| `cat` | `--path PATH`、`--all`、`--raw`、`--binary-ok` |
| `browse` | `--path PATH`、`--all`、`--filter TEXT` |
| `stats` | `--all`、`--text` |
| `prune` | `--path PATH`、`--keep-last N`、`--keep-within-days DAYS`、`--keep-daily N`、`--keep-weekly N`、`--keep-tag TAG`、`--protect NAME`、`-y`、`--dry-run`、`--no-gc`、`--text` |
| `gc` | `--path PATH`、`--all`、`--dry-run`、`--rebuild-index` |
| `check` | `--path PATH`、`--all`、`--deep`、`--fix` |
| `migrate` | `--path PATH`、`--all`、`--to v3`、`--dry-run` |
| `relocate` | `OLD NEW`，或 `--auto ROOT...`，另有 `--dry-run`、`-y` |
| `archive restore` | `<archive> <name> <dst>`、`--overwrite` |
| `bundle` | `--archive`、`--overwrite` |
| `import` | `--path PATH`、`--overwrite` |
| `login` | `--tenant`、`--username`、`--password`、`--device`、`--tls-ca`、`--tls-client-cert`、`--tls-client-key` |
| `completion` | `bash`、`zsh`、`install`、`--shell`、`--rcfile` |
| `web` | `--host`、`--port`、`--allow-remote` |
| `uninstall` | `-y`、`--purge-data` |

## 服务端命令

服务端 Debian 包只安装 `/usr/bin/snapz-server`。执行
`sudo snapz-server init` 才会创建 `/etc/default/snapz-server`、
`/etc/systemd/system/snapz-server.service` 和数据目录。

| 命令 | 作用 |
|---|---|
| `snapz-server init` | 初始化配置、数据目录和 systemd 服务。 |
| `snapz-server setup` | 只初始化服务端数据目录。 |
| `snapz-server run` | 前台运行 HTTP API 服务。 |
| `snapz-server tenant add <name>` | 创建租户。 |
| `snapz-server user add <tenant> <username>` | 创建用户。 |
| `snapz-server user reset-password <tenant> <username>` | 设置新密码。 |
| `snapz-server device revoke <device-id>` | 吊销设备 token。 |
| `snapz-server doctor` | 显示健康状态和存储统计。 |
| `snapz-server update` | 安装最新 `snapz-server` `.deb`，不修改配置。 |

服务端全局选项：

| 选项 | 说明 |
|---|---|
| `--data DATA` | 数据目录，默认 `~/.snapz-server` 或 `SNAPZ_SERVER_DATA`。 |
| `--config FILE` | env 风格配置文件，默认 `/etc/default/snapz-server` 或 `SNAPZ_SERVER_CONFIG`。 |
| `--version` | 打印服务端版本。 |

服务端重要参数：

| 命令 | 参数 |
|---|---|
| `init` | `--config`、`--data`、`--host`、`--port`、`--admin-token`、`--max-bundle-mb`、`--cors-origin`、`--tls-cert`、`--tls-key`、`--tls-client-ca`、`--service-file`、`--server-bin`、`--force`、`--no-enable` |
| `run` | `--config`、`--host`、`--port`、`--cors-origin`、`--max-bundle-mb`、`--tls-cert`、`--tls-key`、`--tls-client-ca`、`--admin-token` |
| `user add`、`user reset-password` | `--password` 可避免交互输入。 |

## 环境变量

| 环境变量 | 说明 |
|---|---|
| `SNAPZ_ALL_ROOT` | 本地客户端存储根目录，默认 `~/.snapz-all`。 |
| `SNAPZ_LANG` | `en` 或 `zh`。 |
| `SNAPZ_SAVE_WORKERS` | 默认保存 worker 数。 |
| `SNAPZ_ZSTD_LEVEL` | zstd 压缩等级。 |
| `SNAPZ_GZIP_LEVEL` | gzip 压缩等级。 |
| `SNAPZ_REMOTE_ONLY` | remote-only 本地 blob 驱逐的运行时默认值。 |
| `SNAPZ_SERVER_DATA` | 服务端数据目录。 |
| `SNAPZ_SERVER_HOST` | 服务端监听地址。 |
| `SNAPZ_SERVER_PORT` | 服务端监听端口。 |
| `SNAPZ_SERVER_ADMIN_TOKEN` | 管理 API bearer token。 |
| `SNAPZ_SERVER_MAX_BUNDLE_MB` | 上传大小上限，单位 MiB。 |
| `SNAPZ_SERVER_CORS_ORIGIN` | 管理 API CORS 允许的浏览器 Origin，多个用逗号分隔。 |
| `SNAPZ_SERVER_TLS_CERT` | HTTPS 证书路径。 |
| `SNAPZ_SERVER_TLS_KEY` | HTTPS 私钥路径。 |
| `SNAPZ_SERVER_TLS_CLIENT_CA` | mTLS 客户端证书 CA 路径。 |
