"""Reusable, testable file-system operations for production assets."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

IMAGE_EXTENSIONS = frozenset(
    {".bmp", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
)


@dataclass(frozen=True)
class FolderSize:
    path: Path
    size_bytes: int

    @property
    def size_megabytes(self) -> float:
        return self.size_bytes / (1024 * 1024)


def folder_size(path: Path) -> int:
    """Return the recursive size of regular files below *path*."""
    total = 0
    for root, _directories, filenames in os.walk(path):
        root_path = Path(root)
        for filename in filenames:
            candidate = root_path / filename
            try:
                if candidate.is_file():
                    total += candidate.stat().st_size
            except OSError:
                # Files may disappear or become unreadable during a long scan.
                continue
    return total


def child_folder_sizes(base_path: Path) -> list[FolderSize]:
    """Measure each immediate child directory, ordered by name."""
    if not base_path.is_dir():
        raise NotADirectoryError(base_path)
    return [
        FolderSize(path=child, size_bytes=folder_size(child))
        for child in sorted(base_path.iterdir(), key=lambda item: item.name.casefold())
        if child.is_dir()
    ]


def filter_folder_sizes(
    measurements: Iterable[FolderSize],
    *,
    minimum_megabytes: float | None = None,
    maximum_megabytes: float | None = None,
) -> list[FolderSize]:
    """Filter measured folders by optional inclusive size boundaries."""
    if minimum_megabytes is not None and minimum_megabytes < 0:
        raise ValueError("minimum_megabytes cannot be negative")
    if maximum_megabytes is not None and maximum_megabytes < 0:
        raise ValueError("maximum_megabytes cannot be negative")
    if (
        minimum_megabytes is not None
        and maximum_megabytes is not None
        and minimum_megabytes > maximum_megabytes
    ):
        raise ValueError("minimum_megabytes cannot exceed maximum_megabytes")

    results: list[FolderSize] = []
    for measurement in measurements:
        size = measurement.size_megabytes
        if minimum_megabytes is not None and size < minimum_megabytes:
            continue
        if maximum_megabytes is not None and size > maximum_megabytes:
            continue
        results.append(measurement)
    return results


def list_images(folder: Path) -> list[Path]:
    """Return supported image files sorted case-insensitively by name."""
    if not folder.is_dir():
        raise NotADirectoryError(folder)
    return sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS
        ),
        key=lambda path: path.name.casefold(),
    )


def build_numbered_targets(
    sources: Iterable[Path],
    *,
    prefix: str,
    padding: int = 2,
) -> list[Path]:
    """Build sequential target names while preserving each file extension."""
    if padding < 1:
        raise ValueError("padding must be at least 1")
    source_list = list(sources)
    return [
        source.with_name(f"{prefix}{index:0{padding}d}{source.suffix.casefold()}")
        for index, source in enumerate(source_list, start=1)
    ]


def rename_images(
    folder: Path,
    *,
    prefix: str,
    padding: int = 2,
    apply: bool = False,
) -> list[tuple[Path, Path]]:
    """Plan or safely apply a collision-resistant sequential image rename."""
    sources = list_images(folder)
    targets = build_numbered_targets(sources, prefix=prefix, padding=padding)
    plan = list(zip(sources, targets))
    if not apply or not plan:
        return plan

    source_paths = {source.resolve(strict=False) for source in sources}
    for target in targets:
        if target.exists() and target.resolve(strict=False) not in source_paths:
            raise FileExistsError(f"Refusing to overwrite existing file: {target}")

    staged: list[tuple[Path, Path, Path]] = []
    try:
        for source, target in plan:
            temporary = source.with_name(f".__bpa_{uuid.uuid4().hex}{source.suffix.casefold()}")
            source.rename(temporary)
            staged.append((source, temporary, target))

        for _source, temporary, target in staged:
            temporary.rename(target)
    except Exception:
        for source, temporary, target in reversed(staged):
            current = target if target.exists() else temporary
            if current.exists() and not source.exists():
                current.rename(source)
        raise

    return plan
