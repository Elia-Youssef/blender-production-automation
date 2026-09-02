from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


# -----------------------------
# CONFIG
# -----------------------------
@dataclass(frozen=True)
class Config:
    source_folder: Path
    library_folder: Path
    data_folder: Path
    bottle_libraries: Dict[str, Path]
    customer_urls_filename: str
    customers_map_filename: str
    library_routes_filename: str
    default_urls: Tuple[str, ...]
    always_open_url: str
    strict_prefix_match: bool
    pick_pdf_mode: str
    overwrite_on_copy: bool
    hdri_filename: str
    log_level: int


SETTINGS_ENV_VAR = "BPA_SETTINGS_FILE"


def repository_root() -> Path:
    """Return the source checkout root for editable and direct-script usage."""
    return Path(__file__).resolve().parents[2]


def _resolve_path(value: Any, *, base_dir: Path, default: Path) -> Path:
    raw = str(value).strip() if value is not None else ""
    candidate = Path(os.path.expandvars(raw)).expanduser() if raw else default
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate.resolve(strict=False)


def _read_log_level(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        level = getattr(logging, value.upper(), None)
        if isinstance(level, int):
            return level
    return logging.INFO


def _read_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    raise ValueError(f"Expected a boolean value, got {value!r}")


def load_config(settings_path: Optional[Path] = None) -> Config:
    """Load public-safe defaults and overlay optional private local settings."""
    repo_root = repository_root()
    configured_path = settings_path or Path(
        os.environ.get(SETTINGS_ENV_VAR, repo_root / "config" / "settings.local.json")
    )
    configured_path = configured_path.expanduser().resolve(strict=False)

    raw: Mapping[str, Any] = {}
    if configured_path.exists():
        with configured_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise ValueError(f"Settings must be a JSON object: {configured_path}")
        raw = loaded

    base_dir = configured_path.parent
    source_folder = _resolve_path(
        os.environ.get("BPA_SOURCE_FOLDER", raw.get("source_folder")),
        base_dir=base_dir,
        default=repo_root / "workspace" / "inbox",
    )
    library_folder = _resolve_path(
        os.environ.get("BPA_LIBRARY_FOLDER", raw.get("library_folder")),
        base_dir=base_dir,
        default=repo_root / "workspace" / "library",
    )
    data_folder = _resolve_path(
        os.environ.get("BPA_DATA_FOLDER", raw.get("data_folder")),
        base_dir=base_dir,
        default=repo_root / "config" / "local",
    )

    libraries_raw = raw.get("bottle_libraries", {})
    if libraries_raw is None:
        libraries_raw = {}
    if not isinstance(libraries_raw, dict):
        raise ValueError("'bottle_libraries' must be an object mapping customer names to folders.")
    bottle_libraries = {
        str(customer).strip(): _resolve_path(path, base_dir=base_dir, default=library_folder)
        for customer, path in libraries_raw.items()
        if str(customer).strip() and isinstance(path, str) and path.strip()
    }

    default_urls_raw = raw.get("default_urls", [])
    if not isinstance(default_urls_raw, list):
        raise ValueError("'default_urls' must be a list of URL strings.")
    default_urls = tuple(
        item.strip() for item in default_urls_raw if isinstance(item, str) and item.strip()
    )

    return Config(
        source_folder=source_folder,
        library_folder=library_folder,
        data_folder=data_folder,
        bottle_libraries=bottle_libraries,
        customer_urls_filename=str(raw.get("customer_urls_filename", "customer_urls.json")),
        customers_map_filename=str(raw.get("customers_map_filename", "customers_map.json")),
        library_routes_filename=str(raw.get("library_routes_filename", "library_routes.json")),
        default_urls=default_urls,
        always_open_url=str(raw.get("always_open_url", "")).strip(),
        strict_prefix_match=_read_bool(raw.get("strict_prefix_match"), default=True),
        pick_pdf_mode=str(raw.get("pick_pdf_mode", "newest")),
        overwrite_on_copy=_read_bool(raw.get("overwrite_on_copy"), default=True),
        hdri_filename=str(raw.get("hdri_filename", "scifi_room_hdri.jpg")),
        log_level=_read_log_level(raw.get("log_level", "INFO")),
    )


CONFIG = load_config()

LOG = logging.getLogger("blender_production_automation")


def init_logging(level: int) -> None:
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


# -----------------------------
# NORMALIZATION / MATCHING
# -----------------------------


def best_key_loose(keys: List[str], query: Optional[str]) -> Optional[str]:
    if not query:
        return None

    q_loose = norm_loose(query)
    q_lower = (query or "").lower()
    q_ml = extract_ml(query)
    want_metal = "can" in q_lower  # CAN250ML -> treat as METAL preference

    best_key = None
    best_score = -(10**9)

    for k in keys:
        k_loose = norm_loose(k)
        score = 0

        # exact loose match wins hard
        if k_loose == q_loose:
            score += 1000

        # substring relations
        if q_loose in k_loose:
            score += 200
        if k_loose in q_loose:
            score += 50

        # volume preference
        if q_ml is not None and str(q_ml) in k_loose:
            score += 150

        # type preference (CAN -> METAL)
        if want_metal and "metal" in k.lower():
            score += 120
        if want_metal and ("bot" in k.lower() or "bottle" in k.lower()):
            score -= 30  # slightly penalize generic plastic bottle keys

        if score > best_score:
            best_score = score
            best_key = k

    return best_key if best_score > 0 else None


def extract_ml(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = re.search(r"(?i)(\d{2,4})\s*ml\b", s)  # works for "CAN250ML"
    return int(m.group(1)) if m else None


def is_can_bottle(s: Optional[str]) -> bool:
    return "can" in (s or "").lower()


MAX_FIELD_LEN = 120
CUSTOMER_TAIL_RE = re.compile(r"(?i)\bPLEASE INDICATE\b.*$")
INTERNAL_CUSTOMER_RE = re.compile(r"(?i)^factors\s+group(?:\s+of\s+nutritional)?\b")
BOX_DIE_CODE_RE = re.compile(r"(?i)(?<![A-Z0-9])(?:\d+-)?R\d{6,8}(?!\d)")
BOX_BASE_R_CODE_RE = re.compile(r"(?i)(?<![A-Z0-9])R\d{6,8}(?!\d)")
BOX_DIE_LABEL_RE = re.compile(r"(?i)(?:\bDIE\b|CAD\s*#)")
BOX_PROOF_CODE_RE = re.compile(r"(?i)\b(?:BCR|IFC)[A-Z]*\d+\b")
POUCH_DIE_CODE_RE = re.compile(r"(?i)(?<![A-Z0-9])OL[_ -]?\d{6}[A-Z](?![A-Z0-9])")


def clamp_field(s: Optional[str], max_len: int = MAX_FIELD_LEN) -> Optional[str]:
    if not s:
        return s
    s = s.strip()
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s


def clean_customer_value(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    s = s.strip()
    s = CUSTOMER_TAIL_RE.sub("", s).strip()
    return clamp_field(s)


def clean_bottle_value(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    return clamp_field(s)


def is_internal_customer_value(s: Optional[str]) -> bool:
    return bool(INTERNAL_CUSTOMER_RE.match((s or "").strip()))


def extract_box_die_code(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    m = BOX_DIE_CODE_RE.search(s)
    return m.group(0).upper() if m else None


def find_box_bottle_value(lines: List[str], bottle_raw: Optional[str]) -> Optional[str]:
    """
    Box proofs can carry the inner bottle in BOTTLE while the render asset is
    keyed by the outer die code, for example DIE:R2302024.
    """
    die_code = next(
        (
            extract_box_die_code(ln)
            for ln in lines
            if BOX_DIE_LABEL_RE.search(ln) and extract_box_die_code(ln)
        ),
        None,
    )
    if not die_code:
        return None

    has_box_evidence = "box" in (bottle_raw or "").lower()
    has_box_evidence = has_box_evidence or any(BOX_PROOF_CODE_RE.search(ln) for ln in lines)
    has_box_evidence = has_box_evidence or any(re.search(r"(?i)\bbox\b", ln) for ln in lines)
    if not has_box_evidence:
        return None

    return f"BOX {die_code}"


def extract_pouch_die_code(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    m = POUCH_DIE_CODE_RE.search(s)
    if not m:
        return None
    return re.sub(r"[_ ]+", "-", m.group(0).upper())


def find_pouch_bottle_value(lines: List[str], bottle_raw: Optional[str]) -> Optional[str]:
    """
    Pouch proofs can use BOTTLE:N/A while the asset is keyed by the die code,
    for example DIE:... OL_012126S -> POUCH OL-012126S.
    """
    bottle_norm = norm_loose(bottle_raw)
    if bottle_raw and bottle_norm not in {"na", "n/a"}:
        return None

    has_pouch_evidence = any(re.search(r"(?i)\bpouch\b|\bPCH[A-Z]*\d+", ln) for ln in lines)
    if not has_pouch_evidence:
        return None

    die_code = next(
        (
            extract_pouch_die_code(ln)
            for ln in lines
            if (BOX_DIE_LABEL_RE.search(ln) or re.search(r"(?i)\bpouch\b", ln))
            and extract_pouch_die_code(ln)
        ),
        None,
    )
    if not die_code:
        return None

    return f"POUCH {die_code}"


def norm(s: Optional[str]) -> str:
    """Basic normalization (keeps spaces)."""
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def norm_loose(s: Optional[str]) -> str:
    """
    Loose normalization:
    - lower
    - remove symbols (including '-', '_', etc.)
    - keep only a-z0-9
    This is what you asked for when matching customers/bottles.
    """
    s0 = (s or "").lower()
    return re.sub(r"[^a-z0-9]+", "", s0)


REGION_TOKEN_ALIASES = {
    "us": "us",
    "usa": "us",
    "canada": "ca",
    "ca": "ca",
    "aus": "aus",
    "australia": "aus",
    "australian": "aus",
    "nz": "nz",
    "china": "china",
    "japan": "japan",
    "korea": "korea",
    "taiwan": "taiwan",
    "ksa": "ksa",
    "uae": "uae",
    "uk": "uk",
    "eu": "eu",
    "europe": "eu",
    "greece": "greece",
    "philippines": "philippines",
    "lebanon": "lebanon",
    "mongolia": "mongolia",
    "iraq": "iraq",
    "slovakia": "slovakia",
    "france": "france",
    "spain": "spain",
    "kenya": "kenya",
}
REGION_TOKENS = set(REGION_TOKEN_ALIASES.values())
NOISE_CUSTOMER_TOKENS = {"inc", "ltd", "llc", "the", "and"}


def customer_tokens(s: Optional[str]) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (s or "").lower())
    tokens: set[str] = set()
    i = 0
    while i < len(words):
        if i + 1 < len(words) and words[i] == "united" and words[i + 1] == "states":
            tokens.add("us")
            i += 2
            continue
        if i + 1 < len(words) and words[i] == "new" and words[i + 1] == "zealand":
            tokens.add("nz")
            i += 2
            continue

        mapped = REGION_TOKEN_ALIASES.get(words[i], words[i])
        if mapped not in NOISE_CUSTOMER_TOKENS:
            tokens.add(mapped)
        i += 1
    return tokens


def region_tokens(s: Optional[str]) -> set[str]:
    return customer_tokens(s) & REGION_TOKENS


def score_customer_key(candidate: str, target: Optional[str]) -> int:
    """
    Score a customer/route key against parsed PDF customer text.
    Regions are treated as important so CA/US variants do not collapse into
    the same match just because the brand words are shared.
    """
    if not candidate or not target:
        return -1

    c_loose = norm_loose(candidate)
    t_loose = norm_loose(target)
    if not c_loose or not t_loose:
        return -1

    score = -1
    if c_loose == t_loose:
        score = 100000 + len(c_loose)
    elif t_loose.startswith(c_loose):
        score = 80000 + len(c_loose)
    elif c_loose.startswith(t_loose):
        score = 70000 + len(t_loose)

    c_tokens = customer_tokens(candidate)
    t_tokens = customer_tokens(target)
    shared = c_tokens & t_tokens
    brand_shared = shared - REGION_TOKENS
    if brand_shared:
        token_score = len(shared) * 100
        if c_tokens <= t_tokens:
            token_score += 10000 + len(c_tokens) * 10
        if t_tokens <= c_tokens:
            token_score += 9000 + len(t_tokens) * 10
        score = max(score, token_score)

    if score < 0:
        return -1

    c_regions = c_tokens & REGION_TOKENS
    t_regions = t_tokens & REGION_TOKENS
    if c_regions and t_regions:
        if c_regions & t_regions:
            score += 20000
        else:
            score -= 60000
    elif t_regions and not c_regions:
        score -= 1000

    return score if score > 0 else -1


def find_best_customer_key(keys: List[str], target: Optional[str]) -> Optional[str]:
    scored: List[Tuple[int, int, str]] = []
    target_regions = region_tokens(target)
    for key in keys:
        if not target_regions and region_tokens(key):
            continue
        score = score_customer_key(key, target)
        if score >= 0:
            scored.append((score, len(norm_loose(key)), key))
    if not scored:
        return None
    scored.sort(reverse=True)
    return scored[0][2]


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def customer_urls_path(config: Config) -> Path:
    return config.data_folder / config.customer_urls_filename


def customers_map_path(config: Config) -> Path:
    return config.data_folder / config.customers_map_filename


def library_routes_path(config: Config) -> Path:
    return config.data_folder / config.library_routes_filename


def find_key_loose(keys: List[str], target: str) -> Optional[str]:
    """
    Find the best matching key in `keys` for a given `target` using norm_loose.
    Returns the original key as it appears in the JSON (preserving exact casing/spaces).
    """
    t = norm_loose(target)
    if not t:
        return None

    # 1) exact loose match
    for k in keys:
        if norm_loose(k) == t:
            return k

    # 2) startswith (either direction) as a fallback
    for k in sorted(keys, key=len, reverse=True):
        nk = norm_loose(k)
        if nk and (t.startswith(nk) or nk.startswith(t)):
            return k

    return None


# -----------------------------
# LOAD CUSTOMERS MAP
# -----------------------------
def load_customers_map(path: Path) -> Dict[str, List[str]]:
    """
    Customer alias map format:
    {
      "Example Nutrition": ["Example Nutrition", "Example Nutrition US", ...],
      "Sample Wellness": ["Sample Wellness", ...]
    }
    """
    if not path.exists():
        return {}

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, dict):
        raise ValueError("customers_map.json must be a JSON object (dictionary).")

    out: Dict[str, List[str]] = {}
    for canonical, aliases in raw.items():
        if not isinstance(canonical, str) or not canonical.strip():
            continue
        if not isinstance(aliases, list):
            raise ValueError(f"customers_map.json: '{canonical}' must map to a list of aliases.")
        cleaned = []
        for a in aliases:
            if isinstance(a, str) and a.strip():
                cleaned.append(a.strip())
        # also allow canonical itself even if omitted
        if canonical.strip() not in cleaned:
            cleaned.append(canonical.strip())
        out[canonical.strip()] = cleaned

    return out


def resolve_canonical_customer(
    customer_raw: Optional[str], customers_map: Dict[str, List[str]]
) -> Optional[str]:
    """
    Return the canonical parent key by scanning configured aliases using
    loose normalization.
    """
    if not customer_raw:
        return None
    t = norm_loose(customer_raw)
    if not t:
        return None

    best: Optional[Tuple[int, int, str]] = None
    for canonical, aliases in customers_map.items():
        for alias in aliases:
            score = score_customer_key(alias, customer_raw)
            if score < 0:
                continue
            candidate = (score, len(norm_loose(alias)), canonical)
            if best is None or candidate > best:
                best = candidate

    return best[2] if best else None


# -----------------------------
# LOAD CUSTOMER URLS (NEW STRUCTURE)
# -----------------------------
CustomerUrlsType = Dict[str, Dict[str, Any]]


@dataclass(frozen=True)
class UrlResolution:
    urls: Tuple[str, ...]
    canonical_customer: Optional[str]
    url_parent: Optional[str]
    bottle_key: Optional[str]
    subcustomer_key: Optional[str]
    used_default_urls: bool


def load_customer_urls(path: Path) -> CustomerUrlsType:
    """
    Customer URL map format:
    {
      "Example Nutrition": {
        "BOTTLE 400": {
          "Example Nutrition CA": ["url1","url2","url3"],
          "Example Nutrition US": ["url1","url2","url3"]
        },
        "BOTTLE 100": []
      }
    }
    """
    if not path.exists():
        return {}

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, dict):
        raise ValueError("customer_urls.json must be a JSON object (dictionary).")

    # Keep as-is; validate lightly (avoid blocking you on partial fills)
    out: CustomerUrlsType = {}
    for parent, bottles in raw.items():
        if not isinstance(parent, str) or not parent.strip():
            continue
        if not isinstance(bottles, dict):
            # allow empty/nonconforming but skip
            continue
        out[parent.strip()] = bottles
    return out


def urls_from_entry(entry: Any) -> Optional[List[str]]:
    if isinstance(entry, list):
        urls = [x.strip() for x in entry if isinstance(x, str) and x.strip()]
        if 1 <= len(urls) <= 3:
            return urls
    return None


def norm_bottle_unit_alias(value: Optional[str]) -> str:
    """Normalize equivalent bottle codes that optionally spell out litre/liter."""
    return re.sub(r"litres?|liters?", "", norm_loose(value))


def select_bottle_key(keys: List[str], bottle_raw: Optional[str]) -> Optional[str]:
    # ---- Bottle selection (more deterministic) ----
    # PDF example: "CAN250ML (taller can)" should prefer JSON key containing "METAL" + "250"
    want_metal = "can" in (bottle_raw or "").lower()
    ml = extract_ml(bottle_raw)

    bottle_key: Optional[str] = None

    # 1) Hard preference: CAN -> METAL + matching ml
    if want_metal and ml is not None:
        metal_candidates = [k for k in keys if ("metal" in k.lower() and str(ml) in norm_loose(k))]
        if metal_candidates:
            bottle_key = metal_candidates[0]

    # 2) Otherwise best loose match (scoring)
    if not bottle_key:
        bottle_key = best_key_loose(keys, bottle_raw)

    # 3) Match the base R-code when a proof includes a numeric dieline prefix
    #    or the JSON key contains alternate codes, for example:
    #    BOX 8-R0819146 -> BOX A8692 OR R0819146.
    if not bottle_key:
        raw_codes = {
            match.group(0).upper() for match in BOX_BASE_R_CODE_RE.finditer(bottle_raw or "")
        }
        code_candidates = [
            key
            for key in keys
            if raw_codes & {match.group(0).upper() for match in BOX_BASE_R_CODE_RE.finditer(key)}
        ]
        if code_candidates:
            bottle_key = min(code_candidates, key=lambda key: (len(norm_loose(key)), key))

    # 4) Treat spelled-out litre/liter as optional in compact bottle codes.
    #    Only use the primary value before descriptors such as "same as".
    if not bottle_key:
        primary_raw = re.split(r"(?i)\bsame\s+as\b", bottle_raw or "", maxsplit=1)[0]
        raw_unit_alias = norm_bottle_unit_alias(primary_raw)
        unit_candidates = [
            key for key in keys if raw_unit_alias and norm_bottle_unit_alias(key) == raw_unit_alias
        ]
        if unit_candidates:
            bottle_key = min(unit_candidates, key=lambda key: (len(norm_loose(key)), key))

    # 5) Final fallback: anything containing the ml
    if not bottle_key and ml is not None:
        ml_candidates = [k for k in keys if str(ml) in norm_loose(k)]
        if ml_candidates:
            # still prefer metal if possible
            if want_metal:
                metal = [k for k in ml_candidates if "metal" in k.lower()]
                bottle_key = metal[0] if metal else ml_candidates[0]
            else:
                bottle_key = ml_candidates[0]

    return bottle_key


def find_subcustomer_key(
    sub_keys: List[str],
    customer_raw: Optional[str],
    canonical: Optional[str],
    customers_map: Dict[str, List[str]],
) -> Optional[str]:
    del canonical, customers_map
    return find_best_customer_key(sub_keys, customer_raw)


def find_subcustomer_key_from_hint(sub_keys: List[str], hint: Optional[str]) -> Optional[str]:
    """Match a configured subcustomer explicitly named inside a filename or other hint."""
    hint_loose = norm_loose(hint)
    if not hint_loose:
        return None

    hint_words = set(re.findall(r"[a-z0-9]+", (hint or "").lower()))

    def key_matches(key: str) -> bool:
        key_loose = norm_loose(key)
        if key_loose and key_loose in hint_loose:
            return True

        words = [
            word
            for word in re.findall(r"[a-z0-9]+", key.lower())
            if word not in NOISE_CUSTOMER_TOKENS
        ]
        suffix_acronyms = {
            "".join(word[0] for word in words[start:])
            for start in range(len(words))
            if len(words) - start >= 3
        }
        return bool(suffix_acronyms & hint_words)

    matches = [key for key in sub_keys if key_matches(key)]
    if not matches:
        return None
    return max(matches, key=lambda key: (len(norm_loose(key)), key))


def is_parent_customer_variant(candidate: Optional[str], canonical: Optional[str]) -> bool:
    """Return whether a key is the canonical parent brand with only regional qualifiers."""
    if not candidate or not canonical:
        return False
    candidate_brand = customer_tokens(candidate) - REGION_TOKENS
    canonical_brand = customer_tokens(canonical) - REGION_TOKENS
    return bool(candidate_brand) and candidate_brand == canonical_brand


def default_url_resolution(
    default_urls: Iterable[str],
    canonical: Optional[str] = None,
    url_parent: Optional[str] = None,
    bottle_key: Optional[str] = None,
    subcustomer_key: Optional[str] = None,
) -> UrlResolution:
    return UrlResolution(
        urls=tuple(default_urls),
        canonical_customer=canonical,
        url_parent=url_parent,
        bottle_key=bottle_key,
        subcustomer_key=subcustomer_key,
        used_default_urls=True,
    )


def resolve_url_context(
    customer_raw: Optional[str],
    bottle_raw: Optional[str],
    customers_map: Dict[str, List[str]],
    customer_urls: CustomerUrlsType,
    default_urls: Iterable[str],
    *,
    subcustomer_hint: Optional[str] = None,
) -> UrlResolution:
    """
    Resolve parent, bottle key, subcustomer key, and URLs as one context.
    This same context is also used as the local route hint so folder lookup
    does not confuse CA/US variants that share the same job number.
    """
    if not customer_raw or not bottle_raw:
        LOG.warning("Missing customer or bottle; using default URLs.")
        return default_url_resolution(default_urls)

    canonical = resolve_canonical_customer(customer_raw, customers_map)
    if not canonical:
        LOG.warning("No canonical customer match for '%s'; using default URLs.", customer_raw)
        return default_url_resolution(default_urls)

    parent_names = []
    if canonical in customer_urls:
        parent_names.append(canonical)
    parent_names.extend(parent for parent in customer_urls.keys() if parent != canonical)

    candidates: List[Tuple[int, int, str, int, UrlResolution]] = []
    for parent in parent_names:
        parent_block = customer_urls.get(parent)
        if not isinstance(parent_block, dict):
            continue

        bottle_key = select_bottle_key(list(parent_block.keys()), bottle_raw)
        if not bottle_key:
            continue

        bottle_entry = parent_block.get(bottle_key)
        parent_bonus = 10000 if parent == canonical else 0

        # Case A: bottle directly maps to urls.
        direct = urls_from_entry(bottle_entry)
        if direct:
            resolution = UrlResolution(
                urls=tuple(direct),
                canonical_customer=canonical,
                url_parent=parent,
                bottle_key=bottle_key,
                subcustomer_key=None,
                used_default_urls=False,
            )
            candidates.append(
                (
                    parent_bonus + 1000,
                    len(norm_loose(bottle_key)),
                    parent,
                    len(candidates),
                    resolution,
                )
            )
            continue

        # Case B: bottle maps to subclient dict.
        if isinstance(bottle_entry, dict):
            sub_key = find_subcustomer_key(
                list(bottle_entry.keys()), customer_raw, canonical, customers_map
            )
            sub_match_value = customer_raw
            hint_key = find_subcustomer_key_from_hint(list(bottle_entry.keys()), subcustomer_hint)
            if hint_key and (
                not sub_key
                or (hint_key != sub_key and is_parent_customer_variant(sub_key, canonical))
            ):
                sub_key = hint_key
                sub_match_value = sub_key
                LOG.info(
                    "Subcustomer '%s' inferred from hint '%s'.",
                    sub_key,
                    subcustomer_hint,
                )
            if not sub_key:
                continue

            sub_entry = bottle_entry.get(sub_key)
            sub_urls = urls_from_entry(sub_entry)
            if not sub_urls:
                continue

            sub_score = max(score_customer_key(sub_key, sub_match_value), 0)
            resolution = UrlResolution(
                urls=tuple(sub_urls),
                canonical_customer=canonical,
                url_parent=parent,
                bottle_key=bottle_key,
                subcustomer_key=sub_key,
                used_default_urls=False,
            )
            candidates.append(
                (
                    parent_bonus + 5000 + sub_score,
                    len(norm_loose(bottle_key)),
                    parent,
                    len(candidates),
                    resolution,
                )
            )

    if candidates:
        candidates.sort(reverse=True)
        best = candidates[0][4]
        if best.url_parent != canonical:
            LOG.info(
                "URL parent '%s' selected for canonical customer '%s'.",
                best.url_parent,
                canonical,
            )
        return best

    LOG.warning(
        "No URL match for customer='%s' bottle='%s' canonical='%s'; using default URLs.",
        customer_raw,
        bottle_raw,
        canonical,
    )
    return default_url_resolution(default_urls, canonical=canonical)


def resolve_urls(
    customer_raw: Optional[str],
    bottle_raw: Optional[str],
    customers_map: Dict[str, List[str]],
    customer_urls: CustomerUrlsType,
    default_urls: Iterable[str],
) -> List[str]:
    """Backward-compatible URL-only wrapper."""
    context = resolve_url_context(
        customer_raw, bottle_raw, customers_map, customer_urls, default_urls
    )
    return list(context.urls)


# -----------------------------
# LOAD LIBRARY ROUTES
# -----------------------------
def load_library_routes(path: Path, default_library_root: Path) -> Tuple[List[dict], str, Path]:
    if not path.exists():
        return [], "{pdf_stem}", (default_library_root / "_Unsorted")

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, dict):
        raise ValueError("library_routes.json must be a JSON object (dictionary).")

    routes = raw.get("routes", [])
    if not isinstance(routes, list):
        raise ValueError("'routes' must be a list in library_routes.json.")

    folder_name_template = raw.get("folder_name_template", "{pdf_stem}")
    if not isinstance(folder_name_template, str) or not folder_name_template.strip():
        folder_name_template = "{pdf_stem}"

    def resolve_route_path(value: str) -> Path:
        candidate = Path(os.path.expandvars(value)).expanduser()
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        return candidate.resolve(strict=False)

    fallback_root_raw = raw.get("fallback_root")
    fallback_root = (
        resolve_route_path(fallback_root_raw)
        if isinstance(fallback_root_raw, str) and fallback_root_raw.strip()
        else (default_library_root / "_Unsorted")
    )

    cleaned_routes: List[dict] = []
    for r in routes:
        if not isinstance(r, dict):
            continue
        m = r.get("match")
        rt = r.get("root")
        if isinstance(m, str) and m.strip() and isinstance(rt, str) and rt.strip():
            cleaned_routes.append({"match": m.strip(), "root": str(resolve_route_path(rt))})

    return cleaned_routes, folder_name_template, fallback_root


def route_root_for_customer(customer_raw: Optional[str], routes: List[dict]) -> Optional[Path]:
    if not customer_raw:
        return None
    c = norm(customer_raw)
    best: Optional[Tuple[int, int, str, Path, str]] = None
    for r in routes:
        match = r["match"]
        m = norm(match)
        score = -1
        if c == m:
            score = 100000 + len(m)
        elif c.startswith(m):
            score = 80000 + len(m)
        else:
            score = score_customer_key(match, customer_raw)

        if score >= 0:
            candidate = (score, len(norm_loose(match)), norm(match), Path(r["root"]), match)
            if best is None or candidate > best:
                best = candidate
    if best:
        LOG.info("Route match: customer='%s' via '%s' -> '%s'", customer_raw, best[4], best[3])
        return best[3]
    LOG.warning("No route match for customer='%s'.", customer_raw)
    return None


# -----------------------------
# PDF TEXT EXTRACTION
# -----------------------------
def extract_pdf_text(pdf_path: Path) -> str:
    try:
        import pdfplumber  # type: ignore

        text_parts = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t.strip():
                    text_parts.append(t)
        text = "\n".join(text_parts).strip()
        if text:
            return text
    except ModuleNotFoundError:
        pass
    except Exception:
        pass

    try:
        try:
            from pypdf import PdfReader  # type: ignore
        except ModuleNotFoundError:
            # Backward compatibility for existing workstations.
            from PyPDF2 import PdfReader  # type: ignore

        reader = PdfReader(str(pdf_path))
        text_parts = []
        for p in reader.pages:
            t = p.extract_text() or ""
            if t.strip():
                text_parts.append(t)
        text = "\n".join(text_parts).strip()
        if text:
            return text
    except ModuleNotFoundError as e:
        raise RuntimeError(
            "Missing PDF parser dependency.\n"
            "Install one of these and rerun:\n"
            "  python -m pip install pdfplumber\n"
            "  python -m pip install pypdf\n"
        ) from e

    raise RuntimeError("PDF text extraction failed or returned empty text.")


def parse_customer_and_bottle(pdf_text: str) -> Tuple[Optional[str], Optional[str]]:
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in pdf_text.splitlines()]
    lines = [ln for ln in lines if ln]

    def find_values(label: str) -> List[Tuple[str, str]]:
        values: List[Tuple[str, str]] = []
        for ln in lines:
            m = re.search(rf"(?i)\b{re.escape(label)}\b\s*[:\-]\s*(.+)$", ln)
            if m:
                values.append((m.group(1).strip(), ln))

        label_u = label.upper()
        for i, ln in enumerate(lines[:-1]):
            if ln.upper() == label_u:
                values.append((lines[i + 1].strip(), ln))

        return values

    def choose_customer_value(values: List[Tuple[str, str]]) -> Optional[str]:
        cleaned = [(clean_customer_value(value), source_line) for value, source_line in values]
        cleaned = [
            (value, source_line)
            for value, source_line in cleaned
            if value and not is_internal_customer_value(value)
        ]
        if not cleaned:
            return None

        for value, source_line in cleaned:
            if re.search(r"(?i)\bQA/RA\b|PLEASE INDICATE", source_line):
                return value

        return cleaned[0][0]

    customer = choose_customer_value(find_values("CUSTOMER"))
    bottle_values = find_values("BOTTLE")
    bottle = clean_bottle_value(bottle_values[0][0]) if bottle_values else None
    box_bottle = find_box_bottle_value(lines, bottle)
    if box_bottle:
        bottle = box_bottle
    else:
        pouch_bottle = find_pouch_bottle_value(lines, bottle)
        if pouch_bottle:
            bottle = pouch_bottle
    return customer, bottle


# -----------------------------
# FILENAME / PICKER
# -----------------------------
def parse_job_id_from_filename(pdf_path: Path) -> Optional[str]:
    m = re.match(r"^(\d+)", pdf_path.stem)
    return m.group(1) if m else None


def is_auxiliary_job_pdf(pdf_path: Path) -> bool:
    stem = pdf_path.stem.lower()
    if "dieline" in stem or "ifc-style" in stem:
        return True
    return bool(re.search(r"(?:^|[ _-])(?:b-)?dl(?:$|[ _-])", stem))


def pick_pdf(source_folder: Path, pick_pdf_mode: str) -> Path:
    pdfs = sorted(source_folder.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"No PDFs found in SourceFolder: {source_folder}")

    job_pdfs = [p for p in pdfs if re.match(r"^\d+", p.stem)]
    if not job_pdfs:
        raise ValueError(
            f"No digit-prefixed job PDF found in SourceFolder. Found: {[p.name for p in pdfs]}"
        )

    proof_pdfs = [p for p in job_pdfs if not is_auxiliary_job_pdf(p)]
    if proof_pdfs:
        job_pdfs = proof_pdfs

    if pick_pdf_mode.lower() == "first":
        return sorted(job_pdfs)[0]

    return max(job_pdfs, key=lambda p: p.stat().st_mtime)


# -----------------------------
# LIBRARY SEARCH + CREATE + COPY
# -----------------------------
REV_SUFFIX_RE = re.compile(r"(?i)_r\d+$")


def strip_revision_suffix(name: str) -> str:
    # removes trailing "_R8", "_r0", etc.
    return REV_SUFFIX_RE.sub("", name)


def is_job_folder_match(folder_name: str, job_id: str, strict_prefix_match: bool) -> bool:
    if not folder_name.startswith(job_id):
        return False
    if not strict_prefix_match:
        return True
    if len(folder_name) == len(job_id):
        return True
    return folder_name[len(job_id)] in (" ", "_", "-")


def find_job_folders(library_folder: Path, job_id: str, strict_prefix_match: bool) -> List[Path]:
    matches: List[Path] = []
    for root, dirs, _files in os.walk(library_folder):
        for d in dirs:
            if is_job_folder_match(d, job_id, strict_prefix_match):
                matches.append(Path(root) / d)
    return sorted(matches, key=lambda p: str(p).lower())


def find_job_folder(
    library_folder: Path,
    job_id: str,
    strict_prefix_match: bool,
    preferred_folder_name: Optional[str] = None,
) -> Optional[Path]:
    matches = find_job_folders(library_folder, job_id, strict_prefix_match)
    if not matches:
        return None

    if preferred_folder_name:
        exact = [p for p in matches if p.name.lower() == preferred_folder_name.lower()]
        if exact:
            return exact[0]

    if len(matches) == 1:
        return matches[0]

    LOG.warning(
        "Multiple folders with job id '%s' under '%s'; only an exact folder-name match will be reused.",
        job_id,
        library_folder,
    )
    for p in matches[:10]:
        LOG.warning("  candidate: %s", p)
    if len(matches) > 10:
        LOG.warning("  ... and %d more", len(matches) - 10)
    return None


def safe_folder_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip().strip(".")


def render_folder_name(
    template: str, *, pdf_stem: str, job_id: str, bottle: str, customer: str
) -> str:
    pdf_stem_clean = strip_revision_suffix(pdf_stem)

    rendered = template.format(
        pdf_stem=pdf_stem_clean,
        job_id=job_id,
        bottle=bottle,
        customer=customer,
    )
    return safe_folder_name(rendered)


def ensure_job_folder(
    library_root: Path,
    job_id: str,
    pdf_path: Path,
    bottle_norm: Optional[str],
    customer_raw: Optional[str],
    route_customer: Optional[str],
    routes: List[dict],
    folder_name_template: str,
    fallback_root: Path,
    strict_prefix_match: bool,
) -> Tuple[Path, bool]:
    """
    Returns: (job_folder_path, created_new)
    """
    customer_for_route = route_customer or customer_raw
    create_root = (
        route_root_for_customer(customer_for_route, routes) if customer_for_route else None
    )
    if create_root is None:
        create_root = fallback_root

    folder_name = render_folder_name(
        folder_name_template,
        pdf_stem=pdf_path.stem,
        job_id=job_id,
        bottle=bottle_norm or "",
        customer=norm(customer_raw) if customer_raw else "",
    )

    dest = create_root / folder_name
    if dest.exists():
        return dest, False

    found = find_job_folder(
        create_root, job_id, strict_prefix_match, preferred_folder_name=folder_name
    )
    if found:
        return found, False

    dest.mkdir(parents=True, exist_ok=True)
    return dest, True


def copy_job_files_to_folder(
    source_folder: Path,
    job_id: str,
    dest_folder: Path,
    overwrite_on_copy: bool,
) -> None:
    patterns = ["*.pdf", "*.jpg", "*.jpeg", "*.JPG", "*.JPEG"]
    candidates: List[Path] = []
    for pat in patterns:
        candidates.extend(source_folder.glob(pat))

    # De-dupe by filename
    job_files: Dict[str, Path] = {}
    for p in candidates:
        if re.match(rf"^{re.escape(job_id)}", p.stem):
            job_files[p.name] = p

    for name, f in job_files.items():
        dst = dest_folder / name
        if dst.exists() and not overwrite_on_copy:
            continue
        shutil.copy2(str(f), str(dst))


# -----------------------------
# BOTTLE LIBRARY SUPPORT
# -----------------------------
def get_bottle_library_root(
    canonical_customer: Optional[str],
    config: Config,
) -> Optional[Path]:
    """Resolve a canonical customer to a configured bottle asset library."""
    if not canonical_customer or not config.bottle_libraries:
        return None

    exact = find_key_loose(list(config.bottle_libraries), canonical_customer)
    if exact:
        return config.bottle_libraries[exact]

    best = find_best_customer_key(list(config.bottle_libraries), canonical_customer)
    return config.bottle_libraries.get(best) if best else None


def looks_like_bottle_code(candidate: str) -> bool:
    if len(candidate) < 3:
        return False
    has_alpha = bool(re.search(r"[a-z]", candidate))
    has_digit = bool(re.search(r"\d", candidate))
    if has_alpha and has_digit:
        return True
    generic_prefixes = (
        "bot",
        "bo",
        "box",
        "hgr",
        "glass",
        "metal",
        "pouch",
        "tube",
        "jar",
    )
    return any(
        candidate.startswith(prefix) and len(candidate) > len(prefix) for prefix in generic_prefixes
    )


def bottle_code_candidates(*values: Optional[str]) -> List[str]:
    """
    Build practical lookup candidates for consolidated .blend files.

    PDF BOTTLE values often include production descriptors, for example
    "HGR300W PEEL BACK", while the library file only contains "HGR300W".
    """
    candidates: List[str] = []
    seen: set[str] = set()

    def add(value: Optional[str]) -> None:
        loose = norm_loose(value)
        if not loose or loose in seen:
            return
        seen.add(loose)
        candidates.append(loose)

    for value in values:
        if not value:
            continue

        add(value)

        for part in re.split(r"(?i)\bor\b", value):
            add(part)

        tokens = re.findall(r"[a-zA-Z0-9.]+", value)
        for token in tokens:
            loose = norm_loose(token)
            if looks_like_bottle_code(loose):
                add(token)

        # Add short token windows so codes split by spaces still match, such
        # as "BOT 625 PETCL" or "BOT 1.5 LITRE WH".
        max_window = 5
        for i in range(len(tokens)):
            for j in range(i + 2, min(len(tokens), i + max_window) + 1):
                window = " ".join(tokens[i:j])
                loose = norm_loose(window)
                if looks_like_bottle_code(loose):
                    add(window)

    return candidates


def find_best_bottle_blend(
    bottle_lib_root: Path, bottle_candidates: Iterable[str]
) -> Optional[Path]:
    """
    Finds a .blend file whose name contains one of the bottle candidates.
    Example candidates: ["hgr175w", "hgr175wpeelback"]
    """
    targets = [norm_loose(c) for c in bottle_candidates if norm_loose(c)]
    if not targets:
        return None

    best: Optional[Path] = None
    best_score = -1
    for root, _dirs, files in os.walk(bottle_lib_root):
        for fn in files:
            if not fn.lower().endswith(".blend"):
                continue
            fn_norm = norm_loose(Path(fn).stem)
            score = -1
            for target in targets:
                if fn_norm == target:
                    score = max(score, 1000 + len(target))
                elif target in fn_norm:
                    score = max(score, 500 + len(target))
                elif fn_norm in target:
                    score = max(score, 100 + len(fn_norm))

            if score < 0:
                continue

            p = Path(root) / fn
            if score > best_score or (
                score == best_score and best is not None and len(fn) < len(best.name)
            ):
                best = p
                best_score = score
    return best


def find_hdri_file(bottle_lib_root: Path, hdri_filename: str) -> Optional[Path]:
    target = hdri_filename.lower()
    for root, _dirs, files in os.walk(bottle_lib_root):
        for fn in files:
            if fn.lower() == target:
                return Path(root) / fn
    return None


def copy_extra_bottle_assets_if_new(
    created_new: bool,
    dest_folder: Path,
    canonical_customer: Optional[str],
    bottle_raw: Optional[str],
    bottle_norm: Optional[str],
    resolved_bottle_key: Optional[str],
    config: Config,
) -> None:
    """
    When a new job folder is created:
      - open bottle library folder in explorer
      - copy matching .blend and scifi_room_hdri.jpg into dest_folder
    When an existing job folder is reused, only fill missing .blend/HDRI files.
    """
    if not bottle_norm and not resolved_bottle_key:
        return

    existing_files = {p.name.lower() for p in dest_folder.iterdir() if p.is_file()}
    needs_blend = created_new or not any(
        p.suffix.lower() == ".blend" for p in dest_folder.iterdir() if p.is_file()
    )
    needs_hdri = created_new or config.hdri_filename.lower() not in existing_files
    if not needs_blend and not needs_hdri:
        return

    bottle_root = get_bottle_library_root(canonical_customer, config)
    if bottle_root is None:
        return
    if not bottle_root.exists():
        return

    if created_new:
        open_in_explorer(bottle_root)

    blend_candidates = bottle_code_candidates(
        bottle_raw,
        bottle_norm,
        resolved_bottle_key,
        dest_folder.name,
    )
    blend_path = find_best_bottle_blend(bottle_root, blend_candidates) if needs_blend else None
    hdri_path = find_hdri_file(bottle_root, config.hdri_filename) if needs_hdri else None

    if needs_blend and blend_path is None:
        LOG.warning(
            "No consolidated .blend match for bottle='%s' candidates=%s root='%s'.",
            bottle_raw or bottle_norm,
            blend_candidates,
            bottle_root,
        )

    for p in [blend_path, hdri_path]:
        if p is None:
            continue
        dst = dest_folder / p.name
        if dst.exists() and not config.overwrite_on_copy:
            continue
        shutil.copy2(str(p), str(dst))


# -----------------------------
# OPENERS
# -----------------------------
def open_in_explorer(path: Path) -> None:
    if sys.platform.startswith("win"):
        subprocess.run(["explorer.exe", str(path)], check=False)
    else:
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(path)], check=False)


def open_chrome_tabs(urls: List[str]) -> None:
    if not urls:
        return

    chrome_candidates: List[str] = []
    if sys.platform.startswith("win"):
        chrome_candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]

    chrome_path = next((p for p in chrome_candidates if Path(p).exists()), None)
    if chrome_path:
        subprocess.run([chrome_path, "--new-window", *urls], check=False)
        return

    import webbrowser

    webbrowser.open(urls[0], new=1)
    for u in urls[1:]:
        webbrowser.open(u, new=2)


# -----------------------------
# MAIN
# -----------------------------
def main(config: Optional[Config] = None) -> None:
    config = config or load_config()
    init_logging(config.log_level)
    source = config.source_folder.expanduser()
    library_root = config.library_folder.expanduser()

    if not source.exists():
        raise FileNotFoundError(f"SourceFolder does not exist: {source}")
    if not library_root.exists():
        raise FileNotFoundError(f"LibraryFolder does not exist: {library_root}")

    customers_map = load_customers_map(customers_map_path(config))
    customer_urls = load_customer_urls(customer_urls_path(config))

    routes, folder_name_template, fallback_root = load_library_routes(
        library_routes_path(config), library_root
    )

    pdf_path = pick_pdf(source, config.pick_pdf_mode)
    job_id = parse_job_id_from_filename(pdf_path)
    if not job_id:
        raise ValueError(f"Could not parse numeric job_id prefix from filename: {pdf_path.name}")

    pdf_text = extract_pdf_text(pdf_path)
    customer_raw, bottle_raw = parse_customer_and_bottle(pdf_text)
    LOG.info("Parsed CUSTOMER='%s' BOTTLE='%s' from '%s'", customer_raw, bottle_raw, pdf_path.name)

    bottle_n = norm_loose(bottle_raw) if bottle_raw else None

    # Resolve canonical customer (parent key) from customers_map.json
    canonical_customer = resolve_canonical_customer(customer_raw, customers_map)
    if canonical_customer:
        LOG.info("Canonical customer resolved: '%s'", canonical_customer)

    url_context = resolve_url_context(
        customer_raw,
        bottle_raw,
        customers_map,
        customer_urls,
        config.default_urls,
        subcustomer_hint=pdf_path.stem,
    )
    if url_context.canonical_customer:
        canonical_customer = url_context.canonical_customer
    LOG.info(
        "URL context: canonical='%s' url_parent='%s' bottle_key='%s' subcustomer='%s' defaults=%s",
        url_context.canonical_customer,
        url_context.url_parent,
        url_context.bottle_key,
        url_context.subcustomer_key,
        url_context.used_default_urls,
    )

    # 1) Find or create job folder
    route_customer = url_context.subcustomer_key or customer_raw
    job_folder, created_new = ensure_job_folder(
        library_root=library_root,
        job_id=job_id,
        pdf_path=pdf_path,
        bottle_norm=bottle_n,
        customer_raw=customer_raw,
        route_customer=route_customer,
        routes=routes,
        folder_name_template=folder_name_template,
        fallback_root=fallback_root,
        strict_prefix_match=config.strict_prefix_match,
    )

    # Always open the job folder and copy SourceFolder assets
    open_in_explorer(job_folder)
    copy_job_files_to_folder(source, job_id, job_folder, config.overwrite_on_copy)

    # If this was a newly created folder, also pull bottle assets (using canonical)
    copy_extra_bottle_assets_if_new(
        created_new,
        job_folder,
        canonical_customer,
        bottle_raw,
        bottle_n,
        url_context.bottle_key,
        config,
    )

    # 2) Open URLs based on:
    #    parent key (canonical) -> bottle -> subclient (customer_raw) -> [3 urls]
    urls_to_open = list(url_context.urls)
    if config.always_open_url and config.always_open_url.strip():
        urls_to_open.append(config.always_open_url.strip())

    open_chrome_tabs(urls_to_open)


if __name__ == "__main__":
    main()
