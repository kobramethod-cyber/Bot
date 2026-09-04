import io
import logging
import os
from telegram import (
    BufferedInputFile,
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
ADMIN_USERNAME = "@Vidsell6"

PHOTO_ID = (
    "AgACAgUAAxkBAAICYGqawsPSsd-rVZF8QNyGGavXiRnYAAJ0FGsbNXDQVB25ko4WD9yEAQADAgADeAADPQQ"
)

START_TEXT = """✨ Welcome to Premium Access Hub ✨

🔥 Buy Premium Groups in just ₹50!

📂 Resources:
https://t.me/+h7qBjBXj13djMWI1
https://t.me/+bxjfe4zWwqQ4ZjY0

💎 Features:
• ♾️ Lifetime Permanent Access
• 📁 All Premium Categories included
• 🚀 Instant delivery after verification

✨ One-time payment, enjoy forever!"""

WAITING_FOR_SCREENSHOT = 1
WAITING_FOR_BROADCAST = 2

# Initialize MongoDB via Motor
client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = client["premium_access_hub"]
users_col = db["users"]
purchases_col = db["purchases"]
settings_col = db["settings"]
products_col = db["products"]


async def init_default_products():
  count = await products_col.count_documents({})
  if count == 0:
    default_products = [
        {
            "product_id": "prod_1",
            "name": "Premium Access",
            "price": 50,
            "link": "https://t.me/+H_y0MBr_c0g2ZDk1",
        },
        {
            "product_id": "prod_2",
            "name": "VIP Access",
            "price": 100,
            "link": "https://t.me/+bxjfe4zWwqQ4ZjY0",
        },
        {
            "product_id": "prod_3",
            "name": "Lifetime Bundle",
            "price": 200,
            "link": "https://t.me/+h7qBjBXj13djMWI1",
        },
    ]
    await products_col.insert_many(default_products)


def generate_upi_qr(upi_id: str, amount: int, name: str = "Premium Hub"):
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

  keyboard = [
      [InlineKeyboardButton("🛒 Buy Premium", callback_data="buy_menu")],
      [
          InlineKeyboardButton("❓ How To Buy", callback_data="how"),
          InlineKeyboardButton(
              "🆘 Admin Support", url="https://t.me/Vidsell6"
          ),
      ],
  ]

  if user.id in ADMIN_IDS:
    keyboard.append([
        InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
        InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
    ])

  await update.message.reply_photo(
      photo=PHOTO_ID, caption=START_TEXT, reply_markup=InlineKeyboardMarkup(keyboard)
  )


async def button_router(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
  query = update.callback_query
  await query.answer()
  user = query.from_user

  if query.data == "main_menu":
    keyboard = [
        [InlineKeyboardButton("🛒 Buy Premium", callback_data="buy_menu")],
        [
            InlineKeyboardButton("❓ How To Buy", callback_data="how"),
            InlineKeyboardButton(
                "🆘 Admin Support", url="https://t.me/Vidsell6"
            ),
        ],
    ]
    if user.id in ADMIN_IDS:
      keyboard.append([
          InlineKeyboardButton(
              "📢 Broadcast", callback_data="admin_broadcast"
          ),
          InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
      ])

    await query.message.edit_caption(
        caption=START_TEXT, reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END

  elif query.data == "buy_menu":
    existing_pending = await purchases_col.find_one(
        {"user_id": user.id, "status": "pending"}
    )
    if existing_pending:
      await query.message.reply_text(
          "⚠️ You already have a payment verification pending with admins."
      )
      return ConversationHandler.END

    products = await products_col.find({}).to_list(length=10)
    keyboard = [
        [
            InlineKeyboardButton(
                f"📦 {p['name']} - ₹{p['price']}",
                callback_data=f"select_prod_{p['product_id']}",
            )
        ]
        for p in products
    ]
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])

    await query.message.edit_caption(
        caption="🛍️ <b>Select a Product / Plan Below:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END

  elif query.data.startswith("select_prod_"):
    prod_id = query.data.split("_")[2]
    product = await products_col.find_one({"product_id": prod_id})

    if not product:
      await query.answer("Product not found!", show_alert=True)
      return ConversationHandler.END

    existing_pending = await purchases_col.find_one(
        {"user_id": user.id, "status": "pending"}
    )
    if existing_pending:
      await query.message.reply_text(
          "⚠️ You already have a payment verification pending with admins."
      )
      return ConversationHandler.END

    context.user_data["selected_product"] = product["name"]
    context.user_data["amount"] = product["price"]

    qr_bio = generate_upi_qr("nagargoje12@ptyes", product["price"])
    payment_text = (
        f"✦ <b>𝗣𝗥𝗘𝗠𝗜𝗨𝗠 𝗣𝗔𝗬𝗠𝗘𝗡𝗧</b>\n\n"
        f"❐ Product: {product['name']}\n"
        f"❐ Amount: ₹{product['price']}\n"
        f"❐ Validity: Lifetime\n\n"
        "────────────────────\n\n"
        "❐ <b>PAYMENT METHODS</b>\n\n"
        "Paytm • GPay • PhonePe • UPI\n\n"
        "UPI ID:\n"
        "<b>nagargoje12@ptyes</b>\n\n"
        "<b>AFTER PAYMENT:</b>\n"
        "Send payment screenshot in this chat."
    )

    keyboard = [
        [InlineKeyboardButton("🔙 Back", callback_data="buy_menu")]
    ]

    await query.message.delete()
    await query.message.reply_photo(
        photo=BufferedInputFile(qr_bio.read(), filename="qr.png"),
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

  elif query.data == "admin_broadcast":
    if user.id not in ADMIN_IDS:
      await query.answer("Unauthorized!", show_alert=True)
      return ConversationHandler.END

    await query.message.reply_text(
        "📢 Send the text or photo you want to broadcast to all users:"
    )
    return WAITING_FOR_BROADCAST

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
    await query.message.reply_text(stats_text, parse_mode=ParseMode.HTML)
    return ConversationHandler.END

  return ConversationHandler.END


async def set_howto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if update.message.from_user.id not in ADMIN_IDS:
    return

  if not update.message.video:
    await update.message.reply_text(
        "⚠️ Please send a video with the /sethowto command."
    )
    return

  file_id = update.message.video.file_id
  await settings_col.update_one(
      {"key": "how_to_video"}, {"$set": {"file_id": file_id}}, upsert=True
  )
  await update.message.reply_text("✅ How to buy video updated successfully!")


async def receive_screenshot(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
  if not update.message.photo:
    await update.message.reply_text(
        "⚠️ Please send a valid image/screenshot of your payment!"
    )
    return WAITING_FOR_SCREENSHOT

  user = update.effective_user
  existing_pending = await purchases_col.find_one(
      {"user_id": user.id, "status": "pending"}
  )
  if existing_pending:
    await update.message.reply_text(
        "⚠️ You already have a payment verification pending with admins."
    )
    return ConversationHandler.END

  product_name = context.user_data.get("selected_product", "Premium Access")
  amount = context.user_data.get("amount", 50)
  photo_id = update.message.photo[-1].file_id
  username = f"@{user.username}" if user.username else "No Username"

  purchase_doc = {
      "user_id": user.id,
      "username": username,
      "product": product_name,
      "amount": amount,
      "status": "pending",
      "date": update.message.date,
  }
  await purchases_col.insert_one(purchase_doc)

  await update.message.reply_text(
      "✅ <b>Screenshot Received Successfully!</b>\n\nYour payment has been sent"
      " to admins for verification. Please wait a moment.",
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
      f"📦 Product: {product_name}\n"
      f"💰 Amount: ₹{amount}\n\n"
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

  purchase = await purchases_col.find_one(
      {"user_id": target_user_id, "status": "pending"}
  )
  if not purchase:
    await query.answer(
        "⚠️ This payment has already been processed or does not exist.",
        show_alert=True,
    )
    try:
      await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
      pass
    return

  product_name = purchase.get("product", "Premium Access")
  prod_doc = await products_col.find_one({"name": product_name})
  channel_link = (
      prod_doc["link"] if prod_doc else "https://t.me/+H_y0MBr_c0g2ZDk1"
  )

  admin_name = query.from_user.first_name or "Admin"

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
        f"{channel_link}\n\n"
        "If you face any issue, feel free to message me anytime 😊\n\n"
        "👉 @Vidsell6\n\n"
        "🙏 Thanks for trusting us!"
    )
    try:
      await context.bot.send_message(
          chat_id=target_user_id, text=success_msg, parse_mode=ParseMode.HTML
      )
    except Exception as e:
      logger.error(f"Could not message user {target_user_id}: {e}")

    await query.message.edit_caption(
        caption=query.message.caption
        + f"\n\n🟢 <b>APPROVED by {admin_name}</b>",
        reply_markup=None,
        parse_mode=ParseMode.HTML,
    )

  elif action == "reject":
    await purchases_col.update_one(
        {"user_id": target_user_id, "status": "pending"},
        {"$set": {"status": "rejected"}},
    )

    reject_msg = (
        "❌ Payment Verification Failed\n\nPlease contact admin:\n@Vidsell6"
    )
    try:
      await context.bot.send_message(
          chat_id=target_user_id, text=reject_msg, parse_mode=ParseMode.HTML
      )
    except Exception as e:
      logger.error(f"Could not message user {target_user_id}: {e}")

    await query.message.edit_caption(
        caption=query.message.caption
        + f"\n\n🔴 <b>REJECTED by {admin_name}</b>",
        reply_markup=None,
        parse_mode=ParseMode.HTML,
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  if update.message.from_user.id not in ADMIN_IDS:
    return

  await update.message.reply_text(
      "📢 Send the text or photo you want to broadcast to all users:"
  )
  return WAITING_FOR_BROADCAST


async def execute_broadcast(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
  if update.message.from_user.id not in ADMIN_IDS:
    return ConversationHandler.END

  users = await users_col.find({}).to_list(length=100000)
  success, fail = 0, 0
  status_msg = await update.message.reply_text(
      f"🚀 Broadcasting to {len(users)} users..."
  )

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
      f"✅ <b>Broadcast Completed!</b>\nSuccess Count: {success}\nFailed Count:"
      f" {fail}",
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


def main():
  app = Application.builder().token(BOT_TOKEN).build()

  conv_handler = ConversationHandler(
      entry_points=[
          CallbackQueryHandler(
              button_router,
              pattern="^(buy_menu|how|main_menu|admin_broadcast|admin_stats|select_prod_.+)$",
          ),
          CommandHandler("broadcast", broadcast_command),
      ],
      states={
          WAITING_FOR_SCREENSHOT: [
              MessageHandler(filters.PHOTO, receive_screenshot)
          ],
          WAITING_FOR_BROADCAST: [
              MessageHandler(
                  (filters.PHOTO | filters.TEXT) & ~filters.COMMAND,
                  execute_broadcast,
              )
          ],
      },
      fallbacks=[CommandHandler("start", start)],
  )

  app.add_handler(CommandHandler("start", start))
  app.add_handler(CommandHandler("stats", stats_command))
  app.add_handler(CommandHandler("sethowto", set_howto_command))
  app.add_handler(conv_handler)
  app.add_handler(
      CallbackQueryHandler(admin_action, pattern="^(approve|reject)_")
  )

  async def post_init(application: Application):
    await init_default_products()
    logger.info("Database initialized & Bot Started...")

  app.post_init = post_init
  app.run_polling()


if __name__ == "__main__":
  main()
