"""Command-line interface for the public automation package."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .file_ops import child_folder_sizes, filter_folder_sizes, rename_images
from .workflow import (
    customer_urls_path,
    customers_map_path,
    library_routes_path,
    load_config,
    load_customer_urls,
    load_customers_map,
    load_library_routes,
)
from .workflow import (
    main as run_production_workflow,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blender-production-automation",
        description="Utilities for metadata-driven Blender production workflows.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    run_parser = subcommands.add_parser("run", help="Run the PDF-to-asset routing workflow.")
    run_parser.add_argument("--settings", type=Path, help="Path to a private settings JSON file.")

    validate_parser = subcommands.add_parser(
        "validate-config", help="Validate settings and the three workflow mapping files."
    )
    validate_parser.add_argument("--settings", type=Path, help="Path to a settings JSON file.")

    size_parser = subcommands.add_parser(
        "folder-sizes", help="Report the recursive size of immediate child folders."
    )
    size_parser.add_argument("folder", type=Path)
    size_parser.add_argument("--min-mb", type=float, dest="minimum_megabytes")
    size_parser.add_argument("--max-mb", type=float, dest="maximum_megabytes")

    rename_parser = subcommands.add_parser(
        "rename-images", help="Preview or apply sequential image filenames."
    )
    rename_parser.add_argument("folder", type=Path)
    rename_parser.add_argument("--prefix", required=True)
    rename_parser.add_argument("--padding", type=int, default=2)
    rename_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the rename. Without this flag, only a preview is printed.",
    )
    return parser


def validate_config(settings: Path | None) -> None:
    config = load_config(settings)
    load_customers_map(customers_map_path(config))
    load_customer_urls(customer_urls_path(config))
    load_library_routes(library_routes_path(config), config.library_folder)
    print("Configuration is valid.")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "run":
        run_production_workflow(load_config(args.settings))
        return 0

    if args.command == "validate-config":
        validate_config(args.settings)
        return 0

    if args.command == "folder-sizes":
        measurements = filter_folder_sizes(
            child_folder_sizes(args.folder),
            minimum_megabytes=args.minimum_megabytes,
            maximum_megabytes=args.maximum_megabytes,
        )
        for measurement in measurements:
            print(f"{measurement.size_megabytes:10.2f} MB  {measurement.path.name}")
        return 0

    if args.command == "rename-images":
        plan = rename_images(
            args.folder,
            prefix=args.prefix,
            padding=args.padding,
            apply=args.apply,
        )
        for source, target in plan:
            print(f"{source.name} -> {target.name}")
        if plan and not args.apply:
            print("Preview only. Re-run with --apply to rename files.")
        return 0

    raise AssertionError(f"Unhandled command: {args.command}")
