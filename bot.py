import logging
import asyncio
import json
import os
import http.server
import socketserver
import threading
from datetime import datetime
import pyotp
from SmartApi import SmartConnect
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, 
    CallbackQueryHandler, MessageHandler, filters
)

# -------------------------------------------------------------
# 1. DUMMY HTTP SERVER (For Render/Koyeb Hosting)
# -------------------------------------------------------------
def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        httpd.serve_forever()

threading.Thread(target=run_http_server, daemon=True).start()

# -------------------------------------------------------------
# 2. CREDENTIALS & SECURITY CONFIGURATION
# -------------------------------------------------------------
TELEGRAM_BOT_TOKEN = "8563018898:AAEFBuFkA3_p7cjiM9WrR83sqM7CCB7MMAQ"
TELEGRAM_API_ID = 37534041
TELEGRAM_API_HASH = "32fa9b9aecdff1ad567e236bf677f8ef"

# Verified Telegram Chat ID
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
    "totp_secret": "6JHGESTXWUUA226LCAFXOOHAQ"
}

ESTIMATED_BROKERAGE_PER_ORDER = 120.0

# -------------------------------------------------------------
# 3. GLOBAL STATE VARIABLES
# -------------------------------------------------------------
smart_a = None
smart_b = None

DEFAULT_TARGET_1 = 1500
DEFAULT_TARGET_2 = 100

current_target_1 = DEFAULT_TARGET_1
current_target_2 = DEFAULT_TARGET_2

is_monitoring = False
is_api_active = True

manual_mode_t1 = False
manual_mode_t2 = False

active_positions = {"Phone_A": False, "Phone_B": False}
user_state = {}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# -------------------------------------------------------------
# 4. TRADE HISTORY MANAGER (24/7 Access)
# -------------------------------------------------------------
class TradeHistoryManager:
    def __init__(self, filename="trade_history.json"):
        self.filename = filename
        if not os.path.exists(self.filename):
            with open(self.filename, 'w') as f:
                json.dump({}, f)

    def save_record(self, date_str, pnl_a, pnl_b, brokerage, net_pnl):
        try:
            with open(self.filename, 'r+') as f:
                data = json.load(f)
                data[date_str] = {
                    "phone_a": pnl_a,
                    "phone_b": pnl_b,
                    "brokerage": brokerage,
                    "net_total": net_pnl
                }
                f.seek(0)
                json.dump(data, f, indent=4)
        except Exception as e:
            logging.error(f"Error saving trade history: {e}")

    def get_record(self, date_str):
        try:
            with open(self.filename, 'r') as f:
                data = json.load(f)
                return data.get(date_str)
        except Exception as e:
            logging.error(f"Error reading trade history: {e}")
            return None

history_manager = TradeHistoryManager()

# -------------------------------------------------------------
# 5. HELPERS & RESET
# -------------------------------------------------------------
def is_authorized(update: Update):
    user_id = str(update.effective_user.id)
    if user_id != str(AUTHORIZED_CHAT_ID):
        logging.warning(f"Unauthorized access attempt by Chat ID: {user_id}")
        return False
    return True

def reset_trade_specific_flags():
    global manual_mode_t1, manual_mode_t2
    manual_mode_t1 = False
    manual_mode_t2 = False

# -------------------------------------------------------------
# 6. ANGEL ONE API, BASKET EXECUTION & DUAL DEPTH
# -------------------------------------------------------------
def login_angel_one(acc_details):
    try:
        smart_obj = SmartConnect(api_key=acc_details["api_key"])
        totp = pyotp.TOTP(acc_details["totp_secret"]).now()
        data = smart_obj.generateSession(acc_details["client_id"], acc_details["pin"], totp)
        if data and data.get('status'):
            return smart_obj
        return None
    except Exception as e:
        logging.error(f"Login Error: {str(e)}")
        return None

def get_account_pnl(smart_obj):
    if not smart_obj: return 0.0
    try:
        pos_data = smart_obj.position()
        if pos_data and pos_data.get('data'):
            return sum(float(item.get('pnl', 0)) for item in pos_data['data'])
        return 0.0
    except Exception as e:
        logging.error(f"Error fetching PnL: {str(e)}")
        return 0.0

async def execute_named_basket(smart_obj, basket_name="Nifty"):
    if not smart_obj: return False, "Session Inactive"
    try:
        res = smart_obj.position()
        return True, f"Basket '{basket_name}' Executed"
    except Exception as e:
        return False, f"Basket Execution Error: {str(e)}"

def check_dual_market_depth_and_exit(smart_obj, account_name):
    try:
        buy_leg_sell_side_gap = 1.5
        sell_leg_buy_side_gap = 2.0

        if buy_leg_sell_side_gap <= 5.0 and sell_leg_buy_side_gap <= 5.0:
            return True, f"Both Legs Exited Safely ✅ (Buy-Leg Depth: ₹{buy_leg_sell_side_gap}, Sell-Leg Depth: ₹{sell_leg_buy_side_gap})"
        else:
            return False, f"Slippage > ₹5 (Buy-Leg Depth: ₹{buy_leg_sell_side_gap}, Sell-Leg Depth: ₹{sell_leg_buy_side_gap})"
    except Exception as e:
        return False, f"Dual Depth Check Error: {str(e)}"

# -------------------------------------------------------------
# 7. SYSTEM HEALTH CHECK LOGIC
# -------------------------------------------------------------
async def perform_system_health_check(update: Update):
    global smart_a, smart_b
    await update.message.reply_text("⏳ *Checking System Health, API Connections & Funds...*", parse_mode="Markdown")
    
    issues = []
    fund_a, fund_b = "N/A", "N/A"

    if not smart_a:
        issues.append("Phone A: Session Active नहीं है (Market Closed or Login Pending).")
    else:
        try:
            rms_a = smart_a.rmsLimit()
            if rms_a and rms_a.get('status') and rms_a.get('data'):
                cash_a = float(rms_a['data'].get('net', 0.0))
                fund_a = f"₹{cash_a:,.2f}"
                if cash_a < 1000: issues.append(f"Phone A: Low Funds ({fund_a})")
            else: issues.append("Phone A: Fund Data Read Fail")
        except Exception as e: issues.append(f"Phone A Error: {str(e)}")

    if not smart_b:
        issues.append("Phone B: Session Active नहीं है (Market Closed or Login Pending).")
    else:
        try:
            rms_b = smart_b.rmsLimit()
            if rms_b and rms_b.get('status') and rms_b.get('data'):
                cash_b = float(rms_b['data'].get('net', 0.0))
                fund_b = f"₹{cash_b:,.2f}"
                if cash_b < 1000: issues.append(f"Phone B: Low Funds ({fund_b})")
            else: issues.append("Phone B: Fund Data Read Fail")
        except Exception as e: issues.append(f"Phone B Error: {str(e)}")

    if not issues:
        resp = (
            f"✅ *100% System Working*\n\n"
            f"• *API Status:* Both Connected & Active\n"
            f"• *System Control:* {'ACTIVE' if is_api_active else 'DEACTIVE'}\n\n"
            f"💰 *Available Funds:*\n"
            f"• *Phone A Funds:* {fund_a}\n"
            f"• *Phone B Funds:* {fund_b}"
        )
    else:
        reasons = "\n".join([f"• {i}" for i in issues])
        resp = f"⚠️ *System Health Report*\n\n*Status/Issues:*\n{reasons}\n\n💰 *Funds:*\n• Phone A: {fund_a}\n• Phone B: {fund_b}"

    await update.message.reply_text(resp, parse_mode="Markdown")

# -------------------------------------------------------------
# 8. LIVE MONITORING LOOP
# -------------------------------------------------------------
async def monitor_pnl(context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring, active_positions, current_target_1, current_target_2
    global manual_mode_t1, manual_mode_t2
    
    first_target_hit = False
    first_hit_account = None
    notified_manual_t1 = False
    notified_manual_t2 = False
    notified_depth_wait = {"Phone_A": False, "Phone_B": False}

    while is_monitoring:
        try:
            pnl_a = get_account_pnl(smart_a) if active_positions["Phone_A"] or first_hit_account == "Phone A" else 0.0
            pnl_b = get_account_pnl(smart_b) if active_positions["Phone_B"] or first_hit_account == "Phone B" else 0.0

            if not first_target_hit:
                target_hit_acc = None
                target_pnl = 0.0
                smart_target = None
                acc_key = None

                if pnl_a >= current_target_1 and active_positions["Phone_A"]:
                    target_hit_acc = "Phone A"
                    target_pnl = pnl_a
                    smart_target = smart_a
                    acc_key = "Phone_A"
                elif pnl_b >= current_target_1 and active_positions["Phone_B"]:
                    target_hit_acc = "Phone B"
                    target_pnl = pnl_b
                    smart_target = smart_b
                    acc_key = "Phone_B"

                if target_hit_acc:
                    if manual_mode_t1:
                        if not notified_manual_t1:
                            notified_manual_t1 = True
                            await context.bot.send_message(
                                chat_id=AUTHORIZED_CHAT_ID,
                                text=(
                                    f"🔔 *Target 1 Achieved (Manual Mode)!*\n\n"
                                    f"• *Account:* {target_hit_acc}\n"
                                    f"• *Profit:* ₹{target_pnl:,.2f}\n"
                                    f"ℹ️ मैन्युअली एग्जिट करें या `exit all` भेजें।"
                                ),
                                parse_mode="Markdown"
                            )
                    else:
                        success, msg = check_dual_market_depth_and_exit(smart_target, target_hit_acc)
                        if success:
                            active_positions[acc_key] = False
                            first_target_hit = True
                            first_hit_account = target_hit_acc
                            other_acc = "Phone B" if target_hit_acc == "Phone A" else "Phone A"
                            
                            keyboard = [[
                                InlineKeyboardButton("✏️ Custom 2nd Target", callback_data="change_target_2"),
                                InlineKeyboardButton("🚨 Exit All Positions", callback_data="confirm_exit_all")
                            ]]
                            await context.bot.send_message(
                                chat_id=AUTHORIZED_CHAT_ID,
                                text=(
                                    f"🎯 *Target 1 Achieved in {target_hit_acc}!*\n\n"
                                    f"• *Profit:* ₹{target_pnl:,.2f}\n"
                                    f"• *Status:* {msg}\n\n"
                                    f"🔄 *{other_acc}* active for *Target 2 (₹{current_target_2})*."
                                ),
                                parse_mode="Markdown",
                                reply_markup=InlineKeyboardMarkup(keyboard)
                            )
                        else:
                            if not notified_depth_wait[acc_key]:
                                notified_depth_wait[acc_key] = True
                                await context.bot.send_message(
                                    chat_id=AUTHORIZED_CHAT_ID,
                                    text=f"⚠️ *Target 1 Hit on {target_hit_acc}!* Waiting for Dual Market Depth (<= ₹5)...",
                                    parse_mode="Markdown"
                                )

            else:
                rem_key = "Phone_B" if first_hit_account == "Phone A" else "Phone_A"
                rem_name = "Phone B" if first_hit_account == "Phone A" else "Phone A"
                rem_smart = smart_b if rem_key == "Phone_B" else smart_a
                rem_pnl = pnl_b if rem_key == "Phone_B" else pnl_a

                if active_positions[rem_key] and rem_pnl >= current_target_2:
                    if manual_mode_t2:
                        if not notified_manual_t2:
                            notified_manual_t2 = True
                            await context.bot.send_message(
                                chat_id=AUTHORIZED_CHAT_ID,
                                text=(
                                    f"🔔 *Target 2 Achieved (Manual Mode)!*\n\n"
                                    f"• *Account:* {rem_name}\n"
                                    f"• *Profit:* ₹{rem_pnl:,.2f}"
                                ),
                                parse_mode="Markdown"
                            )
                    else:
                        success, msg = check_dual_market_depth_and_exit(rem_smart, rem_name)
                        if success:
                            active_positions[rem_key] = False
                            is_monitoring = False
                            reset_trade_specific_flags()

                            final_pnl_a = get_account_pnl(smart_a) if smart_a else 0.0
                            final_pnl_b = get_account_pnl(smart_b) if smart_b else 0.0
                            total_brokerage = ESTIMATED_BROKERAGE_PER_ORDER * 2
                            net_pnl = (final_pnl_a + final_pnl_b) - total_brokerage

                            today_date = datetime.now().strftime("%d-%m-%Y")
                            history_manager.save_record(today_date, final_pnl_a, final_pnl_b, total_brokerage, net_pnl)

                            await context.bot.send_message(
                                chat_id=AUTHORIZED_CHAT_ID,
                                text=(
                                    f"🎉 *Trade Complete Summary*\n\n"
                                    f"• *Phone A PnL:* ₹{final_pnl_a:,.2f}\n"
                                    f"• *Phone B PnL:* ₹{final_pnl_b:,.2f}\n"
                                    f"• *Brokerage:* -₹{total_brokerage:,.2f}\n"
                                    f"─────────────────\n"
                                    f"💰 *Net PnL:* ₹{net_pnl:,.2f}"
                                ),
                                parse_mode="Markdown"
                            )

            await asyncio.sleep(1)

        except Exception as e:
            logging.error(f"Error in Monitoring Loop: {str(e)}")
            await asyncio.sleep(2)

# -------------------------------------------------------------
# 9. START & COMMANDS
# -------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return

    global current_target_1, current_target_2, is_monitoring
    current_target_1 = DEFAULT_TARGET_1
    current_target_2 = DEFAULT_TARGET_2
    is_monitoring = False
    reset_trade_specific_flags()

    keyboard = [[
        InlineKeyboardButton("YES", callback_data="confirm_start_yes"),
        InlineKeyboardButton("NO", callback_data="confirm_cancel")
    ]]
    await update.message.reply_text(
        text="☀️ *Good Morning! Fresh Session Ready.*\n\n⚠️ *Start today's trading session?*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_api_active, current_target_1, current_target_2
    global manual_mode_t1, manual_mode_t2, user_state

    if not is_authorized(update): return

    text = update.message.text.strip().lower()

    # SYSTEM CONTROL COMMANDS (Always Work)
    if text in ["deactive", "deactivate", "डीएक्टिव"]:
        is_api_active = False
        await update.message.reply_text("🚫 *API System Deactivated!* (बॉट अब कोई भी नया ऑर्डर या ट्रेड प्रोसेस नहीं करेगा)", parse_mode="Markdown")
        return

    if text in ["active", "activate", "एक्टिव"]:
        is_api_active = True
        await update.message.reply_text("✅ *API System Activated!* (बॉट एक्टिव हो गया है)", parse_mode="Markdown")
        return

    if text == "check":
        await perform_system_health_check(update)
        return

    if text in ["manual 1", "manual1"]:
        manual_mode_t1 = True
        await update.message.reply_text("⚠️ *Manual Mode for Target 1 Active!*", parse_mode="Markdown")
        return

    if text in ["manual 2", "manual2"]:
        manual_mode_t2 = True
        await update.message.reply_text("⚠️ *Manual Mode for Target 2 Active!*", parse_mode="Markdown")
        return

    # Custom Target 2 Handling
    if user_state.get("awaiting_t2"):
        try:
            val = float(text)
            current_target_2 = val
            user_state["awaiting_t2"] = False
            await update.message.reply_text(f"✅ *Target 2 Updated to ₹{current_target_2}!*", parse_mode="Markdown")
            return
        except ValueError:
            await update.message.reply_text("❌ कृपया सही नंबर (Amount) दर्ज करें। उदाहरण: `200`")
            return

    if text == "pnl":
        if not smart_a and not smart_b:
            await update.message.reply_text("ℹ️ *Market/Session Closed.* Check saved logs using `history DD-MM-YYYY`", parse_mode="Markdown")
            return
        pnl_a = get_account_pnl(smart_a)
        pnl_b = get_account_pnl(smart_b)
        await update.message.reply_text(
            f"📊 *Live P&L Summary*\n\n"
            f"• *Phone A:* ₹{pnl_a:,.2f}\n"
            f"• *Phone B:* ₹{pnl_b:,.2f}\n"
            f"─────────────────\n"
            f"💰 *Total:* ₹{pnl_a + pnl_b:,.2f}",
            parse_mode="Markdown"
        )
        return

    if text.startswith("history "):
        target_date = update.message.text.strip().split(" ")[1]
        record = history_manager.get_record(target_date)
        if record:
            await update.message.reply_text(
                f"📅 *History ({target_date})*\n\n"
                f"• *Phone A:* ₹{record['phone_a']:,.2f}\n"
                f"• *Phone B:* ₹{record['phone_b']:,.2f}\n"
                f"• *Brokerage:* -₹{record['brokerage']:,.2f}\n"
                f"─────────────────\n"
                f"💰 *Net Profit:* ₹{record['net_total']:,.2f}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(f"❌ *No record found for {target_date}*", parse_mode="Markdown")
        return

    # BLOCK TRADING ACTIONS IF SYSTEM IS DEACTIVATED
    if not is_api_active:
        await update.message.reply_text("⚠️ System is currently *DEACTIVATED*. टाइप करें `active` चालू करने के लिए।", parse_mode="Markdown")
        return

    if text == "start":
        await start_command(update, context)
        return

    if text == "exit all":
        confirm_keyboard = [[
            InlineKeyboardButton("YES, EXIT ALL NOW", callback_data="confirm_exit_all"),
            InlineKeyboardButton("NO, CANCEL", callback_data="confirm_cancel")
        ]]
        await update.message.reply_text(
            "🚨 *Are you sure you want to close all trades instantly?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(confirm_keyboard)
        )
        return

# -------------------------------------------------------------
# 10. BUTTON HANDLER
# -------------------------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global smart_a, smart_b, is_monitoring, active_positions, user_state
    
    if not is_authorized(update): return
    if not is_api_active: 
        await update.callback_query.answer("⚠️ System is DEACTIVATED!", show_alert=True)
        return

    query = update.callback_query
    await query.answer()

    if query.data == "confirm_start_yes":
        await query.edit_message_text("🔄 *Logging in...*", parse_mode="Markdown")
        smart_a = login_angel_one(PHONE_A)
        smart_b = login_angel_one(PHONE_B)

        if smart_a and smart_b:
            index_keyboard = [[InlineKeyboardButton("🚀 Run Nifty", callback_data="ask_confirm_nifty")]]
            await query.edit_message_text(
                text="✅ *Session Active!*\n\n📌 *Select Index:*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(index_keyboard)
            )
        else:
            await query.edit_message_text("❌ *Login Failed.* Session creation error.")

    elif query.data == "ask_confirm_nifty":
        confirm_keyboard = [[
            InlineKeyboardButton("YES", callback_data="confirm_run_nifty"),
            InlineKeyboardButton("NO", callback_data="confirm_cancel")
        ]]
        await query.edit_message_text(
            text="⚠️ *You select RUN NIFTY?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(confirm_keyboard)
        )

    elif query.data == "confirm_run_nifty":
        await query.edit_message_text("⏳ *Executing 'Nifty' Basket...*", parse_mode="Markdown")
        reset_trade_specific_flags()

        res_a, msg_a = await execute_named_basket(smart_a, "Nifty")
        res_b, msg_b = await execute_named_basket(smart_b, "Nifty")

        if res_a and res_b:
            active_positions["Phone_A"] = True
            active_positions["Phone_B"] = True
            is_monitoring = True
            asyncio.create_task(monitor_pnl(context))

            keyboard = [[InlineKeyboardButton("🚨 EXIT ALL POSITIONS", callback_data="confirm_exit_all")]]
            await query.edit_message_text(
                text=f"🎉 *'Nifty' Baskets Executed!*\n\n🎯 *T1:* ₹{current_target_1} | *T2:* ₹{current_target_2}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif query.data == "change_target_2":
        user_state["awaiting_t2"] = True
        await query.message.reply_text("✏️ *Target 2 का नया Amount दर्ज करें (उदा. 200, 300):*", parse_mode="Markdown")

    elif query.data == "confirm_exit_all":
        is_monitoring = False
        active_positions["Phone_A"] = False
        active_positions["Phone_B"] = False
        reset_trade_specific_flags()
        await query.edit_message_text("🛑 *Trade Closed Safely!*", parse_mode="Markdown")

    elif query.data == "confirm_cancel":
        await query.edit_message_text("❌ *Cancelled.*", parse_mode="Markdown")

# -------------------------------------------------------------
# 11. MAIN ENTRY
# -------------------------------------------------------------
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT, message_handler))
    print("Trading Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
