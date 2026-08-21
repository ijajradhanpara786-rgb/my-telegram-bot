import os
import sys
import json
import logging
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, filters
)
from SmartApi import SmartConnect
import pyotp

# Logger Setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- 1. HTTP Server to Keep Render Alive ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Dual Trading Bot is Live and Running!")

def run_http_server():
    server = HTTPServer(('0.0.0.0', 10000), SimpleHTTPRequestHandler)
    server.serve_forever()

threading.Thread(target=run_http_server, daemon=True).start()

# --- 2. CONFIGURATION ---
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "admin_id": 0,
    "security_pin": "500947",
    "is_active": True,
    "accounts": {
        "phone_a": {
            "api_key": "t1kFsPtj",
            "client_code": "R372797",
            "password": "5009",
            "totp_secret": "UIN2PAMXKIVAQPMX22BM7PQYAA"
        },
        "phone_b": {
            "api_key": "UFcQVst3",
            "client_code": "AACK748195",
            "password": "0714",
            "totp_secret": "6JHGESTXWUUA226LCAFXOOOHAQ"
        }
    },
    "indices": {
        "NIFTY": {"target1": 1500, "target2": 100, "buy_depth": 5, "sell_depth": 5, "lot_size": 75, "symbol": "NIFTY", "token": "99926000"},
        "BANKNIFTY": {"target1": 2500, "target2": 200, "buy_depth": 6, "sell_depth": 6, "lot_size": 15, "symbol": "BANKNIFTY", "token": "99926009"},
        "FINNIFTY": {"target1": 1200, "target2": 80, "buy_depth": 5, "sell_depth": 5, "lot_size": 40, "symbol": "FINNIFTY", "token": "99926037"}
    },
    "telegram_token": "8563018898:AAEFBuFkA3_p7cjiM9WrR83sqM7CCB7MMAQ"
}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        return DEFAULT_CONFIG
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

config = load_config()

# SmartAPI Session Helper
def get_smart_session(account_key):
    acc_info = config["accounts"][account_key]
    try:
        smart_api = SmartConnect(api_key=acc_info["api_key"])
        totp = pyotp.TOTP(acc_info["totp_secret"]).now()
        data = smart_api.generateSession(acc_info["client_code"], acc_info["password"], totp)
        if data and data.get("status"):
            jwt_token = data['data']['jwtToken']
            smart_api.setAccessToken(jwt_token)
            return smart_api, True, "Success"
        err_msg = data.get("message", "Session Generation Failed") if data else "No Response"
        return None, False, err_msg
    except Exception as e:
        logging.error(f"Error in SmartAPI session for {account_key}: {e}")
        return None, False, str(e)

def execute_angel_order(api, trading_symbol, symbol_token, qty, transaction_type="BUY"):
    try:
        orderparams = {
            "variety": "NORMAL",
            "tradingsymbol": trading_symbol,
            "symboltoken": symbol_token,
            "transactiontype": transaction_type,
            "exchange": "NFO",
            "ordertype": "MARKET",
            "producttype": "CARRYFORWARD",
            "duration": "DAY",
            "price": "0",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(qty)
        }
        res = api.placeOrder(orderparams)
        if isinstance(res, str):
            return True, res
        elif isinstance(res, dict) and res.get("status"):
            return True, res.get("data", {}).get("orderid", "Success")
        else:
            err = res.get("message", "Order Placement Failed") if isinstance(res, dict) else "Unknown Error"
            return False, err
    except Exception as e:
        return False, str(e)

def is_admin(update: Update):
    if config.get("admin_id") == 0:
        return True
    user = update.effective_user
    return user and user.id == config["admin_id"]

# --- COMMAND HANDLERS ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("🚫 Unauthorized Access Denied!")
        return
    
    keyboard = [
        [InlineKeyboardButton("✅ Yes / Confirm", callback_data="start_confirm")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]
    ]
    await update.message.reply_text(
        "⚠️ **You want to active today's trading?**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def handle_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text("🔍 Checking system status & account balances...")
    
    status_msg = "✅ **SYSTEM HEALTH STATUS:**\n\n"
    has_error = False
    
    # Phone A
    api_a, success_a, err_a = get_smart_session("phone_a")
    if success_a:
        try:
            rms_a = api_a.rmsLimit()
            margin_a = rms_a['data']['net']
            status_msg += f"🟢 **Phone A (R372797) Margin:** ₹{margin_a}\n"
        except Exception as e:
            status_msg += f"🟡 **Phone A (R372797):** Connected (Margin Error: {e})\n"
    else:
        status_msg += f"🔴 **Phone A (R372797):** Session Failed ({err_a})\n"
        has_error = True
        
    # Phone B
    api_b, success_b, err_b = get_smart_session("phone_b")
    if success_b:
        try:
            rms_b = api_b.rmsLimit()
            margin_b = rms_b['data']['net']
            status_msg += f"🟢 **Phone B (AACK748195) Margin:** ₹{margin_b}\n"
        except Exception as e:
            status_msg += f"🟡 **Phone B (AACK748195):** Connected (Margin Error: {e})\n"
    else:
        status_msg += f"🔴 **Phone B (AACK748195):** Session Failed ({err_b})\n"
        has_error = True
        
    if not has_error:
        status_msg += "\n🚀 **Your system is 100% working and ready to trade!**"
    else:
        status_msg += "\n⚠️ **SYSTEM PROBLEM DETECTED!** Check credentials or margin."
        
    await update.message.reply_text(status_msg, parse_mode="Markdown")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    text = update.message.text.strip()
    text_lower = text.lower()
    
    if text_lower == "deactive":
        config["is_active"] = False
        save_config(config)
        await update.message.reply_text("🔴 **BOT DEACTIVATED!** API controls stopped.")
        return
    elif text_lower == "active":
        config["is_active"] = True
        save_config(config)
        await update.message.reply_text("🟢 **BOT ACTIVATED!** API control is back online.")
        return
        
    if not config.get("is_active", True):
        await update.message.reply_text("⚠️ Bot is currently DEACTIVATED. Type `active` to enable API controls.")
        return
        
    if text_lower == "check":
        await handle_check(update, context)

# --- CALLBACK QUERY HANDLER ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "start_confirm":
        keyboard = []
        for idx in config["indices"].keys():
            keyboard.append([InlineKeyboardButton(idx, callback_data=f"sel_idx_{idx}")])
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")])
        
        await query.edit_message_text(
            "🟢 **Your setup is ready for trade.**\n\n🎯 **Select Your Index:**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        
    elif data.startswith("sel_idx_"):
        idx_name = data.replace("sel_idx_", "")
        context.user_data["selected_index"] = idx_name
        
        keyboard = [
            [InlineKeyboardButton("1 Lot", callback_data="sel_lot_1"), InlineKeyboardButton("2 Lots", callback_data="sel_lot_2")],
            [InlineKeyboardButton("5 Lots", callback_data="sel_lot_5"), InlineKeyboardButton("10 Lots", callback_data="sel_lot_10")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]
        ]
        await query.edit_message_text(
            f"📦 **{idx_name} Selected!**\n\nSelect Lot Size:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        
    elif data.startswith("sel_lot_"):
        lots = data.replace("sel_lot_", "")
        context.user_data["selected_lots"] = lots
        idx_name = context.user_data.get("selected_index", "NIFTY")
        keyboard = [
            [InlineKeyboardButton("✅ Confirm", callback_data="exec_trade_now")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]
        ]
        await query.edit_message_text(
            f"⚠️ **Confirm Your Selection:**\n\n📌 **Index:** {idx_name}\n📦 **Lots:** {lots} Lots",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        
    elif data == "exec_trade_now":
        idx = context.user_data.get("selected_index", "NIFTY")
        num_lots = int(context.user_data.get("selected_lots", 1))
        idx_data = config["indices"].get(idx, config["indices"]["NIFTY"])
        
        total_qty = idx_data["lot_size"] * num_lots
        
        await query.edit_message_text("⏳ Connecting to Angel One API & Placing Orders...")
        
        # Phone A
        api_a, success_a, err_a = get_smart_session("phone_a")
        if success_a:
            ok_a, res_a = execute_angel_order(api_a, idx_data["symbol"], idx_data["token"], total_qty)
            res_a_msg = f"🟢 Order ID: `{res_a}`" if ok_a else f"🔴 Failed: {res_a}"
        else:
            res_a_msg = f"🔴 Session Failed: {err_a}"

        # Phone B
        api_b, success_b, err_b = get_smart_session("phone_b")
        if success_b:
            ok_b, res_b = execute_angel_order(api_b, idx_data["symbol"], idx_data["token"], total_qty)
            res_b_msg = f"🟢 Order ID: `{res_b}`" if ok_b else f"🔴 Failed: {res_b}"
        else:
            res_b_msg = f"🔴 Session Failed: {err_b}"
            
        keyboard = [
            [InlineKeyboardButton("🎯 First Target Customize", callback_data="btn_cust_t1")],
            [InlineKeyboardButton("🎯 Second Target Customize", callback_data="btn_cust_t2")],
            [InlineKeyboardButton("🚨 Exit All Position", callback_data="btn_exit_all")]
        ]
        
        msg = f"📱 **ORDER EXECUTION REPORT:**\n\n"
        msg += f"📌 **Index:** {idx} | **Quantity:** {total_qty} ({num_lots} Lots)\n\n"
        msg += f"📱 **Phone A (R372797):** {res_a_msg}\n"
        msg += f"📱 **Phone B (AACK748195):** {res_b_msg}"
        
        await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
    elif data == "btn_exit_all":
        await query.message.reply_text("🚨 **Exiting all positions via Angel One API...**")
        
        idx = context.user_data.get("selected_index", "NIFTY")
        num_lots = int(context.user_data.get("selected_lots", 1))
        idx_data = config["indices"].get(idx, config["indices"]["NIFTY"])
        total_qty = idx_data["lot_size"] * num_lots
        
        api_a, success_a, _ = get_smart_session("phone_a")
        res_a_str = "Session Failed"
        if success_a:
            ok_a, res_a = execute_angel_order(api_a, idx_data["symbol"], idx_data["token"], total_qty, transaction_type="SELL")
            res_a_str = f"Success (ID: {res_a})" if ok_a else f"Failed: {res_a}"
            
        api_b, success_b, _ = get_smart_session("phone_b")
        res_b_str = "Session Failed"
        if success_b:
            ok_b, res_b = execute_angel_order(api_b, idx_data["symbol"], idx_data["token"], total_qty, transaction_type="SELL")
            res_b_str = f"Success (ID: {res_b})" if ok_b else f"Failed: {res_b}"
            
        await query.message.reply_text(
            f"✅ **Square-off Report:**\n\n📱 **Phone A:** {res_a_str}\n📱 **Phone B:** {res_b_str}", 
            parse_mode="Markdown"
        )
        
    elif data == "cancel_action":
        await query.edit_message_text("❌ **Action Cancelled.**")

# --- MAIN RUNNER ---
def main():
    cfg = load_config()
    token = cfg.get("telegram_token")
    
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    print("Bot is successfully starting on Render...")
    app.run_polling()

if __name__ == "__main__":
    main()
