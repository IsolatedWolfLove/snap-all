# snapz Command Reference

This page is a compact reference for the command line surface. For a guided
manual, see [USAGE.md](./USAGE.md). Chinese: [COMMANDS.zh.md](./COMMANDS.zh.md).

Global options:

| Option | Meaning |
|---|---|
| `--version` | Print the installed version. |
| `--json` | Emit machine-readable JSON where supported. It may appear before or after the subcommand. |
| `--no-zstd` | Force gzip compression for this invocation. |
| `--minimal` | Skip TUI prompts and prefer plain text for this invocation. |

Language is selected with `SNAPZ_LANG=en` or `SNAPZ_LANG=zh`.

## Client Commands

| Command | Purpose |
|---|---|
| `snapz` / `snapz <path>` | Interactive snapshot of the current or given directory. |
| `snapz save <path>` | Scriptable snapshot creation. |
| `snapz list [path]` | List snapshots for one source directory. |
| `snapz alist` | List snapshots across all recorded source directories. |
| `snapz show [name]` | Print snapshot metadata. |
| `snapz mv [old] [new]` | Rename a snapshot. |
| `snapz rm [name]` | Delete a snapshot. |
| `snapz protect [name]` | Protect a snapshot from delete/prune. |
| `snapz unprotect [name]` | Remove snapshot protection. |
| `snapz tag ...` | Add, remove, or list snapshot tags. |
| `snapz log` | Show the operation history. |
| `snapz restore [name]` | Restore a full snapshot over its source directory. |
| `snapz export [name] <dst>` | Extract a snapshot to another directory. |
| `snapz revert [name] [paths...]` | Restore selected files or subtrees from a snapshot. |
| `snapz undo` | Undo the most recent restore/revert safety snapshot. |
| `snapz diff [a] [b]` | Compare snapshots, or a snapshot against the live tree. |
| `snapz find <pattern>` | Find snapshots containing a path or glob. |
| `snapz cat [name] [relpath]` | Print one file from a snapshot. |
| `snapz browse [name]` | Browse paths inside a snapshot. |
| `snapz stats [path]` | Show storage usage and deduplication. |
| `snapz prune` | Delete snapshots according to a retention policy. |
| `snapz gc` | Reclaim unreferenced blobs. |
| `snapz check` | Validate metadata and blob reachability. |
| `snapz migrate` | Migrate old per-source CAS blobs into the v3 global blob pool. |
| `snapz init [path]` | Write a `.snapz-id` marker for move detection. |
| `snapz relocate ...` | Move a source binding after a directory rename. |
| `snapz archive ...` | List archived sources or restore archived snapshots. |
| `snapz bundle <source> <dst>` | Pack one source's history into a portable bundle. |
| `snapz import <bundle>` | Import a portable bundle. |
| `snapz login <server>` | Save remote credentials. |
| `snapz logout` | Remove saved remote credentials. |
| `snapz push all` | Upload all local sources to the configured server. |
| `snapz pull all` | Download all remote sources into local archives. |
| `snapz adopt <archive-key> <path>` | Bind an archived source to a live directory. |
| `snapz config ...` | Read or write persistent preferences. |
| `snapz completion ...` | Generate or install bash/zsh completion. |
| `snapz web` | Start the local client web UI. |
| `snapz update` | Install the latest `snapz-cli` GitHub Release `.deb`. |
| `snapz uninstall` | Uninstall the client and optionally delete local data. |

## Common Client Flags

| Command | Important flags |
|---|---|
| `save` | `-n/--name`, `-y/--yes`, `--overwrite`, `--include-large`, `--no-cache`, `--workers N`, `-m/--message NOTE` |
| `list`, `alist` | `--text`, `--all`, `list --timeline` |
| `show`, `mv`, `rm`, `protect`, `unprotect` | `--path PATH`, `--all`; destructive commands also use `-y` |
| `restore` | `--path PATH`, `--all`, `-y`, `--no-auto-save`, `--clean` |
| `export` | `--path PATH`, `--all`, `--overwrite`, `-y` |
| `revert` | `--path PATH`, `--all`, `-y`, `--no-auto-save`, `--delete-extras`, `--text` |
| `undo` | `--path PATH`, `-y`, `--no-clean` |
| `diff` | `--path PATH`, `--all`, `--text`, `--tui` |
| `find` | `--path PATH`, `--all`, `--text` |
| `cat` | `--path PATH`, `--all`, `--raw`, `--binary-ok` |
| `browse` | `--path PATH`, `--all`, `--filter TEXT` |
| `stats` | `--all`, `--text` |
| `prune` | `--path PATH`, `--keep-last N`, `--keep-within-days DAYS`, `--keep-daily N`, `--keep-weekly N`, `--keep-tag TAG`, `--protect NAME`, `-y`, `--dry-run`, `--no-gc`, `--text` |
| `gc` | `--path PATH`, `--all`, `--dry-run`, `--rebuild-index` |
| `check` | `--path PATH`, `--all`, `--deep`, `--fix` |
| `migrate` | `--path PATH`, `--all`, `--to v3`, `--dry-run` |
| `relocate` | `OLD NEW`, or `--auto ROOT...`, plus `--dry-run`, `-y` |
| `archive restore` | `<archive> <name> <dst>`, `--overwrite` |
| `bundle` | `--archive`, `--overwrite` |
| `import` | `--path PATH`, `--overwrite` |
| `login` | `--tenant`, `--username`, `--password`, `--device`, `--tls-ca`, `--tls-client-cert`, `--tls-client-key` |
| `completion` | `bash`, `zsh`, `install`, `--shell`, `--rcfile` |
| `web` | `--host`, `--port`, `--allow-remote` |
| `uninstall` | `-y`, `--purge-data` |

## Server Commands

The Debian server package installs only `/usr/bin/snapz-server`. Run
`sudo snapz-server init` to create `/etc/default/snapz-server`,
`/etc/systemd/system/snapz-server.service`, and the data directory.

| Command | Purpose |
|---|---|
| `snapz-server init` | Initialize config, data, and systemd service. |
| `snapz-server setup` | Initialize only the server data directory. |
| `snapz-server run` | Run the HTTP API server in the foreground. |
| `snapz-server tenant add <name>` | Create a tenant. |
| `snapz-server user add <tenant> <username>` | Create a user. |
| `snapz-server user reset-password <tenant> <username>` | Set a new password. |
| `snapz-server device revoke <device-id>` | Revoke a device token. |
| `snapz-server doctor` | Show health and storage statistics. |
| `snapz-server update` | Install the latest `snapz-server` `.deb` without touching config. |

Server global options:

| Option | Meaning |
|---|---|
| `--data DATA` | Data directory, defaulting to `~/.snapz-server` or `SNAPZ_SERVER_DATA`. |
| `--config FILE` | Env-style config file, defaulting to `/etc/default/snapz-server` or `SNAPZ_SERVER_CONFIG`. |
| `--version` | Print server version. |

Important server flags:

| Command | Flags |
|---|---|
| `init` | `--config`, `--data`, `--host`, `--port`, `--admin-token`, `--max-bundle-mb`, `--cors-origin`, `--tls-cert`, `--tls-key`, `--tls-client-ca`, `--service-file`, `--server-bin`, `--force`, `--no-enable` |
| `run` | `--config`, `--host`, `--port`, `--cors-origin`, `--max-bundle-mb`, `--tls-cert`, `--tls-key`, `--tls-client-ca`, `--admin-token` |
| `user add`, `user reset-password` | `--password` to avoid an interactive prompt. |

## Environment Variables

| Variable | Meaning |
|---|---|
| `SNAPZ_ALL_ROOT` | Local client store root, default `~/.snapz-all`. |
| `SNAPZ_LANG` | `en` or `zh`. |
| `SNAPZ_SAVE_WORKERS` | Default save worker count. |
| `SNAPZ_ZSTD_LEVEL` | zstd compression level. |
| `SNAPZ_GZIP_LEVEL` | gzip compression level. |
| `SNAPZ_REMOTE_ONLY` | Runtime default for remote-only local blob eviction. |
| `SNAPZ_SERVER_DATA` | Server data directory. |
| `SNAPZ_SERVER_HOST` | Server listen host. |
| `SNAPZ_SERVER_PORT` | Server listen port. |
| `SNAPZ_SERVER_ADMIN_TOKEN` | Admin API bearer token. |
| `SNAPZ_SERVER_MAX_BUNDLE_MB` | Upload size limit in MiB. |
| `SNAPZ_SERVER_CORS_ORIGIN` | Comma-separated browser origins for admin API CORS. |
| `SNAPZ_SERVER_TLS_CERT` | HTTPS certificate path. |
| `SNAPZ_SERVER_TLS_KEY` | HTTPS private key path. |
| `SNAPZ_SERVER_TLS_CLIENT_CA` | Client certificate CA path for mTLS. |
