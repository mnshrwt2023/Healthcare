from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List

import requests


class DrugNDCAgent:
    """NDC-centric drug identity resolver using openFDA."""

    OPENFDA_NDC_URL = "https://api.fda.gov/drug/ndc.json"
    QUERY_STOPWORDS = {
        "a",
        "an",
        "and",
        "for",
        "from",
        "give",
        "me",
        "drug",
        "drugs",
        "of",
        "on",
        "please",
        "show",
        "the",
        "to",
        "with",
    }

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "DrugResearchWorkbench/1.0 (+streamlit)",
                "Accept": "application/json",
            }
        )

    @staticmethod
    def is_ndc_like(value: str) -> bool:
        cleaned = value.strip()
        return bool(re.fullmatch(r"\d{4,5}-?\d{3,4}-?\d{1,2}", cleaned))

    def _request(self, search_query: str, limit: int = 50) -> List[Dict[str, Any]]:
        params = {"search": search_query, "limit": limit}
        resp = self.session.get(self.OPENFDA_NDC_URL, params=params, timeout=self.timeout)
        if resp.status_code != 200:
            return []
        payload = resp.json()
        return payload.get("results", [])

    def _search_by_ndc(self, ndc_value: str, limit: int) -> List[Dict[str, Any]]:
        ndc_clean = ndc_value.strip()
        queries = [
            f'product_ndc:"{ndc_clean}"',
            f'packaging.package_ndc:"{ndc_clean}"',
        ]
        all_rows: List[Dict[str, Any]] = []
        for query in queries:
            all_rows.extend(self._request(query, limit=limit))

        deduped: Dict[str, Dict[str, Any]] = {}
        for row in all_rows:
            product_id = row.get("product_id") or row.get("product_ndc") or str(row)
            deduped[product_id] = row
        return list(deduped.values())

    def _search_by_name(self, name_value: str, limit: int) -> List[Dict[str, Any]]:
        name_clean = re.sub(r"\s+", " ", name_value.strip().replace('"', "")).strip()
        normalized = re.sub(r"[^A-Za-z0-9\-\s]", " ", name_clean.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()

        candidates: List[str] = []

        def add_candidate(value: str) -> None:
            value = re.sub(r"\s+", " ", value).strip()
            if value and value not in candidates:
                candidates.append(value)

        add_candidate(name_clean)

        tokens = [
            token
            for token in normalized.split()
            if token not in self.QUERY_STOPWORDS and len(token) > 1
        ]
        if tokens:
            add_candidate(" ".join(tokens))
            add_candidate(tokens[-1])
            if len(tokens) >= 2:
                add_candidate(" ".join(tokens[-2:]))

        deduped: Dict[str, Dict[str, Any]] = {}
        for candidate in candidates[:5]:
            escaped = candidate.replace('"', "")
            query_variants = [
                f'brand_name:"{escaped}" OR generic_name:"{escaped}"',
            ]
            if " " not in escaped:
                query_variants.extend(
                    [
                        f"brand_name:{escaped}",
                        f"generic_name:{escaped}",
                    ]
                )

            for query in query_variants:
                for row in self._request(query, limit=limit):
                    product_id = row.get("product_id") or row.get("product_ndc") or str(row)
                    deduped[product_id] = row
                if len(deduped) >= limit:
                    return list(deduped.values())[:limit]

        return list(deduped.values())[:limit]

    @staticmethod
    def _selected_drug(rows: List[Dict[str, Any]]) -> Dict[str, Any] | None:
        if not rows:
            return None
        finished_rows = [r for r in rows if r.get("finished") is True]
        candidates = finished_rows if finished_rows else rows
        candidates.sort(
            key=lambda r: (
                str(r.get("marketing_start_date", "")),
                str(r.get("listing_expiration_date", "")),
            ),
            reverse=True,
        )
        return candidates[0]

    @staticmethod
    def _summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        labelers: Dict[str, int] = {}
        dosage_forms: Dict[str, int] = {}
        routes: Dict[str, int] = {}

        for row in rows:
            labeler = row.get("labeler_name") or "Unknown"
            dosage = row.get("dosage_form") or "Unknown"

            labelers[labeler] = labelers.get(labeler, 0) + 1
            dosage_forms[dosage] = dosage_forms.get(dosage, 0) + 1

            for route in row.get("route", []) or []:
                route_name = route or "Unknown"
                routes[route_name] = routes.get(route_name, 0) + 1

        return {
            "total_products": len(rows),
            "labeler_counts": labelers,
            "dosage_form_counts": dosage_forms,
            "route_counts": routes,
        }

    @staticmethod
    def _extract_labeler_prefix(ndc_value: str) -> str | None:
        """Return the labeler segment (first hyphen-delimited part) of an NDC string."""
        clean = ndc_value.strip()
        parts = clean.split("-")
        if len(parts) >= 2 and parts[0].isdigit():
            return parts[0]
        # Unhyphenated: first 5 digits for 11-digit NDC, first 4-5 for 10-digit
        digits_only = re.sub(r"\D", "", clean)
        if len(digits_only) >= 9:
            return digits_only[:5]
        return None

    def _search_by_labeler_prefix(self, labeler_prefix: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Return products whose product_ndc begins with the given labeler prefix.

        openFDA tokenises product_ndc on hyphens, so searching the plain prefix token
        (e.g. product_ndc:0169) is the reliable way to find all NDCs from that labeler.
        We then post-filter to ensure only rows where product_ndc genuinely starts with
        '{labeler_prefix}-' are returned.
        """
        rows = self._request(f"product_ndc:{labeler_prefix}", limit=limit)
        # Post-filter: confirm the prefix occupies the labeler segment (before the first hyphen)
        filtered = [
            r for r in rows
            if str(r.get("product_ndc", "")).startswith(f"{labeler_prefix}-")
        ]
        return filtered if filtered else rows

    def resolve(self, query: str, limit: int = 50) -> Dict[str, Any]:
        query_clean = query.strip()
        ndc_prefix_match = False
        labeler_prefix: str | None = None

        if self.is_ndc_like(query_clean):
            rows = self._search_by_ndc(query_clean, limit=limit)
            search_mode = "ndc"
            if not rows:
                # Exact NDC not found — fall back to labeler prefix
                labeler_prefix = self._extract_labeler_prefix(query_clean)
                if labeler_prefix:
                    rows = self._search_by_labeler_prefix(labeler_prefix, limit=10)
                    if rows:
                        ndc_prefix_match = True
                        search_mode = "ndc_prefix_fallback"
        else:
            rows = self._search_by_name(query_clean, limit=limit)
            search_mode = "drug_name"

        selected = self._selected_drug(rows)

        return {
            "agent": "Drug NDC Assistant",
            "search_mode": search_mode,
            "query": query_clean,
            "resolved_on": date.today().isoformat(),
            "products": rows,
            "selected_drug": selected,
            "summary": self._summary(rows),
            "ndc_prefix_match": ndc_prefix_match,
            "labeler_prefix": labeler_prefix,
            "source": {
                "name": "openFDA Drug NDC API",
                "url": self.OPENFDA_NDC_URL,
            },
        }
