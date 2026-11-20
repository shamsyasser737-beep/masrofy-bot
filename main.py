import os, re, sqlite3, asyncio, json
from datetime import datetime, date
import pytz
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import google.generativeai as genai

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
BUDGET = 200
TZ = pytz.timezone("Africa/Cairo")

CATEGORIES = {
    "اكل": "🍔 أكل",
    "مواصلات": "🚕 مواصلات",
    "شغل": "💼 شغل",
    "سجاير": "🚬 سجاير",
    "مشروبات": "🥤 مشروبات",
    "غيره": "🛒 غيره"
}

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel("gemini-2.0-flash")

conn = sqlite3.connect("expenses.db", check_same_thread=False)
conn.execute("CREATE TABLE IF NOT EXISTS expenses (id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL, item TEXT, category TEXT, date TEXT)")
conn.commit()

def add_expense(user_id, amount, item, category):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    conn.execute("INSERT INTO expenses (user_id, amount, item, category, date) VALUES (?,?,?,?,?)", (user_id, amount, item, category, today))
    conn.commit()

def today_total(user_id):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    cur = conn.execute("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE user_id=? AND date=?", (user_id, today))
    return cur.fetchone()[0]

def today_report(user_id):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    cur = conn.execute("SELECT category, SUM(amount) FROM expenses WHERE user_id=? AND date=? GROUP BY category", (user_id, today))
    rows = cur.fetchall()
    total = sum(r[1] for r in rows)
    d = {r[0]: r[1] for r in rows}
    msg = f"📊 تقرير {datetime.now(TZ).strftime('%d %B')}\n\n"
    for k, label in CATEGORIES.items():
        msg += f"{label}: {d.get(k, 0):.0f} جنيه\n"
    msg += f"---\n💰 الإجمالي: {total:.0f} / {BUDGET} جنيه\n"
    if total > BUDGET:
        msg += f"⚠️ عديت الميزانية بـ {total-BUDGET:.0f} جنيه!"
    else:
        msg += f"✅ فاضلك {BUDGET-total:.0f} جنيه"
    return msg

def parse_text(text):
    text = text.lower()
    # استخرج الرقم
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if not nums: return None
    amount = float(nums[-1])
    item = re.sub(r"\d+(?:\.\d+)?", "", text).replace("بـ","").replace("ب","").replace("جنيه","").strip() or "مصروف"
    # تحديد التصنيف
    cat = "غيره"
    if any(w in text for w in ["قهوة","شاي","عصير","بيبسي","مشروب","نسكافيه"]): cat="مشروبات"
    elif any(w in text for w in ["سجاير","سيجارة","دخان","marlboro","lm"]): cat="سجاير"
    elif any(w in text for w in ["اكل","فطار","غدا","عشا","سندوتش","كشري","برجر","بيتزا","مطعم"]): cat="اكل"
    elif any(w in text for w in ["مواصلات","اوبر","تاكسي","ميكروباص","مترو","بنزين","توكتوك"]): cat="مواصلات"
    elif any(w in text for w in ["شغل","طباعة","ورق","شحن"]): cat="شغل"
    return amount, item, cat

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلا بيك في مصروفي 💰\nابعت أي مصروف كده: `سجاير 75` أو صورة فاتورة\nوهبعتلك تقرير كل يوم 11:55 بالليل\nالميزانية: 200 جنيه")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parsed = parse_text(update.message.text)
    if not parsed:
        await update.message.reply_text("مش فاهم السعر، ابعت كده: `قهوة 60`")
        return
    amount, item, cat = parsed
    add_expense(update.effective_user.id, amount, item, cat)
    total = today_total(update.effective_user.id)
    msg = f"✅ اتسجلت: {CATEGORIES[cat]} - {item} - {amount:.0f} جنيه\n💰 صرفت النهاردة: {total:.0f} / {BUDGET}"
    if total > BUDGET: msg += "\n⚠️ عديت الـ 200 جنيه!"
    await update.message.reply_text(msg)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ بقرا الصورة...")
    try:
        photo = await update.message.photo[-1].get_file()
        data = await photo.download_as_bytearray()
        res = model.generate_content(["طلع من الصورة دي JSON بس: {\"item\": \"اسم الحاجة\", \"amount\": رقم, \"category\": واحدة من [اكل, مواصلات, شغل, سجاير, مشروبات, غيره]} لو مش لاقي سعر حط 0", {"mime_type": "image/jpeg", "data": bytes(data)}])
        j = json.loads(res.text.strip().replace("```json","").replace("```",""))
        if j["amount"] == 0:
            await update.message.reply_text("مش عارف أقرا السعر من الصورة، ابعته كتابة: `قهوة 60`")
            return
        add_expense(update.effective_user.id, j["amount"], j["item"], j["category"])
        total = today_total(update.effective_user.id)
        await update.message.reply_text(f"✅ اتسجلت من الصورة: {CATEGORIES[j['category']]} - {j['item']} - {j['amount']} جنيه\n💰 صرفت النهاردة: {total:.0f} / {BUDGET}")
    except Exception as e:
        await update.message.reply_text("معرفتش أقرا الصورة، ابعت السعر كتابة: `قهوة 60`")

async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(today_report(update.effective_user.id))

async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    # هيبعت لكل اليوزرز اللي سجلوا النهاردة
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    cur = conn.execute("SELECT DISTINCT user_id FROM expenses WHERE date=?", (today,))
    for (uid,) in cur.fetchall():
        try: await context.bot.send_message(uid, today_report(uid))
        except: pass

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", today_cmd))
    app.add_handler(CommandHandler("تقرير", today_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    # جدولة التقرير 11:55
    app.job_queue.run_daily(daily_job, time=datetime.strptime("23:55", "%H:%M").time().replace(tzinfo=TZ), name="daily")
    print("Bot started")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
