#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "ERROR: Python environment not found at $PYTHON_BIN"
  echo "Create/activate your .venv first, then rerun this validation."
  exit 1
fi

cd "$ROOT_DIR"

echo "[1/3] Running syntax compile checks..."
"$PYTHON_BIN" -m py_compile app.py src/agents/*.py src/insight_engine.py

echo "[2/3] Running intent and workflow smoke checks..."
"$PYTHON_BIN" -c "from src.agents import DrugResearchOrchestrator; o=DrugResearchOrchestrator(); a=o.run('metformin contraindications', ndc_limit=20); b=o.run('metformin cost summary', ndc_limit=20); assert a.get('query_context',{}).get('needs_cost') is False, 'Expected needs_cost=False for clinical query'; assert (a.get('cost',{}).get('source_logs') or [{}])[0].get('status') == 'not_requested', 'Expected cost stage to be skipped for non-cost query'; assert 'Drug Label Intelligence' in a.get('workflow',[]), 'Expected label stage in clinical workflow'; assert b.get('query_context',{}).get('needs_cost') is True, 'Expected needs_cost=True for cost query'; assert 'Drug Cost Lookup' in b.get('workflow',[]), 'Expected cost stage in cost workflow'; print('OK: intent and workflow checks passed')"

echo "[3/3] Running label extraction shape checks..."
"$PYTHON_BIN" -c "from src.agents.label_agent import DrugLabelIntelligenceAgent; a=DrugLabelIntelligenceAgent(); r=a.lookup(query='glucophage', selected_drug={'application_number':'NDA020357','brand_name':'GLUCOPHAGE','generic_name':'metformin'}, products=[]); q=r.get('section_quality',{}); assert all(k in q for k in ('indications_and_usage','contraindications','adverse_reactions')), 'Missing section quality keys'; assert isinstance(r.get('label_documents',[]), list), 'label_documents must be a list'; print('OK: label extraction checks passed')"

echo "Validation complete."
