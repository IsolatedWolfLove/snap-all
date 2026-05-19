"""i18n module + localized CLI surface."""

from __future__ import annotations

import pytest

from snapz import cli
from snapz.i18n import _EN, _ZH, get_lang, t


# ----------------- t() basics --------------------------------------------


def test_t_default_lang_is_english(monkeypatch):
    monkeypatch.delenv("SNAPZ_LANG", raising=False)
    assert get_lang() == "en"
    assert t("save.help") == _EN["save.help"]


def test_t_picks_zh_when_env_set(monkeypatch):
    monkeypatch.setenv("SNAPZ_LANG", "zh")
    assert get_lang() == "zh"
    assert t("save.help") == _ZH["save.help"]
    assert t("save.help") != _EN["save.help"]


def test_t_unknown_lang_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("SNAPZ_LANG", "fr")
    # Unknown locale → DEFAULT_LANG (which is "en" in the source tree).
    assert get_lang() == "en"
    assert t("save.help") == _EN["save.help"]


def test_t_missing_zh_key_falls_back_to_en(monkeypatch):
    # All keys MUST exist in EN; ZH may be missing some. Use a key we know
    # exists in EN — patching ZH locally — to verify fallback.
    from snapz import i18n

    sentinel_key = "__test_only_missing__"
    monkeypatch.setitem(i18n._EN, sentinel_key, "english fallback")
    monkeypatch.setenv("SNAPZ_LANG", "zh")
    assert t(sentinel_key) == "english fallback"


def test_t_unknown_key_returns_literal_key(monkeypatch):
    monkeypatch.delenv("SNAPZ_LANG", raising=False)
    assert t("definitely.not.a.key") == "definitely.not.a.key"


def test_t_format_kwargs_substitute(monkeypatch):
    monkeypatch.delenv("SNAPZ_LANG", raising=False)
    assert t("msg.deleted_one", name="release-1.0") == "deleted release-1.0"


def test_t_format_swallows_extra_kwargs(monkeypatch):
    """Extra ``**kwargs`` not referenced by the template must NOT raise."""

    monkeypatch.delenv("SNAPZ_LANG", raising=False)
    # `msg.saved` is a static "saved"; passing irrelevant kwargs should
    # not blow up — that protects polyglot strings where one language
    # references a placeholder the other one doesn't.
    assert t("msg.saved", name="x", n=5) == "saved"


# ----------------- ZH translations cover every UI key --------------------


def test_zh_strings_cover_every_en_key():
    """Every public-facing EN key SHOULD have a ZH entry; missing keys
    just fall back to English at runtime, but it's a useful smoke check
    that translators didn't drop anything wholesale.
    """

    missing = sorted(set(_EN) - set(_ZH))
    # Allow a small grace list if needed in the future; today every key
    # has a translation, so missing must be empty.
    assert missing == [], f"keys missing from zh: {missing}"


# ----------------- CLI --help surface ------------------------------------


def test_cli_help_default_is_english(monkeypatch, capsys):
    monkeypatch.delenv("SNAPZ_LANG", raising=False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Lightweight directory snapshot tool." in out
    assert "non-interactive snapshot create" in out


def test_cli_help_zh_flips_descriptions(monkeypatch, capsys):
    monkeypatch.setenv("SNAPZ_LANG", "zh")
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    # Top-line description and at least one subcommand summary should
    # appear in Chinese.
    assert "轻量的目录快照工具" in out
    assert "非交互式创建快照" in out
    assert "用法:" in out
    assert "位置参数" in out
    assert "选项" in out
    assert "显示这条帮助信息并退出" in out
    assert "显示程序版本并退出" in out
    assert "登录 snapz-server 远端" in out
    assert "initd" not in out
    # English originals should be gone.
    assert "Lightweight directory snapshot tool" not in out
    assert "positional arguments" not in out
    assert "show program's version number and exit" not in out
    assert "log in to a snapz-server remote" not in out


def test_cli_subcommand_help_zh(monkeypatch, capsys):
    monkeypatch.setenv("SNAPZ_LANG", "zh")
    # The strings here are *argument* help (which subparser --help shows),
    # not the per-subparser top-level help (which only the parent --help
    # surfaces).
    for sub, needle in [
        ("save", "跳过确认"),
        ("prune", "始终保留最新的 N 个快照"),
        ("revert", "要回滚到的快照"),
        ("stats", "汇总所有已记录的源目录"),
    ]:
        with pytest.raises(SystemExit):
            cli.main([sub, "--help"])
        out = capsys.readouterr().out
        assert needle in out, f"{sub} --help missing {needle!r}; got:\n{out}"


def test_cli_argparse_error_zh(monkeypatch, capsys):
    monkeypatch.setenv("SNAPZ_LANG", "zh")
    with pytest.raises(SystemExit) as exc:
        cli.main(["show", "--bogus"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "用法:" in err
    assert "错误:" in err
    assert "无法识别的参数" in err


# ----------------- runtime output respects SNAPZ_LANG ----------------------


@pytest.fixture
def env_root(monkeypatch, tmp_path):
    root = tmp_path / "snapz-all"
    root.mkdir()
    monkeypatch.setenv("SNAPZ_ALL_ROOT", str(root))
    return root


def test_cli_save_zh_output(monkeypatch, env_root, project_dir, capsys):
    monkeypatch.setenv("SNAPZ_LANG", "zh")
    rc = cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    out = capsys.readouterr().out
    assert rc == 0
    # Header verb + the localised "N 个文件" pluralisation come through.
    assert "已保存" in out
    assert "个文件" in out
    # English-only artefacts must be gone.
    assert "saved " not in out


def test_cli_prune_dry_run_zh_output(monkeypatch, env_root, project_dir, capsys):
    monkeypatch.setenv("SNAPZ_LANG", "zh")
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    cli.main(["save", str(project_dir), "-n", "v2", "-y"])
    capsys.readouterr()

    rc = cli.main([
        "prune", "--path", str(project_dir),
        "--keep-last", "1", "--dry-run", "--text",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    # "保留" and "丢弃" come from kv.keep / kv.drop in ZH.
    assert "保留" in out
    assert "丢弃" in out
    # Default-en label "deleted" should NOT leak into ZH dry-run.
    assert "deleted" not in out


def test_cli_revert_zh_text_error(monkeypatch, env_root, project_dir, capsys):
    """``revert --text`` with no paths should surface the ZH no-paths msg."""

    monkeypatch.setenv("SNAPZ_LANG", "zh")
    cli.main(["save", str(project_dir), "-n", "v1", "-y"])
    capsys.readouterr()

    rc = cli.main([
        "revert", "v1",
        "--path", str(project_dir),
        "--text",
    ])
    err = capsys.readouterr().err
    assert rc != 0
    assert "未指定路径" in err
