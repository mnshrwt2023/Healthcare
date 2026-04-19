from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd


class InsightDecisionEngine:
    """Legacy compatibility engine now limited to source-attributed summary outputs only."""

    def run(
        self,
        query: str,
        products_df: pd.DataFrame,
        costs_df: pd.DataFrame,
        logs_df: pd.DataFrame,
        label_sections: Dict[str, str] | None = None,
        query_context: Dict[str, Any] | None = None,
        audience_role: str | None = None,
    ) -> Dict[str, Any]:
        del audience_role
        label_sections = label_sections or {}
        query_context = query_context or {}

        matched_products = int(len(products_df.index))
        cost_points = int(len(costs_df.index))
        label_sections_available = int(sum(1 for value in label_sections.values() if str(value).strip()))

        source_reliability_rows: List[Dict[str, Any]] = []
        if not logs_df.empty and all(c in logs_df.columns for c in ["Source", "Status", "URL", "Checked On"]):
            grouped = logs_df.groupby("Source", as_index=False).agg(
                {
                    "Status": "last",
                    "URL": "first",
                    "Checked On": "max",
                }
            )
            for _, row in grouped.iterrows():
                source_reliability_rows.append(
                    {
                        "Source": row["Source"],
                        "Latest Status": row["Status"],
                        "Latest Checked On": row["Checked On"],
                        "URL": row["URL"],
                    }
                )

        executive_summary = [
            f"Query '{query}' matched {matched_products} product records.",
            f"FDA clinical section coverage is {label_sections_available}/3.",
            (
                f"Cost datapoints available: {cost_points}."
                if query_context.get("needs_cost")
                else "Cost datapoints were not requested for this query."
            ),
            f"Tracked source systems: {len(source_reliability_rows)}.",
        ]

        return {
            "executive_summary": executive_summary,
            "source_reliability": pd.DataFrame(source_reliability_rows),
            "kpis": {
                "matched_products": matched_products,
                "cost_points": cost_points,
                "label_sections_available": label_sections_available,
                "source_systems": len(source_reliability_rows),
            },
        }
