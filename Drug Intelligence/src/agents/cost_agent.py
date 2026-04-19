from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Tuple

import requests
from bs4 import BeautifulSoup

from src.utils.ndc_converter import normalize_ndc_to_11_digits


class DrugCostLookupAgent:
    """Cost lookup with source attribution and graceful fallbacks."""

    GOODRX_URL = "https://www.goodrx.com"
    NADAC_URL = "https://data.medicaid.gov/resource/tau9-gfwr.json"
    CMS_DATA_API_BASE = "https://data.cms.gov/data-api/v1/dataset"
    CMS_SPENDING_DATASETS = (
        {
            "id": "be64fce3-e835-4589-b46b-024198e524a6",
            "source": "CMS Medicaid Spending by Drug",
        },
        {
            "id": "7e0b4365-fd63-4a29-8f5e-e0ac9f66a81b",
            "source": "CMS Medicare Part D Spending by Drug",
        },
        {
            "id": "76a714ad-3a2c-43ac-b76d-9dadf8f7d890",
            "source": "CMS Medicare Part B Spending by Drug",
        },
    )
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
                "Accept": "application/json,text/html",
            }
        )

    @staticmethod
    def _slugify(name: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower())
        return slug.strip("-")

    @staticmethod
    def _extract_money_values(text: str) -> List[float]:
        pattern = r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?"
        raw = re.findall(pattern, text)
        values: List[float] = []
        for item in raw:
            normalized = item.replace("$", "").replace(",", "").strip()
            try:
                values.append(float(normalized))
            except ValueError:
                continue
        deduped = sorted({v for v in values if v > 0})
        return deduped

    @staticmethod
    def _looks_blocked(html_text: str) -> bool:
        lowered = html_text.lower()
        block_signals = [
            "access denied",
            "enable js",
            "captcha",
            "denied",
            "forbidden",
            "cloudflare",
        ]
        return any(signal in lowered for signal in block_signals)

    @staticmethod
    def _ndc_to_11_digits(ndc_value: str) -> str | None:
        return normalize_ndc_to_11_digits(ndc_value)

    @staticmethod
    def _candidate_names(query: str, selected_drug: Dict[str, Any] | None) -> List[str]:
        names: List[str] = []
        if query:
            names.append(query)
        if selected_drug:
            for key in ("brand_name", "generic_name", "brand_name_base"):
                value = selected_drug.get(key)
                if value:
                    names.append(str(value))

        cleaned = []
        seen = set()
        for name in names:
            norm = re.sub(r"\s+", " ", name).strip()
            if norm and norm.lower() not in seen:
                seen.add(norm.lower())
                cleaned.append(norm)

        if query:
            normalized = re.sub(r"[^A-Za-z0-9\-\s]", " ", query.lower())
            normalized = re.sub(r"\s+", " ", normalized).strip()
            tokens = [
                token
                for token in normalized.split()
                if token not in DrugCostLookupAgent.QUERY_STOPWORDS and len(token) > 1
            ]
            if tokens:
                token_candidates = [" ".join(tokens), tokens[-1]]
                if len(tokens) >= 2:
                    token_candidates.append(" ".join(tokens[-2:]))
                for candidate in token_candidates:
                    if candidate and candidate.lower() not in seen:
                        seen.add(candidate.lower())
                        cleaned.append(candidate)

        return cleaned

    @staticmethod
    def _latest_numeric_metric(item: Dict[str, Any], prefixes: List[str]) -> Tuple[float | None, str | None, str | None]:
        best_year = -1
        best_value: float | None = None
        best_key: str | None = None
        best_year_text: str | None = None

        for key, value in item.items():
            if value in (None, ""):
                continue
            if not any(key.startswith(prefix) for prefix in prefixes):
                continue
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                continue

            match = re.search(r"(20\d{2})$", key)
            year = int(match.group(1)) if match else 0
            year_text = match.group(1) if match else None
            if year >= best_year:
                best_year = year
                best_value = numeric_value
                best_key = key
                best_year_text = year_text

        return best_value, best_year_text, best_key

    def _cms_spending_rows(self, names: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        rows: List[Dict[str, Any]] = []
        logs: List[Dict[str, Any]] = []
        checked_on = date.today().isoformat()

        for dataset in self.CMS_SPENDING_DATASETS:
            dataset_id = dataset["id"]
            source_name = dataset["source"]
            endpoint = f"{self.CMS_DATA_API_BASE}/{dataset_id}/data"
            dataset_matched = False

            for name in names[:4]:
                params = {"keyword": name, "size": 5}
                try:
                    response = self.session.get(endpoint, params=params, timeout=self.timeout)
                except requests.RequestException as exc:
                    logs.append(
                        {
                            "source": source_name,
                            "url": endpoint,
                            "status": "error",
                            "details": str(exc),
                            "checked_on": checked_on,
                        }
                    )
                    continue

                if response.status_code != 200:
                    logs.append(
                        {
                            "source": source_name,
                            "url": response.url,
                            "status": "http_error",
                            "details": f"HTTP {response.status_code}",
                            "checked_on": checked_on,
                        }
                    )
                    continue

                try:
                    payload = response.json()
                except ValueError:
                    logs.append(
                        {
                            "source": source_name,
                            "url": response.url,
                            "status": "non_json_response",
                            "details": "Response was not JSON",
                            "checked_on": checked_on,
                        }
                    )
                    continue

                if not isinstance(payload, list) or not payload:
                    continue

                query_token = name.lower().strip()
                matched_items = []
                for item in payload:
                    brand = str(item.get("Brnd_Name", "")).strip()
                    generic = str(item.get("Gnrc_Name", "")).strip()
                    haystack = f"{brand} {generic}".lower()
                    if query_token and query_token in haystack:
                        matched_items.append(item)

                if not matched_items:
                    matched_items = payload

                added = 0
                for item in matched_items[:3]:
                    avg_value, avg_year, avg_field = self._latest_numeric_metric(
                        item,
                        prefixes=[
                            "Avg_Spnd_Per_Dsg_Unt_Wghtd_",
                            "Avg_Spndng_Per_Dsg_Unt_",
                            "Avg_Spnd_Per_Dsg_Unt_",
                        ],
                    )
                    total_value, total_year, total_field = self._latest_numeric_metric(
                        item,
                        prefixes=["Tot_Spndng_"],
                    )

                    brand = str(item.get("Brnd_Name", "")).strip()
                    generic = str(item.get("Gnrc_Name", "")).strip()

                    if avg_value is not None:
                        rows.append(
                            {
                                "source": source_name,
                                "price_type": "avg_spending_per_dosage_unit",
                                "amount_usd": avg_value,
                                "unit": "USD per dosage unit",
                                "as_of": avg_year or checked_on,
                                "url": response.url,
                                "notes": f"Brand: {brand}; Generic: {generic}; Field: {avg_field}",
                            }
                        )
                        added += 1

                    if total_value is not None:
                        rows.append(
                            {
                                "source": source_name,
                                "price_type": "total_spending_observed",
                                "amount_usd": total_value,
                                "unit": "USD total spending",
                                "as_of": total_year or checked_on,
                                "url": response.url,
                                "notes": f"Brand: {brand}; Generic: {generic}; Field: {total_field}",
                            }
                        )
                        added += 1

                if added:
                    dataset_matched = True
                    logs.append(
                        {
                            "source": source_name,
                            "url": response.url,
                            "status": "success",
                            "details": f"Extracted {added} spending metrics",
                            "checked_on": checked_on,
                        }
                    )
                    break

            if not dataset_matched:
                logs.append(
                    {
                        "source": source_name,
                        "url": endpoint,
                        "status": "no_match",
                        "details": "No spending rows matched the candidate names",
                        "checked_on": checked_on,
                    }
                )

        deduped: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
        for row in rows:
            key = (
                str(row.get("source", "")),
                str(row.get("price_type", "")),
                str(row.get("as_of", "")),
                str(row.get("notes", "")),
            )
            deduped[key] = row

        return list(deduped.values()), logs

    @staticmethod
    def _candidate_ndcs(products: List[Dict[str, Any]]) -> List[str]:
        ndcs: List[str] = []
        for row in products:
            product_ndc = row.get("product_ndc")
            if product_ndc:
                ndcs.append(str(product_ndc))
            for package in row.get("packaging", []) or []:
                package_ndc = package.get("package_ndc")
                if package_ndc:
                    ndcs.append(str(package_ndc))

        seen = set()
        unique = []
        for item in ndcs:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return unique

    def _goodrx_rows(self, names: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        rows: List[Dict[str, Any]] = []
        logs: List[Dict[str, Any]] = []

        for name in names[:3]:
            slug = self._slugify(name)
            if not slug:
                continue
            url = f"{self.GOODRX_URL}/{slug}"
            checked_on = date.today().isoformat()

            try:
                response = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                logs.append(
                    {
                        "source": "GoodRx",
                        "url": url,
                        "status": "error",
                        "details": str(exc),
                        "checked_on": checked_on,
                    }
                )
                continue

            if response.status_code != 200:
                logs.append(
                    {
                        "source": "GoodRx",
                        "url": url,
                        "status": "http_error",
                        "details": f"HTTP {response.status_code}",
                        "checked_on": checked_on,
                    }
                )
                continue

            if self._looks_blocked(response.text):
                logs.append(
                    {
                        "source": "GoodRx",
                        "url": url,
                        "status": "blocked",
                        "details": "Source blocked bot-like access",
                        "checked_on": checked_on,
                    }
                )
                continue

            text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
            prices = self._extract_money_values(text)
            if not prices:
                logs.append(
                    {
                        "source": "GoodRx",
                        "url": url,
                        "status": "no_price_found",
                        "details": "No parseable prices in response",
                        "checked_on": checked_on,
                    }
                )
                continue

            min_price = prices[0]
            median_price = prices[len(prices) // 2]
            max_price = prices[-1]

            rows.extend(
                [
                    {
                        "source": "GoodRx",
                        "price_type": "observed_min_price",
                        "amount_usd": min_price,
                        "unit": "USD",
                        "as_of": checked_on,
                        "url": url,
                        "notes": f"Parsed from page text for {name}",
                    },
                    {
                        "source": "GoodRx",
                        "price_type": "observed_median_price",
                        "amount_usd": median_price,
                        "unit": "USD",
                        "as_of": checked_on,
                        "url": url,
                        "notes": f"Parsed from page text for {name}",
                    },
                    {
                        "source": "GoodRx",
                        "price_type": "observed_max_price",
                        "amount_usd": max_price,
                        "unit": "USD",
                        "as_of": checked_on,
                        "url": url,
                        "notes": f"Parsed from page text for {name}",
                    },
                ]
            )
            logs.append(
                {
                    "source": "GoodRx",
                    "url": url,
                    "status": "success",
                    "details": f"Extracted {len(prices)} numeric price points",
                    "checked_on": checked_on,
                }
            )
            break

        return rows, logs

    def _nadac_rows(self, names: List[str], ndcs: List[str]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        rows: List[Dict[str, Any]] = []
        logs: List[Dict[str, Any]] = []
        checked_on = date.today().isoformat()

        # First try exact NDC lookups.
        for raw_ndc in ndcs[:10]:
            ndc_11 = self._ndc_to_11_digits(raw_ndc)
            if not ndc_11:
                continue

            params = {"ndc": ndc_11, "$limit": 5}
            try:
                response = self.session.get(self.NADAC_URL, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                logs.append(
                    {
                        "source": "CMS NADAC",
                        "url": self.NADAC_URL,
                        "status": "error",
                        "details": str(exc),
                        "checked_on": checked_on,
                    }
                )
                continue

            if response.status_code != 200:
                logs.append(
                    {
                        "source": "CMS NADAC",
                        "url": response.url,
                        "status": "http_error",
                        "details": f"HTTP {response.status_code}",
                        "checked_on": checked_on,
                    }
                )
                continue

            try:
                payload = response.json()
            except ValueError:
                logs.append(
                    {
                        "source": "CMS NADAC",
                        "url": response.url,
                        "status": "non_json_response",
                        "details": "Response was not JSON",
                        "checked_on": checked_on,
                    }
                )
                continue

            if not payload:
                continue

            for item in payload:
                amount = item.get("nadac_per_unit")
                if amount is None:
                    continue
                try:
                    amount_value = float(amount)
                except (TypeError, ValueError):
                    continue

                rows.append(
                    {
                        "source": "CMS NADAC",
                        "price_type": "nadac_per_unit",
                        "amount_usd": amount_value,
                        "unit": item.get("pricing_unit") or "unit",
                        "as_of": item.get("as_of_date") or checked_on,
                        "url": response.url,
                        "notes": item.get("ndc_description") or "NADAC row",
                    }
                )

            if rows:
                logs.append(
                    {
                        "source": "CMS NADAC",
                        "url": response.url,
                        "status": "success",
                        "details": f"Matched NDC {ndc_11}",
                        "checked_on": checked_on,
                    }
                )
                return rows, logs

        # Then try name-based search if no NDC match succeeded.
        if names:
            name_token = names[0].upper().replace("'", "")
            params = {
                "$where": f"upper(ndc_description) like '%{name_token}%'",
                "$limit": 5,
            }
            try:
                response = self.session.get(self.NADAC_URL, params=params, timeout=self.timeout)
                if response.status_code == 200:
                    payload = response.json()
                else:
                    payload = []

                for item in payload:
                    amount = item.get("nadac_per_unit")
                    if amount is None:
                        continue
                    try:
                        amount_value = float(amount)
                    except (TypeError, ValueError):
                        continue

                    rows.append(
                        {
                            "source": "CMS NADAC",
                            "price_type": "nadac_per_unit",
                            "amount_usd": amount_value,
                            "unit": item.get("pricing_unit") or "unit",
                            "as_of": item.get("as_of_date") or checked_on,
                            "url": response.url,
                            "notes": item.get("ndc_description") or "NADAC row",
                        }
                    )

                logs.append(
                    {
                        "source": "CMS NADAC",
                        "url": response.url,
                        "status": "success" if rows else "no_match",
                        "details": "Name-based lookup",
                        "checked_on": checked_on,
                    }
                )
            except requests.RequestException as exc:
                logs.append(
                    {
                        "source": "CMS NADAC",
                        "url": self.NADAC_URL,
                        "status": "error",
                        "details": str(exc),
                        "checked_on": checked_on,
                    }
                )

        return rows, logs

    def lookup(
        self,
        query: str,
        selected_drug: Dict[str, Any] | None,
        products: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        names = self._candidate_names(query, selected_drug)
        ndcs = self._candidate_ndcs(products)

        goodrx_rows, goodrx_logs = self._goodrx_rows(names)
        nadac_rows, nadac_logs = self._nadac_rows(names, ndcs)
        cms_rows, cms_logs = self._cms_spending_rows(names)

        all_rows = goodrx_rows + nadac_rows + cms_rows
        all_logs = goodrx_logs + nadac_logs + cms_logs

        if not all_rows:
            all_logs.append(
                {
                    "source": "Cost Aggregator",
                    "url": "",
                    "status": "no_cost_data",
                    "details": "No cost values extracted from configured sources",
                    "checked_on": date.today().isoformat(),
                }
            )

        return {
            "agent": "Drug Cost Lookup",
            "query": query,
            "cost_rows": all_rows,
            "source_logs": all_logs,
            "candidate_names": names,
            "candidate_ndcs": ndcs,
        }
