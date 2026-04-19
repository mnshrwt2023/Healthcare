from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from .cost_agent import DrugCostLookupAgent
from .interaction_agent import DrugInteractionAgent
from .label_agent import DrugLabelIntelligenceAgent
from .ndc_agent import DrugNDCAgent
from .query_intelligence import QueryIntelligenceAgent
from .toxicity_agent import DrugToxicityAgent


class DrugResearchOrchestrator:
    """Runs NDC first, then cost, with optional FDA label intelligence and indexed follow-ups."""

    def __init__(self) -> None:
        self.ndc_agent = DrugNDCAgent()
        self.cost_agent = DrugCostLookupAgent()
        self.label_agent = DrugLabelIntelligenceAgent()
        self.toxicity_agent = DrugToxicityAgent()
        self.interaction_agent = DrugInteractionAgent()
        self.query_agent = QueryIntelligenceAgent()
        self.entity_index: Dict[str, Dict[str, Any]] = {}
        self.last_entity_query = ""

    @staticmethod
    def _entity_key(query: str, ndc_result: Dict[str, Any]) -> str:
        selected = ndc_result.get("selected_drug") or {}
        key_candidates = [
            selected.get("generic_name"),
            selected.get("brand_name"),
            selected.get("product_ndc"),
            query,
        ]
        for candidate in key_candidates:
            value = str(candidate or "").strip().lower()
            if value:
                return value
        return query.strip().lower()

    @staticmethod
    def _is_placeholder_result(result: Dict[str, Any] | None) -> bool:
        if not result:
            return False
        source_logs = result.get("source_logs") or []
        statuses = {
            str(item.get("status", "")).strip().lower()
            for item in source_logs
            if isinstance(item, dict) and str(item.get("status", "")).strip()
        }
        return bool(statuses) and statuses == {"not_requested"}

    def _cached_stage_result(self, cache_entry: Dict[str, Any], stage_name: str) -> Dict[str, Any] | None:
        cached = cache_entry.get(stage_name)
        if self._is_placeholder_result(cached):
            return None
        return cached

    def run(self, query: str, ndc_limit: int = 50, is_first_query: bool = False, session_intents: set | None = None, rendered_sections: set | None = None) -> Dict[str, Any]:
        query_context = self.query_agent.analyze(
            query=query,
            last_entity_query=self.last_entity_query,
        )
        entity_query = query_context.get("entity_query") or query.strip()
        if entity_query:
            self.last_entity_query = entity_query

        cache_key = entity_query.lower()
        cache_hit = cache_key in self.entity_index
        cache_entry = self.entity_index.get(cache_key, {})

        ndc_result = cache_entry.get("ndc")
        if not ndc_result:
            ndc_result = self.ndc_agent.resolve(query=entity_query, limit=ndc_limit)

        today = datetime.utcnow().date().isoformat()
        needs_cost = bool(query_context.get("needs_cost"))
        needs_toxicity = bool(query_context.get("needs_toxicity"))
        needs_interactions = bool(query_context.get("needs_interactions"))
        cost_result = None
        if needs_cost:
            cost_result = self._cached_stage_result(cache_entry, "cost")
            if not cost_result:
                cost_result = self.cost_agent.lookup(
                    query=entity_query,
                    selected_drug=ndc_result.get("selected_drug"),
                    products=ndc_result.get("products", []),
                )
        else:
            cost_result = {
                "agent": "Drug Cost Lookup",
                "query": entity_query,
                "cost_rows": [],
                "source_logs": [
                    {
                        "source": "Drug Cost Lookup",
                        "url": "",
                        "status": "not_requested",
                        "details": "Cost stage skipped because user intent did not request cost intelligence",
                        "checked_on": today,
                    }
                ],
                "candidate_names": [],
                "candidate_ndcs": [],
            }

        label_result = self._cached_stage_result(cache_entry, "label")
        needs_label = bool(query_context.get("needs_label"))
        if needs_label and not label_result:
            label_result = self.label_agent.lookup(
                query=entity_query,
                selected_drug=ndc_result.get("selected_drug"),
                products=ndc_result.get("products", []),
            )
        if not label_result:
            label_result = {
                "agent": "Drug Label Intelligence",
                "query": entity_query,
                "retrieved_on": today,
                "as_of": "",
                "sections": {
                    "indications_and_usage": "",
                    "contraindications": "",
                    "adverse_reactions": "",
                },
                "source_logs": [
                    {
                        "source": "Drug Label Intelligence",
                        "url": "https://api.fda.gov/drug/label.json",
                        "status": "not_requested",
                        "details": "Label stage skipped for this query intent",
                        "checked_on": today,
                    }
                ],
            }

        toxicity_result = None
        if needs_toxicity:
            toxicity_result = self._cached_stage_result(cache_entry, "toxicity")
            if not toxicity_result:
                toxicity_result = self.toxicity_agent.lookup(
                    query=entity_query,
                    selected_drug=ndc_result.get("selected_drug"),
                    products=ndc_result.get("products", []),
                )
        else:
            toxicity_result = {
                "agent": "Drug Toxicity Intelligence",
                "query": entity_query,
                "retrieved_on": today,
                "profiles": [],
                "source_logs": [
                    {
                        "source": "Drug Toxicity Intelligence",
                        "url": "",
                        "status": "not_requested",
                        "details": "Toxicity stage skipped because user intent did not request toxicity",
                        "checked_on": today,
                    }
                ],
            }

        interaction_result = None
        if needs_interactions:
            interaction_result = self._cached_stage_result(cache_entry, "interactions")
            if not interaction_result:
                interaction_result = self.interaction_agent.lookup(
                    query=entity_query,
                    selected_drug=ndc_result.get("selected_drug"),
                    products=ndc_result.get("products", []),
                )
        else:
            interaction_result = {
                "agent": "Drug Interaction Intelligence",
                "query": entity_query,
                "retrieved_on": today,
                "interactions": [],
                "source_logs": [
                    {
                        "source": "Drug Interaction Intelligence",
                        "url": "",
                        "status": "not_requested",
                        "details": "Interaction stage skipped because user intent did not request interactions",
                        "checked_on": today,
                    }
                ],
            }

        has_label_sections = any(str(v).strip() for v in (label_result.get("sections") or {}).values())
        follow_up_suggestions = self.query_agent.follow_up_suggestions(
            query_context=query_context,
            selected_drug=ndc_result.get("selected_drug"),
            has_cost_rows=bool(cost_result.get("cost_rows")) if needs_cost else False,
            has_label_sections=has_label_sections,
            is_first_query=is_first_query,
            session_intents=session_intents or set(),
            rendered_sections=rendered_sections or set(),
        )

        final_key = self._entity_key(entity_query, ndc_result)
        entry = {
            "ndc": ndc_result,
            "cost": cost_result if needs_cost else cache_entry.get("cost"),
            "label": label_result if needs_label else cache_entry.get("label"),
            "toxicity": toxicity_result if needs_toxicity else cache_entry.get("toxicity"),
            "interactions": interaction_result if needs_interactions else cache_entry.get("interactions"),
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        self.entity_index[final_key] = entry
        if cache_key:
            self.entity_index[cache_key] = entry

        workflow = ["Drug NDC Assistant"]
        if needs_cost:
            workflow.append("Drug Cost Lookup")
        if needs_label:
            workflow.append("Drug Label Intelligence")
        if needs_toxicity:
            workflow.append("Drug Toxicity Intelligence")
        if needs_interactions:
            workflow.append("Drug Interaction Intelligence")

        return {
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "workflow": workflow,
            "query": query,
            "resolved_query": entity_query,
            "query_context": query_context,
            "follow_up_suggestions": follow_up_suggestions,
            "index": {
                "cache_hit": cache_hit,
                "indexed_entities": len(self.entity_index),
                "active_entity_key": final_key,
            },
            "ndc": ndc_result,
            "cost": cost_result,
            "label": label_result,
            "toxicity": toxicity_result,
            "interactions": interaction_result,
        }
