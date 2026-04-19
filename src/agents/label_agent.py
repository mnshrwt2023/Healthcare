from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Tuple

import requests


class DrugLabelIntelligenceAgent:
    """Pull FDA label sections for clinical monograph rendering."""

    OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"
    MAX_SECTION_CHARS = 2600

    SECTION_FIELD_MAP = {
        "indications_and_usage": ["indications_and_usage", "purpose", "description"],
        "contraindications": ["contraindications", "do_not_use", "when_using"],
        "adverse_reactions": ["adverse_reactions", "warnings_and_precautions", "warnings"],
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
    def _candidate_names(query: str, selected_drug: Dict[str, Any] | None) -> List[str]:
        names: List[str] = []
        if selected_drug:
            for key in ("brand_name", "generic_name", "substance_name"):
                value = selected_drug.get(key)
                if isinstance(value, list):
                    for item in value:
                        if item:
                            names.append(str(item))
                elif value:
                    names.append(str(value))

        if query:
            names.append(query)

        deduped: List[str] = []
        seen = set()
        for name in names:
            normalized = re.sub(r"\s+", " ", str(name)).strip()
            key = normalized.lower()
            if normalized and key not in seen:
                seen.add(key)
                deduped.append(normalized)

        return deduped

    @staticmethod
    def _candidate_ndcs(products: List[Dict[str, Any]], selected_drug: Dict[str, Any] | None) -> List[str]:
        ndcs: List[str] = []
        if selected_drug and selected_drug.get("product_ndc"):
            ndcs.append(str(selected_drug.get("product_ndc")))

        for product in products:
            product_ndc = product.get("product_ndc")
            if product_ndc:
                ndcs.append(str(product_ndc))
            for packaging in product.get("packaging", []) or []:
                package_ndc = packaging.get("package_ndc")
                if package_ndc:
                    ndcs.append(str(package_ndc))

        deduped: List[str] = []
        seen = set()
        for ndc in ndcs:
            cleaned = ndc.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                deduped.append(cleaned)

        return deduped

    def _label_search(self, search_query: str, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            response = self.session.get(
                self.OPENFDA_LABEL_URL,
                params={"search": search_query, "limit": limit},
                timeout=self.timeout,
            )
        except requests.RequestException:
            return []

        if response.status_code != 200:
            return []

        try:
            payload = response.json()
        except ValueError:
            return []

        return payload.get("results", [])

    @staticmethod
    def _strip_reference_markers(text: str) -> str:
        cleaned = text.translate(str.maketrans("", "", "\u00b9\u00b2\u00b3\u2070\u2074\u2075\u2076\u2077\u2078\u2079"))
        cleaned = re.sub(r"\[(?:\d+|[A-Za-z])\]", "", cleaned)
        cleaned = re.sub(r"(?<=\w)\((?:\d+|[A-Za-z])\)", "", cleaned)
        return cleaned

    @staticmethod
    def _collapse_text(values: List[str]) -> str:
        text = "\n\n".join(v.strip() for v in values if v.strip())
        text = DrugLabelIntelligenceAgent._strip_reference_markers(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _truncate_text(text: str, limit: int) -> Tuple[str, bool]:
        if len(text) <= limit:
            return text, False

        sentence_matches = list(re.finditer(r"[.!?](?:\s|$)", text))
        for match in reversed(sentence_matches):
            candidate = text[: match.end()].rstrip()
            if len(candidate) <= limit:
                return candidate, True

        return text[: limit - 3].rstrip() + "...", True

    def _section_from_fields(
        self,
        row: Dict[str, Any],
        fields: List[str],
        max_chars: int | None = None,
    ) -> Dict[str, Any]:
        limit = max_chars if max_chars is not None else self.MAX_SECTION_CHARS

        for field in fields:
            raw_value = row.get(field)
            if isinstance(raw_value, str):
                raw_value = [raw_value]
            if not isinstance(raw_value, list):
                continue

            merged = self._collapse_text([str(item) for item in raw_value])
            if not merged:
                continue

            original_length = len(merged)
            if original_length <= limit:
                return {
                    "text": merged,
                    "source_field": field,
                    "was_truncated": False,
                    "original_length": original_length,
                }

            truncated_text, was_truncated = self._truncate_text(merged, limit)
            return {
                "text": truncated_text,
                "source_field": field,
                "was_truncated": was_truncated,
                "original_length": original_length,
            }

        return {
            "text": "",
            "source_field": "",
            "was_truncated": False,
            "original_length": 0,
        }

    def _dedupe_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            openfda = row.get("openfda", {}) if isinstance(row.get("openfda"), dict) else {}
            spl_set_id = ",".join(openfda.get("spl_set_id", []) or [])
            product_ndc = ",".join(openfda.get("product_ndc", []) or [])
            key = "|".join(
                [
                    str(row.get("id", "")),
                    str(row.get("effective_time", "")),
                    spl_set_id,
                    product_ndc,
                ]
            )
            deduped[key] = row
        return list(deduped.values())

    def _row_section_coverage(self, row: Dict[str, Any]) -> int:
        coverage = 0
        for fields in self.SECTION_FIELD_MAP.values():
            section = self._section_from_fields(row, fields, max_chars=10000)
            if section.get("text"):
                coverage += 1
        return coverage

    def _select_best_row(self, rows: List[Dict[str, Any]]) -> Tuple[Dict[str, Any] | None, Dict[str, Any]]:
        if not rows:
            return None, {
                "rows_considered": 0,
                "selected_coverage": 0,
                "selection_reason": "no_rows",
            }

        ranked = sorted(
            rows,
            key=lambda row: (
                self._row_section_coverage(row),
                str(row.get("effective_time", "")),
            ),
            reverse=True,
        )

        selected = ranked[0]
        coverage = self._row_section_coverage(selected)
        reason = "complete_section_coverage" if coverage == 3 else "partial_section_coverage"

        return selected, {
            "rows_considered": len(rows),
            "selected_coverage": coverage,
            "selection_reason": reason,
            "selected_effective_time": str(selected.get("effective_time", "")),
        }

    def lookup(
        self,
        query: str,
        selected_drug: Dict[str, Any] | None,
        products: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        checked_on = date.today().isoformat()
        names = self._candidate_names(query, selected_drug)
        ndcs = self._candidate_ndcs(products, selected_drug)

        rows: List[Dict[str, Any]] = []
        source_logs: List[Dict[str, Any]] = []

        for ndc in ndcs[:10]:
            search = f'openfda.product_ndc:"{ndc}" OR openfda.package_ndc:"{ndc}"'
            matched_rows = self._label_search(search, limit=5)
            if matched_rows:
                rows.extend(matched_rows)
                source_logs.append(
                    {
                        "source": "openFDA Drug Label",
                        "url": self.OPENFDA_LABEL_URL,
                        "status": "success",
                        "details": f"Matched {len(matched_rows)} label row(s) by NDC {ndc}",
                        "checked_on": checked_on,
                    }
                )

        if not rows:
            for name in names[:6]:
                escaped = name.replace('"', "")
                search = f'openfda.brand_name:"{escaped}" OR openfda.generic_name:"{escaped}"'
                matched_rows = self._label_search(search, limit=5)
                if matched_rows:
                    rows.extend(matched_rows)
                    source_logs.append(
                        {
                            "source": "openFDA Drug Label",
                            "url": self.OPENFDA_LABEL_URL,
                            "status": "success",
                            "details": f"Matched {len(matched_rows)} label row(s) by name '{name}'",
                            "checked_on": checked_on,
                        }
                    )

        if not rows:
            source_logs.append(
                {
                    "source": "openFDA Drug Label",
                    "url": self.OPENFDA_LABEL_URL,
                    "status": "no_match",
                    "details": "No label rows matched candidate names or NDC values",
                    "checked_on": checked_on,
                }
            )

        rows = self._dedupe_rows(rows)
        label_row, row_quality = self._select_best_row(rows)

        sections: Dict[str, str] = {}
        section_quality: Dict[str, Dict[str, Any]] = {}
        for key, fields in self.SECTION_FIELD_MAP.items():
            section_data = self._section_from_fields(label_row or {}, fields)
            sections[key] = str(section_data.get("text", ""))
            section_quality[key] = {
                "status": (
                    "missing"
                    if not section_data.get("text")
                    else ("truncated" if section_data.get("was_truncated") else "complete")
                ),
                "source_field": section_data.get("source_field", ""),
                "was_truncated": bool(section_data.get("was_truncated", False)),
                "original_length": int(section_data.get("original_length", 0)),
                "preview_length": len(str(section_data.get("text", ""))),
            }

        if label_row and row_quality.get("selected_coverage", 0) < 3:
            source_logs.append(
                {
                    "source": "openFDA Drug Label",
                    "url": self.OPENFDA_LABEL_URL,
                    "status": "partial_section_coverage",
                    "details": (
                        f"Best label row has {row_quality.get('selected_coverage', 0)}/3 target sections; "
                        "fallback fields were applied"
                    ),
                    "checked_on": checked_on,
                }
            )

        openfda_meta = label_row.get("openfda", {}) if label_row else {}
        return {
            "agent": "Drug Label Intelligence",
            "query": query,
            "retrieved_on": checked_on,
            "as_of": (label_row or {}).get("effective_time", ""),
            "sections": sections,
            "section_quality": section_quality,
            "label_row_quality": row_quality,
            "label_metadata": {
                "brand_name": ", ".join(openfda_meta.get("brand_name", []) or []),
                "generic_name": ", ".join(openfda_meta.get("generic_name", []) or []),
                "manufacturer_name": ", ".join(openfda_meta.get("manufacturer_name", []) or []),
                "spl_set_id": ", ".join(openfda_meta.get("spl_set_id", []) or []),
            },
            "source_logs": source_logs,
            "candidate_names": names,
            "candidate_ndcs": ndcs,
        }
