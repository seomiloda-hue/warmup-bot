import gspread
from oauth2client.service_account import ServiceAccountCredentials
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time
import random
import json
from datetime import datetime, timedelta
import os
import requests

# ================== إعدادات ==================
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
JSON_FILE = "gcp-key.json"               # خليه هكذا
WARMUP_SHEET = "Warmup Accounts"
STATE_FILE = "warmup_state.json"

ZOHO_EMAIL = os.environ.get("ZOHO_EMAIL", "contact@dualwin.agency")
SMTP_SERVER = "smtp.zoho.com"
SMTP_PORT = 587

WORK_START = 13
WORK_END = 19

# خطة بسيطة مناسبة لك (4 إيميلات/اليوم للعملاء)
# إذا بغيتي نرفعها تدريجياً من بعد، نقدر نبدلها لاحقاً
DAILY_GOALS = {
    1: 5,  2: 5,  3: 6,  4: 6,  5: 7,
    6: 7,  7: 8,  8: 8,  9: 9,  10: 9,
    11: 10, 12: 10, 13: 12, 14: 12, 15: 14,
    16: 14, 17: 16, 18: 16, 19: 18, 20: 18,
    21: 20, 22: 20, 23: 20, 24: 20, 25: 20
}
TOTAL_DAYS = 25

MIN_DELAY = 2 * 60
MAX_DELAY = 7 * 60

NTFY_TOPIC = "DualWin_Agency"

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
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return None
    return None

def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)

def init_state():
    return {
        "current_day": 1,
        "total_sent": 0,
        "remaining": 0,
        "last_index": 0,
        "completed": False
    }

# ================== Sheets ==================
def connect_sheet():
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, SCOPE)
        client = gspread.authorize(creds)
        sheet = client.open(WARMUP_SHEET).sheet1
        print("✅ Connected to Google Sheet")
        return sheet
    except Exception as e:
        print(f"❌ Sheet connect error: {e}")
        return None

def get_emails(sheet):
    try:
        emails = sheet.col_values(1)
        valid = [e.strip() for e in emails if e and '@' in e]
        print(f"📧 Found {len(valid)} emails")
        return valid
    except Exception as e:
        print(f"❌ Read emails error: {e}")
        return []

# ================== Email ==================
def send_email(to, subject, body, zoho_password):
    try:
        msg = MIMEMultipart()
        msg['From'] = ZOHO_EMAIL
        msg['To'] = to
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
        server.starttls()
        server.login(ZOHO_EMAIL, zoho_password)
        server.send_message(msg)
        server.quit()
        print(f"   ✅ Sent to {to}")
        return True
    except Exception as e:
        print(f"   ❌ Send error to {to}: {e}")
        return False

# ================== ntfy ==================
def send_ntfy(text, title="Warmup Bot", tags="memo"):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=text.encode('utf-8'),
            headers={"Title": title, "Priority": "default", "Tags": tags},
            timeout=10
        )
        print("📨 ntfy sent")
    except Exception as e:
        print(f"⚠️ ntfy failed: {e}")

# ================== Day ==================
def build_target_list(emails, total_needed):
    targets = []
    for i in range(total_needed):
        targets.append(emails[i % len(emails)])
    random.shuffle(targets)
    return targets

def process_day(sheet, state, zoho_password):
    day = state["current_day"]
    if day > TOTAL_DAYS:
        state["completed"] = True
        save_state(state)
        print("🎉 Completed all days!")
        send_ntfy("🎉 Warmup completed!", title="Warmup Done", tags="tada")
        return False

    base_goal = DAILY_GOALS[day]
    total_goal = base_goal + state["remaining"]

    print("\n" + "="*50)
    print(f"🔥 DAY {day}/{TOTAL_DAYS}")
    print(f"🎯 Target today: {total_goal} (base {base_goal} + remaining {state['remaining']})")
    print("="*50)

    emails = get_emails(sheet)
    if not emails:
        print("❌ No emails in sheet. Waiting 10 min...")
        time.sleep(600)
        return True

    targets = build_target_list(emails, total_goal)
    start_idx = state["last_index"]
    sent_today = 0

    for i in range(start_idx, len(targets)):
        now = datetime.now()
        if now.hour < WORK_START or now.hour >= WORK_END:
            state["last_index"] = i
            state["remaining"] = total_goal - sent_today
            save_state(state)
            send_ntfy(
                f"⏸️ Day {day} stopped (work hours ended).\nSent: {sent_today}\nRemaining: {state['remaining']}\nTotal: {state['total_sent']}",
                title="Daily Summary (paused)",
                tags="warning"
            )
            return True

        to = targets[i]
        subject = random.choice(SUBJECTS)
        body = random.choice(MESSAGES)
        print(f"\n📨 Sending to {to} ...")

        success = send_email(to, subject, body, zoho_password)
        if success:
            sent_today += 1
            state["total_sent"] += 1

        state["last_index"] = i + 1
        state["remaining"] = total_goal - sent_today
        save_state(state)

        if i < len(targets) - 1:
            delay = random.randint(MIN_DELAY, MAX_DELAY)
            print(f"⏳ Waiting {delay//60}m {delay%60}s...")
            end_sleep = datetime.now() + timedelta(seconds=delay)
            while datetime.now() < end_sleep:
                time.sleep(1)
                if datetime.now().hour >= WORK_END:
                    state["remaining"] = total_goal - sent_today
                    save_state(state)
                    send_ntfy(
                        f"⏸️ Day {day} stopped during wait.\nSent: {sent_today}\nRemaining: {state['remaining']}\nTotal: {state['total_sent']}",
                        title="Daily Summary (paused)",
                        tags="warning"
                    )
                    return True

    print(f"\n✅ Day {day} completed! Sent {total_goal}.")
    send_ntfy(
        f"✅ Day {day} completed.\nSent today: {total_goal}\nTotal sent: {state['total_sent']}",
        title="Daily Summary (done)",
        tags="white_check_mark"
    )

    state["current_day"] = day + 1
    state["last_index"] = 0
    state["remaining"] = 0
    save_state(state)
    return True

# ================== Main ==================
def main():
    print("\n" + "="*60)
    print("🚀 WARMUP BOT (simple + stable)")
    print("="*60)

    zoho_password = os.environ.get("ZOHO_PASSWORD")
    if not zoho_password:
        print("❌ Missing ZOHO_PASSWORD environment variable.")
        print("Stop now. We'll set it in the next step.")
        return

    state = load_state()
    if not state:
        state = init_state()
        save_state(state)
        print("📝 New state created.")
    else:
        print(f"📝 Resuming from day {state['current_day']}")

    sheet = None
    while sheet is None:
        sheet = connect_sheet()
        if sheet is None:
            print("Retrying in 10 min...")
            time.sleep(600)

    while not state["completed"]:
        now = datetime.now()
        if now.hour < WORK_START or now.hour >= WORK_END:
            next_start = now.replace(hour=WORK_START, minute=0, second=0, microsecond=0)
            if now.hour >= WORK_END:
                next_start += timedelta(days=1)

            print(f"😴 Outside work hours. Next start: {next_start.strftime('%Y-%m-%d %H:%M')}")
            # Sleep in small chunks (more stable)
            while datetime.now() < next_start:
                time.sleep(60)
            continue

        process_day(sheet, state, zoho_password)

        # reload state (in case)
        state = load_state() or state

if __name__ == "__main__":
    main()