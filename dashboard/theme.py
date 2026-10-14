"""RTL styling for the Streamlit dashboard.

Streamlit renders LTR by default. Arabic needs more than `direction: rtl` on
the body: widget labels, dataframe cells, tabs, metrics and the sidebar each
need explicit alignment, and Plotly legends/axes are separate again.
"""

from __future__ import annotations

import streamlit as st

PRIMARY = "#0b5d4a"
ACCENT = "#118a6e"

# Text colours chosen for contrast on white: INK clears WCAG AA at body size,
# MUTED is only used at 14px and above where it still clears AA.
INK = "#16241f"
MUTED = "#5a6b66"
LINE = "#d7e2de"
SURFACE = "#ffffff"
CANVAS = "#f4f7f6"

SENTIMENT_COLORS = {
    "Positive": "#157f52",
    "Negative": "#b3271d",
    "Neutral": "#6b7c86",
}

# One dark, distinct color per benchmark. Streamlit's default chart palette
# includes pale swatches that vanish on this light canvas, and two regions can
# land on almost the same totals, so each series also gets its own dash.
BENCHMARK_COLORS = [
    "#355c7d",
    "#c06c4c",
    "#1f6f78",
    "#7a3e8a",
    "#a88412",
    "#a33b4a",
    "#3e5c8a",
    "#6b4f2a",
    "#3d6b4f",
]
LINE_DASHES = [
    "solid",
    "dash",
    "dot",
    "longdash",
    "dashdot",
    "longdashdot",
    "8px,4px",
    "2px,6px",
    "12px,3px,2px,3px",
]
MARKER_SYMBOLS = [
    "circle-open",
    "diamond-open",
    "x",
    "square-open",
    "cross",
    "triangle-up-open",
    "star-open",
    "hexagon-open",
    "pentagon-open",
]

# IBM Plex Sans Arabic covers Arabic and Latin in one family, so titles,
# numbers, and dates match. Segoe UI is the offline fallback.
UI_FONT = "IBM Plex Sans Arabic"
ARABIC_FONT = f"'{UI_FONT}', 'Segoe UI', Tahoma, sans-serif"

_RTL_CSS = f"""
<style>
/* Load an Arabic webfont so rendering is consistent across machines. */
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {{
    font-family: {ARABIC_FONT};
    color: {INK};
}}

/* A quiet canvas so the white cards are what the eye lands on. */
.stApp {{
    background: {CANVAS};
}}
.block-container {{
    padding-top: 1.4rem;
    padding-bottom: 3rem;
    max-width: 1240px;
}}

/* Flip the main app and sidebar. */
.stApp, section.main, [data-testid="stSidebar"] {{
    direction: rtl;
    text-align: right;
}}

h1, h2, h3, h4, h5, h6, p, span, label, li {{
    direction: rtl;
    text-align: right;
}}

/* Section titles: one clear size, with room to breathe above them. */
h4 {{
    font-size: 1.2rem !important;
    font-weight: 700 !important;
    color: {INK} !important;
    margin: 1.1rem 0 0.15rem !important;
}}
h5 {{
    font-size: 1.02rem !important;
    font-weight: 700 !important;
    color: {INK} !important;
}}

/* Supporting captions stay secondary, but large enough to read. */
[data-testid="stCaptionContainer"] p {{
    font-size: 0.92rem !important;
    color: {MUTED} !important;
    line-height: 1.6;
}}

/* Widget labels and help text. */
[data-testid="stWidgetLabel"] p,
.stSelectbox label, .stMultiSelect label, .stTextInput label,
.stRadio label, .stSlider label, .stCheckbox label {{
    direction: rtl;
    text-align: right;
    width: 100%;
    font-weight: 600;
    color: {INK};
}}

/* Metric cards: the number is the point, so it dominates the label. */
[data-testid="stMetric"] {{
    direction: rtl;
    text-align: right;
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 14px;
    padding: 16px 18px 12px;
    box-shadow: 0 1px 2px rgba(22, 36, 31, 0.04);
}}
[data-testid="stMetricLabel"], [data-testid="stMetricValue"],
[data-testid="stMetricDelta"] {{
    direction: rtl;
    text-align: right;
    justify-content: flex-start;
}}
[data-testid="stMetricLabel"] p {{
    font-size: 0.92rem !important;
    font-weight: 600;
    color: {MUTED} !important;
}}
[data-testid="stMetricValue"] {{
    font-size: 1.85rem !important;
    font-weight: 700;
    color: {INK};
    line-height: 1.15;
}}

/* Dataframes: larger type, right-aligned, and a calmer header. */
[data-testid="stDataFrame"] {{ direction: rtl; }}
[data-testid="stDataFrame"] [role="columnheader"] {{
    text-align: right !important;
    direction: rtl;
    background: {CANVAS} !important;
    font-weight: 700 !important;
    font-size: 0.92rem !important;
}}
[data-testid="stDataFrame"] [role="gridcell"] {{
    text-align: right !important;
    direction: rtl;
    font-size: 0.95rem !important;
    line-height: 1.55;
}}

/* Tabs: bigger hit targets, and the active one is unmistakable. */
[data-baseweb="tab-list"] {{
    flex-direction: row-reverse;
    justify-content: flex-end;
    gap: 4px;
    border-bottom: 1px solid {LINE};
}}
[data-baseweb="tab"] {{
    font-size: 1rem !important;
    font-weight: 600;
    padding: 10px 16px !important;
    color: {MUTED};
}}
[data-baseweb="tab"][aria-selected="true"] {{
    color: {PRIMARY} !important;
}}

/* Expander headers. */
[data-testid="stExpander"] summary {{
    direction: rtl;
    text-align: right;
    font-weight: 600;
}}

/* Alerts keep their icon on the right and stay readable. */
[data-testid="stAlert"] {{
    direction: rtl;
    text-align: right;
    font-size: 0.98rem;
}}

/* Sidebar: a clean white panel, with filter chips that don't overflow. */
[data-testid="stSidebar"] {{
    background: {SURFACE};
    border-left: 1px solid {LINE};
}}
[data-testid="stSidebar"] h3 {{
    font-size: 1.05rem !important;
    margin-bottom: 0.2rem;
}}
[data-baseweb="tag"] {{
    direction: rtl;
    font-size: 0.85rem;
    max-width: 100%;
}}

/* Header: white card, green mark, update kept in its own chip. */
.jsdo-header-wrap {{
    container-type: inline-size;
    margin-bottom: 16px;
}}
.jsdo-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    background: {SURFACE};
    border: 1px solid {LINE};
    border-radius: 18px;
    padding: 18px 20px;
    direction: rtl;
    text-align: right;
    box-shadow: 0 1px 2px rgba(22, 36, 31, 0.04);
}}
.jsdo-header-main {{
    display: flex;
    align-items: center;
    gap: 16px;
    flex: 1;
    min-width: 0;
}}
.jsdo-copy {{
    min-width: 0;
}}
.jsdo-mark {{
    width: 54px;
    height: 54px;
    border-radius: 16px;
    background: linear-gradient(160deg, {PRIMARY}, {ACCENT});
    display: grid;
    place-items: center;
    flex: none;
}}
.jsdo-kicker {{
    margin: 0 0 2px;
    font-size: 0.95rem !important;
    font-weight: 700;
    color: {PRIMARY} !important;
    line-height: 1.45;
}}
.jsdo-header h1 {{
    color: {INK} !important;
    margin: 0;
    font-size: 1.55rem !important;
    font-weight: 700;
    letter-spacing: 0;
    line-height: 1.35;
}}
.jsdo-meta {{
    margin: 4px 0 0;
    color: {MUTED} !important;
    font-size: 0.95rem !important;
    line-height: 1.4;
}}
.jsdo-updated {{
    flex: none;
    background: {CANVAS};
    border: 1px solid {LINE};
    border-radius: 14px;
    padding: 12px 16px;
    text-align: center;
    min-width: 168px;
}}
.jsdo-updated span {{
    display: block;
    font-size: 0.85rem !important;
    font-weight: 700;
    color: {PRIMARY} !important;
    line-height: 1.3;
}}
.jsdo-updated strong {{
    display: block;
    margin-top: 4px;
    font-size: 1rem !important;
    color: {INK} !important;
    font-weight: 700;
    direction: ltr;
    line-height: 1.3;
}}
@container (max-width: 680px) {{
    .jsdo-header {{
        flex-direction: column;
        align-items: stretch;
        gap: 12px;
    }}
    .jsdo-updated {{
        min-width: 0;
    }}
}}

/* Account and platform pickers: large enough to tap and read. */
[data-testid="stPills"] button,
[data-testid="stSegmentedControl"] button {{
    font-size: 1rem !important;
    font-weight: 600 !important;
    min-height: 2.5rem;
    padding: 0.35rem 0.95rem !important;
    border-radius: 999px !important;
}}
[data-testid="stPills"] {{
    gap: 8px;
}}

/* Comment cards: the text is the content, so it gets the room. */
.comment-card {{
    background: #fff;
    border: 1px solid {LINE};
    border-right: 5px solid {MUTED};
    border-radius: 14px;
    padding: 16px 20px 14px;
    margin-bottom: 12px;
    direction: rtl;
    text-align: right;
}}
.comment-card.is-positive {{ border-right-color: #157f52; }}
.comment-card.is-negative {{ border-right-color: #b3271d; background: #fff8f7; }}
.comment-card.is-neutral {{ border-right-color: #6b7c86; }}
.comment-card .meta {{
    font-size: 0.92rem;
    color: {MUTED};
    margin-bottom: 8px;
    line-height: 1.6;
}}
.comment-card .badge {{
    display: inline-block;
    font-weight: 700;
    color: {INK};
    background: {CANVAS};
    border-radius: 999px;
    padding: 1px 10px;
    margin-left: 8px;
}}
.comment-card .body {{
    font-size: 1.15rem;
    color: {INK};
    line-height: 1.85;
}}
.comment-card .body a,
.comment-card .reason a {{
    color: inherit;
    text-decoration: none;
}}
.comment-card .body a:hover,
.comment-card .reason a:hover {{
    text-decoration: underline;
}}
.comment-card .reason {{
    font-size: 0.98rem;
    color: {PRIMARY};
    margin-top: 10px;
    font-weight: 700;
    line-height: 1.5;
}}

/* Hide the Streamlit chrome so the report is the whole page. */
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {{
    visibility: hidden;
    height: 0;
}}

/* Streamlit slides a closed sidebar off-screen with translateX. In RTL that
   slide crosses the page and leaves a vertical strip of labels. On a phone
   the panel is removed; the refresh control lives on the page instead. */
.stSidebar,
[data-testid="stSidebar"] {{
    overflow: hidden !important;
}}
.st-key-phone_refresh {{
    display: none !important;
}}
.modebar-container, .modebar {{
    display: none !important;
}}

/* Phone: the open sidebar covers the page instead of squeezing a sliver of
   it beside a 300px panel, and the metric row becomes two cards across. */
@media (max-width: 820px) {{
    .block-container {{
        padding-top: 1.25rem !important;
        padding-left: 0.75rem !important;
        padding-right: 0.75rem !important;
    }}
    .stSidebar,
    [data-testid="stSidebar"] {{
        display: none !important;
        transform: none !important;
        width: 0 !important;
        min-width: 0 !important;
        max-width: 0 !important;
        height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        border: 0 !important;
        overflow: hidden !important;
        visibility: hidden !important;
        position: absolute !important;
        pointer-events: none !important;
    }}
    .st-key-phone_refresh {{
        display: block !important;
        margin: 0 0 12px;
    }}
    [data-testid="stHorizontalBlock"] {{
        flex-wrap: wrap !important;
        gap: 0.65rem !important;
    }}
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
        flex: 1 1 100% !important;
        width: 100% !important;
        min-width: 0 !important;
        max-width: 100% !important;
    }}
    [data-testid="stPills"] {{
        max-width: 100%;
    }}
    [data-testid="stPills"] [data-testid="stMarkdownContainer"],
    [data-testid="stPills"] div {{
        flex-wrap: wrap !important;
        max-width: 100%;
    }}
    .jsdo-header {{
        flex-direction: column;
        align-items: stretch;
        padding: 14px;
        border-radius: 14px;
        gap: 12px;
    }}
    .jsdo-header-main {{
        align-items: flex-start;
    }}
    .jsdo-kicker, .jsdo-header h1, .jsdo-meta {{
        overflow-wrap: anywhere;
    }}
    .jsdo-header h1 {{
        font-size: 1.15rem !important;
    }}
    .jsdo-mark {{
        width: 44px;
        height: 44px;
    }}
    .jsdo-updated {{
        min-width: 0;
    }}
    [data-testid="stMetric"] {{
        padding: 12px;
    }}
    [data-testid="stMetricValue"] {{
        font-size: 1.4rem !important;
    }}
    [data-baseweb="tab-list"] {{
        overflow-x: auto;
        flex-wrap: nowrap;
    }}
    [data-baseweb="tab"] {{
        font-size: 0.86rem !important;
        padding: 8px 10px !important;
        white-space: nowrap;
    }}
    .comment-card {{
        padding: 12px 14px;
    }}
    .comment-card .body {{
        font-size: 1.02rem;
    }}
    [data-testid="stPlotlyChart"] {{
        overflow: hidden;
    }}
    .stApp {{
        padding-bottom: 4.5rem;
    }}
}}
</style>
"""


# No zoom, pan, box, or lasso. A legend tap is the only way to change the chart.
_CHART_CONFIG = {
    "displayModeBar": False,
    "displaylogo": False,
    "scrollZoom": False,
    "doubleClick": False,
    "showTips": False,
}


def show_chart(fig, height: int | None = None) -> None:
    st.plotly_chart(
        rtl_layout(fig, height),
        width="stretch",
        config=_CHART_CONFIG,
        selection_mode=(),
    )


def apply_rtl() -> None:
    st.markdown(_RTL_CSS, unsafe_allow_html=True)


def rtl_layout(fig, height: int | None = None):
    """Apply RTL-aware Plotly layout: legend and axes flipped, Arabic font."""
    fig.update_layout(
        font=dict(family=ARABIC_FONT, size=14, color=INK),
        dragmode=False,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            traceorder="reversed",
            itemclick="toggle",
            itemdoubleclick=False,
            font=dict(size=13),
        ),
        margin=dict(l=8, r=8, t=56, b=24),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hoverlabel=dict(font_family=ARABIC_FONT, align="right"),
    )
    # Category labels read right-to-left; the y axis sits on the right.
    fig.update_yaxes(side="right", gridcolor="#eceff1", fixedrange=True)
    fig.update_xaxes(gridcolor="#eceff1", fixedrange=True)
    if height:
        fig.update_layout(height=height)
    return fig


def header(
    title: str,
    office: str,
    detail: str = "",
    updated: str | None = None,
) -> None:
    """Page header: office, title, and the last refresh in a separate chip."""
    import html

    mark = (
        '<div class="jsdo-mark" aria-hidden="true">'
        '<svg width="26" height="26" viewBox="0 0 26 26" fill="none">'
        '<rect x="3" y="14" width="4.5" height="9" rx="1.2" fill="white" opacity="0.65"/>'
        '<rect x="10.75" y="8" width="4.5" height="15" rx="1.2" fill="white" opacity="0.85"/>'
        '<rect x="18.5" y="3" width="4.5" height="20" rx="1.2" fill="white"/>'
        "</svg></div>"
    )
    detail_html = (
        f'<p class="jsdo-meta">{html.escape(detail)}</p>' if detail else ""
    )
    updated_html = (
        '<div class="jsdo-updated"><span>آخر تحديث</span>'
        f"<strong>{html.escape(updated)}</strong></div>"
        if updated
        else ""
    )
    st.markdown(
        '<div class="jsdo-header-wrap"><div class="jsdo-header">'
        '<div class="jsdo-header-main">'
        f'{mark}<div class="jsdo-copy">'
        f'<p class="jsdo-kicker">{html.escape(office)}</p>'
        f"<h1>{html.escape(title)}</h1>"
        f"{detail_html}</div></div>{updated_html}</div></div>",
        unsafe_allow_html=True,
    )
