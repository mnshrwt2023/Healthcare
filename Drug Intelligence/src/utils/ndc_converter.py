from __future__ import annotations

from functools import lru_cache
import re
from typing import Any, Dict, List, Sequence

import pandas as pd
import requests


SUPERSCRIPT_DIGITS = str.maketrans("", "", "\u00b9\u00b2\u00b3\u2070\u2074\u2075\u2076\u2077\u2078\u2079")
NDC_LIKE_PATTERN = re.compile(r"^\d{4,5}-\d{3,4}-\d{1,2}$|^\d{9,11}$")
DIRECT_SEGMENT_PATTERNS = {
    (4, 4, 1),
    (4, 4, 2),
    (4, 3, 2),
    (5, 3, 1),
    (5, 3, 2),
    (5, 4, 1),
    (5, 4, 2),
}
RAW_SEGMENT_PATTERNS = {
    9: ((4, 4, 1), (4, 3, 2), (5, 3, 1)),
    10: ((4, 4, 2), (5, 3, 2), (5, 4, 1)),
}
OPENFDA_NDC_URL = "https://api.fda.gov/drug/ndc.json"
OPENFDA_HEADERS = {
    "User-Agent": "DrugResearchWorkbench/1.0 (+streamlit)",
    "Accept": "application/json",
}
OPENFDA_TIMEOUT = 15


def sanitize_ndc_value(value: Any) -> str:
    text = str(value or "").strip().translate(SUPERSCRIPT_DIGITS)
    text = re.sub(r"\s+", "", text)
    return text


def normalize_hyphenated_ndc(cleaned: str) -> str | None:
    parts = cleaned.split("-")
    if len(parts) != 3:
        return None

    a, b, c = parts
    lengths = (len(a), len(b), len(c))
    if lengths not in DIRECT_SEGMENT_PATTERNS:
        return None
    if not (a.isdigit() and b.isdigit() and c.isdigit()):
        return None
    if len(c) > 2:
        return None

    return f"{a.zfill(5)}{b.zfill(4)}{c.zfill(2)}"


def normalize_ndc_to_11_digits(value: Any) -> str | None:
    cleaned = sanitize_ndc_value(value)
    if not cleaned:
        return None
    if cleaned.isdigit() and len(cleaned) == 11:
        return cleaned
    if cleaned.isdigit() and len(cleaned) in {9, 10}:
        return None
    if "-" not in cleaned:
        return None
    return normalize_hyphenated_ndc(cleaned)


def generate_raw_digit_candidates(cleaned: str) -> List[Dict[str, str]]:
    patterns = RAW_SEGMENT_PATTERNS.get(len(cleaned), ())
    candidates: List[Dict[str, str]] = []
    for pattern in patterns:
        first, second, third = pattern
        if first + second + third != len(cleaned):
            continue
        a = cleaned[:first]
        b = cleaned[first : first + second]
        c = cleaned[first + second :]
        hyphenated = f"{a}-{b}-{c}"
        normalized = normalize_hyphenated_ndc(hyphenated)
        if normalized:
            candidates.append(
                {
                    "pattern": f"{first}-{second}-{third}",
                    "hyphenated": hyphenated,
                    "ndc_11": normalized,
                }
            )
    return candidates


def hyphenated_lookup_candidates(cleaned: str, normalized: str) -> List[str]:
    candidates: List[str] = []

    def add_candidate(value: str) -> None:
        value = value.strip()
        if value and value not in candidates:
            candidates.append(value)

    if "-" in cleaned:
        add_candidate(cleaned)

    if normalized.isdigit() and len(normalized) == 11:
        segments = [normalized[:5], normalized[5:9], normalized[9:]]
        add_candidate(normalized)
        add_candidate("-".join(segments))
        add_candidate("-".join(segments[:2]))
        for index, segment in enumerate(segments):
            if not segment.startswith("0"):
                continue
            candidate_segments = list(segments)
            candidate_segments[index] = segment[1:]
            candidate = "-".join(candidate_segments)
            if NDC_LIKE_PATTERN.match(candidate):
                add_candidate(candidate)
                if len(candidate_segments[2]) <= 2:
                    add_candidate("-".join(candidate_segments[:2]))

    return candidates


def format_fda_drug_name(row: Dict[str, Any]) -> str:
    brand_name = str(row.get("brand_name", "")).strip()
    generic_name = str(row.get("generic_name", "")).strip()
    if brand_name and generic_name and brand_name.lower() != generic_name.lower():
        return f"{brand_name} / {generic_name}"
    return brand_name or generic_name


@lru_cache(maxsize=1024)
def lookup_fda_drug_name(search_term: str) -> str:
    if not search_term:
        return ""

    try:
        response = requests.get(
            OPENFDA_NDC_URL,
            params={
                "search": f'product_ndc:"{search_term}" OR packaging.package_ndc:"{search_term}"',
                "limit": 1,
            },
            headers=OPENFDA_HEADERS,
            timeout=OPENFDA_TIMEOUT,
        )
    except requests.RequestException:
        return ""

    if response.status_code != 200:
        return ""

    try:
        payload = response.json()
    except ValueError:
        return ""

    rows = payload.get("results", []) or []
    if not rows:
        return ""

    return format_fda_drug_name(rows[0])


@lru_cache(maxsize=512)
def lookup_fda_labeler_prefix(labeler_prefix: str) -> str:
    """Search openFDA by labeler prefix token; return a manufacturer + drug note when the
    specific NDC was not found but the labeler prefix is recognised."""
    if not labeler_prefix or not labeler_prefix.isdigit():
        return ""

    try:
        response = requests.get(
            OPENFDA_NDC_URL,
            params={"search": f"product_ndc:{labeler_prefix}", "limit": 5},
            headers=OPENFDA_HEADERS,
            timeout=OPENFDA_TIMEOUT,
        )
    except requests.RequestException:
        return ""

    if response.status_code != 200:
        return ""

    try:
        payload = response.json()
    except ValueError:
        return ""

    rows = payload.get("results", []) or []
    # Keep only rows whose product_ndc genuinely starts with this labeler prefix
    rows = [r for r in rows if str(r.get("product_ndc", "")).startswith(f"{labeler_prefix}-")]
    if not rows:
        return ""

    labeler_name = str(rows[0].get("labeler_name", "")).strip()
    seen_brands: set = set()
    drug_labels: List[str] = []
    for row in rows[:3]:
        brand = str(row.get("brand_name", "")).strip()
        if brand and brand.upper() not in seen_brands:
            seen_brands.add(brand.upper())
            generic = str(row.get("generic_name", "")).strip()
            drug_labels.append(f"{brand.upper()} ({generic})" if generic else brand.upper())

    drug_part = ", ".join(drug_labels) if drug_labels else ""
    if labeler_name and drug_part:
        return f"[Prefix {labeler_prefix}: {labeler_name} \u2014 {drug_part}]"
    if labeler_name:
        return f"[Prefix {labeler_prefix}: {labeler_name}]"
    return ""


def resolve_fda_drug_name(cleaned: str, normalized: str) -> str:
    for candidate in hyphenated_lookup_candidates(cleaned, normalized):
        drug_name = lookup_fda_drug_name(candidate)
        if drug_name:
            return drug_name
    # Fallback: labeler prefix note when exact NDC is not in openFDA
    labeler_prefix = cleaned.split("-")[0] if "-" in cleaned else (normalized[:5] if len(normalized) >= 5 else "")
    if labeler_prefix and labeler_prefix.isdigit():
        return lookup_fda_labeler_prefix(labeler_prefix)
    return ""


def blank_record() -> Dict[str, str]:
    return {
        "Input NDC": "",
        "11-Digit NDC": "",
        "FDA Drug Name": "",
        "Mode": "blank",
        "Status": "BLANK",
        "Reason": "Input was blank.",
        "Confidence": "NONE",
        "Validation Source": "NONE",
        "Candidate 1": "",
        "Candidate 2": "",
        "Candidate 3": "",
    }


def direct_record(cleaned: str, normalized: str, lookup_fda_name: bool = True) -> Dict[str, str]:
    status = "VALID" if cleaned == normalized else "CONVERTED"
    reason = "Already in 11-digit format." if status == "VALID" else "Converted to 11-digit format."
    drug_name = resolve_fda_drug_name(cleaned, normalized) if lookup_fda_name else ""
    return {
        "Input NDC": cleaned,
        "11-Digit NDC": normalized,
        "FDA Drug Name": drug_name,
        "Mode": "direct_conversion",
        "Status": status,
        "Reason": reason,
        "Confidence": "HIGH",
        "Validation Source": "FDA pattern",
        "Candidate 1": "",
        "Candidate 2": "",
        "Candidate 3": "",
    }


def review_record(cleaned: str, candidates: List[Dict[str, str]]) -> Dict[str, str]:
    candidate_values = [f"{candidate['pattern']} -> {candidate['ndc_11']}" for candidate in candidates]
    return {
        "Input NDC": cleaned,
        "11-Digit NDC": "",
        "FDA Drug Name": "",
        "Mode": "review_required",
        "Status": "AMBIGUOUS",
        "Reason": "Raw 9-10 digit input needs review before choosing one FDA-recognized segmentation.",
        "Confidence": "NONE",
        "Validation Source": "NONE",
        "Candidate 1": candidate_values[0] if len(candidate_values) > 0 else "",
        "Candidate 2": candidate_values[1] if len(candidate_values) > 1 else "",
        "Candidate 3": candidate_values[2] if len(candidate_values) > 2 else "",
    }


def invalid_record(cleaned: str, reason: str) -> Dict[str, str]:
    return {
        "Input NDC": cleaned,
        "11-Digit NDC": "",
        "FDA Drug Name": "",
        "Mode": "unsupported",
        "Status": "INVALID",
        "Reason": reason,
        "Confidence": "NONE",
        "Validation Source": "NONE",
        "Candidate 1": "",
        "Candidate 2": "",
        "Candidate 3": "",
    }


def convert_ndc_record(value: Any, lookup_fda_name: bool = True) -> Dict[str, str]:
    cleaned = sanitize_ndc_value(value)
    if not cleaned:
        return blank_record()

    normalized = normalize_ndc_to_11_digits(cleaned)
    if normalized:
        return direct_record(cleaned, normalized, lookup_fda_name=lookup_fda_name)

    if cleaned.isdigit() and len(cleaned) in {9, 10}:
        return review_record(cleaned, generate_raw_digit_candidates(cleaned))
    if cleaned.isdigit() and len(cleaned) == 8:
        return invalid_record(cleaned, "8-digit raw values are not supported by FDA-recognized NDC segmentation rules.")
    if NDC_LIKE_PATTERN.match(cleaned):
        return invalid_record(cleaned, "NDC format is not convertible with supported FDA segment patterns.")
    return invalid_record(cleaned, "Value is not a recognizable NDC format.")


def batch_convert_ndcs(values: Sequence[Any], lookup_fda_name: bool = True) -> List[Dict[str, str]]:
    return [convert_ndc_record(value, lookup_fda_name=lookup_fda_name) for value in values]


def detect_ndc_columns(df: pd.DataFrame, sample_size: int = 50) -> List[str]:
    candidates: List[tuple[int, str]] = []
    for column in df.columns:
        series = df[column].fillna("").astype(str)
        sample = [sanitize_ndc_value(item) for item in series.head(sample_size).tolist() if str(item).strip()]
        if not sample:
            continue

        score = 0
        lowered_name = str(column).lower()
        if "ndc" in lowered_name:
            score += 3
        if "package" in lowered_name or "product" in lowered_name:
            score += 1

        ndc_like = sum(1 for item in sample if NDC_LIKE_PATTERN.match(item))
        converted = sum(1 for item in sample if normalize_ndc_to_11_digits(item))
        raw_review = sum(1 for item in sample if item.isdigit() and len(item) in {9, 10})
        if ndc_like:
            score += ndc_like
        if converted:
            score += converted
        if raw_review:
            score += raw_review

        if score > 0:
            candidates.append((score, str(column)))

    candidates.sort(key=lambda item: (-item[0], item[1].lower()))
    return [column for _, column in candidates]