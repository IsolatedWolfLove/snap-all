"""Snapshot retention planning and pruning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from snapz import events, preferences
from snapz.config import RuntimeConfig, default_config
from snapz.store import SnapshotMeta, Store
from snapz._api_core import _delete_snapshot_with_refs, _record_event, _source_path
from snapz._api_maintenance import gc


# ---------------------------------------------------------------------------
# Prune — retention policy over snapshots
# ---------------------------------------------------------------------------


@dataclass
class PrunePlan:
    """A keep/drop split produced by :func:`plan_prune`."""

    abspath: Path
    keep: list[SnapshotMeta]
    drop: list[SnapshotMeta]
    rules: dict[str, object]


@dataclass
class PruneOutcome:
    """Result of :func:`execute_prune`."""

    deleted: list[str]
    blobs_removed: int
    bytes_freed: int
    dry_run: bool


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _bucket_keys(dt: datetime) -> tuple[str, str]:
    """Return ``(day_key, week_key)`` for grouping in retention rules."""

    iso_year, iso_week, _ = dt.isocalendar()
    return dt.strftime("%Y-%m-%d"), f"{iso_year}-W{iso_week:02d}"


def plan_prune(
    path: str | Path,
    *,
    keep_last: Optional[int] = None,
    keep_within_days: Optional[int] = None,
    keep_daily: Optional[int] = None,
    keep_weekly: Optional[int] = None,
    keep_tag: Iterable[str] = (),
    protect: Iterable[str] = (),
    config: Optional[RuntimeConfig] = None,
) -> PrunePlan:
    """Compute which snapshots to keep / drop under the given rules.

    Rules are *unioned*: a snapshot is kept if **any** rule matches.
    Snapshots whose name appears in *protect* are always kept, as are
    snapshots carrying any tag listed in *keep_tag*. Pass at least one
    rule (and/or *protect* / *keep_tag*) — calling with no rules raises
    ``ValueError`` so users can't accidentally wipe everything.
    """

    config = config or default_config()
    root = Path(config.root)
    if keep_last is None:
        keep_last = int(preferences.get_config_value(root, "retention.keep_last") or 0) or None
    if keep_within_days is None:
        keep_within_days = (
            int(preferences.get_config_value(root, "retention.keep_within_days") or 0)
            or None
        )
    if keep_daily is None:
        keep_daily = int(preferences.get_config_value(root, "retention.keep_daily") or 0) or None
    if keep_weekly is None:
        keep_weekly = int(preferences.get_config_value(root, "retention.keep_weekly") or 0) or None

    explicit_protect = list(protect)
    keep_tag_set = {str(t).strip() for t in keep_tag if str(t).strip()}
    if (
        keep_last is None
        and keep_within_days is None
        and keep_daily is None
        and keep_weekly is None
        and not explicit_protect
        and not keep_tag_set
    ):
        raise ValueError(
            "plan_prune requires at least one retention rule "
            "(keep_last / keep_within_days / keep_daily / keep_weekly / "
            "keep_tag / protect)"
        )

    abspath = _source_path(path, config=config)
    store = Store(config)
    snaps = store.list_snapshots(abspath)

    def _snapshot_sort_key(meta: SnapshotMeta) -> tuple[str, int]:
        try:
            mtime = store.meta_path(abspath, meta.name).stat().st_mtime_ns
        except OSError:
            mtime = 0
        return (meta.created, mtime)

    snaps_sorted = sorted(snaps, key=_snapshot_sort_key, reverse=True)

    keep_names: set[str] = {s.name for s in snaps if s.protected}
    keep_names.update(name for name in explicit_protect if name)
    if keep_tag_set:
        for snap in snaps:
            if keep_tag_set & set(snap.tags):
                keep_names.add(snap.name)

    if keep_last is not None and keep_last > 0:
        for snapz in snaps_sorted[:keep_last]:
            keep_names.add(snapz.name)

    if keep_within_days is not None and keep_within_days > 0:
        cutoff = datetime.now() - timedelta(days=keep_within_days)
        for snapz in snaps_sorted:
            dt = _parse_iso(snapz.created)
            if dt is not None and dt >= cutoff:
                keep_names.add(snapz.name)

    if keep_daily is not None and keep_daily > 0:
        seen_days: dict[str, str] = {}  # day_key -> snapshot name (latest)
        for snapz in snaps_sorted:
            dt = _parse_iso(snapz.created)
            if dt is None:
                continue
            day, _ = _bucket_keys(dt)
            if day not in seen_days:
                seen_days[day] = snapz.name
        for name in list(seen_days.values())[:keep_daily]:
            keep_names.add(name)

    if keep_weekly is not None and keep_weekly > 0:
        seen_weeks: dict[str, str] = {}
        for snapz in snaps_sorted:
            dt = _parse_iso(snapz.created)
            if dt is None:
                continue
            _, week = _bucket_keys(dt)
            if week not in seen_weeks:
                seen_weeks[week] = snapz.name
        for name in list(seen_weeks.values())[:keep_weekly]:
            keep_names.add(name)

    keep = [s for s in snaps_sorted if s.name in keep_names]
    drop = [s for s in snaps_sorted if s.name not in keep_names]
    rules: dict[str, object] = {
        "keep_last": keep_last,
        "keep_within_days": keep_within_days,
        "keep_daily": keep_daily,
        "keep_weekly": keep_weekly,
        "keep_tag": sorted(keep_tag_set),
        "protect": sorted(keep_names),
    }
    return PrunePlan(abspath=abspath, keep=keep, drop=drop, rules=rules)


def execute_prune(
    plan: PrunePlan,
    *,
    drop_names: Optional[Iterable[str]] = None,
    run_gc: bool = True,
    dry_run: bool = False,
    config: Optional[RuntimeConfig] = None,
) -> PruneOutcome:
    """Apply *plan*. Pass *drop_names* to override the plan's drop list
    (useful for the TUI which lets users toggle individual rows)."""

    config = config or default_config()
    store = Store(config)
    names = (
        list(drop_names)
        if drop_names is not None
        else [s.name for s in plan.drop]
    )
    protected_names = {
        s.name for s in store.list_snapshots(plan.abspath) if s.protected
    }
    names = [name for name in names if name not in protected_names]

    deleted: list[str] = []
    if not dry_run:
        for name in names:
            if _delete_snapshot_with_refs(store, plan.abspath, name):
                deleted.append(name)
    else:
        deleted = list(names)

    blobs_removed = 0
    bytes_freed = 0
    if run_gc and deleted:
        gc_res = gc(plan.abspath, dry_run=dry_run, config=config)
        blobs_removed = gc_res.blobs_removed
        bytes_freed = gc_res.bytes_freed
    if deleted and not dry_run:
        _record_event(
            store, plan.abspath, events.KIND_PRUNE,
            deleted=list(deleted),
            blobs_removed=blobs_removed,
            bytes_freed=bytes_freed,
        )
    return PruneOutcome(
        deleted=deleted,
        blobs_removed=blobs_removed,
        bytes_freed=bytes_freed,
        dry_run=dry_run,
    )


def _auto_prune_after_save(abspath: Path, config: RuntimeConfig) -> None:
    try:
        enabled = preferences.get_config_value(
            Path(config.root), "retention.auto_prune_after_save"
        )
    except KeyError:
        return
    if not enabled:
        return
    try:
        plan = plan_prune(abspath, config=config)
    except ValueError:
        return
    execute_prune(plan, config=config)
