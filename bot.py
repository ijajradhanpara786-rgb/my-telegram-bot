import os
import asyncio
from telethon import TelegramClient, events

# आपकी API जानकारी
API_ID = 37534041
API_HASH = '32fa9b9aecdff1ad567e236bf677f8ef'
BOT_TOKEN = '8563018898:AAEFBuFkA3_p7cjiM9WrR83sqM7CCB7MMAQ'

# यहाँ अपने सारे Telegram Numbers जोड़ें (कोमा लगाकर)
ACCOUNTS = [
    '+919328113549',
    # बाकी नंबर यहाँ '+91...' करके जोड़ते जाएँ
]

print("Bot is starting...")
