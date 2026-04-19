from __future__ import annotations

import io
import re
from typing import Any, Dict, List

import altair as alt
import pandas as pd
import streamlit as st

from src.agents import DrugResearchOrchestrator
from src.utils import batch_convert_ndcs, detect_ndc_columns


st.set_page_config(
    page_title="Drug Intelligence Studio",
    page_icon="🩺",
    layout="wide",
)

DEFAULT_NDC_LIMIT = 60
SECTION_GUIDE = [
    (
        "NDC and Manufacturer Documentation",
        "Resolved identity details for the queried drug, including product NDC, labeler, dosage form, and route.",
    ),
    (
        "FDA Clinical Monograph",
        "Structured FDA label content for indications and usage, contraindications, and adverse reactions.",
    ),
    (
        "Cost Points",
        "The number of cost observations returned for the current query when cost intelligence was requested.",
    ),
    (
        "Toxicity Findings",
        "The number of documented NIH or FDA toxicity findings returned when toxicity was requested.",
    ),
    (
        "Interaction Findings",
        "The number of documented NIH or FDA interaction findings returned when interaction intelligence was requested.",
    ),
    (
        "Source Logs",
        "A source-attributed checklist of systems queried for the current result, including URLs and retrieval date.",
    ),
    (
        "NDC Converter",
        "A standalone workflow for converting hyphenated 10-digit NDC values into the FDA 11-digit format.",
    ),
]


@st.cache_resource
def get_orchestrator() -> DrugResearchOrchestrator:
    return DrugResearchOrchestrator()


def inject_healthcare_theme() -> None:
    st.markdown(
        """
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&family=Libre+Baskerville:wght@700&display=swap');

            :root {
                --hc-navy: #0e2b45;
                --hc-teal: #0f7a7a;
                --hc-slate: #1f3f58;
                --hc-mint: #e5f7f5;
                --hc-bg: #f4f8fb;
                --hc-card: #ffffff;
                --hc-border: #c7dbe6;
            }

            .stApp {
                background:
                    radial-gradient(circle at 12% -8%, rgba(15, 122, 122, 0.18), transparent 34%),
                    radial-gradient(circle at 88% -12%, rgba(14, 43, 69, 0.15), transparent 32%),
                    linear-gradient(180deg, var(--hc-bg) 0%, #eef5fa 50%, #f7fbfd 100%);
                font-family: 'Source Sans 3', sans-serif;
            }

            h1, h2, h3 {
                font-family: 'Libre Baskerville', serif !important;
                color: var(--hc-navy);
            }

            .hero {
                padding: 1.1rem 1.4rem;
                background: linear-gradient(120deg, rgba(14, 43, 69, 0.95) 0%, rgba(15, 122, 122, 0.95) 100%);
                border-radius: 18px;
                color: #f8fdff;
                margin-bottom: 1rem;
                box-shadow: 0 10px 24px rgba(14, 43, 69, 0.2);
            }

            .hero h2 { color: #f8fdff !important; margin: 0; }
            .hero p { margin: 0.45rem 0 0; color: rgba(245, 252, 255, 0.94); }

            .summary-box {
                padding: 0.95rem 1rem;
                border-radius: 14px;
                border: 1px solid var(--hc-border);
                background: linear-gradient(180deg, #ffffff 0%, #f7fbfe 100%);
                margin-bottom: 0.85rem;
            }

            .kpi-card {
                padding: 0.75rem;
                border-radius: 14px;
                border: 1px solid var(--hc-border);
                background: var(--hc-card);
            }

            .kpi-label { color: #476277; font-size: 0.86rem; margin-bottom: 0.2rem; }
            .kpi-value { color: var(--hc-navy); font-weight: 700; font-size: 1.2rem; }

            .mono-card {
                border: 1px solid var(--hc-border);
                border-radius: 14px;
                background: #ffffff;
                padding: 0.9rem 1rem;
                margin-bottom: 0.9rem;
            }

            .mono-meta {
                color: #476277;
                font-size: 0.88rem;
                margin-bottom: 0.45rem;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def format_amount(amount: Any) -> str:
    try:
        return f"{float(amount):,.2f}"
    except (TypeError, ValueError):
        return str(amount or "")


def products_to_df(products: List[Dict[str, Any]]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for item in products:
        package_ndc = ""
        packaging = item.get("packaging") or []
        if packaging:
            package_ndc = packaging[0].get("package_ndc", "")

        rows.append(
            {
                "Product NDC": item.get("product_ndc", ""),
                "Package NDC": package_ndc,
                "Brand": item.get("brand_name", ""),
                "Generic": item.get("generic_name", ""),
                "Labeler": item.get("labeler_name", ""),
                "Dosage Form": item.get("dosage_form", ""),
                "Route": ", ".join(item.get("route", []) or []),
                "Application #": item.get("application_number", ""),
            }
        )

    return pd.DataFrame(rows)


def costs_to_df(cost_rows: List[Dict[str, Any]]) -> pd.DataFrame:
    columns = ["Source", "Price Type", "Amount (USD)", "Unit", "As Of", "Source URL", "Notes"]
    if not cost_rows:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(cost_rows).rename(
        columns={
            "source": "Source",
            "price_type": "Price Type",
            "amount_usd": "Amount (USD)",
            "unit": "Unit",
            "as_of": "As Of",
            "url": "Source URL",
            "notes": "Notes",
        }
    )
    if "Amount (USD)" in df.columns:
        df["Amount (USD)"] = df["Amount (USD)"].apply(format_amount)
    return df


def logs_to_df(source_logs: List[Dict[str, Any]]) -> pd.DataFrame:
    columns = ["Source", "URL", "Checked On"]
    if not source_logs:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(source_logs).rename(
        columns={
            "source": "Source",
            "url": "URL",
            "checked_on": "Checked On",
        }
    )
    for column in columns:
        if column not in df.columns:
            df[column] = ""
    return df[columns].drop_duplicates().reset_index(drop=True)


def label_sections_to_df(label_result: Dict[str, Any]) -> pd.DataFrame:
    sections = label_result.get("sections", {})
    quality = label_result.get("section_quality", {})
    as_of = label_result.get("as_of", "")
    retrieved_on = label_result.get("retrieved_on", "")

    rows = []
    mapping = [
        ("indications_and_usage", "Indications and Usage"),
        ("contraindications", "Contraindications"),
        ("adverse_reactions", "Adverse Reactions"),
    ]
    for key, title in mapping:
        rows.append(
            {
                "Section": title,
                "Summary Text": sections.get(key, ""),
                "Status": quality.get(key, {}).get("status", "missing"),
                "Source Field": quality.get(key, {}).get("source_field", ""),
                "Truncated": quality.get(key, {}).get("was_truncated", False),
                "Data Currency": as_of,
                "Retrieved On": retrieved_on,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df[df["Summary Text"].fillna("").astype(str).str.strip() != ""].reset_index(drop=True)


def toxicity_to_df(toxicity_result: Dict[str, Any]) -> pd.DataFrame:
    rows = toxicity_result.get("profiles", []) or []
    if not rows:
        return pd.DataFrame(columns=["Source", "Profile Type", "Severity", "Finding", "As Of", "URL", "Notes"])
    return pd.DataFrame(rows).rename(
        columns={
            "source": "Source",
            "profile_type": "Profile Type",
            "severity": "Severity",
            "finding": "Finding",
            "as_of": "As Of",
            "url": "URL",
            "notes": "Notes",
        }
    )


def interactions_to_df(interaction_result: Dict[str, Any]) -> pd.DataFrame:
    rows = interaction_result.get("interactions", []) or []
    if not rows:
        return pd.DataFrame(
            columns=["Source", "Interacting Drug", "Severity", "Mechanism", "Evidence", "As Of", "URL", "Notes"]
        )
    return pd.DataFrame(rows).rename(
        columns={
            "source": "Source",
            "interacting_drug": "Interacting Drug",
            "severity": "Severity",
            "mechanism": "Mechanism",
            "evidence_text": "Evidence",
            "as_of": "As Of",
            "url": "URL",
            "notes": "Notes",
        }
    )


def csv_download(df: pd.DataFrame, label: str, file_name: str, key: str) -> None:
    st.download_button(
        label=label,
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=file_name,
        mime="text/csv",
        use_container_width=True,
        key=key,
    )


def build_session_excel(history: List[Dict[str, Any]]) -> bytes:
    """Build an Excel workbook with one sheet group per query result in history."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:  # type: ignore[call-overload]
        for idx, result in enumerate(history):
            query_label = str(result.get("query", f"Query {idx + 1}"))[:20].strip()
            prefix = f"Q{idx + 1}"

            ndc_result = result.get("ndc", {})
            cost_result = result.get("cost", {})
            label_result = result.get("label", {})
            toxicity_result = result.get("toxicity", {})
            interaction_result = result.get("interactions", {})

            products_df = products_to_df(ndc_result.get("products", []))
            costs_df = costs_to_df(cost_result.get("cost_rows", []))
            label_df = label_sections_to_df(label_result)
            tox_df = toxicity_to_df(toxicity_result)
            inter_df = interactions_to_df(interaction_result)
            logs_df = logs_to_df(
                (cost_result.get("source_logs", []) or [])
                + (label_result.get("source_logs", []) or [])
                + (toxicity_result.get("source_logs", []) or [])
                + (interaction_result.get("source_logs", []) or [])
            )

            # Add a Query column to source logs for traceability
            if not logs_df.empty:
                logs_df.insert(0, "Query", query_label)

            def _write(df: pd.DataFrame, sheet_suffix: str) -> None:
                if df.empty:
                    return
                sheet_name = f"{prefix} {sheet_suffix}"[:31]  # Excel sheet name limit
                df.to_excel(writer, sheet_name=sheet_name, index=False)

            _write(products_df, "NDC Docs")
            _write(label_df, "FDA Label")
            _write(costs_df, "Cost")
            _write(tox_df, "Toxicity")
            _write(inter_df, "Interactions")
            _write(logs_df, "Source Logs")

    return buf.getvalue()


def strip_inline_references(text: str) -> str:
    cleaned = text.translate(str.maketrans("", "", "\u00b9\u00b2\u00b3\u2070\u2074\u2075\u2076\u2077\u2078\u2079"))
    cleaned = re.sub(r"\[(?:\d+|[A-Za-z])\]", "", cleaned)
    cleaned = re.sub(r"(?<=\w)\((?:\d+|[A-Za-z])\)", "", cleaned)
    return cleaned


def strip_section_heading(text: str, section: str) -> str:
    heading_pattern = re.escape(section.upper())
    patterns = [
        rf"^\s*{heading_pattern}\s*[:.-]?\s*",
        rf"^\s*{re.escape(section)}\s*[:.-]?\s*",
    ]
    stripped = text
    for pattern in patterns:
        stripped = re.sub(pattern, "", stripped, flags=re.IGNORECASE)
    return stripped.strip()


def build_key_takeaways(text: str, max_points: int = 3) -> List[str]:
    if not text.strip():
        return []
    chunks = re.split(r"(?<=[.!?])\s+", text.strip())
    takeaways = [chunk.strip() for chunk in chunks if chunk.strip() and chunk.strip().endswith((".", "!", "?"))]
    return takeaways[:max_points]


def normalize_clinical_text(text: str, section: str = "") -> str:
    normalized = strip_inline_references(text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    normalized = normalized.strip()
    if section:
        normalized = strip_section_heading(normalized, section)
    return normalized.strip()


def split_embedded_list_items(block: str) -> List[str]:
    compact = re.sub(r"\s+", " ", block).strip()
    markers = re.findall(r"(?:^|\s)(?:\(?\d{1,2}[.)])\s+", compact)
    if len(markers) < 2:
        return []

    pieces = re.split(r"(?=(?:\(?\d{1,2}[.)])\s+)", compact)
    items: List[str] = []
    for piece in pieces:
        cleaned = re.sub(r"^\(?\d{1,2}[.)]\s*", "", piece.strip(" ;"))
        if cleaned:
            items.append(cleaned)
    return items


def format_clinical_blocks(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []

    blocks: List[Dict[str, Any]] = []
    for raw_block in [item.strip() for item in text.split("\n\n") if item.strip()]:
        raw_lines = [line.strip() for line in raw_block.split("\n") if line.strip()]
        compact_block = re.sub(r"\s+", " ", raw_block).strip()

        if len(raw_lines) > 1 and all(re.match(r"^(?:[-*•]|\(?\d{1,2}[.)]|[A-Za-z][.)])\s+", line) for line in raw_lines):
            items = [re.sub(r"^(?:[-*•]|\(?\d{1,2}[.)]|[A-Za-z][.)])\s+", "", line).strip() for line in raw_lines]
            items = [item for item in items if item]
            if items:
                blocks.append({"type": "list", "items": items})
                continue

        numbered_markers = list(re.finditer(r"(?:^|\s)(?=\(?\d{1,2}[.)]\s+)", compact_block))
        if len(numbered_markers) >= 2:
            prefix = compact_block[: numbered_markers[0].start()].strip(" ;")
            embedded_items = split_embedded_list_items(compact_block[numbered_markers[0].start() :].strip())
            if prefix:
                blocks.append({"type": "paragraph", "text": prefix})
            if embedded_items:
                blocks.append({"type": "list", "items": embedded_items})
                continue

        paragraph = compact_block
        sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", paragraph) if item.strip()]
        if len(sentences) >= 4 and len(paragraph) > 500:
            for index in range(0, len(sentences), 2):
                pair = " ".join(sentences[index : index + 2]).strip()
                if pair:
                    blocks.append({"type": "paragraph", "text": pair})
            continue

        if len(paragraph) <= 100 and (paragraph.endswith(":") or paragraph.isupper()):
            blocks.append({"type": "heading", "text": paragraph.rstrip(":")})
            continue

        blocks.append({"type": "paragraph", "text": paragraph})

    return blocks


def render_formatted_clinical_text(text: str) -> None:
    for block in format_clinical_blocks(text):
        if block["type"] == "list":
            st.markdown("\n".join(f"- {item}" for item in block["items"]))
        elif block["type"] == "heading":
            st.markdown(f"**{block['text']}**")
        else:
            st.write(block["text"])


def render_monograph_section(row: pd.Series) -> None:
    section = str(row.get("Section", ""))
    text = str(row.get("Summary Text", "")).strip()
    status = str(row.get("Status", "missing"))
    source_field = str(row.get("Source Field", ""))
    truncated = bool(row.get("Truncated", False))
    as_of = str(row.get("Data Currency", ""))
    retrieved_on = str(row.get("Retrieved On", ""))

    st.subheader(section)
    st.markdown("<div class='mono-card'>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='mono-meta'>Status: {status} | Source field: {source_field or 'n/a'} | "
        f"Data currency: {as_of or 'n/a'} | Retrieved: {retrieved_on or 'n/a'}"
        + (" | Truncated preview: yes" if truncated else "")
        + "</div>",
        unsafe_allow_html=True,
    )

    if not text:
        st.warning("No section text available from FDA label source for this query.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    display_text = normalize_clinical_text(text, section=section)
    takeaways = build_key_takeaways(display_text)
    if takeaways:
        st.markdown("**Key Takeaways**")
        for point in takeaways:
            st.write(f"- {point}")

    st.markdown("**Detailed Clinical Narrative**")
    render_formatted_clinical_text(display_text)
    st.markdown("</div>", unsafe_allow_html=True)


def manufacturer_chart(products_df: pd.DataFrame) -> None:
    if products_df.empty:
        st.info("No manufacturer distribution available.")
        return

    chart_df = (
        products_df[products_df["Labeler"] != ""]
        .groupby("Labeler", as_index=False)
        .size()
        .sort_values("size", ascending=False)
        .head(10)
    )
    if chart_df.empty:
        st.info("No manufacturer distribution available.")
        return

    chart = (
        alt.Chart(chart_df)
        .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
        .encode(
            x=alt.X("size:Q", title="Matched Products"),
            y=alt.Y("Labeler:N", sort="-x", title="Manufacturer / Labeler"),
            tooltip=["Labeler", "size"],
            color=alt.Color("size:Q", legend=None, scale=alt.Scale(scheme="teals")),
        )
        .properties(height=300)
    )
    st.altair_chart(chart, use_container_width=True)


def render_kpi_cards(metrics: List[tuple[str, str]]) -> None:
    if not metrics:
        return

    cols = st.columns(len(metrics))
    for idx, (label, value) in enumerate(metrics):
        with cols[idx]:
            st.markdown(
                f"""
                <div class='kpi-card'>
                    <div class='kpi-label'>{label}</div>
                    <div class='kpi-value'>{value}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _build_prefix_notice(ndc_result: Dict[str, Any], queried_ndc: str) -> str | None:
    """Return a human-readable notice when an NDC was not found but its labeler prefix was matched."""
    if not ndc_result.get("ndc_prefix_match"):
        return None
    labeler_prefix = ndc_result.get("labeler_prefix") or ""
    products = ndc_result.get("products") or []
    if not products:
        return None

    # Derive labeler name from matched products (all share the same labeler)
    labeler_name = next(
        (str(p.get("labeler_name", "")).strip() for p in products if p.get("labeler_name")),
        "an unknown labeler",
    )

    # Collect unique brand names with their generic names
    seen: set = set()
    drug_labels: List[str] = []
    for p in products:
        brand = str(p.get("brand_name", "")).strip()
        generic = str(p.get("generic_name", "")).strip()
        if brand and brand.upper() not in seen:
            seen.add(brand.upper())
            label = f"**{brand.upper()}** ({generic})" if generic else f"**{brand.upper()}**"
            drug_labels.append(label)
        if len(drug_labels) >= 5:
            break

    if not drug_labels:
        return None

    displayed = ", ".join(drug_labels[:3])
    remainder = len(drug_labels) - 3
    if remainder > 0:
        displayed += f", and {remainder} other product{'s' if remainder > 1 else ''}"

    return (
        f"NDC **{queried_ndc}** is not explicitly listed in openFDA, but the labeler prefix "
        f"**{labeler_prefix}** belongs to **{labeler_name}** and is associated with: {displayed}."
    )


def render_identity_section(result: Dict[str, Any], products_df: pd.DataFrame, key_suffix: str) -> None:
    ndc_result = result.get("ndc", {}) or {}
    selected = ndc_result.get("selected_drug") or {}

    st.header("NDC and Manufacturer Documentation")

    # Prefix fallback notice — shown when the queried NDC wasn't found but labeler prefix was matched
    queried_ndc = str(ndc_result.get("query", "")).strip()
    prefix_notice = _build_prefix_notice(ndc_result, queried_ndc)
    if prefix_notice:
        st.warning(prefix_notice)

    if selected:
        pass  # selected drug detail is already present in products_df rows

    left, right = st.columns([1.2, 1])
    with left:
        st.dataframe(products_df, use_container_width=True, height=300)
        csv_download(products_df, "Download NDC Documentation (CSV)", f"ndc_products_{key_suffix}.csv", f"ndc_csv_{key_suffix}")
    with right:
        manufacturer_chart(products_df)


def render_cost_section(costs_df: pd.DataFrame, key_suffix: str) -> None:
    st.header("Cost Intelligence")
    if costs_df.empty:
        st.info("Cost intelligence is unavailable for this run.")
        return

    st.dataframe(costs_df, use_container_width=True, height=300)
    csv_download(costs_df, "Download Cost Intelligence (CSV)", f"drug_costs_{key_suffix}.csv", f"cost_csv_{key_suffix}")


def render_toxicity_section(tox_df: pd.DataFrame, key_suffix: str) -> None:
    st.header("Toxicity Profiles (NIH and U.S. FDA)")
    if tox_df.empty:
        st.info("No toxicity profile findings were extracted for this query.")
        return

    st.markdown(
        """
        <div class='summary-box'>
            Documented toxicity findings are sourced from NIH PubChem and FDA safety sections when available.
            Review severity and evidence text before presentation use.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.dataframe(tox_df, use_container_width=True, height=300)
    csv_download(
        tox_df,
        "Download Toxicity Profiles (CSV)",
        f"toxicity_profiles_{key_suffix}.csv",
        f"toxicity_csv_{key_suffix}",
    )


def render_interactions_section(inter_df: pd.DataFrame, key_suffix: str) -> None:
    st.header("Drug Interactions (NIH and U.S. FDA)")
    if inter_df.empty:
        st.info("No interaction findings found for this query.")
        return

    st.markdown(
        """
        <div class='summary-box'>
            Interaction findings include NIH RxNav pair evidence and FDA label narrative context where available.
            Verify severity and mechanism fields against source links before operational use.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.dataframe(inter_df, use_container_width=True, height=300)
    csv_download(
        inter_df,
        "Download Drug Interactions (CSV)",
        f"drug_interactions_{key_suffix}.csv",
        f"interactions_csv_{key_suffix}",
    )


def monograph_reference_link(label_result: Dict[str, Any]) -> str:
    label_metadata = label_result.get("label_metadata", {}) or {}
    spl_set_id = str(label_metadata.get("spl_set_id", "")).strip()
    if spl_set_id:
        return f"https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid={spl_set_id}"

    source_logs = label_result.get("source_logs", []) or []
    for item in source_logs:
        if str(item.get("url", "")).strip():
            return str(item.get("url", "")).strip()
    return "https://api.fda.gov/drug/label.json"


def render_label_monograph(label_df: pd.DataFrame, label_result: Dict[str, Any]) -> None:
    st.header("FDA Clinical Monograph")
    row_quality = label_result.get("label_row_quality", {})
    reference_url = monograph_reference_link(label_result)
    st.markdown(
        """
        <div class='summary-box'>
            This section presents FDA label content in a documented clinical-monograph format instead of a raw dump.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(
        "Best-row selection quality: "
        f"{row_quality.get('selected_coverage', 0)}/3 sections | "
        f"effective time {row_quality.get('selected_effective_time', 'n/a')} | "
        f"reason {row_quality.get('selection_reason', 'n/a')}"
    )

    if label_df.empty:
        st.info("FDA label content was not available for the requested monograph sections.")
        return

    for _, row in label_df.iterrows():
        render_monograph_section(row)

    st.markdown(f"Reference: [FDA Clinical Label]({reference_url})")


def render_source_logs(logs_df: pd.DataFrame, key_suffix: str) -> None:
    st.header("Source Logs")
    st.dataframe(logs_df, use_container_width=True, height=240)
    csv_download(logs_df, "Download Source Logs (CSV)", f"source_logs_{key_suffix}.csv", f"logs_csv_{key_suffix}")


def render_follow_ups(result: Dict[str, Any], history_index: int) -> None:
    suggestions = result.get("follow_up_suggestions", [])
    if not suggestions:
        return

    st.header("Suggested Follow-Up Questions")
    for idx, suggestion in enumerate(suggestions):
        if st.button(suggestion, key=f"followup_{history_index}_{idx}", use_container_width=True):
            st.session_state["pending_intelligence_query"] = suggestion
            st.session_state["auto_run_intelligence_query"] = True
            st.rerun()


def is_interaction_only_view(query_context: Dict[str, Any]) -> bool:
    intents = set(query_context.get("intents", []))
    label_intents = {"label_usage", "contraindications", "adverse_reactions", "ndc_identity", "executive_summary"}
    return (
        bool(query_context.get("needs_interactions"))
        and not bool(query_context.get("needs_cost"))
        and not bool(query_context.get("needs_toxicity"))
        and query_context.get("focus_area") == "drug_interactions"
        and not bool(intents & label_intents)
    )


def is_toxicity_only_view(query_context: Dict[str, Any]) -> bool:
    intents = set(query_context.get("intents", []))
    label_intents = {"label_usage", "contraindications", "adverse_reactions", "ndc_identity", "executive_summary"}
    return (
        bool(query_context.get("needs_toxicity"))
        and not bool(query_context.get("needs_cost"))
        and not bool(query_context.get("needs_interactions"))
        and query_context.get("focus_area") == "toxicity"
        and not bool(intents & label_intents)
    )


def is_cost_only_view(query_context: Dict[str, Any]) -> bool:
    intents = set(query_context.get("intents", []))
    label_intents = {"label_usage", "contraindications", "adverse_reactions", "ndc_identity", "executive_summary"}
    return (
        bool(query_context.get("needs_cost"))
        and not bool(query_context.get("needs_toxicity"))
        and not bool(query_context.get("needs_interactions"))
        and query_context.get("focus_area") == "cost"
        and not bool(intents & label_intents)
    )


def has_requested_details(
    query_context: Dict[str, Any],
    products_df: pd.DataFrame,
    costs_df: pd.DataFrame,
    label_df: pd.DataFrame,
    tox_df: pd.DataFrame,
    inter_df: pd.DataFrame,
) -> bool:
    checks: List[bool] = []

    if bool(query_context.get("needs_label")):
        checks.append(not label_df.empty)
    if bool(query_context.get("needs_cost")):
        checks.append(not costs_df.empty)
    if bool(query_context.get("needs_toxicity")):
        checks.append(not tox_df.empty)
    if bool(query_context.get("needs_interactions")):
        checks.append(not inter_df.empty)

    focus_area = str(query_context.get("focus_area", "")).strip()
    if focus_area in {"ndc_identity", "executive_summary"} or not checks:
        checks.append(not products_df.empty)

    return any(checks)


def get_result_history() -> List[Dict[str, Any]]:
    if "result_history" not in st.session_state:
        legacy_result = st.session_state.get("result")
        st.session_state["result_history"] = [legacy_result] if legacy_result else []
    return st.session_state["result_history"]


def get_rendered_sections() -> set:
    """Track which section types have already been rendered this session."""
    if "rendered_sections" not in st.session_state:
        st.session_state["rendered_sections"] = set()
    return st.session_state["rendered_sections"]


def mark_section_rendered(section: str) -> None:
    get_rendered_sections().add(section)


def is_section_rendered(section: str) -> bool:
    return section in get_rendered_sections()


def append_result_history(result: Dict[str, Any]) -> None:
    history = get_result_history()
    history.append(result)
    st.session_state["result_history"] = history
    st.session_state["result"] = result


def run_drug_query(query: str) -> None:
    orchestrator = get_orchestrator()
    history = get_result_history()
    is_first = len(history) == 0
    # Collect all intents already used in this session
    session_intents: set = set()
    for past in history:
        session_intents.update(past.get("query_context", {}).get("intents", []))
    rendered_sections = get_rendered_sections()
    with st.spinner("Running intent-aware research workflow..."):
        result = orchestrator.run(
            query=query,
            ndc_limit=DEFAULT_NDC_LIMIT,
            is_first_query=is_first,
            session_intents=session_intents,
            rendered_sections=rendered_sections,
        )
    append_result_history(result)


def render_result_payload(result: Dict[str, Any], history_index: int, show_follow_ups: bool) -> None:
    query_context = result.get("query_context", {})
    needs_cost = bool(query_context.get("needs_cost"))
    needs_toxicity = bool(query_context.get("needs_toxicity"))
    needs_interactions = bool(query_context.get("needs_interactions"))
    interaction_only_view = is_interaction_only_view(query_context)
    toxicity_only_view = is_toxicity_only_view(query_context)
    cost_only_view = is_cost_only_view(query_context)
    ndc_already_shown = is_section_rendered("ndc_identity")

    ndc_result = result.get("ndc", {})
    cost_result = result.get("cost", {})
    label_result = result.get("label", {})
    toxicity_result = result.get("toxicity", {})
    interaction_result = result.get("interactions", {})

    products_df = products_to_df(ndc_result.get("products", []))
    costs_df = costs_to_df(cost_result.get("cost_rows", []))
    label_df = label_sections_to_df(label_result)
    tox_df = toxicity_to_df(toxicity_result)
    inter_df = interactions_to_df(interaction_result)
    logs_df = logs_to_df(
        (cost_result.get("source_logs", []) or [])
        + (label_result.get("source_logs", []) or [])
        + (toxicity_result.get("source_logs", []) or [])
        + (interaction_result.get("source_logs", []) or [])
    )

    key_suffix = f"result_{history_index + 1}"
    if history_index > 0:
        st.markdown("---")

    st.markdown(
        f"""
        <div class='summary-box'>
            Query: <strong>{result.get('query', '')}</strong><br/>
            Resolved entity: <strong>{result.get('resolved_query', '')}</strong><br/>
            Generated at (UTC): <strong>{result.get('generated_at', '')}</strong><br/>
            Workflow: <strong>{', '.join(result.get('workflow', []))}</strong>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not has_requested_details(query_context, products_df, costs_df, label_df, tox_df, inter_df):
        st.info("No details were found.")
        if show_follow_ups:
            render_follow_ups(result, history_index)
        return

    metrics = [("Matched Products", str(len(products_df.index)))]
    if interaction_only_view:
        metrics = [("Interaction Findings", str(len(inter_df.index)))]
    elif toxicity_only_view:
        metrics = [("Toxicity Findings", str(len(tox_df.index)))]
    elif cost_only_view:
        metrics = [("Cost Points", str(len(costs_df.index)))]
    else:
        if needs_cost:
            metrics.append(("Cost Points", str(len(costs_df.index))))
        if needs_toxicity:
            metrics.append(("Toxicity Findings", str(len(tox_df.index))))
        if needs_interactions:
            metrics.append(("Interaction Findings", str(len(inter_df.index))))
    render_kpi_cards(metrics)

    if interaction_only_view:
        render_interactions_section(inter_df, key_suffix)
        if show_follow_ups:
            render_follow_ups(result, history_index)
        return

    if toxicity_only_view:
        render_toxicity_section(tox_df, key_suffix)
        render_source_logs(logs_df, key_suffix)
        if show_follow_ups:
            render_follow_ups(result, history_index)
        return

    if cost_only_view:
        render_cost_section(costs_df, key_suffix)
        render_source_logs(logs_df, key_suffix)
        if show_follow_ups:
            render_follow_ups(result, history_index)
        return

    if not ndc_already_shown:
        render_identity_section(result, products_df, key_suffix)
        mark_section_rendered("ndc_identity")
    label_requested = bool(set(query_context.get("intents", [])) & {"label_usage", "contraindications", "adverse_reactions"})
    if label_requested and not label_df.empty:
        render_label_monograph(label_df, label_result)

    if needs_toxicity:
        render_toxicity_section(tox_df, key_suffix)
    if needs_interactions:
        render_interactions_section(inter_df, key_suffix)
    if needs_cost:
        render_cost_section(costs_df, key_suffix)

    render_source_logs(logs_df, key_suffix)
    if show_follow_ups:
        render_follow_ups(result, history_index)


def render_page_guide() -> None:
    left, right = st.columns([12, 1])
    with left:
        st.caption("Use the page guide to understand what each section and KPI represents.")
    with right:
        if hasattr(st, "popover"):
            with st.popover("i", help="Page guide"):
                st.markdown("### Page Guide")
                for title, description in SECTION_GUIDE:
                    st.markdown(f"**{title}**")
                    st.write(description)
        else:
            with st.expander("Page Guide"):
                for title, description in SECTION_GUIDE:
                    st.markdown(f"**{title}**")
                    st.write(description)


def load_uploaded_table(uploaded_file: Any) -> pd.DataFrame:
    file_name = str(uploaded_file.name).lower()
    if hasattr(uploaded_file, "getvalue"):
        raw_bytes = uploaded_file.getvalue()
    else:
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)
        raw_bytes = uploaded_file.read()
    if file_name.endswith(".csv"):
        return pd.read_csv(io.BytesIO(raw_bytes), dtype=str, keep_default_na=False)
    if file_name.endswith(".xlsx"):
        return pd.read_excel(io.BytesIO(raw_bytes), dtype=str, keep_default_na=False, engine="openpyxl")
    raise ValueError("Only CSV and XLSX files are supported.")


def build_sheet_conversion_df(df: pd.DataFrame, selected_column: str, lookup_fda_name: bool = True) -> pd.DataFrame:
    converted_rows = pd.DataFrame(
        batch_convert_ndcs(df[selected_column].fillna("").astype(str).tolist(), lookup_fda_name=lookup_fda_name)
    ).reset_index(drop=True)
    output_df = df.copy().reset_index(drop=True)

    # Use next() scan instead of list.index() — survives duplicate / whitespace-padded
    # column names that previously caused ValueError: list.index(x): x not in list.
    insert_at = next(
        (i + 1 for i, c in enumerate(output_df.columns.tolist()) if c == selected_column),
        len(output_df.columns),
    )

    added = pd.DataFrame({
        "11-Digit NDC": converted_rows["11-Digit NDC"],
        "FDA Drug Name": converted_rows["FDA Drug Name"],
        "Conversion Mode": converted_rows["Mode"],
        "Conversion Status": converted_rows["Status"],
        "Conversion Reason": converted_rows["Reason"],
        "Confidence": converted_rows["Confidence"],
        "Validation Source": converted_rows["Validation Source"],
        "Candidate 1": converted_rows["Candidate 1"],
        "Candidate 2": converted_rows["Candidate 2"],
        "Candidate 3": converted_rows["Candidate 3"],
    })

    left = output_df.iloc[:, :insert_at].reset_index(drop=True)
    right = output_df.iloc[:, insert_at:].reset_index(drop=True)
    return pd.concat([left, added, right], axis=1).reset_index(drop=True)


def render_converter_results(df: pd.DataFrame, prefix: str, file_name: str) -> None:
    # Sheet results rename "Mode" → "Conversion Mode"; handle both column names
    mode_col = "Mode" if "Mode" in df.columns else "Conversion Mode"
    status_col = "Status" if "Status" in df.columns else "Conversion Status"
    reason_col = "Reason" if "Reason" in df.columns else "Conversion Reason"
    input_col = "Input NDC" if "Input NDC" in df.columns else None

    direct_df = df[df[mode_col] == "direct_conversion"].copy()
    review_df = df[df[mode_col] == "review_required"].copy()
    unsupported_df = df[df[mode_col] == "unsupported"].copy()

    def _cols(*names: str) -> list:
        return [c for c in names if c is not None and c in df.columns]

    if not direct_df.empty:
        st.subheader("Direct Conversion Results")
        st.dataframe(
            direct_df[_cols(input_col, "11-Digit NDC", "FDA Drug Name", status_col)],
            use_container_width=True,
            height=220,
        )

    if not review_df.empty:
        st.subheader("Needs Review")
        st.caption("Raw 9-10 digit values without hyphens need review before choosing one FDA-recognized candidate.")
        st.dataframe(
            review_df[_cols(input_col, status_col, reason_col, "Candidate 1", "Candidate 2", "Candidate 3")],
            use_container_width=True,
            height=220,
        )

    if not unsupported_df.empty:
        st.subheader("Unsupported Inputs")
        st.dataframe(
            unsupported_df[_cols(input_col, status_col, reason_col)],
            use_container_width=True,
            height=180,
        )

    csv_download(df, "Download Conversion Results (CSV)", file_name, f"converter_csv_{prefix}")


def render_ndc_converter_tab() -> None:
    st.header("NDC Converter")
    st.markdown(
        """
        <div class='summary-box'>
            Hyphenated 9-10 digit NDCs convert directly to 11 digits.
            Raw 9-10 digit values without hyphens are separated into a review-required table.
        </div>
        """,
        unsafe_allow_html=True,
    )

    include_fda_name = st.checkbox(
        "Include FDA drug name lookup",
        value=True,
        key="converter_fda_name_checkbox",
        help="When checked, each converted NDC is looked up in openFDA to retrieve the brand and generic drug name. "
             "Uncheck to skip the lookup and speed up bulk conversions.",
    )

    multiline_input = st.text_area(
        "Paste NDC values (one per line)",
        height=180,
        placeholder="0002-8215-01\n60574-4114-1\n1234-5678-1\n00002821501",
        key="converter_multiline_input",
    )
    if st.button("Convert Pasted NDCs", key="converter_multiline_run", use_container_width=True):
        values = [line.strip() for line in multiline_input.splitlines() if line.strip()]
        st.session_state["converter_multiline_results"] = pd.DataFrame(
            batch_convert_ndcs(values, lookup_fda_name=include_fda_name)
        )

    multiline_results = st.session_state.get("converter_multiline_results")
    if isinstance(multiline_results, pd.DataFrame) and not multiline_results.empty:
        st.subheader("Pasted NDC Results")
        render_converter_results(multiline_results, "multiline", "ndc_converter_multiline.csv")

    uploaded_file = st.file_uploader(
        "Upload a CSV or XLSX sheet",
        type=["csv", "xlsx"],
        key="converter_upload",
    )
    if uploaded_file is not None:
        try:
            uploaded_df = load_uploaded_table(uploaded_file)
        except Exception as exc:
            st.error(f"Unable to read uploaded file: {exc}")
            uploaded_df = pd.DataFrame()

        if not uploaded_df.empty:
            detected_columns = detect_ndc_columns(uploaded_df)
            options = list(uploaded_df.columns)
            default_column = next(
                (c for c in detected_columns if c in options),
                options[0] if options else None,
            )
            default_index = options.index(default_column) if default_column in options else 0
            selected_column = st.selectbox(
                "Select the column to convert",
                options=options,
                index=default_index,
                key="converter_selected_column",
            )
            if detected_columns:
                st.caption(f"Detected likely NDC columns: {', '.join(detected_columns[:3])}")
            else:
                st.caption("No obvious NDC column was detected automatically. Select the correct column manually.")

            st.dataframe(uploaded_df.head(10), use_container_width=True, height=220)
            if st.button("Convert Uploaded Sheet", key="converter_sheet_run", use_container_width=True):
                st.session_state["converter_sheet_results"] = build_sheet_conversion_df(
                    uploaded_df, selected_column, lookup_fda_name=include_fda_name
                )

    sheet_results = st.session_state.get("converter_sheet_results")
    if isinstance(sheet_results, pd.DataFrame) and not sheet_results.empty:
        st.subheader("Sheet Conversion Results")
        render_converter_results(sheet_results, "sheet", "ndc_converter_sheet.csv")


def render_drug_intelligence_tab() -> None:
    pending_query = st.session_state.pop("pending_intelligence_query", None)
    if pending_query is not None:
        st.session_state["intelligence_query_input"] = pending_query
    elif "intelligence_query_input" not in st.session_state:
        st.session_state["intelligence_query_input"] = ""

    auto_run = bool(st.session_state.pop("auto_run_intelligence_query", False))

    st.text_input(
        "Ask a drug intelligence question",
        placeholder="Example: contraindications and interactions for metformin",
        key="intelligence_query_input",
    )
    run_clicked = st.button("Generate Intelligence", type="primary", use_container_width=True, key="run_intelligence")
    query = st.session_state.get("intelligence_query_input", "").strip()
    if (run_clicked or auto_run) and query:
        run_drug_query(query)

    history = get_result_history()
    if not history:
        st.info("Enter a question and select Generate Intelligence.")
        return

    latest_index = len(history) - 1

    # Session-level Excel export
    excel_bytes = build_session_excel(history)
    st.download_button(
        label="Download Full Session as Excel",
        data=excel_bytes,
        file_name="drug_intelligence_session.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        key="session_excel_download",
    )

    for history_index, result in enumerate(history):
        render_result_payload(result, history_index, show_follow_ups=history_index == latest_index)


def main() -> None:
    inject_healthcare_theme()

    st.markdown(
        """
        <div class='hero'>
            <h2>Drug Intelligence Studio</h2>
            <p>
                Documented NDC, FDA clinical label sections, NIH/FDA toxicity profiles, drug interaction intelligence,
                and a standalone NDC 11-digit converter.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_page_guide()
    intelligence_tab, converter_tab = st.tabs(["Drug Intelligence", "NDC Converter"])

    with intelligence_tab:
        render_drug_intelligence_tab()

    with converter_tab:
        render_ndc_converter_tab()


if __name__ == "__main__":
    main()
