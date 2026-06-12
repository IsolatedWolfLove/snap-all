# Snapz remote_only 远端索引同步使用流程

这份文档说明如何使用 `remote_only` 模式：本地先保存快照，确认上传到 `snapz-server` 后，只保留索引，内容 blob 可以从远端按需取回。

## 适用场景

适合这些情况：

- 希望本地保留快照列表、文件树、manifest 索引。
- 希望大部分内容存到远端，减少本地长期占用。
- 希望 `restore`、`cat`、diff 查看文件内容时再按需从远端下载对应内容。

不适合这些情况：

- 经常离线恢复大量文件。
- 不想依赖远端服务可用性。
- 还没有配置 `snapz-server` 登录信息。

## 完整连接流程

远端同步分两端：

- Server 机器：运行 `snapz-server`，保存远端 bundle、索引和用户/device token。
- Client 机器：运行 `snapz`，登录 server，然后 `push` / `pull` / `remote_only`。

下面用一个示例环境说明：

- server 地址：`backup.example.com`
- server 数据目录：`/srv/snapz`
- tenant：`acme`
- 用户：`alice`
- 服务端口：`8765`

### 1. Server 机器：安装命令

server 机器上需要有 `snapz-server` 命令。开发环境可用：

```bash
python3 -m venv .venv
.venv/bin/pip install -e .[dev]
ln -sf "$PWD/.venv/bin/snapz-server" ~/.local/bin/snapz-server
```

如果使用构建产物：

```bash
install -m 0755 dist/snapz-server.pyz ~/.local/bin/snapz-server
```

确认命令可用：

```bash
snapz-server --version
```

### 2. Server 机器：初始化数据目录

创建 server 数据目录并初始化数据库：

```bash
sudo mkdir -p /srv/snapz
sudo chown "$USER":"$USER" /srv/snapz
snapz-server --data /srv/snapz setup
```

如果后面准备用 systemd 的 `User=snapz` 运行服务，可以先创建系统用户，并把目录交给它：

```bash
sudo useradd --system --home /srv/snapz --shell /usr/sbin/nologin snapz
sudo chown -R snapz:snapz /srv/snapz
sudo -u snapz snapz-server --data /srv/snapz setup
```

创建 tenant：

```bash
snapz-server --data /srv/snapz tenant add acme
```

创建用户。交互式设置密码：

```bash
snapz-server --data /srv/snapz user add acme alice
```

或者非交互式设置密码：

```bash
snapz-server --data /srv/snapz user add acme alice --password 'change-me'
```

### 3. Server 机器：启动服务

本机测试只监听 localhost：

```bash
snapz-server --data /srv/snapz run --host 127.0.0.1 --port 8765
```

局域网或公网访问需要监听所有网卡：

```bash
snapz-server --data /srv/snapz run --host 0.0.0.0 --port 8765
```

如果机器有防火墙，需要放行端口，例如 Ubuntu/UFW：

```bash
sudo ufw allow 8765/tcp
```

如果要启用管理界面 `/admin`，启动时加 admin token：

```bash
export SNAPZ_SERVER_ADMIN_TOKEN="$(openssl rand -hex 32)"
snapz-server --data /srv/snapz run \
  --host 0.0.0.0 \
  --port 8765 \
  --admin-token "$SNAPZ_SERVER_ADMIN_TOKEN"
```

浏览器访问：

```text
http://backup.example.com:8765/admin
```

管理界面 token 就是 `SNAPZ_SERVER_ADMIN_TOKEN` 的值。

### 4. Server 机器：建议启用 HTTPS

公网使用时建议启用 HTTPS。假设证书在：

- `/etc/snapz/tls/fullchain.pem`
- `/etc/snapz/tls/privkey.pem`

启动：

```bash
snapz-server --data /srv/snapz run \
  --host 0.0.0.0 \
  --port 8765 \
  --tls-cert /etc/snapz/tls/fullchain.pem \
  --tls-key /etc/snapz/tls/privkey.pem \
  --admin-token "$SNAPZ_SERVER_ADMIN_TOKEN"
```

如果是自签证书，客户端登录时需要传 `--tls-ca`。

可选 mTLS：要求客户端也提供证书：

```bash
snapz-server --data /srv/snapz run \
  --host 0.0.0.0 \
  --port 8765 \
  --tls-cert /etc/snapz/tls/fullchain.pem \
  --tls-key /etc/snapz/tls/privkey.pem \
  --tls-client-ca /etc/snapz/tls/client-ca.pem
```

### 5. Server 机器：用 systemd 常驻运行

推荐直接用 `snapz-server init` 从头初始化服务端配置和自启动文件：

```bash
sudo snapz-server init \
  --data /srv/snapz \
  --host 0.0.0.0 \
  --port 8765
sudo editor /etc/default/snapz-server
```

`init` 会创建 `/etc/default/snapz-server`、初始化数据目录、写入
`/etc/systemd/system/snapz-server.service`，并执行 `systemctl daemon-reload`
和 `systemctl enable --now snapz-server`。如果配置文件或 service 已经存在，
默认会保留原文件；需要从头重写时加 `--force`。

之后升级程序用：

```bash
sudo snapz-server update
```

`update` 只升级程序，不覆盖 `/etc/default/snapz-server`。

如果不用 `snapz-server init`，也可以手动创建同样风格的配置和 systemd service：

```bash
sudo tee /etc/default/snapz-server >/dev/null <<'EOF'
SNAPZ_SERVER_DATA=/srv/snapz
SNAPZ_SERVER_HOST=0.0.0.0
SNAPZ_SERVER_PORT=8765
SNAPZ_SERVER_ADMIN_TOKEN=change-this-admin-token
SNAPZ_SERVER_TLS_CERT=
SNAPZ_SERVER_TLS_KEY=
SNAPZ_SERVER_TLS_CLIENT_CA=
EOF
```

```bash
sudo tee /etc/systemd/system/snapz-server.service >/dev/null <<'EOF'
[Unit]
Description=snapz remote sync server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=snapz
Group=snapz
EnvironmentFile=-/etc/default/snapz-server
ExecStart=/usr/local/bin/snapz-server --config /etc/default/snapz-server run
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

如果你用 HTTPS，在 `/etc/default/snapz-server` 里填写：

```text
SNAPZ_SERVER_TLS_CERT=/etc/snapz/tls/fullchain.pem
SNAPZ_SERVER_TLS_KEY=/etc/snapz/tls/privkey.pem
```

启动并设置开机自启：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now snapz-server
sudo systemctl status snapz-server
```

查看日志：

```bash
journalctl -u snapz-server -f
```

注意：上面的示例使用 `User=snapz`。如果你前面没有创建这个用户，需要先创建，并确保它能读写 `/srv/snapz`：

```bash
sudo useradd --system --home /srv/snapz --shell /usr/sbin/nologin snapz
sudo chown -R snapz:snapz /srv/snapz
```

### 6. Client 机器：安装命令

client 机器需要 `snapz` 命令。开发环境可用：

```bash
python3 -m venv .venv
.venv/bin/pip install -e .[dev]
ln -sf "$PWD/.venv/bin/snapz" ~/.local/bin/snapz
```

如果使用构建产物：

```bash
install -m 0755 dist/snapz.pyz ~/.local/bin/snapz
```

确认命令可用：

```bash
snapz --version
```

### 7. Client 机器：测试能连到 server

HTTP：

```bash
curl http://backup.example.com:8765/api/me
```

这一步没有 token 时返回 unauthorized 是正常的，说明网络和 server HTTP 入口通了。

HTTPS：

```bash
curl https://backup.example.com:8765/
```

如果是自签证书：

```bash
curl --cacert /path/to/server-ca.pem https://backup.example.com:8765/
```

### 8. Client 机器：登录远端

HTTP 登录：

```bash
snapz login http://backup.example.com:8765 --tenant acme --username alice
```

带密码参数：

```bash
snapz login http://backup.example.com:8765 \
  --tenant acme \
  --username alice \
  --password 'change-me'
```

HTTPS 登录：

```bash
snapz login https://backup.example.com:8765 \
  --tenant acme \
  --username alice
```

HTTPS 自签证书：

```bash
snapz login https://backup.example.com:8765 \
  --tenant acme \
  --username alice \
  --tls-ca /path/to/server-ca.pem
```

mTLS 登录：

```bash
snapz login https://backup.example.com:8765 \
  --tenant acme \
  --username alice \
  --tls-ca /path/to/server-ca.pem \
  --tls-client-cert ~/.config/snapz/client.pem \
  --tls-client-key ~/.config/snapz/client-key.pem
```

登录成功后，token 会保存到本机 snapz 存储根目录下的 `_remote.json`。

### 9. Client 机器：首次推送和拉取

先在项目里保存一个 snapshot：

```bash
snapz save /path/to/project first
```

上传到 server：

```bash
snapz push all
```

拉取远端索引：

```bash
snapz pull all
```

查看本地记录：

```bash
snapz alist
snapz archive list
```

到这里，client 和 server 已经连接成功。后面再开启 `remote_only`。

## 启用 remote_only

执行：

```bash
snapz config set remote_only true
```

如果当前是交互式终端，snapz 会继续询问是否安装 cron：

```text
install a cron job to run snapz push/pull every 3 hours? [y/N]
```

输入 `y` 后，snapz 会写入当前用户的 crontab，每 3 小时执行一次：

```bash
snapz push all
snapz pull all
```

如果输入 `n` 或直接回车，则只开启 `remote_only`，不会安装定时任务。

查看配置：

```bash
snapz config get remote_only
```

关闭配置：

```bash
snapz config set remote_only false
```

注意：关闭 `remote_only` 不会自动删除已经安装的 cron。需要时请用 `crontab -e` 手动删除 `# snapz remote sync ...` 那组条目。

## 保存快照时会发生什么

正常保存：

```bash
snapz save /path/to/project release-1
```

在 `remote_only=true` 时，流程是：

1. 先在本地完整保存 snapshot。
2. 尝试把新增内容上传到远端。
3. 远端确认收到并校验成功后，本地删除可从远端取回的内容 blob。
4. 本地继续保留索引文件：source meta、snapshot meta、manifest。

如果保存时没有网络、没有登录、server 不可用，保存不会失败。snapz 会保留本地内容 blob，之后执行 `snapz push all` 或 cron 定时任务时再补传。

## 手动同步

手动上传本地未同步内容：

```bash
snapz push all
```

手动拉取远端索引：

```bash
snapz pull all
```

推荐在刚启用后先手动跑一次：

```bash
snapz push all
snapz pull all
```

`push` 现在是增量上传：远端已有的 blob 不会重复传。同名 snapshot 被 overwrite 后，也会检测 manifest/meta 变化并更新远端。

增量 `push` 只向 server 新增或替换 snapshot，不会把“本地已经没有的
snapshot”解释成远端删除。也就是说，本地 `snapz rm` 后再 `snapz push all`
不会静默删除云端副本；云端删除以后必须是单独、显式、带确认的操作。

`pull` 默认优先只拉索引，不下载完整内容包。本地能看到远端 snapshot，但内容会在需要时再下载。

## 定时任务行为

安装 cron 后，每 3 小时会运行一次：

```cron
# snapz remote sync <store-hash>
0 */3 * * * SNAPZ_ALL_ROOT=... python -m snapz push all >/dev/null 2>&1; SNAPZ_ALL_ROOT=... python -m snapz pull all >/dev/null 2>&1
```

特点：

- 只写入当前用户 crontab。
- 带有 `# snapz remote sync <store-hash>` marker。
- 再次安装会替换 snapz 自己的旧条目。
- 不会删除或改写用户其他 cron 任务。

查看当前 crontab：

```bash
crontab -l
```

手动编辑：

```bash
crontab -e
```

## 查看快照列表

本地索引存在时，普通列表不需要下载内容：

```bash
snapz list --path /path/to/project
```

查看远端拉下来的归档源：

```bash
snapz archive list
```

或者：

```bash
snapz alist
```

`snapz alist` 里显示的远端归档索引是只读入口：可以恢复，也可以用
`snapz adopt` 绑定到本地目录，但普通本地删除不会删除 server 上的副本。

## 按需下载内容

当本地只有索引、没有内容 blob 时，下面这些操作会按需从远端下载需要的对象：

恢复归档 snapshot：

```bash
snapz archive restore remote-src_xxx release-1 /tmp/restored
```

恢复普通 source：

```bash
snapz restore release-1 --path /path/to/project
```

查看某个文件：

```bash
snapz cat release-1 README.md --path /path/to/project
```

diff 的文件内容钻取也会按需下载相关 blob。结构级 diff 只依赖 manifest，通常不需要下载内容。

## 常见问题

### 保存后上传失败怎么办？

不用特别处理。因为本地内容还没有被删除，下一次执行：

```bash
snapz push all
```

会继续尝试上传。

### cron 没装上怎么办？

可能是系统没有 `crontab` 命令，或者当前用户没有权限。

可以手动同步：

```bash
snapz push all
snapz pull all
```

也可以手动加入 crontab：

```cron
0 */3 * * * SNAPZ_ALL_ROOT=/path/to/.snapz-all python3 -m snapz push all >/dev/null 2>&1; SNAPZ_ALL_ROOT=/path/to/.snapz-all python3 -m snapz pull all >/dev/null 2>&1
```

### 恢复时报 blob missing 怎么办？

先确认已经登录远端：

```bash
snapz login http://server:8765 --tenant default --username alice
```

然后拉一次索引：

```bash
snapz pull all
```

如果远端没有这个对象，说明当时没有成功上传。回到拥有本地完整内容的机器上运行：

```bash
snapz push all
```

### 不想继续 remote_only 怎么办？

关闭配置：

```bash
snapz config set remote_only false
```

删除 cron：

```bash
crontab -e
```

删掉类似下面两行：

```cron
# snapz remote sync <store-hash>
0 */3 * * * ...
```

关闭后，新的 snapshot 会按普通本地模式保留内容。已经被驱逐的旧 blob 仍然会在需要时从远端按需下载。
