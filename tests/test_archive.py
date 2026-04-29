from pathlib import Path

import pytest

from snapz import archive
from snapz.config import RuntimeConfig
from snapz.ignore import build_matcher


def test_dry_run_respects_gitignore(project_dir, config):
    matcher = build_matcher(project_dir)
    walk = archive.dry_run(project_dir, matcher, config)
    rels = sorted(f.relpath for f in walk.files)
    assert "src/main.py" in rels
    assert "src/lib.py" in rels
    assert "data/input.txt" in rels
    assert "README.md" in rels
    # gitignored + default-ignored should be skipped
    for rel in rels:
        assert not rel.startswith("ignored/")
        assert not rel.startswith("__pycache__/")
    # ``.gitignore`` itself is part of the source and should be packed
    assert ".gitignore" in rels


def test_dry_run_marks_large_files(project_dir, config):
    big = project_dir / "huge.bin"
    big.write_bytes(b"\x00" * 200)
    config = RuntimeConfig(root=config.root, large_file_bytes=64)
    matcher = build_matcher(project_dir)
    walk = archive.dry_run(project_dir, matcher, config)
    large_rels = {f.relpath for f in walk.large_files}
    assert "huge.bin" in large_rels
    packed_rels = {f.relpath for f in walk.files}
    assert "huge.bin" not in packed_rels


def test_dry_run_include_large_keeps_them(project_dir, config):
    big = project_dir / "huge.bin"
    big.write_bytes(b"\x00" * 200)
    config = RuntimeConfig(root=config.root, large_file_bytes=64)
    matcher = build_matcher(project_dir)
    walk = archive.dry_run(project_dir, matcher, config, include_large=True)
    rels = {f.relpath for f in walk.files}
    assert "huge.bin" in rels


@pytest.mark.parametrize("compression", ["zstd", "gzip"])
def test_pack_and_unpack_roundtrip(project_dir, config, tmp_path, compression):
    if compression == "zstd" and not archive.zstd_available():
        pytest.skip("zstandard not installed")
    config = RuntimeConfig(root=config.root, use_zstd=(compression == "zstd"))
    matcher = build_matcher(project_dir)
    walk = archive.dry_run(project_dir, matcher, config)
    suffix = ".tar.zst" if compression == "zstd" else ".tar.gz"
    target = tmp_path / f"out{suffix}"
    result = archive.pack(
        source=project_dir,
        target_path=target,
        walk_result=walk,
        config=config,
    )
    assert target.exists()
    assert result.bytes_written > 0
    assert result.compression == compression
    assert result.file_count == walk.file_count

    out_dir = tmp_path / "restored"
    extracted = archive.unpack(target, out_dir)
    assert extracted == walk.file_count
    # Spot-check restored content
    assert (out_dir / "src" / "main.py").read_text() == "print('hi')\n"
    assert not (out_dir / "ignored").exists()
    assert not (out_dir / "__pycache__").exists()


def test_pack_invokes_progress_callback(project_dir, config, tmp_path):
    matcher = build_matcher(project_dir)
    walk = archive.dry_run(project_dir, matcher, config)
    target = tmp_path / "out.tar.gz"
    config = RuntimeConfig(root=config.root, use_zstd=False)
    seen: list[int] = []

    def cb(idx, total, _entry):
        seen.append(idx)
        assert total == walk.file_count

    archive.pack(project_dir, target, walk, config=config, on_progress=cb)
    assert seen == list(range(1, walk.file_count + 1))
