import os
import json
import random
import math
from datetime import datetime, date
from zoneinfo import ZoneInfo

import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ================== CONFIG ==================
TZ = ZoneInfo("Africa/Casablanca")  # المغرب
WORK_START = 13
WORK_END = 19  # exclusive

RUN_INTERVAL_MIN = 10  # must match cron

SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
JSON_FILE = "gcp-key.json"
WARMUP_SHEET = "Warmup Accounts"
STATE_FILE = "warmup_state.json"

ZOHO_EMAIL = os.environ.get("ZOHO_EMAIL", "contact@dualwin.agency")
ZOHO_PASSWORD = os.environ.get("ZOHO_PASSWORD", "")
SMTP_SERVER = "smtp.zoho.com"
SMTP_PORT = 587

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "DualWin_Agency")

TOTAL_DAYS = 25

# خطة مناسبة لـ 4 حسابات عندك (آمنة + واقعية)
DAILY_GOALS = {
    1: 5,  2: 5,  3: 6,  4: 6,  5: 7,
    6: 7,  7: 8,  8: 8,  9: 9,  10: 9,
    11: 10, 12: 10, 13: 12, 14: 12, 15: 14,
    16: 14, 17: 16, 18: 16, 19: 18, 20: 18,
    21: 20, 22: 20, 23: 20, 24: 20, 25: 20
}

SUBJECTS = ["اختبار", "تحقق سريع", "تأكيد الاستلام", "رسالة اختبار"]
MESSAGES = [
    "هل وصلك هذا الإيميل؟",
    "تجربة سريعة لنظام الإرسال.",
    "يرجى تجاهل هذه الرسالة، مجرد اختبار.",
    "تأكيد وصول البريد.",
    "اختبار بسيط لوصول الرسائل.",
    "هل يظهر هذا الإيميل في الوارد لديك؟",
]

# ================== STATE ==================
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return None
    return None

def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)

def init_state():
    today = date.today().isoformat()
    return {
        "start_date": today,        # بداية day1
        "last_date": today,         # لتصفير sent_today يومياً
        "sent_today": 0,
        "carryover": 0,             # المتبقي من أمس
        "total_sent": 0,
        "last_day_finished": 0,     # لمنع تكرار إشعار “Day completed”
        "completed": False
    }

def migrate_old_state(state):
    # إذا كان عندك state قديم من النسخة الأولى
    if not state or "start_date" not in state:
        return init_state()
    for k, v in init_state().items():
        state.setdefault(k, v)
    return state

def day_number(state):
    start = date.fromisoformat(state["start_date"])
    return (date.today() - start).days + 1

def in_work_hours(now):
    return WORK_START <= now.hour < WORK_END

def reset_daily_if_needed(state):
    today = date.today().isoformat()
    if state.get("last_date") != today:
        # حساب carryover من أمس
        yday = day_number(state) - 1
        if 1 <= yday <= TOTAL_DAYS:
            y_target = DAILY_GOALS[yday] + int(state.get("carryover", 0))
            remaining = max(0, y_target - int(state.get("sent_today", 0)))
            state["carryover"] = remaining
        else:
            state["carryover"] = 0

        state["sent_today"] = 0
        state["last_date"] = today

# ================== SHEETS ==================
def connect_sheet():
    creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, SCOPE)
    client = gspread.authorize(creds)
    return client.open(WARMUP_SHEET).sheet1

def get_emails(sheet):
    emails = sheet.col_values(1)
    return [e.strip() for e in emails if e and "@" in e]

# ================== SMTP ==================
def send_email(to_addr, subject, body):
    msg = MIMEMultipart()
    msg["From"] = ZOHO_EMAIL
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
    server.starttls()
    server.login(ZOHO_EMAIL, ZOHO_PASSWORD)
    server.send_message(msg)
    server.quit()

# ================== NTFY ==================
def ntfy(text, title="Warmup Bot", tags="memo"):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=text.encode("utf-8"),
            headers={"Title": title, "Priority": "default", "Tags": tags},
            timeout=10,
        )
    except:
        pass

# ================== BATCH LOGIC (NO SLEEP) ==================
def runs_left_today(now):
    end = now.replace(hour=WORK_END, minute=0, second=0, microsecond=0)
    sec_left = max(0, int((end - now).total_seconds()))
    return max(1, (sec_left // (RUN_INTERVAL_MIN * 60)) + 1)

def batch_size(remaining, now):
    # توزيع الباقي على عدد التشغيلات المتبقية اليوم
    rl = runs_left_today(now)
    b = math.ceil(remaining / rl)
    # نخليها طبيعية وسريعة (GitHub)
    b = max(1, min(b, 2, remaining))  # أقصى 2 في كل Run
    return b

def main():
    if not ZOHO_PASSWORD:
        print("ERROR: Missing ZOHO_PASSWORD")
        return

    state = migrate_old_state(load_state())
    reset_daily_if_needed(state)

    now = datetime.now(TZ)
    d = day_number(state)

    if d > TOTAL_DAYS:
        state["completed"] = True
        save_state(state)
        ntfy("🎉 اكتملت مرحلة التسخين (25 يوم).", title="Warmup Done", tags="tada")
        print("DONE")
        return

    if not in_work_hours(now):
        # مهم: نخرج بسرعة. GitHub هو اللي يرجع يشغل حسب الجدولة.
        save_state(state)
        print("Outside work hours, exiting.")
        return

    # هدف اليوم مع carryover
    target_today = DAILY_GOALS[d] + int(state.get("carryover", 0))
    sent_today = int(state.get("sent_today", 0))
    remaining = target_today - sent_today

    if remaining <= 0:
        # إشعار اكتمال اليوم مرة واحدة فقط
        if int(state.get("last_day_finished", 0)) != d:
            ntfy(
                f"✅ اليوم {d} اكتمل.\nأُرسل اليوم: {target_today}\nالإجمالي: {state.get('total_sent', 0)}",
                title="Daily Summary (done)",
                tags="white_check_mark",
            )
            state["last_day_finished"] = d
            state["carryover"] = 0
            save_state(state)
        print("Day already completed.")
        return

    # قراءة الشيت
    try:
        sheet = connect_sheet()
        emails = get_emails(sheet)
    except Exception as e:
        ntfy(f"❌ خطأ في Google Sheet: {e}", title="Warmup Error", tags="x")
        print(f"Sheet error: {e}")
        return

    if not emails:
        ntfy("❌ لا توجد إيميلات في الشيت (عمود A).", title="Warmup Error", tags="x")
        print("No emails in sheet.")
        return

    # إرسال دفعة صغيرة
    b = batch_size(remaining, now)
    sent_now = 0

    for _ in range(b):
        to_addr = random.choice(emails)
        subject = random.choice(SUBJECTS)
        body = random.choice(MESSAGES)
        try:
            send_email(to_addr, subject, body)
            sent_now += 1
            state["sent_today"] = int(state.get("sent_today", 0)) + 1
            state["total_sent"] = int(state.get("total_sent", 0)) + 1
            save_state(state)
        except Exception as e:
            ntfy(f"❌ خطأ SMTP: {e}", title="Warmup SMTP Error", tags="x")
            print(f"SMTP error: {e}")
            break

    # إذا كمل اليوم بعد هاد الدفعة
    remaining_after = target_today - int(state.get("sent_today", 0))
    if remaining_after <= 0 and int(state.get("last_day_finished", 0)) != d:
        ntfy(
            f"✅ اليوم {d} اكتمل.\nأُرسل اليوم: {target_today}\nالإجمالي: {state.get('total_sent', 0)}",
            title="Daily Summary (done)",
            tags="white_check_mark",
        )
        state["last_day_finished"] = d
        state["carryover"] = 0
        save_state(state)

    print(f"OK day={d} target={target_today} sent_now={sent_now} sent_today={state['sent_today']} total={state['total_sent']}")

if __name__ == "__main__":
    main()
