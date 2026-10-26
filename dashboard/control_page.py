"""Control page: keywords, mailing list, comparison accounts, and a new scrape."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from aljouf_report.control_store import (
    ControlStoreError,
    add_competitor,
    competitors,
    keywords,
    recipients,
    remove_competitor,
    save_keywords,
    save_recipients,
)
from theme import header

_SCOPE_TO_AR = {"local": "محلي", "national": "سعودي عام"}
_SCOPE_FROM_AR = {label: key for key, label in _SCOPE_TO_AR.items()}


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _log_path() -> Path:
    return _project_root() / "logs" / "control-scrape.log"


def _pid_path() -> Path:
    return _project_root() / "logs" / "control-scrape.pid"


def _code_path() -> Path:
    return _project_root() / "logs" / "control-scrape.code"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def scrape_status() -> dict:
    """Whether a scrape started from this page is still running."""
    pid_file = _pid_path()
    running = False
    pid = 0
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            pid = 0
        running = _pid_alive(pid)
    code = None
    if _code_path().exists() and not running:
        raw = _code_path().read_text(encoding="utf-8").strip()
        if raw.lstrip("-").isdigit():
            code = int(raw)
    tail = ""
    if _log_path().exists():
        text = _log_path().read_text(encoding="utf-8", errors="replace")
        tail = "\n".join(text.splitlines()[-12:])
    return {"running": running, "pid": pid, "code": code, "tail": tail}


def start_scrape() -> None:
    """Run one collection in the background. It does not send the email."""
    status = scrape_status()
    if status["running"]:
        raise ControlStoreError("هناك جمع يعمل الآن.")
    logs = _log_path().parent
    logs.mkdir(parents=True, exist_ok=True)
    _code_path().write_text("", encoding="utf-8")
    _log_path().write_text("", encoding="utf-8")
    root = _project_root()
    command = [
        sys.executable,
        "-c",
        (
            "import os, subprocess, sys\n"
            "from pathlib import Path\n"
            "root = Path(sys.argv[1])\n"
            "log = open(root / 'logs' / 'control-scrape.log', 'w', encoding='utf-8')\n"
            "env = os.environ.copy()\n"
            "env['PYTHONIOENCODING'] = 'utf-8'\n"
            "proc = subprocess.run(\n"
            "    [sys.executable, '-m', 'aljouf_report', 'run', '--skip-email'],\n"
            "    cwd=root, stdout=log, stderr=subprocess.STDOUT, env=env,\n"
            ")\n"
            "(root / 'logs' / 'control-scrape.code').write_text(str(proc.returncode), encoding='utf-8')\n"
        ),
        str(root),
    ]
    process = subprocess.Popen(
        command,
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _pid_path().write_text(str(process.pid), encoding="utf-8")


def _unlock() -> bool:
    password = os.environ.get("CONTROL_PASSWORD", "").strip()
    if not password:
        st.info(
            "صفحة التحكم تُفتح من لوحة البيانات على جهاز المكتب. "
            "أضف CONTROL_PASSWORD في ملف البيئة ثم أعد تشغيل اللوحة."
        )
        return False
    if st.session_state.get("control_unlocked"):
        return True
    with st.form("control_lock"):
        typed = st.text_input(
            "كلمة مرور التحكم",
            type="password",
            help="كلمة المرور المحلية لصفحة التحكم.",
        )
        submitted = st.form_submit_button("دخول", width="stretch")
    if submitted:
        if typed == password:
            st.session_state["control_unlocked"] = True
            st.rerun()
        st.error("كلمة المرور غير صحيحة.")
    return False


def _save_notice() -> None:
    st.cache_data.clear()
    st.cache_resource.clear()


def _keywords_tab() -> None:
    current = keywords()
    frame = pd.DataFrame(
        [
            {
                "الكلمة": item.get("query", ""),
                "الاسم": item.get("label") or item.get("query", ""),
                "النوع": _SCOPE_TO_AR.get(item.get("scope") or "local", "محلي"),
                "يشترط ذكر الجوف": bool(item.get("require_region")),
            }
            for item in current
        ]
    )
    st.caption("هذه الكلمات هي ما يبحث عنه جمع الأخبار. احذف صفاً أو أضف صفاً ثم احفظ.")
    edited = st.data_editor(
        frame,
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "الكلمة": st.column_config.TextColumn(
                "الكلمة",
                required=True,
                help="ما يبحث عنه جمع الأخبار، مثل سكاكا أو Al Jouf.",
            ),
            "الاسم": st.column_config.TextColumn(
                "الاسم في التقرير",
                help="الاسم الذي يظهر بجانب الخبر. إن تركته فارغاً تُستخدم الكلمة نفسها.",
            ),
            "النوع": st.column_config.SelectboxColumn(
                "النوع",
                options=["محلي", "سعودي عام"],
                required=True,
                help="محلي لأخبار الجوف. سعودي عام لأخبار السعودية.",
            ),
            "يشترط ذكر الجوف": st.column_config.CheckboxColumn(
                "يشترط ذكر الجوف",
                help="لأسماء الأماكن العامة التي قد تطابق بلداً آخر.",
            ),
        },
        key="keyword_editor",
    )
    if st.button("حفظ الكلمات", type="primary", key="save_keywords"):
        rows = []
        for _, row in edited.iterrows():
            raw_query = row.get("الكلمة")
            if pd.isna(raw_query):
                continue
            query = str(raw_query).strip()
            if not query:
                continue
            raw_label = row.get("الاسم")
            label = query if pd.isna(raw_label) else str(raw_label).strip() or query
            raw_scope = row.get("النوع")
            scope_label = "محلي" if pd.isna(raw_scope) else str(raw_scope)
            raw_region = row.get("يشترط ذكر الجوف")
            rows.append(
                {
                    "query": query,
                    "label": label,
                    "scope": _SCOPE_FROM_AR.get(scope_label, "local"),
                    "require_region": False if pd.isna(raw_region) else bool(raw_region),
                }
            )
        try:
            saved = save_keywords(rows)
        except ControlStoreError as exc:
            st.error(str(exc))
        else:
            _save_notice()
            st.success(f"حُفظت {len(saved)} كلمة. الجمع القادم يستخدمها.")


def _mail_tab() -> None:
    current = recipients()
    st.caption("كل سطر بريد واحد. التقرير المجدول يُرسل إلى هذه القائمة.")
    text = st.text_area(
        "البريد",
        value="\n".join(current),
        height=220,
        help="بريد واحد في كل سطر. التقرير يُرسل إلى هذه العناوين.",
    )
    if st.button("حفظ قائمة البريد", type="primary", key="save_mail"):
        try:
            saved = save_recipients(text.splitlines())
        except ControlStoreError as exc:
            st.error(str(exc))
        else:
            _save_notice()
            if saved:
                st.success("القائمة المحفوظة: " + "، ".join(saved))
            else:
                st.warning("القائمة فارغة. لن يُرسل التقرير حتى تضيف بريداً.")


def _accounts_tab() -> None:
    rows = competitors()
    st.caption(
        "هذه الجهات تظهر في رسوم المقارنة. حذف جهة يخفيها من الرسم فوراً. "
        "الجهة الجديدة تظهر بعد إعادة الجمع. واتساب وسناب شات للجوف فقط ولا يُضيفان هنا."
    )
    if not rows:
        st.info("لا توجد جهات مقارنة.")
    for row in rows:
        handles = " · ".join(
            f"{name}: {handle}" for name, handle in row["platforms"].items() if handle
        )
        left, right = st.columns([5, 1])
        left.markdown(f"**{row['short_name_ar']}**")
        left.caption(handles or "لا توجد حسابات")
        if right.button("حذف", key=f"drop_{row['key']}", width="stretch"):
            try:
                remove_competitor(row["key"])
            except ControlStoreError as exc:
                st.error(str(exc))
            else:
                _save_notice()
                st.rerun()

    st.divider()
    st.markdown("##### إضافة جهة")
    with st.form("add_competitor"):
        key = st.text_input(
            "الرمز بالانقليزي",
            help="أحرف إنجليزية صغيرة، مثل najran. لا يظهر في الرسم.",
        )
        short_name = st.text_input(
            "الاسم في الرسم",
            help="الاسم المختصر على الرسم، مثل نجران.",
        )
        full_name = st.text_input(
            "الاسم الكامل",
            help="الاسم الرسمي للجهة، مثل هيئة تطوير منطقة نجران.",
        )
        english_name = st.text_input(
            "الاسم بالإنجليزية",
            help="الاسم الإنجليزي للجهة. يُحفظ في السجل ولا يظهر في الرسم.",
        )
        instagram = st.text_input(
            "إنستغرام",
            help="اسم الحساب فقط، مثل najran_office. اتركه فارغاً إن لم يوجد حساب.",
        )
        x_handle = st.text_input(
            "إكس",
            help="اسم الحساب فقط، مثل NajranOffice. اتركه فارغاً إن لم يوجد حساب.",
        )
        youtube = st.text_input(
            "يوتيوب",
            help="اسم القناة، مثل @NajranOffice. اتركه فارغاً إن لم توجد قناة.",
        )
        linkedin = st.text_input(
            "لينكدإن",
            help="اسم الشركة في رابط لينكدإن، مثل najran-office. اتركه فارغاً إن لم يوجد.",
        )
        submitted = st.form_submit_button("إضافة الجهة", type="primary")
    if submitted:
        try:
            add_competitor(
                key=key,
                short_name_ar=short_name,
                name_ar=full_name,
                name_en=english_name,
                handles={
                    "instagram": instagram,
                    "x": x_handle,
                    "youtube": youtube,
                    "linkedin": linkedin,
                },
            )
        except ControlStoreError as exc:
            st.error(str(exc))
        else:
            _save_notice()
            st.success(f"أُضيفت {short_name.strip()}. أعد الجمع لتظهر أرقامها في الرسم.")
            st.rerun()


def _scrape_tab() -> None:
    st.caption(
        "يعيد جمع المتابعين والأخبار والتعليقات للإعدادات المحفوظة. "
        "يستغرق نحو عشر دقائق ويستخدم رصيد Apify. لا يرسل التقرير بالبريد."
    )
    status = scrape_status()
    if status["running"]:
        st.info("الجمع يعمل الآن. أبقِ هذه الصفحة وانتظر حتى تكتمل.")
        if status["tail"]:
            st.code(status["tail"])
        if st.button("تحديث الحالة", key="refresh_scrape"):
            st.rerun()
        return
    if status["code"] == 0:
        st.success("اكتمل آخر جمع. حدّث صفحة التقرير لترى الأرقام الجديدة.")
    elif status["code"] not in (None, 0) and status["code"] != "":
        st.error("توقف آخر جمع قبل أن يكتمل. التفاصيل في الأسفل.")
    if status["tail"] and status["code"] is not None:
        with st.expander("سجل آخر جمع"):
            st.code(status["tail"])
    if st.button("إعادة الجمع الآن", type="primary", key="ask_scrape"):
        st.session_state["confirm_scrape"] = True
    if st.session_state.get("confirm_scrape"):
        st.warning("سيبدأ جمعاً جديداً الآن ولن يرسل البريد.")
        if st.button("ابدأ الجمع", key="confirm_scrape_go"):
            try:
                start_scrape()
            except ControlStoreError as exc:
                st.error(str(exc))
            else:
                st.session_state["confirm_scrape"] = False
                st.rerun()


def render_control() -> None:
    header(
        "التحكم",
        "المكتب الاستراتيجي لتطوير منطقة الجوف",
        "الكلمات، البريد، وجهات المقارنة",
    )
    if not _unlock():
        return
    words, mail, offices, scrape = st.tabs(
        ["كلمات البحث", "البريد", "جهات المقارنة", "إعادة الجمع"]
    )
    with words:
        _keywords_tab()
    with mail:
        _mail_tab()
    with offices:
        _accounts_tab()
    with scrape:
        _scrape_tab()
