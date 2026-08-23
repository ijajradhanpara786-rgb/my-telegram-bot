import logging
import asyncio
import json
import os
import http.server
import socketserver
import threading
import time
from datetime import datetime
import pyotp
import requests
from SmartApi import SmartConnect
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, 
    CallbackQueryHandler, MessageHandler, filters
)

# -------------------------------------------------------------
# 1. DUMMY HTTP SERVER (For 24/7 Hosting)
# -------------------------------------------------------------
def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        httpd.serve_forever()

threading.Thread(target=run_http_server, daemon=True).start()

# -------------------------------------------------------------
# 2. CREDENTIALS & CONFIGURATION
# -------------------------------------------------------------
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
# 4. TRADE HISTORY MANAGER
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
            logging.error(f"Error saving history: {e}")

    def get_record(self, date_str):
        try:
            with open(self.filename, 'r') as f:
                data = json.load(f)
                return data.get(date_str)
        except Exception as e:
            logging.error(f"Error reading history: {e}")
            return None

history_manager = TradeHistoryManager()

# -------------------------------------------------------------
# 5. DYNAMIC NIFTY FUT EXPIRY & TOKEN FETCHER
# -------------------------------------------------------------
def get_nifty_fut_tokens():
    try:
        url = "https://margincalculator.angelbroking.com/OpenAPI_Standard_OpenAPI_MasterData/OpenAPIScripMaster.json"
        response = requests.get(url, timeout=10)
        data = response.json()
        
        nifty_futs = [
            item for item in data 
            if item.get('name') == 'NIFTY' and item.get('instrumenttype') == 'FUTIDX' and item.get('exch_seg') == 'NFO'
        ]
        
        nifty_futs.sort(key=lambda x: datetime.strptime(x['expiry'], '%d%b%Y'))
        
        current_month_fut = nifty_futs[0]
        next_month_fut = nifty_futs[1]
        
        return current_month_fut, next_month_fut
    except Exception as e:
        logging.error(f"Error fetching master tokens: {e}")
        return None, None

def is_authorized(update: Update):
    return str(update.effective_user.id) == str(AUTHORIZED_CHAT_ID)

def reset_trade_specific_flags():
    global manual_mode_t1, manual_mode_t2
    manual_mode_t1 = False
    manual_mode_t2 = False

# -------------------------------------------------------------
# 6. ANGEL ONE LOGIN, PNL & BASKET EXECUTION
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

async def execute_hedged_nifty_basket(smart_obj, account_type):
    if not smart_obj: return False, "Session Inactive"
    try:
        curr_fut, next_fut = get_nifty_fut_tokens()
        if not curr_fut or not next_fut:
            return False, "Failed to fetch Future Tokens"

        lot_size = str(curr_fut.get('lotsize', '65'))

        if account_type == "Phone_A":
            basket_orders = [
                {
                    "variety": "NORMAL", "tradingsymbol": curr_fut['symbol'], "symboltoken": curr_fut['token'],
                    "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                    "producttype": "CARRYFORWARD", "duration": "DAY", "price": "0", "quantity": lot_size
                },
                {
                    "variety": "NORMAL", "tradingsymbol": next_fut['symbol'], "symboltoken": next_fut['token'],
                    "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET",
                    "producttype": "CARRYFORWARD", "duration": "DAY", "price": "0", "quantity": lot_size
                }
            ]

        elif account_type == "Phone_B":
            basket_orders = [
                {
                    "variety": "NORMAL", "tradingsymbol": next_fut['symbol'], "symboltoken": next_fut['token'],
                    "transactiontype": "BUY", "exchange": "NFO", "ordertype": "MARKET",
                    "producttype": "CARRYFORWARD", "duration": "DAY", "price": "0", "quantity": lot_size
                },
                {
                    "variety": "NORMAL", "tradingsymbol": curr_fut['symbol'], "symboltoken": curr_fut['token'],
                    "transactiontype": "SELL", "exchange": "NFO", "ordertype": "MARKET",
                    "producttype": "CARRYFORWARD", "duration": "DAY", "price": "0", "quantity": lot_size
                }
            ]

        response = smart_obj.placeBasketOrder(basket_orders)
        if response and response.get('status'):
            return True, f"Basket Executed for {account_type}"
        else:
            for order in basket_orders:
                smart_obj.placeOrder(order)
            return True, f"Sequential Orders Placed for {account_type}"

    except Exception as e:
        return False, f"Execution Failed: {str(e)}"

# -------------------------------------------------------------
# 7. NON-BLOCKING CUSTOM SAFE EXIT LOGIC
# -------------------------------------------------------------
async def smart_limit_exit_all(smart_obj, account_name):
    if not smart_obj: return False, "Session Inactive"
    try:
        pos_res = smart_obj.position()
        if not pos_res or not pos_res.get('data'):
            return True, f"All Positions Closed for {account_name} ✅"

        open_positions = [p for p in pos_res['data'] if int(p.get('netqty', 0)) != 0]
        if not open_positions:
            return True, f"All Positions Closed for {account_name} ✅"

        for pos in open_positions:
            net_qty = int(pos.get('netqty', 0))
            token = pos.get('symboltoken')
            exchange = pos.get('exchange', 'NFO')
            symbol = pos.get('tradingsymbol')
            product = pos.get('producttype')
            ltp = float(pos.get('ltp', 0))

            best_price = ltp
            market_data = smart_obj.getMarketData("FULL", {exchange: [token]})
            
            if market_data and market_data.get('status') and market_data.get('data'):
                fetched_item = market_data['data']['fetched'][0]
                depth = fetched_item.get('depth', {})
                fetched_ltp = float(fetched_item.get('ltp', ltp))
                if fetched_ltp > 0:
                    ltp = fetched_ltp

                if net_qty > 0:
                    buy_list = depth.get('buy', [{}])
                    if buy_list and buy_list[0].get('price'):
                        best_price = float(buy_list[0].get('price'))
                else:
                    sell_list = depth.get('sell', [{}])
                    if sell_list and sell_list[0].get('price'):
                        best_price = float(sell_list[0].get('price'))

            gap = abs(ltp - best_price)
            
            if gap > 5.0:
                logging.info(f"Skipping Exit for {account_name}: Gap Rs.{gap:.2f} > Rs.5.0")
                return False, f"Gap Rs.{gap:.2f} is wider than Rs.5"

            tx_type = "SELL" if net_qty > 0 else "BUY"

            order_params = {
                "variety": "NORMAL",
                "tradingsymbol": symbol,
                "symboltoken": token,
                "transactiontype": tx_type,
                "exchange": exchange,
                "ordertype": "LIMIT",
                "producttype": product,
                "duration": "DAY",
                "price": str(best_price),
                "quantity": str(abs(net_qty))
            }
            
            order_res = smart_obj.placeOrder(order_params)
            order_id = order_res.get('data', {}).get('orderid') if order_res else None

            await asyncio.sleep(2)

            if order_id:
                try:
                    order_book = smart_obj.orderBook()
                    if order_book and order_book.get('data'):
                        for ord_item in order_book['data']:
                            if ord_item.get('orderid') == order_id:
                                status = ord_item.get('status', '').upper()
                                if status in ['VALIDATION PENDING', 'OPEN', 'PENDING']:
                                    smart_obj.cancelOrder(order_id, "NORMAL")
                                    logging.info(f"Pending order {order_id} cancelled.")
                                    return False, "Order timed out and was cancelled"
                except Exception as ex:
                    logging.error(f"Error checking order: {ex}")

        pos_check = smart_obj.position()
        if pos_check and pos_check.get('data'):
            remaining = [p for p in pos_check['data'] if int(p.get('netqty', 0)) != 0]
            if not remaining:
                return True, f"All Positions Closed for {account_name} ✅"

        return False, "Positions still open"

    except Exception as e:
        logging.error(f"Custom Exit Error: {str(e)}")
        return False, f"Exit Error: {str(e)}"

# Helper to send Non-blocking Telegram Messages in background
def send_telegram_async(context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    async def _send():
        try:
            await context.bot.send_message(
                chat_id=AUTHORIZED_CHAT_ID,
                text=text,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        except Exception as e:
            logging.error(f"Async Telegram Send Error: {e}")
    asyncio.create_task(_send())

# -------------------------------------------------------------
# 8. NON-BLOCKING MONITORING LOOP (0.3s Interval + 30s Anti-Spam)
# -------------------------------------------------------------
async def monitor_pnl(context: ContextTypes.DEFAULT_TYPE):
    global is_monitoring, active_positions, current_target_1, current_target_2
    global manual_mode_t1, manual_mode_t2
    
    first_target_hit = False
    first_hit_account = None

    t1_alert_msg_id = None
    t1_alert_count = 0
    last_telegram_t1_time = 0

    t2_alert_msg_id = None
    t2_alert_count = 0
    last_telegram_t2_time = 0

    is_exiting_t1 = False
    is_exiting_t2 = False

    while is_monitoring:
        try:
            pnl_a = get_account_pnl(smart_a) if active_positions["Phone_A"] else 0.0
            pnl_b = get_account_pnl(smart_b) if active_positions["Phone_B"] else 0.0

            # ---------------------------------------------------------
            # PHASE 1: TARGET 1 MONITORING (Strict Positive Profit >= 1500)
            # ---------------------------------------------------------
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

                if target_hit_acc and not is_exiting_t1:
                    current_time = time.time()
                    
                    if (current_time - last_telegram_t1_time) >= 30 or t1_alert_msg_id is None:
                        t1_alert_count += 1
                        last_telegram_t1_time = current_time
                        
                        alert_text = (
                            f"🎯 *TARGET 1 TRIGGERED in {target_hit_acc}!*\n"
                            f"🔔 *Notification #{t1_alert_count}*\n\n"
                            f"💰 *Current P&L:* ₹{target_pnl:,.2f}\n"
                            f"⏳ *Attempting Safe Exit (Gap Limit Rs. 0-5)...*"
                        )

                        if t1_alert_msg_id:
                            try:
                                await context.bot.edit_message_text(
                                    chat_id=AUTHORIZED_CHAT_ID,
                                    message_id=t1_alert_msg_id,
                                    text=alert_text,
                                    parse_mode="Markdown"
                                )
                            except Exception:
                                pass
                        else:
                            msg = await context.bot.send_message(
                                chat_id=AUTHORIZED_CHAT_ID,
                                text=alert_text,
                                parse_mode="Markdown"
                            )
                            t1_alert_msg_id = msg.message_id

                    if manual_mode_t1:
                        send_telegram_async(context, f"🔔 *Manual Mode Active for Target 1!* Please exit manually.")
                    else:
                        is_exiting_t1 = True

                        async def exit_worker_t1(s_obj, a_name, a_key):
                            nonlocal is_exiting_t1, first_target_hit, first_hit_account
                            success, msg_txt = await smart_limit_exit_all(s_obj, a_name)
                            if success:
                                active_positions[a_key] = False
                                first_target_hit = True
                                first_hit_account = a_name
                                other_acc = "Phone B" if a_name == "Phone A" else "Phone A"
                                
                                keyboard = [[
                                    InlineKeyboardButton("✏️ Custom 2nd Target", callback_data="change_target_2"),
                                    InlineKeyboardButton("🚨 Exit All Positions", callback_data="confirm_exit_all")
                                ]]
                                
                                success_text = (
                                    f"✅ *Target 1 EXECUTED & CLOSED for {a_name}!*\n\n"
                                    f"🔄 *{other_acc}* is now active for Target 2 (₹{current_target_2})."
                                )
                                send_telegram_async(context, success_text, InlineKeyboardMarkup(keyboard))
                            is_exiting_t1 = False

                        asyncio.create_task(exit_worker_t1(smart_target, target_hit_acc, acc_key))

            # ---------------------------------------------------------
            # PHASE 2: TARGET 2 MONITORING (Supports Profit or Stop-loss)
            # ---------------------------------------------------------
            else:
                rem_key = "Phone_B" if first_hit_account == "Phone A" else "Phone_A"
                rem_name = "Phone B" if first_hit_account == "Phone A" else "Phone A"
                rem_smart = smart_b if rem_key == "Phone_B" else smart_a
                rem_pnl = pnl_b if rem_key == "Phone_B" else pnl_a

                t2_triggered = False
                if current_target_2 >= 0 and rem_pnl >= current_target_2:
                    t2_triggered = True
                elif current_target_2 < 0 and rem_pnl <= current_target_2:
                    t2_triggered = True

                if active_positions[rem_key] and t2_triggered and not is_exiting_t2:
                    current_time = time.time()
                    if (current_time - last_telegram_t2_time) >= 30 or t2_alert_msg_id is None:
                        t2_alert_count += 1
                        last_telegram_t2_time = current_time
                        
                        t2_text = (
                            f"🎯 *TARGET 2 TRIGGERED in {rem_name}!*\n"
                            f"🔔 *Notification #{t2_alert_count}*\n\n"
                            f"💰 *Current P&L:* ₹{rem_pnl:,.2f}\n"
                            f"⏳ *Attempting Safe Exit...*"
                        )

                        if t2_alert_msg_id:
                            try:
                                await context.bot.edit_message_text(
                                    chat_id=AUTHORIZED_CHAT_ID,
                                    message_id=t2_alert_msg_id,
                                    text=t2_text,
                                    parse_mode="Markdown"
                                )
                            except Exception:
                                pass
                        else:
                            msg = await context.bot.send_message(
                                chat_id=AUTHORIZED_CHAT_ID,
                                text=t2_text,
                                parse_mode="Markdown"
                            )
                            t2_alert_msg_id = msg.message_id

                    if manual_mode_t2:
                        send_telegram_async(context, f"🔔 *Manual Mode Active for Target 2!* Please exit manually.")
                    else:
                        is_exiting_t2 = True

                        async def exit_worker_t2(r_smart, r_name, r_key):
                            nonlocal is_exiting_t2
                            global is_monitoring
                            success, msg_txt = await smart_limit_exit_all(r_smart, r_name)
                            if success:
                                active_positions[r_key] = False
                                is_monitoring = False
                                reset_trade_specific_flags()

                                final_pnl_a = get_account_pnl(smart_a) if smart_a else 0.0
                                final_pnl_b = get_account_pnl(smart_b) if smart_b else 0.0
                                total_brokerage = ESTIMATED_BROKERAGE_PER_ORDER * 2
                                net_pnl = (final_pnl_a + final_pnl_b) - total_brokerage

                                today_date = datetime.now().strftime("%d-%m-%Y")
                                history_manager.save_record(today_date, final_pnl_a, final_pnl_b, total_brokerage, net_pnl)

                                summary_text = (
                                    f"🎉 *Trade Complete Summary*\n\n"
                                    f"• *Phone A:* ₹{final_pnl_a:,.2f}\n"
                                    f"• *Phone B:* ₹{final_pnl_b:,.2f}\n"
                                    f"• *Brokerage:* -₹{total_brokerage:,.2f}\n"
                                    f"─────────────────\n"
                                    f"💰 *Net PnL:* ₹{net_pnl:,.2f}"
                                )
                                send_telegram_async(context, summary_text)
                            is_exiting_t2 = False

                        asyncio.create_task(exit_worker_t2(rem_smart, rem_name, rem_key))

            await asyncio.sleep(0.3)

        except Exception as e:
            logging.error(f"Error in Monitoring Loop: {str(e)}")
            await asyncio.sleep(0.5)

# -------------------------------------------------------------
# 9. COMMANDS & MESSAGES
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

    if text in ["deactive", "deactivate", "डीएक्टिव"]:
        is_api_active = False
        await update.message.reply_text("🚫 *API System Deactivated!*", parse_mode="Markdown")
        return

    if text in ["active", "activate", "एक्टिव"]:
        is_api_active = True
        await update.message.reply_text("✅ *API System Activated!*", parse_mode="Markdown")
        return

    if text in ["manual 1", "manual1"]:
        manual_mode_t1 = True
        await update.message.reply_text("⚠️ *Manual Mode for Target 1 Active!*", parse_mode="Markdown")
        return

    if text in ["manual 2", "manual2"]:
        manual_mode_t2 = True
        await update.message.reply_text("⚠️ *Manual Mode for Target 2 Active!*", parse_mode="Markdown")
        return

    if user_state.get("awaiting_t2"):
        try:
            val = float(text)
            current_target_2 = val
            user_state["awaiting_t2"] = False
            await update.message.reply_text(f"✅ *Target 2 Updated to ₹{current_target_2}!*", parse_mode="Markdown")
            return
        except ValueError:
            await update.message.reply_text("❌ कृपया सही नंबर दर्ज करें।")
            return

    if text == "pnl":
        if not smart_a and not smart_b:
            await update.message.reply_text("ℹ️ *Market/Session Closed.*", parse_mode="Markdown")
            return
        pnl_a = get_account_pnl(smart_a)
        pnl_b = get_account_pnl(smart_b)
        await update.message.reply_text(
            f"📊 *Live P&L Summary*\n\n• *Phone A:* ₹{pnl_a:,.2f}\n• *Phone B:* ₹{pnl_b:,.2f}\n─────────────────\n💰 *Total:* ₹{pnl_a + pnl_b:,.2f}",
            parse_mode="Markdown"
        )
        return

    if text.startswith("history "):
        target_date = update.message.text.strip().split(" ")[1]
        record = history_manager.get_record(target_date)
        if record:
            await update.message.reply_text(
                f"📅 *History ({target_date})*\n\n• *Phone A:* ₹{record['phone_a']:,.2f}\n• *Phone B:* ₹{record['phone_b']:,.2f}\n• *Brokerage:* -₹{record['brokerage']:,.2f}\n─────────────────\n💰 *Net Profit:* ₹{record['net_total']:,.2f}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(f"❌ *No record found for {target_date}*", parse_mode="Markdown")
        return

    if not is_api_active:
        await update.message.reply_text("⚠️ System is *DEACTIVATED*.", parse_mode="Markdown")
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
        await query.edit_message_text("⏳ *Executing Hedged Nifty Baskets...*", parse_mode="Markdown")
        reset_trade_specific_flags()

        res_a, msg_a = await execute_hedged_nifty_basket(smart_a, "Phone_A")
        res_b, msg_b = await execute_hedged_nifty_basket(smart_b, "Phone_B")

        if res_a and res_b:
            active_positions["Phone_A"] = True
            active_positions["Phone_B"] = True
            is_monitoring = True
            asyncio.create_task(monitor_pnl(context))

            keyboard = [[InlineKeyboardButton("🚨 EXIT ALL POSITIONS", callback_data="confirm_exit_all")]]
            await query.edit_message_text(
                text=f"🎉 *Hedged Nifty Baskets Executed!*\n\n🎯 *T1:* ₹{current_target_1} | *T2:* ₹{current_target_2}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.edit_message_text(f"❌ *Execution Failed:*\n\n• Phone A: {msg_a}\n• Phone B: {msg_b}")

    elif query.data == "change_target_2":
        user_state["awaiting_t2"] = True
        await query.message.reply_text("✏️ *Target 2 का नया Amount दर्ज करें:*", parse_mode="Markdown")

    elif query.data == "confirm_exit_all":
        is_monitoring = False
        await query.edit_message_text("⏳ *Closing all trades...*", parse_mode="Markdown")
        
        res_a, msg_a = await smart_limit_exit_all(smart_a, "Phone A")
        res_b, msg_b = await smart_limit_exit_all(smart_b, "Phone B")

        active_positions["Phone_A"] = False
        active_positions["Phone_B"] = False
        reset_trade_specific_flags()

        await query.message.reply_text(f"🛑 *Trade Closed Status:*\n\n• *Phone A:* {msg_a}\n• *Phone B:* {msg_b}", parse_mode="Markdown")

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
    print("Trading Bot is running with Non-Blocking Async Architecture...")
    app.run_polling()

if __name__ == "__main__":
    main()
