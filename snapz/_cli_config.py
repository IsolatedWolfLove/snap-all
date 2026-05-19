"""Configuration command."""

from __future__ import annotations

from snapz._cli_common import *
def _format_config_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)

def cmd_config(args: argparse.Namespace, config: RuntimeConfig) -> int:
    from snapz import preferences

    op = args.op
    root = Path(config.root)

    if op == "list":
        on_disk = preferences.load_config(root)
        if _wants_json(args):
            _emit_json({
                "config": preferences.effective_config(root),
                "overrides": on_disk,
            })
            return EXIT_OK
        key_w = max(len(k) for k in preferences.KNOWN_CONFIG_KEYS)
        for key, spec in preferences.KNOWN_CONFIG_KEYS.items():
            effective = on_disk.get(key, spec["default"])
            origin = st.muted(
                t("config.set_marker") if key in on_disk
                else t("config.default_marker")
            )
            value_text = _format_config_value(effective)
            print(
                f"{st.name(key.ljust(key_w))}  "
                f"{st.numeric(value_text.rjust(10))}  {origin}"
            )
            print(f"  {st.muted(spec['help'])}")
        return EXIT_OK

    if op in ("get", "set", "unset") and not args.key:
        _print_error(t('config.requires_key', op=op))
        return EXIT_ERROR

    try:
        if op == "get":
            value = preferences.get_config_value(root, args.key)
            if _wants_json(args):
                _emit_json({"key": args.key, "value": value})
                return EXIT_OK
            print(_format_config_value(value))
            return EXIT_OK
        if op == "set":
            if args.value is None:
                _print_error(t('config.set_requires_value'))
                return EXIT_ERROR
            parsed = preferences.set_config_value(root, args.key, args.value)
            if _wants_json(args):
                _emit_json({"key": args.key, "value": parsed, "set": True})
                return EXIT_OK
            print(
                f"{st.ok_mark()} {st.name(args.key)} = "
                f"{st.numeric(_format_config_value(parsed))}"
            )
            return EXIT_OK
        if op == "unset":
            removed = preferences.unset_config_value(root, args.key)
            if _wants_json(args):
                default = preferences.KNOWN_CONFIG_KEYS.get(args.key, {}).get("default")
                _emit_json({
                    "key": args.key,
                    "removed": removed,
                    "value": default,
                })
                return EXIT_OK
            if removed:
                spec = preferences.KNOWN_CONFIG_KEYS.get(args.key)
                default = spec["default"] if spec else None
                print(
                    f"{st.ok_mark()} unset {st.name(args.key)} "
                    f"{st.muted(t('config.unset_default'))} "
                    f"{st.numeric(_format_config_value(default))}"
                )
            else:
                print(st.muted(t('config.was_not_set', key=args.key)))
            return EXIT_OK
    except KeyError as exc:
        _print_error(str(exc).strip("'\""))
        known = ", ".join(preferences.KNOWN_CONFIG_KEYS.keys())
        _print_error(t('config.known_keys', keys=known))
        return EXIT_ERROR
    except ValueError as exc:
        _print_error(str(exc))
        return EXIT_ERROR

    _print_error(t('config.unknown_op', op=op))
    return EXIT_ERROR

