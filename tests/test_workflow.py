from __future__ import annotations

import json
import os
from pathlib import Path

from blender_production_automation.workflow import (
    find_best_bottle_blend,
    find_best_customer_key,
    load_config,
    load_library_routes,
    parse_customer_and_bottle,
    pick_pdf,
    resolve_url_context,
    select_bottle_key,
)


def test_region_aware_customer_matching_prefers_requested_market() -> None:
    keys = ["Example Nutrition CA", "Example Nutrition US"]

    assert find_best_customer_key(keys, "Example Nutrition US Inc") == "Example Nutrition US"


def test_box_proof_prefers_outer_die_code() -> None:
    text = """
    CUSTOMER: Example Nutrition US
    BOTTLE: BOTTLE 100
    PROOF TYPE: BOX BCR1234
    DIE: R1234567
    """

    customer, bottle = parse_customer_and_bottle(text)

    assert customer == "Example Nutrition US"
    assert bottle == "BOX R1234567"


def test_box_proof_preserves_numeric_die_prefix() -> None:
    text = """
    CUSTOMER: Preferred Nutrition
    BOTTLE: (Blister packs go inside)
    PROOF: FINAL CODE: IFCPN0646
    DIE: 7-R0618202-rev0
    """

    customer, bottle = parse_customer_and_bottle(text)

    assert customer == "Preferred Nutrition"
    assert bottle == "BOX 7-R0618202"


def test_pouch_proof_uses_normalized_die_code() -> None:
    text = """
    CUSTOMER: Sample Wellness AUS
    BOTTLE: N/A
    PRODUCT: POUCH PCH1234
    DIE: OL_123456A
    """

    customer, bottle = parse_customer_and_bottle(text)

    assert customer == "Sample Wellness AUS"
    assert bottle == "POUCH OL-123456A"


def test_url_resolution_uses_matching_subcustomer() -> None:
    customers_map = {
        "Example Nutrition": ["Example Nutrition", "Example Nutrition CA", "Example Nutrition US"]
    }
    customer_urls = {
        "Example Nutrition": {
            "BOTTLE 100": {
                "Example Nutrition CA": ["https://example.com/ca"],
                "Example Nutrition US": ["https://example.com/us"],
            }
        }
    }

    result = resolve_url_context(
        "Example Nutrition US",
        "BOTTLE 100",
        customers_map,
        customer_urls,
        ["https://example.com/default"],
    )

    assert result.urls == ("https://example.com/us",)
    assert result.subcustomer_key == "Example Nutrition US"
    assert result.used_default_urls is False


def test_url_resolution_uses_filename_subcustomer_hint() -> None:
    customers_map = {
        "Webber Naturals": ["Webber Naturals", "Webber Naturals Canada", "PGX Daily"]
    }
    customer_urls = {
        "Webber Naturals": {
            "BOT 500WN": {
                "Webber Naturals": ["https://example.com/webber"],
                "PGX Daily": ["https://example.com/pgx"],
                "TruNature US": ["https://example.com/trunature"],
            }
        }
    }

    result = resolve_url_context(
        "Webber Naturals (Canada, mass)",
        "BOT500WN",
        customers_map,
        customer_urls,
        ["https://example.com/default"],
        subcustomer_hint="3751-7_LABWN_PGXDaily_BOT500WN_R5",
    )

    assert result.urls == ("https://example.com/pgx",)
    assert result.subcustomer_key == "PGX Daily"
    assert result.used_default_urls is False


def test_bottle_selection_matches_base_r_code_in_combined_key() -> None:
    keys = ["BOX A8692 OR R0819146", "BOX R2302024"]

    assert select_bottle_key(keys, "BOX 8-R0819146") == "BOX A8692 OR R0819146"


def test_pick_pdf_ignores_auxiliary_artwork(tmp_path: Path) -> None:
    proof = tmp_path / "12345-proof.pdf"
    auxiliary = tmp_path / "12345-DL.pdf"
    proof.write_bytes(b"proof")
    auxiliary.write_bytes(b"dieline")
    os.utime(auxiliary, (2_000_000_000, 2_000_000_000))

    assert pick_pdf(tmp_path, "newest") == proof


def test_find_best_bottle_blend_uses_normalized_candidate(tmp_path: Path) -> None:
    expected = tmp_path / "EX-BOTTLE-100.blend"
    expected.write_bytes(b"")
    (tmp_path / "EX-BOTTLE-200.blend").write_bytes(b"")

    assert find_best_bottle_blend(tmp_path, ["bottle 100"]) == expected


def test_load_config_resolves_relative_paths(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "source_folder": "inbox",
                "library_folder": "library",
                "data_folder": "data",
                "bottle_libraries": {"Example Nutrition": "bottles/example"},
                "default_urls": [],
            }
        ),
        encoding="utf-8",
    )

    config = load_config(settings)

    assert config.source_folder == tmp_path / "inbox"
    assert config.library_folder == tmp_path / "library"
    assert config.data_folder == tmp_path / "data"
    assert config.bottle_libraries["Example Nutrition"] == tmp_path / "bottles" / "example"


def test_library_routes_resolve_relative_to_the_mapping_file(tmp_path: Path) -> None:
    mapping = tmp_path / "library_routes.json"
    mapping.write_text(
        json.dumps(
            {
                "fallback_root": "library/_Unsorted",
                "routes": [{"match": "Example Nutrition", "root": "library/example"}],
            }
        ),
        encoding="utf-8",
    )

    routes, _template, fallback = load_library_routes(mapping, tmp_path / "default")

    assert fallback == tmp_path / "library" / "_Unsorted"
    assert Path(routes[0]["root"]) == tmp_path / "library" / "example"
