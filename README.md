# snapz

Lightweight directory snapshot CLI. `snapz <path>` packs a directory into
a `tar.zst` archive under `~/.snapz-all/`, with named retention,
`ncdu`-style management, and dry-run + double confirmation before any
destructive action.

> **Status — stable (v2.0.0).** Save, list, restore (auto pre-restore +
> `--clean`), the `ncdu`-style curses TUI, rename/delete, **stats**,
> **prune** (retention policies + protected snapshots), **revert**
> (selective rollback), **undo** (chained rollback to the initial state),
> **find** (locate a path or glob across every snapshot), **check**
> (store validation), source lifecycle **init/archive/relocate** with
> automatic move detection, portable **bundle/import**, multi-tenant
> **snapz-server** remote sync, TUI **`/` filter**, machine
> readable **`--json`** output on every read command, and a one-command
> release pipeline (`scripts/build.sh` → wheel + sdist + `.pyz` +
> `.deb`) are all implemented and tested. **Snapshots are
> content-addressed**, so
> resnapping an unchanged tree costs ~0 bytes and `snapz gc` reclaims
> orphaned blobs after deletes. New captures use a root-level v3 blob
> pool shared across recorded source directories, while v2 per-dir CAS
> snapshots remain readable.

[**中文文档 / Chinese README**](./README.zh.md)

## Why

Existing snapshot tools are either heavy (restic / borg / kopia,
designed for repository-style backup with init + remote) or git-bound
(`git stash`, `git-snapshot`). `snapz` aims for the missing UX: type one
command on any directory, get a named, restorable archive in seconds,
without touching the directory itself.

## Install

Pick whichever fits your environment:

| Mode | File | Size | Best for |
|---|---|---|---|
| Debian package | `dist/snapz-cli_*_all.deb` | ~10 MB | Ubuntu/Debian installs with `/usr/bin/snapz` and `/usr/bin/snapz-server` |
| Zipapp | `dist/snapz.pyz`, `dist/snapz-server.pyz` | ~5 MB each | drop-in single executables; need `python3 ≥ 3.10` on target |
| Wheel | `dist/snapz_cli-*.whl` | ~30 KB | `pip install`, library use |

### From a release artifact

```bash
# 1. Debian / Ubuntu
sudo apt install ./dist/snapz-cli_*_all.deb
# or
sudo dpkg -i dist/snapz-cli_*_all.deb

# 2. Zipapp — single self-contained executable (zstandard bundled inside)
install -m 0755 dist/snapz.pyz ~/.local/bin/snapz
install -m 0755 dist/snapz-server.pyz ~/.local/bin/snapz-server

# 3. Wheel
pipx install "dist/snapz_cli-*.whl[zstd]"
# or
pip install --user "dist/snapz_cli-*.whl[zstd]"
```

### Update / uninstall

```bash
snapz update        # reinstall the latest snapz from GitHub
snapz uninstall     # show ~/.snapz-all size, ask whether to delete data, then uninstall
```

### From source (development)

```bash
git clone <this repo>
cd snapz
python3 -m venv .venv
.venv/bin/pip install -e .[dev]
ln -sf "$PWD/.venv/bin/snapz" ~/.local/bin/snapz             # shell-wide
ln -sf "$PWD/.venv/bin/snapz-server" ~/.local/bin/snapz-server
```

> ⚠️ **Conflict warning:** Ubuntu/Debian ship `snapd` at `/usr/bin/snapz`.
> Make sure `~/.local/bin` (or wherever you installed this `snapz`)
> precedes `/usr/bin` on `PATH`, or rename the binary if you prefer not
> to shadow it.

## Building release artifacts

Everything is wrapped by `scripts/build.sh`:

```bash
./scripts/build.sh all              # wheel + sdist + client/server .pyz + .deb
./scripts/build.sh wheel            # PEP 517 wheel + sdist only
./scripts/build.sh pyz              # shiv zipapp only
./scripts/build.sh deb              # Debian package only (rebuilds .pyz first)
./scripts/build.sh smoke            # run --version against the built artifacts
./scripts/build.sh --clean          # nuke dist/, build/, .build-venv/
./scripts/build.sh --lang zh all    # bake Chinese as the default --help language
                                    # (the SNAPZ_LANG env var still wins at runtime)
```

The script creates an isolated `.build-venv/`, installs `build`, `shiv`,
and `zstandard`, then drops everything in `dist/`. It unsets
`PYTHONPATH` first so a sourced ROS environment doesn't leak in.
Override the host Python with
`PYTHON=/path/to/python3 ./scripts/build.sh all`.

GitHub Releases are tag-driven. After committing the version bump, push
the branch and then push a matching `vX.Y.Z` tag:

```bash
git tag v2.0.0
git push origin main v2.0.0
```

The release workflow verifies that the tag matches `pyproject.toml`,
runs tests, builds the English `dist/`, then rebuilds with `--lang zh`
and uploads the standard `.deb`, `.pyz`, wheel, and sdist files plus
Chinese-default `snapz-zh.pyz`, `snapz-server-zh.pyz`, and
`snapz-cli_<version>_all.zh.deb` files to the GitHub Release.

A previous iteration also produced a 13 MB PyInstaller `--onefile`
binary; it was removed because the `.pyz` is much lighter and Python is
already on every realistic target.

## Quick start

```bash
$ snapz                              # interactive snapshot of current dir
$ snapz .                            # same as above
$ snapz ../some/relative/path        # any path, resolved to abs
$ snapz /abs/path

$ snapz save /tmp/proj -n baseline -y  # scriptable, no prompts
$ snapz list                          # ncdu-style TUI for cwd
$ snapz list --text                   # plain text table
$ snapz alist                         # global TUI across all dirs
$ snapz show baseline --path /tmp/proj
$ snapz mv baseline v0.1 --path /tmp/proj
$ snapz rm v0.1 --path /tmp/proj -y
$ snapz restore v0.1 --path /tmp/proj           # dry-run + 2-step confirm
$ snapz restore v0.1 --path /tmp/proj --clean   # also remove extras
$ snapz restore v0.1 --path /tmp/proj --no-auto-save -y   # scripted

# Annotated snapshots
$ snapz save /tmp/proj -n release-1 -y -m "before refactor of fooBar"
$ snapz show release-1 --path /tmp/proj         # 'note' line is highlighted

# Diff & local excludes
$ snapz diff release-1 --path /tmp/proj         # snapshot vs live tree (TUI by default)
$ snapz diff v0.1 v0.2 --path /tmp/proj         # two snapshots
$ snapz diff --path /tmp/proj                   # interactive: pick A, then pick B
                                               # (B picker includes a [live] row)
$ snapz diff release-1 --path /tmp/proj --text  # plain text instead of curses
# Inside the diff TUI:
#   ↑↓     navigate
#   ⏎      open the unified diff for the file under the cursor
#   space  mark file (q/⏎ to leave the unified diff sub-view)
#   d      mark parent dir
#   a/n    select all / clear
#   e      apply marks (appends patterns to local excludes)

# Snapshot pickers — every name argument is now optional. Run any of these
# with no name and you get an interactive picker (current dir is searched):
$ snapz rm --path /tmp/proj
$ snapz show
$ snapz restore
$ snapz export /tmp/scratch
$ snapz revert
$ snapz mv             # picks "old", then prompts for the new name

# Export to an arbitrary directory (no auto-pre-restore, never touches src)
$ snapz export v0.1 /tmp/scratch --path /tmp/proj

# Portable bundles (move snapshot history between machines/stores)
$ snapz bundle /tmp/proj /tmp/proj.snapz        # pack every snapshot for /tmp/proj
$ snapz import /tmp/proj.snapz                  # import as archived history
$ snapz import /tmp/proj.snapz --path /tmp/proj # bind to an existing live directory

# Remote sync through a standalone multi-tenant server
$ snapz-server --data /srv/snapz setup
$ snapz-server --data /srv/snapz user add acme alice
$ snapz-server --data /srv/snapz run \
    --host 0.0.0.0 \
    --port 8765 \
    --tls-cert /etc/snapz/tls/fullchain.pem \
    --tls-key /etc/snapz/tls/privkey.pem \
    --admin-token "$(openssl rand -hex 32)"
# Admin UI: https://server:8765/admin
# Cross-origin admin apps must be allowlisted with --cors-origin https://admin.example
# Optional mTLS hardening: add --tls-client-ca /etc/snapz/tls/client-ca.pem
# Vben Admin drop-in files: web/vue-vben-admin-snapz/
$ snapz login https://server:8765 --tenant acme --username alice
# With mTLS enabled:
$ snapz login https://server:8765 --tenant acme --username alice \
    --tls-ca /etc/snapz/tls/server-ca.pem \
    --tls-client-cert ~/.config/snapz/client.pem \
    --tls-client-key ~/.config/snapz/client-key.pem
$ snapz push all                                # upload every active/archived source
$ snapz pull all                                # pull every remote source into archive
$ snapz adopt remote-src_xxx /tmp/proj          # bind a pulled archive to a live dir

# Storage breakdown (TUI by default; --text for plain output)
$ snapz stats                                    # current dir
$ snapz stats --all                              # every recorded source
$ snapz stats /tmp/proj --text                   # per-dir + dedup ratio

# Retention policy: drop snapshots, keep what matters (curses preview by default)
$ snapz prune --keep-last 5 --path /tmp/proj
$ snapz prune --keep-daily 7 --keep-weekly 4 --path /tmp/proj
$ snapz prune --keep-within-days 30 --protect release-1.0 --path /tmp/proj
$ snapz prune --keep-last 5 --dry-run --text     # report only
$ snapz protect release-1.0 --path /tmp/proj     # persistent prune/delete guard
$ snapz unprotect release-1.0 --path /tmp/proj

# Source directory lifecycle
$ snapz init /tmp/proj                          # write .snapz-id for cross-device move detection
$ mv /tmp/proj /tmp/proj-renamed
$ snapz list /tmp/proj-renamed                  # exact moved-source matches auto-bind on use
$ snapz relocate /tmp/proj /tmp/proj-renamed     # bind snapshots to renamed dir
$ snapz relocate --auto /tmp -y                 # auto-bind exact inode/.snapz-id matches
$ snapz relocate --auto ~ --dry-run             # preview only
$ snapz archive list                             # dirs that were deleted/recreated
$ snapz archive restore <key> baseline /tmp/out  # restore archived snapshot elsewhere

# Selective rollback (revert one file or subtree without touching the rest)
$ snapz revert v0.1 src/main.py --path /tmp/proj          # one file
$ snapz revert v0.1 src docs --path /tmp/proj             # two subtrees
$ snapz revert v0.1 src --delete-extras --path /tmp/proj  # also wipe additions
$ snapz revert v0.1 --path /tmp/proj                      # opens the picker TUI

# Undo — pop the most recent restore/revert, chain to roll back further
$ snapz undo                                     # confirm + roll back the last op
$ snapz undo -y                                  # no prompt; great for "oh no, again"
$ snapz undo --no-clean                          # keep files added since the safety capture
# Each `restore` / `revert` saves an `auto-pre-*` snapshot before writing;
# `snapz undo` consumes the most recent one. Repeat until you're back to
# the initial state — `auto-*` snapshots are otherwise hidden from `list`.

# Find — locate a path / glob across every CAS snapshot
$ snapz find src/main.py                         # exact path: every snapshot containing it
$ snapz find src --path /tmp/proj                # directory prefix → whole subtree
$ snapz find '**/*.py'                           # recursive glob (quote it!)
$ snapz find src/main.py --json | jq             # structured rows for chatops

# Store reliability
$ snapz check --path /tmp/proj                   # manifest/blob/metadata validation
$ snapz check --all --deep --json | jq           # decompress and verify every blob
$ snapz check --all --fix                        # safe fixes: registry, perms, temp files
$ snapz migrate --all --to v3 --dry-run          # preview v2 per-dir CAS migration
$ snapz migrate --all --to v3                    # move old blobs into the global pool

# Persistent preferences (~/.snapz-all/_config.json)
$ snapz config list                              # show defaults + overrides
$ snapz config set save_picker true              # enable post-walk picker in
                                                # interactive `snapz save`
$ snapz config set update_check.enabled false    # disable daily background
                                                # GitHub update checks
$ snapz config get save_picker
$ snapz config unset save_picker
```

### Local excludes

Per-directory opt-out patterns live at
`~/.snapz-all/<key>/_local_excludes` (gitignore syntax, one per line).
Unlike `.snapzignore` / `.gitignore` they are **never committed** —
they're attached to the local store, not the project. Edit by hand or
let `snapz diff --tui` / the save picker append entries for you.

### Shell completion

Shell completion is provided by the optional `argcomplete` package:

```bash
pip install argcomplete
# bash:
eval "$(register-python-argcomplete snapz)"
# zsh: same, but `register-python-argcomplete --shell zsh snapz`
```

Subcommands, flags, and **snapshot names** (for `rm`, `mv`, `show`,
`restore`, `export`, `diff`) all complete dynamically against the
current directory's store.

### TUI keys

In `snapz list` / `snapz alist`:

| Key | Action |
|---|---|
| `j` / `k`, `↑` / `↓` | move cursor |
| `PgUp` / `PgDn`, `Home` / `End` | jump |
| `Enter` | snapshot details popup |
| `r` | restore (suspends TUI, runs the regular confirm flow) |
| `d` | delete (in-place yes/no popup) |
| `n` | rename (in-place input box) |
| `/` | substring filter on name + note (Esc clears) |
| `q`, `Esc` | quit (Esc clears the filter first if one is active) |

The same `/` filter is available inside the snapshot picker (`rm`,
`mv`, `show`, `restore`, `export`, `diff`, `revert`).

When stdout is not a TTY (piped, captured, redirected) `list` / `alist`
automatically degrade to a plain text table; `--text` forces that
behaviour explicitly.

In `snapz stats`:

| Key | Action |
|---|---|
| `j` / `k`, `↑` / `↓` | move cursor |
| `Enter`, `d`, `→` | drill into the per-source snapshot view |
| `b`, `Esc`, `←`, `Backspace` | back to the top-level overview |
| `q` | quit |

In `snapz prune`:

| Key | Action |
|---|---|
| `j` / `k`, `↑` / `↓` | move cursor |
| `Space` | toggle keep / drop on this row |
| `a` / `n` | mark every row drop / keep |
| `r` | reset to the policy-computed plan |
| `Enter`, `e` | apply (delete the rows currently marked drop) |
| `q`, `Esc` | cancel without deleting |

In `snapz revert` (when no `paths` given):

| Key | Action |
|---|---|
| `j` / `k`, `↑` / `↓` | move cursor |
| `Space` | toggle the cursor file |
| `d` | toggle the cursor file's parent directory (recursive) |
| `a` / `n` | mark every entry / clear all |
| `Enter`, `e` | apply — proceeds to the confirm prompt |
| `q`, `Esc` | skip / abort |

### Internationalization (i18n)

`snapz` ships with English and Chinese (`zh`) translations of every CLI
string — argparse `--help` output, prompts, confirms, and runtime
status lines. Pick a language at runtime, build, or both:

```bash
SNAPZ_LANG=zh snapz --help            # one-shot: Chinese for this invocation
SNAPZ_LANG=zh snapz save .            # affects everything snapz prints
export SNAPZ_LANG=zh                 # session-wide

./scripts/build.sh --lang zh all    # bake Chinese into the artifact
                                    # (the env var still overrides at runtime)
```

Resolution order is: `SNAPZ_LANG` env var → `DEFAULT_LANG` baked into
`snapz/i18n.py` (default `"en"`) → English. Unknown / partial
translations fall back to English silently — they never crash the CLI.

### Colour & visual style

`snapz` ships with semantic ANSI colouring (cyan paths, bold snapshot
names, dim metadata, green success, yellow warnings, red errors). It
auto-disables when stdout isn't a TTY, so piping into `grep`, `less`,
or a CI log stays clean.

| Environment variable | Effect |
|---|---|
| `NO_COLOR=1` | force monochrome output everywhere |
| `FORCE_COLOR=1` | force colour even when not a TTY (e.g. `snapz list \| less -R`) |
| `TERM=dumb` | treated as monochrome |
| `SNAPZ_LANG=zh` | switch CLI text (--help, prompts, runtime output) to Chinese |
| `SNAPZ_LANG=en` | force English even on a build with `--lang zh` baked in |

The curses TUI uses the same palette via curses colour pairs.

The interactive flow (semantic colour omitted in this snippet — running
in a real terminal you'll see cyan paths, bold names, and a green
progress bar):

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

## Auto-* safety snapshots and `snapz undo`

Every destructive op (`restore`, `revert`) takes a content-addressed
snapshot of the live tree first, named `auto-pre-restore-<ts>` /
`auto-pre-revert-<ts>`. By default these are **hidden** from
`snapz list`, `snapz alist`, and every interactive picker — they're
plumbing for `snapz undo`, not part of your named history. The footer
of `snapz list` reminds you when some are present:

```
…
auto-* hidden — pass --all to include
```

`snapz undo` pops the most recent safety snapshot, restores it
(`auto_save=False, --clean` by default), and **deletes** it after a
successful roll-back so the next `undo` walks one step further back.
Repeat until there's nothing left to undo and you're sitting on the
initial state.

```bash
$ snapz restore release-1.0    # captures auto-pre-restore-T1 (= live before restore)
$ snapz revert release-1.0 src # captures auto-pre-revert-T2  (= live after T1's restore)
$ snapz undo                   # rolls back to T2 (and consumes that capture)
$ snapz undo                   # rolls back to T1 (initial state)
$ snapz undo                   # error: no more undo points
```

Use `snapz list --all` if you ever want to see / hand-clean the safety
snapshots; `snapz rm --all` exposes them in the picker for deletion.

## `snapz find` — locate a path / glob across every snapshot

```bash
snapz find src/main.py            # exact path
snapz find src                    # directory prefix → whole subtree
snapz find '**/*.py'              # recursive glob (quote it so the shell doesn't expand)
snapz find docs/intro.md --json   # structured: by-path → [hits…]
```

Output groups every matching snapshot by source-relative path,
newest-first, and tags rows whose content differs from the
chronologically next-newer snapshot with `← changed`. Internally each
manifest is a `path → sha256` table, so `find` is fast even across
hundreds of snapshots — no archive unpacking required.

## JSON output (`--json`)

`save`, `list`, `alist`, `show`, `stats`, `gc`, `find`, and `undo`
emit structured JSON to stdout when invoked with `--json`. The flag
is position-independent (`snapz --json list` and `snapz list --json`
are equivalent). Pretty progress / ANSI styling stays on stderr where
applicable.

```bash
snapz list --json | jq '.snapshots[] | select(.size_bytes > 1e6) | .name'
snapz find 'src/**/*.py' --json | jq '.by_path | keys'
snapz undo --json -y          # script-friendly rollback (must pass -y, JSON never prompts)
```

`snapz undo --json` without `-y` returns `{"undone": false, "reason":
"needs-confirmation", "target": …}` and a non-zero exit code, so a CI
script can dry-run-inspect what would happen before committing.

## Stats, prune, revert

These three subcommands round out day-to-day maintenance once you have
a few months of snapshots piling up:

- **`snapz stats`** — per-source storage breakdown: snapshot count,
  on-disk bytes, logical bytes (sum of pre-dedup sizes), and the
  resulting dedup ratio. The TUI sorts heaviest consumers first; press
  `Enter` to drill into a single source and inspect every snapshot it
  owns. `--all` widens the top view across every recorded directory.

- **`snapz prune`** — apply a retention policy and delete the rest.
  Rules are *unioned* (a snapshot is kept if any rule matches):

  | Flag | Meaning |
  |---|---|
  | `--keep-last N` | keep the N most recent snapshots |
  | `--keep-within-days D` | keep everything created in the last D days |
  | `--keep-daily N` | keep the latest of each day for the last N days |
  | `--keep-weekly N` | keep the latest of each ISO week for the last N |
  | `--protect NAME` | never delete this snapshot (repeatable) |

  By default the curses TUI shows the keep/drop split, lets you toggle
  individual rows, then applies. `-y` skips the TUI; `--dry-run` reports
  without deleting; `--no-gc` keeps orphan blobs around for later. At
  least one rule (or `--protect`) is required — calling `prune` with no
  rules raises an error so a typo can't wipe everything.

- **`snapz revert`** — restore selected paths from a snapshot back into
  the live source tree without touching anything else. Pass
  source-relative paths (files or directories) on the command line, or
  omit them to open a multi-select picker over the snapshot's manifest.
  Before writing, an `auto-pre-revert-*` snapshot is taken (disable with
  `--no-auto-save`), so the operation is always reversible. `--delete-extras`
  additionally removes files under each requested path that aren't in
  the snapshot — useful when you want a chosen subtree to match the
  snapshot exactly. CAS-format snapshots only; legacy `.tar.zst`
  archives error out (use `restore` / `export`).

## Storage layout

`snapz` uses **content-addressable storage**: each unique file content
(by sha256) is stored once as a zstd-compressed blob and shared across
every snapshot that contains that file. Snapshots are tiny manifests
referencing those blobs.

```
~/.snapz-all/
├── registry.json                       # path <-> key reverse lookup
└── <sha1[:12]>-<basename>/             # one folder per snapshotted dir
    ├── _meta.json                      # { abspath, first_seen, last_used, snapshot_count }
    ├── objects/                        # blob pool (sha256 sharded by 2-char prefix)
    │   └── ab/
    │       └── abcdef1234...           # zstd-compressed file payload
    ├── snapshots/
    │   ├── before-refactor.manifest.json   # path -> sha256 + mode + mtime
    │   └── auto-20260428.manifest.json
    ├── before-refactor.meta.json       # { name, source, created, size_bytes, file_count, ... }
    └── auto-20260428.meta.json
```

**Practical impact.** Re-snapping a 500 MB project where nothing
changed costs ~0 bytes (just the manifest, ~kilobytes). Changing one
file adds exactly one new blob. Snapshots created before this format
(plain `.tar.zst` archives) still live alongside the new layout and
remain restorable.

The storage root is `~/.snapz-all/` by default. Set `SNAPZ_ALL_ROOT` to
override (used by tests).

Folder permissions are clamped to `700` and blobs/manifests to `600`.

### Reclaiming orphaned blobs

Deleting a snapshot only removes its manifest + meta — blobs that other
snapshots still reference stay where they are. When a blob becomes
unreferenced (because every snapshot that pointed at it is gone), it
hangs around until you run:

```bash
snapz gc                  # reclaim orphans for cwd
snapz gc --path /a/b      # reclaim orphans for a specific dir
snapz gc --all            # reclaim across every recorded directory
snapz gc --dry-run        # report only, don't delete
```

`snapz save -y` and `snapz restore -y` already do everything else
on-the-fly; `gc` is the only command you ever need to run periodically
(or never — it's only useful after deleting old snapshots).

### Validating and migrating the store

`snapz check` verifies registry entries, per-directory metadata,
snapshot meta/manifest pairs, blob reachability, and orphaned global
blobs. Add `--deep` to decompress every blob and verify its sha256.
`--fix` is intentionally conservative: it rebuilds safe metadata,
repairs permissions, removes stale temporary files, and rewrites a
manifest's snapshot name when it disagrees with the meta file. It does
not delete snapshots or orphan blobs; use `snapz gc` for reclamation.

`snapz migrate --to v3` moves legacy per-directory CAS blobs into the
root-level `~/.snapz-all/objects/` pool. Existing v2 snapshots stay
readable before and after migration.

### Renamed and deleted source directories

New snapshots record the source directory identity (`dev:ino`) in store
metadata. If a source directory is deleted, or deleted and later
recreated as a different directory, its old snapshots stop appearing in
normal `snapz list` / `snapz alist` output and move to
`snapz archive list`. This prevents a new unrelated directory at the
same path from inheriting old snapshots by accident.

When a directory is intentionally renamed, run:

```bash
snapz relocate /old/path /new/path
```

This moves the store binding to the new live directory and updates
snapshot metadata to point at that path. Snapshot contents are not
rewritten because manifests store source-relative paths.

Archived snapshots can be restored without recreating the original
source path:

```bash
snapz archive restore <archive-key> <snapshot-name> /restore/path
```

In a TTY, `snapz archive restore` opens pickers for the archived source
and snapshot when those arguments are omitted.

## Ignore rules

By default the following sources are merged when scanning a tree:

1. Built-in defaults: `__pycache__/`, `node_modules/`, `.venv/`,
   `venv/`, `*.pyc`, `.DS_Store`, `dist/`, `build/`, etc.
2. `.gitignore` files from the source root and nested directories, plus
   `.git/info/exclude`.
3. `.snapzignore` files from the source root and nested directories.

Ignore matching is powered by `pathspec`, so Git-style negation
(`!keep.log`), anchored patterns, directory patterns, and nested ignore
files are honoured.

Files larger than 100 MiB are skipped with a warning. Pass
`--include-large` to keep them.

## Compression

`snapz` writes `.tar.zst` when the optional `zstandard` package is
available, otherwise falls back to `.tar.gz` (stdlib). You can force
gzip with `snapz --no-zstd ...`. Portable `.snapz` bundles and remote
pushes use the same zstd-first behavior, and remote uploads are checked
with a bundle SHA-256 before they are accepted.

## Library use

The same operations are available as Python functions, useful for
embedding `snapz` into chatops or scripts:

```python
from snapz import api

outcome = api.save("/path/to/proj", "before-refactor")
print(outcome.snapshot.size_bytes)

for snapz in api.list_snapshots("/path/to/proj"):
    print(snapz.name, snapz.created, snapz.size_bytes)

# Restore (auto-pre-restore + clean both default-off in api; turn on as needed)
estimate = api.restore_estimate("/path/to/proj", "before-refactor")
print(len(estimate.new_files), len(estimate.overwritten_files))
api.restore("/path/to/proj", "before-refactor", auto_save=True, clean=False)

api.rename("/path/to/proj", "before-refactor", "v0.1")
api.delete("/path/to/proj", "v0.1")

# Storage breakdown
for entry in api.stats():                  # all sources, sorted by disk usage
    print(entry.abspath, entry.snapshot_count,
          entry.on_disk_bytes, f"{entry.dedup_ratio:.1f}x")

# Retention policy
plan = api.plan_prune("/path/to/proj", keep_last=10, keep_weekly=4)
print(len(plan.keep), "kept,", len(plan.drop), "to drop")
outcome = api.execute_prune(plan, dry_run=False)
print(outcome.deleted, outcome.bytes_freed)

# Selective rollback (auto-pre-revert snapshot is created by default)
result = api.revert("/path/to/proj", "v0.1", ["src/main.py"])
print(result.reverted_count, "files written, pre-revert =",
      result.pre_revert.name if result.pre_revert else None)
```

`api.estimate(path)` runs only the dry-run walker and returns the
projected file count and byte total. `api.restore_estimate(path, name)`
diffs an archive against the current tree and reports the would-be
adds, overwrites and extras.

## Roadmap

- **M1 ✅** — non-TUI command surface
- **M2 ✅** — curses TUI for `snapz list` / `snapz alist` (`d` / `n` keys)
- **M3 ✅** — `snapz restore <name>` + auto pre-restore + TUI `r` key
- **M5 ✅** — automated build (wheel/sdist/zipapp/standalone binary)
- **M6 ✅** — `snapz stats` / `snapz prune` / `snapz revert` with curses pickers
- **M4 ✅** — full `.snapzignore` / `.gitignore` semantics, store check,
  protected snapshots, v3 global CAS migration
- **M7** — richer `gc` policies, in-TUI sort, and additional filters

## Development

```bash
env -u PYTHONPATH .venv/bin/python -m pytest tests/
```

The `env -u PYTHONPATH` is only relevant when ROS or another stack
injects pytest plugins into the system path; drop it on a clean shell.

Compile-only sanity check:

```bash
.venv/bin/python -m py_compile snapz/*.py
```

## License

MIT.
