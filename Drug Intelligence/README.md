# Drug Intelligence Studio

A Streamlit-based clinical drug intelligence workbench for NDC resolution, FDA label intelligence, NIH/FDA toxicity and interaction profiles, cost lookup, and standalone NDC 11-digit conversion.

---

## Features

| Feature | Description |
|---|---|
| **Drug Intelligence** | Intent-aware query pipeline resolving drug identity, FDA label sections, NIH toxicity, drug interactions, and cost |
| **NDC Converter** | Normalize hyphenated or raw NDC values to FDA 11-digit format from pasted input or uploaded CSV/XLSX |
| **Session Excel Export** | Download all query results in a multi-tab Excel file with source logs per query |
| **Follow-up Suggestions** | Contextual next-step suggestions that suppress already-covered intents for the session |

---

## Data Sources

| Source | Used For |
|---|---|
| openFDA NDC API | Drug identity, manufacturer, dosage form, route |
| openFDA Drug Label API | Indications, contraindications, adverse reactions |
| NIH PubChem | Toxicity profiles |
| NIH RxNav | Drug interaction findings |
| DailyMed (SPL) | Monograph reference links |
| CMS NADAC / Spending datasets | Wholesale and program cost data |
| GoodRx / Drugs.com | Retail cost attempt (network-dependent) |

> **Note:** GoodRx and Drugs.com may be blocked depending on network or bot-protection policies. All other sources use public APIs with no key required.

---

## Requirements

- Python 3.10+
- Dependencies listed in `requirements.txt`

---

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/your-username/ndc-agent.git
cd ndc-agent

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate       # macOS/Linux
.venv\Scripts\activate          # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the app
streamlit run app.py
```

The app opens at `http://localhost:8501` in your browser.

---

## Project Structure

```
ndc-agent/
├── app.py                        # Streamlit UI and rendering
├── requirements.txt
├── PROJECT_UNDERSTANDING.md      # Architecture and behavior reference
├── src/
│   ├── agents/
│   │   ├── ndc_agent.py          # Drug identity resolver (openFDA)
│   │   ├── label_agent.py        # FDA clinical monograph sections
│   │   ├── cost_agent.py         # Cost intelligence stage
│   │   ├── toxicity_agent.py     # NIH/FDA toxicity profiles
│   │   ├── interaction_agent.py  # NIH/FDA drug interactions
│   │   ├── query_intelligence.py # Intent parser and follow-up engine
│   │   └── research_orchestrator.py  # Pipeline coordinator
│   └── utils/
│       └── ndc_converter.py      # NDC normalization and FDA name enrichment
└── scripts/
    └── local_validate.sh         # Local smoke-test helper
```

---

## Usage

### Drug Intelligence tab
Type any drug question in plain English:
- `contraindications and adverse reactions for metformin`
- `toxicity profile for Wegovy`
- `drug interactions for semaglutide`
- `cost data for Ozempic`
- `NDC and Manufacturer Documentation for Humira`

Results append below each other in the session. Download the full session as Excel with the **Download Full Session as Excel** button.

### NDC Converter tab
- **Paste mode:** Enter one NDC per line (hyphenated or raw digits)
- **Sheet mode:** Upload a CSV or XLSX; select the NDC column; download results

---

## Architecture

For detailed architecture, behavior rules, and validation scenarios, see [PROJECT_UNDERSTANDING.md](PROJECT_UNDERSTANDING.md).

Key highlights:
- NDC identity resolution always runs first
- FDA, toxicity, interactions, and cost stages run only when intent-requested
- Session-aware follow-up suggestions suppress already-covered intents
- NDC and Manufacturer Documentation renders at most once per session
- All results are source-attributed with retrieval dates and URLs

---

## License

MIT
