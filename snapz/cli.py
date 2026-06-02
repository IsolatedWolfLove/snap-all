# PYTHON_ARGCOMPLETE_OK
"""Command-line interface compatibility facade.

The command implementations are split by feature in ``snapz._cli_*`` modules.
This module keeps the original public import surface, including ``main`` and
private helpers used by tests and shell integrations.
"""

from __future__ import annotations

from typing import Optional

from snapz._cli_archive import _print_archive_table, _resolve_archive_entry, cmd_archive
from snapz._cli_bundle_remote import (
    _print_sync_outcome,
    cmd_adopt,
    cmd_bundle,
    cmd_import,
    cmd_login,
    cmd_logout,
    cmd_pull,
    cmd_push,
)
from snapz._cli_completion import cmd_completion
from snapz._cli_common import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USER_ABORT,
    SNAPZ_GITHUB_INSTALL_TARGET,
    SNAPZ_GITHUB_REPO,
    SNAPZ_PACKAGE_NAME,
    SUGGEST_EXCLUDE_DIRS,
    SUGGEST_EXCLUDE_SUFFIXES,
    _confirm,
    _delete_data_root,
    _emit_abort,
    _emit_json,
    _filter_user_visible,
    _format_data_size,
    _json_default,
    _kv,
    _looks_binary,
    _path_total_bytes,
    _pluralize,
    _print_error,
    _print_path_preview,
    _prompt,
    _resolve_snapshot_name,
    _run_pip,
    _stdout_is_tty,
    _wants_json,
)
from snapz._cli_config import _format_config_value, cmd_config
from snapz._cli_diff import _STATUS_FNS, _fmt_change_line, cmd_diff
from snapz._cli_list import (
    _print_alist_table,
    _print_snapshot_table,
    _print_snapshot_timeline,
    _timeline_bucket,
    cmd_alist,
    cmd_list,
)
from snapz._cli_log_self import _LOG_EXTRA_ORDER, _format_log_extras, _print_log_text, cmd_log, cmd_uninstall, cmd_update
from snapz._cli_maintenance import (
    _print_auto_relocate_outcome,
    _print_check_result,
    cmd_check,
    cmd_gc,
    cmd_init,
    cmd_migrate,
    cmd_relocate,
)
from snapz._cli_parser import _main_impl, _snapshot_name_completer, _tag_completer, build_parser
from snapz._cli_paths import (
    _print_find_text,
    _print_revert_outcome,
    cmd_browse,
    cmd_cat,
    cmd_find,
    cmd_revert,
    cmd_tag,
    cmd_undo,
)
from snapz._cli_save import (
    _ExcludeSuggestion,
    _Progress,
    _maybe_offer_exclude_suggestions,
    _maybe_run_first_source_config,
    _maybe_run_save_picker,
    _print_walk_summary,
    _suggest_excludes,
    cmd_save_interactive,
    cmd_save_scripted,
)
from snapz._cli_snapshot import _restore_with_confirmation, cmd_export, cmd_mv, cmd_protect, cmd_restore, cmd_rm, cmd_show


def main(argv: Optional[list[str]] = None) -> int:
    try:
        return _main_impl(argv)
    except KeyboardInterrupt:
        _emit_abort()
        return EXIT_USER_ABORT


__all__ = [name for name in globals() if not name.startswith("__")]


if __name__ == "__main__":
    raise SystemExit(main())
