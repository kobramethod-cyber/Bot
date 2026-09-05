import io
import logging
import os
from flask import Flask  # ADDED FOR UPTIMEROBOT
from threading import Thread  # ADDED FOR UPTIMEROBOT
from telegram import (
    InputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
import motor.motor_asyncio
import qrcode

# Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Environment Variables & Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
ADMIN_IDS = [1936430807, 8720701910]

# Default fallback configurations (will be overridden/initialized from MongoDB settings)
DEFAULT_SETTINGS = {
    "upi_id": "nagargoje12@ptyes",
    "price": 50,
    "group_link": "https://t.me/+H_y0MBr_c0g2ZDk1",
    "welcome_text": (
        "✨ Welcome to Premium Access Hub ✨\n\n"
        "🔥 Buy Premium Groups in just ₹{price}!\n\n"
        "📂 Resources:\n"
        "https://t.me/+h7qBjBXj13djMWI1\n"
        "https://t.me/+bxjfe4zWwqQ4ZjY0\n\n"
        "💎 Features:\n"
        "• ♾️ Lifetime Permanent Access\n"
        "• 📁 All Premium Categories included\n"
        "• 🚀 Instant delivery after verification\n\n"
        "✨ One-time payment, enjoy forever!"
    ),
    "support_username": "@Vidsell6",
}

WAITING_FOR_SCREENSHOT = 1
WAITING_FOR_BROADCAST = 2

# Admin Conversation States
(
    SETTING_UPI,
    SETTING_PRICE,
    SETTING_LINK,
    SETTING_WELCOME,
    SETTING_HOWTO,
    SETTING_SUPPORT,
) = range(10, 16)

# Initialize MongoDB via Motor
client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = client["premium_access_hub"]
users_col = db["users"]
purchases_col = db["purchases"]
settings_col = db["settings"]

# ADDED FOR UPTIMEROBOT: Flask app for keep-alive
app_flask = Flask(__name__)


@app_flask.route("/")
def home():
  return "Bot is alive!"


def run_flask():
  port = int(os.getenv("PORT", 8080))
  app_flask.run(host="0.0.0.0", port=port)


def keep_alive():
  t = Thread(target=run_flask)
  t.daemon = True
  t.start()


async def get_setting(key: str):
  doc = await settings_col.find_one({"key": key})
  if doc and "value" in doc:
    return doc["value"]
  return DEFAULT_SETTINGS.get(key)


async def set_setting(key: str, value):
  await settings_col.update_one({"key": key}, {"$set": {"value": value}}, upsert=True)


async def initialize_settings():
  for key, val in DEFAULT_SETTINGS.items():
    existing = await settings_col.find_one({"key": key})
    if not existing:
      await settings_col.insert_one({"key": key, "value": val})


def generate_upi_qr(upi_id: str, amount: int, name: str = "Desi Group"):
  upi_url = f"upi://pay?pa={upi_id}&pn={name}&am={amount}&cu=INR"
  qr = qrcode.QRCode(
      version=1,
      error_correction=qrcode.constants.ERROR_CORRECT_L,
      box_size=10,
      border=4,
  )
  qr.add_data(upi_url)
  qr.make(fit=True)
  img = qr.make_image(fill_color="black", back_color="white")
  bio = io.BytesIO()
  bio.name = "upi_qr.png"
  img.save(bio, "PNG")
  bio.seek(0)
  return bio


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  user = update.effective_user
  await users_col.update_one(
      {"user_id": user.id},
      {
          "$set": {
              "username": user.username or "No Username",
              "last_active": update.message.date,
          },
          "$setOnInsert": {"join_date": update.message.date},
      },
      upsert=True,
  )

  price = await get_setting("price")
  welcome_template = await get_setting("welcome_text")
  support_username = await get_setting("support_username")
  start_text = welcome_template.format(price=price)

  keyboard = [
      [InlineKeyboardButton("🛒 Buy Premium", callback_data="buy")],
      [
          InlineKeyboardButton("❓ How To Buy", callback_data="how"),
          InlineKeyboardButton("🆘 Admin Support", url=f"https://t.me/{support_username.lstrip('@')}"),
      ],
  ]

  if user.id in ADMIN_IDS:
    keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])

  await update.message.reply_photo(
      photo=PHOTO_ID if "PHOTO_ID" in globals() else "AgACAgUAAxkBAAICYGqawsPSsd-rVZF8QNyGGavXiRnYAAJ0FGsbNXDQVB25ko4WD9yEAQADAgADeAADPQQ",
      caption=start_text,
      reply_markup=InlineKeyboardMarkup(keyboard),
  )


async def admin_panel_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
  query = update.callback_query
  await query.answer()
  user = query.from_user

  if user.id not in ADMIN_IDS:
    await query.answer("Unauthorized!", show_alert=True)
    return

  admin_text = "👑 <b>ADMIN PANEL</b>\n\nChoose an action below:"
  keyboard = [
      [InlineKeyboardButton("📊 Stats", callback_data="admin_stats"), InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
      [InlineKeyboardButton("💳 Change UPI ID", callback_data="set_upi"), InlineKeyboardButton("💰 Change Price", callback_data="set_price")],
      [InlineKeyboardButton("🔗 Change Link", callback_data="set_link"), InlineKeyboardButton("📝 Change Welcome", callback_data="set_welcome")],
      [InlineKeyboardButton("🎥 Change HowTo Video", callback_data="set_howto_menu"), InlineKeyboardButton("🆘 Change Support", callback_data="set_support")],
      [InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")],
  ]

  await query.message.edit_caption(caption=admin_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
  query = update.callback_query
  await query.answer()
  user = query.from_user

  if query.data == "main_menu":
    price = await get_setting("price")
    welcome_template = await get_setting("welcome_text")
    support_username = await get_setting("support_username")
    start_text = welcome_template.format(price=price)

    keyboard = [
        [InlineKeyboardButton("🛒 Buy Premium", callback_data="buy")],
        [
            InlineKeyboardButton("❓ How To Buy", callback_data="how"),
            InlineKeyboardButton("🆘 Admin Support", url=f"https://t.me/{support_username.lstrip('@')}"),
        ],
    ]
    if user.id in ADMIN_IDS:
      keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])

    try:
      await query.message.edit_caption(caption=start_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    except Exception:
      await query.message.delete()
      await query.message.reply_photo(photo=PHOTO_ID, caption=start_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return ConversationHandler.END

  elif query.data == "admin_panel":
    if user.id not in ADMIN_IDS:
      await query.answer("Unauthorized!", show_alert=True)
      return ConversationHandler.END
    await admin_panel_menu(update, context)
    return ConversationHandler.END

  elif query.data == "buy":
    existing_pending = await purchases_col.find_one({"user_id": user.id, "status": "pending"})
    if existing_pending:
      await query.message.reply_text("⚠️ You already have a payment verification pending with admins.")
      return ConversationHandler.END

    current_upi = await get_setting("upi_id")
    current_price = await get_setting("price")

    qr_bio = generate_upi_qr(current_upi, current_price)
    payment_text = (
        "✦ <b>𝗣𝗥𝗘𝗠𝗜𝗨𝗠 𝗣𝗔𝗬𝗠𝗘𝗡𝗧</b>\n\n"
        "📦 Product: PREMIUM ACCESS\n"
        f"❐ Amount: ₹{current_price}\n"
        "❐ Validity: Lifetime\n\n"
        "────────────────────\n\n"
        "❐ <b>PAYMENT METHODS</b>\n\n"
        "Paytm • GPay • PhonePe • UPI\n\n"
        "UPI ID:\n"
        f"<b>{current_upi}</b>\n\n"
        "<b>AFTER PAYMENT:</b>\n"
        "Send payment screenshot in this chat."
    )

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]

    await query.message.delete()
    await query.message.reply_photo(
        photo=InputFile(qr_bio, filename="qr.png"),
        caption=payment_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )
    return WAITING_FOR_SCREENSHOT

  elif query.data == "how":
    settings_doc = await settings_col.find_one({"key": "how_to_video"})
    if settings_doc and "file_id" in settings_doc:
      await query.message.reply_video(video=settings_doc["file_id"])
    else:
      await query.message.reply_text("Video will be added by admin later.")
    return ConversationHandler.END

  elif query.data == "admin_stats":
    if user.id not in ADMIN_IDS:
      await query.answer("Unauthorized!", show_alert=True)
      return ConversationHandler.END

    total_users = await users_col.count_documents({})
    total_pending = await purchases_col.count_documents({"status": "pending"})
    total_approved = await purchases_col.count_documents({"status": "approved"})
    total_rejected = await purchases_col.count_documents({"status": "rejected"})

    stats_text = (
        f"📊 <b>Bot Statistics</b>\n\n"
        f"Total Users: <code>{total_users}</code>\n"
        f"Pending Payments: <code>{total_pending}</code>\n"
        f"Approved Payments: <code>{total_approved}</code>\n"
        f"Rejected Payments: <code>{total_rejected}</code>"
    )
    keyboard = [[InlineKeyboardButton("🔙 Back to Panel", callback_data="admin_panel")]]
    await query.message.edit_caption(caption=stats_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    return ConversationHandler.END

  elif query.data == "admin_broadcast":
    if user.id not in ADMIN_IDS:
      await query.answer("Unauthorized!", show_alert=True)
      return ConversationHandler.END

    await query.message.reply_text("📢 Send the text or photo you want to broadcast to all users:")
    return WAITING_FOR_BROADCAST

  elif query.data == "set_upi":
    if user.id not in ADMIN_IDS:
      return ConversationHandler.END
    await query.message.reply_text("💳 Send the new UPI ID:")
    return SETTING_UPI

  elif query.data == "set_price":
    if user.id not in ADMIN_IDS:
      return ConversationHandler.END
    await query.message.reply_text("💰 Send the new Price (numeric value only):")
    return SETTING_PRICE

  elif query.data == "set_link":
    if user.id not in ADMIN_IDS:
      return ConversationHandler.END
    await query.message.reply_text("🔗 Send the new Premium Group Link:")
    return SETTING_LINK

  elif query.data == "set_welcome":
    if user.id not in ADMIN_IDS:
      return ConversationHandler.END
    await query.message.reply_text("📝 Send the new Welcome Message template (use {price} for price dynamic tag):")
    return SETTING_WELCOME

  elif query.data == "set_howto_menu":
    if user.id not in ADMIN_IDS:
      return ConversationHandler.END
    await query.message.reply_text("🎥 Send the video file with command /sethowto or reply here with the video.")
    return SETTING_HOWTO

  elif query.data == "set_support":
    if user.id not in ADMIN_IDS:
      return ConversationHandler.END
    await query.message.reply_text("🆘 Send the new Support Username (e.g., @Vidsell6):")
    return SETTING_SUPPORT

  return ConversationHandler.END


async def admin_set_upi_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if update.message.from_user.id not in ADMIN_IDS:
    return ConversationHandler.END
  new_upi = update.message.text.strip()
  await set_setting("upi_id", new_upi)
  await update.message.reply_text(f"✅ UPI Updated Successfully to: {new_upi}")
  return ConversationHandler.END


async def admin_set_price_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if update.message.from_user.id not in ADMIN_IDS:
    return ConversationHandler.END
  try:
    new_price = int(update.message.text.strip())
    await set_setting("price", new_price)
    await update.message.reply_text(f"✅ Price Updated Successfully to: ₹{new_price}")
  except ValueError:
    await update.message.reply_text("⚠️ Invalid price. Please send a valid number.")
  return ConversationHandler.END


async def admin_set_link_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if update.message.from_user.id not in ADMIN_IDS:
    return ConversationHandler.END
  new_link = update.message.text.strip()
  await set_setting("group_link", new_link)
  await update.message.reply_text("✅ Link Updated Successfully")
  return ConversationHandler.END


async def admin_set_welcome_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if update.message.from_user.id not in ADMIN_IDS:
    return ConversationHandler.END
  new_welcome = update.message.text.strip()
  await set_setting("welcome_text", new_welcome)
  await update.message.reply_text("✅ Updated Successfully")
  return ConversationHandler.END


async def admin_set_howto_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if update.message.from_user.id not in ADMIN_IDS:
    return ConversationHandler.END
  if not update.message.video:
    await update.message.reply_text("⚠️ Please send a video file.")
    return SETTING_HOWTO
  file_id = update.message.video.file_id
  await settings_col.update_one({"key": "how_to_video"}, {"$set": {"file_id": file_id}}, upsert=True)
  await update.message.reply_text("✅ How to buy video updated successfully!")
  return ConversationHandler.END


async def admin_set_support_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if update.message.from_user.id not in ADMIN_IDS:
    return ConversationHandler.END
  new_support = update.message.text.strip()
  await set_setting("support_username", new_support)
  await update.message.reply_text("✅ Support Username Updated Successfully")
  return ConversationHandler.END


async def set_howto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if update.message.from_user.id not in ADMIN_IDS:
    return

  if not update.message.video:
    await update.message.reply_text("⚠️ Please send a video with the /sethowto command.")
    return

  file_id = update.message.video.file_id
  await settings_col.update_one({"key": "how_to_video"}, {"$set": {"file_id": file_id}}, upsert=True)
  await update.message.reply_text("✅ How to buy video updated successfully!")


async def receive_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
  if not update.message.photo:
    await update.message.reply_text("⚠️ Please send a valid image/screenshot of your payment!")
    return WAITING_FOR_SCREENSHOT

  user = update.effective_user
  existing_pending = await purchases_col.find_one({"user_id": user.id, "status": "pending"})
  if existing_pending:
    await update.message.reply_text("⚠️ You already have a payment verification pending with admins.")
    return ConversationHandler.END

  photo_id = update.message.photo[-1].file_id
  username = f"@{user.username}" if user.username else "No Username"
  current_price = await get_setting("price")

  purchase_doc = {
      "user_id": user.id,
      "username": username,
      "product": "PREMIUM ACCESS",
      "amount": current_price,
      "status": "pending",
      "date": update.message.date,
  }
  await purchases_col.insert_one(purchase_doc)

  await update.message.reply_text(
      "✅ <b>Screenshot Received Successfully!</b>\n\nYour payment has been sent to admins for verification. Please wait a moment.",
      parse_mode=ParseMode.HTML,
  )

  admin_keyboard = [
      [
          InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}"),
          InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user.id}"),
      ]
  ]

  forward_caption = (
      f"🔔 <b>New Payment Verification Request!</b>\n\n"
      f"👤 User ID: <code>{user.id}</code>\n"
      f"🔗 Username: {username}\n"
      f"📦 Product: PREMIUM ACCESS\n"
      f"💰 Amount: ₹{current_price}\n\n"
      f"Please check the screenshot below:"
  )

  for admin_id in ADMIN_IDS:
    try:
      await context.bot.send_photo(
          chat_id=admin_id,
          photo=photo_id,
          caption=forward_caption,
          reply_markup=InlineKeyboardMarkup(admin_keyboard),
          parse_mode=ParseMode.HTML,
      )
    except Exception as e:
      logger.error(f"Failed to forward screenshot to admin {admin_id}: {e}")

  return ConversationHandler.END


async def admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
  query = update.callback_query
  await query.answer()

  if query.from_user.id not in ADMIN_IDS:
    await query.answer("You are not authorized!", show_alert=True)
    return

  data = query.data.split("_")
  action = data[0]
  target_user_id = int(data[1])

  purchase = await purchases_col.find_one({"user_id": target_user_id, "status": "pending"})
  if not purchase:
    await query.answer("⚠️ This payment has already been processed or does not exist.", show_alert=True)
    try:
      await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
      pass
    return

  admin_name = query.from_user.first_name or "Admin"
  group_link = await get_setting("group_link")
  support_username = await get_setting("support_username")

  if action == "approve":
    await purchases_col.update_one(
        {"user_id": target_user_id, "status": "pending"},
        {"$set": {"status": "approved"}},
    )

    success_msg = (
        "✅ Payment Received Successfully!\n\n"
        "Hi 👋\n\n"
        "Thank you for your payment 💖\n\n"
        "🔗 Your private channel link 👇\n"
        f"{group_link}\n\n"
        "If you face any issue, feel free to message me anytime 😊\n\n"
        f"👉 {support_username}\n\n"
        "🙏 Thanks for trusting us!"
    )
    try:
      await context.bot.send_message(
          chat_id=target_user_id, text=success_msg, parse_mode=ParseMode.HTML
      )
    except Exception as e:
      logger.error(f"Could not message user {target_user_id}: {e}")

    await query.message.edit_caption(
        caption=query.message.caption + f"\n\n🟢 <b>APPROVED by {admin_name}</b>",
        reply_markup=None,
        parse_mode=ParseMode.HTML,
    )

  elif action == "reject":
    await purchases_col.update_one(
        {"user_id": target_user_id, "status": "pending"},
        {"$set": {"status": "rejected"}},
    )

    reject_msg = f"❌ Payment Verification Failed\n\nPlease contact admin:\n{support_username}"
    try:
      await context.bot.send_message(
          chat_id=target_user_id, text=reject_msg, parse_mode=ParseMode.HTML
      )
    except Exception as e:
      logger.error(f"Could not message user {target_user_id}: {e}")

    await query.message.edit_caption(
        caption=query.message.caption + f"\n\n🔴 <b>REJECTED by {admin_name}</b>",
        reply_markup=None,
        parse_mode=ParseMode.HTML,
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if update.message.from_user.id not in ADMIN_IDS:
    return

  await update.message.reply_text("📢 Send the text or photo you want to broadcast to all users:")
  return WAITING_FOR_BROADCAST


async def execute_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
  if update.message.from_user.id not in ADMIN_IDS:
    return ConversationHandler.END

  users = await users_col.find({}).to_list(length=100000)
  success, fail = 0, 0
  status_msg = await update.message.reply_text(f"🚀 Broadcasting to {len(users)} users...")

  for user in users:
    uid = user["user_id"]
    try:
      if update.message.photo:
        await context.bot.send_photo(
            chat_id=uid,
            photo=update.message.photo[-1].file_id,
            caption=update.message.caption or "",
        )
      else:
        await context.bot.send_message(chat_id=uid, text=update.message.text)
      success += 1
    except Exception:
      fail += 1

  await status_msg.edit_text(
      f"✅ <b>Broadcast Completed!</b>\nSuccess Count: {success}\nFailed Count: {fail}",
      parse_mode=ParseMode.HTML,
  )
  return ConversationHandler.END


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if update.message.from_user.id not in ADMIN_IDS:
    return

  total_users = await users_col.count_documents({})
  total_pending = await purchases_col.count_documents({"status": "pending"})
  total_approved = await purchases_col.count_documents({"status": "approved"})
  total_rejected = await purchases_col.count_documents({"status": "rejected"})

  stats_text = (
      f"📊 <b>Bot Statistics</b>\n\n"
      f"Total Users: <code>{total_users}</code>\n"
      f"Pending Payments: <code>{total_pending}</code>\n"
      f"Approved Payments: <code>{total_approved}</code>\n"
      f"Rejected Payments: <code>{total_rejected}</code>"
  )
  await update.message.reply_text(stats_text, parse_mode=ParseMode.HTML)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if update.message.from_user.id not in ADMIN_IDS:
    return
  admin_text = "👑 <b>ADMIN PANEL</b>\n\nChoose an action below:"
  keyboard = [
      [InlineKeyboardButton("📊 Stats", callback_data="admin_stats"), InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
      [InlineKeyboardButton("💳 Change UPI ID", callback_data="set_upi"), InlineKeyboardButton("💰 Change Price", callback_data="set_price")],
      [InlineKeyboardButton("🔗 Change Link", callback_data="set_link"), InlineKeyboardButton("📝 Change Welcome", callback_data="set_welcome")],
      [InlineKeyboardButton("🎥 Change HowTo Video", callback_data="set_howto_menu"), InlineKeyboardButton("🆘 Change Support", callback_data="set_support")],
      [InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")],
  ]
  await update.message.reply_text(admin_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


def main():
  keep_alive()  # ADDED FOR UPTIMEROBOT: Start Flask server in background thread

  app = Application.builder().token(BOT_TOKEN).build()

  async def post_init(application: Application):
    await initialize_settings()
    logger.info("Bot is up and running...")

  app.post_init = post_init

  conv_handler = ConversationHandler(
      entry_points=[
          CallbackQueryHandler(
              button_router,
              pattern="^(buy|how|main_menu|admin_panel|admin_stats|admin_broadcast|set_upi|set_price|set_link|set_welcome|set_howto_menu|set_support)$",
          ),
          CommandHandler("broadcast", broadcast_command),
          CommandHandler("admin", admin_command),
      ],
      states={
          WAITING_FOR_SCREENSHOT: [MessageHandler(filters.PHOTO, receive_screenshot)],
          WAITING_FOR_BROADCAST: [
              MessageHandler((filters.PHOTO | filters.TEXT) & ~filters.COMMAND, execute_broadcast)
          ],
          SETTING_UPI: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_upi_receive)],
          SETTING_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_price_receive)],
          SETTING_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_link_receive)],
          SETTING_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_welcome_receive)],
          SETTING_HOWTO: [MessageHandler(filters.VIDEO, admin_set_howto_receive)],
          SETTING_SUPPORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_support_receive)],
      },
      fallbacks=[CommandHandler("start", start)],
  )

  app.add_handler(CommandHandler("start", start))
  app.add_handler(CommandHandler("admin", admin_command))
  app.add_handler(CommandHandler("stats", stats_command))
  app.add_handler(CommandHandler("sethowto", set_howto_command))
  app.add_handler(conv_handler)
  app.add_handler(CallbackQueryHandler(admin_action, pattern="^(approve|reject)_"))

  app.run_polling()


if __name__ == "__main__":
  main()
