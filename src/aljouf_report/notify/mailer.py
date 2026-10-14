"""Weekly email: run summary plus a link to the live dashboard."""

from __future__ import annotations

import html
import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from typing import Any, Sequence

from ..config import AppConfig
from ..models import PLATFORM_AR, SENTIMENT_AR, RunSummary

log = logging.getLogger(__name__)

LABEL_COLORS = {"Positive": "#1b8a5a", "Negative": "#c0392b", "Neutral": "#7f8c8d"}


class EmailNotConfigured(RuntimeError):
    pass


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"


def _is_national_news(keyword: str) -> bool:
    text = keyword.casefold()
    return "saudi" in text or "السعودية" in keyword


def _fmt_delta(delta: int | None) -> str:
    if delta is None:
        return '<span style="color:#7f8c8d">—</span>'
    if delta > 0:
        return f'<span style="color:#1b8a5a">▲ {delta:,}</span>'
    if delta < 0:
        return f'<span style="color:#c0392b">▼ {abs(delta):,}</span>'
    return '<span style="color:#7f8c8d">0</span>'


class WeeklyMailer:
    def __init__(self, config: AppConfig):
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.secrets.smtp_ready and bool(self.config.recipients)

    # -- rendering -------------------------------------------------------- #
    def render_html(
        self,
        summary: RunSummary,
        owned_followers: Sequence[dict[str, Any]],
        top_negative: Sequence[dict[str, Any]],
        week_comments: Sequence[dict[str, Any]],
        week_news: Sequence[dict[str, Any]],
    ) -> str:
        cfg = self.config.settings
        report_days = cfg.windows.comments_scrape_days
        sentiment = summary.sentiment_counts
        total_scored = sum(sentiment.values()) or 1

        follower_total = sum(
            int(row["followers"])
            for row in owned_followers
            if row.get("followers") is not None
        )
        follower_rows = "\n".join(
            f"""
            <tr>
              <td style="{_TD}">{_esc(PLATFORM_AR.get(row['platform'], row['platform']))}</td>
              <td style="{_TD}"><strong>{_fmt_int(row.get('followers'))}</strong></td>
              <td style="{_TD}">{_fmt_delta(row.get('delta'))}</td>
            </tr>"""
            for row in owned_followers
        ) or f'<tr><td style="{_TD}" colspan="3">لا توجد بيانات متابعين.</td></tr>'

        sentiment_rows = "\n".join(
            f"""
            <tr>
              <td style="{_TD}">
                <span style="color:{LABEL_COLORS.get(label, '#333')};font-weight:700">
                  {_esc(SENTIMENT_AR.get(label, label))}
                </span>
              </td>
              <td style="{_TD}">{count:,}</td>
              <td style="{_TD}">{count / total_scored * 100:.0f}%</td>
            </tr>"""
            for label, count in sorted(
                sentiment.items(), key=lambda kv: kv[1], reverse=True
            )
        ) or f'<tr><td style="{_TD}" colspan="3">لا توجد تعليقات محللة.</td></tr>'

        negative_rows = "\n".join(
            f"""
            <tr>
              <td style="{_TD}">{_esc(PLATFORM_AR.get(row.get('platform'), row.get('platform')))}</td>
              <td style="{_TD}">{_esc(str(row.get('text') or '')[:220])}</td>
              <td style="{_TD};color:#c0392b">{_esc(row.get('reason_ar'))}</td>
            </tr>"""
            for row in top_negative
        ) or f'<tr><td style="{_TD}" colspan="3">لا توجد تعليقات سلبية هذا الأسبوع. 🎉</td></tr>'

        comment_rows = "\n".join(
            f"""
            <tr>
              <td style="{_TD}">{_esc(PLATFORM_AR.get(row.get('platform'), row.get('platform')))}</td>
              <td style="{_TD}">{_esc(str(row.get('text') or '')[:300])}</td>
              <td style="{_TD};color:{LABEL_COLORS.get(str(row.get('label')), '#333')};font-weight:700">
                {_esc(SENTIMENT_AR.get(str(row.get('label') or ''), row.get('label') or '—'))}
              </td>
            </tr>"""
            for row in list(week_comments)[:40]
        ) or f'<tr><td style="{_TD}" colspan="3">لا توجد تعليقات خلال آخر {report_days} أيام.</td></tr>'

        news_counts: dict[str, int] = {}
        for row in week_news:
            for keyword in str(row.get("keyword") or "").split(","):
                key = keyword.strip()
                if key:
                    news_counts[key] = news_counts.get(key, 0) + 1
        keyword_chips = " ".join(
            f'<span style="display:inline-block;background:#eef3f8;border-radius:12px;'
            f'padding:3px 10px;margin:2px;font-size:13px">{_esc(k)}: {v}</span>'
            for k, v in sorted(news_counts.items(), key=lambda kv: -kv[1])
        ) or "لا توجد أخبار."

        ranked_news = sorted(
            week_news,
            key=lambda row: row.get("published_at") or "",
            reverse=True,
        )
        ranked_news.sort(key=lambda row: _is_national_news(str(row.get("keyword") or "")))
        shown_news = ranked_news[:25]
        hidden_news = len(ranked_news) - len(shown_news)
        news_rows = "\n".join(
            f"""
            <tr>
              <td style="{_TD}"><a href="{_esc(row.get('url'))}" style="color:#0b5d4a">{_esc(row.get('title'))}</a></td>
              <td style="{_TD}">{_esc(row.get('source') or row.get('domain'))}</td>
              <td style="{_TD}">{_esc(str(row.get('published_at') or '')[:10])}</td>
            </tr>"""
            for row in shown_news
        ) or f'<tr><td style="{_TD}" colspan="3">لا توجد أخبار خلال آخر {report_days} أيام.</td></tr>'
        news_more = (
            f'<p style="margin:8px 0 0;font-size:13px;color:#7f8c8d">و{hidden_news} خبراً آخر على لوحة المعلومات.</p>'
            if hidden_news
            else ""
        )

        errors_block = ""
        if summary.errors:
            items = "\n".join(
                f"<li>{_esc(e)}</li>" for e in summary.errors[:12]
            )
            extra = (
                f"<li>… و{len(summary.errors) - 12} تحذيراً آخر</li>"
                if len(summary.errors) > 12
                else ""
            )
            errors_block = f"""
            <div style="margin-top:18px;padding:12px 16px;background:#fff8e1;
                        border-right:4px solid #f0ad4e;border-radius:6px">
              <strong>تحذيرات التشغيل ({len(summary.errors)})</strong>
              <ul style="margin:8px 0 0;padding-right:20px;font-size:13px;color:#8a6d3b">
                {items}{extra}
              </ul>
            </div>"""

        dashboard_url = _esc(self.config.dashboard_url)
        finished = summary.finished_at or summary.started_at

        return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f6f8;
             font-family:'Segoe UI',Tahoma,Arial,sans-serif;direction:rtl;text-align:right">
  <div style="max-width:720px;margin:0 auto;padding:24px">

    <div style="background:linear-gradient(135deg,#0b5d4a,#118a6e);color:#fff;
                border-radius:10px 10px 0 0;padding:24px">
      <h1 style="margin:0;font-size:22px">تقرير وسائل التواصل الاجتماعي</h1>
      <p style="margin:6px 0 0;opacity:.9;font-size:14px">
        المكتب الاستراتيجي لتطوير منطقة الجوف ·
        {_esc(finished.strftime('%Y-%m-%d %H:%M'))} UTC
      </p>
    </div>

    <div style="background:#fff;padding:24px;border-radius:0 0 10px 10px">

      <table style="width:100%;border-collapse:separate;border-spacing:8px 0;margin-bottom:8px">
        <tr>
          {_stat_cell('المتابعون عبر المنصات', follower_total)}
          {_stat_cell('أخبار ورصد', len(week_news))}
          {_stat_cell('تعليقات', len(week_comments))}
          {_stat_cell('تم تحليلها', summary.comments_analyzed)}
        </tr>
      </table>

      <p style="text-align:center;margin:22px 0">
        <a href="{dashboard_url}"
           style="display:inline-block;background:#118a6e;color:#fff;text-decoration:none;
                  padding:13px 34px;border-radius:7px;font-weight:700;font-size:16px">
          عرض لوحة المعلومات التفاعلية
        </a>
      </p>
      <p style="text-align:center;margin:-12px 0 24px;font-size:12px;color:#7f8c8d">
        <a href="{dashboard_url}" style="color:#7f8c8d">{dashboard_url}</a>
      </p>

      <h2 style="{_H2}">متابعو حساباتنا</h2>
      <table style="{_TABLE}">
        <thead><tr>
          <th style="{_TH}">المنصة</th><th style="{_TH}">المتابعون</th>
          <th style="{_TH}">التغير عن الأسبوع الماضي</th>
        </tr></thead>
        <tbody>{follower_rows}</tbody>
      </table>

      <h2 style="{_H2}">تحليل مشاعر التعليقات (آخر {report_days} أيام)</h2>
      <table style="{_TABLE}">
        <thead><tr>
          <th style="{_TH}">التصنيف</th><th style="{_TH}">العدد</th><th style="{_TH}">النسبة</th>
        </tr></thead>
        <tbody>{sentiment_rows}</tbody>
      </table>

      <h2 style="{_H2}">تعليقات آخر {report_days} أيام</h2>
      <table style="{_TABLE}">
        <thead><tr>
          <th style="{_TH}">المنصة</th><th style="{_TH}">التعليق</th><th style="{_TH}">التصنيف</th>
        </tr></thead>
        <tbody>{comment_rows}</tbody>
      </table>

      <h2 style="{_H2}">تعليقات سلبية تحتاج مراجعة</h2>
      <table style="{_TABLE}">
        <thead><tr>
          <th style="{_TH}">المنصة</th><th style="{_TH}">التعليق</th><th style="{_TH}">السبب</th>
        </tr></thead>
        <tbody>{negative_rows}</tbody>
      </table>

      <h2 style="{_H2}">الأخبار ذات الصلة (آخر {report_days} أيام)</h2>
      <p style="margin:0 0 12px">{keyword_chips}</p>
      <table style="{_TABLE}">
        <thead><tr>
          <th style="{_TH}">العنوان</th><th style="{_TH}">المصدر</th><th style="{_TH}">التاريخ</th>
        </tr></thead>
        <tbody>{news_rows}</tbody>
      </table>
      {news_more}

      {errors_block}

      <hr style="border:0;border-top:1px solid #eceff1;margin:24px 0 14px">
      <p style="margin:0;font-size:12px;color:#95a5a6">
        تقرير آلي · التشغيل رقم {summary.run_id} ·
        استغرق {summary.duration_secs:.0f} ثانية ·
        البيانات مجمّعة من مصادر عامة دون استخدام حسابات إدارية.
      </p>
    </div>
  </div>
</body>
</html>"""

    def render_text(self, summary: RunSummary, follower_total: int | None = None) -> str:
        sentiment = " | ".join(
            f"{SENTIMENT_AR.get(k, k)}: {v}" for k, v in summary.sentiment_counts.items()
        ) or "لا توجد تعليقات محللة"
        total = summary.followers_collected if follower_total is None else follower_total
        return (
            "تقرير وسائل التواصل الاجتماعي - المكتب الاستراتيجي لتطوير منطقة الجوف\n"
            f"التشغيل رقم: {summary.run_id}\n"
            f"المتابعون عبر المنصات: {total:,} "
            f"(تعذر: {summary.followers_failed})\n"
            f"أخبار: {summary.news_collected}\n"
            f"تعليقات: {summary.comments_collected} "
            f"(تم تحليل {summary.comments_analyzed})\n"
            f"المشاعر: {sentiment}\n"
            f"تحذيرات: {len(summary.errors)}\n\n"
            f"لوحة المعلومات: {self.config.dashboard_url}\n"
        )

    # -- sending ---------------------------------------------------------- #
    def send(
        self, summary: RunSummary, html_body: str, follower_total: int | None = None
    ) -> list[str]:
        if not self.enabled:
            reason = (
                "SMTP credentials are incomplete"
                if not self.config.secrets.smtp_ready
                else "no recipients configured"
            )
            log.info("Email skipped (%s).", reason)
            return []

        secrets = self.config.secrets
        recipients = self.config.recipients

        message = EmailMessage()
        message["Subject"] = (
            f"{self.config.settings.email.subject_prefix} · "
            f"{datetime.now().strftime('%Y-%m-%d')}"
        )
        message["From"] = secrets.email_from or formataddr(
            ("JSDO Social Reporting", secrets.smtp_username or "")
        )
        message["To"] = ", ".join(recipients)
        message["Date"] = formatdate(localtime=True)
        message.set_content(self.render_text(summary, follower_total))
        message.add_alternative(html_body, subtype="html")

        try:
            if secrets.smtp_port == 465:
                server: smtplib.SMTP = smtplib.SMTP_SSL(
                    secrets.smtp_host, secrets.smtp_port, timeout=60
                )
            else:
                server = smtplib.SMTP(secrets.smtp_host, secrets.smtp_port, timeout=60)
            with server:
                server.ehlo()
                if secrets.smtp_use_tls and secrets.smtp_port != 465:
                    server.starttls()
                    server.ehlo()
                server.login(secrets.smtp_username, secrets.smtp_password)
                server.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailNotConfigured(f"Failed to send the weekly email: {exc}") from exc

        log.info("Email sent to %s", ", ".join(recipients))
        return recipients


# Inline styles: email clients strip <style> blocks, so every rule is inline.
_TABLE = (
    "width:100%;border-collapse:collapse;margin:0 0 20px;font-size:14px;direction:rtl"
)
_TH = (
    "text-align:right;padding:9px 10px;background:#f4f6f8;border-bottom:2px solid #dfe4e8;"
    "font-weight:700;color:#37474f"
)
_TD = "text-align:right;padding:9px 10px;border-bottom:1px solid #eceff1;color:#37474f"
_H2 = "font-size:16px;color:#0b5d4a;margin:22px 0 10px;padding-bottom:6px;border-bottom:2px solid #e8f5e9"


def _stat_cell(label: str, value: int) -> str:
    return (
        '<td style="background:#f4f8f7;border-radius:8px;padding:14px 8px;'
        'text-align:center;width:25%">'
        f'<div style="font-size:24px;font-weight:700;color:#0b5d4a">{value:,}</div>'
        f'<div style="font-size:12px;color:#607d8b;margin-top:2px">{_esc(label)}</div>'
        "</td>"
    )
