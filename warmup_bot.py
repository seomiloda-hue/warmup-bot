import os, json, random
from datetime import datetime, date, timedelta, timezone
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ====== CONFIG ======
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
JSON_FILE = "gcp-key.json"
WARMUP_SHEET = "Warmup Accounts"   # اسم Google Sheet
STATE_FILE = "warmup_state.json"

SMTP_SERVER = "smtp.zoho.com"
SMTP_PORT = 587

# مغرب (حالياً كتستعمل UTC+0 فـرمضان غالباً) — خليه UTC باش ما توقعش فـمشاكل DST
TZ = timezone.utc

# كل Run فـ GitHub Actions غادي يرسل غير عدد صغير ثم يخرج
MAX_SEND_PER_RUN = int(os.environ.get("MAX_SEND_PER_RUN", "1"))

# خطة 25 يوم
DAILY_GOALS = {
    1: 5,  2: 5,  3: 6,  4: 6,  5: 7,
    6: 7,  7: 8,  8: 8,  9: 9,  10: 9,
    11: 10, 12: 10, 13: 12, 14: 12, 15: 14,
    16: 14, 17: 16, 18: 16, 19: 18, 20: 18,
    21: 20, 22: 20, 23: 20, 24: 20, 25: 20
}
TOTAL_DAYS = 25

SUBJECTS = ["اختبار", "تحقق سريع", "تأكيد الاستلام", "رسالة اختبار"]
MESSAGES = [
    "هل وصلك هذا الإيميل؟",
    "تجربة سريعة لنظام الإرسال.",
    "يرجى تجاهل هذه الرسالة، مجرد اختبار.",
    "تأكيد وصول البريد.",
    "اختبار بسيط لوصول الرسائل.",
    "هل يظهر هذا الإيميل في الوارد لديك؟"
]

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "DualWin_Agency")

# ====== STATE ======
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
                # compatibility / defaults
                s.setdefault("start_date", date.today().isoformat())
                s.setdefault("sent_today", 0)
                s.setdefault("last_day", date.today().isoformat())
                s.setdefault("total_sent", 0)
                s.setdefault("completed", False)
                return s
        except:
            pass
    return {
        "start_date": date.today().isoformat(),
        "last_day": date.today().isoformat(),
        "sent_today": 0,
        "total_sent": 0,
        "completed": False
    }

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def current_day_number(state):
    start = date.fromisoformat(state["start_date"])
    today = datetime.now(TZ).date()
    day = (today - start).days + 1
    return max(1, min(TOTAL_DAYS, day))

def reset_day_if_needed(state):
    today = datetime.now(TZ).date().isoformat()
    if state.get("last_day") != today:
        state["last_day"] = today
        state["sent_today"] = 0

# ====== GOOGLE SHEET ======
def connect_sheet():
    creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, SCOPE)
    client = gspread.authorize(creds)
    return client.open(WARMUP_SHEET).sheet1

def get_emails(sheet):
    emails = sheet.col_values(1)
    valid = [e.strip() for e in emails if e and "@" in e]
    return valid

# ====== EMAIL ======
def send_email(from_email, app_password, to_email, subject, body):
    msg = MIMEMultipart()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
    server.starttls()
    server.login(from_email, app_password)
    server.send_message(msg)
    server.quit()

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

# ====== MAIN ======
def main():
    from_email = os.environ.get("ZOHO_EMAIL", "").strip()
    app_password = os.environ.get("ZOHO_PASSWORD", "").strip()

    if not from_email:
        print("Missing ZOHO_EMAIL")
        ntfy("❌ Missing ZOHO_EMAIL secret/variable", title="Warmup Error", tags="x")
        return
    if not app_password:
        print("Missing ZOHO_PASSWORD")
        ntfy("❌ Missing ZOHO_PASSWORD secret", title="Warmup Error", tags="x")
        return

    state = load_state()
    reset_day_if_needed(state)

    day = current_day_number(state)
    if day >= TOTAL_DAYS and state.get("completed"):
        print("Completed.")
        return

    target_today = DAILY_GOALS.get(day, 5)
    remaining_today = max(0, target_today - int(state.get("sent_today", 0)))

    if remaining_today <= 0:
        print(f"OK day={day} already completed target={target_today}")
        ntfy(f"✅ Day {day} already completed.\nTarget: {target_today}\nTotal sent: {state['total_sent']}",
             title="Daily Summary", tags="white_check_mark")
        save_state(state)
        return

    to_send_now = min(MAX_SEND_PER_RUN, remaining_today)

    try:
        sheet = connect_sheet()
        emails = get_emails(sheet)
        if not emails:
            print("No emails in sheet.")
            ntfy("⚠️ No emails found in sheet column A", title="Warmup Warning", tags="warning")
            save_state(state)
            return
    except Exception as e:
        print("Sheet error:", e)
        ntfy(f"❌ Google Sheet error: {e}", title="Warmup Error", tags="x")
        save_state(state)
        return

    sent_now = 0
    for _ in range(to_send_now):
        to_email = random.choice(emails)
        subject = random.choice(SUBJECTS)
        body = random.choice(MESSAGES)
        try:
            send_email(from_email, app_password, to_email, subject, body)
            sent_now += 1
            state["sent_today"] = int(state.get("sent_today", 0)) + 1
            state["total_sent"] = int(state.get("total_sent", 0)) + 1
        except Exception as e:
            print("SMTP error:", e)
            ntfy(f"❌ SMTP error: {e}", title="Warmup Error", tags="x")
            break

    save_state(state)

    print(f"OK day={day} target={target_today} sent_now={sent_now} sent_today={state['sent_today']} total={state['total_sent']}")

    # إذا كمّل الهدف ديال اليوم، صيفط Summary
    if state["sent_today"] >= target_today:
        if day >= TOTAL_DAYS:
            state["completed"] = True
            save_state(state)
            ntfy(f"🎉 Warmup completed!\nTotal sent: {state['total_sent']}", title="Warmup Done", tags="tada")
        else:
            ntfy(f"✅ Day {day} completed.\nTarget: {target_today}\nTotal sent: {state['total_sent']}",
                 title="Daily Summary", tags="white_check_mark")

if __name__ == "__main__":
    main()
