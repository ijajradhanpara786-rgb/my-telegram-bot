# =============================================================
# 0. ANGEL ONE COMPATIBILITY PATCH & FLASK
# =============================================================
import logging
import sys
import types
import asyncio
import os
import threading
from datetime import datetime
from flask import Flask

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Logzero Patch
_logzero_shim = types.ModuleType("logzero")
_logzero_shim.logger = logging.getLogger()
_logzero_shim.logfile = lambda *args, **kwargs: None
_logzero_shim.loglevel = lambda *args, **kwargs: None
_logzero_shim.json = lambda *args, **kwargs: None
sys.modules['logzero'] = _logzero_shim

# Render Port Binding
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is Live!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()

import pyotp
import requests
from SmartApi import SmartConnect
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters

# =============================================================
# 1. CREDENTIALS & CONFIG
# =============================================================
TELEGRAM_BOT_TOKEN = "8563018898:AAEFBuFkA3_p7cjiM9WrR83sqM7CCB7MMAQ"
AUTHORIZED_CHAT_ID = "6562604119"

PHONE_A = {
    "api_key": "t1kFsPtj",
    "client_id": "R372797",
    "pin": "5009",
    "totp_secret": "UIN2PAMXKIVAQPMAX22BM7PQYAA"
}

PHONE_B = {
    "api_key": "UFCQVst3",
    "client_id": "AACK748195",
    "pin": "0714",
    "totp_secret": "6JHGESTXWUUA226LCAFXOOOHAQ"
}

# =============================================================
# 2. GLOBAL STATE
# =============================================================
smart_a = None
smart_b = None
current_target_1 = 1500.0
current_target_2 = 200.0
is_monitoring = False
is_api_active = True
active_positions = {"Phone_A": False, "Phone_B": False}
awaiting_input = None  # Stores 't1' or 't2' when waiting for custom target input

# =============================================================
# 3. HELPER FUNCTIONS
# =============================================================
def is_authorized(update: Update):
    return str(update.effective_user.id) == str(AUTHORIZED_CHAT_ID)

def login_angel_one(acc):
    try:
        smart_obj = SmartConnect(api_key=acc["api_key"])
        clean_secret = acc["totp_secret"].strip().replace(" ", "").upper()
        missing_padding = len(clean_secret) % 8
        if missing_padding != 0:
            clean_secret += '=' * (8 - missing_padding)

        totp = pyotp.TOTP(clean_secret).now()
        data = smart_obj.generateSession(acc["client_id"], acc["pin"], totp)
        return smart_obj if data and data.get('status') else None
    except Exception as e:
        logging.error(f"Login Error: {e}")
        return None

def get_pnl(smart_obj):
    if not smart_obj: return 0.0
    try:
        pos = smart_obj.position()
        if pos and pos.get('data'):
            return sum(float(i.get('pnl', 0)) for i in pos['data'])
        return 0.0
    except Exception:
        return 0.0

def get_nifty_tokens():
    try:
        url = "https://margincalculator.angelbroking.com/OpenAPI_Standard_OpenAPI_MasterData/OpenAPIScripMaster.json"
        res = requests.get(url, timeout=10).json()
        futs = [i for i in res if i.get('name') == 'NIFTY' and i.get('instrumenttype') == 'FUTIDX' and i.get('exch_seg') == 'NFO']
        futs.sort(key=lambda x: datetime.strptime(x['expiry'], '%d%b%Y'))
        return futs[0], futs[1]
    except Exception as e:
        logging.error(f"Token Fetch Error: {e}")
        return None, None

async def execute_nifty(smart_obj, account_type):
    if not smart_obj: return False
    try:
        curr_fut, next_fut = get_nifty_tokens()
        if not curr_fut or not next_fut: return False
        lot = str(curr_fut.get('lotsize', '65'))

        if account_type == "Phone_A":
            b = [
                {"variety": "NORMAL", "tradingsymbol": curr_fut['symbol'], "symboltoken": curr_fut['token'], "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET", "producttype": "CARRYFORWARD", "duration": "DAY", "price": "0", "quantity": lot},
                {"variety": "NORMAL", "tradingsymbol": next_fut['symbol'], "symboltoken": next_fut['token'], "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET", "producttype": "CARRYFORWARD", "duration": "DAY", "price": "0", "quantity": lot}
            ]
        else:
            b = [
                {"variety": "NORMAL", "tradingsymbol": next_fut['symbol'], "symboltoken": next_fut['token'], "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET", "producttype": "CARRYFORWARD", "duration": "DAY", "price": "0", "quantity": lot},
                {"variety": "NORMAL", "tradingsymbol": curr_fut['symbol'], "symboltoken": curr_fut['token'], "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET", "producttype": "CARRYFORWARD", "duration": "DAY", "price": "0", "quantity": lot}
            ]
        
        res = smart_obj.placeBasketOrder(b)
        return True if res and res.get('status') else False
    except Exception:
        return False

async def exit_all_positions(smart_obj):
    if not smart_obj: return
    try:
        pos = smart_obj.position()
        if pos and pos.get('data'):
            for p in pos['data']:
                qty = int(p.get('netqty', 0))
                if qty != 0:
                    smart_obj.placeOrder({
                        "variety": "NORMAL", "tradingsymbol": p.get('tradingsymbol'),
                        "symboltoken": p.get('symboltoken'), "transactiontype": "SELL" if qty > 0 else "BUY",
                        "exchange": p.get('exchange', 'NFO'), "ordertype": "MARKET",
                        "producttype": p.get('producttype'), "duration": "DAY", "price": "0", "quantity": str(abs(qty))
                    })
    except Exception as e:
        logging.error(f"Exit Error: {e}")

# =============================================================
# 4. MONITORING LOOP
# =============================================================
async def monitor_pnl(context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring, active_positions
    t1_hit = False
    first_hit_acc = None

    while is_monitoring:
        try:
            p_a = get_pnl(smart_a) if active_positions["Phone_A"] else 0.0
            p_b = get_pnl(smart_b) if active_positions["Phone_B"] else 0.0

            # Target 1 Check
            if not t1_hit:
                hit_acc = None
                if p_a >= current_target_1 and active_positions["Phone_A"]:
                    hit_acc, smart_target, acc_key = "Phone A", smart_a, "Phone_A"
                elif p_b >= current_target_1 and active_positions["Phone_B"]:
                    hit_acc, smart_target, acc_key = "Phone B", smart_b, "Phone_B"

                if hit_acc:
                    await exit_all_positions(smart_target)
                    active_positions[acc_key] = False
                    t1_hit = True
                    first_hit_acc = hit_acc
                    
                    kb = [
                        [InlineKeyboardButton("✏️ Custom T2", callback_data="set_t2")],
                        [InlineKeyboardButton("🚨 Exit All", callback_data="exit_all")]
                    ]
                    await context.bot.send_message(
                        chat_id=AUTHORIZED_CHAT_ID,
                        text=f"🎯 *Target 1 (₹{current_target_1}) Hit on {hit_acc}!* Position Closed.\n\nTarget 2 (₹{current_target_2}) Active for other phone.",
                        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
                    )

            # Target 2 Check
            else:
                rem_key = "Phone_B" if first_hit_acc == "Phone A" else "Phone_A"
                rem_smart = smart_b if rem_key == "Phone_B" else smart_a
                rem_pnl = p_b if rem_key == "Phone_B" else p_a

                if active_positions[rem_key] and rem_pnl >= current_target_2:
                    await exit_all_positions(rem_smart)
                    active_positions[rem_key] = False
                    is_monitoring = False
                    await context.bot.send_message(
                        chat_id=AUTHORIZED_CHAT_ID,
                        text=f"🎉 *Target 2 Hit!* All Trades Complete.\n\n• Phone A: ₹{p_a:,.2f}\n• Phone B: ₹{p_b:,.2f}",
                        parse_mode="Markdown"
                    )

            await asyncio.sleep(1)
        except Exception as e:
            logging.error(f"Monitor Loop Error: {e}")
            await asyncio.sleep(1)

# =============================================================
# 5. BOT HANDLERS & BUTTONS
# =============================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return
    kb = [[InlineKeyboardButton("YES", callback_data="confirm_start"), InlineKeyboardButton("NO", callback_data="cancel")]]
    await update.message.reply_text("⚠️ *Start Today's Session?*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global smart_a, smart_b, is_monitoring, active_positions, awaiting_input
    if not is_authorized(update) or not is_api_active: return

    query = update.callback_query
    await query.answer()

    if query.data == "confirm_start":
        await query.edit_message_text("🔄 *Logging in...*", parse_mode="Markdown")
        smart_a = login_angel_one(PHONE_A)
        smart_b = login_angel_one(PHONE_B)

        if smart_a and smart_b:
            kb = [[InlineKeyboardButton("🚀 Run Nifty", callback_data="run_nifty")]]
            await query.edit_message_text("✅ *Login Success!*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await query.edit_message_text("❌ *Login Failed!* Check details.")

    elif query.data == "run_nifty":
        await query.edit_message_text("⏳ *Executing Orders...*", parse_mode="Markdown")
        res_a = await execute_nifty(smart_a, "Phone_A")
        res_b = await execute_nifty(smart_b, "Phone_B")

        if res_a and res_b:
            active_positions["Phone_A"] = True
            active_positions["Phone_B"] = True
            is_monitoring = True
            asyncio.create_task(monitor_pnl(context))
            
            # 4 Options Panel: Custom T1, Custom T2, and EXIT ALL
            kb = [
                [InlineKeyboardButton("✏️ Custom Target 1", callback_data="set_t1"), InlineKeyboardButton("✏️ Custom Target 2", callback_data="set_t2")],
                [InlineKeyboardButton("🚨 EXIT ALL POSITIONS", callback_data="exit_all")]
            ]
            await query.edit_message_text(
                f"🚀 *Nifty Live!*\n\n🎯 *Current T1:* ₹{current_target_1}\n🎯 *Current T2:* ₹{current_target_2}",
                parse_mode="Markdown", 
                reply_markup=InlineKeyboardMarkup(kb)
            )
        else:
            await query.edit_message_text("❌ *Order Execution Failed!*")

    elif query.data == "set_t1":
        awaiting_input = "t1"
        await query.message.reply_text("✏️ *Target 1 (T1) का नया अमाउंट टाइप करें:*", parse_mode="Markdown")

    elif query.data == "set_t2":
        awaiting_input = "t2"
        await query.message.reply_text("✏️ *Target 2 (T2) का नया अमाउंट टाइप करें:*", parse_mode="Markdown")

    elif query.data == "exit_all":
        is_monitoring = False
        await exit_all_positions(smart_a)
        await exit_all_positions(smart_b)
        active_positions = {"Phone_A": False, "Phone_B": False}
        await query.edit_message_text("🚨 *Exited All Positions Successfully!*", parse_mode="Markdown")

    elif query.data == "cancel":
        await query.edit_message_text("❌ *Cancelled.*", parse_mode="Markdown")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_api_active, current_target_1, current_target_2, awaiting_input
    if not is_authorized(update): return

    msg = update.message.text.strip().lower()

    if msg in ["deactive", "deactivate"]:
        is_api_active = False
        await update.message.reply_text("🚫 *Bot Deactivated!*", parse_mode="Markdown")
        return
    elif msg in ["active", "activate"]:
        is_api_active = True
        await update.message.reply_text("✅ *Bot Activated!*", parse_mode="Markdown")
        return

    # Handle Custom Inputs
    if awaiting_input == "t1":
        try:
            current_target_1 = float(msg)
            awaiting_input = None
            await update.message.reply_text(f"✅ *Target 1 Updated to ₹{current_target_1}!*", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ कृपया सही नंबर दर्ज करें।")
    elif awaiting_input == "t2":
        try:
            current_target_2 = float(msg)
            awaiting_input = None
            await update.message.reply_text(f"✅ *Target 2 Updated to ₹{current_target_2}!*", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ कृपया सही नंबर दर्ज करें।")

# =============================================================
# 6. MAIN FUNCTION
# =============================================================
def main():
    bot_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))
    print("Bot is started cleanly...")
    bot_app.run_polling()

if __name__ == "__main__":
    main()
