from snapz import archive
from snapz.ignore import IgnoreMatcher, build_matcher


def test_default_matcher_filters_pycache(project_dir):
    matcher = build_matcher(project_dir)
    assert matcher.match("__pycache__", is_dir=True)
    assert matcher.match("__pycache__/x.pyc", is_dir=False)
    assert matcher.match(".snapz-id", is_dir=False)
    assert matcher.match("./.snapz-id", is_dir=False)


def test_default_matcher_keeps_source(project_dir):
    matcher = build_matcher(project_dir)
    assert not matcher.match("src", is_dir=True)
    assert not matcher.match("src/main.py", is_dir=False)
    assert not matcher.match("README.md", is_dir=False)


def test_gitignore_dir_pattern_filters_subtree(project_dir):
    matcher = build_matcher(project_dir)
    # the conftest fixture writes "ignored/" into .gitignore
    assert matcher.match("ignored", is_dir=True)
    assert matcher.match_dir_early("ignored")


def test_snapzignore_takes_effect(project_dir):
    (project_dir / ".snapzignore").write_text("data/\n", encoding="utf-8")
    matcher = build_matcher(project_dir)
    assert matcher.match("data", is_dir=True)


def test_disable_defaults_keeps_pycache(project_dir):
    matcher = build_matcher(
        project_dir,
        apply_defaults=False,
        apply_gitignore=False,
        apply_snapzignore=False,
    )
    assert not matcher.match("__pycache__", is_dir=True)


def test_dir_only_pattern_does_not_match_files():
    m = IgnoreMatcher().extended(["build/"])
    assert m.match("build", is_dir=True)
    assert not m.match("build", is_dir=False)


def test_nested_gitignore_negation(project_dir):
    logs = project_dir / "logs"
    logs.mkdir()
    (logs / ".gitignore").write_text("*.log\n!important.log\n", encoding="utf-8")
    (logs / "drop.log").write_text("drop\n", encoding="utf-8")
    (logs / "important.log").write_text("keep\n", encoding="utf-8")

    matcher = build_matcher(project_dir)

    assert matcher.match("logs/drop.log", is_dir=False)
    assert not matcher.match("logs/important.log", is_dir=False)


def test_dir_prune_count_for_ignored_subtree(project_dir, config):
    matcher = build_matcher(project_dir)

    walk = archive.dry_run(project_dir, matcher, config)

    assert walk.dirs_pruned >= 1


def test_early_prune_respects_negation(project_dir):
    (project_dir / ".snapzignore").write_text(
        "docs/\n!docs/keep.txt\n",
        encoding="utf-8",
    )
    matcher = build_matcher(project_dir)

    assert matcher.match("docs", is_dir=True)
    assert not matcher.match_dir_early("docs")


def test_extended_returns_new_matcher_without_mutating_original():
    base = IgnoreMatcher().extended(["foo"])
    extra = base.extended(["bar"])
    assert base.match("foo", is_dir=False)
    assert not base.match("bar", is_dir=False)
    assert extra.match("bar", is_dir=False)
