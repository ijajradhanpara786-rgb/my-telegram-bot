# -------------------------------------------------------------
# 9. COMMAND HANDLERS & SECURITY
# -------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return

    keyboard = [
        [InlineKeyboardButton("🚀 Start Trade", callback_data="confirm_start_yes"), InlineKeyboardButton("📊 Status", callback_data="get_status")],
        [InlineKeyboardButton("🎯 Set Target 1", callback_data="ask_t1_input"), InlineKeyboardButton("🎯 Set Target 2", callback_data="ask_t2_input")],
        [InlineKeyboardButton("🚨 Exit All Positions", callback_data="confirm_exit_all")]
    ]
    
    welcome_msg = (
        "🤖 *Angel One Trading Control Panel*\n\n"
        f"🎯 *Target 1:* ₹{current_target_1}\n"
        f"🎯 *Target 2:* ₹{current_target_2}\n"
        f"📡 *API System Status:* {'Active' if is_api_active else 'Deactivated'}\n"
        f"🔄 *Monitoring Running:* {is_monitoring}"
    )
    
    await update.message.reply_text(
        text=welcome_msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update): return

    if not smart_a and not smart_b:
        await update.message.reply_text("ℹ️ *Sessions Inactive.* (Run /start to log in)", parse_mode="Markdown")
        return

    pnl_a = get_account_pnl(smart_a)
    pnl_b = get_account_pnl(smart_b)
    total_pnl = pnl_a + pnl_b

    status_text = (
        "📊 *Live Account P&L & Position Status*\n\n"
        f"📱 *Phone A:* ₹{pnl_a:,.2f} | Active: `{active_positions['Phone_A']}`\n"
        f"📱 *Phone B:* ₹{pnl_b:,.2f} | Active: `{active_positions['Phone_B']}`\n"
        f"─────────────────\n"
        f"💰 *Total P&L:* ₹{total_pnl:,.2f}\n"
        f"📡 *Monitoring:* `{is_monitoring}`"
    )
    await update.message.reply_text(status_text, parse_mode="Markdown")

# -------------------------------------------------------------
# 10. CALLBACK QUERY HANDLER (INLINE BUTTON ACTIONS)
# -------------------------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global smart_a, smart_b, is_monitoring, active_positions, user_state
    
    if not is_authorized(update): return
    if not is_api_active: 
        await update.callback_query.answer("⚠️ System is DEACTIVATED!", show_alert=True)
        return

    query = update.callback_query
    await query.answer()

    if query.data == "get_status":
        pnl_a = get_account_pnl(smart_a) if smart_a else 0.0
        pnl_b = get_account_pnl(smart_b) if smart_b else 0.0
        await query.message.reply_text(
            f"📊 *Live P&L*\n\n• Phone A: ₹{pnl_a:,.2f}\n• Phone B: ₹{pnl_b:,.2f}\n• Total: ₹{pnl_a+pnl_b:,.2f}",
            parse_mode="Markdown"
        )

    elif query.data == "confirm_start_yes":
        await query.edit_message_text("🔄 *Logging into Angel One Accounts...*", parse_mode="Markdown")
        smart_a = login_angel_one(PHONE_A)
        smart_b = login_angel_one(PHONE_B)

        if smart_a and smart_b:
            index_keyboard = [[InlineKeyboardButton("🚀 Confirm & Run Nifty Basket", callback_data="confirm_run_nifty")]]
            await query.edit_message_text(
                text="✅ *Angel One Logins Successful!*\n\nExecute Nifty Hedged Basket for Phone A & Phone B?",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(index_keyboard)
            )
        else:
            await query.edit_message_text("❌ *Login Failed.* Please check API keys / TOTP secrets.")

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
                text=f"🎉 *Hedged Nifty Baskets Executed!*\n\n🎯 *T1 Target:* ₹{current_target_1} | *T2 Target:* ₹{current_target_2}\n📡 *Monitoring Started...*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.edit_message_text(f"❌ *Execution Failed:*\n\n• Phone A: {msg_a}\n• Phone B: {msg_b}")

    elif query.data == "ask_t1_input":
        user_state["awaiting_t1"] = True
        await query.message.reply_text("✏️ *Target 1 (T1) का नया Value दर्ज करें:*", parse_mode="Markdown")

    elif query.data == "ask_t2_input" or query.data == "change_target_2":
        user_state["awaiting_t2"] = True
        await query.message.reply_text("✏️ *Target 2 (T2) का नया Value दर्ज करें:*", parse_mode="Markdown")

    elif query.data == "confirm_exit_all":
        is_monitoring = False
        await query.edit_message_text("⏳ *Closing all trades via Safe Limit Exit...*", parse_mode="Markdown")
        
        res_a, msg_a = await smart_limit_exit_all(smart_a, "Phone A")
        res_b, msg_b = await smart_limit_exit_all(smart_b, "Phone B")

        active_positions["Phone_A"] = False
        active_positions["Phone_B"] = False
        reset_trade_specific_flags()

        await query.message.reply_text(f"🛑 *Trade Closed Status:*\n\n• *Phone A:* {msg_a}\n• *Phone B:* {msg_b}", parse_mode="Markdown")

    elif query.data == "confirm_cancel":
        await query.edit_message_text("❌ *Operation Cancelled.*", parse_mode="Markdown")

# -------------------------------------------------------------
# 11. CUSTOM TEXT & TARGET VALUE INPUT HANDLER
# -------------------------------------------------------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_api_active, current_target_1, current_target_2
    global manual_mode_t1, manual_mode_t2, user_state

    if not is_authorized(update): return

    text = update.message.text.strip().lower()

    if text in ["deactive", "deactivate"]:
        is_api_active = False
        await update.message.reply_text("🚫 *API System Deactivated!*", parse_mode="Markdown")
        return

    if text in ["active", "activate"]:
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

    if user_state.get("awaiting_t1"):
        try:
            val = float(text)
            current_target_1 = val
            user_state["awaiting_t1"] = False
            await update.message.reply_text(f"✅ *Target 1 Updated to ₹{current_target_1}!*", parse_mode="Markdown")
            return
        except ValueError:
            await update.message.reply_text("❌ कृपया सही नंबर दर्ज करें।")
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
        await status_command(update, context)
        return

# -------------------------------------------------------------
# 12. MAIN BOT APPLICATION BUILDER & STARTUP
# -------------------------------------------------------------
def main():
    app_bot = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Handlers Registration
    app_bot.add_handler(CommandHandler("start", start_command))
    app_bot.add_handler(CommandHandler("status", status_command))
    app_bot.add_handler(CallbackQueryHandler(button_handler))
    app_bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))
    
    print("Trading Bot is running with Flask Port binding...")
    app_bot.run_polling()

if __name__ == "__main__":
    main()
