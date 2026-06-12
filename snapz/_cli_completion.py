"""Shell-completion helpers for the snapz CLI."""

from __future__ import annotations

import argparse
import os
import shlex
from pathlib import Path

from snapz._cli_common import EXIT_ERROR, EXIT_OK, RuntimeConfig, _print_error
from snapz.i18n import t

SUPPORTED_COMPLETION_SHELLS = ("bash", "zsh")


def _completion_shellcode(shell: str, executable: str = "snapz") -> str:
    if shell not in SUPPORTED_COMPLETION_SHELLS:
        raise ValueError(t("completion.unsupported_shell", shell_name=shell))
    try:
        from argcomplete.shell_integration import shellcode
    except ImportError as exc:
        raise RuntimeError(t("completion.argcomplete_missing")) from exc
    return shellcode([executable], shell=shell)  # nosec B604


def _detect_shell() -> str:
    shell_name = Path(os.environ.get("SHELL", "")).name
    return shell_name if shell_name in SUPPORTED_COMPLETION_SHELLS else ""


def _default_rcfile(shell: str) -> Path:
    home = Path.home()
    if shell == "bash":
        return home / ".bashrc"
    if shell == "zsh":
        return home / ".zshrc"
    raise ValueError(t("completion.unsupported_shell", shell_name=shell))


def _install_completion(shell: str, rcfile: Path) -> tuple[bool, Path]:
    code = _completion_shellcode(shell)
    marker = "# snapz completion"
    block = f"\n{marker}\n{code.rstrip()}\n"
    existing = ""
    try:
        existing = rcfile.read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    if marker in existing and "snapz" in existing:
        return False, rcfile
    rcfile.parent.mkdir(parents=True, exist_ok=True)
    with rcfile.open("a", encoding="utf-8") as fh:
        fh.write(block)
    return True, rcfile


def cmd_completion(args: argparse.Namespace, config: RuntimeConfig) -> int:
    del config
    action = getattr(args, "completion_action", None)
    shell = getattr(args, "shell", None)
    if action in SUPPORTED_COMPLETION_SHELLS:
        shell = action
        action = "print"

    if action == "install":
        shell = shell or _detect_shell()
        if not shell:
            _print_error(t("completion.detect_failed"))
            return EXIT_ERROR
        rcfile = Path(getattr(args, "rcfile", None) or _default_rcfile(shell)).expanduser()
        try:
            changed, path = _install_completion(shell, rcfile)
        except (RuntimeError, ValueError) as exc:
            _print_error(str(exc))
            return EXIT_ERROR
        message_key = "completion.installed" if changed else "completion.already_installed"
        print(t(message_key, shell_name=shell, path=path))
        return EXIT_OK

    if action in (None, "print"):
        shell = shell or _detect_shell() or "bash"
        try:
            print(_completion_shellcode(shell).rstrip())
        except (RuntimeError, ValueError) as exc:
            _print_error(str(exc))
            return EXIT_ERROR
        return EXIT_OK

    _print_error(t("completion.unknown_action", action=shlex.quote(str(action))))
    return EXIT_ERROR
