"""Statistics and retention pruning commands."""

from __future__ import annotations

from snapz._cli_common import *
def _print_stats_table(entries) -> None:
    if not entries:
        print(st.muted(t('status.no_sources_recorded')))
        return
    name_w = max(3, *(len(str(e.abspath)) for e in entries))
    header = (
        f"  {t('header.DIR').ljust(name_w)}  {t('header.SNAPS').rjust(5)}  "
        f"{t('header.ON_DISK').rjust(10)}  {t('header.LOGICAL').rjust(10)}  "
        f"{t('header.DEDUP').rjust(6)}  {t('header.NEWEST').ljust(16)}"
    )
    print(st.dim(header))
    total_disk = 0
    total_logical = 0
    for entry in entries:
        ratio = entry.dedup_ratio
        ratio_text = f"{ratio:.1f}\u00d7" if ratio else '\u2014'
        newest = format_iso(entry.newest) if entry.newest else '\u2014'
        print(
            f"  {st.path(str(entry.abspath).ljust(name_w))}  "
            f"{st.numeric(f'{entry.snapshot_count:,}'.rjust(5))}  "
            f"{st.numeric(format_size(entry.on_disk_bytes).rjust(10))}  "
            f"{st.numeric(format_size(entry.logical_bytes).rjust(10))}  "
            f"{st.numeric(ratio_text.rjust(6))}  "
            f"{st.muted(newest.ljust(16))}"
        )
        total_disk += entry.on_disk_bytes
        total_logical += entry.logical_bytes
    if len(entries) > 1:
        print(st.muted(t(
            'label.totals',
            disk=format_size(total_disk),
            logical=format_size(total_logical),
        )))

def cmd_stats(args: argparse.Namespace, config: RuntimeConfig) -> int:
    if args.path is None and not args.all:
        # Default to the current directory; users explicitly opt into a
        # global view with --all so a `cd` mistake doesn't surface
        # everyone's snapshots.
        path = "."
    else:
        path = args.path
    entries = (
        api.stats(config=config) if args.all
        else api.stats(path, config=config)
    )

    if _wants_json(args):
        _emit_json({
            "scope": "all" if args.all else "single",
            "entries": entries,
        })
        return EXIT_OK

    # ``stats`` has historically promised a curses view, but the dedicated
    # widget was never finished. Until it is, always render the text
    # table — it's already informative and avoids an AttributeError.
    _print_stats_table(entries)
    return EXIT_OK

def _print_prune_plan(plan) -> None:
    rules_bits = []
    for k in ("keep_last", "keep_within_days", "keep_daily", "keep_weekly"):
        v = plan.rules.get(k)
        if v:
            rules_bits.append(f"{k.replace('_', '-')}={v}")
    protect = plan.rules.get("protect") or []
    if isinstance(protect, list) and protect:
        rules_bits.append(f"protect={','.join(protect)}")
    print(
        f"\U0001f5d1  {t('verb.prune_imp')} {st.path(str(plan.abspath))}  "
        f"{st.muted('(' + (', '.join(rules_bits) or t('label.no_rules')) + ')')}"
    )
    print(_kv(t('kv.keep'), st.numeric(f'{len(plan.keep):,}')))
    print(_kv(t('kv.drop'), st.numeric(f'{len(plan.drop):,}')))
    if plan.drop:
        name_w = max(4, *(len(s.name) for s in plan.drop))
        print(st.dim(
            f"  {t('header.NAME').ljust(name_w)}  "
            f"{t('header.CREATED').ljust(16)}  {t('header.SIZE')}"
        ))
        for snapz in plan.drop:
            print(
                f"  {st.warn(snapz.name.ljust(name_w))}  "
                f"{st.muted(format_iso(snapz.created))}  "
                f"{st.numeric(format_size(snapz.size_bytes))}"
            )

def cmd_prune(args: argparse.Namespace, config: RuntimeConfig) -> int:
    path = resolve_path(args.path or ".")
    try:
        plan = api.plan_prune(
            path,
            keep_last=args.keep_last,
            keep_within_days=args.keep_within_days,
            keep_daily=args.keep_daily,
            keep_weekly=args.keep_weekly,
            keep_tag=args.keep_tag or (),
            protect=args.protect or (),
            config=config,
        )
    except ValueError as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    drop_names = [s.name for s in plan.drop]
    if not plan.keep and not plan.drop:
        if _wants_json(args):
            _emit_json({"plan": plan, "outcome": None})
            return EXIT_OK
        print(st.muted(t('status.no_snapshots_dir')))
        return EXIT_OK

    if _wants_json(args):
        if args.dry_run:
            _emit_json({"plan": plan, "outcome": None})
            return EXIT_OK
        if not args.yes:
            _emit_json({
                "pruned": False,
                "reason": "needs-confirmation",
                "plan": plan,
            })
            return EXIT_ERROR
        outcome = api.execute_prune(
            plan,
            drop_names=drop_names,
            run_gc=not args.no_gc,
            dry_run=False,
            config=config,
        )
        _emit_json({"pruned": True, "plan": plan, "outcome": outcome})
        return EXIT_OK

    use_tui = (
        not args.text
        and not args.dry_run
        and not args.yes
        and _stdout_is_tty()
    )
    if use_tui:
        from snapz import tui
        chosen = tui.run_prune_view(plan)
        if chosen is None:
            print(st.muted(t('status.aborted')))
            return EXIT_USER_ABORT
        drop_names = chosen
    else:
        _print_prune_plan(plan)
        if args.dry_run:
            print(st.muted(t('status.dry_run_nothing')))
            return EXIT_OK
        if not drop_names:
            print(st.muted(t('status.nothing_to_prune')))
            return EXIT_OK
        if not args.yes and not _confirm(
            t('prompt.delete_n', n=len(drop_names)),
            default_yes=False,
        ):
            return EXIT_USER_ABORT

    if not drop_names:
        print(st.muted(t('status.nothing_to_prune')))
        return EXIT_OK

    outcome = api.execute_prune(
        plan,
        drop_names=drop_names,
        run_gc=not args.no_gc,
        dry_run=args.dry_run,
        config=config,
    )
    verb = t('verb.would_delete') if outcome.dry_run else t('verb.deleted')
    print(
        f"{st.ok_mark()} {verb} {st.numeric(f'{len(outcome.deleted):,}')} "
        f"{t('label.snapshots_n')}"
    )
    if outcome.blobs_removed:
        gc_verb = t('verb.would_free') if outcome.dry_run else t('verb.freed')
        print(_kv(
            t('kv.gc'),
            f"{gc_verb} {st.numeric(format_size(outcome.bytes_freed))}  "
            f"{st.muted(t('label.blobs_paren', n=outcome.blobs_removed))}",
        ))
    return EXIT_OK

