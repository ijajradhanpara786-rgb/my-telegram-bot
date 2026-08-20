import os
import asyncio
from telethon import TelegramClient, events

API_ID = 37534041
API_HASH = '32fa9b9aecdff1ad567e236bf677f8ef'
BOT_TOKEN = '8563018898:AAEFBuFkA3_p7cjiM9WrR83sqM7CCB7MMAQ'

# Telegram Numbers List
ACCOUNTS = [
    "+919328113549",
    # Baaki 7 number yahan add ho jayenge
]

bot = TelegramClient('central_master_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

@bot.on(events.NewMessage(pattern='/start'))
async def start_cmd(event):
    await event.respond("🤖 **Master Control Bot Active!**\n\nCommands:\n/status - Check Accounts\n/deactivate - Stop Accounts")

@bot.on(events.NewMessage(pattern='/status'))
async def status_cmd(event):
    await event.respond(f"📊 Connected Accounts: {len(ACCOUNTS)}")

@bot.on(events.NewMessage(pattern='/deactivate'))
async def deactivate_cmd(event):
    await event.respond("🔴 Sabhi accounts deactivate kar diye gaye hain.")

print("Master Bot is running...")
bot.run_until_disconnected()
