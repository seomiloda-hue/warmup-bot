import gspread
from oauth2client.service_account import ServiceAccountCredentials
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import random
import json
from datetime import datetime, timedelta, date
import os
import math
import requests

# ================== إعدادات ==================
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
JSON_FILE = "gcp-key.json"
WARMUP_SHEET = "Warmup Accounts"
STATE_FILE = "warmup_state.json"

ZOHO_EMAIL = os.environ.get("ZOHO_EMAIL", "contact@dualwin.agency")
SMTP_SERVER = "smtp.zoho.com"
SMTP_PORT = 587

# وقت العمل (بالـ UTC داخل GitHub Actions)
# المغرب غالباً UTC أو UTC+1 حسب التوقيت. نحن نخدم بالـ UTC لتفادي المشاكل.
WORK_START_UTC = 12   # 12:00 UTC ≈ 13:00 المغرب (إذا كان UTC+1)
WORK_END_UTC   = 18   # 18:00 UTC ≈ 19:00 المغرب

# interval ديال GitHub cron (بالدقائق) — خليها 5 لأننا سنشغل كل 5 دقائق
RUN_INTERVAL_MIN = 5

# خطة 25 يوم (خفيفة بما يناسب 4 inboxes عندك)
DAILY_GOALS = {
    1: 5,  2: 5,  3: 6,  4: 6,  5: 7,
    6: 7,  7: 8,  8: 8,  9: 9,  10: 9,
    11: 10, 12: 10, 13: 12, 14: 12, 15: 14,
    16: 14, 17: 16, 18: 16, 19: 18, 20: 18,
    21: 20, 22: 20, 23: 20, 24: 20, 25: 20
}
TOTAL_DAYS = 25

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "DualWin_Agency")

MESSAGES = [
    "هل وصلك هذا الإيميل؟",
    "تجربة سريعة لنظام الإرسال.",
    "يرجى تجاهل هذه الرسالة، مجرد اختبار.",
    "تأكيد وصول البريد.",
    "اختبار بسيط لوصول الرسائل.",
    "هل يظهر هذا الإيميل في الوارد لديك؟"
]

SUBJECTS = [
    "اختبار", "تحقق سريع", "تأكيد الاستلام", "رسالة اختبار"
]

# ================== State ==================
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
        "start_date": today,          # بداية البرنامج (لتحويلها إلى Day 1..25 تلقائياً)
        "last_date": today,           # آخر تاريخ اشتغل عليه
        "sent_today": 0,              # كم رسالة رسلنا اليوم
        "total_sent": 0,              # إجمالي الرسائل
        "completed": False
    }

def calc_day_number(state):
    start = date.fromisoformat(state["start_date"])
    delta_days = (date.today() - start).days
    return delta_days + 1

def reset_daily_if_needed(state):
    today = date.today().isoformat()
    if state.get("last_date") != today:
        state["last_date"] = today
        state["sent_today"] = 0

# ================== Sheets ==================
def connect_sheet():
    creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, SCOPE)
    client = gspread.authorize(creds)
    return client.open(WARMUP_SHEET).sheet1

def get_emails(sheet):
    emails = sheet.col_values(1)
    valid = [e.strip() for e in emails if e and "@" in e]
    return valid

# ================== Email ==================
def send_email(to, subject, body, zoho_password):
    msg = MIMEMultipart()
    msg["From"] = ZOHO_EMAIL
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
    server.starttls()
    server.login(ZOHO_EMAIL, zoho_password)
    server.send_message(msg)
    server.quit()
    return True

# ================== ntfy ==================
def send_ntfy(text, title="Warmup Bot", tags="memo"):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=text.encode("utf-8"),
            headers={"Title": title, "Priority": "default", "Tags": tags},
            timeout=10
        )
    except:
        pass

# ================== Logic: one short batch per run ==================
def within_work_hours_utc(now_utc: datetime) -> bool:
    return WORK_START_UTC <= now_utc.hour < WORK_END_UTC

def build_targets(emails, n):
    targets = []
    for i in range(n):
        targets.append(emails[i % len(emails)])
    random.shuffle(targets)
    return targets

def compute_batch_size(now_utc: datetime, remaining_today: int) -> int:
    """
    نقسم المتبقي على عدد التشغيلات المتبقية اليوم.
    هذا يضمن أننا نكمل هدف اليوم بدون ما نعلق ساعات.
    """
    end_today = now_utc.replace(hour=WORK_END_UTC, minute=0, second=0, microsecond=0)
    seconds_left = max(0, int((end_today - now_utc).total_seconds()))
    runs_left = max(1, (seconds_left // (RUN_INTERVAL_MIN * 60)) + 1)
    batch = math.ceil(remaining_today / runs_left)

    # حد أقصى صغير حتى يبقى طبيعي وما يتجاوز وقت GitHub
    batch = max(1, min(batch, 5, remaining_today))
    return batch

def main():
    zoho_password = os.environ.get("ZOHO_PASSWORD")
    if not zoho_password:
        print("❌ Missing ZOHO_PASSWORD secret.")
        return

    # 1) Load state
    state = load_state()
    if not state:
        state = init_state()
        save_state(state)

    reset_daily_if_needed(state)

    # 2) Determine day number
    day = calc_day_number(state)
    if day > TOTAL_DAYS:
        state["completed"] = True
        save_state(state)
        send_ntfy("🎉 اكتملت مدة التسخين (25 يوم).", title="Warmup Done", tags="tada")
        print("DONE")
        return

    # 3) Work hours check (UTC)
    now_utc = datetime.utcnow()
    if not within_work_hours_utc(now_utc):
        # خارج الوقت: نخرج فوراً (لا ننتظر داخل السكربت أبداً)
        print(f"Outside work hours (UTC). Now={now_utc.strftime('%H:%M')}. Exiting.")
        return

    # 4) Daily goal
    goal_today = DAILY_GOALS[day]
    remaining_today = goal_today - int(state.get("sent_today", 0))
    if remaining_today <= 0:
        # اليوم كمل: أرسل إشعار مرة وحدة فقط (اختياري)
        print(f"Day {day} already completed. sent_today={state['sent_today']}/{goal_today}")
        return

    # 5) Read sheet
    try:
        sheet = connect_sheet()
        emails = get_emails(sheet)
    except Exception as e:
        print(f"Sheet error: {e}")
        return

    if not emails:
        print("No emails found in sheet.")
        return

    # 6) Compute batch size for this run
    batch_size = compute_batch_size(now_utc, remaining_today)

    # 7) Send batch
    targets = build_targets(emails, batch_size)
    sent_now = 0
    for to in targets:
        subject = random.choice(SUBJECTS)
        body = random.choice(MESSAGES)
        try:
            send_email(to, subject, body, zoho_password)
            sent_now += 1
            state["sent_today"] = int(state.get("sent_today", 0)) + 1
            state["total_sent"] = int(state.get("total_sent", 0)) + 1
            save_state(state)
        except Exception as e:
            print(f"Send error: {e}")
            break

    # 8) ntfy (نرسل إشعار فقط إذا كمل اليوم)
    remaining_after = goal_today - int(state.get("sent_today", 0))
    if remaining_after <= 0:
        send_ntfy(
            f"✅ Day {day} completed\nSent today: {goal_today}\nTotal sent: {state['total_sent']}",
            title="Daily Summary (done)",
            tags="white_check_mark"
        )

    print(f"OK. Day={day} goal={goal_today} sent_now={sent_now} sent_today={state['sent_today']} total={state['total_sent']}")

if __name__ == "__main__":
    main()
