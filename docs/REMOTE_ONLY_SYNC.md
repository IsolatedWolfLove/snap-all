# Snapz Remote-Only Sync Workflow

This guide explains `remote_only`: local saves keep indexes and manifests, but
content blobs are evicted after they are confirmed uploaded to `snapz-server`.
Chinese: [REMOTE_ONLY_SYNC.zh.md](./REMOTE_ONLY_SYNC.zh.md).

## When To Use It

Use `remote_only` when you want local snapshot lists, file trees, and metadata,
but want most long-term content storage on a remote server.

Avoid it when you often restore large trees while offline, cannot rely on the
server being reachable, or have not configured `snapz login` yet.

## End-To-End Setup

Example values:

| Item | Value |
|---|---|
| Server | `backup.example.com` |
| Server data | `/srv/snapz` |
| Tenant | `acme` |
| User | `alice` |
| Port | `8765` |

## 1. Install The Server Command

On Debian/Ubuntu:

```bash
sudo apt install ./dist/snapz-server_*_all.deb
```

The server Debian package installs `/usr/bin/snapz-server` only. It does not
write `/etc/default/snapz-server` or a systemd unit until you run
`snapz-server init`.

Zipapp install:

```bash
install -m 0755 dist/snapz-server.pyz ~/.local/bin/snapz-server
snapz-server --version
```

## 2. Initialize Server Config And Service

Recommended systemd setup:

```bash
sudo snapz-server init \
  --data /srv/snapz \
  --host 0.0.0.0 \
  --port 8765
sudo editor /etc/default/snapz-server
```

`init` creates the env-style config file, initializes the database, writes
`/etc/systemd/system/snapz-server.service`, then runs `systemctl daemon-reload`
and `systemctl enable --now snapz-server`. Existing config and service files are
kept unless you pass `--force`.

For local testing without systemd:

```bash
snapz-server --data /srv/snapz setup
snapz-server --data /srv/snapz run --host 127.0.0.1 --port 8765
```

## 3. Create Tenant And User

```bash
snapz-server --data /srv/snapz tenant add acme
snapz-server --data /srv/snapz user add acme alice
```

Non-interactive password setup:

```bash
snapz-server --data /srv/snapz user add acme alice --password 'change-me'
```

## 4. Enable HTTPS And Admin Access

For public or shared networks, run behind HTTPS:

```bash
snapz-server --data /srv/snapz run \
  --host 0.0.0.0 \
  --port 8765 \
  --tls-cert /etc/snapz/tls/fullchain.pem \
  --tls-key /etc/snapz/tls/privkey.pem \
  --admin-token "$SNAPZ_SERVER_ADMIN_TOKEN"
```

For a browser admin app on another origin:

```bash
snapz-server --data /srv/snapz run \
  --host 0.0.0.0 \
  --port 8765 \
  --admin-token "$SNAPZ_SERVER_ADMIN_TOKEN" \
  --cors-origin https://admin.example
```

Optional mTLS:

```bash
snapz-server --data /srv/snapz run \
  --host 0.0.0.0 \
  --port 8765 \
  --tls-cert /etc/snapz/tls/fullchain.pem \
  --tls-key /etc/snapz/tls/privkey.pem \
  --tls-client-ca /etc/snapz/tls/client-ca.pem
```

The built-in admin page is available at `/admin` when an admin token is set.

## 5. Install And Login From The Client

```bash
sudo apt install ./dist/snapz-cli_*_all.deb
# or
install -m 0755 dist/snapz.pyz ~/.local/bin/snapz
```

Login:

```bash
snapz login https://backup.example.com:8765 \
  --tenant acme \
  --username alice \
  --tls-ca /etc/snapz/tls/server-ca.pem
```

With mTLS:

```bash
snapz login https://backup.example.com:8765 \
  --tenant acme \
  --username alice \
  --tls-ca /etc/snapz/tls/server-ca.pem \
  --tls-client-cert ~/.config/snapz/client.pem \
  --tls-client-key ~/.config/snapz/client-key.pem
```

## 6. First Push And Pull

```bash
snapz save /path/to/project -n baseline -y
snapz push all
snapz pull all
```

Pulled remote sources are stored as archived sources until bound locally:

```bash
snapz archive list
snapz adopt remote-src_xxx /path/to/project
```

## 7. Enable Remote-Only

```bash
snapz config set remote_only true
```

When this is run from an interactive terminal, snapz offers to install a cron
job that periodically runs:

```bash
snapz push all
snapz pull all
```

The cron entry is scoped to the current `SNAPZ_ALL_ROOT` and runs every three
hours. You can always sync manually with the same two commands.

## 8. What Happens On Save

With `remote_only=true`, a save still writes local metadata and enough indexes
to list, search, diff manifests, and browse trees. After the background push
confirms uploaded blobs on the server, local content blobs that can be fetched
again are evicted.

If upload fails, snapz preserves local blobs that are not safely remote yet.

## 9. Restore Or Cat Missing Content

If a restore, `cat`, or content diff needs blobs that were evicted, sync first:

```bash
snapz pull all
snapz restore baseline --path /path/to/project
```

If a command reports missing blobs, verify login, server reachability, and that
the source has been pushed at least once:

```bash
snapz login https://backup.example.com:8765 --tenant acme --username alice
snapz push all
snapz pull all
snapz check --all
```

## 10. Disable Remote-Only

```bash
snapz config set remote_only false
snapz pull all
```

Disabling the setting stops future eviction. Run `snapz pull all` to hydrate
remote content back into the local store as needed.

## 11. Maintenance

```bash
snapz-server --data /srv/snapz doctor
snapz-server --data /srv/snapz user reset-password acme alice
snapz-server --data /srv/snapz device revoke <device-id>
sudo snapz-server update
```

`snapz-server update` installs the latest server `.deb` and preserves the
config file and systemd unit created by `snapz-server init`.
