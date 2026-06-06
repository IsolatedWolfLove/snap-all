"""Store maintenance and relocation commands."""

from __future__ import annotations

from snapz._cli_common import *
def cmd_gc(args: argparse.Namespace, config: RuntimeConfig) -> int:
    try:
        if args.all:
            result = api.gc(
                all_dirs=True,
                dry_run=args.dry_run,
                rebuild_index=args.rebuild_index,
                config=config,
            )
        else:
            path = resolve_path(args.path or ".")
            result = api.gc(
                path,
                dry_run=args.dry_run,
                rebuild_index=args.rebuild_index,
                config=config,
            )
    except ValueError as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    if _wants_json(args):
        _emit_json(result)
        return EXIT_OK

    verb = t('verb.would_free') if args.dry_run else t('verb.freed')
    blobs = f"{result.blobs_removed:,}"
    suffix = st.muted(
        t('label.gc_summary', blobs=blobs, dirs=result.dirs_scanned)
    )
    if result.blobs_removed == 0:
        print(
            f"{st.ok_mark()} {t('status.nothing_to_reclaim')} "
            f"{st.muted(t('label.scan_summary', n=result.dirs_scanned))}"
        )
        return EXIT_OK
    print(
        f"{st.ok_mark()} {verb} "
        f"{st.numeric(format_size(result.bytes_freed))}  {suffix}"
    )
    return EXIT_OK

def _print_check_result(result) -> None:
    status = "ok" if result.ok else "issues found"
    scanned_text = f"({result.dirs_scanned} dir(s) scanned)"
    print(
        f"{st.ok_mark() if result.ok else st.warn('!')} "
        f"check {status}  {st.muted(scanned_text)}"
    )
    if result.fixed_count:
        print(_kv("fixed", st.numeric(str(result.fixed_count))))
    if not result.issues:
        return
    for issue in result.issues:
        color = st.error if issue.severity == "error" else st.warn
        fixed = f" {st.success('(fixed)')}" if issue.fixed else ""
        where = f"  {st.muted(issue.path)}" if issue.path else ""
        snap = f"  {st.name(issue.snapshot)}" if issue.snapshot else ""
        print(
            f"  {color(issue.severity.upper())} "
            f"{issue.code}{fixed}{snap}{where}"
        )
        print(f"    {issue.message}")

def cmd_check(args: argparse.Namespace, config: RuntimeConfig) -> int:
    path_arg = getattr(args, "path_option", None) or args.path
    try:
        result = api.check(
            None if args.all else resolve_path(path_arg or "."),
            all_dirs=args.all,
            deep=args.deep,
            fix=args.fix,
            config=config,
        )
    except ValueError as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(result)
        return EXIT_OK if result.ok else EXIT_ERROR
    _print_check_result(result)
    return EXIT_OK if result.ok else EXIT_ERROR

def cmd_migrate(args: argparse.Namespace, config: RuntimeConfig) -> int:
    path_arg = getattr(args, "path_option", None) or args.path
    try:
        outcome = api.migrate(
            None if args.all else resolve_path(path_arg or "."),
            all_dirs=args.all,
            to=args.to,
            dry_run=args.dry_run,
            config=config,
        )
    except ValueError as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(outcome)
        return EXIT_OK
    verb = "would migrate" if outcome.dry_run else "migrated"
    print(
        f"{st.ok_mark()} {verb} "
        f"{st.numeric(f'{outcome.blobs_migrated:,}')} blob(s)  "
        f"{st.muted(format_size(outcome.bytes_migrated))}"
    )
    if outcome.blobs_skipped:
        print(_kv("skipped", st.numeric(f"{outcome.blobs_skipped:,}")))
    print(_kv("dirs", st.numeric(str(outcome.dirs_scanned))))
    return EXIT_OK

def cmd_init(args: argparse.Namespace, config: RuntimeConfig) -> int:
    try:
        outcome = api.init_source(args.path or ".", config=config, force=args.force)
    except (NotADirectoryError, ValueError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(outcome)
        return EXIT_OK
    action = "created" if outcome.created else "exists"
    print(
        f"{st.ok_mark()} {action} {st.path(str(outcome.marker_path))}"
    )
    print(_kv("source", st.path(str(outcome.source))))
    print(_kv("id", st.muted(outcome.marker_id)))
    return EXIT_OK

def _print_auto_relocate_outcome(outcome) -> None:
    verb = "would relocate" if outcome.dry_run else "relocated"
    if not outcome.relocated and not outcome.skipped:
        print(st.muted("(no relocation candidates found)"))
        return
    if outcome.relocated:
        print(
            f"{st.ok_mark()} {verb} "
            f"{st.numeric(f'{len(outcome.relocated):,}')} source(s)"
        )
        for item in outcome.relocated:
            print(
                f"  {st.path(str(item.old_path))} "
                f"{st.arrow()} {st.path(str(item.new_path))}  "
                f"{st.muted('(' + item.method + ')')}"
            )
    if outcome.skipped:
        print(_kv("skipped", st.warn(f"{len(outcome.skipped):,}")))
        for item in outcome.skipped:
            print(
                f"  {st.warn(item.reason)}  "
                f"{st.path(str(item.old_path))}"
            )
            for cand in item.candidates[:3]:
                print(
                    f"    {st.muted('-')} {st.path(str(cand.new_path))}  "
                    f"{st.muted('(' + cand.method + ')')}"
                )

def cmd_relocate(args: argparse.Namespace, config: RuntimeConfig) -> int:
    if args.auto:
        roots = args.paths or ["."]
        if _wants_json(args):
            if not args.dry_run and not args.yes:
                try:
                    outcome = api.auto_relocate_sources(
                        roots, config=config, dry_run=True,
                    )
                except NotADirectoryError as exc:
                    _emit_json({"relocated": False, "reason": str(exc)})
                    return EXIT_ERROR
                _emit_json({
                    "relocated": False,
                    "reason": "needs-confirmation",
                    "outcome": outcome,
                })
                return EXIT_ERROR
            try:
                outcome = api.auto_relocate_sources(
                    roots, config=config, dry_run=args.dry_run,
                )
            except NotADirectoryError as exc:
                _emit_json({"relocated": False, "reason": str(exc)})
                return EXIT_ERROR
            _emit_json(outcome)
            return EXIT_OK

        try:
            plan = api.auto_relocate_sources(roots, config=config, dry_run=True)
        except NotADirectoryError as exc:
            _print_error(str(exc))
            return EXIT_ERROR
        _print_auto_relocate_outcome(plan)
        if args.dry_run or not plan.relocated:
            return EXIT_OK
        if not args.yes and not _confirm(
            f"relocate {len(plan.relocated)} source(s)?",
            default_yes=False,
        ):
            print(st.muted(t('status.aborted')))
            return EXIT_USER_ABORT
        outcome = api.auto_relocate_sources(roots, config=config, dry_run=False)
        _print_auto_relocate_outcome(outcome)
        return EXIT_OK

    if len(args.paths) != 2:
        _print_error("relocate requires OLD NEW, or use --auto ROOT...")
        return EXIT_ERROR
    old, new = args.paths
    try:
        entry = api.relocate_source(old, new, config=config)
    except (FileNotFoundError, NotADirectoryError, FileExistsError) as exc:
        _print_error(str(exc))
        return EXIT_ERROR
    if _wants_json(args):
        _emit_json(entry)
        return EXIT_OK
    print(
        f"{st.ok_mark()} relocated {st.path(old)} "
        f"{st.arrow()} {st.path(entry.meta.abspath)}"
    )
    print(_kv("key", st.muted(entry.key)))
    print(_kv("snapshots", st.numeric(str(len(entry.snapshots)))))
    return EXIT_OK
