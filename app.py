import io
import os
import math
import re
import stat
import tempfile
from pathlib import Path
from dotenv import load_dotenv
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# from processor_with_classes import process_uploaded_files, build_validation_report
from processor_groq import process_uploaded_files, build_validation_report
from exporter import build_standardized_workbook
from export_dashboard import main as export_live_dashboard

# Local development opts in via .env: APP_MODE=local.
# Streamlit Cloud defaults safely to live/read-only because .env is not committed.
load_dotenv()
APP_MODE = os.getenv("APP_MODE", "live").strip().lower()
IS_LIVE = APP_MODE == "live"

SAVED_WORKBOOK = (
    Path(__file__).with_name("processed_cache")
    / "latest_processed_analysis.xlsx"
)


def save_cumulative_workbook(workbook_bytes: bytes) -> None:
    """Windows-safe write of the cumulative workbook."""
    SAVED_WORKBOOK.parent.mkdir(parents=True, exist_ok=True)
    if SAVED_WORKBOOK.exists():
        try:
            SAVED_WORKBOOK.chmod(SAVED_WORKBOOK.stat().st_mode | stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass

    fd, tmp_name = tempfile.mkstemp(
        prefix="latest_processed_analysis.", suffix=".tmp.xlsx", dir=str(SAVED_WORKBOOK.parent)
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_bytes(workbook_bytes)
        os.replace(tmp, SAVED_WORKBOOK)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write {SAVED_WORKBOOK}. Close the workbook if it is open and make sure the project folder is writable."
        ) from exc
    finally:
        tmp.unlink(missing_ok=True)


def refresh_fast_frontend() -> None:
    """Export the cumulative workbook to frontend/dashboard_data.json automatically."""
    export_live_dashboard()


def load_saved_processed_workbook(path):
    """Load the persisted seven-sheet processed workbook."""
    xls = pd.ExcelFile(path)

    raw = pd.read_excel(xls, "Raw_Data")
    turns = pd.read_excel(xls, "Turn_Level_Analysis")
    requests = pd.read_excel(xls, "Code_Request_Events")
    sessions = pd.read_excel(xls, "Session_Summary")
    questions = pd.read_excel(xls, "Question_Summary")
    validation = pd.read_excel(xls, "Validation_Report")

    return raw, turns, sessions, questions, requests, validation

st.set_page_config(
    page_title="Conversational Assessment Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -----------------------------------------------------------------------------
# Visual system — modern SaaS palette (Linear / Stripe / Notion inspired)
# Neutral, low-noise background; a single confident accent color; restrained
# semantic colors used only where they carry meaning (risk / warning / good).
# -----------------------------------------------------------------------------
INK = "#0F172A"          # primary text
INK_MUTED = "#64748B"    # secondary text
INK_FAINT = "#94A3B8"    # tertiary text / captions
BORDER = "#E7EAF0"       # hairline borders
SURFACE = "#FFFFFF"      # card surface
CANVAS = "#F7F8FA"       # page background

ACCENT = "#4F46E5"       # indigo — primary accent
ACCENT_SOFT = "#EEF0FF"  # accent tint for fills/highlights
ACCENT_LINE = "#C7CBFA"

GOOD = "#16A34A"
GOOD_SOFT = "#ECFDF3"
WARN = "#D97706"
WARN_SOFT = "#FFFAEB"
RISK = "#DC2626"
RISK_SOFT = "#FEF2F2"

GRID = "#EEF1F6"

# Chart-facing aliases (kept so downstream chart code below is unaffected in
# structure — only the underlying hex values were tuned for the new palette).
BLUE = ACCENT
BLUE_2 = "#818CF8"
NAVY = INK
TEXT = INK
MUTED = INK_MUTED
LINE = BORDER
RED = RISK
YELLOW = WARN
GREEN = GOOD

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"] {
  font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  -webkit-font-smoothing: antialiased;
}
.stApp { background: #F7F8FA; color: #0F172A; }

/* Generous, centered reading column — consistent SaaS dashboard rhythm */
.block-container { max-width: 1200px; padding: 2rem 2rem 4rem; margin: 0 auto; }
header[data-testid="stHeader"] { background: transparent; }
footer, #MainMenu { visibility: hidden; }
[data-testid="stSidebar"] {
  background:#FFFFFF;
  border-right:1px solid #E7EAF0;
}
[data-testid="stSidebar"] > div:first-child { padding-top:1rem; }
[data-testid="stSidebar"] [data-testid="stFileUploader"] { margin-top:.35rem; }
[data-testid="stSidebar"] .stRadio > div { gap:.25rem; }
[data-testid="stSidebar"] .stRadio label {
  border-radius:10px; padding:.45rem .55rem; transition:background .15s ease;
}
[data-testid="stSidebar"] .stRadio label:hover { background:#F5F6FE; }


/* ---------- Header ---------- */
.app-header { display:flex; align-items:center; justify-content:space-between; gap:1rem; }
.app-badge {
  display:inline-flex; align-items:center; justify-content:center;
  width:40px; height:40px; border-radius:11px;
  background: linear-gradient(145deg, #4F46E5, #6366F1);
  color:#fff; font-size:1.05rem; font-weight:800; flex-shrink:0;
  box-shadow: 0 4px 10px rgba(79,70,229,.25);
}
.app-title-wrap { display:flex; align-items:center; gap:.75rem; }
.dashboard-title { font-size: 1.55rem; font-weight: 800; color: #0F172A; letter-spacing: -.02em; margin: 0; line-height:1.2; }
.dashboard-sub { font-size: .84rem; color: #64748B; margin-top: .18rem; font-weight: 500; }

.badge-pill {
  display:inline-flex; align-items:center; gap:.35rem;
  background:#EEF0FF; color:#4F46E5; border:1px solid #DDE0FB;
  border-radius:999px; padding:.3rem .7rem; font-size:.72rem; font-weight:700;
  letter-spacing:.01em; white-space:nowrap;
}

/* ---------- Cards ---------- */
[data-testid="stVerticalBlockBorderWrapper"] {
  background:#FFFFFF; border:1px solid #E7EAF0 !important; border-radius:16px !important;
  box-shadow:0 1px 2px rgba(15,23,42,.04); padding: 0 !important; margin: 0 0 1.25rem !important;
  transition: box-shadow .15s ease, border-color .15s ease;
}
[data-testid="stVerticalBlockBorderWrapper"]:hover {
  box-shadow: 0 4px 16px rgba(15,23,42,.06);
}
[data-testid="stVerticalBlockBorderWrapper"] > div { padding: 1.6rem 1.75rem !important; }

/* Balanced dashboard grid: paired question cards share the same row height.
   Content remains unchanged; compact inner spacing prevents large empty zones. */
/* Make the two cards in each dashboard row equal height without touching their data/content. */
[data-testid="stHorizontalBlock"]:has(.section-title) { align-items: stretch; }
[data-testid="stHorizontalBlock"]:has(.section-title) > [data-testid="column"] { display:flex; }
[data-testid="stHorizontalBlock"]:has(.section-title) > [data-testid="column"] > div { width:100%; display:flex; flex-direction:column; }
[data-testid="stHorizontalBlock"]:has(.section-title) [data-testid="stVerticalBlockBorderWrapper"] { flex:1; }
[data-testid="stHorizontalBlock"]:has(.section-title) [data-testid="stVerticalBlockBorderWrapper"] > div { height:100%; display:flex; flex-direction:column; }
[data-testid="stHorizontalBlock"]:has(.section-title) [data-testid="stPlotlyChart"] { margin-bottom:.1rem; }
[data-testid="stHorizontalBlock"]:has(.section-title) .stat-row { margin-top:.7rem; }
[data-testid="stHorizontalBlock"]:has(.section-title) .section-sub { margin-bottom:.7rem; }

.section-title { font-size: 1rem; font-weight: 700; color:#0F172A; margin:0 0 .2rem; letter-spacing:-.005em; line-height:1.35; }
.section-sub { font-size: .8rem; color:#8A93A6; margin-bottom: 1rem; line-height:1.5; }
.section-eyebrow {
  display:inline-block; font-size:.68rem; font-weight:800; letter-spacing:.06em; text-transform:uppercase;
  color:#4F46E5; margin-bottom:.35rem;
}

/* ---------- Stat chips (metric cards) ---------- */
.stat-row { display:flex; flex-wrap:wrap; gap:.65rem; margin: 1.1rem 0 .1rem; }
.stat-chip {
  background:#FAFBFD; border:1px solid #EDEFF4; border-radius:12px;
  padding:.75rem .9rem; min-width:118px; flex:1;
  position:relative; overflow:hidden;
}
.stat-chip::before {
  content:""; position:absolute; left:0; top:0; bottom:0; width:3px; background:#4F46E5; opacity:.55;
}
.stat-label { color:#8A93A6; font-size:.68rem; font-weight:700; text-transform:uppercase; letter-spacing:.04em; }
.stat-value { color:#0F172A; font-size:1.5rem; font-weight:800; line-height:1.25; margin-top:.18rem; letter-spacing:-.01em; }
.stat-foot { color:#A3AABB; font-size:.71rem; margin-top:.15rem; font-weight:500; }

/* ---------- Callouts ---------- */
.attn {
  display:flex; align-items:flex-start; gap:.55rem;
  background:#FFFAEB; border:1px solid #FBE8B4;
  border-radius:10px; padding:.65rem .85rem; margin: 1rem 0 0; color:#8A5A00; font-size:.79rem; line-height:1.5;
}
.attn .attn-icon { flex-shrink:0; font-size:.85rem; line-height:1.5; }
.attn.blue { background:#EEF0FF; border-color:#DDE0FB; color:#3730A3; }
.attn.red { background:#FEF2F2; border-color:#F9D0D0; color:#991B1B; }
.attn b { font-weight:750; }

/* ---------- Quality bar (Q3) ---------- */
.quality-bar { display:flex; width:100%; height:40px; border-radius:10px; overflow:hidden; margin-top:.4rem; border:1px solid #EDEFF4; }
.quality-seg { display:flex; align-items:center; justify-content:center; text-align:center; font-weight:750; font-size:.78rem; line-height:1.2; color:#fff; }
.quality-seg.yellow { color:#5C3A00; }
.legend-row { display:flex; flex-wrap:wrap; gap:1.2rem; margin-top:.6rem; font-size:.75rem; color:#8A93A6; font-weight:600; }
.legend-dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:.35rem; }

/* ---------- 2x2 matrix (Q5) ---------- */
.matrix {
  display:grid; grid-template-columns: 128px 1fr 1fr;
  border:1px solid #EDEFF4; border-radius:12px; overflow:hidden; background:#fff; margin-top:.7rem;
}
.matrix > div { padding:.85rem .6rem; border-right:1px solid #EDEFF4; border-bottom:1px solid #EDEFF4; text-align:center; font-size:.78rem; line-height:1.3; }
.matrix > div:nth-child(3n) { border-right:none; }
.matrix .head { background:#FAFBFD; font-weight:700; color:#5B6478; font-size:.72rem; text-transform:uppercase; letter-spacing:.03em; }
.matrix .rowhead { background:#FAFBFD; font-weight:700; color:#5B6478; display:flex; align-items:center; justify-content:center; font-size:.72rem; text-transform:uppercase; letter-spacing:.03em; }
.matrix .bluecell { background:#F5F6FE; }
.matrix .warmcell { background:#FFFAEB; }
.matrix .cellnum { font-size:1.3rem; font-weight:800; color:#4F46E5; }
.matrix .warmcell .cellnum { color:#A8710A; }
.matrix-note { text-align:right; color:#A3AABB; font-size:.71rem; margin-top:.4rem; font-weight:500; }

/* ---------- Inputs ---------- */
[data-testid="stFileUploader"] {
  background:#FAFBFD; border:1.5px dashed #C7CBFA; border-radius:14px; padding:.55rem .7rem;
  transition: border-color .15s ease, background .15s ease;
}
[data-testid="stFileUploader"]:hover { border-color:#4F46E5; background:#F5F6FE; }
[data-testid="stFileUploaderDropzone"] { background: transparent !important; }
[data-testid="stFileUploader"] section { background: transparent !important; border: none !important; }
[data-testid="stFileUploader"] small { color:#94A3B8 !important; }
[data-testid="stFileUploader"] button {
  background:#0F172A !important; color:#fff !important; border-radius:8px !important; border:none !important;
  font-weight:600 !important; font-size:.8rem !important;
}

[data-testid="stSelectbox"] label, [data-testid="stSlider"] label { color:#334155; font-weight:650; font-size:.78rem; text-transform:uppercase; letter-spacing:.03em; }
[data-baseweb="select"] > div {
  border-color:#E7EAF0 !important; border-radius:10px !important; min-height:42px; background:#fff !important;
  font-size:.87rem; box-shadow:none !important;
}
[data-baseweb="select"]:hover > div { border-color:#C7CBFA !important; }

[data-testid="stSlider"] { padding-top:.2rem; }
[data-testid="stSlider"] [role="slider"] { background-color:#4F46E5 !important; }
[data-testid="stTickBar"] { display:none; }

[data-testid="stPlotlyChart"] { background: transparent; }
[data-testid="stVerticalBlock"] { gap: .5rem; }
[data-testid="stHorizontalBlock"] { gap: 1.3rem; }

hr.divider { border:none; border-top:1px solid #E7EAF0; margin: 1.6rem 0 1.8rem; }

.section-group-label {
  font-size:.72rem; font-weight:800; text-transform:uppercase; letter-spacing:.06em;
  color:#A3AABB; margin: 1.6rem 0 .9rem; display:flex; align-items:center; gap:.6rem;
}
.section-group-label::after { content:""; flex:1; height:1px; background:#E7EAF0; }

/* Captions / info states */
[data-testid="stCaptionContainer"], .stCaption { color:#94A3B8 !important; }
[data-testid="stAlert"] { border-radius:12px; }




/* ---------- Responsive fixed header, aligned to Streamlit main content ---------- */

/* Make the keyed header fixed, but let its horizontal position follow
   Streamlit's actual main-content bounds rather than a hard-coded sidebar width. */
.st-key-sticky_header {
    position: fixed !important;
    top: 0 !important;
    z-index: 10000 !important;

    background: #F7F8FA !important;
    border: none !important;
    border-radius: 0 !important;
    box-shadow: none !important;

    margin: 0 !important;
    padding: 1rem 2rem .8rem !important;

    /* Default when sidebar is expanded */
    left: var(--fixed-header-left, 21rem) !important;
    right: 0 !important;
    width: auto !important;
    max-width: none !important;
}

/* Keep header content constrained like the original .block-container */
.st-key-sticky_header > div {
    max-width: 1200px !important;
    margin: 0 auto !important;
    width: 100% !important;
}

/* Original visual proportions */
.st-key-sticky_header .dashboard-title {
    font-size: 1.55rem !important;
    font-weight: 800 !important;
    line-height: 1.2 !important;
    margin: 0 !important;
}

.st-key-sticky_header .dashboard-sub {
    font-size: .84rem !important;
    margin-top: .18rem !important;
}

.st-key-sticky_header .app-badge {
    width: 40px !important;
    height: 40px !important;
    border-radius: 11px !important;
    font-size: 1.05rem !important;
}

/* Prevent filters from overflowing or disappearing */
.st-key-sticky_header [data-testid="stHorizontalBlock"] {
    width: 100% !important;
    align-items: center !important;
    flex-wrap: nowrap !important;
}

.st-key-sticky_header [data-testid="column"] {
    min-width: 0 !important;
}

.st-key-sticky_header [data-testid="stSelectbox"] {
    width: 100% !important;
    min-width: 0 !important;
}

.st-key-sticky_header [data-baseweb="select"] > div {
    min-height: 42px !important;
    height: 42px !important;
    width: 100% !important;
    min-width: 0 !important;
}

.st-key-sticky_header [data-testid="stSelectbox"] label {
    margin-bottom: .2rem !important;
}

/* Natural divider */
.st-key-sticky_header::after {
    content: "";
    position: absolute;
    left: 2rem;
    right: 2rem;
    bottom: 0;
    height: 1px;
    background: #E7EAF0;
}

/* Reserve space below fixed header */
.st-key-fixed_header_spacer {
    height: 6.7rem !important;
    min-height: 6.7rem !important;
}

/* Sidebar collapsed:
   Streamlit marks the sidebar aria-expanded=false.
   Use :has() on stApp to move the header fully left. */
.stApp:has([data-testid="stSidebar"][aria-expanded="false"]) .st-key-sticky_header {
    left: 0 !important;
}

/* Also handle the case where the sidebar is completely absent from layout */
.stApp:not(:has([data-testid="stSidebar"])) .st-key-sticky_header {
    left: 0 !important;
}

/* Medium/narrow desktop: reduce horizontal padding and rebalance columns */
@media (max-width: 1350px) {
    .st-key-sticky_header {
        padding-left: 1.25rem !important;
        padding-right: 1.25rem !important;
    }

    .st-key-sticky_header::after {
        left: 1.25rem;
        right: 1.25rem;
    }
}

/* Tablet/small viewport: keep all filters visible */
@media (max-width: 1050px) {
    .st-key-sticky_header {
        left: 0 !important;
        padding: .8rem 1rem .7rem !important;
    }

    .st-key-sticky_header .dashboard-title {
        font-size: 1.2rem !important;
    }

    .st-key-sticky_header .dashboard-sub {
        font-size: .72rem !important;
    }

    .st-key-sticky_header .app-badge {
        width: 34px !important;
        height: 34px !important;
        font-size: .8rem !important;
    }

    .st-key-sticky_header::after {
        left: 1rem;
        right: 1rem;
    }

    .st-key-fixed_header_spacer {
        height: 6.2rem !important;
        min-height: 6.2rem !important;
    }
}

/* Very narrow screens: allow the row to wrap instead of hiding Class */
@media (max-width: 760px) {
    .st-key-sticky_header [data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
        gap: .45rem !important;
    }

    .st-key-sticky_header [data-testid="column"] {
        flex: 1 1 100% !important;
        width: 100% !important;
    }

    .st-key-fixed_header_spacer {
        height: 12rem !important;
        min-height: 12rem !important;
    }
}

</style>
""",
    unsafe_allow_html=True,
)


def stat_chip(label, value, foot=""):
    return f"""<div class="stat-chip"><div class="stat-label">{label}</div><div class="stat-value">{value}</div><div class="stat-foot">{foot}</div></div>"""


def stat_row(*chips):
    return '<div class="stat-row">' + ''.join(chips) + '</div>'


def attention(text, tone=""):
    cls = f"attn {tone}".strip()
    icon = "▲" if tone == "red" else ("●" if tone == "blue" else "◆")
    return f'<div class="{cls}"><span class="attn-icon">{icon}</span><span>{text}</span></div>'


def section_heading(title, subtitle=""):
    st.markdown(
        f'<div class="section-title">{title}</div>' + (f'<div class="section-sub">{subtitle}</div>' if subtitle else ''),
        unsafe_allow_html=True,
    )


def clean_plot(fig, height=240, showlegend=False):
    fig.update_layout(
        height=height, margin=dict(l=8, r=14, t=22, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=TEXT, size=11, family="Inter, sans-serif"),
        hoverlabel=dict(bgcolor="#0F172A", font_color="#FFFFFF", font_size=11, bordercolor="#0F172A"),
        showlegend=showlegend,
        legend=dict(font=dict(size=10, color=MUTED), orientation="h", y=-0.18, x=0),
        bargap=0.35,
    )
    fig.update_xaxes(showgrid=False, linecolor=LINE, tickfont=dict(color=MUTED, size=10), title_font=dict(color=MUTED, size=10.5))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, tickfont=dict(color=MUTED, size=10), title_font=dict(color=MUTED, size=10.5))
    return fig


def histogram_counts(series):
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if vals.empty:
        return pd.DataFrame(columns=["value", "count"])
    vals = vals.round().astype(int)
    out = vals.value_counts().sort_index().rename_axis("value").reset_index(name="count")
    return out


# -----------------------------------------------------------------------------
# Navigation + controls
# -----------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        """
        <div style="padding:.2rem .15rem .7rem;">
          <div style="display:flex;align-items:center;gap:.7rem;">
            <div class="app-badge" style="width:36px;height:36px;border-radius:10px;font-size:.78rem;letter-spacing:.02em;">CA</div>
            <div>
              <div style="font-weight:800;font-size:.94rem;color:#0F172A;line-height:1.2;">Assessment Analytics</div>
              <div style="font-size:.70rem;color:#94A3B8;margin-top:.12rem;">Conversational assessment</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    page = st.radio(
        "Navigation",
        ["Dashboard", "Processed data & validation"],
        index=0,
        label_visibility="collapsed",
    )

    # Raw weekly uploads are a local-processing feature only.
    # The deployed/live dashboard is read-only and loads the committed
    # processed_cache/latest_processed_analysis.xlsx workbook.
    if IS_LIVE:
        uploads = None
    else:
        st.markdown('<div style="height:.7rem"></div>', unsafe_allow_html=True)
        with st.expander("Data source", expanded=False):
            uploads = st.file_uploader(
                "Upload weekly Excel file(s)",
                type=["xlsx"],
                accept_multiple_files=True,
            )

@st.cache_data(show_spinner=False)
def cached_process(file_payloads):
    prepared = []
    for name, payload in file_payloads:
        bio = io.BytesIO(payload)
        bio.name = name
        prepared.append(bio)
    return process_uploaded_files(prepared)


def merge_processed_table(old_df, new_df, key_candidates):
    """Append new processed rows to saved rows and remove duplicates safely."""
    if old_df is None or old_df.empty:
        return new_df.copy().reset_index(drop=True)
    if new_df is None or new_df.empty:
        return old_df.copy().reset_index(drop=True)

    combined = pd.concat([old_df, new_df], ignore_index=True, sort=False)

    # Prefer stable identifiers when they are available. Keeping the newest row
    # means re-uploading a week can refresh an existing processed record.
    for keys in key_candidates:
        if all(key in combined.columns for key in keys):
            return (
                combined
                .drop_duplicates(subset=keys, keep="last")
                .reset_index(drop=True)
            )

    # Safe fallback when a table schema changes.
    return combined.drop_duplicates(keep="last").reset_index(drop=True)


# Prefer a previously persisted processed workbook when no new uploads are provided.
if not uploads and SAVED_WORKBOOK.exists():
    with st.spinner("Loading saved processed data..."):
        (
            raw_data,
            turns,
            sessions,
            questions,
            request_events,
            validation,
        ) = load_saved_processed_workbook(SAVED_WORKBOOK)

elif uploads and not IS_LIVE:
    payloads = tuple((f.name, f.getvalue()) for f in uploads)

    with st.spinner("Processing new conversations..."):
        # Process ONLY the newly uploaded file(s).
        (
            new_raw,
            new_turns,
            new_sessions,
            new_questions,
            new_request_events,
        ) = cached_process(payloads)

        # If cumulative processed data already exists, keep it and append only
        # the newly processed rows instead of replacing previous weeks.
        if SAVED_WORKBOOK.exists():
            (
                old_raw,
                old_turns,
                old_sessions,
                old_questions,
                old_request_events,
                _old_validation,
            ) = load_saved_processed_workbook(SAVED_WORKBOOK)

            raw_data = merge_processed_table(
                old_raw,
                new_raw,
                [
                    ["week", "Message Id"],
                    ["week", "message_id"],
                    ["week", "conversation_id", "timestamp", "role", "message"],
                ],
            )

            turns = merge_processed_table(
                old_turns,
                new_turns,
                [
                    ["week", "conversation_id", "turn_index"],
                    ["week", "conversation_id", "student_message_id"],
                    ["week", "conversation_id", "timestamp", "student_answer"],
                ],
            )

            sessions = merge_processed_table(
                old_sessions,
                new_sessions,
                [
                    ["week", "conversation_id"],
                ],
            )

            questions = merge_processed_table(
                old_questions,
                new_questions,
                [
                    ["week", "question_id"],
                    ["week", "question_text"],
                    ["week", "assistant_question"],
                ],
            )

            request_events = merge_processed_table(
                old_request_events,
                new_request_events,
                [
                    ["week", "conversation_id", "request_number"],
                    ["week", "conversation_id", "request_text"],
                ],
            )
        else:
            # First ever processing run: the new data becomes the cumulative data.
            raw_data = new_raw
            turns = new_turns
            sessions = new_sessions
            questions = new_questions
            request_events = new_request_events

        # Validation must describe the final cumulative dataset, not only the
        # newly uploaded week.
        validation = build_validation_report(
            raw_data, turns, sessions, request_events
        )

        # Persist one cumulative workbook containing all previously processed
        # weeks plus the new upload(s).
        workbook = build_standardized_workbook(
            raw_data, turns, sessions, questions, request_events, validation
        )
        workbook_bytes = (
            workbook.getvalue() if hasattr(workbook, "getvalue") else workbook
        )

        save_cumulative_workbook(workbook_bytes)
        refresh_fast_frontend()

else:
    st.markdown(
        """
        <div style="max-width:760px;margin:9vh auto 0;text-align:center;padding:2rem;">
          <div class="app-badge" style="margin:0 auto 1rem;width:52px;height:52px;border-radius:15px;font-size:.9rem;">CA</div>
          <div style="font-size:1.7rem;font-weight:800;letter-spacing:-.03em;color:#0F172A;">Conversational Assessment</div>
          <div style="margin:.55rem auto 0;max-width:560px;color:#64748B;font-size:.92rem;line-height:1.6;">
            No saved processed workbook was found.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

if sessions.empty:
    st.error("No valid sessions were reconstructed from the uploaded file(s).")
    st.stop()

weeks = sorted(sessions["week"].dropna().unique().tolist())
has_class_col = "class" in sessions.columns

# Header: title on the left, filters on the right.
# The keyed container is made sticky by CSS so it remains visible while scrolling.
with st.container(key="sticky_header"):
    head_title, head_week, head_class = st.columns(
        [3.8, 1.15, 1.35],
        gap="medium",
        vertical_alignment="center",
    )

    with head_week:
        week_options = ["All weeks"] + weeks
        week = st.selectbox(
            "Week",
            week_options,
            index=0,
            format_func=lambda x: x if x == "All weeks" else f"Week {x}",
        )

    with head_class:
        if has_class_col:
            classes = ["All classes"] + sorted(sessions["class"].dropna().unique().tolist())
            selected_class = st.selectbox("Class", classes, index=0)
        else:
            selected_class = "All classes"
            st.selectbox("Class", ["All classes"], index=0, disabled=True)

    with head_title:
        week_label = "All weeks" if week == "All weeks" else f"Week {week}"
        class_label = "All classes" if selected_class == "All classes" else str(selected_class)
        st.markdown(
            f"""
            <div class="app-header" style="margin-bottom:.05rem;">
              <div class="app-title-wrap">
                <div class="app-badge">CA</div>
                <div>
                  <div class="dashboard-title">Conversational Assessment Analytics</div>
                  <div class="dashboard-sub">{week_label} · {class_label} · evidence for the five instructor questions</div>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

with st.container(key="fixed_header_spacer"):
    st.empty()

st.markdown('<hr class="divider">', unsafe_allow_html=True)

if week == "All weeks":
    sf = sessions.copy()
    tf = turns.copy()
    rf = request_events.copy()
else:
    sf = sessions[sessions["week"] == week].copy()
    tf = turns[turns["week"] == week].copy()
    rf = request_events[request_events["week"] == week].copy() if len(request_events) else request_events.copy()

if has_class_col and selected_class != "All classes":
    sf = sf[sf["class"] == selected_class].copy()

# Restrict turn/event rows to sessions in selected week (and class, if filtered).
valid_convs = set(sf["conversation_id"])

if week != "All weeks" and "week" in tf.columns:
    tf = tf[
        (tf["week"] == week) &
        (tf["conversation_id"].isin(valid_convs))
    ].copy()
else:
    tf = tf[tf["conversation_id"].isin(valid_convs)].copy()

if len(rf):
    if week != "All weeks" and "week" in rf.columns:
        rf = rf[
            (rf["week"] == week) &
            (rf["conversation_id"].isin(valid_convs))
        ].copy()
    else:
        rf = rf[rf["conversation_id"].isin(valid_convs)].copy()

# -----------------------------------------------------------------------------
# Shared metrics
# -----------------------------------------------------------------------------
students_total = int(sf["student_id"].nunique()) if len(sf) else 0
req_sessions = sf[sf["completion_code_requested"] == True].copy()
asked_students = int(req_sessions["student_id"].nunique()) if len(req_sessions) else 0
first_q = pd.to_numeric(req_sessions.get("first_code_request_after_questions"), errors="coerce").dropna()
median_first = first_q.median() if len(first_q) else math.nan
early_n = int((first_q <= 2).sum()) if len(first_q) else 0
early_pct = (early_n / len(first_q) * 100) if len(first_q) else 0

answer_rows = tf[tf["is_answer_attempt"] == True].copy()

# Q2 = 2a only, per the instructor's simplification: non-serious answers per student,
# not a separate "answers before request" metric (that's treated as covered by Q1).
if "seriousness" in answer_rows.columns and len(answer_rows):
    nonserious_by_session = (
        answer_rows[answer_rows["seriousness"] == "Non-serious"]
        .groupby("conversation_id").size()
        .reindex(sf["conversation_id"], fill_value=0)
    )
else:
    nonserious_by_session = pd.Series(0, index=sf["conversation_id"])

nonserious_n = int(nonserious_by_session.sum())
students_with_nonserious = int((nonserious_by_session > 0).sum())
median_nonserious = nonserious_by_session.median() if len(nonserious_by_session) else math.nan
mean_nonserious = nonserious_by_session.mean() if len(nonserious_by_session) else math.nan
sf["nonserious_count"] = nonserious_by_session.values  # aligned by construction (reindexed on sf's own conversation_id order)

serious_counts = answer_rows["seriousness"].value_counts() if "seriousness" in answer_rows.columns else pd.Series(dtype=int)
serious_n = int(serious_counts.get("Serious", 0))
pending_n = int(serious_counts.get("Pending LLM", 0)) + int(serious_counts.get("Unclear", 0))
serious_total = serious_n + nonserious_n + pending_n

result_counts = answer_rows["answer_result"].value_counts() if "answer_result" in answer_rows.columns else pd.Series(dtype=int)
correct_n = int(result_counts.get("Correct", 0))
partial_n = int(result_counts.get("Partially correct", 0))
incorrect_n = int(result_counts.get("Incorrect", 0))
unclear_n = int(result_counts.get("Unclear", 0)) + int(result_counts.get("Pending LLM", 0))
classified_total = correct_n + partial_n + incorrect_n

# 3a: follow-up questions needed to recover from an incorrect answer
recovery = pd.to_numeric(
    answer_rows.loc[
        (answer_rows["answer_result"] == "Incorrect") & answer_rows["followups_to_recovery"].notna(),
        "followups_to_recovery",
    ],
    errors="coerce",
).dropna() if "followups_to_recovery" in answer_rows.columns else pd.Series(dtype=float)

avg_followups = recovery.mean() if len(recovery) else math.nan

# Q4: Coding vs Conceptual questions answered incorrectly (categories come straight from the data)
if len(answer_rows) and "question_type" in answer_rows.columns:
    q4 = answer_rows[answer_rows["answer_result"] == "Incorrect"].copy()
    q4["question_type"] = q4["question_type"].fillna("Unclassified").astype(str)
    q4_counts = q4["question_type"].value_counts()
else:
    q4_counts = pd.Series(dtype=int)


def iqr_outliers(series):
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if len(vals) < 4:
        return pd.Series(dtype=float), math.nan, math.nan
    q1, q3 = vals.quantile([.25, .75])
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return vals[(vals < lo) | (vals > hi)], lo, hi


if page == "Dashboard":
    # -----------------------------------------------------------------------------
    # Layout — exactly the plot options selected by Abhinava:
    # Malgorzata feedback: Q1 A + rich hover · Q2 A + B + word cloud · Q3 A & B · Q4 A+B combined · Q5 A
    # -----------------------------------------------------------------------------

    row1 = st.columns(2, gap="large")

    # Q1 — Option A: Distribution of first request + informative hover context
    with row1[0]:
        with st.container(border=True):
            section_heading(
                "1. How many questions has the chatbot asked when the student asks for the code?",
                "Distribution of when students first ask for the completion code. Hover over a bar for class and student context.",
            )

            hist = histogram_counts(first_q)
            if len(hist):
                _q1_source = req_sessions.copy()
                _q1_source["_first_request_q"] = pd.to_numeric(
                    _q1_source["first_code_request_after_questions"], errors="coerce"
                ).round()

                _q1_hover = []
                for _q in hist["value"].tolist():
                    _rows = _q1_source[_q1_source["_first_request_q"] == _q].copy()
                    _parts = [
                        f"<b>{int(_q)} questions before first request</b>",
                        f"Students: {int(_rows['student_id'].nunique()) if 'student_id' in _rows.columns else len(_rows)}",
                        f"Sessions: {len(_rows)}",
                    ]

                    if "class" in _rows.columns and len(_rows):
                        _class_counts = (
                            _rows["class"].fillna("Unknown class").astype(str).value_counts()
                        )
                        _class_text = "<br>".join(
                            f"• {name}: {int(count)}"
                            for name, count in _class_counts.items()
                        )
                        _parts.append("<b>Class breakdown</b><br>" + _class_text)

                    if "student_id" in _rows.columns and len(_rows):
                        _ids = _rows["student_id"].dropna().astype(str).drop_duplicates().tolist()
                        _shown = ", ".join(_ids[:8])
                        if len(_ids) > 8:
                            _shown += f", +{len(_ids)-8} more"
                        if _shown:
                            _parts.append("<b>Student IDs</b><br>" + _shown)

                    _q1_hover.append("<br>".join(_parts))

                fig = go.Figure(go.Bar(
                    x=hist["value"].astype(str),
                    y=hist["count"],
                    marker_color=BLUE,
                    marker_line_width=0,
                    text=hist["count"],
                    textposition="outside",
                    textfont=dict(size=10, color=INK_MUTED),
                    customdata=_q1_hover,
                    hovertemplate="%{customdata}<extra></extra>",
                ))
                # Light red background bands make IQR outliers visible without changing the bars.
                _q1_outliers, _q1_lo, _q1_hi = iqr_outliers(first_q)
                _q1_outlier_values = set(
                    pd.to_numeric(_q1_outliers, errors="coerce").dropna().round().astype(int).tolist()
                )
                _q1_categories = hist["value"].astype(int).tolist()
                for _idx, _value in enumerate(_q1_categories):
                    if _value in _q1_outlier_values:
                        fig.add_vrect(
                            x0=_idx - 0.48,
                            x1=_idx + 0.48,
                            fillcolor="rgba(220,38,38,0.10)",
                            line_width=0,
                            layer="below",
                        )

                if not pd.isna(median_first):
                    fig.add_vline(
                        x=median_first,
                        line_dash="dash",
                        line_color=NAVY,
                        line_width=1.4,
                        annotation_text=f"median {median_first:.0f}",
                        annotation_position="top",
                        annotation_font_size=10,
                        annotation_font_color=NAVY,
                    )
                fig.update_xaxes(title="Questions before request")
                fig.update_yaxes(title="Students")
                st.plotly_chart(clean_plot(fig, 245), use_container_width=True, config={"displayModeBar": False})
            else:
                st.caption("No code requests detected.")

            st.markdown(stat_row(
                stat_chip("Asked", asked_students, f"of {students_total} students"),
                stat_chip("Median", "—" if pd.isna(median_first) else f"{median_first:.0f}", "questions · supplementary"),
                stat_chip("Early", early_n, f"≤2 · {early_pct:.0f}%"),
            ), unsafe_allow_html=True)

            if early_n:
                st.markdown(
                    attention(f"<b>{early_n} students</b> requested the code within the first two questions.", "red"),
                    unsafe_allow_html=True,
                )


    # Q2 — Options A + B + simple word cloud, with answer examples on hover
    with row1[1]:
        with st.container(border=True):
            section_heading(
                "2. How many non-serious answers has the student made?",
                "Distribution per student, ranked cases, and a compact word cloud. Hover over ranked points to inspect examples of the non-serious answers.",
            )

            _ns_rows = answer_rows[
                answer_rows["seriousness"].astype(str).str.lower().eq("non-serious")
            ].copy() if "seriousness" in answer_rows.columns else pd.DataFrame()

            # Count per student, including students with zero non-serious answers.
            _all_students = sf["student_id"].dropna().astype(str).drop_duplicates().tolist()
            if len(_ns_rows):
                _ns_rows["_student_key"] = _ns_rows["student_id"].astype(str)
                _ns_counts_student = _ns_rows.groupby("_student_key").size()
            else:
                _ns_counts_student = pd.Series(dtype=int)

            _student_counts = pd.Series(
                {_sid: int(_ns_counts_student.get(_sid, 0)) for _sid in _all_students},
                dtype=int,
            )

            # A. Distribution per student
            _dist = histogram_counts(_student_counts)
            sub_a, sub_b = st.columns(2, gap="medium")

            with sub_a:
                st.markdown(
                    '<div class="section-eyebrow">A · Distribution per student</div>',
                    unsafe_allow_html=True,
                )
                if len(_dist):
                    _dist_hover = []
                    for _count in _dist["value"].tolist():
                        _members = _student_counts[_student_counts == _count].index.tolist()
                        _shown = ", ".join(_members[:7])
                        if len(_members) > 7:
                            _shown += f", +{len(_members)-7} more"
                        _dist_hover.append(
                            f"<b>{int(_count)} non-serious answer(s)</b><br>"
                            f"Students: {len(_members)}"
                            + (f"<br>Student IDs: {_shown}" if _shown else "")
                        )

                    _fig_a = go.Figure(go.Bar(
                        x=_dist["value"].astype(str),
                        y=_dist["count"],
                        marker_color=YELLOW,
                        marker_line_width=0,
                        text=_dist["count"],
                        textposition="outside",
                        textfont=dict(size=10, color=INK_MUTED),
                        customdata=_dist_hover,
                        hovertemplate="%{customdata}<extra></extra>",
                    ))
                    _fig_a.update_xaxes(title="Non-serious answers")
                    _fig_a.update_yaxes(title="Students")
                    st.plotly_chart(
                        clean_plot(_fig_a, 190),
                        use_container_width=True,
                        config={"displayModeBar": False},
                    )
                else:
                    st.caption("No student-level data available.")

            # B. Ranked students with >=1 non-serious answer.
            with sub_b:
                st.markdown(
                    '<div class="section-eyebrow">B · Ranked non-serious counts</div>',
                    unsafe_allow_html=True,
                )
                _ranked = (
                    _student_counts[_student_counts > 0]
                    .sort_values(ascending=False)
                    .rename("nonserious_count")
                    .reset_index()
                    .rename(columns={"index": "student_id"})
                )

                if len(_ranked):
                    _ranked["rank"] = range(1, len(_ranked) + 1)
                    _hover_texts = []

                    for _, _r in _ranked.iterrows():
                        _sid = str(_r["student_id"])
                        _examples = []
                        _class_info = []

                        if len(_ns_rows):
                            _sr = _ns_rows[_ns_rows["_student_key"] == _sid].copy()
                            if "student_answer" in _sr.columns:
                                for _ans in _sr["student_answer"].dropna().astype(str).tolist():
                                    _ans = " ".join(_ans.split())
                                    if _ans and _ans not in _examples:
                                        _examples.append(_ans[:160] + ("…" if len(_ans) > 160 else ""))
                                    if len(_examples) >= 3:
                                        break

                        if "class" in sf.columns:
                            _student_classes = (
                                sf[sf["student_id"].astype(str) == _sid]["class"]
                                .dropna().astype(str).drop_duplicates().tolist()
                            )
                            _class_info = _student_classes

                        _parts = [
                            f"<b>Student {_sid}</b>",
                            f"Non-serious answers: {int(_r['nonserious_count'])}",
                        ]
                        if _class_info:
                            _parts.append("Class: " + ", ".join(_class_info))
                        if _examples:
                            _parts.append(
                                "<b>Examples</b><br>" +
                                "<br>".join(f"• {x}" for x in _examples)
                            )
                        _hover_texts.append("<br>".join(_parts))

                    _fig_b = go.Figure(go.Scatter(
                        x=_ranked["rank"],
                        y=_ranked["nonserious_count"],
                        mode="markers",
                        marker=dict(
                            size=9,
                            color=RED,
                            opacity=.78,
                            line=dict(width=1, color="#FFFFFF"),
                        ),
                        customdata=_hover_texts,
                        hovertemplate="%{customdata}<extra></extra>",
                    ))
                    _fig_b.update_xaxes(
                        title="Students with ≥1 non-serious answer",
                        showticklabels=False,
                    )
                    _fig_b.update_yaxes(title="Non-serious answers", dtick=1)
                    st.plotly_chart(
                        clean_plot(_fig_b, 190),
                        use_container_width=True,
                        config={"displayModeBar": False},
                    )
                else:
                    st.caption("No non-serious answers detected.")

            # Simple word cloud from the actual non-serious answers — no extra package dependency.
            st.markdown(
                '<div class="section-eyebrow">Overview · words used in non-serious answers</div>',
                unsafe_allow_html=True,
            )

            _stop_words = {
                "the","a","an","and","or","but","if","then","than","to","of","in","on","for","with","at","by",
                "from","is","are","was","were","be","been","being","it","this","that","these","those","i","you",
                "he","she","we","they","my","your","our","their","me","him","her","them","as","so","do","does",
                "did","can","could","would","should","will","just","yes","no","not","dont","don't","idk","ok",
                "okay","yeah","yep","nah","what","why","how","when","where","who","which","have","has","had",
            }

            _word_counts = {}
            if len(_ns_rows) and "student_answer" in _ns_rows.columns:
                _all_ns_text = " ".join(_ns_rows["student_answer"].dropna().astype(str).tolist()).lower()
                for _word in re.findall(r"[a-zA-Z][a-zA-Z']{1,}", _all_ns_text):
                    _word = _word.strip("'")
                    if _word and _word not in _stop_words and len(_word) > 2:
                        _word_counts[_word] = _word_counts.get(_word, 0) + 1

            _top_words = sorted(_word_counts.items(), key=lambda x: (-x[1], x[0]))[:35]

            if _top_words:
                _max_freq = max(v for _, v in _top_words)
                _cloud_parts = []
                for _idx, (_word, _freq) in enumerate(_top_words):
                    _size = 12 + int(18 * (_freq / _max_freq))
                    _weight = 600 if _freq < _max_freq * .6 else 800
                    _cloud_parts.append(
                        f'<span title="{_freq} occurrence(s)" '
                        f'style="font-size:{_size}px;font-weight:{_weight};'
                        f'color:{ACCENT if _idx % 3 else INK_MUTED};'
                        f'margin:.22rem .38rem;display:inline-block;line-height:1.15;">'
                        f'{_word}</span>'
                    )
                st.markdown(
                    '<div style="background:#FAFBFD;border:1px solid #EDEFF4;border-radius:12px;'
                    'padding:.65rem .85rem;min-height:72px;text-align:center;">'
                    + "".join(_cloud_parts) +
                    '</div>',
                    unsafe_allow_html=True,
                )
                st.caption("Word size reflects frequency across answers classified as non-serious. Hover a word to see its frequency.")
            else:
                st.caption("No usable words available for the word cloud.")

            st.markdown(stat_row(
                stat_chip("Students", len(_student_counts), "in current filter"),
                stat_chip("With non-serious", int((_student_counts > 0).sum()), "students"),
                stat_chip("Non-serious answers", int(_student_counts.sum()), "total"),
            ), unsafe_allow_html=True)


    row2 = st.columns(2, gap="large")

    # Q3 — Options A & B: Answer-result distribution + follow-up questions per session
    with row2[0]:
        with st.container(border=True):
            section_heading(
                "3. How many answers are incorrect or partially correct, and how many follow-up questions were asked?",
                "Answer-result distribution and follow-up-question count per session.",
            )

            q3_result_labels = ["Incorrect", "Partially correct", "Correct"]
            q3_result_values = [incorrect_n, partial_n, correct_n]
            q3_result_colors = [BLUE, BLUE, BLUE]

            if classified_total:
                q3_text = [f"{100*v/classified_total:.0f}% (n = {v})" for v in q3_result_values]
                fig_a = go.Figure(go.Bar(
                    x=q3_result_labels,
                    y=q3_result_values,
                    marker_color=q3_result_colors,
                    marker_line_width=0,
                    text=q3_text,
                    textposition="outside",
                    textfont=dict(size=10, color=INK_MUTED),
                    hovertemplate="%{x}: %{y} attempts<extra></extra>",
                ))
                fig_a.update_xaxes(title=None)
                fig_a.update_yaxes(visible=False, showgrid=False, zeroline=False)
                st.plotly_chart(clean_plot(fig_a, 190), use_container_width=True, config={"displayModeBar": False})
            else:
                st.caption("No classified answer attempts available.")

            followup_flag_col = next(
                (c for c in ["is_followup", "is_follow_up", "followup_question", "is_followup_question"] if c in tf.columns),
                None,
            )

            if followup_flag_col:
                followups_per_session = (
                    tf.assign(_followup=tf[followup_flag_col].fillna(False).astype(bool))
                    .groupby("conversation_id")["_followup"].sum()
                    .reindex(sf["conversation_id"], fill_value=0)
                )
            elif "followups_to_recovery" in answer_rows.columns:
                followups_per_session = (
                    pd.to_numeric(answer_rows["followups_to_recovery"], errors="coerce")
                    .fillna(0)
                    .groupby(answer_rows["conversation_id"])
                    .sum()
                    .reindex(sf["conversation_id"], fill_value=0)
                )
            else:
                followups_per_session = pd.Series(0, index=sf["conversation_id"])

            followup_hist = histogram_counts(followups_per_session)
            if len(followup_hist):
                fig_b = go.Figure(go.Bar(
                    x=followup_hist["value"].astype(str),
                    y=followup_hist["count"],
                    marker_color=BLUE,
                    marker_line_width=0,
                    text=followup_hist["count"],
                    textposition="outside",
                    textfont=dict(size=10, color=INK_MUTED),
                    hovertemplate="%{x} follow-up question(s): %{y} sessions<extra></extra>",
                ))
                fig_b.update_xaxes(title="Follow-up questions")
                fig_b.update_yaxes(title="Sessions")
                st.plotly_chart(clean_plot(fig_b, 175), use_container_width=True, config={"displayModeBar": False})

            avg_session_followups = followups_per_session.mean() if len(followups_per_session) else math.nan
            st.markdown(stat_row(
                stat_chip("Incorrect", incorrect_n, f"{100*incorrect_n/max(classified_total,1):.0f}%"),
                stat_chip("Partial", partial_n, f"{100*partial_n/max(classified_total,1):.0f}%"),
                stat_chip("Avg follow-ups", "—" if pd.isna(avg_session_followups) else f"{avg_session_followups:.1f}", "per session · supplementary"),
            ), unsafe_allow_html=True)

    # Q4 — Option B: Incorrect rate within type
    with row2[1]:
        with st.container(border=True):
            section_heading(
                "4. Are the incorrectly answered questions coding or conceptual?",
                "Combined count and percentage: each label reports the incorrect rate and raw number of incorrect answers.",
            )

            q4_base = answer_rows[answer_rows["answer_result"].isin(["Correct", "Partially correct", "Incorrect"])].copy()
            # The processor now emits exactly two content categories required by Q4.
            # Question format (e.g. multiple choice) is intentionally not mixed into this field.
            if len(q4_base) and "question_type" in q4_base.columns:
                q4_base = q4_base[q4_base["question_type"].isin(["Coding", "Conceptual"])].copy()
                q4_base["type_group"] = q4_base["question_type"]
            else:
                q4_base = pd.DataFrame()

            if len(q4_base):
                q4_summary = (
                    q4_base.groupby("type_group")
                    .agg(
                        attempts=("answer_result", "size"),
                        incorrect=("answer_result", lambda s: int((s == "Incorrect").sum())),
                    )
                    .reset_index()
                )
                q4_summary["incorrect_rate"] = 100 * q4_summary["incorrect"] / q4_summary["attempts"].replace(0, pd.NA)
                q4_summary["type_group"] = pd.Categorical(q4_summary["type_group"], ["Coding", "Conceptual"], ordered=True)
                q4_summary = q4_summary.sort_values("type_group")

                fig = go.Figure(go.Bar(
                    x=q4_summary["type_group"].astype(str),
                    y=q4_summary["incorrect_rate"],
                    marker_color=BLUE,
                    marker_line_width=0,
                    text=[f"{rate:.0f}% (n = {int(n)})" for rate, n in zip(q4_summary["incorrect_rate"], q4_summary["incorrect"])],
                    textposition="outside",
                    textfont=dict(size=10, color=INK_MUTED),
                    customdata=q4_summary[["incorrect", "attempts"]],
                    hovertemplate="<b>%{x}</b><br>Incorrect rate: %{y:.1f}%<br>Incorrect answers: %{customdata[0]}<br>Classified attempts: %{customdata[1]}<extra></extra>",
                ))
                fig.update_xaxes(title=None)
                fig.update_yaxes(title="Incorrect rate (%)", ticksuffix="%", rangemode="tozero")
                st.plotly_chart(clean_plot(fig, 245), use_container_width=True, config={"displayModeBar": False})

                chips = [
                    stat_chip(
                        str(r["type_group"]),
                        f"{r['incorrect_rate']:.0f}%",
                        f"{int(r['incorrect'])} incorrect / {int(r['attempts'])} attempts",
                    )
                    for _, r in q4_summary.iterrows()
                ]
                if chips:
                    st.markdown(stat_row(*chips), unsafe_allow_html=True)

                worst = q4_summary.loc[q4_summary["incorrect_rate"].idxmax()]
                st.markdown(
                    attention(
                        f"<b>{worst['type_group']}</b> has the higher incorrect rate ({worst['incorrect_rate']:.0f}%).",
                        "blue",
                    ),
                    unsafe_allow_html=True,
                )
            else:
                st.caption("No classified coding/conceptual attempts available.")

    st.markdown('<div class="section-group-label">Enrollment completion</div>', unsafe_allow_html=True)

    # Q5 — Option A: 2×2 matrix for students who did not get the completion code
    with st.container(border=True):
        section_heading(
            "5. Among students who did not get the completion code: did they ask for it, and did they do enough work (N answers)?",
            "2×2 matrix: asked vs did not ask × enough vs not enough work. The instructor sets N.",
        )
        N = st.slider("Minimum answers to count as “enough work” (N)", 1, 10, 5, 1)

        q5_source = sf.copy()
        q5_source["work_count_for_threshold"] = pd.to_numeric(
            q5_source["work_count_for_threshold"], errors="coerce"
        ).fillna(0)

        student_matrix = (
            q5_source.groupby("student_id", as_index=False)
            .agg(
                completion_code_received=("completion_code_received", "max"),
                completion_code_requested=("completion_code_requested", "max"),
                work_count_for_threshold=("work_count_for_threshold", "max"),
            )
        )

        matrix = student_matrix[student_matrix["completion_code_received"] != True].copy()
        matrix["enough_work"] = matrix["work_count_for_threshold"] >= N

        ea = int((matrix["enough_work"] & matrix["completion_code_requested"]).sum())
        na = int((~matrix["enough_work"] & matrix["completion_code_requested"]).sum())
        en = int((matrix["enough_work"] & ~matrix["completion_code_requested"]).sum())
        nn = int((~matrix["enough_work"] & ~matrix["completion_code_requested"]).sum())
        total_students = max(len(matrix), 1)

        def pct(v):
            return 100 * v / total_students

        matrix_html = f"""<div class="matrix">
        <div></div>
        <div class="head">Enough work<br>≥ {N}</div>
        <div class="head">Not enough<br>&lt; {N}</div>
        <div class="rowhead">Asked code</div>
        <div class="bluecell"><span class="cellnum">{ea}</span><br>{pct(ea):.0f}%</div>
        <div class="warmcell"><span class="cellnum">{na}</span><br>{pct(na):.0f}%</div>
        <div class="rowhead">Did not ask</div>
        <div class="bluecell"><span class="cellnum">{en}</span><br>{pct(en):.0f}%</div>
        <div class="warmcell"><span class="cellnum">{nn}</span><br>{pct(nn):.0f}%</div>
        </div>
        <div class="matrix-note">Total: {len(matrix)} students who did not receive the code</div>"""
        st.markdown(matrix_html, unsafe_allow_html=True)

        st.markdown(stat_row(
            stat_chip("Asked + enough", ea, f"{pct(ea):.0f}% of no-code students"),
            stat_chip("Asked + not enough", na, f"{pct(na):.0f}%"),
            stat_chip("Did not ask + enough", en, f"{pct(en):.0f}%"),
            stat_chip("Did not ask + not enough", nn, f"{pct(nn):.0f}%"),
        ), unsafe_allow_html=True)

        if na:
            st.markdown(
                attention(f"<b>{na} no-code student(s)</b> asked for the code without reaching N={N} answers.", "red"),
                unsafe_allow_html=True,
            )

if page == "Processed data & validation":
    # -----------------------------------------------------------------------------
    # Processed data inspector — review exactly what the pipeline produced
    # -----------------------------------------------------------------------------
    st.markdown('<div class="section-group-label">Processed data & validation</div>', unsafe_allow_html=True)

    with st.container(border=True):
        section_heading(
            "Processed data inspector",
            "Review the final tables after deterministic processing, class assignment, LLM enrichment and completion-code validation, then download the complete workbook.",
        )

        workbook = build_standardized_workbook(
            raw_data, turns, sessions, questions, request_events, validation
        )
        workbook_bytes = workbook.getvalue() if hasattr(workbook, "getvalue") else workbook

        # Local mode may refresh the persisted cumulative workbook.
        # Live mode is strictly read-only.
        if not IS_LIVE:
            save_cumulative_workbook(workbook_bytes)
            refresh_fast_frontend()

        llm_turns = int(turns["llm_model"].notna().sum()) if "llm_model" in turns.columns else 0
        audited_completion = (
            int(sessions["completion_code_received_llm"].notna().sum())
            if "completion_code_received_llm" in sessions.columns else 0
        )

        repaired_completion = 0
        completion_flags = 0
        if "completion_validation_status" in sessions.columns:
            _audit_status = sessions["completion_validation_status"].fillna("").astype(str)
            repaired_completion = int((_audit_status == "llm_recovered_deterministic_miss").sum())
            completion_flags = int(
                _audit_status.str.contains(
                    "conflict|unclear|low_confidence|no_literal|llm_error",
                    case=False,
                    regex=True,
                ).sum()
            )

        pending_semantic = (
            int(pd.to_numeric(sessions["semantic_fields_pending"], errors="coerce").fillna(0).sum())
            if "semantic_fields_pending" in sessions.columns else 0
        )

        st.markdown(stat_row(
            stat_chip("LLM-enriched turns", llm_turns, "semantic classifications"),
            stat_chip("Completion audited", audited_completion, "requesting sessions"),
            stat_chip("Recovered misses", repaired_completion, "deterministic misses repaired"),
            stat_chip("Audit flags", completion_flags, "conflict / unclear"),
            stat_chip("Pending semantics", pending_semantic, "still unresolved"),
        ), unsafe_allow_html=True)

        st.download_button(
            "Download complete processed Excel",
            workbook_bytes,
            file_name="standardized_dashboard_dataset.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="content",
        )

        st.markdown(
            attention(
                "The preview below reads the <b>same workbook bytes</b> used by the download button, so you can verify exactly what will be exported.",
                "blue",
            ),
            unsafe_allow_html=True,
        )

        try:
            _excel_file = pd.ExcelFile(io.BytesIO(workbook_bytes))
            _sheet_names = _excel_file.sheet_names

            _sheet_sel = st.selectbox(
                "Inspect workbook sheet",
                _sheet_names,
                index=0,
                help="Select any sheet from the final generated workbook.",
            )

            _sheet_df = pd.read_excel(
                io.BytesIO(workbook_bytes),
                sheet_name=_sheet_sel,
            )

            st.markdown(stat_row(
                stat_chip("Rows", len(_sheet_df), _sheet_sel),
                stat_chip("Columns", len(_sheet_df.columns), "exported fields"),
                stat_chip("Sheets", len(_sheet_names), "in final workbook"),
            ), unsafe_allow_html=True)

            st.dataframe(
                _sheet_df,
                width="stretch",
                hide_index=True,
                height=520,
            )

            st.caption("Workbook sheets: " + " · ".join(_sheet_names))

        except Exception as exc:
            st.warning(f"The workbook was created, but the in-app sheet preview could not be opened: {exc}")

    with st.expander("Inspect LLM completion-code validation", expanded=False):
        _completion_cols = [
            "week", "student_id", "conversation_id",
            "completion_code_requested",
            "completion_code_received_deterministic", "completion_code_deterministic",
            "completion_code_received_llm", "completion_code_llm",
            "completion_code_received", "completion_code",
            "completion_validation_confidence",
            "completion_validation_status",
            "completion_evidence_source",
            "completion_validation_evidence",
            "completion_validation_model",
            "review_required",
        ]
        _completion_cols = [c for c in _completion_cols if c in sessions.columns]
        if _completion_cols:
            _completion_view = sessions.copy()
            if "completion_code_requested" in _completion_view.columns:
                _completion_view = _completion_view[
                    _completion_view["completion_code_requested"] == True
                ]
            st.dataframe(
                _completion_view[_completion_cols],
                width="stretch",
                hide_index=True,
                height=500,
            )
        else:
            st.info("Completion-validation fields are not available in the current processed result.")

    with st.expander("Inspect LLM turn classifications", expanded=False):
        _turn_cols = [
            "week", "student_id", "conversation_id", "timestamp",
            "assistant_question", "student_answer", "assistant_feedback",
            "seriousness", "seriousness_source", "seriousness_confidence",
            "answer_result", "answer_result_source", "answer_result_confidence",
            "question_type", "question_type_source", "question_type_confidence",
            "is_followup_question", "followup_source", "followup_confidence",
            "llm_reason", "llm_model", "llm_prompt_version", "llm_error", "review_required",
        ]
        _turn_cols = [c for c in _turn_cols if c in turns.columns]
        st.dataframe(
            turns[_turn_cols],
            width="stretch",
            hide_index=True,
            height=500,
        )

    with st.expander("Inspect request-event validation", expanded=False):
        _request_cols = [
            "week", "student_id", "conversation_id", "request_number",
            "request_text", "assistant_response_to_request",
            "observed_request_outcome", "completion_code_after_request",
            "completion_code_received_deterministic", "completion_code_deterministic",
            "completion_code_received_llm", "completion_code_llm",
            "completion_validation_confidence",
            "completion_validation_status",
            "completion_evidence_source",
            "llm_model", "llm_error", "review_required",
        ]
        _request_cols = [c for c in _request_cols if c in request_events.columns]
        if len(request_events) and _request_cols:
            st.dataframe(
                request_events[_request_cols],
                width="stretch",
                hide_index=True,
                height=500,
            )
        else:
            st.info("No request-event rows are available for the uploaded data.")

    with st.expander("Inspect validation report", expanded=False):
        st.dataframe(
            validation,
            width="stretch",
            hide_index=True,
            height=420,
        )
