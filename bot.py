from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Premium Bot Online\n\nSend me a photo to get its file_id."
    )

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = update.message.photo[-1].file_id

    await update.message.reply_text(
        f"PHOTO FILE ID:\n\n{file_id}"
    )

app = Application.builder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

print("Bot Started...")
app.run_polling()
