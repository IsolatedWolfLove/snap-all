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
    "flag.no_zstd": "force gzip compression even if zstandard is available",

    # ---- save (scripted) ----
    "save.help": "non-interactive snapshot create",
    "save.yes": "skip confirmation",
    "save.message": "short human-readable note attached to the snapshot",

    # ---- list / alist ----
    "list.help": "list snapshots of the current directory",
    "list.text": "force plain-text output instead of the curses TUI",
    "alist.help": "list snapshots across all directories",

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

    # ---- export ----
    "export.help": "extract a snapshot into an arbitrary destination directory",
    "export.name": "snapshot name",
    "export.dst": "destination directory (will be created if missing)",
    "export.path": (
        "source directory whose store holds the snapshot (default: cwd)"
    ),
    "export.overwrite": "extract even if the destination is non-empty",

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

    # ---- global --json ----
    "flag.json": (
        "emit machine-readable JSON to stdout instead of formatted text "
        "(suitable for piping into `jq`)"
    ),

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
    "kv.size": "size",
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
        "↑↓ move  ·  space toggle  ·  a all  ·  c diffs  ·  n none  ·  "
        "⏎/e apply ({n})  ·  q quit"
    ),

    # generic warn glyph for inline messages
    "warn.bang": "!",

    # column headers (text mode)
    "header.NAME": "NAME",
    "header.CREATED": "CREATED",
    "header.SIZE": "SIZE",
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
    "flag.no_zstd": "即使可用 zstandard，也强制使用 gzip 压缩",

    # ---- save (scripted) ----
    "save.help": "非交互式创建快照",
    "save.yes": "跳过确认",
    "save.message": "附加在快照上的简短备注",

    # ---- list / alist ----
    "list.help": "列出当前目录的快照",
    "list.text": "强制纯文本输出，不使用 curses TUI",
    "alist.help": "跨所有目录列出快照",

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

    # ---- export ----
    "export.help": "把快照解到任意目标目录",
    "export.name": "快照名称",
    "export.dst": "目标目录（不存在会自动创建）",
    "export.path": "持有该快照的源目录（默认:当前目录）",
    "export.overwrite": "即使目标目录非空也强制解出",

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

    # ---- global --json ----
    "flag.json": "输出机器可读的 JSON 到 stdout（便于 jq 等工具处理）",

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
    "kv.size": "大小",
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
        "↑↓ 移动  ·  空格 勾选  ·  a 全选  ·  c 仅勾差异  ·  n 清空  ·  "
        "⏎/e 应用 ({n})  ·  q 退出"
    ),

    # generic warn glyph for inline messages
    "warn.bang": "!",

    # column headers (text mode) — keep ASCII so width math doesn't get confused
    "header.NAME": "名称",
    "header.CREATED": "创建于",
    "header.SIZE": "大小",
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
