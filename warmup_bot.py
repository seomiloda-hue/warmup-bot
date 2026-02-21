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
import csv
import requests
import threading
import socket
from flask import Flask

# ================== الإعدادات الأساسية ==================

SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# ✅ قراءة مسار ملف JSON من متغير البيئة مباشرة
JSON_FILE = os.environ["JSON_FILE"]  # تم التعديل هنا

WARMUP_SHEET = "Warmup Accounts"
MESSAGES_FILE = "messages.json"
STATE_FILE = "warmup_state.json"
LOG_FILE = "warmup_log.csv"

ZOHO_EMAIL = "contact@dualwin.agency"
ZOHO_PASSWORD = os.environ.get("ZOHO_PASSWORD", "")
SMTP_SERVER = "smtp.zoho.com"
SMTP_PORT = 587

# قراءة ساعات العمل من المتغيرات البيئية
WORK_START_HOUR = int(os.environ.get("WORK_START_HOUR", 9))
WORK_END_HOUR = int(os.environ.get("WORK_END_HOUR", 16))

DAILY_LIMITS = {
    1: 10, 2: 10, 3: 10, 4: 10, 5: 10,
    6: 15, 7: 15, 8: 15, 9: 15, 10: 15,
    11: 20, 12: 20, 13: 20, 14: 20, 15: 20,
    16: 25, 17: 25, 18: 25, 19: 25, 20: 25,
    21: 30, 22: 30, 23: 30, 24: 30, 25: 30
}

WARMUP_DAYS = 25

MIN_PERIODS = 3
MAX_PERIODS = 6
MIN_GAP_BETWEEN_PERIODS = 45 * 60
MAX_GAP_BETWEEN_PERIODS = 3 * 60 * 60
MIN_DELAY_WITHIN_PERIOD = 2 * 60
MAX_DELAY_WITHIN_PERIOD = 7 * 60

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "DualWin_Agency")

# ================== خادم Flask لإبقاء البوت نشطاً على Railway ==================

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ بوت التسخين شغال على Railway"

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# تشغيل Flask في خلفية منفصلة
threading.Thread(target=run_flask, daemon=True).start()

# ================== الرسائل والمواضيع ==================

MESSAGES = [
    "هل وصلك هذا الإيميل؟",
    "تجربة سريعة لنظام الإرسال.",
    "يرجى تجاهل هذه الرسالة، مجرد اختبار.",
    "تأكيد وصول البريد.",
    "اختبار بسيط لوصول الرسائل.",
    "فقط تحقق سريع من جهة الإرسال.",
    "تجربة اتصال بين الحسابات.",
    "هل يظهر هذا الإيميل في الوارد لديك؟",
    "اختبار عادي للبريد.",
    "أتأكد فقط من وصول الرسالة.",
    "رسالة تجريبية قصيرة.",
    "تحقق سريع من الاستلام.",
    "تجربة نظام البريد اليوم.",
    "فقط اختبار بسيط، لا حاجة لأي إجراء.",
    "تأكدت من الإرسال، هل تم الاستلام؟",
    "تجربة جديدة للبريد.",
    "فحص سريع لوصول الرسائل.",
    "اختبار أخير للتأكد من الاستلام."
]

SUBJECTS = [
    "اختبار",
    "تجربة",
    "تحقق سريع",
    "تأكيد الاستلام",
    "اختبار بريد",
    "تجربة الإرسال",
    "فحص سريع",
    "رسالة اختبار",
    "تحقق من الوصول"
]

# ================== ربط Google Sheets ==================

def connect_to_warmup_sheet():
    try:
        print(f"🔍 محاولة فتح ملف JSON: {JSON_FILE}")
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, SCOPE)
        print("✅ تم تحميل ملف JSON بنجاح")
        print("🔍 محاولة التفويض لـ Google Sheets...")
        client = gspread.authorize(creds)
        print("✅ تم إنشاء client بنجاح")
        print("✅ تم التفويض لـ Google Sheets")
        print("🔍 محاولة فتح الشيت...")
        
        # ✅ إضافة مهلة زمنية 30 ثانية لمنع التعليق
        socket.setdefaulttimeout(30)
        print("⏰ تم تعيين مهلة زمنية 30 ثانية لعملية فتح الشيت")
        
        # ✅ فتح الشيت باستخدام الاسم
        sheet = client.open(WARMUP_SHEET).sheet1
        print(f"✅ تم فتح شيت: {WARMUP_SHEET}")
        return sheet
    except FileNotFoundError:
        print(f"❌ ملف JSON غير موجود: {JSON_FILE}")
        return None
    except gspread.exceptions.SpreadsheetNotFound:
        print(f"❌ لم يتم العثور على شيت باسم: {WARMUP_SHEET}")
        print("   تأكد من اسم الشيت ومشاركته مع حساب الخدمة.")
        return None
    except socket.timeout:
        print("❌ انتهت المهلة الزمنية أثناء محاولة فتح الشيت (30 ثانية).")
        print("   قد تكون هناك مشكلة في الاتصال بـ Google Sheets.")
        return None
    except Exception as e:
        print(f"❌ خطأ غير متوقع: {type(e).__name__}: {e}")
        return None

# ================== قراءة حسابات Gmail ==================

def get_gmail_accounts(sheet):
    try:
        records = sheet.get_all_records()
        accounts = []
        for row in records:
            email = list(row.values())[0]
            if email and '@' in email:
                accounts.append(email.strip())
        print(f"📧 تم قراءة {len(accounts)} حساب Gmail")
        return accounts
    except Exception as e:
        print(f"❌ خطأ في قراءة الحسابات: {e}")
        return []

# ================== إرسال إيميل عبر Zoho ==================

def send_email(recipient_email, subject, body):
    try:
        msg = MIMEMultipart()
        msg['From'] = ZOHO_EMAIL
        msg['To'] = recipient_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(ZOHO_EMAIL, ZOHO_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        print(f"   ✅ {recipient_email} ← {subject}")
        return True
    except Exception as e:
        print(f"   ❌ فشل الإرسال: {e}")
        return False

# ================== إدارة الحالة ==================

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            return None
    return None

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def init_state():
    return {
        "start_date": datetime.now().isoformat(),
        "current_day": 1,
        "total_sent": 0,
        "completed": False,
        "last_reset_date": datetime.now().strftime("%Y-%m-%d")
    }

# ================== تسجيل النشاط ==================

def log_daily_activity(day, target, sent, accounts_used):
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Day', 'Target', 'Sent', 'Accounts Used', 'Status'])
        status = "Completed" if sent >= target else "Partial"
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d"),
            day,
            target,
            sent,
            accounts_used,
            status
        ])

# ================== إشعار نهاية التسخين ==================

def send_completion_notification(total_sent):
    try:
        message = f"""🔥 اكتملت مرحلة التسخين!

📅 المدة: 25 يوم
📧 إجمالي الرسائل: {total_sent}
✅ contact@dualwin.agency جاهز للعملاء الحقيقيين"""
        
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode('utf-8'),
            headers={
                "Title": "🤖 بوت التسخين",
                "Priority": "high",
                "Tags": "rocket,tada,fire,check"
            }
        )
        print("✅ تم إرسال إشعار النهاية")
    except Exception as e:
        print(f"❌ فشل إرسال الإشعار: {e}")

# ================== إرسال تقرير كل 5 أيام ==================

def send_5day_report(day, total_sent_so_far):
    """يرسل تقرير ملخص كل 5 أيام عبر ntfy"""
    
    days_left = 25 - day
    progress = (day / 25) * 100
    
    report = f"""📊 **تقرير منتصف المرحلة - اليوم {day}/25**

✅ تم إرسال: {total_sent_so_far} رسالة حتى الآن
📈 تقدم المرحلة: {progress:.1f}%
⏳ الأيام المتبقية: {days_left}

🔥 مستمرين في بناء السمعة!
"""
    
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=report.encode('utf-8'),
            headers={
                "Title": "📆 تقرير الـ 5 أيام",
                "Priority": "default",
                "Tags": "bar_chart"
            }
        )
        print(f"📊 تم إرسال تقرير اليوم {day}")
    except Exception as e:
        print(f"❌ فشل إرسال التقرير: {e}")

# ================== التوزيع العشوائي ==================

def distribute_to_accounts(accounts, total_messages):
    if not accounts:
        return {}
    num_accounts = len(accounts)
    distribution = {}
    remaining = total_messages
    for acc in accounts:
        distribution[acc] = 1
        remaining -= 1
    while remaining > 0:
        for acc in accounts:
            if remaining <= 0:
                break
            if random.random() < 0.5:
                distribution[acc] += 1
                remaining -= 1
    return distribution

def generate_activity_periods(total_messages, distribution):
    num_periods = random.randint(MIN_PERIODS, MAX_PERIODS)
    work_seconds = (WORK_END_HOUR - WORK_START_HOUR) * 3600
    period_starts = []
    for _ in range(num_periods):
        start_second = random.randint(0, work_seconds - 1800)
        period_starts.append(start_second)
    period_starts.sort()
    
    periods = []
    remaining = total_messages
    for i in range(num_periods):
        if i == num_periods - 1:
            periods.append(remaining)
        else:
            max_for_period = remaining - (num_periods - i - 1)
            period_msgs = random.randint(1, max_for_period)
            periods.append(period_msgs)
            remaining -= period_msgs
    
    account_schedule = {acc: [] for acc in distribution.keys()}
    for p_idx, p_msgs in enumerate(periods):
        available = list(distribution.keys())
        p_accounts = []
        for _ in range(p_msgs):
            if not available:
                available = list(distribution.keys())
            acc = random.choice(available)
            p_accounts.append(acc)
            available.remove(acc)
        for acc in set(p_accounts):
            account_schedule[acc].append({
                "period_start": period_starts[p_idx],
                "count": p_accounts.count(acc)
            })
    return period_starts, periods, account_schedule

# ================== تشغيل يوم ==================

def run_warmup_day(sheet):
    print("🔥 الدالة run_warmup_day بدأت التنفيذ...")
    state = load_state()
    if not state:
        print("📝 لا توجد حالة سابقة، سيتم إنشاء حالة جديدة.")
        state = init_state()
    
    if state["completed"]:
        print("✅ التسخين مكتمل!")
        return
    
    day = state["current_day"]
    if day > WARMUP_DAYS:
        print("🏁 اليوم أكبر من 25، سيتم إنهاء المرحلة.")
        state["completed"] = True
        save_state(state)
        send_completion_notification(state["total_sent"])
        return
    
    target = DAILY_LIMITS[day]
    print(f"\n🔥 اليوم {day}/25 - المستهدف: {target}")
    
    accounts = get_gmail_accounts(sheet)
    if len(accounts) < 2:
        print("❌ احتيج حسابين Gmail على الأقل")
        return
    
    dist = distribute_to_accounts(accounts, target)
    print("📊 التوزيع:", dist)
    
    starts, counts, schedule = generate_activity_periods(target, dist)
    print(f"⏰ تم إنشاء {len(starts)} فترات نشاط.")
    
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day, WORK_START_HOUR, 0)
    sent = 0
    used_accounts = set()
    
    for p_idx, (start_sec, p_msgs) in enumerate(zip(starts, counts)):
        p_time = today_start + timedelta(seconds=start_sec)
        if p_time > datetime.now():
            wait = (p_time - datetime.now()).total_seconds()
            if wait > 0:
                print(f"⏳ انتظار {p_time.strftime('%H:%M')}")
                time.sleep(wait)
        
        print(f"\n📨 الفترة {p_idx+1} ({p_time.strftime('%H:%M')}) - عدد الرسائل: {p_msgs}")
        
        p_emails = []
        for acc, sch in schedule.items():
            for s in sch:
                if s["period_start"] == start_sec:
                    p_emails.extend([acc] * s["count"])
        random.shuffle(p_emails)
        
        for i, to in enumerate(p_emails):
            subj = random.choice(SUBJECTS)
            msg = random.choice(MESSAGES)
            print(f"   📤 جاري الإرسال إلى {to}...")
            if send_email(to, subj, msg):
                sent += 1
                used_accounts.add(to)
            if i < len(p_emails) - 1:
                delay = random.randint(MIN_DELAY_WITHIN_PERIOD, MAX_DELAY_WITHIN_PERIOD)
                print(f"   ⏳ انتظار {delay//60} د {delay%60} ث")
                time.sleep(delay)
        
        if p_idx < len(starts) - 1:
            next_t = today_start + timedelta(seconds=starts[p_idx + 1])
            wait = (next_t - datetime.now()).total_seconds()
            if wait > MIN_GAP_BETWEEN_PERIODS:
                sleep_time = wait - random.randint(5, 15) * 60
                if sleep_time > 0:
                    print(f"😴 انتظار للفترة القادمة ({sleep_time//60} دقيقة)")
                    time.sleep(sleep_time)
    
    state["total_sent"] += sent
    state["last_reset_date"] = datetime.now().strftime("%Y-%m-%d")
    
    # إرسال تقرير كل 5 أيام
    if day % 5 == 0:
        send_5day_report(day, state["total_sent"])
    
    if day < WARMUP_DAYS:
        state["current_day"] = day + 1
        print(f"📅 الانتقال إلى اليوم {day + 1}")
    else:
        state["completed"] = True
        print("🎉 اكتملت مرحلة التسخين!")
        send_completion_notification(state["total_sent"])
    
    save_state(state)
    log_daily_activity(day, target, sent, len(used_accounts))
    print(f"📊 اليوم {day}: أرسل {sent}/{target}")

# ================== التشغيل الرئيسي ==================

def main():
    print("🚀 بوت التسخين - DualWin Agency")
    print("=" * 50)
    
    print("🔍 الخطوة 1: محاولة الاتصال بـ Google Sheets...")
    sheet = connect_to_warmup_sheet()
    if not sheet:
        print("❌ فشل الاتصال بـ Google Sheets. تحقق من ملف JSON والصلاحيات.")
        return
    
    print("✅ الخطوة 2: تم الاتصال بـ Google Sheets بنجاح.")
    print("✅ البوت جاهز")
    print("=" * 50)
    print("🔥 بدء التشغيل الفوري للتسخين...")
    
    # تشغيل دورة التسخين مباشرة (مرة واحدة)
    run_warmup_day(sheet)
    
    print("\n✅ انتهت دورة التسخين الحالية.")
    print("⏳ سيتم إنهاء البرنامج. يمكنك تشغيله يدوياً مرة أخرى لبدء دورة جديدة.")
    # لا نضع حلقة لا نهائية هنا لتجنب الانتظار

if __name__ == "__main__":
    main()
