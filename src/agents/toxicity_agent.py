from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List

import requests


class DrugToxicityAgent:
    """Retrieve toxicity profiles using NIH PubChem with FDA label fallback context."""

    PUBCHEM_CID_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}/cids/JSON"
    PUBCHEM_RECORD_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
    OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"

    TOXICITY_HEADINGS = {
        "toxicity",
        "toxicological",
        "safety and hazards",
        "hazards identification",
        "ghs classification",
        "acute toxicity",
        "health hazards",
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
        values: List[str] = []
        if selected_drug:
            for key in ("generic_name", "brand_name", "substance_name"):
                value = selected_drug.get(key)
                if isinstance(value, list):
                    values.extend(str(item) for item in value if item)
                elif value:
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

    def _get_cid(self, name: str) -> int | None:
        url = self.PUBCHEM_CID_URL.format(name=requests.utils.quote(name))
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code != 200:
                return None
            payload = response.json()
        except (requests.RequestException, ValueError):
            return None

        cids = payload.get("IdentifierList", {}).get("CID", []) or []
        if not cids:
            return None
        return int(cids[0])

    def _get_pubchem_record(self, cid: int) -> Dict[str, Any] | None:
        url = self.PUBCHEM_RECORD_URL.format(cid=cid)
        try:
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code != 200:
                return None
            return response.json()
        except (requests.RequestException, ValueError):
            return None

    @staticmethod
    def _extract_information_text(info: Dict[str, Any]) -> str:
        value = info.get("Value", {}) if isinstance(info, dict) else {}
        if not isinstance(value, dict):
            return ""

        if "StringWithMarkup" in value:
            parts = [str(item.get("String", "")).strip() for item in value.get("StringWithMarkup", []) if item]
            return " ".join(part for part in parts if part)

        if "String" in value:
            return str(value.get("String", "")).strip()

        if "Number" in value:
            number = value.get("Number", [])
            if isinstance(number, list) and number:
                return str(number[0])

        return ""

    def _walk_sections(self, section: Dict[str, Any]) -> List[Dict[str, Any]]:
        sections = [section]
        for child in section.get("Section", []) or []:
            if isinstance(child, dict):
                sections.extend(self._walk_sections(child))
        return sections

    @staticmethod
    def _guess_severity(text: str) -> str:
        lowered = text.lower()
        if any(word in lowered for word in ["fatal", "life-threatening", "black box", "danger"]):
            return "high"
        if any(word in lowered for word in ["warning", "serious", "severe"]):
            return "medium"
        return "low"

    def _pubchem_profiles(self, cid: int, record: Dict[str, Any]) -> List[Dict[str, Any]]:
        profile_rows: List[Dict[str, Any]] = []
        root_sections = record.get("Record", {}).get("Section", []) or []
        section_nodes: List[Dict[str, Any]] = []
        for root in root_sections:
            if isinstance(root, dict):
                section_nodes.extend(self._walk_sections(root))

        for node in section_nodes:
            heading = str(node.get("TOCHeading", "")).strip()
            heading_lower = heading.lower()
            if not heading:
                continue
            if not any(token in heading_lower for token in self.TOXICITY_HEADINGS):
                continue

            for info in node.get("Information", []) or []:
                text = self._extract_information_text(info)
                if not text:
                    continue
                profile_rows.append(
                    {
                        "source": "NIH PubChem",
                        "profile_type": heading,
                        "finding": text[:1200],
                        "severity": self._guess_severity(text),
                        "as_of": date.today().isoformat(),
                        "url": self.PUBCHEM_RECORD_URL.format(cid=cid),
                        "notes": "PubChem record section extraction",
                    }
                )
                if len(profile_rows) >= 12:
                    return profile_rows

        return profile_rows

    def _fda_fallback_profiles(self, names: List[str]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        for name in names[:4]:
            escaped = name.replace('"', "")
            query = f'openfda.brand_name:"{escaped}" OR openfda.generic_name:"{escaped}"'
            try:
                response = self.session.get(
                    self.OPENFDA_LABEL_URL,
                    params={"search": query, "limit": 1},
                    timeout=self.timeout,
                )
                if response.status_code != 200:
                    continue
                payload = response.json()
            except (requests.RequestException, ValueError):
                continue

            result = (payload.get("results", []) or [None])[0]
            if not result:
                continue

            for field in ("boxed_warning", "warnings_and_precautions", "warnings"):
                value = result.get(field, [])
                if isinstance(value, str):
                    value = [value]
                if not isinstance(value, list):
                    continue
                for item in value:
                    text = str(item).strip()
                    if not text:
                        continue
                    rows.append(
                        {
                            "source": "U.S. FDA Label",
                            "profile_type": field,
                            "finding": text[:1200],
                            "severity": self._guess_severity(text),
                            "as_of": str(result.get("effective_time", "")),
                            "url": response.url,
                            "notes": "FDA label safety section fallback",
                        }
                    )
            if rows:
                break

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
        profiles: List[Dict[str, Any]] = []
        selected_cid: int | None = None

        for name in names[:5]:
            cid = self._get_cid(name)
            if cid is None:
                source_logs.append(
                    {
                        "source": "NIH PubChem",
                        "url": self.PUBCHEM_CID_URL.format(name=requests.utils.quote(name)),
                        "status": "no_match",
                        "details": f"No CID resolved for '{name}'",
                        "checked_on": checked_on,
                    }
                )
                continue

            record = self._get_pubchem_record(cid)
            if not record:
                source_logs.append(
                    {
                        "source": "NIH PubChem",
                        "url": self.PUBCHEM_RECORD_URL.format(cid=cid),
                        "status": "error",
                        "details": f"Could not retrieve record for CID {cid}",
                        "checked_on": checked_on,
                    }
                )
                continue

            selected_cid = cid
            profiles = self._pubchem_profiles(cid, record)
            source_logs.append(
                {
                    "source": "NIH PubChem",
                    "url": self.PUBCHEM_RECORD_URL.format(cid=cid),
                    "status": "success" if profiles else "partial",
                    "details": f"Extracted {len(profiles)} toxicity findings for CID {cid}",
                    "checked_on": checked_on,
                }
            )
            if profiles:
                break

        if not profiles:
            fda_rows = self._fda_fallback_profiles(names)
            profiles.extend(fda_rows)
            source_logs.append(
                {
                    "source": "U.S. FDA Label",
                    "url": self.OPENFDA_LABEL_URL,
                    "status": "success" if fda_rows else "no_match",
                    "details": f"Fallback extracted {len(fda_rows)} toxicity-context findings",
                    "checked_on": checked_on,
                }
            )

        deduped: Dict[str, Dict[str, Any]] = {}
        for row in profiles:
            key = "|".join([
                str(row.get("source", "")),
                str(row.get("profile_type", "")),
                str(row.get("finding", ""))[:120],
            ])
            deduped[key] = row

        return {
            "agent": "Drug Toxicity Intelligence",
            "query": query,
            "retrieved_on": checked_on,
            "cid": selected_cid,
            "profiles": list(deduped.values()),
            "source_logs": source_logs,
            "candidate_names": names,
        }
