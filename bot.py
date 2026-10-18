# -*- coding: utf-8 -*-
import io
import secrets
from urllib.parse import urlencode
import logging
import os
import time
import asyncio
import sys
import platform
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread
from telegram import (
    InputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TimedOut, NetworkError, BadRequest
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
INITIAL_ADMIN_IDS = [1936430807, 8720701910]

BOT_START_TIME = time.time()

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
    "mega_group_link": "https://t.me/+MjSeL0dF9V42ZjJl",
    "mega_caption": (
        "📁 Mega Premium Access\n\n"
        "Choose Your Plan:\n\n"
        "🔥 1 Day - ₹70\n"
        "🔥 1 Month - ₹300\n"
        "🔥 Permanent - ₹800\n\n"
        "After payment send screenshot."
    ),
    "mega_menu_name": "📁 Mega Access",
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
    ADDING_ADMIN,
    REMOVING_ADMIN,
    ADDING_PRODUCT,
    REMOVING_PRODUCT,
    EDITING_PRODUCT_NAME,
    EDITING_PRODUCT_PRICE,
    EDITING_PRODUCT_LINK,
    SETTING_START_PHOTO,
    MEGA_ADDING_PLAN,
    MEGA_CHANGING_PRICE,
    MEGA_CHANGING_DAYS,
    MEGA_CHANGING_LINK,
    MEGA_CHANGING_CAPTION,
    MEGA_CHANGING_PHOTO,
    MEGA_CHANGING_MENU_NAME,
) = range(10, 31)

# Initialize MongoDB via Motor
client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = client["premium_access_hub"]
users_col = db["users"]
purchases_col = db["purchases"]
settings_col = db["settings"]
admins_col = db["admins"]
products_col = db["products"]

# Mega Collections
mega_plans_col = db["mega_plans"]
mega_subs_col = db["mega_subs"]
mega_purchases_col = db["mega_purchases"]

# Flask app for keep-alive
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


async def is_admin(user_id: int) -> bool:
    if user_id in INITIAL_ADMIN_IDS:
        return True
    doc = await admins_col.find_one({"user_id": user_id})
    return doc is not None


async def get_setting(key: str):
    doc = await settings_col.find_one({"key": key})
    if doc and "value" in doc:
        return doc["value"]
    return DEFAULT_SETTINGS.get(key)


async def set_setting(key: str, value):
    await settings_col.update_one({"key": key}, {"$set": {"value": value}}, upsert=True)


async def get_mega_menu_name() -> str:
    try:
        doc = await settings_col.find_one({"key": "mega_menu_name"})
        if doc and "value" in doc:
            return doc["value"]
    except Exception as e:
        logger.error(f"Error fetching mega menu name: {e}")
    return DEFAULT_SETTINGS.get("mega_menu_name", "📁 Mega Access")


async def initialize_settings():
    for key, val in DEFAULT_SETTINGS.items():
        existing = await settings_col.find_one({"key": key})
        if not existing:
            await settings_col.insert_one({"key": key, "value": val})

    for admin_id in INITIAL_ADMIN_IDS:
        await admins_col.update_one({"user_id": admin_id}, {"$set": {"user_id": admin_id}}, upsert=True)

    default_prod = await products_col.find_one({"product_id": "default"})
    if not default_prod:
        price = await get_setting("price")
        link = await get_setting("group_link")
        await products_col.update_one(
            {"product_id": "default"},
            {"$set": {"name": "PREMIUM ACCESS", "price": price, "group_link": link}},
            upsert=True
        )

    mega_count = await mega_plans_col.count_documents({})
    if mega_count == 0:
        default_plans = [
            {"plan_id": "plan_1day", "name": "1 Day", "price": 70, "days": 1},
            {"plan_id": "plan_1month", "name": "1 Month", "price": 300, "days": 30},
            {"plan_id": "plan_permanent", "name": "Permanent", "price": 800, "days": 0},
        ]
        for p in default_plans:
            await mega_plans_col.update_one({"plan_id": p["plan_id"]}, {"$set": p}, upsert=True)


def new_order_id():
    # Cryptographically random order ID.
    return "ORD" + secrets.token_hex(7).upper()


def generate_upi_qr(upi_id: str, amount: int, name: str = "Desi Group"):
    params = {"pa": upi_id, "pn": name, "am": str(amount), "cu": "INR"}
    upi_url = "upi://pay?" + urlencode(params)
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
                "status": "active"
            },
            "$setOnInsert": {"join_date": update.message.date},
        },
        upsert=True,
    )

    price = await get_setting("price")
    welcome_template = await get_setting("welcome_text")
    support_username = await get_setting("support_username")
    start_text = welcome_template.format(price=price)
    mega_button_text = await get_mega_menu_name()

    keyboard = []
    products = await products_col.find({}).to_list(length=100)
    if products:
        for prod in products:
            p_id = prod.get("product_id", "default")
            p_name = prod.get("name", "Premium Access")
            keyboard.append([InlineKeyboardButton(f"🛒 Buy {p_name}", callback_data=f"buy_{p_id}")])
    else:
        keyboard.append([InlineKeyboardButton("🛒 Buy Premium", callback_data="buy_default")])

    keyboard.append([InlineKeyboardButton(mega_button_text, callback_data="mega_access_menu")])

    keyboard.append([
        InlineKeyboardButton("❓ How To Buy", callback_data="how"),
        InlineKeyboardButton("🆘 Admin Support", url=f"https://t.me/{support_username.lstrip('@')}"),
    ])

    if await is_admin(user.id):
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])

    start_photo_doc = await settings_col.find_one({"key": "start_photo"})
    if start_photo_doc and "file_id" in start_photo_doc:
        try:
            await update.message.reply_photo(
                photo=start_photo_doc["file_id"],
                caption=start_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return
        except Exception as e:
            logger.error(f"Failed to send start photo: {e}")

    await update.message.reply_text(
        text=start_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def get_formatted_stats_text() -> str:
    total_users = await users_col.count_documents({})
    active_users = await users_col.count_documents({"status": "active"})
    blocked_users = await users_col.count_documents({"status": "blocked"})
    banned_users = await users_col.count_documents({"status": "banned"})
    
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    new_today = await users_col.count_documents({"join_date": {"$gte": today_start}})

    total_orders = await purchases_col.count_documents({})
    pending_orders_cursor = purchases_col.find({"status": "pending"})
    pending_orders_list = await pending_orders_cursor.to_list(length=1000)
    pending_count = len(pending_orders_list)
    pending_amount = sum(p.get("amount", 0) for p in pending_orders_list)

    awaiting_payment = await purchases_col.count_documents({"status": "awaiting"})
    delivered_orders = await purchases_col.count_documents({"status": "approved"})
    delivered_today = await purchases_col.count_documents({"status": "approved", "date": {"$gte": today_start}})
    rejected_orders = await purchases_col.count_documents({"status": "rejected"})

    rev_today_cursor = purchases_col.aggregate([{"$match": {"status": "approved", "date": {"$gte": today_start}}}, {"$group": {"_id": None, "total": {"$sum": "$amount"}}}])
    rev_today_list = await rev_today_cursor.to_list(length=1)
    rev_today = rev_today_list[0]["total"] if rev_today_list else 0

    rev_total_cursor = purchases_col.aggregate([{"$match": {"status": "approved"}}, {"$group": {"_id": None, "total": {"$sum": "$amount"}}}])
    rev_total_list = await rev_total_cursor.to_list(length=1)
    rev_total = rev_total_list[0]["total"] if rev_total_list else 0

    total_categories = await products_col.count_documents({})
    current_price = await get_setting("price")
    support_username = await get_setting("support_username")

    uptime_seconds = int(time.time() - BOT_START_TIME)
    days, rem = divmod(uptime_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"

    python_version = f"{platform.python_version()}"
    ptb_version = "21.x"

    stats_text = (
        f"Lɪᴠᴇ Dᴀꜱʜʙᴏᴀʀᴅ\n\n"
        f"Uꜱᴇʀꜱ\n"
        f"Tᴏᴛᴀʟ : {total_users}\n"
        f"Aᴄᴛɪᴠᴇ : {active_users}\n"
        f"Bʟᴏᴄᴋᴇᴅ Bᴏᴛ : {blocked_users}\n"
        f"Bᴀɴɴᴇᴅ : {banned_users}\n"
        f"Nᴇᴡ Tᴏᴅᴀʏ : {new_today}\n\n"
        f"Oʀᴅᴇʀꜱ\n"
        f"Tᴏᴛᴀʟ : {total_orders}\n"
        f"Pᴇɴᴅɪɴɢ : {pending_count} · ₹{pending_amount:.2f}\n"
        f"Aᴡᴀɪᴛɪɴɢ Pᴀʏᴍᴇɴᴛ : {awaiting_payment}\n"
        f"Dᴇʟɪᴠᴇʀᴇᴅ : {delivered_orders}\n"
        f"Dᴇʟɪᴠᴇʀᴇᴅ Tᴏᴅᴀʏ : {delivered_today}\n"
        f"Rᴇᴊᴇᴄᴛᴇᴅ : {rejected_orders}\n\n"
        f"Rᴇᴠᴇɴᴜᴇ\n"
        f"Tᴏᴛᴀʟ : ₹{rev_total}\n"
        f"Tᴏᴅᴀʏ : ₹{rev_today}\n\n"
        f"Sᴛᴏʀᴇ\n"
        f"Cᴀᴛᴇɢᴏʀɪᴇꜱ : {total_categories} active / {total_categories}\n"
        f"Uᴘɪ Iᴅꜱ : 1 active / 1\n"
        f"Bᴜʏ-Aʟʟ Pʀɪᴄᴇ : ₹{current_price}\n"
        f"Fᴏʀᴄᴇ Jᴏɪɴ : OFF (0)\n"
        f"Sᴜᴘᴘᴏʀᴛ : {support_username}\n\n"
        f"Sʏꜱᴛᴇᴍ\n"
        f"Uᴘᴛɪᴍᴇ : {uptime_str}\n"
        f"Dᴀᴛᴀʙᴀꜱᴇ : 536.00 KB\n"
        f"Pʏᴛʜᴏɴ : {python_version}\n"
        f"Pᴛʙ : {ptb_version}\n"
        f"Sǫʟɪᴛᴇ : 3.53.1\n\n"
        f"Uᴘᴅᴀᴛᴇᴅ : {datetime.now().strftime('%I:%M:%S %p')}"
    )
    return stats_text


async def admin_panel_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if not await is_admin(user.id):
        await query.answer("Unauthorized!", show_alert=True)
        return

    admin_text = "👑 <b>ADMIN PANEL</b>\n\nChoose an action below:"
    keyboard = [
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats"), InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("💳 Change UPI ID", callback_data="set_upi"), InlineKeyboardButton("💰 Change Global Price", callback_data="set_price")],
        [InlineKeyboardButton("🔗 Change Global Link", callback_data="set_link"), InlineKeyboardButton("📝 Change Welcome", callback_data="set_welcome")],
        [InlineKeyboardButton("🖼 Change Start Photo", callback_data="set_start_photo_menu"), InlineKeyboardButton("🎥 Change HowTo Video", callback_data="set_howto_menu")],
        [InlineKeyboardButton("🆘 Change Support", callback_data="set_support")],
        [InlineKeyboardButton("➕ Add Admin", callback_data="add_admin_menu"), InlineKeyboardButton("➖ Remove Admin", callback_data="remove_admin_menu")],
        [InlineKeyboardButton("📦 Add Product", callback_data="add_product_menu"), InlineKeyboardButton("🗑 Remove Product", callback_data="remove_product_menu")],
        [InlineKeyboardButton("✏️ Manage Products", callback_data="manage_products_menu")],
        [InlineKeyboardButton("📁 Mega File Manager", callback_data="mega_admin_menu")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")],
    ]

    if query.message.photo:
        await query.message.edit_caption(caption=admin_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
    else:
        await query.message.edit_text(text=admin_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if query.data == "main_menu":
        price = await get_setting("price")
        welcome_template = await get_setting("welcome_text")
        support_username = await get_setting("support_username")
        start_text = welcome_template.format(price=price)
        mega_button_text = await get_mega_menu_name()

        keyboard = []
        products = await products_col.find({}).to_list(length=100)
        if products:
            for prod in products:
                p_id = prod.get("product_id", "default")
                p_name = prod.get("name", "Premium Access")
                keyboard.append([InlineKeyboardButton(f"🛒 Buy {p_name}", callback_data=f"buy_{p_id}")])
        else:
            keyboard.append([InlineKeyboardButton("🛒 Buy Premium", callback_data="buy_default")])

        keyboard.append([InlineKeyboardButton(mega_button_text, callback_data="mega_access_menu")])

        keyboard.append([
            InlineKeyboardButton("❓ How To Buy", callback_data="how"),
            InlineKeyboardButton("🆘 Admin Support", url=f"https://t.me/{support_username.lstrip('@')}"),
        ])
        if await is_admin(user.id):
            keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])

        start_photo_doc = await settings_col.find_one({"key": "start_photo"})
        photo_file_id = start_photo_doc.get("file_id") if start_photo_doc else None

        if query.message.photo:
            if photo_file_id:
                try:
                    await query.message.edit_caption(caption=start_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
                    return ConversationHandler.END
                except Exception:
                    pass
            await query.message.delete()
            if photo_file_id:
                try:
                    await query.message.reply_photo(photo=photo_file_id, caption=start_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
                    return ConversationHandler.END
                except Exception:
                    pass
            await query.message.reply_text(text=start_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            if photo_file_id:
                await query.message.delete()
                try:
                    await query.message.reply_photo(photo=photo_file_id, caption=start_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
                    return ConversationHandler.END
                except Exception:
                    pass
                await query.message.reply_text(text=start_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            else:
                try:
                    await query.message.edit_text(text=start_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
                except Exception:
                    await query.message.delete()
                    await query.message.reply_text(text=start_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    elif query.data == "admin_panel":
        if not await is_admin(user.id):
            await query.answer("Unauthorized!", show_alert=True)
            return ConversationHandler.END
        await admin_panel_menu(update, context)
        return ConversationHandler.END

    elif query.data == "mega_access_menu":
        mega_caption = await get_setting("mega_caption")
        plans = await mega_plans_col.find({}).to_list(length=100)

        keyboard = []
        for p in plans:
            p_id = p.get("plan_id")
            p_name = p.get("name")
            p_price = p.get("price")
            keyboard.append([InlineKeyboardButton(f"🔥 {p_name} - ₹{p_price}", callback_data=f"megabuy_{p_id}")])

        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])

        mega_photo_doc = await settings_col.find_one({"key": "mega_photo"})
        photo_file_id = mega_photo_doc.get("file_id") if mega_photo_doc else None

        if query.message.photo:
            await query.message.delete()

        if photo_file_id:
            try:
                await query.message.reply_photo(
                    photo=photo_file_id,
                    caption=mega_caption,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )
                return ConversationHandler.END
            except Exception:
                pass

        await query.message.reply_text(
            text=mega_caption,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    elif query.data.startswith("megabuy_"):
        plan_id = query.data.split("_", 1)[1]
        plan = await mega_plans_col.find_one({"plan_id": plan_id})
        if not plan:
            await query.answer("Plan not found!", show_alert=True)
            return ConversationHandler.END

        p_name = plan.get("name", "Mega Plan")
        p_price = plan.get("price", 100)
        current_upi = await get_setting("upi_id")

        existing_pending = await mega_purchases_col.find_one({"user_id": user.id, "status": "pending"})
        if existing_pending:
            await query.message.reply_text("⚠️ You already have a payment verification pending with admins.")
            return ConversationHandler.END

        order_id = new_order_id()
        context.user_data["mega_order_id"] = order_id
        qr_bio = generate_upi_qr(current_upi, p_price, name=f"Mega {p_name}")
        payment_text = (
            "✦ <b>MEGA PAYMENT</b>\n\n"
            f"📦 Plan: {p_name}\n"
            f"🔹 Amount: ₹{p_price}\n"
            f"🧾 Order ID: <code>{order_id}</code>\n"
            f"🔹 Validity: {p_name}\n\n"
            "───────────────────\n\n"
            "🔹 <b>PAYMENT METHODS</b>\n\n"
            "Paytm • GPay • PhonePe • UPI\n\n"
            "UPI ID:\n"
            f"<b>{current_upi}</b>\n\n"
            "<b>AFTER PAYMENT:</b>\n"
            "Send payment screenshot in this chat."
        )

        context.user_data["mega_selected_plan_id"] = plan_id
        context.user_data["mega_selected_plan_name"] = p_name
        context.user_data["mega_selected_plan_price"] = p_price

        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="mega_access_menu")]]

        await query.message.delete()
        await query.message.reply_photo(
            photo=InputFile(qr_bio, filename="qr.png"),
            caption=payment_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
        return WAITING_FOR_SCREENSHOT

    elif query.data == "mega_admin_menu":
        if not await is_admin(user.id):
            await query.answer("Unauthorized!", show_alert=True)
            return ConversationHandler.END

        text = "📁 <b>MEGA FILE MANAGER ADMIN</b>\n\nChoose an action below:"
        kb = [
            [InlineKeyboardButton("➕ Add Plan", callback_data="mega_add_plan_menu"), InlineKeyboardButton("🗑 Remove Plan", callback_data="mega_remove_plan_menu")],
            [InlineKeyboardButton("💰 Change Price", callback_data="mega_change_price_menu"), InlineKeyboardButton("📅 Change Days", callback_data="mega_change_days_menu")],
            [InlineKeyboardButton("🔗 Change Group Chat ID/Link", callback_data="mega_set_link"), InlineKeyboardButton("📝 Change Caption", callback_data="mega_set_caption")],
            [InlineKeyboardButton("🖼 Change Photo", callback_data="mega_set_photo"), InlineKeyboardButton("📊 Mega Stats", callback_data="mega_stats")],
            [InlineKeyboardButton("✏ Change Mega Menu Name", callback_data="mega_change_menu_name")],
            [InlineKeyboardButton("🔙 Back to Panel", callback_data="admin_panel")]
        ]
        if query.message.photo:
            await query.message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        else:
            await query.message.edit_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    elif query.data == "mega_add_plan_menu":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("➕ Send plan details in this format:\n`Plan Name | Price | Days`\n\nExample:\n`7 Day | 150 | 7`\n(Use 0 days for Permanent)", parse_mode=ParseMode.HTML)
        return MEGA_ADDING_PLAN

    elif query.data == "mega_remove_plan_menu":
        if not await is_admin(user.id):
            return ConversationHandler.END
        plans = await mega_plans_col.find({}).to_list(length=100)
        if not plans:
            await query.message.reply_text("⚠️ No plans available to remove.")
            return ConversationHandler.END
        kb = []
        for p in plans:
            kb.append([InlineKeyboardButton(f"❌ {p.get('name')}", callback_data=f"megadelplan_{p.get('plan_id')}")])
        kb.append([InlineKeyboardButton("🔙 Back to Mega Menu", callback_data="mega_admin_menu")])
        if query.message.photo:
            await query.message.edit_caption(caption="🗑️ Select plan to remove:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        else:
            await query.message.edit_text(text="🗑️ Select plan to remove:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    elif query.data.startswith("megadelplan_"):
        if not await is_admin(user.id):
            return ConversationHandler.END
        p_id = query.data.split("_", 1)[1]
        await mega_plans_col.delete_one({"plan_id": p_id})
        kb = [[InlineKeyboardButton("🔙 Back to Mega Menu", callback_data="mega_admin_menu")]]
        if query.message.photo:
            await query.message.edit_caption(caption="✅ Plan removed successfully!", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await query.message.edit_text(text="✅ Plan removed successfully!", reply_markup=InlineKeyboardMarkup(kb))
        return ConversationHandler.END

    elif query.data == "mega_change_price_menu":
        if not await is_admin(user.id):
            return ConversationHandler.END
        plans = await mega_plans_col.find({}).to_list(length=100)
        if not plans:
            await query.message.reply_text("⚠️ No plans available.")
            return ConversationHandler.END
        kb = []
        for p in plans:
            kb.append([InlineKeyboardButton(f"💰 {p.get('name')} (₹{p.get('price')})", callback_data=f"megachprice_{p.get('plan_id')}")])
        kb.append([InlineKeyboardButton("🔙 Back to Mega Menu", callback_data="mega_admin_menu")])
        if query.message.photo:
            await query.message.edit_caption(caption="Select plan to change price:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        else:
            await query.message.edit_text(text="Select plan to change price:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    elif query.data.startswith("megachprice_"):
        if not await is_admin(user.id):
            return ConversationHandler.END
        p_id = query.data.split("_", 1)[1]
        context.user_data["mega_editing_plan_id"] = p_id
        await query.message.reply_text("💰 Send the new numeric price for this plan:")
        return MEGA_CHANGING_PRICE

    elif query.data == "mega_change_days_menu":
        if not await is_admin(user.id):
            return ConversationHandler.END
        plans = await mega_plans_col.find({}).to_list(length=100)
        if not plans:
            await query.message.reply_text("⚠️ No plans available.")
            return ConversationHandler.END
        kb = []
        for p in plans:
            kb.append([InlineKeyboardButton(f"📅 {p.get('name')} ({p.get('days')} days)", callback_data=f"megachdays_{p.get('plan_id')}")])
        kb.append([InlineKeyboardButton("🔙 Back to Mega Menu", callback_data="mega_admin_menu")])
        if query.message.photo:
            await query.message.edit_caption(caption="Select plan to change days/validity:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        else:
            await query.message.edit_text(text="Select plan to change days/validity:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    elif query.data.startswith("megachdays_"):
        if not await is_admin(user.id):
            return ConversationHandler.END
        p_id = query.data.split("_", 1)[1]
        context.user_data["mega_editing_plan_id"] = p_id
        await query.message.reply_text("📅 Send the new number of days (0 for permanent):")
        return MEGA_CHANGING_DAYS

    elif query.data == "mega_set_link":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("🔗 Send the new Mega Group Numeric Chat ID (e.g. -100xxxxxxxxxx) or Group Link:")
        return MEGA_CHANGING_LINK

    elif query.data == "mega_set_caption":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("📝 Send the new Mega Access caption:")
        return MEGA_CHANGING_CAPTION

    elif query.data == "mega_set_photo":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("🖼 Please send the new photo for Mega Access:")
        return MEGA_CHANGING_PHOTO

    elif query.data == "mega_change_menu_name":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("Send the new name for the Mega Access button.")
        return MEGA_CHANGING_MENU_NAME

    elif query.data == "mega_stats":
        if not await is_admin(user.id):
            await query.answer("Unauthorized!", show_alert=True)
            return ConversationHandler.END

        total_subs = await mega_subs_col.count_documents({})
        active_subs = await mega_subs_col.count_documents({"status": "active"})
        expired_subs = await mega_subs_col.count_documents({"status": "expired"})

        pipeline = [{"$match": {"status": "approved"}}, {"$group": {"_id": None, "total": {"$sum": "$amount"}}}]
        rev_cursor = mega_purchases_col.aggregate(pipeline)
        rev_list = await rev_cursor.to_list(length=1)
        total_rev = rev_list[0]["total"] if rev_list else 0

        stats_text = (
            f"📊 <b>Mega File Manager Statistics</b>\n\n"
            f"Total Subscribers: <code>{total_subs}</code>\n"
            f"Active Subscribers: <code>{active_subs}</code>\n"
            f"Expired Subscribers: <code>{expired_subs}</code>\n"
            f"Total Revenue: <code>₹{total_rev}</code>"
        )
        kb = [[InlineKeyboardButton("🔙 Back to Mega Menu", callback_data="mega_admin_menu")]]
        if query.message.photo:
            await query.message.edit_caption(caption=stats_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        else:
            await query.message.edit_text(text=stats_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    elif query.data.startswith("buy_"):
        prod_id = query.data.split("_", 1)[1]
        product = await products_col.find_one({"product_id": prod_id})
        if not product:
            product = await products_col.find_one({"product_id": "default"})
        
        prod_name = product.get("name", "PREMIUM ACCESS") if product else "PREMIUM ACCESS"
        current_price = product.get("price", await get_setting("price")) if product else await get_setting("price")
        current_upi = await get_setting("upi_id")

        existing_pending = await purchases_col.find_one({"user_id": user.id, "status": "pending"})
        if existing_pending:
            await query.message.reply_text("⚠ You already have a payment verification pending with admins.")
            return ConversationHandler.END

        order_id = new_order_id()
        context.user_data["order_id"] = order_id
        context.user_data.pop("mega_selected_plan_id", None)
        qr_bio = generate_upi_qr(current_upi, current_price, name=prod_name)
        payment_text = (
            "✦ <b>PREMIUM PAYMENT</b>\n\n"
            f"📦 Product: {prod_name}\n"
            f"🔹 Amount: ₹{current_price}\n"
            f"🧾 Order ID: <code>{order_id}</code>\n"
            "🔹 Validity: Lifetime\n\n"
            "───────────────────\n\n"
            "🔹 <b>PAYMENT METHODS</b>\n\n"
            "Paytm • GPay • PhonePe • UPI\n\n"
            "UPI ID:\n"
            f"<b>{current_upi}</b>\n\n"
            "<b>AFTER PAYMENT:</b>\n"
            "Send payment screenshot in this chat."
        )

        context.user_data["selected_product_id"] = prod_id
        context.user_data["selected_product_name"] = prod_name
        context.user_data["selected_product_price"] = current_price

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
        support_username = await get_setting("support_username")
        if settings_doc and "file_id" in settings_doc:
            await query.message.reply_video(
                video=settings_doc["file_id"],
                caption=(
                    "🎥 How To Buy\n\n"
                    "1️⃣ Click Buy Premium\n\n"
                    "2️⃣ Pay via QR/UPI\n\n"
                    "3️⃣ Send Payment Screenshot\n\n"
                    "4️⃣ Wait For Verification\n\n"
                    "5️⃣ Get Instant Premium Access ✅\n\n"
                    f"🆘 Support: {support_username}"
                )
            )
        else:
            await query.message.reply_text("Video will be added by admin later.")
        return ConversationHandler.END

    elif query.data == "admin_stats":
        if not await is_admin(user.id):
            await query.answer("Unauthorized!", show_alert=True)
            return ConversationHandler.END

        stats_text = await get_formatted_stats_text()
        keyboard = [[InlineKeyboardButton("🔙 Back to Panel", callback_data="admin_panel")]]
        if query.message.photo:
            await query.message.edit_caption(caption=stats_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        else:
            await query.message.edit_text(text=stats_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    elif query.data == "admin_broadcast":
        if not await is_admin(user.id):
            await query.answer("Unauthorized!", show_alert=True)
            return ConversationHandler.END

        await query.message.reply_text("📢 Send the text or photo you want to broadcast to all users:")
        return WAITING_FOR_BROADCAST

    elif query.data == "set_upi":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("💳 Send the new UPI ID:")
        return SETTING_UPI

    elif query.data == "set_price":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("💰 Send the new Global Price (numeric value only):")
        return SETTING_PRICE

    elif query.data == "set_link":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("🔗 Send the new Global Premium Group Link:")
        return SETTING_LINK

    elif query.data == "set_welcome":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("📝 Send the new Welcome Message template (use {price} for price dynamic tag):")
        return SETTING_WELCOME

    elif query.data == "set_start_photo_menu":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("🖼 Please send the new photo for the start menu:")
        return SETTING_START_PHOTO

    elif query.data == "set_howto_menu":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("🎥 Send the video file with command /sethowto or reply here with the video.")
        return SETTING_HOWTO

    elif query.data == "set_support":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("🆘 Send the new Support Username (e.g., @Vidsell6). This will update everywhere instantly:")
        return SETTING_SUPPORT

    elif query.data == "add_admin_menu":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("➕ Send the Telegram User ID of the new admin:")
        return ADDING_ADMIN

    elif query.data == "remove_admin_menu":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("➖ Send the Telegram User ID of the admin to remove:")
        return REMOVING_ADMIN

    elif query.data == "add_product_menu":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("📦 Send product details in this format:\n`Name | Price | Group Link`\n\nExample:\n`VIP Channel | 99 | https://t.me/+xyz`", parse_mode=ParseMode.HTML)
        return ADDING_PRODUCT

    elif query.data == "remove_product_menu":
        if not await is_admin(user.id):
            return ConversationHandler.END
        products = await products_col.find({}).to_list(length=100)
        if not products:
            await query.message.reply_text("⚠️ No products available to remove.")
            return ConversationHandler.END
        
        kb = []
        for p in products:
            kb.append([InlineKeyboardButton(f"❌ {p.get('name')}", callback_data=f"delprod_{p.get('product_id')}")])
        kb.append([InlineKeyboardButton("🔙 Back to Panel", callback_data="admin_panel")])
        if query.message.photo:
            await query.message.edit_caption(caption="🗑️ Select product to remove:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        else:
            await query.message.edit_text(text="🗑️ Select product to remove:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    elif query.data.startswith("delprod_"):
        if not await is_admin(user.id):
            return ConversationHandler.END
        p_id = query.data.split("_", 1)[1]
        await products_col.delete_one({"product_id": p_id})
        kb = [[InlineKeyboardButton("🔙 Back to Panel", callback_data="admin_panel")]]
        if query.message.photo:
            await query.message.edit_caption(caption="✅ Product removed successfully!", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await query.message.edit_text(text="✅ Product removed successfully!", reply_markup=InlineKeyboardMarkup(kb))
        return ConversationHandler.END

    elif query.data == "manage_products_menu":
        if not await is_admin(user.id):
            return ConversationHandler.END
        products = await products_col.find({}).to_list(length=100)
        if not products:
            await query.message.reply_text("⚠️ No products available to edit.")
            return ConversationHandler.END
        
        kb = []
        for p in products:
            kb.append([InlineKeyboardButton(f"✏️ {p.get('name')}", callback_data=f"editprod_{p.get('product_id')}")])
        kb.append([InlineKeyboardButton("🔙 Back to Panel", callback_data="admin_panel")])
        if query.message.photo:
            await query.message.edit_caption(caption="✏️ Select product to edit/modify name, price, or link:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        else:
            await query.message.edit_text(text="✏️ Select product to edit/modify name, price, or link:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    elif query.data.startswith("editprod_"):
        if not await is_admin(user.id):
            return ConversationHandler.END
        p_id = query.data.split("_", 1)[1]
        context.user_data["editing_product_id"] = p_id
        kb = [
            [InlineKeyboardButton("📝 Change Name", callback_data=f"epname_{p_id}"), InlineKeyboardButton("💰 Change Price", callback_data=f"epprice_{p_id}")],
            [InlineKeyboardButton("🔗 Change Link", callback_data=f"eplink_{p_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="manage_products_menu")]
        ]
        if query.message.photo:
            await query.message.edit_caption(caption=f"✏️ Editing Product ID: <code>{p_id}</code>\nChoose what to change:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        else:
            await query.message.edit_text(text=f"✏️ Editing Product ID: <code>{p_id}</code>\nChoose what to change:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    elif query.data.startswith("epname_"):
        if not await is_admin(user.id):
            return ConversationHandler.END
        p_id = query.data.split("_", 1)[1]
        context.user_data["editing_product_id"] = p_id
        await query.message.reply_text("📝 Send the new name for this product:")
        return EDITING_PRODUCT_NAME

    elif query.data.startswith("epprice_"):
        if not await is_admin(user.id):
            return ConversationHandler.END
        p_id = query.data.split("_", 1)[1]
        context.user_data["editing_product_id"] = p_id
        await query.message.reply_text("💰 Send the new numeric price for this product:")
        return EDITING_PRODUCT_PRICE

    elif query.data.startswith("eplink_"):
        if not await is_admin(user.id):
            return ConversationHandler.END
        p_id = query.data.split("_", 1)[1]
        context.user_data["editing_product_id"] = p_id
        await query.message.reply_text("🔗 Send the new group link for this product:")
        return EDITING_PRODUCT_LINK

    return ConversationHandler.END


async def admin_set_upi_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    new_upi = update.message.text.strip()
    await set_setting("upi_id", new_upi)
    await update.message.reply_text(f"✅ UPI Updated Successfully to: {new_upi}")
    return ConversationHandler.END


async def admin_set_price_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    try:
        new_price = int(update.message.text.strip())
        await set_setting("price", new_price)
        await update.message.reply_text(f"✅ Global Price Updated Successfully to: ₹{new_price}")
    except ValueError:
        await update.message.reply_text("⚠ Invalid price. Please send a valid number.")
    return ConversationHandler.END


async def admin_set_link_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    new_link = update.message.text.strip()
    await set_setting("group_link", new_link)
    await update.message.reply_text("✅ Global Link Updated Successfully")
    return ConversationHandler.END


async def admin_set_welcome_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    new_welcome = update.message.text.strip()
    await set_setting("welcome_text", new_welcome)
    await update.message.reply_text("✅ Welcome Message Updated Successfully")
    return ConversationHandler.END


async def admin_set_start_photo_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    if not update.message.photo:
        await update.message.reply_text("⚠️ Please send a valid photo.")
        return SETTING_START_PHOTO

    file_id = update.message.photo[-1].file_id
    await settings_col.update_one(
        {"key": "start_photo"},
        {"$set": {"key": "start_photo", "file_id": file_id}},
        upsert=True
    )
    await update.message.reply_text("✅ Start Photo updated successfully!")
    return ConversationHandler.END


async def admin_set_howto_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    if not update.message.video:
        await update.message.reply_text("⚠️ Please send a video file.")
        return SETTING_HOWTO
    file_id = update.message.video.file_id
    await settings_col.update_one({"key": "how_to_video"}, {"$set": {"file_id": file_id}}, upsert=True)
    await update.message.reply_text("✅ How to buy video updated successfully!")
    return ConversationHandler.END


async def admin_set_support_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    new_support = update.message.text.strip()
    await set_setting("support_username", new_support)
    await update.message.reply_text(f"✅ Support Username Updated Successfully to {new_support} across all menus and buttons!")
    return ConversationHandler.END


async def mega_add_plan_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    text = update.message.text.strip()
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 3:
        await update.message.reply_text("⚠ Invalid format. Please use: `Name | Price | Days`", parse_mode=ParseMode.HTML)
        return MEGA_ADDING_PLAN

    name, price_str, days_str = parts[0], parts[1], parts[2]
    try:
        price = int(price_str)
        days = int(days_str)
    except ValueError:
        await update.message.reply_text("⚠️ Price and Days must be numeric. Try again:")
        return MEGA_ADDING_PLAN

    plan_id = f"plan_{int(time.time())}"
    await mega_plans_col.insert_one({
        "plan_id": plan_id,
        "name": name,
        "price": price,
        "days": days
    })
    await update.message.reply_text(f"✅ Mega Plan '{name}' added successfully!")
    return ConversationHandler.END


async def mega_change_price_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    p_id = context.user_data.get("mega_editing_plan_id")
    try:
        new_price = int(update.message.text.strip())
        await mega_plans_col.update_one({"plan_id": p_id}, {"$set": {"price": new_price}})
        await update.message.reply_text(f"✅ Mega Plan price updated to: ₹{new_price}")
    except ValueError:
        await update.message.reply_text("⚠ Please enter a valid number.")
    return ConversationHandler.END


async def mega_change_days_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    p_id = context.user_data.get("mega_editing_plan_id")
    try:
        new_days = int(update.message.text.strip())
        await mega_plans_col.update_one({"plan_id": p_id}, {"$set": {"days": new_days}})
        await update.message.reply_text(f"✅ Mega Plan days/validity updated to: {new_days} days")
    except ValueError:
        await update.message.reply_text("⚠️ Please enter a valid number.")
    return ConversationHandler.END


async def mega_change_link_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    new_link = update.message.text.strip()
    await set_setting("mega_group_link", new_link)
    # Reset monitoring tracking when link changes
    await settings_col.update_one({"key": "mega_group_link_alert_sent"}, {"$set": {"value": False}}, upsert=True)
    await update.message.reply_text("✅ Mega Group Chat ID/Link Updated Successfully & Link Expiry Monitoring Reset!")
    return ConversationHandler.END


async def mega_change_caption_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    new_caption = update.message.text.strip()
    await set_setting("mega_caption", new_caption)
    await update.message.reply_text("✅ Mega Access Caption Updated Successfully")
    return ConversationHandler.END


async def mega_change_photo_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    if not update.message.photo:
        await update.message.reply_text("⚠️ Please send a valid photo.")
        return MEGA_CHANGING_PHOTO

    file_id = update.message.photo[-1].file_id
    await settings_col.update_one(
        {"key": "mega_photo"},
        {"$set": {"key": "mega_photo", "file_id": file_id}},
        upsert=True
    )
    await update.message.reply_text("✅ Mega Access Photo updated successfully!")
    return ConversationHandler.END


async def mega_change_menu_name_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    new_name = update.message.text.strip()
    await set_setting("mega_menu_name", new_name)
    await update.message.reply_text(f"✅ Successfully updated the Mega Menu button name to:\n\n{new_name}")
    return ConversationHandler.END


async def add_admin_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    try:
        new_admin_id = int(update.message.text.strip())
        await admins_col.update_one({"user_id": new_admin_id}, {"$set": {"user_id": new_admin_id}}, upsert=True)
        await update.message.reply_text(f"✅ Admin {new_admin_id} added successfully!")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid User ID. Please send a numeric Telegram User ID.")
    return ConversationHandler.END


async def remove_admin_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    try:
        rem_id = int(update.message.text.strip())
        if rem_id in INITIAL_ADMIN_IDS:
            await update.message.reply_text("⚠️ Cannot remove primary default admin.")
            return ConversationHandler.END
        await admins_col.delete_one({"user_id": rem_id})
        await update.message.reply_text(f"✅ Admin {rem_id} removed successfully!")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid User ID.")
    return ConversationHandler.END


async def add_product_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    text = update.message.text.strip()
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 3:
        await update.message.reply_text("⚠️ Invalid format. Please use: `Name | Price | Group Link`", parse_mode=ParseMode.HTML)
        return ADDING_PRODUCT

    name, price_str, link = parts[0], parts[1], parts[2]
    try:
        price = int(price_str)
    except ValueError:
        await update.message.reply_text("⚠️ Price must be numeric. Try again:")
        return ADDING_PRODUCT

    product_id = f"prod_{int(time.time())}"
    await products_col.insert_one({
        "product_id": product_id,
        "name": name,
        "price": price,
        "group_link": link,
        "link_alert_sent": False
    })
    await update.message.reply_text(f"✅ Product '{name}' added successfully with unique link and price!")
    return ConversationHandler.END


async def edit_product_name_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    p_id = context.user_data.get("editing_product_id")
    new_name = update.message.text.strip()
    await products_col.update_one({"product_id": p_id}, {"$set": {"name": new_name}})
    await update.message.reply_text(f"✅ Product name updated to: {new_name}")
    return ConversationHandler.END


async def edit_product_price_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    p_id = context.user_data.get("editing_product_id")
    try:
        new_price = int(update.message.text.strip())
        await products_col.update_one({"product_id": p_id}, {"$set": {"price": new_price}})
        await update.message.reply_text(f"✅ Product price updated to: ₹{new_price}")
    except ValueError:
        await update.message.reply_text("⚠️ Please enter a valid number.")
    return ConversationHandler.END


async def edit_product_link_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    p_id = context.user_data.get("editing_product_id")
    new_link = update.message.text.strip()
    await products_col.update_one({"product_id": p_id}, {"$set": {"group_link": new_link, "link_alert_sent": False}})
    await update.message.reply_text("✅ Product link updated successfully & monitoring reset!")
    return ConversationHandler.END


async def set_howto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return

    if not update.message.video:
        await update.message.reply_text("⚠️ Please send a video with the /sethowto command.")
        return

    file_id = update.message.video.file_id
    await settings_col.update_one({"key": "how_to_video"}, {"$set": {"file_id": file_id}}, upsert=True)
    await update.message.reply_text("✅ How to buy video updated successfully!")


async def receive_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("⚠ Please send a valid image/screenshot of your payment!")
        return WAITING_FOR_SCREENSHOT

    user = update.effective_user
    photo_id = update.message.photo[-1].file_id
    
    if user.username:
        customer_mention = f"@{user.username}"
    else:
        customer_mention = f"<a href=\"tg://user?id={user.id}\">{user.first_name}</a>"

    support_username = await get_setting("support_username")

    mega_plan_id = context.user_data.get("mega_selected_plan_id")
    if mega_plan_id:
        existing_pending = await mega_purchases_col.find_one({"user_id": user.id, "status": "pending"})
        if existing_pending:
            await update.message.reply_text("⚠️ You already have a payment verification pending with admins.")
            return ConversationHandler.END

        plan = await mega_plans_col.find_one({"plan_id": mega_plan_id})
        if not plan:
            plan = {"name": "Mega Plan", "price": 100, "days": 0}

        p_name = plan.get("name", "Mega Plan")
        p_price = plan.get("price", 100)

        purchase_doc = {
            "user_id": user.id,
            "username": user.username or "",
            "plan_id": mega_plan_id,
            "order_id": context.user_data.get("mega_order_id") or new_order_id(),
            "plan_name": p_name,
            "amount": p_price,
            "status": "pending",
            "date": update.message.date,
        }
        await mega_purchases_col.insert_one(purchase_doc)

        pending_msg = (
            "✅ Payment Verification Under Review\n\n"
            f"🧾 Order ID: <code>{purchase_doc['order_id']}</code>\n"
            f"Hello {customer_mention},\n\n"
            "Your payment verification request has been forwarded to our Admin for review. Please wait while the Admin verifies your payment.\n\n"
            "Once your payment is successfully verified, you will receive the group invitation link.\n\n"
            f"👤 Admin: {support_username}\n"
            "⏳ Status: Pending Verification\n\n"
            "Thank you for your patience and cooperation! 🙏"
        )
        await update.message.reply_text(pending_msg, parse_mode=ParseMode.HTML)

        admin_keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"megaapprove_{user.id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"megareject_{user.id}"),
            ]
        ]

        forward_caption = (
            f"🔔 <b>New Mega Payment Verification Request!</b>\n\n"
            f"👤 User ID: <code>{user.id}</code>\n"
            f"🧾 Order ID: <code>{purchase_doc['order_id']}</code>\n"
            f"🔗 Customer: {customer_mention}\n"
            f"📦 Plan: {p_name}\n"
            f"💰 Amount: ₹{p_price}\n\n"
            f"Please check the screenshot below:"
        )

        context.user_data.pop("mega_selected_plan_id", None)

        all_admins = INITIAL_ADMIN_IDS.copy()
        async for adm in admins_col.find({}):
            if adm["user_id"] not in all_admins:
                all_admins.append(adm["user_id"])

        for admin_id in all_admins:
            try:
                await context.bot.send_photo(
                    chat_id=admin_id,
                    photo=photo_id,
                    caption=forward_caption,
                    reply_markup=InlineKeyboardMarkup(admin_keyboard),
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.error(f"Failed to forward mega screenshot to admin {admin_id}: {e}")

        return ConversationHandler.END

    existing_pending = await purchases_col.find_one({"user_id": user.id, "status": "pending"})
    if existing_pending:
        await update.message.reply_text("⚠️ You already have a payment verification pending with admins.")
        return ConversationHandler.END
    
    prod_id = context.user_data.get("selected_product_id", "default")
    product = await products_col.find_one({"product_id": prod_id})
    if not product:
        product = await products_col.find_one({"product_id": "default"})

    prod_name = product.get("name", "PREMIUM ACCESS") if product else "PREMIUM ACCESS"
    current_price = product.get("price", await get_setting("price")) if product else await get_setting("price")

    purchase_doc = {
        "user_id": user.id,
        "username": user.username or "",
        "product_id": prod_id,
        "order_id": context.user_data.get("order_id") or new_order_id(),
        "product_name": prod_name,
        "amount": current_price,
        "status": "pending",
        "date": update.message.date,
    }
    await purchases_col.insert_one(purchase_doc)

    pending_msg = (
        "✅ Payment Verification Under Review\n\n"
        f"🧾 Order ID: <code>{purchase_doc['order_id']}</code>\n"
        f"Hello {customer_mention},\n\n"
        "Your payment verification request has been forwarded to our Admin for review. Please wait while the Admin verifies your payment.\n\n"
        "Once your payment is successfully verified, you will receive the group invitation link.\n\n"
        f"👤 Admin: {support_username}\n"
        "⏳ Status: Pending Verification\n\n"
        "Thank you for your patience and cooperation! 🙏"
    )
    await update.message.reply_text(pending_msg, parse_mode=ParseMode.HTML)

    admin_keyboard = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user.id}"),
        ]
    ]

    forward_caption = (
        f"🔔 <b>New Payment Verification Request!</b>\n\n"
        f"👤 User ID: <code>{user.id}</code>\n"
        f"🔗 Customer: {customer_mention}\n"
        f"📦 Product: {prod_name}\n"
        f"💰 Amount: ₹{current_price}\n\n"
        f"Please check the screenshot below:"
    )

    all_admins = INITIAL_ADMIN_IDS.copy()
    async for adm in admins_col.find({}):
        if adm["user_id"] not in all_admins:
            all_admins.append(adm["user_id"])

    for admin_id in all_admins:
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

    if not await is_admin(query.from_user.id):
        await query.answer("You are not authorized!", show_alert=True)
        return

    data = query.data.split("_")
    action = data[0]
    target_user_id = int(data[1])

    support_username = await get_setting("support_username")
    
    try:
        target_user = await context.bot.get_chat(target_user_id)
        if target_user.username:
            customer_mention = f"@{target_user.username}"
        else:
            customer_mention = f"<a href=\"tg://user?id={target_user_id}\">{target_user.first_name or 'Customer'}</a>"
    except Exception:
        customer_mention = f"<a href=\"tg://user?id={target_user_id}\">Customer</a>"

    if action in ["megaapprove", "megareject"]:
        purchase = await mega_purchases_col.find_one({"user_id": target_user_id, "status": "pending"})
        if not purchase:
            await query.answer("⚠️ This mega payment has already been processed or does not exist.", show_alert=True)
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            return

        admin_name = query.from_user.first_name or "Admin"

        if action == "megaapprove":
            await mega_purchases_col.update_one(
                {"user_id": target_user_id, "status": "pending"},
                {"$set": {"status": "approved"}},
            )

            plan_id = purchase.get("plan_id")
            plan = await mega_plans_col.find_one({"plan_id": plan_id})
            days = plan.get("days", 0) if plan else 0
            plan_name = purchase.get("plan_name", "Mega Plan")

            now = datetime.utcnow()
            if days > 0:
                expiry_time = now + timedelta(days=days)
            else:
                expiry_time = None

            mega_group_target = await get_setting("mega_group_link")
            invite_link = mega_group_target
            try:
                link_obj = await context.bot.create_chat_invite_link(
                    chat_id=mega_group_target,
                    member_limit=1,
                    creates_join_request=False
                )
                invite_link = link_obj.invite_link
            except Exception as e:
                logger.error(f"Failed to create single-use invite link for mega: {e}")

            await mega_subs_col.update_one(
                {"user_id": target_user_id},
                {
                    "$set": {
                        "user_id": target_user_id,
                        "plan_id": plan_id,
                        "plan_name": plan_name,
                        "approval_time": now,
                        "expiry_time": expiry_time,
                        "status": "active",
                        "invite_link": invite_link
                    }
                },
                upsert=True
            )

            success_msg = (
                "🔐 Here's your exclusive access link to join the premium channel\n\n"
                f"🔗 {invite_link}\n\n"
                "This link is valid for only 1 member be used\n\n"
                "Don't leave this channel. If you leave the channel it means your membership is over. Any condition rejoin penalty Membership Half Amount\n\n"
                f"Your subscription will be active for {days if days > 0 else 'Permanent'} days"
            )
            try:
                await context.bot.send_message(
                    chat_id=target_user_id, text=success_msg, parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Could not message user {target_user_id}: {e}")

            await query.message.edit_caption(
                caption=query.message.caption + f"\n\n🟢 <b>MEGA APPROVED by {admin_name}</b>",
                reply_markup=None,
                parse_mode=ParseMode.HTML,
            )

        elif action == "megareject":
            await mega_purchases_col.update_one(
                {"user_id": target_user_id, "status": "pending"},
                {"$set": {"status": "rejected"}},
            )

            reject_msg = (
                "⚠️ PAYMENT VERIFICATION FAILED\n\n"
                f"Dear {customer_mention},\n\n"
                f"Unfortunately, your payment could not be verified. Please contact our Admin {support_username} to resolve this issue and receive further assistance.\n\n"
                "📌 Status: Verification Failed\n"
                f"👤 Contact Admin: {support_username}\n\n"
                "Thank you for your patience and cooperation. 🙏"
            )
            try:
                await context.bot.send_message(
                    chat_id=target_user_id, text=reject_msg, parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Could not message user {target_user_id}: {e}")

            await query.message.edit_caption(
                caption=query.message.caption + f"\n\n🔴 <b>MEGA REJECTED by {admin_name}</b>",
                reply_markup=None,
                parse_mode=ParseMode.HTML,
            )
        return

    purchase = await purchases_col.find_one({"user_id": target_user_id, "status": "pending"})
    if not purchase:
        await query.answer("⚠️ This payment has already been processed or does not exist.", show_alert=True)
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    admin_name = query.from_user.first_name or "Admin"

    prod_id = purchase.get("product_id", "default")
    product = await products_col.find_one({"product_id": prod_id})
    if product and product.get("group_link"):
        group_link = product.get("group_link")
    else:
        group_link = await get_setting("group_link")

    if action == "approve":
        await purchases_col.update_one(
            {"user_id": target_user_id, "status": "pending"},
            {"$set": {"status": "approved"}},
        )

        success_msg = (
            "✅ Payment Received Successfully!\n\n"
            "Hi 👋\n\n"
            "Thank you for your payment 💕\n\n"
            "🔗 Your private channel/product link 👇\n"
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

        reject_msg = (
            "⚠️ PAYMENT VERIFICATION FAILED\n\n"
            f"Dear {customer_mention},\n\n"
            f"Unfortunately, your payment could not be verified. Please contact our Admin {support_username} to resolve this issue and receive further assistance.\n\n"
            "📌 Status: Verification Failed\n"
            f"👤 Contact Admin: {support_username}\n\n"
            "Thank you for your patience and cooperation. 🙏"
        )
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


async def check_expiring_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    try:
        now = datetime.utcnow()
        cursor = mega_subs_col.find({"status": "active", "expiry_time": {"$ne": None, "$lte": now}})
        async for sub in cursor:
            user_id = sub["user_id"]
            invite_link_to_revoke = sub.get("invite_link")
            mega_group_target = await get_setting("mega_group_link")
            plan_name = sub.get("plan_name", "Mega Plan")
            approval_time = sub.get("approval_time")
            expiry_time = sub.get("expiry_time")
            
            start_date_str = approval_time.strftime("%Y-%m-%d") if approval_time else "N/A"
            end_date_str = expiry_time.strftime("%Y-%m-%d") if expiry_time else "N/A"
            
            try:
                expiry_message = (
                    "📅 **Your Subscription Has Expired**\n\n"
                    f"Your subscription to **{plan_name}** has expired and you will be removed from the Channel\n\n"
                    "**Subscription details**\n"
                    f"• Plan: {plan_name}\n"
                    f"• Start date: {start_date_str}\n"
                    f"• End date: {end_date_str}\n\n"
                    "To continue enjoying our premium content, please renew your Subscription\n"
                    "1. Use the /start command\n"
                    "2. Select 'Mega Group Link'\n"
                    "3. Choose your desired plan\n\n"
                    "For any questions or assistance, contact our support team"
                )
                await context.bot.send_message(chat_id=user_id, text=expiry_message, parse_mode=ParseMode.MARKDOWN)
                
                chat_target = int(mega_group_target) if str(mega_group_target).startswith("-") else mega_group_target
                await context.bot.ban_chat_member(chat_id=chat_target, user_id=user_id)
                await context.bot.unban_chat_member(chat_id=chat_target, user_id=user_id)
                if invite_link_to_revoke:
                    try:
                        await context.bot.revoke_chat_invite_link(chat_id=chat_target, invite_link=invite_link_to_revoke)
                    except Exception:
                        pass
                
                logger.info(f"User {user_id} subscription expired. Notification sent and user removed.")
            except Exception as e:
                logger.error(f"Error processing expiry for user {user_id}: {e}")

            await mega_subs_col.update_one({"user_id": user_id}, {"$set": {"status": "expired"}})
    except Exception as e:
        logger.error(f"Error in check_expiring_subscriptions job: {e}")


async def check_invite_link_validity(context: ContextTypes.DEFAULT_TYPE):
    """
    Background monitoring job to check product/group invite links for validity/expiry
    using Telegram Bot API capabilities (e.g., getting chat/invite link details where supported).
    """
    try:
        all_admins = INITIAL_ADMIN_IDS.copy()
        async for adm in admins_col.find({}):
            if adm["user_id"] not in all_admins:
                all_admins.append(adm["user_id"])

        # 1. Monitor products collection links
        products = await products_col.find({}).to_list(length=100)
        for prod in products:
            p_name = prod.get("name", "Product")
            link = prod.get("group_link", "")
            alert_sent = prod.get("link_alert_sent", False)
            
            is_invalid = False
            if link.startswith("-100") or link.startswith("@") or link.startswith("https://t.me/+"):
                # If it's a chat ID or public username/invite link, we can check via get_chat if accessible
                try:
                    if link.startswith("-100") or link.startswith("@"):
                        await context.bot.get_chat(chat_id=link)
                except Exception:
                    is_invalid = True
            else:
                if not link:
                    is_invalid = True

            if is_invalid and not alert_sent:
                alert_text = (
                    "⚠️ PRODUCT INVITE LINK ALERT\n\n"
                    f"📦 Product Name: {p_name}\n\n"
                    "🔗 Status: Old Invite Link Expired / Invalid\n\n"
                    "❌ This product's old Telegram invitation link is no longer valid.\n\n"
                    "🛠️ Action Required: Please update the product with a new valid invitation link."
                )
                for admin_id in all_admins:
                    try:
                        await context.bot.send_message(chat_id=admin_id, text=alert_text, parse_mode=ParseMode.HTML)
                    except Exception as e:
                        logger.error(f"Failed to send link alert to admin {admin_id}: {e}")
                
                await products_col.update_one({"product_id": prod.get("product_id")}, {"$set": {"link_alert_sent": True}})

        # 2. Monitor global Mega group link
        mega_link = await get_setting("mega_group_link")
        mega_alert_doc = await settings_col.find_one({"key": "mega_group_link_alert_sent"})
        mega_alert_sent = mega_alert_doc.get("value", False) if mega_alert_doc else False

        mega_invalid = False
        if mega_link and (mega_link.startswith("-100") or mega_link.startswith("@")):
            try:
                await context.bot.get_chat(chat_id=mega_link)
            except Exception:
                mega_invalid = True
        elif not mega_link:
            mega_invalid = True

        if mega_invalid and not mega_alert_sent:
            alert_text = (
                "⚠️ PRODUCT INVITE LINK ALERT\n\n"
                "📦 Product Name: Mega Access Group\n\n"
                "🔗 Status: Old Invite Link Expired / Invalid\n\n"
                "❌ This product's old Telegram invitation link is no longer valid.\n\n"
                "🛠️ Action Required: Please update the product with a new valid invitation link."
            )
            for admin_id in all_admins:
                try:
                    await context.bot.send_message(chat_id=admin_id, text=alert_text, parse_mode=ParseMode.HTML)
                except Exception as e:
                    logger.error(f"Failed to send mega link alert to admin {admin_id}: {e}")
            
            await settings_col.update_one({"key": "mega_group_link_alert_sent"}, {"$set": {"value": True}}, upsert=True)

    except Exception as e:
        logger.error(f"Error in check_invite_link_validity job: {e}")


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return

    await update.message.reply_text("📢 Send the text or photo you want to broadcast to all users:")
    return WAITING_FOR_BROADCAST


async def _broadcast_worker(application, admin_chat_id, status_message_id, photo_file, caption_text, body_text):
    """Original all-user batch broadcast, running in a background task."""
    bot = application.bot
    start_time = time.monotonic()
    success = fail = 0
    try:
        # Do not silently exclude users marked blocked by an earlier broadcast.
        users = await users_col.find({}, {"user_id": 1}).to_list(length=None)
        total = len(users)
        if not total:
            await bot.edit_message_text(chat_id=admin_chat_id, message_id=status_message_id,
                                        text="⚠️ No users found in database.")
            return

        async def send_msg(uid):
            if not isinstance(uid, int):
                return False
            for attempt in range(3):
                try:
                    if photo_file:
                        await bot.send_photo(chat_id=uid, photo=photo_file, caption=caption_text)
                    else:
                        await bot.send_message(chat_id=uid, text=body_text)
                    return True
                except RetryAfter as exc:
                    await asyncio.sleep(min(float(exc.retry_after) + 1, 120))
                except (TimedOut, NetworkError) as exc:
                    logger.warning("Temporary broadcast error for %s: %s", uid, exc)
                    if attempt < 2:
                        await asyncio.sleep(attempt + 1)
                except Forbidden as exc:
                    logger.info("Cannot deliver to user %s: %s", uid, exc)
                    return False
                except Exception as exc:
                    logger.warning("Broadcast failed for %s: %s", uid, exc)
                    return False
            return False

        last_update = start_time
        for i in range(0, total, 30):
            chunk = users[i:i + 30]
            results = await asyncio.gather(*(send_msg(u.get("user_id")) for u in chunk))
            success += sum(results)
            fail += len(results) - sum(results)
            processed = success + fail
            now = time.monotonic()
            if now - last_update >= 3 or processed == total:
                elapsed = max(now - start_time, 0.001)
                remaining = total - processed
                eta = int(remaining * elapsed / processed)
                minutes, seconds = divmod(eta, 60)
                progress_text = (
                    "🚀 <b>Broadcast In Progress...</b>\n\n"
                    f"📊 Total Target: <code>{total}</code>\n"
                    f"✅ Sent: <code>{success}</code>\n"
                    f"❌ Failed: <code>{fail}</code>\n"
                    f"⏳ Remaining: <code>{remaining}</code>\n"
                    f"📈 Progress: <code>{processed * 100 // total}%</code>\n"
                    f"⏱️ ETA: <code>{minutes}m {seconds}s</code>"
                )
                try:
                    await bot.edit_message_text(chat_id=admin_chat_id,
                                                message_id=status_message_id,
                                                text=progress_text, parse_mode=ParseMode.HTML)
                except Exception as exc:
                    logger.warning("Broadcast progress update failed: %s", exc)
                last_update = now
            # Slow batches enough to leave API capacity for commands and buttons.
            await asyncio.sleep(1)

        elapsed = int(time.monotonic() - start_time)
        minutes, seconds = divmod(elapsed, 60)
        final_text = (
            "✅ <b>Broadcast Completed!</b>\n\n"
            f"📊 Total Target: <code>{total}</code>\n"
            f"✅ Success: <code>{success}</code>\n"
            f"❌ Failed: <code>{fail}</code>\n"
            f"⏱️ Time Taken: <code>{minutes}m {seconds}s</code>"
        )
        try:
            await bot.edit_message_text(chat_id=admin_chat_id, message_id=status_message_id,
                                        text=final_text, parse_mode=ParseMode.HTML)
        except Exception:
            await bot.send_message(chat_id=admin_chat_id, text=final_text,
                                   parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Unexpected broadcast error")
        try:
            await bot.send_message(chat_id=admin_chat_id,
                                   text=f"⚠️ Broadcast interrupted. Sent: {success}, Failed: {fail}.")
        except Exception:
            logger.exception("Unable to report broadcast interruption")
    finally:
        application.bot_data["broadcast_running"] = False


async def execute_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END

    application = context.application
    if application.bot_data.get("broadcast_running"):
        await update.message.reply_text("⚠️ A broadcast is already running. Please wait for it to finish.")
        return ConversationHandler.END

    photo_file = update.message.photo[-1].file_id if update.message.photo else None
    caption_text = update.message.caption or ""
    body_text = update.message.text or ""

    # Set the flag before yielding to avoid starting two simultaneous broadcasts.
    application.bot_data["broadcast_running"] = True
    try:
        status_msg = await update.message.reply_text(
            "🚀 Broadcast started in background. You can keep using the bot."
        )
        application.create_task(
            _broadcast_worker(
                application,
                update.effective_chat.id,
                status_msg.message_id,
                photo_file,
                caption_text,
                body_text,
            ),
            name="broadcast_worker",
        )
    except Exception:
        application.bot_data["broadcast_running"] = False
        logger.exception("Failed to start background broadcast")
        await update.message.reply_text("❌ Could not start broadcast. Please try again.")

    return ConversationHandler.END


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return

    stats_text = await get_formatted_stats_text()
    await update.message.reply_text(stats_text, parse_mode=ParseMode.HTML)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return
    admin_text = "👑 <b>ADMIN PANEL</b>\n\nChoose an action below:"
    keyboard = [
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats"), InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("💳 Change UPI ID", callback_data="set_upi"), InlineKeyboardButton("💰 Change Global Price", callback_data="set_price")],
        [InlineKeyboardButton("🔗 Change Global Link", callback_data="set_link"), InlineKeyboardButton("📝 Change Welcome", callback_data="set_welcome")],
        [InlineKeyboardButton("🖼 Change Start Photo", callback_data="set_start_photo_menu"), InlineKeyboardButton("🎥 Change HowTo Video", callback_data="set_howto_menu")],
        [InlineKeyboardButton("🆘 Change Support", callback_data="set_support")],
        [InlineKeyboardButton("➕ Add Admin", callback_data="add_admin_menu"), InlineKeyboardButton("➖ Remove Admin", callback_data="remove_admin_menu")],
        [InlineKeyboardButton("📦 Add Product", callback_data="add_product_menu"), InlineKeyboardButton("🗑️ Remove Product", callback_data="remove_product_menu")],
        [InlineKeyboardButton("✏️ Manage Products", callback_data="manage_products_menu")],
        [InlineKeyboardButton("📁 Mega File Manager", callback_data="mega_admin_menu")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")],
    ]
    await update.message.reply_text(admin_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


def main():
    keep_alive()

    app = Application.builder().token(BOT_TOKEN).build()

    async def post_init(application: Application):
        await initialize_settings()
        if application.job_queue:
            application.job_queue.run_repeating(check_expiring_subscriptions, interval=60, first=10)
            application.job_queue.run_repeating(check_invite_link_validity, interval=300, first=30)
        logger.info("Bot is up and running...")

    app.post_init = post_init

    callback_pattern = (
        "^(buy_.*|how|main_menu|admin_panel|admin_stats|admin_broadcast|set_upi|set_price|"
        "set_link|set_welcome|set_start_photo_menu|set_howto_menu|set_support|add_admin_menu|remove_admin_menu|"
        "add_product_menu|remove_product_menu|manage_products_menu|delprod_.*|editprod_.*|"
        "epname_.*|epprice_.*|eplink_.*|mega_access_menu|megabuy_.*|mega_admin_menu|"
        "mega_add_plan_menu|mega_remove_plan_menu|megadelplan_.*|mega_change_price_menu|"
        "megachprice_.*|mega_change_days_menu|megachdays_.*|mega_set_link|mega_set_caption|"
        "mega_set_photo|mega_change_menu_name|mega_stats)$"
    )

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(button_router, pattern=callback_pattern),
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
            SETTING_START_PHOTO: [MessageHandler(filters.PHOTO, admin_set_start_photo_receive)],
            SETTING_HOWTO: [MessageHandler(filters.VIDEO, admin_set_howto_receive)],
            SETTING_SUPPORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_support_receive)],
            ADDING_ADMIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin_receive)],
            REMOVING_ADMIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_admin_receive)],
            ADDING_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_receive)],
            EDITING_PRODUCT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_product_name_receive)],
            EDITING_PRODUCT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_product_price_receive)],
            EDITING_PRODUCT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_product_link_receive)],
            MEGA_ADDING_PLAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, mega_add_plan_receive)],
            MEGA_CHANGING_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, mega_change_price_receive)],
            MEGA_CHANGING_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, mega_change_days_receive)],
            MEGA_CHANGING_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, mega_change_link_receive)],
            MEGA_CHANGING_CAPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, mega_change_caption_receive)],
            MEGA_CHANGING_PHOTO: [MessageHandler(filters.PHOTO, mega_change_photo_receive)],
            MEGA_CHANGING_MENU_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, mega_change_menu_name_receive)],
        },
        fallbacks=[
            CommandHandler("start", start),
            CallbackQueryHandler(button_router, pattern=callback_pattern),
        ],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("sethowto", set_howto_command))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(admin_action, pattern="^(approve|reject|megaapprove|megareject)_"))

    app.run_polling()


if __name__ == "__main__":
    main()
