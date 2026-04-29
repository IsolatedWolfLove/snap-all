from snapz.ignore import IgnoreMatcher, build_matcher


def test_default_matcher_filters_pycache(project_dir):
    matcher = build_matcher(project_dir)
    assert matcher.match("__pycache__", is_dir=True)
    assert matcher.match("__pycache__/x.pyc", is_dir=False)


def test_default_matcher_keeps_source(project_dir):
    matcher = build_matcher(project_dir)
    assert not matcher.match("src", is_dir=True)
    assert not matcher.match("src/main.py", is_dir=False)
    assert not matcher.match("README.md", is_dir=False)


def test_gitignore_dir_pattern_filters_subtree(project_dir):
    matcher = build_matcher(project_dir)
    # the conftest fixture writes "ignored/" into .gitignore
    assert matcher.match("ignored", is_dir=True)


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


def test_extended_returns_new_matcher_without_mutating_original():
    base = IgnoreMatcher().extended(["foo"])
    extra = base.extended(["bar"])
    assert base.match("foo", is_dir=False)
    assert not base.match("bar", is_dir=False)
    assert extra.match("bar", is_dir=False)
