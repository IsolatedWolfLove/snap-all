# snapz User Manual

This manual covers daily use of `snapz`: local snapshots, restore, diff,
cleanup, source moves, portable bundles, remote sync, automation, and
troubleshooting. Chinese: [USAGE.zh.md](./USAGE.zh.md). Compact command
reference: [COMMANDS.md](./COMMANDS.md).

## 1. What snapz Is

`snapz` is a directory snapshot tool. It stores named, restorable snapshots of
any directory under `~/.snapz-all/` by default. It is useful before risky
scripts, large refactors, dependency upgrades, migration work, or any operation
where you want a fast local rollback point.

New snapshots use content-addressed storage (CAS). Identical file contents are
stored once, so saving an unchanged tree usually adds only small metadata files.

## 2. Install

From release artifacts:

```bash
# Debian / Ubuntu client and server packages
sudo apt install ./dist/snapz-cli_*_all.deb
sudo apt install ./dist/snapz-server_*_all.deb

# Or install the zipapps
install -m 0755 dist/snapz.pyz ~/.local/bin/snapz
install -m 0755 dist/snapz-server.pyz ~/.local/bin/snapz-server

# Or install the wheel
pipx install "dist/snapz_cli-*.whl[zstd]"
```

The `snapz-server` Debian package installs only `/usr/bin/snapz-server`.
Run `sudo snapz-server init` to create `/etc/default/snapz-server`, initialize
the data directory, and install/enable the systemd service.

From source:

```bash
git clone <repo-url>
cd snapz
python3 -m venv .venv
.venv/bin/pip install -e .[dev]
.venv/bin/python -m snapz --help
```

Build release artifacts:

```bash
./scripts/build.sh all
./scripts/build.sh smoke
./scripts/build.sh --lang zh all
```

## 3. Core Concepts

| Concept | Meaning |
|---|---|
| Source directory | The directory you are protecting, such as `/home/me/project`. |
| Snapshot name | The name used later for restore, diff, delete, or export. |
| Store root | The local snapz database, defaulting to `~/.snapz-all/`. |
| CAS objects | Deduplicated file content blobs shared by recorded source directories. |
| Manifest | Per-snapshot file listing that maps paths to CAS objects. |
| Safety snapshot | `auto-pre-*` snapshot created before `restore` and `revert`. |
| Archive source | Snapshot history whose original source path is missing or not bound. |

Override the store root with:

```bash
SNAPZ_ALL_ROOT=/data/snapz-store snapz list .
```

## 4. Global Options And Environment

```bash
snapz --help
snapz --version
snapz --json list
snapz list --json
snapz --no-zstd save . -n gzip-test -y
SNAPZ_LANG=zh snapz --help
SNAPZ_LANG=zh snapz-server --help
```

Important environment variables:

| Variable | Meaning |
|---|---|
| `SNAPZ_ALL_ROOT` | Local snapshot store root. |
| `SNAPZ_LANG` | `en` or `zh`. |
| `SNAPZ_SAVE_WORKERS` | Default save worker count. |
| `SNAPZ_ZSTD_LEVEL` | zstd compression level, default `3`. |
| `SNAPZ_GZIP_LEVEL` | gzip compression level, default `6`. |
| `SNAPZ_REMOTE_ONLY` | Runtime default for remote-only local blob eviction. |
| `SNAPZ_SERVER_DATA` | Server data directory. |
| `SNAPZ_SERVER_HOST` | Server listen host. |
| `SNAPZ_SERVER_PORT` | Server listen port. |
| `SNAPZ_SERVER_ADMIN_TOKEN` | Server admin API token. |
| `SNAPZ_SERVER_MAX_BUNDLE_MB` | Server upload limit in MiB. |
| `SNAPZ_SERVER_CORS_ORIGIN` | Allowed admin UI browser origins. |
| `SNAPZ_SERVER_TLS_CERT` | HTTPS certificate. |
| `SNAPZ_SERVER_TLS_KEY` | HTTPS private key. |
| `SNAPZ_SERVER_TLS_CLIENT_CA` | Client certificate CA for mTLS. |

## 5. First Snapshot

Interactive snapshot of the current directory:

```bash
cd /path/to/project
snapz
```

Scripted snapshot:

```bash
snapz save /path/to/project -n baseline -y
snapz save . -n before-upgrade -m "before dependency upgrade" -y
snapz save . -n latest --overwrite -y
snapz save . -n full --include-large -y
snapz save . -n serial --workers 1 -y
```

List snapshots:

```bash
snapz list
snapz list --text
snapz list --all --text
snapz alist
snapz alist --text
```

## 6. Manage Snapshots

```bash
snapz show baseline --path /path/to/project
snapz mv baseline release-1 --path /path/to/project
snapz rm release-1 --path /path/to/project -y
snapz protect release-1 --path /path/to/project
snapz unprotect release-1 --path /path/to/project
```

Tags and logs:

```bash
snapz tag add release-1 stable prod --path /path/to/project
snapz tag rm release-1 prod --path /path/to/project
snapz tag list --path /path/to/project
snapz log --path /path/to/project -n 20
snapz log --all --json
```

Deleting a snapshot removes snapshot metadata and manifests. CAS blobs are
reclaimed later by `snapz gc`.

## 7. Restore, Export, Revert, Undo

Restore a full snapshot over the source directory:

```bash
snapz restore baseline --path /path/to/project
snapz restore baseline --path /path/to/project -y
snapz restore baseline --path /path/to/project --clean
```

By default, restore creates an `auto-pre-restore-*` safety snapshot first and
keeps files that are not present in the target snapshot. `--clean` deletes those
extra files. `--no-auto-save` disables the safety snapshot and should be used
only when another backup exists or the directory is disposable.

Export a snapshot without touching the source:

```bash
snapz export baseline /tmp/project-baseline --path /path/to/project
snapz export baseline /tmp/project-baseline --path /path/to/project --overwrite
```

Restore selected files or subtrees:

```bash
snapz revert baseline src/main.py --path /path/to/project
snapz revert baseline src docs --path /path/to/project
snapz revert baseline src --path /path/to/project --delete-extras
snapz revert baseline --path /path/to/project
```

Undo the most recent restore/revert:

```bash
snapz undo --path /path/to/project
snapz undo --path /path/to/project -y
snapz undo --path /path/to/project --no-clean
```

## 8. Diff, Find, Cat, Browse

```bash
snapz diff baseline --path /path/to/project
snapz diff baseline after-refactor --path /path/to/project --text
snapz diff --path /path/to/project

snapz find src/main.py --path /path/to/project
snapz find '**/*.py' --path /path/to/project --json
snapz find src/main.py --path /path/to/project --all

snapz cat baseline src/main.py --path /path/to/project
snapz cat baseline README.md --path /path/to/project --raw > README.old.md
snapz cat baseline image.png --path /path/to/project --binary-ok > image.png

snapz browse baseline --path /path/to/project
snapz browse baseline --path /path/to/project --filter main.py
```

When a snapshot name is omitted in a TTY, commands that need a snapshot usually
open an interactive picker.

## 9. Storage, Prune, GC, Check, Migrate

Storage statistics:

```bash
snapz stats
snapz stats . --text
snapz stats --all
snapz stats --all --json
```

Retention policies:

```bash
snapz prune --path /path/to/project --keep-last 5
snapz prune --path /path/to/project --keep-within-days 30 --keep-weekly 4
snapz prune --path /path/to/project --keep-last 5 --keep-tag stable
snapz prune --path /path/to/project --keep-last 5 --dry-run --text
snapz prune --path /path/to/project --keep-last 5 -y
```

Automatic prune after save:

```bash
snapz config set retention.keep_last 10
snapz config set retention.keep_weekly 4
snapz config set retention.auto_prune_after_save true
```

Reclaim unreferenced content blobs:

```bash
snapz gc --path /path/to/project
snapz gc --all
snapz gc --all --dry-run
snapz gc --all --rebuild-index
```

Validate and repair:

```bash
snapz check /path/to/project
snapz check --all
snapz check --all --deep
snapz check --all --fix
```

Migrate older per-source CAS stores to the v3 global pool:

```bash
snapz migrate /path/to/project --to v3
snapz migrate --all --to v3
snapz migrate --all --to v3 --dry-run
```

## 10. Configuration And Local Excludes

```bash
snapz config list
snapz config get color
snapz config set color never
snapz config set save_picker true
snapz config set ui_mode minimal
snapz config set update_check.enabled false
snapz config unset color
```

Known config keys:

| Key | Default | Meaning |
|---|---:|---|
| `ui_mode` | `tui` | `tui` or `minimal`. |
| `save_picker` | `false` | Open a save picker to add large paths to local excludes. |
| `color` | `auto` | `auto`, `always`, or `never`. |
| `update_check.enabled` | `true` | Run a daily non-blocking GitHub update check. |
| `retention.keep_last` | `0` | Default `prune --keep-last`. |
| `retention.keep_daily` | `0` | Default `prune --keep-daily`. |
| `retention.keep_weekly` | `0` | Default `prune --keep-weekly`. |
| `retention.keep_within_days` | `0` | Default `prune --keep-within-days`. |
| `retention.auto_prune_after_save` | `false` | Apply configured retention rules after each save. |
| `remote_only` | `false` | Keep local metadata but evict local blobs after confirmed remote upload. |

Snapshot planning considers built-in defaults, `.gitignore`, `.snapzignore`,
and per-source local excludes stored under:

```text
~/.snapz-all/<source-key>/_local_excludes
```

Local excludes are useful for machine-specific files that should not be
committed to a project.

## 11. Source Lifecycle

Write a source marker:

```bash
snapz init /path/to/project
snapz init /path/to/project --force
```

Relocate after a directory move:

```bash
snapz relocate /old/project /new/project
snapz relocate --auto /home/me --dry-run
snapz relocate --auto /home/me -y
```

Work with archived sources:

```bash
snapz archive list
snapz archive restore <archive-key> baseline /tmp/out
snapz archive restore <archive-key> baseline /tmp/out --overwrite
snapz adopt <archive-key> /path/to/project
```

## 12. Portable Bundles

```bash
snapz bundle /path/to/project /tmp/project.snapz
snapz bundle /path/to/project /tmp/project.snapz --overwrite
snapz bundle <archive-key> /tmp/project.snapz --archive

snapz import /tmp/project.snapz
snapz import /tmp/project.snapz --path /path/to/project
snapz import /tmp/project.snapz --path /path/to/project --overwrite
```

A bundle contains one source's metadata, manifests, and required blobs. Treat it
like backup data.

## 13. Remote Sync

Remote sync uses the standalone `snapz-server`. The client stores a token with
`login`, then syncs all sources with `push all` and `pull all`.

Recommended service initialization:

```bash
sudo snapz-server init --data /srv/snapz --host 0.0.0.0 --port 8765
sudo editor /etc/default/snapz-server
```

`init` creates `/etc/default/snapz-server`, initializes the data directory,
writes `/etc/systemd/system/snapz-server.service`, and enables/starts the
service. Existing config and service files are kept unless `--force` is passed.
Later `sudo snapz-server update` upgrades only the program package.

Manual setup:

```bash
snapz-server --data /srv/snapz setup
snapz-server --data /srv/snapz tenant add acme
snapz-server --data /srv/snapz user add acme alice
snapz-server --data /srv/snapz user add acme alice --password 'change-me'
```

Run for local testing:

```bash
snapz-server --data /srv/snapz run --host 127.0.0.1 --port 8765
```

Run with HTTPS, admin API, CORS, or mTLS:

```bash
snapz-server --data /srv/snapz run \
  --host 0.0.0.0 \
  --port 8765 \
  --tls-cert /etc/snapz/tls/fullchain.pem \
  --tls-key /etc/snapz/tls/privkey.pem \
  --admin-token "$SNAPZ_SERVER_ADMIN_TOKEN" \
  --cors-origin https://admin.example

snapz-server --data /srv/snapz run \
  --host 0.0.0.0 \
  --port 8765 \
  --tls-cert /etc/snapz/tls/fullchain.pem \
  --tls-key /etc/snapz/tls/privkey.pem \
  --tls-client-ca /etc/snapz/tls/client-ca.pem
```

Client login and sync:

```bash
snapz login http://127.0.0.1:8765 --tenant acme --username alice
snapz login https://server.example \
  --tenant acme \
  --username alice \
  --tls-ca /etc/snapz/tls/server-ca.pem \
  --tls-client-cert ~/.config/snapz/client.pem \
  --tls-client-key ~/.config/snapz/client-key.pem

snapz push all
snapz pull all
snapz archive list
snapz adopt remote-src_xxx /path/to/project
snapz logout
```

Server maintenance:

```bash
snapz-server --data /srv/snapz doctor
snapz-server --data /srv/snapz user reset-password acme alice
snapz-server --data /srv/snapz device revoke <device-id>
```

The built-in admin page is available at `/admin` when an admin token is set.
The Vben Admin drop-in module lives under `web/vue-vben-admin-snapz/`.

## 14. Remote-Only Mode

`remote_only` keeps local index files but evicts local content blobs once they
have been uploaded and confirmed by the server. It is intended for machines
that want searchable snapshot history with lower local disk usage.

```bash
snapz login https://server.example --tenant acme --username alice
snapz config set remote_only true
snapz save . -n work -y
```

When enabled from an interactive terminal, snapz offers to install a cron entry
that runs `snapz push all; snapz pull all` every three hours for the current
store root. Manual sync is still available:

```bash
snapz push all
snapz pull all
```

If a later restore needs missing content, run `snapz pull all` first or sync
from the server.

## 15. JSON And Automation

Use `--json` for scripts:

```bash
snapz list --json | jq
snapz alist --json | jq
snapz show baseline --json
snapz stats --all --json | jq
snapz log --all --json | jq
```

For destructive commands, pass `-y` explicitly:

```bash
snapz rm old --path /path/to/project --json -y
snapz restore baseline --path /path/to/project --json -y
snapz prune --path /path/to/project --keep-last 5 --json -y
```

In CI, set an isolated `SNAPZ_ALL_ROOT` and avoid parsing colored text.

## 16. TUI Keys

Common list keys:

| Key | Action |
|---|---|
| `j` / `k`, arrows | Move selection. |
| `PgUp` / `PgDn` | Page. |
| `Home` / `End` | Jump to beginning/end. |
| `Enter` | Open details or select. |
| `r` | Restore. |
| `d` | Delete. |
| `n` | Rename. |
| `/` | Filter. |
| `Esc` | Clear filter or exit. |
| `q` | Quit. |

Diff, revert, browse, and picker screens show their available keys in the
footer. The most common keys are arrows, `Enter`, `Space`, `a`, `n`, `/`, `q`,
and `Esc`.

## 17. Troubleshooting

Snapshot not found:

```bash
snapz list /path/to/project
snapz alist --text
snapz archive list
snapz relocate /old/path /new/path
```

Deleted snapshots did not free much space:

```bash
snapz gc --all --dry-run
snapz gc --all
```

Possible store damage:

```bash
snapz check --all
snapz check --all --deep
snapz check --all --fix
```

Snapshots are slow:

```bash
snapz save . -n test -y --workers 8
snapz save . -n no-cache-test -y --no-cache
snapz config set save_picker true
```

Also check `.gitignore`, `.snapzignore`, and local excludes for large generated
directories such as `node_modules/`, `.venv/`, `build/`, and `dist/`.

Wrong language:

```bash
SNAPZ_LANG=en snapz --help
SNAPZ_LANG=zh snapz --help
SNAPZ_LANG=zh snapz-server --help
```

Wrong binary:

```bash
command -v snapz
snapz --version
```

Make sure your install directory, such as `~/.local/bin`, appears before other
directories on `PATH`.

## 18. Security Notes

- Keep the default safety snapshots for real restore/revert operations.
- Preview destructive commands with TUI or `--dry-run` before scripting them.
- Use HTTPS for remote servers; use strong user passwords and an admin token.
- Use mTLS when exposing a server to networks you do not fully control.
- `_remote.json` stores the client remote token and should never be committed.
- Bundle files contain restorable content and should be protected like backups.
- snapz is a fast local protection layer, not a substitute for offsite backups
  for critical data.
