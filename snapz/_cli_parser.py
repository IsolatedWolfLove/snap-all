"""Argparse setup and CLI dispatch."""

from __future__ import annotations

from snapz._cli_common import *
from snapz._cli_archive import cmd_archive
from snapz._cli_bundle_remote import (
    cmd_adopt,
    cmd_bundle,
    cmd_import,
    cmd_login,
    cmd_logout,
    cmd_pull,
    cmd_push,
)
from snapz._cli_config import cmd_config
from snapz._cli_diff import cmd_diff
from snapz._cli_list import cmd_alist, cmd_list
from snapz._cli_log_self import cmd_log, cmd_uninstall, cmd_update
from snapz._cli_maintenance import cmd_check, cmd_gc, cmd_init, cmd_migrate, cmd_relocate
from snapz._cli_paths import cmd_browse, cmd_cat, cmd_find, cmd_revert, cmd_tag, cmd_undo
from snapz._cli_save import cmd_save_interactive, cmd_save_scripted
from snapz._cli_snapshot import cmd_export, cmd_mv, cmd_protect, cmd_restore, cmd_rm, cmd_show
from snapz._cli_stats_prune import cmd_prune, cmd_stats
from snapz import update_check


_ARGPARSE_I18N_KEYS = {
    "usage: ": "argparse.usage",
    "positional arguments": "argparse.positionals",
    "options": "argparse.options",
    "show this help message and exit": "argparse.help",
    "%(prog)s: error: %(message)s\n": "argparse.error_format",
    "unrecognized arguments: %s": "argparse.unrecognized",
    "the following arguments are required: %s": "argparse.required",
    "invalid choice: %(value)r (choose from %(choices)s)": "argparse.invalid_choice",
    "expected one argument": "argparse.expected_one",
    "expected at least one argument": "argparse.expected_at_least_one",
    "expected at most one argument": "argparse.expected_at_most_one",
    "argument %(argument_name)s: %(message)s": "argparse.argument_message",
    "one of the arguments %s is required": "argparse.one_required",
    "not allowed with argument %s": "argparse.not_allowed",
}


def _argparse_gettext(message: str) -> str:
    key = _ARGPARSE_I18N_KEYS.get(message)
    return t(key) if key is not None else message


def _install_argparse_i18n() -> None:
    argparse._ = _argparse_gettext  # type: ignore[attr-defined]  # noqa: SLF001


def _snapshot_name_completer(prefix, parsed_args, **kwargs):
    """argcomplete dynamic completer: list snapshot names for the target dir."""

    try:
        raw = getattr(parsed_args, "path", None) or "."
        snaps = api.list_snapshots(Path(raw))
        return [s.name for s in snaps if s.name.startswith(prefix)]
    except Exception:
        return []

def build_parser() -> argparse.ArgumentParser:
    _install_argparse_i18n()
    parser = argparse.ArgumentParser(
        prog="snapz",
        description=t("root.description"),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"snapz {__version__}",
        help=t("flag.version"),
    )
    parser.add_argument(
        "--no-zstd",
        action="store_true",
        help=t("flag.no_zstd"),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=t("flag.json"),
    )
    parser.add_argument(
        "--minimal",
        action="store_true",
        help=t("flag.minimal"),
    )

    sub = parser.add_subparsers(dest="command")

    # save (scripted)
    p_save = sub.add_parser("save", help=t("save.help"))
    p_save.add_argument("path")
    p_save.add_argument("-n", "--name")
    p_save.add_argument("-y", "--yes", action="store_true", help=t("save.yes"))
    p_save.add_argument("--overwrite", action="store_true")
    p_save.add_argument("--include-large", action="store_true")
    p_save.add_argument(
        "--no-cache",
        action="store_true",
        help=t("save.no_cache"),
    )
    p_save.add_argument(
        "--workers",
        type=int,
        metavar="N",
        help=t("save.workers"),
    )
    p_save.add_argument(
        "-m", "--message", default="", metavar="NOTE",
        help=t("save.message"),
    )
    p_save.set_defaults(func=cmd_save_scripted)

    # list (current dir)
    p_list = sub.add_parser("list", help=t("list.help"))
    p_list.add_argument("path", nargs="?")
    p_list.add_argument(
        "--text",
        action="store_true",
        help=t("list.text"),
    )
    p_list.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_list.add_argument(
        "--timeline",
        action="store_true",
        help=t("list.timeline"),
    )
    p_list.set_defaults(func=cmd_list)

    # alist (global)
    p_alist = sub.add_parser("alist", help=t("alist.help"))
    p_alist.add_argument(
        "--text",
        action="store_true",
        help=t("list.text"),
    )
    p_alist.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_alist.set_defaults(func=cmd_alist)

    # rm
    p_rm = sub.add_parser("rm", help=t("rm.help"))
    p_rm.add_argument("name", nargs="?").completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_rm.add_argument("--path", help=t("rm.path"))
    p_rm.add_argument("-y", "--yes", action="store_true")
    p_rm.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_rm.set_defaults(func=cmd_rm)

    # mv
    p_mv = sub.add_parser("mv", help=t("mv.help"))
    p_mv.add_argument("old", nargs="?").completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_mv.add_argument("new", nargs="?")
    p_mv.add_argument("--path", help=t("mv.path"))
    p_mv.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_mv.set_defaults(func=cmd_mv)

    # show
    p_show = sub.add_parser("show", help=t("show.help"))
    p_show.add_argument("name", nargs="?").completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_show.add_argument("--path", help=t("show.path"))
    p_show.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_show.set_defaults(func=cmd_show)

    # restore
    p_restore = sub.add_parser(
        "restore",
        help=t("restore.help"),
    )
    p_restore.add_argument("name", nargs="?").completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_restore.add_argument("--path", help=t("restore.path"))
    p_restore.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_restore.add_argument(
        "-y", "--yes", action="store_true", help=t("restore.yes")
    )
    p_restore.add_argument(
        "--no-auto-save",
        action="store_true",
        help=t("restore.no_auto_save"),
    )
    p_restore.add_argument(
        "--clean",
        action="store_true",
        help=t("restore.clean"),
    )
    p_restore.set_defaults(func=cmd_restore)

    # export
    p_export = sub.add_parser(
        "export",
        help=t("export.help"),
    )
    p_export.add_argument("name", nargs="?", help=t("export.name")).completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_export.add_argument("dst", help=t("export.dst"))
    p_export.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_export.add_argument(
        "--path",
        help=t("export.path"),
    )
    p_export.add_argument(
        "--overwrite",
        action="store_true",
        help=t("export.overwrite"),
    )
    p_export.set_defaults(func=cmd_export)

    # portable source bundle
    p_bundle = sub.add_parser(
        "bundle",
        help=t("bundle.help"),
    )
    p_bundle.add_argument("source", help=t("bundle.source"))
    p_bundle.add_argument("dst", help=t("bundle.dst"))
    p_bundle.add_argument(
        "--archive",
        action="store_true",
        help=t("bundle.archive"),
    )
    p_bundle.add_argument(
        "--overwrite",
        action="store_true",
        help=t("bundle.overwrite"),
    )
    p_bundle.set_defaults(func=cmd_bundle)

    # portable source import
    p_import = sub.add_parser(
        "import",
        help=t("import.help"),
    )
    p_import.add_argument("bundle", help=t("import.bundle"))
    p_import.add_argument(
        "--path",
        help=t("import.path"),
    )
    p_import.add_argument(
        "--overwrite",
        action="store_true",
        help=t("import.overwrite"),
    )
    p_import.set_defaults(func=cmd_import)

    # remote login/logout
    p_login = sub.add_parser("login", help=t("login.help"))
    p_login.add_argument("server", help=t("login.server"))
    p_login.add_argument("--tenant", help=t("login.tenant"))
    p_login.add_argument("--username", help=t("login.username"))
    p_login.add_argument("--password", help=t("login.password"))
    p_login.add_argument("--device", help=t("login.device"))
    p_login.add_argument(
        "--tls-ca",
        help=t("login.tls_ca"),
    )
    p_login.add_argument(
        "--tls-client-cert",
        help=t("login.tls_client_cert"),
    )
    p_login.add_argument(
        "--tls-client-key",
        help=t("login.tls_client_key"),
    )
    p_login.set_defaults(func=cmd_login)

    p_logout = sub.add_parser("logout", help=t("logout.help"))
    p_logout.set_defaults(func=cmd_logout)

    # remote sync
    p_push = sub.add_parser("push", help=t("push.help"))
    p_push.add_argument("scope", choices=["all"], help=t("push.scope"))
    p_push.set_defaults(func=cmd_push)

    p_pull = sub.add_parser("pull", help=t("pull.help"))
    p_pull.add_argument("scope", choices=["all"], help=t("pull.scope"))
    p_pull.set_defaults(func=cmd_pull)

    p_adopt = sub.add_parser(
        "adopt",
        help=t("adopt.help"),
    )
    p_adopt.add_argument("archive_key", help=t("adopt.archive_key"))
    p_adopt.add_argument("path", help=t("adopt.path"))
    p_adopt.set_defaults(func=cmd_adopt)

    # diff
    p_diff = sub.add_parser(
        "diff",
        help=t("diff.help"),
    )
    p_diff.add_argument("a", nargs="?", help=t("diff.a")).completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_diff.add_argument(
        "b", nargs="?",
        help=t("diff.b"),
    ).completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_diff.add_argument(
        "--path", help=t("diff.path"),
    )
    p_diff.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_diff.add_argument(
        "--text", action="store_true",
        help=t("diff.text"),
    )
    p_diff.add_argument(
        "--tui", action="store_true",
        help=t("diff.tui"),
    )
    p_diff.set_defaults(func=cmd_diff)

    # config
    p_config = sub.add_parser(
        "config",
        help=t("config.help"),
    )
    p_config.add_argument(
        "op",
        choices=["get", "set", "unset", "list"],
        help=t("config.op"),
    )
    p_config.add_argument("key", nargs="?", help=t("config.key"))
    p_config.add_argument("value", nargs="?", help=t("config.value"))
    p_config.set_defaults(func=cmd_config)

    # gc
    p_gc = sub.add_parser(
        "gc",
        help=t("gc.help"),
    )
    p_gc.add_argument("--path", help=t("gc.path"))
    p_gc.add_argument(
        "--all", action="store_true",
        help=t("gc.all"),
    )
    p_gc.add_argument(
        "--dry-run", action="store_true",
        help=t("gc.dry_run"),
    )
    p_gc.add_argument(
        "--rebuild-index", action="store_true",
        help=t("gc.rebuild_index"),
    )
    p_gc.set_defaults(func=cmd_gc)

    # check
    p_check = sub.add_parser(
        "check",
        help=t("check.help"),
    )
    p_check.add_argument("path", nargs="?", help=t("check.path"))
    p_check.add_argument("--all", action="store_true", help=t("check.all"))
    p_check.add_argument("--deep", action="store_true", help=t("check.deep"))
    p_check.add_argument("--fix", action="store_true", help=t("check.fix"))
    p_check.set_defaults(func=cmd_check)

    # migrate
    p_migrate = sub.add_parser(
        "migrate",
        help=t("migrate.help"),
    )
    p_migrate.add_argument("path", nargs="?", help=t("migrate.path"))
    p_migrate.add_argument("--all", action="store_true", help=t("migrate.all"))
    p_migrate.add_argument("--to", default="v3", choices=["v3"], help=t("migrate.to"))
    p_migrate.add_argument("--dry-run", action="store_true", help=t("migrate.dry_run"))
    p_migrate.set_defaults(func=cmd_migrate)

    # init source marker
    p_init = sub.add_parser(
        "init",
        help=t("init.help"),
    )
    p_init.add_argument("path", nargs="?", help=t("init.path"))
    p_init.add_argument(
        "--force",
        action="store_true",
        help=t("init.force"),
    )
    p_init.set_defaults(func=cmd_init)

    # Compatibility alias for the user's requested spelling.
    p_initd = sub.add_parser("initd", help=argparse.SUPPRESS)
    p_initd.add_argument("path", nargs="?", help=t("init.path"))
    p_initd.add_argument("--force", action="store_true", help=t("init.force"))
    p_initd.set_defaults(func=cmd_init)
    sub._choices_actions = [  # type: ignore[attr-defined]  # noqa: SLF001
        action
        for action in sub._choices_actions  # type: ignore[attr-defined]  # noqa: SLF001
        if action.dest != "initd"
    ]

    # relocate source binding after a directory rename
    p_relocate = sub.add_parser(
        "relocate",
        help=t("relocate.help"),
    )
    p_relocate.add_argument(
        "paths",
        nargs="*",
        help=t("relocate.paths"),
    )
    p_relocate.add_argument(
        "--auto",
        action="store_true",
        help=t("relocate.auto"),
    )
    p_relocate.add_argument(
        "--dry-run",
        action="store_true",
        help=t("relocate.dry_run"),
    )
    p_relocate.add_argument(
        "-y", "--yes",
        action="store_true",
        help=t("relocate.yes"),
    )
    p_relocate.set_defaults(func=cmd_relocate)

    # archive sources whose original directory is missing or recreated
    p_archive = sub.add_parser(
        "archive",
        help=t("archive.help"),
    )
    archive_sub = p_archive.add_subparsers(dest="archive_op")
    p_archive_list = archive_sub.add_parser("list", help=t("archive.list_help"))
    p_archive_list.set_defaults(func=cmd_archive)
    p_archive_restore = archive_sub.add_parser(
        "restore",
        help=t("archive.restore_help"),
    )
    p_archive_restore.add_argument(
        "archive",
        nargs="?",
        help=t("archive.archive_arg"),
    )
    p_archive_restore.add_argument("name", nargs="?", help=t("archive.snapshot"))
    p_archive_restore.add_argument("dst", nargs="?", help=t("archive.dst"))
    p_archive_restore.add_argument(
        "--overwrite",
        action="store_true",
        help=t("archive.overwrite"),
    )
    p_archive_restore.set_defaults(func=cmd_archive)

    # protect / unprotect
    p_protect = sub.add_parser("protect", help=t("protect.help"))
    p_protect.add_argument("name", nargs="?").completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_protect.add_argument("--path", help=t("protect.path"))
    p_protect.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_protect.set_defaults(func=cmd_protect)

    p_unprotect = sub.add_parser("unprotect", help=t("unprotect.help"))
    p_unprotect.add_argument("name", nargs="?").completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_unprotect.add_argument("--path", help=t("protect.path"))
    p_unprotect.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_unprotect.set_defaults(func=cmd_protect)

    # stats
    p_stats = sub.add_parser(
        "stats",
        help=t("stats.help"),
    )
    p_stats.add_argument(
        "path", nargs="?",
        help=t("stats.path"),
    )
    p_stats.add_argument(
        "--all", action="store_true",
        help=t("stats.all"),
    )
    p_stats.add_argument(
        "--text", action="store_true",
        help=t("stats.text"),
    )
    p_stats.set_defaults(func=cmd_stats)

    # prune
    p_prune = sub.add_parser(
        "prune",
        help=t("prune.help"),
    )
    p_prune.add_argument(
        "--path", help=t("prune.path"),
    )
    p_prune.add_argument(
        "--keep-last", type=int, metavar="N",
        help=t("prune.keep_last"),
    )
    p_prune.add_argument(
        "--keep-within-days", type=int, metavar="DAYS",
        help=t("prune.keep_within"),
    )
    p_prune.add_argument(
        "--keep-daily", type=int, metavar="N",
        help=t("prune.keep_daily"),
    )
    p_prune.add_argument(
        "--keep-weekly", type=int, metavar="N",
        help=t("prune.keep_weekly"),
    )
    p_prune.add_argument(
        "--keep-tag", action="append", metavar="TAG", default=None,
        help=t("prune.keep_tag"),
    )
    p_prune.add_argument(
        "--protect", action="append", metavar="NAME",
        help=t("prune.protect"),
    ).completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_prune.add_argument(
        "-y", "--yes", action="store_true",
        help=t("prune.yes"),
    )
    p_prune.add_argument(
        "--dry-run", action="store_true",
        help=t("prune.dry_run"),
    )
    p_prune.add_argument(
        "--no-gc", action="store_true",
        help=t("prune.no_gc"),
    )
    p_prune.add_argument(
        "--text", action="store_true",
        help=t("prune.text"),
    )
    p_prune.set_defaults(func=cmd_prune)

    # revert
    p_revert = sub.add_parser(
        "revert",
        help=t("revert.help"),
    )
    p_revert.add_argument(
        "name", nargs="?", help=t("revert.name"),
    ).completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_revert.add_argument(
        "paths", nargs="*",
        help=t("revert.paths"),
    )
    p_revert.add_argument(
        "--path", help=t("revert.path"),
    )
    p_revert.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_revert.add_argument(
        "-y", "--yes", action="store_true",
        help=t("revert.yes"),
    )
    p_revert.add_argument(
        "--no-auto-save", action="store_true",
        help=t("revert.no_auto_save"),
    )
    p_revert.add_argument(
        "--delete-extras", action="store_true",
        help=t("revert.delete_extras"),
    )
    p_revert.add_argument(
        "--text", action="store_true",
        help=t("revert.text"),
    )
    p_revert.set_defaults(func=cmd_revert)

    # undo (v0.2)
    p_undo = sub.add_parser("undo", help=t("undo.help"))
    p_undo.add_argument("--path", help=t("undo.path"))
    p_undo.add_argument(
        "-y", "--yes", action="store_true",
        help=t("undo.yes"),
    )
    p_undo.add_argument(
        "--no-clean", action="store_true",
        help=t("undo.no_clean"),
    )
    p_undo.set_defaults(func=cmd_undo)

    # find (v0.2)
    p_find = sub.add_parser("find", help=t("find.help"))
    p_find.add_argument("pattern", help=t("find.pattern"))
    p_find.add_argument("--path", help=t("find.path"))
    p_find.add_argument(
        "--all", action="store_true",
        help=t("find.all"),
    )
    p_find.add_argument(
        "--text", action="store_true",
        help=t("find.text"),
    )
    p_find.set_defaults(func=cmd_find)

    # cat — print one file from a snapshot
    p_cat = sub.add_parser("cat", help=t("cat.help"))
    p_cat.add_argument("name", nargs="?", help=t("cat.snapshot")).completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_cat.add_argument("relpath", nargs="?", help=t("cat.relpath"))
    p_cat.add_argument("--path", help=t("cat.path"))
    p_cat.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_cat.add_argument(
        "--raw",
        action="store_true",
        help=t("cat.raw"),
    )
    p_cat.add_argument(
        "--binary-ok",
        action="store_true",
        help=t("cat.binary_ok"),
    )
    p_cat.set_defaults(func=cmd_cat)

    # browse — interactive manifest browser
    p_browse = sub.add_parser("browse", help=t("browse.help"))
    p_browse.add_argument("name", nargs="?", help=t("browse.snapshot")).completer = _snapshot_name_completer  # type: ignore[attr-defined]
    p_browse.add_argument("--path", help=t("browse.path"))
    p_browse.add_argument(
        "--all", action="store_true",
        help=t("flag.show_all"),
    )
    p_browse.add_argument(
        "--filter",
        default="",
        help=t("browse.filter"),
    )
    p_browse.set_defaults(func=cmd_browse)

    # tag — user-defined labels
    p_tag = sub.add_parser("tag", help=t("tag.help"))
    tag_sub = p_tag.add_subparsers(dest="tag_action")
    p_tag_add = tag_sub.add_parser("add", help=t("tag.add_help"))
    p_tag_add.add_argument("name", help=t("tag.snapshot"))
    p_tag_add.add_argument("tags", nargs="+", help=t("tag.values"))
    p_tag_add.add_argument("--path", help=t("tag.path"))
    p_tag_add.set_defaults(func=cmd_tag, tag_action="add")
    p_tag_rm = tag_sub.add_parser("rm", help=t("tag.rm_help"))
    p_tag_rm.add_argument("name", help=t("tag.snapshot"))
    p_tag_rm.add_argument("tags", nargs="+", help=t("tag.values"))
    p_tag_rm.add_argument("--path", help=t("tag.path"))
    p_tag_rm.set_defaults(func=cmd_tag, tag_action="rm")
    p_tag_list = tag_sub.add_parser("list", help=t("tag.list_help"))
    p_tag_list.add_argument("--path", help=t("tag.path"))
    p_tag_list.set_defaults(func=cmd_tag, tag_action="list")
    # Default when `snapz tag` is invoked without a subcommand → list.
    p_tag.set_defaults(func=cmd_tag, tag_action="list")

    # log — operation history
    p_log = sub.add_parser("log", help=t("log.help"))
    p_log.add_argument("--path", help=t("log.path"))
    p_log.add_argument(
        "--all", action="store_true",
        help=t("log.all"),
    )
    p_log.add_argument(
        "-n", "--limit", type=int, default=None,
        help=t("log.limit"),
    )
    p_log.add_argument(
        "--kind", default=None,
        help=t("log.kind"),
    )
    p_log.set_defaults(func=cmd_log)

    # self-management
    p_update = sub.add_parser("update", help=t("update.help"))
    p_update.add_argument(
        "--target",
        default=SNAPZ_GITHUB_INSTALL_TARGET,
        help=argparse.SUPPRESS,
    )
    p_update.set_defaults(func=cmd_update)

    p_uninstall = sub.add_parser("uninstall", help=t("uninstall.help"))
    p_uninstall.add_argument(
        "-y", "--yes",
        action="store_true",
        help=t("uninstall.yes"),
    )
    p_uninstall.add_argument(
        "--purge-data",
        action="store_true",
        help=t("uninstall.purge_data"),
    )
    p_uninstall.set_defaults(func=cmd_uninstall)

    sub.metavar = "{" + ",".join(  # type: ignore[attr-defined]  # noqa: SLF001
        action.dest
        for action in sub._choices_actions  # type: ignore[attr-defined]  # noqa: SLF001
    ) + "}"

    return parser

def _main_impl(argv: Optional[list[str]]) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv[:1] == [update_check.WORKER_COMMAND]:
        return update_check.run_worker_cli(argv[1:])

    parser = build_parser()

    # Optional shell-completion hook. Only fires when invoked through
    # the argcomplete environment (``_ARGCOMPLETE``); otherwise it's a
    # no-op and won't slow down regular CLI invocations.
    try:
        import argcomplete  # type: ignore
    except ImportError:
        pass
    else:
        argcomplete.autocomplete(parser)

    # Handle the bare ``snapz`` and ``snapz <path>`` forms before argparse
    # gets a chance to complain about an unknown positional. Anything that
    # starts with ``-`` (e.g. ``--version``, ``--help``) goes through
    # argparse so the user gets the expected behaviour.
    known_subs = {
        "save", "list", "alist", "rm", "mv", "show", "restore",
        "gc", "check", "migrate", "init", "initd", "protect", "unprotect",
        "relocate", "archive",
        "export", "bundle", "import", "config", "diff",
        "login", "logout", "push", "pull", "adopt",
        "stats", "prune", "revert",
        "undo", "find", "cat", "browse", "log", "tag",
        "update", "uninstall",
    }
    enter_bare_mode = (
        not argv
        or (not argv[0].startswith("-") and argv[0] not in known_subs)
    )
    if enter_bare_mode:
        path = argv[0] if argv else "."
        if len(argv) > 1:
            print(t('bare.too_many_args', argv=argv), file=sys.stderr)
            return EXIT_ERROR
        config = default_config()
        try:
            st.configure(str(preferences.get_config_value(Path(config.root), "color")))
        except (KeyError, ValueError):
            st.configure("auto")
        update_check.maybe_start(
            Path(config.root),
            current_version=__version__,
            argv0=sys.argv[0],
            command=None,
        )
        ns = argparse.Namespace(
            path=path,
            yes=False,
            include_large=False,
            message="",
            no_cache=False,
            no_picker=False,
        )
        return cmd_save_interactive(ns, config)

    # Root-level flags (``--no-zstd``, ``--json``) are valid both before
    # and after the subcommand for ergonomics, but argparse only honours
    # them in the leading position. Strip+remember them here so any
    # ordering works (``snapz list --json`` and ``snapz --json list``
    # are equivalent).
    json_requested = "--json" in argv
    no_zstd_requested = "--no-zstd" in argv
    argv = [a for a in argv if a not in ("--json",)]

    args = parser.parse_args(argv)
    config = default_config()
    try:
        st.configure(str(preferences.get_config_value(Path(config.root), "color")))
    except (KeyError, ValueError):
        st.configure("auto")
    if getattr(args, "no_zstd", False) or no_zstd_requested:
        config = RuntimeConfig(
            root=config.root,
            large_file_bytes=config.large_file_bytes,
            follow_symlinks=config.follow_symlinks,
            use_zstd=False,
            apply_default_ignores=config.apply_default_ignores,
            apply_gitignore=config.apply_gitignore,
            apply_snapzignore=config.apply_snapzignore,
            use_file_cache=config.use_file_cache,
            save_workers=config.save_workers,
        )

    workers = getattr(args, "workers", None)
    if workers is not None:
        if workers < 1:
            _print_error(t("save.workers_positive"))
            return EXIT_ERROR
        config = RuntimeConfig(
            root=config.root,
            large_file_bytes=config.large_file_bytes,
            follow_symlinks=config.follow_symlinks,
            use_zstd=config.use_zstd,
            apply_default_ignores=config.apply_default_ignores,
            apply_gitignore=config.apply_gitignore,
            apply_snapzignore=config.apply_snapzignore,
            use_file_cache=config.use_file_cache,
            save_workers=workers,
        )

    if json_requested:
        args.json = True

    if not hasattr(args, "func"):
        parser.print_help()
        return EXIT_OK

    update_check.maybe_start(
        Path(config.root),
        current_version=__version__,
        argv0=sys.argv[0],
        command=getattr(args, "command", None),
        json_requested=_wants_json(args),
    )

    return args.func(args, config)
