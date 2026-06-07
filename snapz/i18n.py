"""Tiny in-process i18n for CLI strings.

Usage::

    from snapz.i18n import t

    print(t("save.confirm"))           # → string in current language
    print(t("kv.deleted", n=3))        # → with `{n}` interpolation

Language is selected, in order, by:

1. ``SNAPZ_LANG`` environment variable (``en`` or ``zh``);
2. The ``DEFAULT_LANG`` constant in this file. The build script can
   sed-patch that constant to bake a language into a release artifact
   (see ``scripts/build.sh --lang zh``).

All keys MUST exist for ``en``; ``zh`` is a partial overlay and falls
back to English on missing keys. This keeps the CLI safe to extend —
forgetting a Chinese translation just shows English, never crashes.

Only user-facing CLI text lives here. Library docstrings, log
records, and exception messages stay English so tooling and tracebacks
stay grep-friendly.
"""

from __future__ import annotations

import os

# build-time bake target — keep on its own line, exact spelling.
DEFAULT_LANG = "en"

SUPPORTED_LANGS = ("en", "zh")


def get_lang() -> str:
    raw = os.environ.get("SNAPZ_LANG")
    if raw and raw in SUPPORTED_LANGS:
        return raw
    if DEFAULT_LANG in SUPPORTED_LANGS:
        return DEFAULT_LANG
    return "en"


_EN: dict[str, str] = {
    # ---- root parser & global flags ----
    "root.description": "Lightweight directory snapshot tool.",
    "flag.version": "show program's version number and exit",
    "flag.no_zstd": "force gzip compression even if zstandard is available",
    "argparse.usage": "usage: ",
    "argparse.positionals": "positional arguments",
    "argparse.options": "options",
    "argparse.help": "show this help message and exit",
    "argparse.error_format": "%(prog)s: error: %(message)s\n",
    "argparse.unrecognized": "unrecognized arguments: %s",
    "argparse.required": "the following arguments are required: %s",
    "argparse.invalid_choice": "invalid choice: %(value)r (choose from %(choices)s)",
    "argparse.expected_one": "expected one argument",
    "argparse.expected_at_least_one": "expected at least one argument",
    "argparse.expected_at_most_one": "expected at most one argument",
    "argparse.argument_message": "argument %(argument_name)s: %(message)s",
    "argparse.one_required": "one of the arguments %s is required",
    "argparse.not_allowed": "not allowed with argument %s",

    # ---- save (scripted) ----
    "save.help": "non-interactive snapshot create",
    "save.yes": "skip confirmation",
    "save.message": "short human-readable note attached to the snapshot",
    "save.no_cache": "disable the per-source file hash cache for this save",
    "save.workers": "number of worker threads to use while writing blobs",
    "save.workers_positive": "--workers must be at least 1",
    "source_config.heading": "Source snapshot profile",
    "source_config.body": (
        "snapz excludes generated files by default. Enter preset names to "
        "include them for this source, or press Enter to keep the defaults."
    ),
    "source_config.include_prompt": "include presets",
    "source_config.added": "added {n} include override(s) for this source",
    "suggest.heading": "Possible local excludes",
    "suggest.summary": "These account for about {pct}% of the planned snapshot.",
    "suggest.confirm": "add these patterns to this source's local excludes?",
    "suggest.added": "added {n} local exclude pattern(s); re-planning...",
    "suggest.none_added": "all suggested patterns were already excluded",

    # ---- list / alist ----
    "list.help": "list snapshots of the current directory",
    "list.text": "force plain-text output instead of the curses TUI",
    "list.timeline": "group text output by day",
    "alist.help": "list snapshots across all directories",

    # ---- web ----
    "web.help": "start the local snapz client web UI",
    "web.host": "host to bind (default: 127.0.0.1)",
    "web.port": "port to bind (default: 3000; use 0 for a random free port)",
    "web.allow_remote": "allow binding to a non-loopback host",
    "web.invalid_port": "--port must be between 0 and 65535",
    "web.refuse_remote": (
        "refusing to bind {host}; pass --allow-remote if you really want "
        "the local snapshot API reachable from other machines"
    ),
    "web.started": "snapz client web UI: {url}",
    "web.stop_hint": "Press Ctrl+C to stop.",

    # ---- rm / mv / show ----
    "rm.help": "delete a snapshot",
    "rm.path": "target directory (default: cwd)",
    "mv.help": "rename a snapshot",
    "mv.path": "target directory (default: cwd)",
    "show.help": "print snapshot metadata",
    "show.path": "target directory (default: cwd)",

    # ---- restore ----
    "restore.help": "restore a snapshot back over the source directory",
    "restore.path": "target directory (default: cwd)",
    "restore.yes": "skip the confirmation prompt",
    "restore.no_auto_save": (
        "do NOT take an auto-pre-restore snapshot before extracting"
    ),
    "restore.clean": (
        "delete files in the working tree that are not in the archive"
    ),
    "restore.preview_overwrite": "will overwrite",
    "restore.preview_add": "will add",
    "restore.preview_clean": "will delete with --clean",
    "restore.preview_keep": "extra files kept",
    "restore.more_paths": "... and {n} more",

    # ---- export ----
    "export.help": "extract a snapshot into an arbitrary destination directory",
    "export.name": "snapshot name",
    "export.dst": "destination directory (will be created if missing)",
    "export.path": (
        "source directory whose store holds the snapshot (default: cwd)"
    ),
    "export.overwrite": "extract even if the destination is non-empty",
    "export.yes": "accepted for script compatibility; export does not prompt",

    # ---- portable bundles ----
    "bundle.help": "pack all snapshots for one source into a portable bundle",
    "bundle.source": "source directory path (or archive key with --archive)",
    "bundle.dst": "bundle file to create, e.g. project.snapz",
    "bundle.archive": "treat source as an archived source key",
    "bundle.overwrite": "replace an existing bundle file",
    "import.help": "import snapshots from a portable snapz bundle",
    "import.bundle": "bundle file produced by `snapz bundle`",
    "import.path": (
        "bind imported snapshots to this existing source directory "
        "(omit to keep them archived)"
    ),
    "import.overwrite": "replace snapshots with the same name in the target store",

    # ---- remote login/logout/sync ----
    "login.help": "log in to a snapz-server remote",
    "login.server": "server URL, e.g. http://127.0.0.1:8765",
    "login.tenant": "tenant name",
    "login.username": "username",
    "login.password": "password (prompts when omitted)",
    "login.device": "device name recorded on the server",
    "login.tls_ca": "CA bundle for verifying the HTTPS server certificate",
    "login.tls_client_cert": "PEM client certificate for mTLS",
    "login.tls_client_key": "PEM private key for the mTLS client certificate",
    "logout.help": "remove the saved remote token",
    "push.help": "push snapshots to the configured remote",
    "push.scope": "push all local sources",
    "pull.help": "pull snapshots from the configured remote",
    "pull.scope": "pull all remote sources",
    "adopt.help": "bind an archived source, such as a pulled remote archive, to a path",
    "adopt.archive_key": "archived source key",
    "adopt.path": "target source directory",

    # ---- diff ----
    "diff.help": (
        "show files changed between two snapshots (or vs the live tree)"
    ),
    "diff.a": "snapshot to diff FROM",
    "diff.b": "snapshot to diff TO (omit to compare against the current tree)",
    "diff.path": "source directory whose store to look in (default: cwd)",
    "diff.text": "force plain-text output instead of curses TUI",
    "diff.tui": "open the curses picker (mark files/dirs to add to local excludes)",

    # ---- config ----
    "config.help": "get/set persistent user preferences (e.g. `save_picker`)",
    "config.op": "operation",
    "config.key": "config key (omit with `list`)",
    "config.value": "value for `set`",

    # ---- gc ----
    "gc.help": "reclaim disk space from blobs no longer referenced by any snapshot",
    "gc.path": "target directory (default: cwd)",
    "gc.all": "garbage-collect every directory under the storage root",
    "gc.dry_run": "report what would be removed without deleting",
    "gc.rebuild_index": "rebuild the global blob reference index before collecting",

    # ---- maintenance / source identity ----
    "check.help": "validate store metadata and blob reachability",
    "check.path": "source directory (default: cwd)",
    "check.all": "check every source under the storage root",
    "check.deep": "also verify blob contents",
    "check.fix": "repair safe metadata issues where possible",
    "migrate.help": "migrate legacy per-directory blobs to the v3 global CAS pool",
    "migrate.path": "source directory (default: cwd)",
    "migrate.all": "migrate every source under the storage root",
    "migrate.to": "target storage layout version",
    "migrate.dry_run": "report what would be migrated without changing data",
    "init.help": "write a .snapz-id marker for reliable move detection",
    "init.path": "source directory (default: cwd)",
    "init.force": "replace an existing .snapz-id marker",
    "relocate.help": "move snapshots from an old source path to a renamed live directory",
    "relocate.paths": "OLD NEW for manual relocation, or ROOT... with --auto",
    "relocate.auto": "scan roots for moved archived sources and relocate exact matches",
    "relocate.dry_run": "show automatic matches without moving store bindings",
    "relocate.yes": "apply automatic relocation without prompting",
    "archive.help": "list archived sources and restore archived snapshots",
    "archive.list_help": "list archived sources",
    "archive.restore_help": "restore an archived snapshot to a destination path",
    "archive.archive_arg": "archive key or original source path",
    "archive.snapshot": "snapshot name",
    "archive.dst": "destination path",
    "archive.overwrite": "extract even if the destination is non-empty",
    "protect.help": "mark a snapshot as protected",
    "protect.path": "target directory (default: cwd)",
    "unprotect.help": "remove snapshot protection",

    # ---- stats ----
    "stats.help": "show storage usage and dedup ratio per source directory",
    "stats.path": "source directory (default: cwd; use --all for everything)",
    "stats.all": "report stats for every recorded source directory",
    "stats.text": "force plain-text output instead of the curses TUI",

    # ---- prune ----
    "prune.help": "delete snapshots according to a retention policy",
    "prune.path": "source directory (default: cwd)",
    "prune.keep_last": "always keep the N most recent snapshots",
    "prune.keep_within": "keep all snapshots created within DAYS days",
    "prune.keep_daily": "keep N most recent calendar days (one per day)",
    "prune.keep_weekly": "keep N most recent ISO weeks (one per week)",
    "prune.protect": "never delete this snapshot (repeatable)",
    "prune.yes": "skip the curses preview / confirmation",
    "prune.dry_run": "report what would be removed without deleting",
    "prune.no_gc": "don't run garbage collection on freed blobs",
    "prune.text": "force plain-text plan instead of the curses TUI",

    # ---- revert ----
    "revert.help": "restore selected paths from a snapshot back into the source",
    "revert.name": "snapshot to revert from",
    "revert.paths": (
        "source-relative file or directory paths to restore "
        "(omit to open the curses picker)"
    ),
    "revert.path": "source directory (default: cwd)",
    "revert.yes": "skip the confirmation prompt",
    "revert.no_auto_save": (
        "do NOT take an auto-pre-revert snapshot before extracting"
    ),
    "revert.delete_extras": (
        "under each requested path, delete files not present in the snapshot"
    ),
    "revert.text": (
        "non-interactive mode (paths must be given on the command line)"
    ),

    # ---- undo (v0.2) ----
    "undo.help": "roll back the most recent restore/revert (chainable to initial)",
    "undo.path": "source directory (default: cwd)",
    "undo.yes": "skip the confirmation prompt",
    "undo.no_clean": (
        "keep extra files that were added since the safety snapshot "
        "(default: clean to match it byte-for-byte)"
    ),
    "undo.heading": "undo {name}",
    "undo.captured": "captured at",
    "undo.remaining": "remaining undo points",
    "undo.no_target": (
        "nothing to undo under {path} — no auto-pre-* safety snapshot found."
    ),
    "undo.confirm": "roll back to this state?",
    "undo.success": "rolled back to {when}",

    # ---- find (v0.2) ----
    "find.help": "list every snapshot that contains a given path or glob",
    "find.pattern": (
        "literal path, directory prefix, or fnmatch glob "
        "(supports ** for recursive matching)"
    ),
    "find.path": "source directory (default: cwd)",
    "find.all": "include auto-* safety snapshots in the scan",
    "find.text": "force plain-text output (default when stdout isn't a TTY)",
    "find.no_matches": "no matches for {pattern} in {path}",
    "find.summary": (
        "matched {paths} path(s) across {hits} snapshot row(s) "
        "in {scanned} CAS snapshot(s)"
    ),
    "find.changed_marker": "← changed",

    # ---- browse (view-mode path picker; shared by cat/browse/future grep) ----
    "cat.help": "print one file from a snapshot",
    "cat.snapshot": "snapshot name",
    "cat.relpath": "source-relative path inside the snapshot",
    "cat.path": "source directory (default: cwd)",
    "cat.raw": "write raw bytes to stdout",
    "cat.binary_ok": "allow binary bytes when stdout is piped",
    "cat.no_path_given": "no path given. pass a source-relative path.",
    "cat.no_such_path": "no path {path!r} in snapshot {snap}",
    "cat.binary_placeholder": "[binary {size}] (use --raw or pipe with --binary-ok to dump bytes)",
    "cat.browse_footer": "↑↓ move  ·  ⏎/→ open dir  ·  r/⏎ choose file  ·  ← up  ·  q quit",
    "browse.help": "browse paths inside a snapshot",
    "browse.snapshot": "snapshot name",
    "browse.path": "source directory (default: cwd)",
    "browse.filter": "initial substring filter",
    "browse.title": "browse {name}",
    "browse.footer": "↑↓ move  ·  Enter open/drill  ·  r return path  ·  h back  ·  q quit",

    # ---- tag (user-defined labels) ----
    "tag.help": "manage user-defined snapshot tags (add / rm / list)",
    "tag.add_help": "attach one or more tags to a snapshot",
    "tag.rm_help": "remove one or more tags from a snapshot",
    "tag.list_help": "list every tag and the snapshots that carry it",
    "tag.snapshot": "snapshot name to mutate",
    "tag.values": "one or more tag labels (whitespace-separated)",
    "tag.path": "source directory (default: cwd)",
    "tag.missing_name": "error: snapshot name is required",
    "tag.missing_tags": "error: at least one tag is required",
    "tag.added": "tagged",
    "tag.removed": "untagged",
    "tag.empty": "no tags on any snapshot",

    # ---- prune.keep_tag flag ----
    "prune.keep_tag": "keep snapshots carrying TAG (repeatable)",

    # ---- log (operation history) ----
    "log.help": "show the operation history for a source (or --all sources)",
    "log.path": "source directory (default: cwd)",
    "log.all": "read events from every source under the store root",
    "log.limit": "limit to the N most recent events (default: all)",
    "log.kind": "comma-separated event kinds to keep (save,restore,revert,...)",
    "log.empty": "no events recorded",

    # ---- shell completion ----
    "completion.help": "generate or install shell completion for bash/zsh",
    "completion.action": "shell to print, or install",
    "completion.shell": "shell to install when using `install`",
    "completion.rcfile": "shell startup file to append to",
    "completion.argcomplete_missing": (
        "shell completion requires argcomplete. Install with "
        "`pip install snapz-cli[completion]` or `pip install argcomplete`."
    ),
    "completion.unsupported_shell": "unsupported shell: {shell}",
    "completion.detect_failed": (
        "could not detect bash or zsh. Pass `--shell bash` or `--shell zsh`."
    ),
    "completion.installed": (
        "installed snapz {shell} completion in {path}. Restart the shell or "
        "source that file."
    ),
    "completion.already_installed": (
        "snapz completion already appears to be installed in {path}"
    ),
    "completion.unknown_action": "unknown completion action: {action}",

    # ---- self-management ----
    "update.help": "update snapz from the latest GitHub .deb release",
    "update.source": "updating from",
    "update.done": "snapz updated",
    "update.failed": "update failed (installer exit code {code})",
    "update_check.notice": (
        "snapz {latest} is available (current {current}). Run `snapz update` "
        "to upgrade. Disable this check with "
        "`snapz config set update_check.enabled false`."
    ),
    "uninstall.help": "uninstall snapz and optionally delete local data",
    "uninstall.yes": "skip uninstall confirmation",
    "uninstall.purge_data": "with -y, delete snapshots and config without prompting",
    "uninstall.heading": "Uninstall snapz",
    "uninstall.delete_data": "delete all local snapshots and config data?",
    "uninstall.confirm_package": "uninstall the snapz command now?",
    "uninstall.data_missing": "data directory does not exist",
    "uninstall.data_deleted": "deleted data at {path}",
    "uninstall.done": "snapz uninstalled",
    "uninstall.failed": "uninstall failed (pip exit code {code})",
    "uninstall.refuse_delete_root": "refusing to delete unsafe data path: {path}",

    # ---- global --json ----
    "flag.json": (
        "emit machine-readable JSON to stdout instead of formatted text "
        "(suitable for piping into `jq`)"
    ),

    # ---- global --minimal ----
    "flag.minimal": "override ui_mode for this invocation: skip all TUI prompts and use plain text output",

    # ---- list/alist/rm: --all flag ----
    "flag.show_all": "include auto-* safety snapshots (hidden by default)",
    "status.hidden_auto": "  ({n} auto-* hidden — pass --all to include)",

    # ---- TUI / filter mode ----
    "tui.filter_prompt": "/",
    "tui.filter_status": "filter: {pattern}  ·  {n}/{total} match",
    "tui.filter_hint": "/  filter  ·  Esc clear",

    # ---- bare-mode dispatch error ----
    "bare.too_many_args": (
        "error: too many arguments. did you mean a subcommand? (got: {argv})"
    ),

    # ---- generic confirms / prompts / labels ----
    "prompt.snapshot_name": "snapshot name",
    "prompt.note_optional": "note (optional)",
    "prompt.create_snapshot": "create snapshot?",
    "prompt.proceed_restore": "proceed with restore?",
    "prompt.proceed_revert": "proceed with revert?",
    "prompt.delete_one": "delete {name} ({size})?",
    "prompt.delete_n": "delete {n} snapshot(s)?",
    "prompt.overwrite_choice": "  [o]verwrite / [r]ename / [a]bort",

    # confirm suffixes ([Y/n] / [y/N]) — kept as ASCII so tests are stable
    "confirm.default_yes": "[Y/n]",
    "confirm.default_no": "[y/N]",

    # status messages
    "status.aborted": "aborted.",
    "status.planning": "planning...",
    "status.empty_walk": (
        "nothing to pack — directory is empty after applying ignores."
    ),
    "status.empty_picker": "nothing to pack after picker — aborting.",
    "status.nothing_to_prune": "nothing to prune.",
    "status.nothing_to_reclaim": "nothing to reclaim",
    "status.no_changes": "  (no changes)",
    "status.no_snapshots_dir": "(no snapshots in this directory)",
    "status.no_existing_snapshots": "(no existing snapshots in this directory)",
    "status.no_snapshots_yet": "  (no snapshots yet)",
    "status.no_snapshots_anywhere": "(no snapshots anywhere)",
    "status.no_snapshots_only_empty": (
        "(no snapshots; only empty per-dir folders found)"
    ),
    "status.no_sources_recorded": "(no source directories recorded yet)",
    "status.dry_run_nothing": "  (dry-run; nothing deleted)",

    # KV / inline label words used inside f-strings
    "kv.note": "note",
    "kv.archive": "archive",
    "kv.archive_size": "archive size",
    "kv.size": "new storage",
    "kv.full_size": "full size",
    "kv.files": "files",
    "kv.total_size": "total size",
    "kv.ignored": "ignored",
    "kv.large_skip": "large skip",
    "kv.to_overwrite": "to overwrite",
    "kv.to_add": "to add",
    "kv.extras": "extras",
    "kv.pre_backup": "pre-backup",
    "kv.extracted": "extracted",
    "kv.cleaned": "cleaned",
    "kv.roll_back": "roll-back",
    "kv.time": "time",
    "kv.source": "source",
    "kv.created": "created",
    "kv.paths": "paths",
    "kv.skipped": "skipped",
    "kv.keep": "keep",
    "kv.drop": "drop",
    "kv.gc": "gc",
    "kv.clean": "clean",
    "kv.blobs": "blobs",
    "kv.key": "key",
    "kv.package": "package",
    "kv.data_root": "data root",
    "kv.data_size": "data size",
    "kv.state": "state",
    "kv.overwritten": "overwritten",

    # restore / revert detail wording
    "restore.will_clean": "(will be deleted: --clean)",
    "restore.kept": "(kept; pass --clean to delete)",
    "restore.pre_yes": "  (auto-pre-restore-* will be created)",
    "restore.pre_no": "  (--no-auto-save)",
    "revert.pre_yes": "  (auto-pre-revert-* will be created)",
    "revert.pre_no": "  (--no-auto-save)",
    "revert.clean_yes": "  (--delete-extras)",
    "label.yes": "yes",
    "label.no": "no",

    # success-line verbs / suffixes
    "msg.saved": "saved",
    "msg.deleted_one": "deleted {name}",
    "msg.renamed": "renamed {old}",
    "msg.restored": "restored",
    "msg.exported": "exported",
    "msg.bundled": "bundled",
    "msg.imported": "imported",
    "msg.reverted_from": "reverted from {name}",
    "msg.added_patterns": (
        "added {n} pattern(s) to local excludes  ({path})"
    ),
    "msg.added_patterns_walk": (
        "added {n} pattern(s) to local excludes; re-walking..."
    ),
    "msg.diff_label": "diff:",
    "msg.exists_n_snapshots": "existing {n} {word}:",

    # imperative-form verbs (used in headings like "↩ restore foo → /tmp")
    "verb.restore_imp": "restore",
    "verb.revert_imp": "revert",
    "verb.prune_imp": "prune",

    # verbs used inline in prune / gc summaries
    "verb.would_delete": "would delete",
    "verb.deleted": "deleted",
    "verb.would_free": "would free",
    "verb.freed": "freed",
    "label.snapshots_n": "snapshot(s)",
    "label.entries_n": "entries",
    "label.entries_paren": "entry(s)",
    "label.files_written_suffix": " written",
    "label.extras_suffix": " extras",
    "label.blobs_paren": "({n} blob(s))",
    "label.scan_summary": "(scanned {n} dir(s))",
    "label.gc_summary": "across {blobs} unreferenced blob(s) in {dirs} dir(s)",
    "label.diff_counts": "{a} added  ·  {m} modified  ·  {d} deleted",
    "label.diff_live": "live",
    "label.totals": "  total: {disk} on disk  ·  {logical} logical",
    "label.dots_more": "... and {n} more",
    "label.no_rules": "no rules",
    "label.files_count": "{n} {word}",

    # save kv lines
    "save.large_over_cap": "{n} file(s) over cap",
    "save.large_hint": "  (use --include-large to keep them)",
    "save.uncompressed_meta": "({size} uncompressed, {n} entries)",
    "save.exists_hint": "  (use --overwrite or pick another name)",
    "save.exists_warn": "a snapshot named {name} already exists.",

    # diff plain-text body
    "msg.no_directory": "not a directory: {path}",
    "msg.no_snapshot_named": "no snapshot named {name} under {path}",

    # config
    "config.set_marker": "(set)",
    "config.default_marker": "(default)",
    "config.requires_key": "{op} requires a key argument",
    "config.set_requires_value": "set requires a value",
    "config.unset_default": "-> default:",
    "config.was_not_set": "{key} was not set",
    "config.unknown_op": "unknown config op: {op}",
    "config.known_keys": "known keys: {keys}",

    # picker / TUI confirm
    "picker.no_paths_given": (
        "no paths given. pass paths explicitly or run interactively "
        "for the picker."
    ),
    "picker.no_name_given": (
        "no snapshot name given. pass it as an argument or run "
        "interactively to pick from the list."
    ),
    "picker.no_snapshots_in": "no snapshots in {path}",
    "picker.cancelled": "cancelled.",
    "picker.snapshot_footer": (
        "↑↓/jk move  ·  ⏎/space select  ·  q/Esc cancel"
    ),
    "picker.live_row": "[live]",
    "picker.live_hint": "current working tree",

    # snapshot picker titles per command
    "picker.title_rm": "select snapshot to delete in {path}",
    "picker.title_show": "select snapshot to show in {path}",
    "picker.title_restore": "select snapshot to restore into {path}",
    "picker.title_export": "select snapshot to export from {path}",
    "picker.title_revert": "select snapshot to revert from in {path}",
    "picker.title_cat": "select snapshot to cat from in {path}",
    "picker.title_browse": "select snapshot to browse in {path}",
    "picker.title_diff_a": "select snapshot A in {path}",
    "picker.title_diff_b": "select snapshot B in {path}  (or [live])",
    "picker.title_mv_old": "select snapshot to rename in {path}",
    "picker.prompt_mv_new": "rename {old} to:",

    # diff TUI footers / unified-diff sub-view
    "diff.list_footer": (
        "↑↓ move  ·  ⏎ open file diff  ·  space file  ·  d parent dir  ·  "
        "a all  ·  n none  ·  e apply ({n})  ·  q quit"
    ),
    "diff.unified_title": "{a} → {b}  ·  {path}",
    "diff.unified_footer": (
        "↑↓/jk scroll  ·  PgUp/PgDn page  ·  g/G top/bottom  ·  q/⏎ back"
    ),
    "diff.placeholder_binary": "(binary file, {size})",
    "diff.placeholder_too_large": "(file too large to render: {size})",
    "diff.placeholder_text": "(no content, {size})",
    "diff.identical": "(no textual differences)",

    # revert path picker
    "revert.picker_title": "select paths to revert  ·  {n} entries  ·  {src}",
    "revert.picker_footer": (
        "↑↓ move  ·  ⏎/→ open dir  ·  ← up  ·  space toggle  ·  "
        "a all  ·  c diffs  ·  n none  ·  e apply ({n})  ·  q quit"
    ),

    # generic warn glyph for inline messages
    "warn.bang": "!",

    # column headers (text mode)
    "header.NAME": "NAME",
    "header.CREATED": "CREATED",
    "header.SIZE": "NEW",
    "header.FILES": "FILES",
    "header.NOTE": "NOTE",
    "header.DIR": "DIR",
    "header.SNAPS": "SNAPS",
    "header.ON_DISK": "ON DISK",
    "header.LOGICAL": "LOGICAL",
    "header.DEDUP": "DEDUP",
    "header.NEWEST": "NEWEST",
}

_ZH: dict[str, str] = {
    # ---- root parser & global flags ----
    "root.description": "轻量的目录快照工具。",
    "flag.version": "显示程序版本并退出",
    "flag.no_zstd": "即使可用 zstandard，也强制使用 gzip 压缩",
    "argparse.usage": "用法: ",
    "argparse.positionals": "位置参数",
    "argparse.options": "选项",
    "argparse.help": "显示这条帮助信息并退出",
    "argparse.error_format": "%(prog)s: 错误: %(message)s\n",
    "argparse.unrecognized": "无法识别的参数: %s",
    "argparse.required": "缺少必需参数: %s",
    "argparse.invalid_choice": "无效选择: %(value)r（可选: %(choices)s）",
    "argparse.expected_one": "需要一个参数",
    "argparse.expected_at_least_one": "至少需要一个参数",
    "argparse.expected_at_most_one": "最多需要一个参数",
    "argparse.argument_message": "参数 %(argument_name)s: %(message)s",
    "argparse.one_required": "以下参数至少需要一个: %s",
    "argparse.not_allowed": "不能与参数 %s 同时使用",

    # ---- save (scripted) ----
    "save.help": "非交互式创建快照",
    "save.yes": "跳过确认",
    "save.message": "附加在快照上的简短备注",
    "save.no_cache": "本次保存不使用源目录文件哈希缓存",
    "save.workers": "写入 blob 时使用的工作线程数",
    "save.workers_positive": "--workers 必须至少为 1",
    "source_config.heading": "源目录快照配置",
    "source_config.body": (
        "snapz 默认排除生成文件。输入 preset 名称可为该源目录加回来，"
        "直接回车则保持默认。"
    ),
    "source_config.include_prompt": "加回的 presets",
    "source_config.added": "已为该源目录添加 {n} 条加回规则",
    "suggest.heading": "建议加入本地排除",
    "suggest.summary": "这些内容约占本次计划快照的 {pct}%。",
    "suggest.confirm": "把这些模式加入该源目录的本地排除?",
    "suggest.added": "已添加 {n} 条本地排除;重新规划...",
    "suggest.none_added": "建议的模式都已经被排除了",

    # ---- list / alist ----
    "list.help": "列出当前目录的快照",
    "list.text": "强制纯文本输出，不使用 curses TUI",
    "list.timeline": "按日期分组显示文本输出",
    "alist.help": "跨所有目录列出快照",

    # ---- web ----
    "web.help": "启动本机 snapz 客户端 Web 界面",
    "web.host": "绑定主机（默认: 127.0.0.1）",
    "web.port": "绑定端口（默认: 3000；0 表示随机空闲端口）",
    "web.allow_remote": "允许绑定到非本机回环地址",
    "web.invalid_port": "--port 必须在 0 到 65535 之间",
    "web.refuse_remote": (
        "拒绝绑定 {host}；如果确实要让其他机器访问本地快照 API，"
        "请传入 --allow-remote"
    ),
    "web.started": "snapz 客户端 Web 界面: {url}",
    "web.stop_hint": "按 Ctrl+C 停止。",

    # ---- rm / mv / show ----
    "rm.help": "删除一个快照",
    "rm.path": "目标目录（默认:当前目录）",
    "mv.help": "重命名快照",
    "mv.path": "目标目录（默认:当前目录）",
    "show.help": "打印快照元数据",
    "show.path": "目标目录（默认:当前目录）",

    # ---- restore ----
    "restore.help": "把快照还原回源目录",
    "restore.path": "目标目录（默认:当前目录）",
    "restore.yes": "跳过确认提示",
    "restore.no_auto_save": "在解包前不自动创建预还原快照",
    "restore.clean": "删除工作目录中归档里没有的文件",
    "restore.preview_overwrite": "将覆盖",
    "restore.preview_add": "将新增",
    "restore.preview_clean": "--clean 将删除",
    "restore.preview_keep": "保留的多余文件",
    "restore.more_paths": "... 以及另外 {n} 个",

    # ---- export ----
    "export.help": "把快照解到任意目标目录",
    "export.name": "快照名称",
    "export.dst": "目标目录（不存在会自动创建）",
    "export.path": "持有该快照的源目录（默认:当前目录）",
    "export.overwrite": "即使目标目录非空也强制解出",
    "export.yes": "为脚本兼容而接受；export 不会提示确认",

    # ---- portable bundles ----
    "bundle.help": "把某个源目录的所有快照打成可迁移 bundle",
    "bundle.source": "源目录路径（配合 --archive 时为归档 key）",
    "bundle.dst": "要创建的 bundle 文件，如 project.snapz",
    "bundle.archive": "把 source 当作归档源 key",
    "bundle.overwrite": "覆盖已存在的 bundle 文件",
    "import.help": "从 snapz bundle 导入快照",
    "import.bundle": "`snapz bundle` 生成的 bundle 文件",
    "import.path": "把导入快照绑定到这个已存在的源目录（省略则保持归档态）",
    "import.overwrite": "覆盖目标存储里同名快照",

    # ---- remote login/logout/sync ----
    "login.help": "登录 snapz-server 远端",
    "login.server": "服务器 URL，例如 http://127.0.0.1:8765",
    "login.tenant": "租户名",
    "login.username": "用户名",
    "login.password": "密码（省略时提示输入）",
    "login.device": "记录到服务器的设备名",
    "login.tls_ca": "用于验证 HTTPS 服务器证书的 CA bundle",
    "login.tls_client_cert": "mTLS 客户端 PEM 证书",
    "login.tls_client_key": "mTLS 客户端 PEM 私钥",
    "logout.help": "移除已保存的远端 token",
    "push.help": "把快照推送到已配置的远端",
    "push.scope": "推送所有本地源",
    "pull.help": "从已配置的远端拉取快照",
    "pull.scope": "拉取所有远端源",
    "adopt.help": "把归档源（如拉取的远端归档）绑定到路径",
    "adopt.archive_key": "归档源 key",
    "adopt.path": "目标源目录",

    # ---- diff ----
    "diff.help": "显示两个快照（或快照与当前目录）之间的文件差异",
    "diff.a": "起点快照",
    "diff.b": "终点快照（省略则与当前目录对比）",
    "diff.path": "持有这些快照的源目录（默认:当前目录）",
    "diff.text": "强制纯文本输出，不使用 curses TUI",
    "diff.tui": "打开 curses 选择器（勾选要加入本地排除的文件/目录）",

    # ---- config ----
    "config.help": "读写持久化用户偏好（如 `save_picker`）",
    "config.op": "操作",
    "config.key": "配置键（list 时可省略）",
    "config.value": "set 时要写入的值",

    # ---- gc ----
    "gc.help": "回收已无任何快照引用的 blob 占用的磁盘空间",
    "gc.path": "目标目录（默认:当前目录）",
    "gc.all": "对存储根下所有目录执行垃圾回收",
    "gc.dry_run": "只报告会回收什么，不实际删除",
    "gc.rebuild_index": "回收前重建全局 blob 引用索引",

    # ---- maintenance / source identity ----
    "check.help": "校验存储元数据和 blob 可达性",
    "check.path": "源目录（默认:当前目录）",
    "check.all": "校验存储根下所有源目录",
    "check.deep": "同时校验 blob 内容",
    "check.fix": "尽可能修复安全的元数据问题",
    "migrate.help": "把旧版按目录存放的 blob 迁移到 v3 全局 CAS 池",
    "migrate.path": "源目录（默认:当前目录）",
    "migrate.all": "迁移存储根下所有源目录",
    "migrate.to": "目标存储布局版本",
    "migrate.dry_run": "只报告会迁移什么，不修改数据",
    "init.help": "写入 .snapz-id 标记，用于可靠识别目录移动",
    "init.path": "源目录（默认:当前目录）",
    "init.force": "替换已存在的 .snapz-id 标记",
    "relocate.help": "把旧源路径的快照移动绑定到改名后的现存目录",
    "relocate.paths": "手动迁移时为 OLD NEW；配合 --auto 时为 ROOT...",
    "relocate.auto": "扫描根目录，定位已移动的归档源并迁移精确匹配",
    "relocate.dry_run": "只显示自动匹配，不移动存储绑定",
    "relocate.yes": "无需提示，直接应用自动迁移",
    "archive.help": "列出归档源，并恢复归档快照",
    "archive.list_help": "列出归档源",
    "archive.restore_help": "把归档快照恢复到目标路径",
    "archive.archive_arg": "归档 key 或原始源路径",
    "archive.snapshot": "快照名称",
    "archive.dst": "目标路径",
    "archive.overwrite": "即使目标目录非空也强制解出",
    "protect.help": "把快照标记为受保护",
    "protect.path": "目标目录（默认:当前目录）",
    "unprotect.help": "移除快照保护",

    # ---- stats ----
    "stats.help": "按源目录展示存储用量与去重比",
    "stats.path": "源目录（默认:当前目录;用 --all 看全部）",
    "stats.all": "汇总所有已记录的源目录",
    "stats.text": "强制纯文本输出，不使用 curses TUI",

    # ---- prune ----
    "prune.help": "按保留策略删除多余快照",
    "prune.path": "源目录（默认:当前目录）",
    "prune.keep_last": "始终保留最新的 N 个快照",
    "prune.keep_within": "保留最近 DAYS 天内创建的全部快照",
    "prune.keep_daily": "最近 N 天每天保留一份（取当天最新）",
    "prune.keep_weekly": "最近 N 个 ISO 周每周保留一份",
    "prune.protect": "永不删除该快照（可重复）",
    "prune.yes": "跳过 curses 预览 / 确认",
    "prune.dry_run": "只报告会删除什么，不实际删除",
    "prune.no_gc": "删完后不顺手回收孤儿 blob",
    "prune.text": "强制纯文本方案，不使用 curses TUI",

    # ---- revert ----
    "revert.help": "把快照中选定的路径还原回源目录",
    "revert.name": "要回滚到的快照",
    "revert.paths": "相对源目录的文件 / 目录路径（省略则打开选择器）",
    "revert.path": "源目录（默认:当前目录）",
    "revert.yes": "跳过确认提示",
    "revert.no_auto_save": "在写入前不自动创建预回滚快照",
    "revert.delete_extras": "对每个所选路径，连带删除快照里没有的多余文件",
    "revert.text": "非交互模式（路径必须由命令行直接给出）",

    # ---- undo (v0.2) ----
    "undo.help": "回退最近一次还原/回滚（可一直回到最初）",
    "undo.path": "源目录（默认:当前目录）",
    "undo.yes": "跳过确认提示",
    "undo.no_clean": "保留兜底快照之后新增的文件（默认会清理到完全一致）",
    "undo.heading": "回退到 {name}",
    "undo.captured": "捕获于",
    "undo.remaining": "剩余可回退步数",
    "undo.no_target": "{path} 下没有可回退的内容（没有 auto-pre-* 兜底快照）。",
    "undo.confirm": "回退到这个状态?",
    "undo.success": "已回到 {when} 的状态",

    # ---- find (v0.2) ----
    "find.help": "列出包含指定路径 / 通配符的所有快照",
    "find.pattern": "字面路径、目录前缀，或 fnmatch 通配符（** 表示递归匹配）",
    "find.path": "源目录（默认:当前目录）",
    "find.all": "把 auto-* 兜底快照也纳入扫描",
    "find.text": "强制纯文本输出（默认在非 TTY 下生效）",
    "find.no_matches": "在 {path} 下没有匹配 {pattern} 的内容",
    "find.summary": "在 {scanned} 个 CAS 快照中匹配到 {paths} 个路径，共 {hits} 行",
    "find.changed_marker": "← 已变化",

    # ---- browse (view 模式路径选择器；cat/browse/future grep 共用) ----
    "cat.help": "打印快照中的单个文件",
    "cat.snapshot": "快照名称",
    "cat.relpath": "快照中的源目录相对路径",
    "cat.path": "源目录（默认:当前目录）",
    "cat.raw": "向 stdout 写出原始字节",
    "cat.binary_ok": "stdout 为管道时允许写出二进制字节",
    "cat.no_path_given": "未指定路径。请传入源目录相对路径。",
    "cat.no_such_path": "快照 {snap} 中没有路径 {path!r}",
    "cat.binary_placeholder": "[二进制 {size}]（用 --raw 或管道配合 --binary-ok 写出字节）",
    "cat.browse_footer": "↑↓ 移动  ·  ⏎/→ 进目录  ·  r/⏎ 选择文件  ·  ← 返回  ·  q 退出",
    "browse.help": "浏览快照中的路径",
    "browse.snapshot": "快照名称",
    "browse.path": "源目录（默认:当前目录）",
    "browse.filter": "初始子串过滤条件",
    "browse.title": "浏览 {name}",
    "browse.footer": "↑↓ 移动  ·  Enter 打开/下钻  ·  r 返回路径  ·  h 返回  ·  q 退出",

    # ---- tag (用户自定义标签) ----
    "tag.help": "管理快照的用户标签（add / rm / list）",
    "tag.add_help": "给快照添加若干标签",
    "tag.rm_help": "移除快照上的若干标签",
    "tag.list_help": "按标签列出携带该标签的所有快照",
    "tag.snapshot": "要操作的快照名",
    "tag.values": "一个或多个标签（空格分隔）",
    "tag.path": "源目录（默认:当前目录）",
    "tag.missing_name": "错误:必须提供快照名",
    "tag.missing_tags": "错误:至少需要一个标签",
    "tag.added": "已添加标签",
    "tag.removed": "已移除标签",
    "tag.empty": "尚无任何快照打了标签",

    # ---- prune.keep_tag ----
    "prune.keep_tag": "保留携带 TAG 的快照（可重复）",

    # ---- log (操作历史) ----
    "log.help": "展示某个源（或 --all 全部）上的操作历史",
    "log.path": "源目录（默认:当前目录）",
    "log.all": "读取存储根下所有源的事件",
    "log.limit": "仅保留最近的 N 条事件（默认:全部）",
    "log.kind": "用逗号分隔的事件类型白名单（save,restore,revert,...）",
    "log.empty": "没有任何事件记录",

    # ---- shell completion ----
    "completion.help": "生成或安装 bash/zsh 的 shell 补全",
    "completion.action": "要输出的 shell，或 install",
    "completion.shell": "install 时要安装到的 shell",
    "completion.rcfile": "要追加写入的 shell 启动文件",
    "completion.argcomplete_missing": (
        "shell 补全需要 argcomplete。请运行 "
        "`pip install snapz-cli[completion]` 或 `pip install argcomplete`。"
    ),
    "completion.unsupported_shell": "不支持的 shell: {shell}",
    "completion.detect_failed": (
        "无法判断当前是 bash 还是 zsh。请传 `--shell bash` 或 `--shell zsh`。"
    ),
    "completion.installed": (
        "已把 snapz {shell} 补全安装到 {path}。重启 shell 或 source 该文件后生效。"
    ),
    "completion.already_installed": "{path} 中看起来已经安装过 snapz 补全",
    "completion.unknown_action": "未知的 completion 操作: {action}",

    # ---- self-management ----
    "update.help": "从 GitHub 最新 .deb 发行包更新 snapz",
    "update.source": "更新来源",
    "update.done": "snapz 已更新",
    "update.failed": "更新失败（安装器退出码 {code}）",
    "update_check.notice": (
        "snapz {latest} 可更新（当前 {current}）。运行 `snapz update` 更新；"
        "可用 `snapz config set update_check.enabled false` 关闭检查。"
    ),
    "uninstall.help": "卸载 snapz，并可选择删除本地数据",
    "uninstall.yes": "跳过卸载确认",
    "uninstall.purge_data": "配合 -y 使用，不提示直接删除快照和配置",
    "uninstall.heading": "卸载 snapz",
    "uninstall.delete_data": "删除所有本地快照和配置数据?",
    "uninstall.confirm_package": "现在卸载 snapz 命令?",
    "uninstall.data_missing": "数据目录不存在",
    "uninstall.data_deleted": "已删除数据目录 {path}",
    "uninstall.done": "snapz 已卸载",
    "uninstall.failed": "卸载失败（pip 退出码 {code}）",
    "uninstall.refuse_delete_root": "拒绝删除不安全的数据路径:{path}",

    # ---- global --json ----
    "flag.json": "输出机器可读的 JSON 到 stdout（便于 jq 等工具处理）",

    # ---- global --minimal ----
    "flag.minimal": "覆盖本次调用的 ui_mode：跳过所有 TUI 提示，使用纯文本输出",

    # ---- list/alist/rm: --all flag ----
    "flag.show_all": "把 auto-* 兜底快照也列出（默认隐藏）",
    "status.hidden_auto": "  （已隐藏 {n} 个 auto-* —— 加 --all 可显示）",

    # ---- TUI / filter mode ----
    "tui.filter_prompt": "/",
    "tui.filter_status": "过滤:{pattern}  ·  匹配 {n}/{total}",
    "tui.filter_hint": "/  过滤  ·  Esc 清除",

    # ---- bare-mode dispatch error ----
    "bare.too_many_args": "错误:参数过多。是否想用某个子命令?（已收到:{argv}）",

    # ---- generic confirms / prompts / labels ----
    "prompt.snapshot_name": "快照名",
    "prompt.note_optional": "备注（可选）",
    "prompt.create_snapshot": "创建快照?",
    "prompt.proceed_restore": "继续执行还原?",
    "prompt.proceed_revert": "继续执行回滚?",
    "prompt.delete_one": "删除 {name} ({size})?",
    "prompt.delete_n": "删除 {n} 个快照?",
    "prompt.overwrite_choice": "  [o] 覆盖 / [r] 改名 / [a] 中止",

    "confirm.default_yes": "[Y/n]",
    "confirm.default_no": "[y/N]",

    # status messages
    "status.aborted": "已中止。",
    "status.planning": "规划中...",
    "status.empty_walk": "没有内容可打包 —— 应用忽略规则后目录为空。",
    "status.empty_picker": "选择器后已无可打包内容 —— 中止。",
    "status.nothing_to_prune": "没有可清理的快照。",
    "status.nothing_to_reclaim": "没有可回收的内容",
    "status.no_changes": "  （无差异）",
    "status.no_snapshots_dir": "（该目录还没有快照）",
    "status.no_existing_snapshots": "（该目录还没有快照）",
    "status.no_snapshots_yet": "  （还没有快照）",
    "status.no_snapshots_anywhere": "（任何目录都没有快照）",
    "status.no_snapshots_only_empty": "（还没有任何快照;只看到空的目录文件夹）",
    "status.no_sources_recorded": "（还没有任何已记录的源目录）",
    "status.dry_run_nothing": "  （dry-run;未删除任何内容）",

    # KV / inline label words used inside f-strings
    "kv.note": "备注",
    "kv.archive": "归档",
    "kv.archive_size": "归档大小",
    "kv.size": "新增占用",
    "kv.full_size": "完整大小",
    "kv.files": "文件数",
    "kv.total_size": "总大小",
    "kv.ignored": "已忽略",
    "kv.large_skip": "大文件跳过",
    "kv.to_overwrite": "将覆盖",
    "kv.to_add": "将新增",
    "kv.extras": "多余项",
    "kv.pre_backup": "兜底快照",
    "kv.extracted": "已解出",
    "kv.cleaned": "已清理",
    "kv.roll_back": "可回退到",
    "kv.time": "耗时",
    "kv.source": "来源",
    "kv.created": "创建于",
    "kv.paths": "路径数",
    "kv.skipped": "已跳过",
    "kv.keep": "保留",
    "kv.drop": "丢弃",
    "kv.gc": "回收",
    "kv.clean": "清理",
    "kv.blobs": "blob 数",
    "kv.key": "key",
    "kv.package": "包名",
    "kv.data_root": "数据目录",
    "kv.data_size": "数据大小",
    "kv.state": "状态",
    "kv.overwritten": "已覆盖",

    # restore / revert detail wording
    "restore.will_clean": "（将被删除:--clean）",
    "restore.kept": "（保留;加 --clean 才删）",
    "restore.pre_yes": "  （会创建 auto-pre-restore-*）",
    "restore.pre_no": "  （--no-auto-save）",
    "revert.pre_yes": "  （会创建 auto-pre-revert-*）",
    "revert.pre_no": "  （--no-auto-save）",
    "revert.clean_yes": "  （--delete-extras）",
    "label.yes": "是",
    "label.no": "否",

    # success-line verbs / suffixes
    "msg.saved": "已保存",
    "msg.deleted_one": "已删除 {name}",
    "msg.renamed": "已重命名 {old}",
    "msg.restored": "已还原",
    "msg.exported": "已导出",
    "msg.bundled": "已打包",
    "msg.imported": "已导入",
    "msg.reverted_from": "已从 {name} 回滚",
    "msg.added_patterns": "已向本地排除追加 {n} 条规则  （{path}）",
    "msg.added_patterns_walk": "已向本地排除追加 {n} 条规则;重新扫描中...",
    "msg.diff_label": "差异:",
    "msg.exists_n_snapshots": "已有 {n} 个快照:",

    # imperative-form verbs (used in headings like "↩ 还原 foo → /tmp")
    "verb.restore_imp": "还原",
    "verb.revert_imp": "回滚",
    "verb.prune_imp": "清理",

    # verbs used inline in prune / gc summaries
    "verb.would_delete": "将删除",
    "verb.deleted": "已删除",
    "verb.would_free": "将释放",
    "verb.freed": "已释放",
    "label.snapshots_n": "个快照",
    "label.entries_n": "条目",
    "label.entries_paren": "项",
    "label.files_written_suffix": " 已写入",
    "label.extras_suffix": " 个多余项",
    "label.blobs_paren": "（{n} 个 blob）",
    "label.scan_summary": "（已扫描 {n} 个目录）",
    "label.gc_summary": "覆盖 {blobs} 个孤儿 blob，跨 {dirs} 个目录",
    "label.diff_counts": "新增 {a}  ·  修改 {m}  ·  删除 {d}",
    "label.diff_live": "当前目录",
    "label.totals": "  合计:磁盘 {disk}  ·  逻辑 {logical}",
    "label.dots_more": "... 还有 {n} 项",
    "label.no_rules": "无规则",
    "label.files_count": "{n} 个文件",

    # save kv lines
    "save.large_over_cap": "{n} 个文件超过上限",
    "save.large_hint": "  （加 --include-large 可一并包含）",
    "save.uncompressed_meta": "（解压后 {size}, {n} 条目）",
    "save.exists_hint": "  （加 --overwrite，或换个名字）",
    "save.exists_warn": "已存在名为 {name} 的快照。",

    # error messages
    "msg.no_directory": "不是目录:{path}",
    "msg.no_snapshot_named": "在 {path} 下找不到名为 {name} 的快照",

    # config
    "config.set_marker": "（已设置）",
    "config.default_marker": "（默认值）",
    "config.requires_key": "{op} 需要一个键名参数",
    "config.set_requires_value": "set 需要一个值",
    "config.unset_default": "-> 默认值:",
    "config.was_not_set": "{key} 本来就没设置",
    "config.unknown_op": "未知的 config 操作:{op}",
    "config.known_keys": "支持的键:{keys}",

    # picker / TUI confirm
    "picker.no_paths_given": "未指定路径。请直接传入路径，或在交互终端里使用选择器。",
    "picker.no_name_given": "未指定快照名。请把快照名当参数传入，或在交互终端里从列表挑选。",
    "picker.no_snapshots_in": "{path} 下没有快照",
    "picker.cancelled": "已取消。",
    "picker.snapshot_footer": "↑↓/jk 移动  ·  ⏎/空格 选中  ·  q/Esc 取消",
    "picker.live_row": "[当前目录]",
    "picker.live_hint": "工作区当前状态",

    # snapshot picker titles per command
    "picker.title_rm": "选择要删除的快照（{path}）",
    "picker.title_show": "选择要查看的快照（{path}）",
    "picker.title_restore": "选择要还原到 {path} 的快照",
    "picker.title_export": "选择要从 {path} 导出的快照",
    "picker.title_revert": "选择要从 {path} 回滚的快照",
    "picker.title_cat": "选择要从 {path} 读取文件的快照",
    "picker.title_browse": "选择要浏览的快照（{path}）",
    "picker.title_diff_a": "选择快照 A（{path}）",
    "picker.title_diff_b": "选择快照 B（{path}） 或 [当前目录]",
    "picker.title_mv_old": "选择要改名的快照（{path}）",
    "picker.prompt_mv_new": "把 {old} 改名为:",

    # diff TUI footers / unified-diff sub-view
    "diff.list_footer": (
        "↑↓ 移动  ·  ⏎ 打开文件 diff  ·  空格 勾选  ·  d 勾父目录  ·  "
        "a 全选  ·  n 清空  ·  e 应用 ({n})  ·  q 退出"
    ),
    "diff.unified_title": "{a} → {b}  ·  {path}",
    "diff.unified_footer": (
        "↑↓/jk 滚动  ·  PgUp/PgDn 翻页  ·  g/G 顶/底  ·  q/⏎ 返回"
    ),
    "diff.placeholder_binary": "（二进制文件，{size}）",
    "diff.placeholder_too_large": "（文件过大，无法渲染：{size}）",
    "diff.placeholder_text": "（无内容，{size}）",
    "diff.identical": "（无文本差异）",

    # revert path picker
    "revert.picker_title": "勾选要回滚的路径  ·  共 {n} 条  ·  {src}",
    "revert.picker_footer": (
        "↑↓ 移动  ·  ⏎/→ 进目录  ·  ← 返回  ·  空格 勾选  ·  "
        "a 全选  ·  c 仅勾差异  ·  n 清空  ·  e 应用 ({n})  ·  q 退出"
    ),

    # generic warn glyph for inline messages
    "warn.bang": "!",

    # column headers (text mode) — keep ASCII so width math doesn't get confused
    "header.NAME": "名称",
    "header.CREATED": "创建于",
    "header.SIZE": "新增",
    "header.FILES": "文件数",
    "header.NOTE": "备注",
    "header.DIR": "目录",
    "header.SNAPS": "快照数",
    "header.ON_DISK": "磁盘占用",
    "header.LOGICAL": "逻辑大小",
    "header.DEDUP": "去重比",
    "header.NEWEST": "最新",
}

STRINGS: dict[str, dict[str, str]] = {"en": _EN, "zh": _ZH}


def t(key: str, /, **kwargs) -> str:
    """Look up *key* in the active language, fall back to English, then
    to the literal key. ``kwargs`` are passed through ``str.format``.

    Missing translations never raise — the worst case is a
    pass-through that's clearly identifiable in logs.
    """

    lang = get_lang()
    template = STRINGS.get(lang, _EN).get(key)
    if template is None:
        template = _EN.get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template
    return template
