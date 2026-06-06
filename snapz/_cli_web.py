"""Local client web UI command."""

from __future__ import annotations

from snapz._cli_common import *
from snapz import web_ui


def cmd_web(args: argparse.Namespace, config: RuntimeConfig) -> int:
    host = str(getattr(args, "host", "127.0.0.1") or "127.0.0.1")
    port = int(getattr(args, "port", 3000))
    if port < 0 or port > 65535:
        _print_error(t("web.invalid_port"))
        return EXIT_ERROR
    if not bool(getattr(args, "allow_remote", False)) and not web_ui.is_loopback_host(host):
        _print_error(t("web.refuse_remote", host=host))
        return EXIT_ERROR

    try:
        server = web_ui.create_server(host, port, config=config)
    except OSError as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    url = web_ui.server_url(server, host)
    print(t("web.started", url=url), flush=True)
    print(t("web.stop_hint"), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        return EXIT_OK
    finally:
        server.server_close()
    return EXIT_OK
