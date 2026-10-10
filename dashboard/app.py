"""Interactive weekly social media dashboard (Arabic / RTL).

Run with:
    python -m aljouf_report dashboard
    # or: streamlit run dashboard/app.py
"""

from __future__ import annotations

import html
import sys
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
for extra_path in (PROJECT_ROOT / "src", HERE):
    if str(extra_path) not in sys.path:
        sys.path.insert(0, str(extra_path))

from aljouf_report.config import load_config  # noqa: E402
from aljouf_report.models import DEMO_MODEL_TAG, PLATFORM_AR, SENTIMENT_AR  # noqa: E402
from aljouf_report.storage import Database  # noqa: E402
from theme import SENTIMENT_COLORS, apply_rtl, header, rtl_layout  # noqa: E402

st.set_page_config(
    page_title="لوحة وسائل التواصل | المكتب الاستراتيجي لتطوير منطقة الجوف",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_rtl()


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
        followers["الجهة"] = followers["entity_name_ar"]
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
    header("لوحة وسائل التواصل الاجتماعي", "لا توجد بيانات بعد")
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


# --------------------------------------------------------------------------- #
# Sidebar filters
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.markdown("### ⚙️ عوامل التصفية")

    platform_options = sorted(followers_df["platform"].unique()) if not followers_df.empty else []
    selected_platforms = st.multiselect(
        "المنصات",
        options=platform_options,
        default=platform_options,
        format_func=lambda p: PLATFORM_AR.get(p, p),
    )

    entity_options = (
        followers_df[["entity_key", "entity_name_ar"]]
        .drop_duplicates()
        .set_index("entity_key")["entity_name_ar"]
        .to_dict()
        if not followers_df.empty
        else {}
    )
    selected_entities = st.multiselect(
        "الجهات",
        options=list(entity_options.keys()),
        default=list(entity_options.keys()),
        format_func=lambda k: entity_options.get(k, k),
    )

    st.divider()
    if st.button("🔄 تحديث البيانات"):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption(f"قاعدة البيانات: `{config.db_path.name}`")
    st.caption(f"نافذة الأخبار: {config.settings.windows.news_lookback_days} يوماً")
    st.caption(f"نافذة التعليقات: {config.settings.windows.comments_lookback_days} أيام")
    refresh_minutes = config.settings.dashboard.auto_refresh_minutes
    if refresh_minutes > 0:
        st.caption(f"تحديث تلقائي: كل {refresh_minutes} دقيقة")

auto_refresh(config.settings.dashboard.auto_refresh_minutes)


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if selected_platforms and "platform" in out.columns:
        out = out[out["platform"].isin(selected_platforms)]
    if selected_entities and "entity_key" in out.columns:
        out = out[out["entity_key"].isin(selected_entities)]
    return out


followers_f = apply_filters(followers_df)
comments_f = apply_filters(comments_df)


# --------------------------------------------------------------------------- #
# Header + KPIs
# --------------------------------------------------------------------------- #
latest_run_time = (
    followers_df["run_started_at"].max() if not followers_df.empty else None
)
subtitle = "المكتب الاستراتيجي لتطوير منطقة الجوف · تقرير آلي أسبوعي"
if latest_run_time is not None and pd.notna(latest_run_time):
    subtitle += f" · آخر تحديث {latest_run_time.strftime('%Y-%m-%d %H:%M')} UTC"
header("📊 لوحة وسائل التواصل الاجتماعي", subtitle)

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
    """Total followers for our accounts in the latest run vs the previous one.

    Runs that collected no follower counts (a missing Apify token, or an actor
    outage) are skipped, so a failed run does not report the total as zero.
    """
    if df.empty:
        return None, None
    owned = df[df["owned"] & df["followers"].notna()]
    if owned.empty:
        return None, None
    runs = sorted(owned["run_id"].unique())
    latest = int(owned[owned["run_id"] == runs[-1]]["followers"].sum())
    if len(runs) < 2:
        return latest, None
    previous = int(owned[owned["run_id"] == runs[-2]]["followers"].sum())
    return latest, previous


def owned_by_platform(df: pd.DataFrame) -> pd.DataFrame:
    """Per-platform follower counts for our accounts: latest run vs previous."""
    if df.empty:
        return pd.DataFrame()
    owned = df[df["owned"] & df["followers"].notna()]
    if owned.empty:
        return pd.DataFrame()
    runs = sorted(owned["run_id"].unique())
    out = (
        owned[owned["run_id"] == runs[-1]]
        .groupby("المنصة")["followers"]
        .sum()
        .rename("now")
        .to_frame()
    )
    out["before"] = (
        owned[owned["run_id"] == runs[-2]].groupby("المنصة")["followers"].sum()
        if len(runs) >= 2
        else pd.NA
    )
    return out.sort_values("now", ascending=False)


total_now, total_before = owned_totals(followers_f)
sentiment_counts = (
    comments_f["label"].value_counts().to_dict() if not comments_f.empty else {}
)
total_scored = sum(sentiment_counts.values())
negative_count = sentiment_counts.get("Negative", 0)
positive_share = (
    sentiment_counts.get("Positive", 0) / total_scored * 100 if total_scored else 0
)

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric(
        "إجمالي متابعي حساباتنا",
        f"{total_now:,}" if total_now is not None else "—",
        delta=(
            f"{total_now - total_before:+,}"
            if total_now is not None and total_before is not None
            else None
        ),
    )
with col2:
    st.metric("أخبار ورصد", f"{len(news_df):,}")
with col3:
    st.metric("تعليقات محللة", f"{total_scored:,}")
with col4:
    st.metric(
        "نسبة الإيجابية",
        f"{positive_share:.0f}%",
        delta=f"{negative_count} تعليق سلبي" if negative_count else "لا سلبيات",
        delta_color="inverse" if negative_count else "normal",
    )

by_platform = owned_by_platform(followers_f)
if not by_platform.empty:
    st.markdown("##### متابعو حساباتنا حسب المنصة")
    for column, (platform, row) in zip(
        st.columns(len(by_platform)), by_platform.iterrows()
    ):
        delta = None
        if pd.notna(row["before"]):
            diff = int(row["now"]) - int(row["before"])
            delta = f"{diff:+,}" if diff else "بدون تغيير"
        column.metric(str(platform), f"{int(row['now']):,}", delta=delta)

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
        st.markdown("#### نمو المتابعين: حساباتنا مقابل الجهات المرجعية")

        valid = followers_f.dropna(subset=["followers"])
        if valid.empty:
            st.warning(
                "تم تسجيل الحسابات لكن لم يتم جمع أي عدد متابعين. "
                "تحقق من ضبط `APIFY_TOKEN`."
            )
        else:
            trend = (
                valid.groupby(["run_started_at", "الجهة", "النوع"], as_index=False)[
                    "followers"
                ]
                .sum()
                .sort_values("run_started_at")
            )

            runs_available = trend["run_started_at"].nunique()
            if runs_available < 2:
                # A single run has no trend to draw, so compare levels instead.
                st.caption(
                    "يتوفر تشغيل واحد فقط حتى الآن، لذا يُعرض المقارنة كأعمدة. "
                    "سيظهر الرسم الزمني بعد التشغيل الأسبوعي القادم."
                )
                latest = trend[trend["run_started_at"] == trend["run_started_at"].max()]
                fig = px.bar(
                    latest.sort_values("followers"),
                    x="followers",
                    y="الجهة",
                    color="النوع",
                    orientation="h",
                    labels={"followers": "عدد المتابعين", "الجهة": ""},
                    color_discrete_map={"حساباتنا": "#118a6e", "الجهات المرجعية": "#b0bec5"},
                    text_auto=".2s",
                )
                st.plotly_chart(
                    rtl_layout(fig, height=460)
                )
            else:
                st.caption(
                    "إجمالي المتابعين لكل جهة عبر المنصات المحددة. "
                    "خط حساباتنا مميّز بالسماكة واللون."
                )
                our_names = set(valid[valid["owned"]]["الجهة"].unique())
                # Draw our own series last so it sits on top, and give the
                # benchmark set a muted palette so the comparison reads at a
                # glance instead of as ten competing lines.
                fig = px.line(
                    trend,
                    x="run_started_at",
                    y="followers",
                    color="الجهة",
                    markers=True,
                    labels={
                        "run_started_at": "تاريخ التشغيل",
                        "followers": "عدد المتابعين",
                    },
                    category_orders={
                        "الجهة": sorted(
                            trend["الجهة"].unique(), key=lambda n: n in our_names
                        )
                    },
                )
                for trace in fig.data:
                    if trace.name in our_names:
                        trace.line.update(width=4.5, color="#0b5d4a")
                        trace.marker.update(size=9)
                        trace.opacity = 1.0
                    else:
                        trace.line.update(width=1.5)
                        trace.marker.update(size=5)
                        trace.opacity = 0.5
                st.plotly_chart(
                    rtl_layout(fig, height=480)
                )

            st.markdown("#### المتابعون حسب المنصة (آخر تشغيل)")
            latest_run = valid["run_id"].max()
            latest = valid[valid["run_id"] == latest_run]
            by_platform = px.bar(
                latest,
                x="المنصة",
                y="followers",
                color="الجهة",
                barmode="group",
                labels={"followers": "عدد المتابعين", "المنصة": ""},
                text_auto=".2s",
            )
            st.plotly_chart(
                rtl_layout(by_platform, height=430)
            )

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
                st.plotly_chart(rtl_layout(donut, height=400))

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
                st.plotly_chart(rtl_layout(bars, height=400))

            st.markdown("#### جميع التعليقات المحللة")
            label_filter = st.multiselect(
                "تصفية بالتصنيف",
                options=["Positive", "Neutral", "Negative"],
                default=["Positive", "Neutral", "Negative"],
                format_func=lambda lab: SENTIMENT_AR.get(lab, lab),
                key="sentiment_label_filter",
            )
            view = scored[scored["label"].isin(label_filter)]
            st.dataframe(
                view[
                    ["published_at", "المنصة", "التصنيف", "text", "reason_ar", "author", "likes"]
                ]
                .rename(
                    columns={
                        "published_at": "التاريخ",
                        "text": "التعليق",
                        "reason_ar": "سبب التصنيف",
                        "author": "الحساب",
                        "likes": "إعجابات",
                    }
                )
                .sort_values("التاريخ", ascending=False),
                hide_index=True,
                height=420,
                column_config={
                    "التاريخ": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
                    "التعليق": st.column_config.TextColumn(width="large"),
                    "سبب التصنيف": st.column_config.TextColumn(width="medium"),
                },
            )


# --------------------------------- News ------------------------------------ #
with tab_news:
    if news_df.empty:
        st.info("لا توجد أخبار في النافذة الزمنية المحددة.")
    else:
        st.markdown(
            f"#### الأخبار والترندات (آخر {config.settings.windows.news_lookback_days} يوماً)"
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            keyword_options = sorted(
                {
                    k.strip()
                    for row in news_df["keyword"].fillna("")
                    for k in str(row).split(",")
                    if k.strip()
                }
            )
            keyword_filter = st.multiselect(
                "الكلمات المفتاحية", keyword_options, default=keyword_options
            )
        with c2:
            lang_options = sorted(news_df["language"].fillna("").unique())
            lang_filter = st.multiselect("اللغة", lang_options, default=lang_options)
        with c3:
            search = st.text_input("بحث في العنوان", placeholder="اكتب كلمة للبحث…")

        view = news_df.copy()
        if keyword_filter:
            pattern = "|".join(pd.Series(keyword_filter).map(lambda k: str(k)).tolist())
            view = view[
                view["keyword"].fillna("").str.contains(pattern, case=False, regex=True)
            ]
        if lang_filter:
            view = view[view["language"].fillna("").isin(lang_filter)]
        if search:
            view = view[view["title"].str.contains(search, case=False, na=False)]

        st.caption(f"عدد النتائج: {len(view):,} من أصل {len(news_df):,}")

        st.dataframe(
            view[["published_at", "title", "source", "keyword", "language", "url"]]
            .rename(
                columns={
                    "published_at": "تاريخ النشر",
                    "title": "العنوان",
                    "source": "المصدر",
                    "keyword": "الكلمة المفتاحية",
                    "language": "اللغة",
                    "url": "الرابط",
                }
            )
            .sort_values("تاريخ النشر", ascending=False),
            hide_index=True,
            height=460,
            column_config={
                "تاريخ النشر": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
                "العنوان": st.column_config.TextColumn(width="large"),
                "الرابط": st.column_config.LinkColumn("الرابط", display_text="فتح"),
            },
        )

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
                    color_discrete_sequence=["#118a6e"],
                )
                st.plotly_chart(rtl_layout(fig, height=340))

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
                    color_discrete_sequence=["#0b5d4a"],
                )
                st.plotly_chart(rtl_layout(fig, height=340))


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

        st.dataframe(
            negative[
                ["published_at", "المنصة", "author", "text", "reason_ar", "likes", "comment_url"]
            ]
            .rename(
                columns={
                    "published_at": "التاريخ",
                    "author": "الحساب",
                    "text": "التعليق",
                    "reason_ar": "سبب التصنيف",
                    "likes": "إعجابات",
                    "comment_url": "الرابط",
                }
            )
            .sort_values("التاريخ", ascending=False),
            hide_index=True,
            height=380,
            column_config={
                "التاريخ": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
                "التعليق": st.column_config.TextColumn(width="large"),
                "سبب التصنيف": st.column_config.TextColumn(width="medium"),
                "الرابط": st.column_config.LinkColumn("الرابط", display_text="فتح"),
            },
        )

        st.markdown("##### عرض تفصيلي")
        for _, row in negative.head(25).iterrows():
            published = (
                row["published_at"].strftime("%Y-%m-%d %H:%M")
                if pd.notna(row.get("published_at"))
                else "تاريخ غير معروف"
            )
            st.markdown(
                f"""<div class="comment-card">
                      <div class="meta">
                        {html.escape(str(row.get('المنصة', '')))} ·
                        {html.escape(str(row.get('author') or 'مستخدم'))} ·
                        {published}
                      </div>
                      <div class="body">{html.escape(str(row.get('text') or ''))}</div>
                      <div class="reason">⚠️ {html.escape(str(row.get('reason_ar') or ''))}</div>
                    </div>""",
                unsafe_allow_html=True,
            )

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
