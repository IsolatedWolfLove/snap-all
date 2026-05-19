"""Diff command."""

from __future__ import annotations

from snapz._cli_common import *
_STATUS_FNS = {
    "A": (st.green, "+"),
    "M": (st.yellow, "~"),
    "D": (st.red, "-"),
    "T": (st.yellow, "T"),
}

def _fmt_change_line(change, *, name_w: int) -> str:
    color_fn, sigil = _STATUS_FNS.get(change.status, (st.muted, change.status))
    if change.status == "A":
        suffix = f"+{format_size(change.size_b or 0)}"
    elif change.status == "D":
        suffix = f"-{format_size(change.size_a or 0)}"
    elif change.status == "T":
        suffix = f"{change.type_a or '?'} -> {change.type_b or '?'}"
    else:  # M
        suffix = f"{format_size(change.size_a or 0)} -> {format_size(change.size_b or 0)}"
    return (
        f"  {color_fn(f'{sigil} {change.status}')}  "
        f"{change.path.ljust(name_w)}  {st.muted(suffix)}"
    )

def cmd_diff(args: argparse.Namespace, config: RuntimeConfig) -> int:
    src = resolve_path(args.path or ".")

    a_name = _resolve_snapshot_name(
        src, args.a, config, title_key="picker.title_diff_a",
        show_auto=bool(getattr(args, "all", False)),
    )
    if a_name is None:
        return EXIT_USER_ABORT if _stdout_is_tty() else EXIT_ERROR

    # When the user gave only `snapz diff` (no args at all) we also offer
    # a B picker — including a synthetic [live] entry. When they passed
    # ``a`` explicitly but omitted ``b`` we keep the long-standing
    # contract: ``b is None`` means "compare A to live tree".
    b_name = args.b
    if not b_name and not args.a and _stdout_is_tty():
        snaps = api.list_snapshots(src, config=config)
        snaps = _filter_user_visible(
            snaps, show_auto=bool(getattr(args, "all", False)),
        )
        from snapz import tui
        chosen = tui.run_snapshot_picker(
            [s for s in snaps if s.name != a_name],
            title=t("picker.title_diff_b", path=str(src)),
            allow_live=True,
        )
        if chosen is None:
            print(st.muted(t("picker.cancelled")))
            return EXIT_USER_ABORT
        if chosen != tui.LIVE:
            b_name = chosen
        # else: keep b_name as None ⇒ live diff

    try:
        result = api.diff(src, a_name, b_name, config=config)
    except (FileNotFoundError, ValueError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    if _wants_json(args):
        _emit_json(result)
        return EXIT_OK

    # The diff TUI is the default when interactive; --text forces plain
    # output, and --tui still works as an explicit opt-in.
    use_tui = (not args.text) and _stdout_is_tty()
    if use_tui:
        from snapz import preferences, tui

        def _read_a(relpath: str) -> Optional[bytes]:
            return api.read_snapshot_bytes(src, a_name, relpath, config=config)

        def _read_b(relpath: str) -> Optional[bytes]:
            if b_name is None:
                return api.read_live_bytes(src, relpath)
            return api.read_snapshot_bytes(src, b_name, relpath, config=config)

        chosen = tui.run_diff_view(result, read_a=_read_a, read_b=_read_b)
        if chosen:
            added = api.add_local_excludes(src, chosen, config=config)
            if added:
                ex_path = preferences.local_excludes_path(
                    Store(config).dir_for(src)
                )
                print(
                    f"{st.ok_mark()} "
                    f"{t('msg.added_patterns', n=st.numeric(str(added)), path=st.muted(str(ex_path)))}"
                )
        return EXIT_OK

    # Plain-text mode (default for non-TTY or no --tui flag)
    label = (
        f"{st.name(result.a_meta.name)} {st.muted(st.arrow())} "
        f"{st.name(result.b_meta.name) if result.b_meta else st.muted(t('label.diff_live'))}"
    )
    print(f"{t('msg.diff_label')} {label}  {st.muted('(' + str(result.abspath) + ')')}")
    if not result.changes:
        print(st.muted(t('status.no_changes')))
        return EXIT_OK

    name_w = max(8, *(len(c.path) for c in result.changes))
    for change in result.changes:
        print(_fmt_change_line(change, name_w=name_w))
    counts = t(
        'label.diff_counts',
        a=len(result.added), m=len(result.modified), d=len(result.deleted),
    )
    print(st.muted(f"\n{counts}"))
    return EXIT_OK
