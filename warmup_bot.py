 import os, json, random, math
from datetime import datetime, date
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ================== SETTINGS ==================
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
JSON_FILE = "gcp-key.json"
WARMUP_SHEET = "Warmup Accounts"
STATE_FILE = "warmup_state.json"

ZOHO_EMAIL = os.environ.get("ZOHO_EMAIL", "contact@dualwin.agency")
ZOHO_PASSWORD = os.environ.get("ZOHO_PASSWORD")  # GitHub Secret
SMTP_SERVER = "smtp.zoho.com"
SMTP_PORT = 587

# Morocco is UTC+0 (you said UTC0). GitHub runner uses UTC time.
WORK_START_HOUR_UTC = 13
WORK_END_HOUR_UTC = 19  # end is exclusive (13:00 to 18:59)

# GitHub schedule interval (minutes) — must match cron below
RUN_INTERVAL_MIN = 5

TOTAL_DAYS = 25

# Your daily plan (safe with 4 inboxes)
DAILY_GOALS = {
    1: 5,  2: 5,  3: 6,  4: 6,  5: 7,
    6: 7,  7: 8,  8: 8,  9: 9,  10: 9,
    11: 10, 12: 10, 13: 12, 14: 12, 15: 14,
    16: 14, 17: 16, 18: 16, 19: 18, 20: 18,
    21: 20, 22: 20, 23: 20, 24: 20, 25: 20
}

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "DualWin_Agency")

MESSAGES = [
    "هل وصلك هذا الإيميل؟",
    "تجربة سريعة لنظام الإرسال.",
    "يرجى تجاهل هذه الرسالة، مجرد اختبار.",
    "تأكيد وصول البريد.",
    "اختبار بسيط لوصول الرسائل.",
    "هل يظهر هذا الإيميل في الوارد لديك؟"
]
SUBJECTS = ["اختبار", "تحقق سريع", "تأكيد الاستلام", "رسالة اختبار"]

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
        "start_date": today,         # day 1 starts today
        "last_date": today,          # used to reset sent_today each day
        "sent_today": 0,
        "carryover": 0,              # leftover from yesterday if not completed
        "total_sent": 0,
        "completed": False,
        "last_day_finished": 0       # last day we sent "done" notification for
    }

def current_day_number(state):
    start = date.fromisoformat(state["start_date"])
    return (date.today() - start).days + 1

def is_within_work_hours(now_utc: datetime) -> bool:
    return WORK_START_HOUR_UTC <= now_utc.hour < WORK_END_HOUR_UTC

def reset_day_if_needed(state):
    today = date.today().isoformat()
    if state.get("last_date") != today:
        # new day: carryover from yesterday if not completed
        # yesterday target = DAILY_GOALS[yesterday] + carryover (old carryover)
        # remaining = target - sent_today
        # new carryover = max(0, remaining)
        yday = current_day_number(state) - 1
        if 1 <= yday <= TOTAL_DAYS:
            y_target = DAILY_GOALS[yday] + int(state.get("carryover", 0))
            remaining = max(0, y_target - int(state.get("sent_today", 0)))
            state["carryover"] = remaining
        else:
            state["carryover"] = 0

        state["sent_today"] = 0
        state["last_date"] = today

# ================== GOOGLE SHEETS ==================
def connect_sheet():
    creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, SCOPE)
    client = gspread.authorize(creds)
    return client.open(WARMUP_SHEET).sheet1

def get_emails(sheet):
    emails = sheet.col_values(1)
    valid = [e.strip() for e in emails if e and "@" in e]
    return valid

# ================== EMAIL ==================
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
            timeout=10
        )
    except:
        pass

# ================== BATCH LOGIC (NO SLEEP) ==================
def runs_left_today(now_utc: datetime) -> int:
    end = now_utc.replace(hour=WORK_END_HOUR_UTC, minute=0, second=0, microsecond=0)
    seconds_left = max(0, int((end - now_utc).total_seconds()))
    return max(1, (seconds_left // (RUN_INTERVAL_MIN * 60)) + 1)

def compute_batch(remaining_today: int, now_utc: datetime) -> int:
    # Distribute remaining over remaining runs, cap to keep it natural and fast
    rl = runs_left_today(now_utc)
    b = math.ceil(remaining_today / rl)
    b = max(1, min(b, 5, remaining_today))
    return b

def main():
    if not ZOHO_PASSWORD:
        print("Missing ZOHO_PASSWORD")
        return

    state = load_state() or init_state()
    reset_day_if_needed(state)

    day = current_day_number(state)
    if day > TOTAL_DAYS:
        state["completed"] = True
        save_state(state)
        ntfy("🎉 اكتملت مرحلة التسخين (25 يوم).", title="Warmup Done", tags="tada")
        print("DONE")
        return

    now_utc = datetime.utcnow()
    if not is_within_work_hours(now_utc):
        # IMPORTANT: exit fast (no waiting) — GitHub will run again on schedule
        save_state(state)
        print("Outside work hours, exiting.")
        return

    # today target (with carryover)
    target_today = DAILY_GOALS[day] + int(state.get("carryover", 0))
    sent_today = int(state.get("sent_today", 0))
    remaining_today = target_today - sent_today

    if remaining_today <= 0:
        # If day already completed, send done notification once
        if int(state.get("last_day_finished", 0)) != day:
            ntfy(
                f"✅ Day {day} completed\nSent today: {target_today}\nTotal sent: {state.get('total_sent', 0)}",
                title="Daily Summary (done)",
                tags="white_check_mark"
            )
            state["last_day_finished"] = day
            state["carryover"] = 0
            save_state(state)
        print("Day already completed.")
        return

    # read recipients
    sheet = connect_sheet()
    emails = get_emails(sheet)
    if not emails:
        ntfy("❌ لا توجد إيميلات في Google Sheet (عمود A).", title="Warmup Error", tags="x")
        print("No emails.")
        return

    # send a small batch now
    batch = compute_batch(remaining_today, now_utc)

    sent_now = 0
    for _ in range(batch):
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
            print(f"Send error: {e}")
            break

    # if we finished today after this batch → ntfy once
    remaining_after = target_today - int(state.get("sent_today", 0))
    if remaining_after <= 0 and int(state.get("last_day_finished", 0)) != day:
        ntfy(
            f"✅ Day {day} completed\nSent today: {target_today}\nTotal sent: {state.get('total_sent', 0)}",
            title="Daily Summary (done)",
            tags="white_check_mark"
        )
        state["last_day_finished"] = day
        state["carryover"] = 0
        save_state(state)

    print(f"OK day={day} target_today={target_today} sent_now={sent_now} sent_today={state['sent_today']} total={state['total_sent']}")

if __name__ == "__main__":
    main()
