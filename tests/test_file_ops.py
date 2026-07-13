from __future__ import annotations

from pathlib import Path

import pytest

from blender_production_automation.file_ops import (
    FolderSize,
    build_numbered_targets,
    filter_folder_sizes,
    list_images,
    rename_images,
)


def test_list_images_filters_and_sorts_supported_files(tmp_path: Path) -> None:
    (tmp_path / "B.PNG").write_bytes(b"b")
    (tmp_path / "a.jpg").write_bytes(b"a")
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")

    assert [path.name for path in list_images(tmp_path)] == ["a.jpg", "B.PNG"]


def test_build_numbered_targets_preserves_extensions(tmp_path: Path) -> None:
    sources = [tmp_path / "front.PNG", tmp_path / "side.jpg"]

    targets = build_numbered_targets(sources, prefix="asset-", padding=3)

    assert [path.name for path in targets] == ["asset-001.png", "asset-002.jpg"]


def test_rename_images_previews_by_default_and_applies_explicitly(tmp_path: Path) -> None:
    (tmp_path / "front.png").write_bytes(b"front")
    (tmp_path / "side.png").write_bytes(b"side")

    preview = rename_images(tmp_path, prefix="render-")
    assert [target.name for _source, target in preview] == ["render-01.png", "render-02.png"]
    assert (tmp_path / "front.png").exists()

    rename_images(tmp_path, prefix="render-", apply=True)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["render-01.png", "render-02.png"]


def test_rename_images_refuses_external_collision(tmp_path: Path) -> None:
    (tmp_path / "front.png").write_bytes(b"front")
    (tmp_path / "render-01.png").mkdir()

    with pytest.raises(FileExistsError):
        rename_images(tmp_path, prefix="render-", apply=True)


def test_filter_folder_sizes_validates_boundaries(tmp_path: Path) -> None:
    measurements = [FolderSize(tmp_path / "small", 1 * 1024 * 1024)]

    assert filter_folder_sizes(measurements, minimum_megabytes=1) == measurements
    with pytest.raises(ValueError):
        filter_folder_sizes(measurements, minimum_megabytes=2, maximum_megabytes=1)
