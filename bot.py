from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")

PHOTO_ID = "AgACAgUAAxkBAAICYGqawsPSsd-rVZF8QNyGGavXiRnYAAJ0FGsbNXDQVB25ko4WD9yEAQADAgADeAADPQQ"

START_TEXT = """
✨ Welcome to Premium Access Hub ✨

🔥 Buy Premium Groups in just ₹50!

📂 Resources:
https://t.me/+h7qBjBXj13djMWI1
https://t.me/+bxjfe4zWwqQ4ZjY0

💎 Features:
• ♾️ Lifetime Permanent Access
• 📁 All Premium Categories included
• 🚀 Instant delivery after verification

✨ One-time payment, enjoy forever!
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = [
        [InlineKeyboardButton("🛒 Buy Premium", callback_data="buy")],
        [InlineKeyboardButton("❓ How To Buy", callback_data="how")],
        [InlineKeyboardButton("🆘 Admin Support", url="https://t.me/Vidsell6")]
    ]

    await update.message.reply_photo(
        photo=PHOTO_ID,
        caption=START_TEXT,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    if query.data == "buy":
        await query.message.reply_text(
            "💳 Premium Purchase\n\n₹50 Lifetime Access\n\nPayment system coming in next update."
        )

    elif query.data == "how":
        await query.message.reply_text(
            "🎥 How To Buy video will be added soon."
        )

app = Application.builder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(buttons))

print("Bot Started...")
app.run_polling()
