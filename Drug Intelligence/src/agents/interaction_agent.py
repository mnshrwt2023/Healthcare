from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List

import requests


class DrugInteractionAgent:
    """Retrieve drug interactions from NIH RxNav and FDA/openFDA label evidence."""

    RXNAV_RXCUI_URL = "https://rxnav.nlm.nih.gov/REST/rxcui.json"
    RXNAV_INTERACTION_URL = "https://rxnav.nlm.nih.gov/REST/interaction/list.json"
    OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"

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
        values: List[str] = []
        if selected_drug:
            for key in ("generic_name", "brand_name"):
                value = selected_drug.get(key)
                if value:
                    values.append(str(value))
        if query:
            values.append(query)

        deduped: List[str] = []
        seen = set()
        for value in values:
            normalized = re.sub(r"\s+", " ", value).strip()
            if normalized and normalized.lower() not in seen:
                seen.add(normalized.lower())
                deduped.append(normalized)
        return deduped

    def _lookup_rxcui(self, name: str) -> str | None:
        try:
            response = self.session.get(
                self.RXNAV_RXCUI_URL,
                params={"name": name, "search": 1},
                timeout=self.timeout,
            )
            if response.status_code != 200:
                return None
            payload = response.json()
        except (requests.RequestException, ValueError):
            return None

        rxnorm_ids = payload.get("idGroup", {}).get("rxnormId", []) or []
        return str(rxnorm_ids[0]) if rxnorm_ids else None

    @staticmethod
    def _severity_from_text(text: str, provided: str | None = None) -> str:
        if provided:
            return str(provided).strip().lower()
        lowered = text.lower()
        if any(token in lowered for token in ["contraindicated", "life-threatening", "fatal"]):
            return "high"
        if any(token in lowered for token in ["serious", "major", "severe"]):
            return "medium"
        return "low"

    def _rxnav_interactions(self, rxcui: str, checked_on: str) -> List[Dict[str, Any]]:
        try:
            response = self.session.get(
                self.RXNAV_INTERACTION_URL,
                params={"rxcuis": rxcui},
                timeout=self.timeout,
            )
            if response.status_code != 200:
                return []
            payload = response.json()
        except (requests.RequestException, ValueError):
            return []

        rows: List[Dict[str, Any]] = []
        groups = payload.get("fullInteractionTypeGroup", []) or []
        for group in groups:
            source_name = str(group.get("sourceName", "NIH RxNav")).strip() or "NIH RxNav"
            for interaction_type in group.get("fullInteractionType", []) or []:
                for pair in interaction_type.get("interactionPair", []) or []:
                    description = str(pair.get("description", "")).strip()
                    if not description:
                        continue

                    concepts = pair.get("interactionConcept", []) or []
                    interacting_drug = ""
                    if len(concepts) >= 2:
                        item = concepts[1].get("minConceptItem", {}) if isinstance(concepts[1], dict) else {}
                        interacting_drug = str(item.get("name", "")).strip()

                    rows.append(
                        {
                            "source": source_name,
                            "interacting_drug": interacting_drug or "See description",
                            "severity": self._severity_from_text(description, pair.get("severity")),
                            "mechanism": "Potential interaction",
                            "evidence_text": description[:1400],
                            "as_of": checked_on,
                            "url": f"{self.RXNAV_INTERACTION_URL}?rxcuis={rxcui}",
                            "notes": "NIH RxNav interaction pair",
                        }
                    )
                    if len(rows) >= 20:
                        return rows
        return rows

    def _fda_label_interactions(self, names: List[str]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        for name in names[:4]:
            escaped = name.replace('"', "")
            query = f'openfda.brand_name:"{escaped}" OR openfda.generic_name:"{escaped}"'
            try:
                response = self.session.get(
                    self.OPENFDA_LABEL_URL,
                    params={"search": query, "limit": 2},
                    timeout=self.timeout,
                )
                if response.status_code != 200:
                    continue
                payload = response.json()
            except (requests.RequestException, ValueError):
                continue

            for row in payload.get("results", []) or []:
                values = row.get("drug_interactions", [])
                if isinstance(values, str):
                    values = [values]
                if not isinstance(values, list):
                    continue

                for value in values:
                    text = str(value).strip()
                    if not text:
                        continue
                    rows.append(
                        {
                            "source": "U.S. FDA Label",
                            "interacting_drug": "See narrative",
                            "severity": self._severity_from_text(text),
                            "mechanism": "Label-documented interaction",
                            "evidence_text": text[:1400],
                            "as_of": str(row.get("effective_time", "")),
                            "url": response.url,
                            "notes": "openFDA drug_interactions field",
                        }
                    )
                if rows:
                    return rows

        return rows

    def lookup(
        self,
        query: str,
        selected_drug: Dict[str, Any] | None,
        products: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        del products
        checked_on = date.today().isoformat()
        names = self._candidate_names(query, selected_drug)

        source_logs: List[Dict[str, Any]] = []
        interactions: List[Dict[str, Any]] = []
        resolved_rxcui = ""

        for name in names[:5]:
            rxcui = self._lookup_rxcui(name)
            if not rxcui:
                source_logs.append(
                    {
                        "source": "NIH RxNav",
                        "url": self.RXNAV_RXCUI_URL,
                        "status": "no_match",
                        "details": f"No RxCUI resolved for '{name}'",
                        "checked_on": checked_on,
                    }
                )
                continue

            resolved_rxcui = rxcui
            rows = self._rxnav_interactions(rxcui, checked_on)
            interactions.extend(rows)
            source_logs.append(
                {
                    "source": "NIH RxNav",
                    "url": f"{self.RXNAV_INTERACTION_URL}?rxcuis={rxcui}",
                    "status": "success" if rows else "partial",
                    "details": f"Extracted {len(rows)} interaction finding(s) for RxCUI {rxcui}",
                    "checked_on": checked_on,
                }
            )
            if rows:
                break

        if not interactions:
            fda_rows = self._fda_label_interactions(names)
            interactions.extend(fda_rows)
            source_logs.append(
                {
                    "source": "U.S. FDA Label",
                    "url": self.OPENFDA_LABEL_URL,
                    "status": "success" if fda_rows else "no_match",
                    "details": f"Fallback extracted {len(fda_rows)} interaction finding(s)",
                    "checked_on": checked_on,
                }
            )

        deduped: Dict[str, Dict[str, Any]] = {}
        for row in interactions:
            key = "|".join([
                str(row.get("source", "")),
                str(row.get("interacting_drug", "")),
                str(row.get("evidence_text", ""))[:120],
            ])
            deduped[key] = row

        return {
            "agent": "Drug Interaction Intelligence",
            "query": query,
            "retrieved_on": checked_on,
            "rxcui": resolved_rxcui,
            "interactions": list(deduped.values()),
            "source_logs": source_logs,
            "candidate_names": names,
        }
