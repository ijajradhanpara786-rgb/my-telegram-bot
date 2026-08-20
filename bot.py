import asyncio
from telethon import TelegramClient, events

# API Details
API_ID = 37534041
API_HASH = '32fa9b9aecdff1ad567e236bf677f8ef'
BOT_TOKEN = '8563018898:AAEFBuFkA3_p7cjiM9WrR83sqM7CCB7MMAQ'

# Telethon Bot Instance
bot = TelegramClient('bot_session', API_ID, API_HASH)

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.respond('Master Bot Online Hai!')

async def main():
    print("Bot is starting...")
    await bot.start(bot_token=BOT_TOKEN)
    print("Bot is Live and Running!")
    await bot.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
