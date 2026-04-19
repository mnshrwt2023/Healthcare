# NDC Agent Project: Current Architecture Reference

## 1) Project Purpose
This project is a Streamlit-based clinical drug intelligence workbench with two top-level workflows:
- Drug Intelligence
- NDC Converter

Core outcomes:
1. Resolve drug identity first (NDC, manufacturer, dosage form, route) using openFDA.
2. Present FDA clinical label sections in a monograph-style format:
   - Indications and Usage
   - Contraindications
   - Adverse Reactions
3. Add NIH/FDA safety intelligence:
   - Toxicity profiles
   - Drug interactions
4. Keep cost intelligence strictly intent-driven (shown only when user asks for cost).
5. Preserve source-attributed traceability logs and no-fabrication behavior.
6. Support standalone NDC normalization to 11-digit format from pasted lists or uploaded sheets.

---

## 2) Current Project Structure

```text
NDC agent/
├─ app.py
├─ PROJECT_UNDERSTANDING.md
├─ requirements.txt
├─ src/
│  ├─ insight_engine.py   (legacy/unused in current app flow)
│  ├─ utils/
│  │  ├─ __init__.py
│  │  └─ ndc_converter.py
│  └─ agents/
│     ├─ __init__.py
│     ├─ ndc_agent.py
│     ├─ cost_agent.py
│     ├─ label_agent.py
│     ├─ toxicity_agent.py
│     ├─ interaction_agent.py
│     ├─ query_intelligence.py
│     └─ research_orchestrator.py
└─ .venv/
```

---

## 3) End-to-End Runtime Flow

### User flow
1. User selects one of two top-level tabs:
   - `Drug Intelligence`
   - `NDC Converter`
2. In `Drug Intelligence`, `DrugResearchOrchestrator.run()` executes the intent-aware staged pipeline:
   - Stage A: `DrugNDCAgent.resolve()` (always first)
   - Stage B: `DrugLabelIntelligenceAgent.lookup()` (only when the query asks for FDA label content or broader summary context)
   - Stage C: `DrugCostLookupAgent.lookup()` (only if cost intent detected)
   - Stage D: `DrugToxicityAgent.lookup()` (only if toxicity intent detected)
   - Stage E: `DrugInteractionAgent.lookup()` (only if interaction intent detected)
3. In `Drug Intelligence`, the app renders:
   - Query summary
   - Conditional KPI cards based on requested intent
   - NDC documentation section
   - FDA clinical monograph section
   - Toxicity section (if requested)
   - Interaction section (if requested)
   - Cost section (if requested)
   - Source logs
   - Follow-up suggestions on the newest result block only
4. In `NDC Converter`, the app accepts:
   - Multiline pasted NDC values
   - CSV or XLSX uploads with auto-detected NDC columns and user override
5. The converter renders separate outputs for:
   - Direct conversion results
   - Review-required raw 9-10 digit inputs
   - Unsupported inputs

### Result history behavior
- `Drug Intelligence` results append below prior results rather than replacing the page.
- Only the newest result block renders follow-up suggestion actions.
- Converter state is isolated from research result history.

### Orchestrator output shape
`research_orchestrator.py` returns:
- `generated_at`
- `workflow`
- `query`
- `resolved_query`
- `query_context`
- `follow_up_suggestions`
- `index`
- `ndc`
- `cost`
- `label`
- `toxicity`
- `interactions`

---

## 4) File-by-File Responsibilities

## `app.py`
Main Streamlit UI.

Responsibilities:
- Render top-level tabs for `Drug Intelligence` and `NDC Converter`
- Trigger orchestrator runs for Drug Intelligence mode
- Maintain appended result history in session state
- Track rendered sections via `rendered_sections` session state set
- Convert staged outputs into dataframes
- Render structured sections and conditional KPI cards
- Render monograph-style FDA section documentation with reference link footer
- Render toxicity/interactions tables with evidence text when requested
- Render source logs and CSV exports
- Render follow-up suggestion actions on the newest result block only
- Render a global page guide / glossary popover for section meanings
- Render standalone NDC conversion workflows for pasted input and uploaded sheets
- Render unified empty-state detection for queries with no useful details

Key helper functions:
- `has_requested_details()`: Unified detector checking if any requested stage has useful output (not empty). Returns False when all stages are empty, triggering simplified no-result message.
- `monograph_reference_link()`: Generates DailyMed SPL URL when spl_set_id available, fallback to openFDA label URL. Used for FDA Clinical Monograph footer.
- `get_rendered_sections()` / `mark_section_rendered()` / `is_section_rendered()`: Session-scoped set tracking which section types (e.g. `ndc_identity`) have already been rendered. Cleared on browser refresh.

Notable current UI behavior:
- Cost, toxicity, and interaction KPI cards only render when requested by the current query.
- Source Logs display a compact view with source, URL, and retrieval date.
- Clinical narrative text is cleaned for readability before rendering.
- Fully empty research results (when `has_requested_details()` is False) collapse to a single `No details were found.` message instead of rendering empty sections and source logs table.
- FDA Clinical Monograph section always includes a reference link footer for user verification.
- Converter FDA Drug Name field populated when direct conversions match openFDA records.
- **NDC and Manufacturer Documentation renders at most once per session.** If already shown in a prior query, it is skipped in all subsequent renders even if the query triggers a full identity path.
- Follow-up suggestions are session-aware: suggestions for intents already covered (by session_intents) or sections already rendered (by rendered_sections) are suppressed.

Not rendered anymore:
- AccessData Label Documents
- Decision Intelligence table
- Risk Flags table
- Audience-role selector

## `src/agents/query_intelligence.py`
Intent parser and follow-up suggestion engine.

Responsibilities:
- Detect intent classes:
  - `cost`
  - `label_usage`
  - `contraindications`
  - `adverse_reactions`
  - `toxicity`
  - `drug_interactions`
- Set stage toggles:
  - `needs_cost`
  - `needs_label`
  - `needs_toxicity`
  - `needs_interactions`
- Produce contextual follow-up questions for missing dimensions
- Suppress follow-up suggestions for intents already seen this session (`session_intents`)
- Suppress NDC/manufacturer suggestion when `ndc_identity` section already rendered (`rendered_sections`)

Entity extraction improvement:
- Base STOPWORDS now include 42+ command verbs and severity modifiers ("pull", "query", "severity", "major", "moderate", "minor", etc.)
- Prevents entity pollution where command verbs or clinical modifiers were previously surviving into the entity_query
- Uses anchored extraction ("for|of|about") followed by multi-pass stopword/keyword filtering
- Enables consistent entity resolution across varied phrasings (e.g., all Wegovy query variants resolve to entity_query="wegovy")

## `src/agents/research_orchestrator.py`
Pipeline coordinator and index manager.

Responsibilities:
- Enforce NDC-first ordering
- Apply query-intent stage gating
- Return stable payloads with `not_requested` logs for skipped stages
- Maintain indexed follow-up context

Cache handling improvements:
- `_is_placeholder_result()`: Detects when a cached stage result is only a placeholder ("not_requested" status), indicating the stage was skipped in that query
- `_cached_stage_result()`: Retrieves cached result safely; returns None for placeholders to force fresh lookups
- Prevents cached "not_requested" placeholders from suppressing later requested stages on the same entity
- Example: Interaction-only query for Wegovy followed by label-focused query now fetches FDA sections instead of returning cached empty placeholder

## `src/agents/label_agent.py`
FDA clinical section extractor from openFDA label endpoint.

Responsibilities:
- Search by NDC or name
- Select best row based on section coverage + recency
- Extract and normalize:
  - `indications_and_usage`
  - `contraindications`
  - `adverse_reactions`
- Return section quality metadata:
  - status (complete/truncated/missing)
  - source field
  - truncation markers

## `src/agents/toxicity_agent.py`
NIH/FDA toxicity intelligence.

Sources:
- NIH PubChem (primary)
- FDA label safety fallback fields (when needed)

Output:
- `profiles` rows with source, profile_type, finding, severity, evidence URL, dates
- source logs with status details

## `src/agents/interaction_agent.py`
NIH/FDA interaction intelligence.

Sources:
- NIH RxNav interaction API (primary)
- FDA label `drug_interactions` fallback (secondary)

Output:
- `interactions` rows with interacting drug, severity, mechanism, evidence text, URLs, dates
- source logs with status details

## `src/agents/cost_agent.py`
Cost intelligence stage (retail/wholesale/program spending).

Stage behavior:
- Executes only when user intent requests cost.
- Reuses shared NDC normalization logic from `src/utils/ndc_converter.py`.

## `src/agents/ndc_agent.py`
Drug identity resolver from openFDA NDC API.

## `src/utils/ndc_converter.py`
Shared NDC normalization utility used by the standalone converter UI and cost-agent logic.

Responsibilities:
- Sanitize incoming NDC values
- Directly normalize supported hyphenated 9-10 digit NDC formats to 11 digits
- Preserve already-11-digit inputs
- Enrich converted NDCs with FDA drug names via multiple search strategies:
  - `hyphenated_lookup_candidates()`: Generates raw 11-digit, hyphenated, and partial-segment variants as FDA search terms
  - `lookup_fda_drug_name()`: LRU-cached openFDA NDC query returning brand/generic name
  - `resolve_fda_drug_name()`: Loops candidates and returns best match or blank
- Separate raw 9-10 digit values without hyphens into a review-required mode
- Emit structured converter rows with mode, status, reason, confidence, validation source, CNN Drug Name, and candidate patterns
- Detect likely NDC columns in uploaded tabular files

## `src/insight_engine.py`
Legacy decision/risk engine retained in repo but not used in current app rendering path.

---

## 5) Output Philosophy

1. Documentation-first rendering, not raw dumps.
2. Every section is source-attributed.
3. Missing/blocked sources are explicitly logged.
4. No fabricated clinical, toxicity, interaction, or cost values.
5. Cost shown only when requested.
6. Converter outputs distinguish between direct conversion and review-required inputs.

---

## 6) Key Behavior Rules

1. NDC identity resolution always runs first.
2. FDA monograph sections run only when the query asks for label-oriented content or broader summary context.
3. Toxicity and interactions run only when requested by intent.
4. Cost stage runs only when requested by intent.
5. Follow-up suggestions explicitly propose missing dimensions (cost, toxicity, interactions).
6. Drug Intelligence results append to history instead of replacing prior output.
7. Converter mode is separate from orchestrator-driven research mode.
8. Raw 9-10 digit inputs without hyphens are not directly auto-converted; they are presented in review-required output.
9. Cached stage results for skipped stages (not_requested) do not suppress later requested stages on the same entity.
10. Queries returning no useful details from all requested stages render a single "No details were found." message without section headers or source logs table.
11. FDA Clinical Monograph section includes a reference link footer (preferring DailyMed SPL when spl_set_id available, fallback to openFDA label URL).
12. Direct NDC conversions attempt FDA drug name enrichment via multiple search strategies; fallback to blank when no match found.
13. NDC and Manufacturer Documentation section renders at most once per session regardless of how many queries are run. Cleared on page refresh.
14. Follow-up suggestions are deduplicated across the session using `session_intents` (intents already asked) and `rendered_sections` (sections already shown). No suggestion is repeated for something already done.

---

## 7) External Data Sources

Identity and label:
- openFDA NDC API
- openFDA Drug Label API

Safety intelligence:
- NIH PubChem (toxicity)
- NIH RxNav (drug interactions)
- FDA label fields as fallback/context

Cost:
- GoodRx (retail attempt)
- CMS NADAC (wholesale)
- CMS spending datasets (fallback)

Converter support:
- Shared normalization rules implemented in `src/utils/ndc_converter.py`
- CSV upload via pandas
- XLSX upload via pandas + `openpyxl`

---

## 8) How to Run

1. Activate `.venv`
2. Install dependencies if needed:
   - `pip install -r requirements.txt`
3. Start app:
   - `streamlit run app.py`

---

## 9) Validation Guidance

Recommended scenario checks:
1. `metformin contraindications`
   - Expect FDA monograph section content
   - No cost section
   - Monograph includes DailyMed SPL reference link at footer
2. `metformin toxicity profile`
   - Expect toxicity findings from NIH/FDA sources
3. `drug interactions for metformin`
   - Expect interaction findings with severity/evidence
4. `metformin cost summary`
   - Expect cost section and cost stage in workflow
5. Pasted hyphenated NDCs such as `1234-5678-1`
   - Expect direct conversion to 11 digits with FDA Drug Name populated
6. Raw 9-10 digit inputs such as `123456789` or `1234567890`
   - Expect review-required output with FDA-recognized candidate patterns
7. CSV/XLSX upload with an NDC-like column
   - Expect column auto-detection and conversion results download
   - FDA Drug Name column populated for direct conversions
8. Query with phrase variations: `Show severity major interactions for Wegovy`
   - Expect entity_query resolves to "wegovy" (no entity pollution from "severity" or "major")
   - Expect 1+ interaction results (not 0)
9. Sequential queries on same drug:
   - First: `Interactions for Wegovy` → returns interaction findings
   - Second: `Show FDA label for Wegovy` → returns FDA sections (not empty placeholder)
10. Query for invalid drug name: `Pull FDA… for zzzzznotarealdrugname`
   - Expect unified "No details were found." message
   - No empty section headers or source logs table rendered

---

## 10) Recent Updates (April 2026)

**Session-Aware Section Deduplication (April 2026)**
- Added `rendered_sections` set in `st.session_state` (cleared on browser refresh)
- `get_rendered_sections()`, `mark_section_rendered()`, `is_section_rendered()` helpers in `app.py`
- NDC and Manufacturer Documentation section is gated: renders only once per session, skipped on all subsequent queries
- `rendered_sections` threaded into `follow_up_suggestions()` — NDC doc suggestion suppressed once section has been shown
- `session_intents` threaded from result history into orchestrator and `follow_up_suggestions()` — each follow-up suggestion skipped if its intent domain was already asked this session
- Both `rendered_sections` and `session_intents` passed via `orchestrator.run()` from `run_drug_query()`


- Direct conversion results now include FDA brand/generic drug names when matched in openFDA
- Expanded search candidates to include raw 11-digit, hyphenated, and partial-segment forms
- Tested against known NDCs (Humulin, Wegovy) with successful name resolution

**Query Entity Extraction Cleanup**
- Moved command verbs ("pull", "query") and severity modifiers ("severity", "major", "moderate", "minor") into base STOPWORDS
- Prevents entity pollution where clinical modifiers were corrupting drug name detection
- Improves consistency of entity resolution across varied phrasings
- All Wegovy query variants now resolve to entity_query="wegovy" regardless of phrasing modifiers

**Orchestrator Cache Reuse for Sequential Queries**
- Added `_is_placeholder_result()` and `_cached_stage_result()` helpers in `src/agents/research_orchestrator.py`
- Cached "not_requested" placeholders no longer suppress fresh lookups when a stage transitions from skipped to requested
- Enables smooth sequential workflows (e.g., first interaction-only query, then label-focused follow-up on same drug)
- Example: Wegovy interaction query followed by label control-follow-up now returns FDA sections instead of empty placeholder

**Unified Empty-State Rendering**
- Added `has_requested_details()` unified detector in `app.py`
- Queries with zero results across all requested stages now render single "No details were found." message
- Errors are no longer verbose (removed empty section headers and source logs table for no-result cases)
- Preserves follow-up suggestions and query context even in empty-state rendering

**FDA Monograph Reference Links**
- Added `monograph_reference_link()` helper in `app.py`
- FDA Clinical Monograph section now includes clickable reference footer
- Prefers DailyMed SPL links (when spl_set_id available); fallback to openFDA label URL
- Enables end-user verification of clinical content source

---

## 11) Quick Reference

- `app.py`: UI rendering and exports
- `src/utils/ndc_converter.py`: shared NDC conversion and column detection
- `src/agents/research_orchestrator.py`: stage routing/index
- `src/agents/query_intelligence.py`: intent extraction/follow-ups
- `src/agents/label_agent.py`: FDA monograph sections
- `src/agents/toxicity_agent.py`: NIH/FDA toxicity profiles
- `src/agents/interaction_agent.py`: NIH/FDA interaction findings
- `src/agents/cost_agent.py`: cost stage
- `src/agents/ndc_agent.py`: identity stage
