"""Interactive weekly social media dashboard (Arabic / RTL).

Run with:
    python -m aljouf_report dashboard
    # or: streamlit run dashboard/app.py
"""

from __future__ import annotations

import html
import sys
import traceback
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

# Allow `streamlit run dashboard/app.py` without installing the package, and
# from any working directory.
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
# The repository source has to win over any older installed copy. Streamlit
# Cloud keeps the environment between deploys, and an older package was being
# imported ahead of this checkout.
for extra_path in (HERE, PROJECT_ROOT / "src"):
    resolved = str(extra_path.resolve())
    sys.path[:] = [item for item in sys.path if str(Path(item).resolve()) != resolved]
    sys.path.insert(0, resolved)

_boot_error = None
try:
    from aljouf_report.config import load_config  # noqa: E402
    from aljouf_report.models import (  # noqa: E402
        DEMO_MODEL_TAG,
        OWNED_ONLY_PLATFORMS,
        PLATFORM_AR,
        SENTIMENT_AR,
    )
    from aljouf_report.notify.mailer import embed_images, insight_table  # noqa: E402
    from aljouf_report.storage import Database  # noqa: E402
except Exception:
    _boot_error = traceback.format_exc()
    load_config = DEMO_MODEL_TAG = OWNED_ONLY_PLATFORMS = None
    PLATFORM_AR = SENTIMENT_AR = embed_images = insight_table = Database = None

from theme import (  # noqa: E402
    ACCENT,
    BENCHMARK_COLORS,
    LINE_DASHES,
    MARKER_SYMBOLS,
    PRIMARY,
    SENTIMENT_COLORS,
    apply_rtl,
    header,
    show_chart,
)

st.set_page_config(
    page_title="لوحة وسائل التواصل | المكتب الاستراتيجي لتطوير منطقة الجوف",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)
apply_rtl()
if _boot_error:
    st.error("تعذر فتح لوحة المعلومات.")
    st.code(_boot_error)
    st.stop()


def auto_refresh(minutes: int) -> None:
    """Reload the whole page on a timer so a wall-mounted screen stays current.

    The data cache expires well inside this interval, so the reload picks up
    whatever the last pipeline run wrote. Done with a timer in an iframe rather
    than a blocking loop so the page stays interactive, and it targets
    `window.parent` because Streamlit renders components in an iframe.
    """
    if minutes <= 0:
        return
    components.html(
        f"""
        <script>
          setTimeout(function () {{
            window.parent.location.reload();
          }}, {minutes * 60 * 1000});
        </script>
        """,
        height=0,
    )


# --------------------------------------------------------------------------- #
# Data access
# --------------------------------------------------------------------------- #
@st.cache_resource
def get_config():
    return load_config()


@st.cache_data(ttl=300)
def load_frames(db_path: str) -> dict[str, pd.DataFrame]:
    """Read everything the dashboard needs in one pass (cached 5 minutes)."""
    db = Database(db_path)
    config = get_config()
    windows = config.settings.windows

    followers = pd.DataFrame(db.follower_history(limit_runs=52))
    news = pd.DataFrame(db.recent_news(windows.news_lookback_days))
    comments = pd.DataFrame(db.scored_comments(windows.comments_lookback_days))

    if not followers.empty:
        followers["run_started_at"] = pd.to_datetime(
            followers["run_started_at"], format="ISO8601", utc=True
        )
        followers["owned"] = followers["owned"].astype(bool)
        followers["المنصة"] = followers["platform"].map(
            lambda p: PLATFORM_AR.get(p, p)
        )
        # Short labels: ten entities sharing a long name prefix make legends,
        # filters and axis ticks unreadable.
        short_names = {e.key: e.short_name for e in config.accounts.entities}
        followers["الجهة"] = followers.apply(
            lambda row: short_names.get(row["entity_key"], row["entity_name_ar"]),
            axis=1,
        )
        followers["النوع"] = followers["owned"].map(
            {True: "حساباتنا", False: "الجهات المرجعية"}
        )

    if not news.empty:
        news["published_at"] = pd.to_datetime(
            news["published_at"], format="ISO8601", utc=True, errors="coerce"
        )

    if not comments.empty:
        comments["published_at"] = pd.to_datetime(
            comments["published_at"], format="ISO8601", utc=True, errors="coerce"
        )
        comments["المنصة"] = comments["platform"].map(lambda p: PLATFORM_AR.get(p, p))
        comments["التصنيف"] = comments["label"].map(
            lambda lab: SENTIMENT_AR.get(lab, "غير محلل") if lab else "غير محلل"
        )

    return {"followers": followers, "news": news, "comments": comments}


config = get_config()
db_path = str(config.db_path)

if not config.db_path.exists():
    header(
        "لوحة وسائل التواصل الاجتماعي",
        "المكتب الاستراتيجي لتطوير منطقة الجوف",
        "لا توجد بيانات بعد",
    )
    st.warning(
        "قاعدة البيانات غير موجودة. شغّل خط المعالجة أولاً:\n\n"
        "```\npython -m aljouf_report run\n```\n\n"
        "أو املأ بيانات تجريبية للعرض:\n\n"
        "```\npython -m aljouf_report seed-demo\n```"
    )
    st.stop()

frames = load_frames(db_path)
followers_df = frames["followers"]
news_df = frames["news"]
comments_df = frames["comments"]
# A competitor removed on the control page leaves old rows in the database.
# Charts follow the current account list, so a removed office disappears now.
known_keys = {entity.key for entity in config.accounts.entities}
if not followers_df.empty and "entity_key" in followers_df.columns:
    followers_df = followers_df[followers_df["entity_key"].isin(known_keys)].copy()


# --------------------------------------------------------------------------- #
# Sidebar filters
# --------------------------------------------------------------------------- #
page = st.segmented_control(
    "الصفحة",
    options=["التقرير", "التحكم"],
    default="التقرير",
    key="page",
    label_visibility="collapsed",
)
if page == "التحكم":
    from control_page import render_control

    render_control()
    st.stop()

with st.sidebar:
    st.markdown("### ⚙️ العرض")
    if st.button("🔄 تحديث البيانات", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    st.caption(f"قاعدة البيانات: `{config.db_path.name}`")
    st.caption(f"نافذة الأخبار: {config.settings.windows.news_lookback_days} يوماً")
    st.caption(
        f"جمع التعليقات: آخر {config.settings.windows.comments_scrape_days} أيام · "
        f"العرض: {config.settings.windows.comments_lookback_days} يوماً"
    )
    refresh_minutes = config.settings.dashboard.auto_refresh_minutes
    if refresh_minutes > 0:
        st.caption(f"تحديث تلقائي: كل {refresh_minutes} دقيقة")

auto_refresh(config.settings.dashboard.auto_refresh_minutes)


def render_comments(frame: pd.DataFrame, height: int = 760) -> None:
    """Full-text comment cards. A table clips Arabic mid-sentence."""
    if frame.empty:
        st.info("لا توجد تعليقات مطابقة.")
        return
    ordered = frame.sort_values("published_at", ascending=False, na_position="last")
    with st.container(height=height):
        for _, row in ordered.iterrows():
            published = (
                row["published_at"].strftime("%Y-%m-%d %H:%M")
                if pd.notna(row.get("published_at"))
                else "تاريخ غير معروف"
            )
            kind = {
                "Positive": "is-positive",
                "Negative": "is-negative",
                "Neutral": "is-neutral",
            }.get(str(row.get("label") or ""), "")
            reason = str(row.get("reason_ar") or "").strip()
            reason_html = (
                f'<div class="reason">{html.escape(reason)}</div>' if reason else ""
            )
            st.markdown(
                f"""<div class="comment-card {kind}">
                      <div class="meta">
                        <span class="badge">{html.escape(str(row.get('التصنيف') or ''))}</span>
                        {html.escape(str(row.get('المنصة') or ''))} ·
                        {html.escape(str(row.get('author') or 'مستخدم'))} ·
                        {published}
                      </div>
                      <div class="body">{html.escape(str(row.get('text') or ''))}</div>
                      {reason_html}
                    </div>""",
                unsafe_allow_html=True,
            )


def render_news(frame: pd.DataFrame, height: int = 780) -> None:
    """Article cards in the same layout as the comment list."""
    if frame.empty:
        st.info("لا توجد أخبار مطابقة.")
        return
    languages = {"ar": "العربية", "en": "English"}
    ordered = frame.sort_values("published_at", ascending=False, na_position="last")
    with st.container(height=height):
        for _, row in ordered.iterrows():
            published = (
                row["published_at"].strftime("%Y-%m-%d %H:%M")
                if pd.notna(row.get("published_at"))
                else "تاريخ غير معروف"
            )
            keyword = str(row.get("keyword") or "").split(",")[0].strip()
            language = languages.get(str(row.get("language") or ""), str(row.get("language") or ""))
            source = str(row.get("source") or row.get("domain") or "مصدر غير معروف")
            url = str(row.get("url") or "").strip()
            title = html.escape(str(row.get("title") or ""))
            title_html = f'<a href="{html.escape(url)}" target="_blank">{title}</a>' if url else title
            link_html = (
                f'<div class="reason"><a href="{html.escape(url)}" target="_blank">فتح المقال</a></div>'
                if url
                else ""
            )
            st.markdown(
                f"""<div class="comment-card">
                      <div class="meta">
                        <span class="badge">{html.escape(keyword or language)}</span>
                        {html.escape(source)} · {html.escape(language)} · {published}
                      </div>
                      <div class="body">{title_html}</div>
                      {link_html}
                    </div>""",
                unsafe_allow_html=True,
            )


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if "platform" in out.columns:
        out = out[out["platform"].isin(selected_platforms)]
    if "entity_key" in out.columns:
        out = out[out["entity_key"].isin(selected_entities)]
    return out


# Filled in by the filter bar, which is rendered under the header so the
# account picker is part of the page rather than a cramped sidebar.
selected_platforms: list[str] = []
selected_entities: list[str] = []


# --------------------------------------------------------------------------- #
# Header + KPIs
# --------------------------------------------------------------------------- #
latest_run_time = (
    followers_df["run_started_at"].max() if not followers_df.empty else None
)
updated = None
if latest_run_time is not None and pd.notna(latest_run_time):
    updated = latest_run_time.strftime("%Y-%m-%d  ·  %H:%M UTC")
header(
    "لوحة وسائل التواصل الاجتماعي",
    "المكتب الاستراتيجي لتطوير منطقة الجوف",
    "تقرير آلي كل أسبوعين",
    updated,
)

with st.container(key="phone_refresh"):
    if st.button("تحديث البيانات", key="refresh_phone", width="stretch"):
        st.cache_data.clear()
        st.rerun()

# Synthetic data must be impossible to mistake for a real collection run.
is_demo = (
    not comments_df.empty
    and "model" in comments_df.columns
    and (comments_df["model"] == DEMO_MODEL_TAG).any()
)
if is_demo:
    st.warning(
        "⚠️ **بيانات تجريبية (Demo)** — هذه الأرقام والتعليقات مولَّدة تلقائياً "
        "لعرض اللوحة فقط وليست بيانات حقيقية. "
        "لجمع البيانات الفعلية: اضبط `APIFY_TOKEN` و`OPENAI_API_KEY` ثم شغّل "
        "`python -m aljouf_report run`."
    )


def owned_totals(df: pd.DataFrame) -> tuple[int | None, int | None]:
    """Current follower total for our accounts, and how many were added since the last refresh.

    The total is every comparable count from the latest run. WhatsApp and
    Snapchat stay on the platform list and are not added here. The change only
    includes accounts that already had a number last time, so a platform
    collected for the first time does not look like thousands of new followers.
    Runs with no counts at all are skipped, so a failed collection does not
    zero the total.
    """
    if df.empty:
        return None, None
    owned = df[df["owned"] & df["followers"].notna()]
    if "platform" in owned.columns:
        owned = owned[~owned["platform"].isin(OWNED_ONLY_PLATFORMS)]
    if owned.empty:
        return None, None
    runs = sorted(owned["run_id"].unique())
    latest_rows = owned[owned["run_id"] == runs[-1]]
    latest = int(latest_rows["followers"].sum())
    if len(runs) < 2:
        return latest, None

    keys = ["entity_key", "platform"]
    previous_rows = owned[owned["run_id"] == runs[-2]]
    shared = latest_rows.merge(
        previous_rows[keys + ["followers"]],
        on=keys,
        how="inner",
        suffixes=("_now", "_before"),
    )
    if shared.empty:
        return latest, None
    added = int(shared["followers_now"].sum() - shared["followers_before"].sum())
    return latest, added


def entity_styles(names: list[str], our_names: set[str]) -> dict[str, dict]:
    """Color, dash, and marker for each entity line.

    Benchmarks cycle through a dark palette. Dashes differ so two regions with
    nearly the same follower total, drawn on top of each other, both stay visible.
    """
    styles: dict[str, dict] = {}
    bench = 0
    for name in names:
        if name in our_names:
            styles[name] = {
                "color": PRIMARY,
                "dash": "solid",
                "symbol": "circle",
                "width": 4.5,
                "size": 10,
            }
            continue
        styles[name] = {
            "color": BENCHMARK_COLORS[bench % len(BENCHMARK_COLORS)],
            "dash": LINE_DASHES[bench % len(LINE_DASHES)],
            "symbol": MARKER_SYMBOLS[bench % len(MARKER_SYMBOLS)],
            "width": 2.6,
            "size": 8,
        }
        bench += 1
    return styles


def owned_by_platform(df: pd.DataFrame) -> pd.DataFrame:
    """Per-platform follower counts for our accounts: latest run vs previous."""
    if df.empty:
        return pd.DataFrame()
    owned = df[df["owned"] & df["followers"].notna()]
    if owned.empty:
        return pd.DataFrame()
    # Each platform keeps its own last successful count. A later run that
    # failed to collect Instagram must not replace 12 followers with a blank.
    latest = owned.loc[owned.groupby("platform")["run_id"].idxmax()]
    out = latest.groupby("المنصة")["followers"].sum().rename("now").to_frame()
    previous_rows = []
    for _, group in owned.groupby("platform"):
        ordered = group.sort_values("run_id")
        if len(ordered) >= 2:
            previous_rows.append(ordered.iloc[-2])
    if previous_rows:
        before = pd.DataFrame(previous_rows).groupby("المنصة")["followers"].sum()
        out["before"] = before.reindex(out.index)
    else:
        out["before"] = pd.NA
    out = out.sort_values("now", ascending=False)
    for platform in OWNED_ONLY_PLATFORMS:
        label = PLATFORM_AR[platform]
        if label not in out.index:
            out.loc[label] = {"now": pd.NA, "before": pd.NA}
    return out


entity_labels = {e.key: e.short_name for e in config.accounts.entities}
owned_keys = {e.key for e in config.accounts.entities if e.owned}
present_keys = (
    list(dict.fromkeys(followers_df["entity_key"].tolist()))
    if not followers_df.empty
    else list(entity_labels)
)
present_keys.sort(key=lambda key: (key not in owned_keys, entity_labels.get(key, key)))
_platform_order = ("instagram", "x", "youtube", "linkedin", *OWNED_ONLY_PLATFORMS)
_present_platforms = (
    set(followers_df["platform"].dropna()) if not followers_df.empty else set()
)
platform_options = [
    platform
    for platform in _platform_order
    if platform in _present_platforms or platform in OWNED_ONLY_PLATFORMS
]

with st.container(border=True):
    st.markdown("##### الحسابات المعروضة")
    scope = st.segmented_control(
        "نطاق الحسابات",
        options=["all", "ours"],
        default="all",
        format_func=lambda key: {"all": "كل الجهات", "ours": "حساباتنا"}[key],
        key="account_view",
        label_visibility="collapsed",
    )
    if scope == "ours":
        choices = [key for key in present_keys if key in owned_keys]
    else:
        choices = present_keys

    def _account_label(key: str) -> str:
        name = entity_labels.get(key, key)
        return f"{name}  ·  حساباتنا" if key in owned_keys else name

    if choices:
        picked_entities = st.pills(
            "الجهات",
            options=choices,
            selection_mode="multi",
            default=choices,
            format_func=_account_label,
            key=f"entity_pills_{scope}",
        )
        selected_entities = list(picked_entities or [])
    if platform_options:
        picked_platforms = st.pills(
            "المنصات",
            options=platform_options,
            selection_mode="multi",
            default=platform_options,
            format_func=lambda platform: PLATFORM_AR.get(platform, platform),
            key="platform_pills",
        )
        selected_platforms = list(picked_platforms or [])

if not selected_entities:
    st.info("اختر جهة واحدة على الأقل.")

followers_f = apply_filters(followers_df)
comments_f = apply_filters(comments_df)

total_now, followers_added = owned_totals(followers_f)
sentiment_counts = (
    comments_f["label"].value_counts().to_dict() if not comments_f.empty else {}
)
total_scored = sum(sentiment_counts.values())
negative_count = sentiment_counts.get("Negative", 0)
positive_share = (
    sentiment_counts.get("Positive", 0) / total_scored * 100 if total_scored else 0
)

week_likes = Database(db_path).week_post_likes(
    get_config().settings.windows.comments_scrape_days
)
if week_likes is not None:
    comment_likes = week_likes
elif "likes" in comments_f.columns and comments_f["likes"].notna().any():
    comment_likes = int(comments_f["likes"].fillna(0).sum())
else:
    comment_likes = None
neutral_count = sentiment_counts.get("Neutral", 0)
by_platform = owned_by_platform(followers_f)
platform_count = 0 if by_platform.empty else len(by_platform)
if followers_added is None:
    extra_label = "—"
elif followers_added > 0:
    extra_label = f"+{followers_added:,}"
elif followers_added < 0:
    extra_label = f"−{abs(followers_added):,}"
else:
    extra_label = "0"
grid, grid_images = insight_table(
    f"{total_now:,}" if total_now is not None else "—",
    extra_label,
    "—" if comment_likes is None else f"{comment_likes:,}",
    f"{total_scored:,}",
    f"{len(news_df):,}",
    f"{platform_count:,}",
    f"{positive_share:.0f}%" if total_scored else "—",
    f"{(negative_count / total_scored * 100):.0f}%" if total_scored else "—",
    f"{(neutral_count / total_scored * 100):.0f}%" if total_scored else "—",
)
st.markdown("##### إحصائيات الحسابات")
st.markdown(
    f'<div class="insight-card">{embed_images(grid, grid_images)}</div>',
    unsafe_allow_html=True,
)

if not by_platform.empty:
    st.markdown("##### متابعو حساباتنا حسب المنصة")
    for column, (platform, row) in zip(
        st.columns(len(by_platform)), by_platform.iterrows()
    ):
        delta = None
        # A zero change is not growth; Streamlit draws an arrow on any delta,
        # so switch the arrow off when the count is unchanged.
        delta_color = "normal"
        if pd.notna(row["before"]):
            diff = int(row["now"]) - int(row["before"])
            if diff:
                delta = f"{diff:+,}"
            else:
                delta = "بدون تغيير"
                delta_color = "off"
        if pd.isna(row["now"]):
            column.metric(str(platform), "—")
        else:
            column.metric(
                str(platform), f"{int(row['now']):,}", delta=delta, delta_color=delta_color
            )

st.divider()


# --------------------------------------------------------------------------- #
# Tabs
# --------------------------------------------------------------------------- #
tab_growth, tab_sentiment, tab_news, tab_review = st.tabs(
    ["📈 نمو المتابعين", "🧭 تحليل المشاعر", "📰 الأخبار والرصد", "⚠️ مراجعة التعليقات السلبية"]
)


# ---------------------------- Follower growth ------------------------------ #
with tab_growth:
    if followers_f.empty:
        st.info("لا توجد بيانات متابعين. شغّل خط المعالجة لجمع البيانات.")
    else:
        valid = followers_f.dropna(subset=["followers"])
        if "platform" in valid.columns:
            valid = valid[~valid["platform"].isin(OWNED_ONLY_PLATFORMS)]
        if valid.empty:
            st.warning(
                "تم تسجيل الحسابات لكن لم يتم جمع أي عدد متابعين. "
                "تحقق من ضبط `APIFY_TOKEN`."
            )
        else:
            newest_by_entity = valid.groupby("entity_key")["run_id"].transform("max")
            current = valid[valid["run_id"] == newest_by_entity]
            our_names = set(current[current["owned"]]["الجهة"].unique())
            entity_order = sorted(
                current["الجهة"].unique(), key=lambda name: (name not in our_names, name)
            )
            styles = entity_styles(entity_order, our_names)
            platform_order = [
                PLATFORM_AR[name]
                for name in ("instagram", "x", "youtube", "linkedin")
                if PLATFORM_AR.get(name) in set(current["المنصة"])
            ]
            if scope != "ours":
                st.markdown("#### المتابعون حسب المنصة: حساباتنا مقابل الجهات المرجعية")
                st.caption("عدد المتابعين في كل منصة. حساباتنا تظهر مع الجهات المرجعية في كل مجموعة.")
                by_network = px.bar(
                    current,
                    x="المنصة",
                    y="followers",
                    color="الجهة",
                    barmode="group",
                    labels={"followers": "عدد المتابعين", "المنصة": ""},
                    text_auto=".2s",
                    category_orders={"الجهة": entity_order, "المنصة": platform_order},
                    color_discrete_map={
                        name: style["color"] for name, style in styles.items()
                    },
                )
                show_chart(by_network, height=480)

            st.markdown("#### المتابعون حسب المنصة (آخر تشغيل)")
            counted = followers_f.dropna(subset=["followers"])
            ours = counted[counted["owned"]] if "owned" in counted.columns else counted
            latest = ours.loc[ours.groupby("platform")["run_id"].idxmax()] if not ours.empty else ours
            our_platform_order = [
                PLATFORM_AR[name]
                for name in ("instagram", "x", "youtube", "linkedin", "whatsapp", "snapchat")
                if PLATFORM_AR.get(name) in set(latest["المنصة"])
            ]
            by_platform = px.bar(
                latest,
                x="المنصة",
                y="followers",
                color="الجهة",
                barmode="group",
                labels={"followers": "عدد المتابعين", "المنصة": ""},
                text_auto=".3s",
                color_discrete_map={
                    name: style["color"] for name, style in styles.items()
                },
                category_orders={"الجهة": entity_order, "المنصة": our_platform_order},
            )
            show_chart(by_platform, height=430)

            if scope != "ours":
                st.markdown("#### مقارنة المتابعين بين المكاتب")
                newest_by_entity = valid.groupby("entity_key")["run_id"].transform("max")
                office_rows = valid[valid["run_id"] == newest_by_entity]
                office_totals = (
                    office_rows.groupby(["الجهة", "النوع"], as_index=False)["followers"]
                    .sum()
                    .sort_values("followers")
                )
                comparison = px.bar(
                    office_totals,
                    x="followers",
                    y="الجهة",
                    color="النوع",
                    orientation="h",
                    labels={"followers": "عدد المتابعين", "الجهة": ""},
                    color_discrete_map={"حساباتنا": PRIMARY, "الجهات المرجعية": "#c4a35a"},
                    text_auto=".3s",
                )
                show_chart(comparison, height=480)

            with st.expander("📋 جدول المتابعين التفصيلي (آخر تشغيل)"):
                table = latest[
                    ["الجهة", "النوع", "المنصة", "followers", "handle", "profile_url"]
                ].rename(
                    columns={
                        "followers": "المتابعون",
                        "handle": "المعرف",
                        "profile_url": "الرابط",
                    }
                )
                st.dataframe(
                    table.sort_values("المتابعون", ascending=False),
                    hide_index=True,
                    column_config={
                        "الرابط": st.column_config.LinkColumn("الرابط", display_text="زيارة"),
                        "المتابعون": st.column_config.NumberColumn(format="%d"),
                    },
                )

            # Scope to the newest run: a failure that has since been fixed must
            # not keep reporting the collector as broken.
            newest_run = followers_f["run_id"].max()
            failures = followers_f[
                (followers_f["run_id"] == newest_run) & followers_f["followers"].isna()
            ]
            if not failures.empty:
                with st.expander(
                    f"⚠️ حسابات لم يتم جمع بياناتها في آخر تشغيل ({len(failures)})"
                ):
                    st.dataframe(
                        failures[["الجهة", "المنصة", "handle", "error"]].rename(
                            columns={"handle": "المعرف", "error": "السبب"}
                        ),
                        hide_index=True,
                    )


# ------------------------------- Sentiment --------------------------------- #
with tab_sentiment:
    if comments_f.empty:
        st.info("لا توجد تعليقات في النافذة الزمنية المحددة.")
    else:
        scored = comments_f[comments_f["label"].notna()]
        if scored.empty:
            st.warning(
                "تم جمع التعليقات لكن لم يتم تحليلها. تحقق من ضبط `OPENAI_API_KEY`."
            )
        else:
            left, right = st.columns([1, 1])

            with left:
                st.markdown("#### توزيع المشاعر")
                counts = scored["label"].value_counts()
                donut = go.Figure(
                    go.Pie(
                        labels=[SENTIMENT_AR.get(k, k) for k in counts.index],
                        values=counts.values,
                        hole=0.55,
                        marker=dict(
                            colors=[SENTIMENT_COLORS.get(k, "#bdc3c7") for k in counts.index]
                        ),
                        textinfo="percent+value",
                        textfont=dict(size=15),
                        sort=False,
                    )
                )
                donut.add_annotation(
                    text=f"<b>{len(scored):,}</b><br>تعليق",
                    showarrow=False,
                    font=dict(size=17),
                )
                show_chart(donut, height=400)

            with right:
                st.markdown("#### المشاعر حسب المنصة")
                by_platform = (
                    scored.groupby(["المنصة", "التصنيف"], as_index=False)
                    .size()
                    .rename(columns={"size": "العدد"})
                )
                bars = px.bar(
                    by_platform,
                    x="المنصة",
                    y="العدد",
                    color="التصنيف",
                    barmode="stack",
                    color_discrete_map={
                        SENTIMENT_AR["Positive"]: SENTIMENT_COLORS["Positive"],
                        SENTIMENT_AR["Negative"]: SENTIMENT_COLORS["Negative"],
                        SENTIMENT_AR["Neutral"]: SENTIMENT_COLORS["Neutral"],
                    },
                    labels={"المنصة": ""},
                    text_auto=True,
                )
                show_chart(bars, height=400)

            st.markdown("#### جميع التعليقات المحللة")
            label_filter = st.pills(
                "تصفية بالتصنيف",
                options=["Positive", "Neutral", "Negative"],
                selection_mode="multi",
                default=["Positive", "Neutral", "Negative"],
                format_func=lambda lab: SENTIMENT_AR.get(lab, lab),
                key="sentiment_label_filter",
            )
            view = scored[scored["label"].isin(label_filter or [])]
            st.caption(f"{len(view):,} تعليق")
            render_comments(view, height=780)


# --------------------------------- News ------------------------------------ #
with tab_news:
    if news_df.empty:
        st.info("لا توجد أخبار في النافذة الزمنية المحددة.")
    else:
        st.markdown(
            f"#### الأخبار والترندات (آخر {config.settings.windows.news_lookback_days} يوماً)"
        )

        keyword_options = sorted(
            {
                k.strip()
                for row in news_df["keyword"].fillna("")
                for k in str(row).split(",")
                if k.strip()
            }
        )
        keyword_filter = st.pills(
            "الكلمات المفتاحية",
            options=keyword_options,
            selection_mode="multi",
            default=keyword_options,
            key="news_keywords",
        )
        lang_options = sorted(news_df["language"].fillna("").unique())
        lang_labels = {"ar": "العربية", "en": "English"}
        lang_filter = st.pills(
            "اللغة",
            options=lang_options,
            selection_mode="multi",
            default=lang_options,
            format_func=lambda code: lang_labels.get(code, code),
            key="news_languages",
        )
        search = st.text_input("بحث في العنوان", placeholder="اكتب كلمة للبحث…")

        view = news_df.copy()
        if keyword_filter:
            pattern = "|".join(pd.Series(list(keyword_filter)).map(lambda k: str(k)).tolist())
            view = view[
                view["keyword"].fillna("").str.contains(pattern, case=False, regex=True)
            ]
        else:
            view = view.iloc[0:0]
        if lang_filter:
            view = view[view["language"].fillna("").isin(list(lang_filter))]
        else:
            view = view.iloc[0:0]
        if search:
            view = view[view["title"].str.contains(search, case=False, na=False)]

        st.caption(f"{len(view):,} خبر")
        render_news(view, height=780)

        g1, g2 = st.columns(2)
        with g1:
            st.markdown("##### عدد الأخبار حسب الكلمة المفتاحية")
            keyword_counts: dict[str, int] = {}
            for row in view["keyword"].fillna(""):
                for keyword in str(row).split(","):
                    key = keyword.strip()
                    if key:
                        keyword_counts[key] = keyword_counts.get(key, 0) + 1
            if keyword_counts:
                kdf = pd.DataFrame(
                    sorted(keyword_counts.items(), key=lambda kv: kv[1]),
                    columns=["الكلمة المفتاحية", "العدد"],
                )
                fig = px.bar(
                    kdf,
                    x="العدد",
                    y="الكلمة المفتاحية",
                    orientation="h",
                    text_auto=True,
                    color_discrete_sequence=[PRIMARY],
                )
                show_chart(fig, height=340)

        with g2:
            st.markdown("##### أكثر المصادر نشراً")
            top_sources = (
                view["domain"].fillna("غير معروف").value_counts().head(10).sort_values()
            )
            if not top_sources.empty:
                sdf = pd.DataFrame(
                    {"المصدر": top_sources.index, "العدد": top_sources.values}
                )
                fig = px.bar(
                    sdf,
                    x="العدد",
                    y="المصدر",
                    orientation="h",
                    text_auto=True,
                    color_discrete_sequence=[ACCENT],
                )
                show_chart(fig, height=340)


# --------------------------- Negative review ------------------------------- #
with tab_review:
    st.markdown("#### التعليقات السلبية للمراجعة السريعة")
    negative = (
        comments_f[comments_f["label"] == "Negative"] if not comments_f.empty else pd.DataFrame()
    )

    if negative.empty:
        st.success("لا توجد تعليقات سلبية في النافذة الزمنية المحددة. 🎉")
    else:
        st.caption(
            f"{len(negative):,} تعليق سلبي خلال "
            f"{config.settings.windows.comments_lookback_days} أيام. "
            "السبب مولَّد آلياً بواسطة نموذج لغوي يراعي اللهجة السعودية والسخرية."
        )

        render_comments(negative, height=640)

        csv = negative[["published_at", "platform", "author", "text", "reason_ar"]].to_csv(
            index=False
        )
        st.download_button(
            "⬇️ تنزيل التعليقات السلبية (CSV)",
            data=csv.encode("utf-8-sig"),  # BOM so Excel renders Arabic correctly
            file_name="negative_comments.csv",
            mime="text/csv",
        )


st.divider()
st.caption(
    "تقرير آلي · البيانات مجمّعة من مصادر عامة عبر Apify دون استخدام حسابات إدارية "
    "أو واجهات برمجية رسمية · تحليل المشاعر بواسطة نموذج لغوي مخصص للهجة السعودية."
)
