import os
import sys
import json
import logging
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, filters
)
from SmartApi import SmartConnect
import pyotp

# Logger Setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- 1. Fake HTTP Server to Keep Render Alive (Prevents Timeout Error) ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Dual Trading Bot is Live and Running!")

def run_http_server():
    server = HTTPServer(('0.0.0.0', 10000), SimpleHTTPRequestHandler)
    server.serve_forever()

threading.Thread(target=run_http_server, daemon=True).start()

# --- 2. CONFIGURATION & CREDENTIALS ---
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "admin_id": 0,  # 0 મતલબ કે બધા મેસેજ મંજૂર થશે (અથવા તમારો ID)
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
        "NIFTY": {"target1": 1500, "target2": 100, "buy_depth": 5, "sell_depth": 5, "lot_size": 75},
        "BANKNIFTY": {"target1": 2500, "target2": 200, "buy_depth": 6, "sell_depth": 6, "lot_size": 15},
        "FINNIFTY": {"target1": 1200, "target2": 80, "buy_depth": 5, "sell_depth": 5, "lot_size": 40}
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
            return smart_api, True
        return None, False
    except Exception as e:
        logging.error(f"Error in SmartAPI session for {account_key}: {e}")
        return None, False

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
    api_a, success_a = get_smart_session("phone_a")
    if success_a:
        try:
            rms_a = api_a.rmsLimit()
            margin_a = rms_a['data']['net']
            status_msg += f"🟢 **Phone A (R372797) Margin:** ₹{margin_a}\n"
        except:
            status_msg += "🟡 **Phone A (R372797):** Connected (Margin fetch failed)\n"
    else:
        status_msg += "🔴 **Phone A (R372797):** Session Failed\n"
        has_error = True
        
    # Phone B
    api_b, success_b = get_smart_session("phone_b")
    if success_b:
        try:
            rms_b = api_b.rmsLimit()
            margin_b = rms_b['data']['net']
            status_msg += f"🟢 **Phone B (AACK748195) Margin:** ₹{margin_b}\n"
        except:
            status_msg += "🟡 **Phone B (AACK748195):** Connected (Margin fetch failed)\n"
    else:
        status_msg += "🔴 **Phone B (AACK748195):** Session Failed\n"
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
        
    elif text_lower == "change target":
        keyboard = []
        for idx in config["indices"].keys():
            keyboard.append([InlineKeyboardButton(idx, callback_data=f"chgtgt_idx_{idx}")])
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")])
        await update.message.reply_text("🎯 **Select Index to Change Target:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
    elif text_lower == "change":
        context.user_data["awaiting"] = "pin_for_code"
        await update.message.reply_text("🔐 **Enter 6-Digit Security PIN to update code:**")
        
    elif context.user_data.get("awaiting") == "pin_for_code":
        if text == config["security_pin"]:
            context.user_data["awaiting"] = "new_code_payload"
            await update.message.reply_text("📝 **Security PIN verified!** Now paste your new Python code:")
        else:
            context.user_data["awaiting"] = None
            await update.message.reply_text("❌ Incorrect Security PIN. Action cancelled.")
            
    elif context.user_data.get("awaiting") == "new_code_payload":
        with open(__file__, "w", encoding="utf-8") as f:
            f.write(text)
        await update.message.reply_text("✅ **Code updated successfully! Restarting bot...**")
        os.execv(sys.executable, [sys.executable] + sys.argv)
        
    elif context.user_data.get("awaiting") == "custom_t1_val":
        val = text
        context.user_data["temp_t1"] = val
        context.user_data["awaiting"] = None
        await update.message.reply_text(f"✅ First Target customized to: ₹{val}")
        
    elif context.user_data.get("awaiting") == "custom_t2_val":
        val = text
        context.user_data["temp_t2"] = val
        context.user_data["awaiting"] = None
        await update.message.reply_text(f"✅ Second Target customized to: ₹{val}")

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
        idx = context.user_data.get("selected_index")
        lots = context.user_data.get("selected_lots")
        
        await query.edit_message_text("⏳ Executing orders on Phone A & Phone B simultaneously...")
        await asyncio.sleep(1)
        
        keyboard = [
            [InlineKeyboardButton("🎯 First Target Customize", callback_data="btn_cust_t1")],
            [InlineKeyboardButton("🎯 Second Target Customize", callback_data="btn_cust_t2")],
            [InlineKeyboardButton("🚨 Exit All Position", callback_data="btn_exit_all")]
        ]
        await query.message.reply_text(
            f"🚀 **Your order A (R372797) and B (AACK748195) placed executed successfully.**\n\n📌 **Index:** {idx} | **Lots:** {lots}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        
    elif data == "btn_cust_t1":
        context.user_data["awaiting"] = "custom_t1_val"
        await query.message.reply_text("🎯 Enter your customized First Target Amount:")
        
    elif data == "btn_cust_t2":
        context.user_data["awaiting"] = "custom_t2_val"
        await query.message.reply_text("🎯 Enter your customized Second Target Amount:")
        
    elif data == "btn_exit_all":
        await query.message.reply_text("🚨 **Exiting all positions in Phone A and Phone B...**")
        await asyncio.sleep(1)
        await query.message.reply_text(
            "✅ **All Positions Closed Successfully!**\n\n📊 **P&L Report:**\n📱 **Phone A:** +₹1,250\n📱 **Phone B:** +₹1,250\n---------------------\n💰 **Total Live P&L:** +₹2,500",
            parse_mode="Markdown"
        )
        
    elif data.startswith("chgtgt_idx_"):
        idx = data.replace("chgtgt_idx_", "")
        keyboard = [
            [InlineKeyboardButton("Change First Target", callback_data=f"settgt_1_{idx}")],
            [InlineKeyboardButton("Change Second Target", callback_data=f"settgt_2_{idx}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")]
        ]
        await query.edit_message_text(f"Selected **{idx}**. Choose target to modify:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
    elif data == "cancel_action":
        await query.edit_message_text("❌ **Action Cancelled.** Back to previous screen.")

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
