"""Archived source commands."""

from __future__ import annotations

from snapz._cli_common import *
def _print_archive_table(entries) -> None:
    if not entries:
        print(st.muted("(no archived sources)"))
        return
    key_w = max(3, *(len(e.key) for e in entries))
    path_w = max(4, *(len(e.meta.abspath) for e in entries))
    print(st.dim(
        f"  {'KEY'.ljust(key_w)}  {'SNAPS'.rjust(5)}  "
        f"{'REASON'.ljust(16)}  {'SOURCE'.ljust(path_w)}"
    ))
    for entry in entries:
        print(
            f"  {st.muted(entry.key.ljust(key_w))}  "
            f"{st.numeric(str(len(entry.snapshots)).rjust(5))}  "
            f"{st.warn((entry.archive_reason or 'archived').ljust(16))}  "
            f"{st.path(entry.meta.abspath)}"
        )

def _resolve_archive_entry(
    raw: Optional[str], config: RuntimeConfig,
) -> Optional[api.DirEntry]:
    entries = api.list_archives(config=config)
    if raw:
        for entry in entries:
            if raw == entry.key or raw == entry.meta.abspath:
                return entry
        maybe_path = str(resolve_path(raw))
        for entry in entries:
            if maybe_path == entry.meta.abspath:
                return entry
        _print_error(f"no archived source matches: {raw}")
        return None
    if not _stdout_is_tty():
        _print_error("archive key/source is required in non-interactive mode")
        return None
    from snapz import tui
    key = tui.run_archive_picker(entries, title="Archived sources")
    if key is None:
        return None
    return next((entry for entry in entries if entry.key == key), None)

def cmd_archive(args: argparse.Namespace, config: RuntimeConfig) -> int:
    if args.archive_op == "list":
        entries = api.list_archives(config=config)
        if _wants_json(args):
            _emit_json({"archives": entries})
            return EXIT_OK
        _print_archive_table(entries)
        return EXIT_OK

    if args.archive_op != "restore":
        _print_error(f"unknown archive operation: {args.archive_op}")
        return EXIT_ERROR

    entry = _resolve_archive_entry(args.archive, config)
    if entry is None:
        return EXIT_USER_ABORT if _stdout_is_tty() else EXIT_ERROR

    name = args.name
    if not name:
        if not _stdout_is_tty():
            _print_error("snapshot name is required in non-interactive mode")
            return EXIT_ERROR
        from snapz import tui
        name = tui.run_snapshot_picker(
            entry.snapshots,
            title=f"Archived snapshots · {entry.meta.abspath}",
        )
        if not name:
            print(st.muted(t("picker.cancelled")))
            return EXIT_USER_ABORT

    dst = args.dst
    if not dst:
        if not _stdout_is_tty():
            _print_error("destination path is required in non-interactive mode")
            return EXIT_ERROR
        from snapz import tui
        dst = tui.prompt_text("restore archived snapshot to:", initial=entry.meta.abspath)
        if not dst:
            print(st.muted(t("picker.cancelled")))
            return EXIT_USER_ABORT

    try:
        outcome = api.restore_archive(
            entry.key,
            name,
            dst,
            config=config,
            overwrite=args.overwrite,
        )
    except (FileNotFoundError, NotADirectoryError, FileExistsError, ValueError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    if _wants_json(args):
        _emit_json({"archive": entry, "outcome": outcome})
        return EXIT_OK
    print(
        f"{st.ok_mark()} restored archived {st.name(outcome.snapshot.name)} "
        f"{st.arrow()} {st.path(str(outcome.destination))}"
    )
    print(_kv("source", st.muted(entry.meta.abspath)))
    print(_kv("extracted", st.numeric(str(outcome.extracted_count))))
    return EXIT_OK

