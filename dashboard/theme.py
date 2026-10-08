"""RTL styling for the Streamlit dashboard.

Streamlit renders LTR by default. Arabic needs more than `direction: rtl` on
the body: widget labels, dataframe cells, tabs, metrics and the sidebar each
need explicit alignment, and Plotly legends/axes are separate again.
"""

from __future__ import annotations

import streamlit as st

PRIMARY = "#0b5d4a"
ACCENT = "#118a6e"

SENTIMENT_COLORS = {
    "Positive": "#1b8a5a",
    "Negative": "#c0392b",
    "Neutral": "#95a5a6",
}

# Plotly needs an Arabic-capable font; these ship with Windows and macOS.
ARABIC_FONT = "Segoe UI, Tahoma, Geneva, Verdana, Arial, sans-serif"

_RTL_CSS = f"""
<style>
/* Load an Arabic webfont so rendering is consistent across machines. */
@import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&display=swap');

html, body, [class*="css"] {{
    font-family: 'Cairo', {ARABIC_FONT};
}}

/* Flip the main app and sidebar. */
.stApp, section.main, [data-testid="stSidebar"] {{
    direction: rtl;
    text-align: right;
}}

/* Headings and body copy. */
h1, h2, h3, h4, h5, h6, p, span, label, li {{
    direction: rtl;
    text-align: right;
}}

/* Widget labels and help text. */
[data-testid="stWidgetLabel"] p,
.stSelectbox label, .stMultiSelect label, .stTextInput label,
.stRadio label, .stSlider label, .stCheckbox label {{
    direction: rtl;
    text-align: right;
    width: 100%;
}}

/* Metric cards. */
[data-testid="stMetric"] {{
    direction: rtl;
    text-align: right;
    background: #f7faf9;
    border: 1px solid #e3ebe9;
    border-radius: 10px;
    padding: 12px 16px;
}}
[data-testid="stMetricLabel"], [data-testid="stMetricValue"],
[data-testid="stMetricDelta"] {{
    direction: rtl;
    text-align: right;
    justify-content: flex-start;
}}

/* Dataframes: right-align cells and headers, and put the index on the right. */
[data-testid="stDataFrame"] {{ direction: rtl; }}
[data-testid="stDataFrame"] [role="columnheader"],
[data-testid="stDataFrame"] [role="gridcell"] {{
    text-align: right !important;
    direction: rtl;
}}

/* Tabs run right-to-left. */
[data-baseweb="tab-list"] {{
    flex-direction: row-reverse;
    justify-content: flex-end;
}}

/* Expander headers. */
[data-testid="stExpander"] summary {{
    direction: rtl;
    text-align: right;
}}

/* Alerts / info boxes keep their icon on the right. */
[data-testid="stAlert"] {{
    direction: rtl;
    text-align: right;
}}

/* Multiselect chips. */
[data-baseweb="tag"] {{ direction: rtl; }}

/* Header band. */
.jsdo-header {{
    background: linear-gradient(135deg, {PRIMARY}, {ACCENT});
    color: #fff;
    padding: 20px 26px;
    border-radius: 12px;
    margin-bottom: 18px;
    direction: rtl;
    text-align: right;
}}
.jsdo-header h1 {{
    color: #fff;
    margin: 0;
    font-size: 26px;
    font-weight: 700;
}}
.jsdo-header p {{
    color: rgba(255,255,255,.88);
    margin: 6px 0 0;
    font-size: 14px;
}}

/* Comment review cards. */
.comment-card {{
    border-right: 4px solid #c0392b;
    background: #fdf6f5;
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 10px;
    direction: rtl;
    text-align: right;
}}
.comment-card .meta {{
    font-size: 12px;
    color: #7f8c8d;
    margin-bottom: 4px;
}}
.comment-card .body {{
    font-size: 15px;
    color: #2c3e50;
    line-height: 1.6;
}}
.comment-card .reason {{
    font-size: 13px;
    color: #c0392b;
    margin-top: 6px;
    font-weight: 600;
}}

/* Hide the Streamlit chrome for a cleaner report. */
#MainMenu, footer {{ visibility: hidden; }}
</style>
"""


def apply_rtl() -> None:
    st.markdown(_RTL_CSS, unsafe_allow_html=True)


def rtl_layout(fig, height: int | None = None):
    """Apply RTL-aware Plotly layout: legend and axes flipped, Arabic font."""
    fig.update_layout(
        font=dict(family=f"Cairo, {ARABIC_FONT}", size=13),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            traceorder="reversed",
        ),
        margin=dict(l=20, r=20, t=50, b=40),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hoverlabel=dict(font_family=f"Cairo, {ARABIC_FONT}", align="right"),
    )
    # Category labels read right-to-left; the y axis sits on the right.
    fig.update_yaxes(side="right", gridcolor="#eceff1")
    fig.update_xaxes(gridcolor="#eceff1")
    if height:
        fig.update_layout(height=height)
    return fig


def header(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="jsdo-header"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )
