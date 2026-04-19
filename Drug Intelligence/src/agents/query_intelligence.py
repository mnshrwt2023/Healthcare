from __future__ import annotations

import re
from typing import Any, Dict, List, Set


class QueryIntelligenceAgent:
    """Classifies user query intent and prepares context for adaptive research output."""

    STOPWORDS = {
        "a",
        "about",
        "all",
        "an",
        "and",
        "any",
        "can",
        "details",
        "do",
        "explain",
        "fda",
        "for",
        "from",
        "give",
        "i",
        "in",
        "list",
        "is",
        "its",
        "me",
        "my",
        "nih",
        "of",
        "outline",
        "on",
        "or",
        "please",
        "provide",
        "pull",
        "query",
        "source",
        "sources",
        "severity",
        "summarize",
        "show",
        "tell",
        "the",
        "to",
        "u",
        "us",
        "what",
        "with",
        "major",
        "moderate",
        "minor",
    }

    INTENT_KEYWORDS = {
        "cost": {
            "cost",
            "price",
            "pricing",
            "retail",
            "wholesale",
            "nadac",
            "goodrx",
            "affordability",
            "expensive",
            "spending",
        },
        "ndc_identity": {
            "ndc",
            "manufacturer",
            "labeler",
            "dosage",
            "route",
            "form",
            "generic",
            "brand",
            "identity",
        },
        "label_usage": {
            "indication",
            "indications",
            "usage",
            "use",
            "label",
            "fda label",
            "accessfda",
        },
        "contraindications": {
            "contraindication",
            "contraindications",
            "avoid",
            "should not",
        },
        "adverse_reactions": {
            "adverse",
            "reaction",
            "reactions",
            "side effect",
            "side effects",
            "safety",
            "warning",
            "warnings",
        },
        "executive_summary": {
            "summary",
            "executive",
            "brief",
            "overview",
            "key points",
            "decision",
        },
        "toxicity": {
            "toxicity",
            "toxic",
            "hazard",
            "safety profile",
            "ghs",
        },
        "drug_interactions": {
            "interaction",
            "interactions",
            "interact",
            "interacting",
            "drug interaction",
            "drug interactions",
            "coadminister",
            "co-administer",
        },
    }

    FOLLOW_UP_PREFIXES = (
        "and ",
        "also ",
        "what about",
        "how about",
        "now ",
        "next ",
        "compare ",
    )

    @staticmethod
    def _tokenize(value: str) -> List[str]:
        normalized = re.sub(r"[^A-Za-z0-9\-\s]", " ", value.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized.split() if normalized else []

    def _detect_intents(self, query: str) -> Set[str]:
        lowered = query.lower()
        intents: Set[str] = set()

        for intent, keywords in self.INTENT_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                intents.add(intent)

        if not intents:
            intents.add("executive_summary")

        return intents

    def _extract_entity_query(self, query: str, last_entity_query: str | None) -> str:
        ndc_match = re.search(r"\b\d{4,5}-?\d{3,4}-?\d{1,2}\b", query)
        if ndc_match:
            return ndc_match.group(0)

        entity_source = query
        anchored_match = re.search(r"\b(?:for|of|about)\b\s+(.+)$", query, flags=re.IGNORECASE)
        if anchored_match:
            entity_source = anchored_match.group(1)

        all_keyword_tokens: Set[str] = set(self.STOPWORDS)
        for words in self.INTENT_KEYWORDS.values():
            for keyword in words:
                all_keyword_tokens.update(self._tokenize(keyword))

        kept_tokens = []
        for token in self._tokenize(entity_source):
            if token in all_keyword_tokens:
                continue
            if token in self.STOPWORDS:
                continue
            kept_tokens.append(token)

        if kept_tokens:
            return " ".join(kept_tokens[:6])

        return (last_entity_query or "").strip()

    def analyze(self, query: str, last_entity_query: str | None = None) -> Dict[str, Any]:
        query_clean = re.sub(r"\s+", " ", query.strip())
        lowered = query_clean.lower()
        intents = self._detect_intents(query_clean)

        entity_query = self._extract_entity_query(query_clean, last_entity_query)
        is_follow_up = any(lowered.startswith(prefix) for prefix in self.FOLLOW_UP_PREFIXES)
        if not entity_query and last_entity_query:
            is_follow_up = True

        label_intents = {"label_usage", "contraindications", "adverse_reactions", "executive_summary"}
        needs_label = bool(intents & label_intents)
        needs_cost = "cost" in intents
        needs_toxicity = "toxicity" in intents
        needs_interactions = "drug_interactions" in intents

        focus_order = [
            "contraindications",
            "adverse_reactions",
            "label_usage",
            "drug_interactions",
            "toxicity",
            "cost",
            "ndc_identity",
            "executive_summary",
        ]
        focus_area = next((item for item in focus_order if item in intents), "executive_summary")

        return {
            "query": query_clean,
            "intents": sorted(intents),
            "focus_area": focus_area,
            "entity_query": entity_query,
            "is_follow_up": is_follow_up,
            "needs_cost": needs_cost,
            "needs_label": needs_label,
            "needs_toxicity": needs_toxicity,
            "needs_interactions": needs_interactions,
        }

    def follow_up_suggestions(
        self,
        query_context: Dict[str, Any],
        selected_drug: Dict[str, Any] | None,
        has_cost_rows: bool,
        has_label_sections: bool,
        is_first_query: bool = False,
        session_intents: set | None = None,
        rendered_sections: set | None = None,
    ) -> List[str]:
        if selected_drug:
            drug_name = (
                selected_drug.get("brand_name")
                or selected_drug.get("generic_name")
                or selected_drug.get("product_ndc")
                or "this drug"
            )
        else:
            drug_name = query_context.get("entity_query") or "this drug"

        focus_area = query_context.get("focus_area", "executive_summary")
        intents = set(query_context.get("intents", []))
        session_intents = (session_intents or set()) | intents  # current query counts too
        rendered_sections = rendered_sections or set()
        suggestions: List[str] = []

        # First query gets NDC & Manufacturer Documentation as an anchor suggestion,
        # but only if ndc_identity was never shown in this session yet.
        if is_first_query and "ndc_identity" not in session_intents and "ndc_identity" not in rendered_sections:
            suggestions.append(f"NDC and Manufacturer Documentation for {drug_name}.")

        if focus_area == "executive_summary" and not is_first_query:
            suggestions.append(f"What are the contraindications and adverse reactions for {drug_name}?")

        # Only suggest cost if not already done in this session
        if "cost" not in session_intents:
            suggestions.append(f"Look up retail and pharmacy cost data for {drug_name}.")

        # Only suggest toxicity if not already done in this session
        if "toxicity" not in session_intents:
            suggestions.append(f"Provide toxicity profile for {drug_name} from NIH and FDA sources.")

        # Only suggest interactions if not already done in this session
        if "drug_interactions" not in session_intents:
            suggestions.append(f"Show drug interactions for {drug_name} and interaction severity.")

        # Only suggest FDA label if not already done in this session
        label_intents = {"label_usage", "contraindications", "adverse_reactions"}
        if not has_label_sections and not bool(session_intents & label_intents):
            suggestions.append(f"Pull FDA indications, contraindications, and adverse reactions for {drug_name}.")

        if "cost" in intents and not has_cost_rows:
            suggestions.append(f"Try alternate cost metrics for {drug_name} and explain source reliability.")

        deduped: List[str] = []
        seen = set()
        for item in suggestions:
            key = item.lower().strip()
            if key not in seen:
                seen.add(key)
                deduped.append(item)

        return deduped[:5]