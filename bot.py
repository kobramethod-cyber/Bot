# -*- coding: utf-8 -*-
import io
import logging
import os
import time
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
    # Mega defaults
    "mega_group_link": "https://t.me/+H_y0MBr_c0g2ZDk1",
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
    # Mega Admin States
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
    """Fetch the dynamic name from MongoDB settings collection, fallback to default."""
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

    # Initialize default Mega plans if empty
    mega_count = await mega_plans_col.count_documents({})
    if mega_count == 0:
        default_plans = [
            {"plan_id": "plan_1day", "name": "1 Day", "price": 70, "days": 1},
            {"plan_id": "plan_1month", "name": "1 Month", "price": 300, "days": 30},
            {"plan_id": "plan_permanent", "name": "Permanent", "price": 800, "days": 0},
        ]
        for p in default_plans:
            await mega_plans_col.update_one({"plan_id": p["plan_id"]}, {"$set": p}, upsert=True)


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

    # Add Mega Access Button (using dynamic name)
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
        [InlineKeyboardButton("📦 Add Product", callback_data="add_product_menu"), InlineKeyboardButton("🗑️️ Remove Product", callback_data="remove_product_menu")],
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

    # ================= MEGA ACCESS ROUTING =================
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

        qr_bio = generate_upi_qr(current_upi, p_price, name=f"Mega {p_name}")
        payment_text = (
            "✦ <b>MEGA PAYMENT</b>\n\n"
            f"📦 Plan: {p_name}\n"
            f"🔹 Amount: ₹{p_price}\n"
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

    # ================= MEGA ADMIN MENU ROUTING =================
    elif query.data == "mega_admin_menu":
        if not await is_admin(user.id):
            await query.answer("Unauthorized!", show_alert=True)
            return ConversationHandler.END

        text = "📁 <b>MEGA FILE MANAGER ADMIN</b>\n\nChoose an action below:"
        kb = [
            [InlineKeyboardButton("➕ Add Plan", callback_data="mega_add_plan_menu"), InlineKeyboardButton("🗑 Remove Plan", callback_data="mega_remove_plan_menu")],
            [InlineKeyboardButton("💰 Change Price", callback_data="mega_change_price_menu"), InlineKeyboardButton("📅 Change Days", callback_data="mega_change_days_menu")],
            [InlineKeyboardButton("🔗 Change Group Link", callback_data="mega_set_link"), InlineKeyboardButton("📝 Change Caption", callback_data="mega_set_caption")],
            [InlineKeyboardButton("🖼 Change Photo", callback_data="mega_set_photo"), InlineKeyboardButton("📊 Mega Stats", callback_data="mega_stats")],
            [InlineKeyboardButton("✏️ Change Mega Menu Name", callback_data="mega_change_menu_name")],
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
        await query.message.reply_text("🔗 Send the new Mega Group/Channel Link:")
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
        day1_subs = await mega_subs_col.count_documents({"plan_name": {"$regex": "1 Day", "$options": "i"}})
        day7_subs = await mega_subs_col.count_documents({"plan_name": {"$regex": "7 Day", "$options": "i"}})
        day30_subs = await mega_subs_col.count_documents({"plan_name": {"$regex": "1 Month|30 Day", "$options": "i"}})
        perm_subs = await mega_subs_col.count_documents({"plan_name": {"$regex": "Permanent", "$options": "i"}})

        pipeline = [{"$match": {"status": "approved"}}, {"$group": {"_id": None, "total": {"$sum": "$amount"}}}]
        rev_cursor = mega_purchases_col.aggregate(pipeline)
        rev_list = await rev_cursor.to_list(length=1)
        total_rev = rev_list[0]["total"] if rev_list else 0

        stats_text = (
            f"📊 <b>Mega File Manager Statistics</b>\n\n"
            f"Total Subscribers: <code>{total_subs}</code>\n"
            f"Active Subscribers: <code>{active_subs}</code>\n"
            f"Expired Subscribers: <code>{expired_subs}</code>\n"
            f"1 Day Users: <code>{day1_subs}</code>\n"
            f"7 Day Users: <code>{day7_subs}</code>\n"
            f"30 Day Users: <code>{day30_subs}</code>\n"
            f"Permanent Users: <code>{perm_subs}</code>\n"
            f"Total Revenue: <code>₹{total_rev}</code>"
        )
        kb = [[InlineKeyboardButton("🔙 Back to Mega Menu", callback_data="mega_admin_menu")]]
        if query.message.photo:
            await query.message.edit_caption(caption=stats_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        else:
            await query.message.edit_text(text=stats_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    # ================= END MEGA ROUTING =================

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

        qr_bio = generate_upi_qr(current_upi, current_price, name=prod_name)
        payment_text = (
            "✦ <b>PREMIUM PAYMENT</b>\n\n"
            f"📦 Product: {prod_name}\n"
            f"🔹 Amount: ₹{current_price}\n"
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
        await query.message.reply_text("📦 Send product details in this format:\n`Product Name | Price | Group Link`\n\nExample:\n`VIP Channel | 99 | https://t.me/+xyz`", parse_mode=ParseMode.HTML)
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
        await update.message.reply_text("⚠️ Invalid price. Please send a valid number.")
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


# ================= MEGA ADMIN RECEIVE HANDLERS =================
async def mega_add_plan_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    text = update.message.text.strip()
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 3:
        await update.message.reply_text("⚠️ Invalid format. Please use: `Name | Price | Days`", parse_mode=ParseMode.HTML)
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
        await update.message.reply_text("⚠️️ Please enter a valid number.")
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
    await update.message.reply_text("✅ Mega Group Link Updated Successfully")
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
# ================= END MEGA ADMIN RECEIVE =================


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
        "group_link": link
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
    await products_col.update_one({"product_id": p_id}, {"$set": {"group_link": new_link}})
    await update.message.reply_text("✅ Product link updated successfully!")
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
        await update.message.reply_text("⚠️️ Please send a valid image/screenshot of your payment!")
        return WAITING_FOR_SCREENSHOT

    user = update.effective_user
    photo_id = update.message.photo[-1].file_id
    username = f"@{user.username}" if user.username else "No Username"

    # Check if this is a Mega purchase or regular product purchase
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
            "username": username,
            "plan_id": mega_plan_id,
            "plan_name": p_name,
            "amount": p_price,
            "status": "pending",
            "date": update.message.date,
        }
        await mega_purchases_col.insert_one(purchase_doc)

        await update.message.reply_text(
            "✅ <b>Screenshot Received Successfully!</b>\n\nYour payment has been sent to admins for verification. Please wait a moment.",
            parse_mode=ParseMode.HTML,
        )

        admin_keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"megaapprove_{user.id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"megareject_{user.id}"),
            ]
        ]

        forward_caption = (
            f"🔔 <b>New Mega Payment Verification Request!</b>\n\n"
            f"👤 User ID: <code>{user.id}</code>\n"
            f"🔗 Username: {username}\n"
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

    # Regular Product Flow (Unchanged)
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
        "username": username,
        "product_id": prod_id,
        "product_name": prod_name,
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

    # Check if Mega Approval
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
        support_username = await get_setting("support_username")

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
                expiry_time = None  # Permanent

            await mega_subs_col.update_one(
                {"user_id": target_user_id},
                {
                    "$set": {
                        "user_id": target_user_id,
                        "plan_id": plan_id,
                        "plan_name": plan_name,
                        "approval_time": now,
                        "expiry_time": expiry_time,
                        "status": "active"
                    }
                },
                upsert=True
            )

            # Create single-use unique Telegram invite link for Mega
            mega_group_link = await get_setting("mega_group_link")
            invite_link = mega_group_link
            try:
                if mega_group_link.startswith("https://t.me/+"):
                    # Extract channel/chat identifier if stored as handle or ID, or use chat id directly if stored
                    pass
                link_obj = await context.bot.create_chat_invite_link(
                    chat_id=mega_group_link,
                    member_limit=1,
                    creates_join_request=False
                )
                invite_link = link_obj.invite_link
            except Exception as e:
                logger.error(f"Failed to create single-use invite link for mega: {e}")

            # Save the generated invite link to sub record so we can revoke it later upon expiration
            await mega_subs_col.update_one(
                {"user_id": target_user_id},
                {"$set": {"invite_link": invite_link}}
            )

            success_msg = (
                "✅ Payment Approved\n\n"
                "Join Here (Single-Use Link):\n"
                f"{invite_link}"
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

            reject_msg = f"❌ Payment Verification Failed\n\nPlease contact admin:\n{support_username}"
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

    # Regular Product Approval Flow (Unchanged)
    purchase = await purchases_col.find_one({"user_id": target_user_id, "status": "pending"})
    if not purchase:
        await query.answer("⚠️ This payment has already been processed or does not exist.", show_alert=True)
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    admin_name = query.from_user.first_name or "Admin"
    support_username = await get_setting("support_username")

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


async def check_expiring_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    """Continuously checks active subscriptions and removes expired users for Mega product."""
    try:
        now = datetime.utcnow()
        cursor = mega_subs_col.find({"status": "active", "expiry_time": {"$ne": None, "$lte": now}})
        async for sub in cursor:
            user_id = sub["user_id"]
            invite_link_to_revoke = sub.get("invite_link")
            mega_group_link = await get_setting("mega_group_link")
            
            try:
                # 1. User ko expiry message bhejein
                expiry_message = (
                    "⚠️ **Aapki Membership Expire Ho Chuki Hai!**\n\n"
                    "Aapka plan khatam ho gaya hai aur aapko group se remove kar diya gaya hai. "
                    "Dobara access paane ke liye naya plan purchase karein."
                )
                await context.bot.send_message(chat_id=user_id, text=expiry_message, parse_mode=ParseMode.MARKDOWN)
                
                # 2. Group se user ko remove karein aur link revoke karein
                target_chat = mega_group_link
                await context.bot.ban_chat_member(chat_id=target_chat, user_id=user_id)
                await context.bot.unban_chat_member(chat_id=target_chat, user_id=user_id)
                if invite_link_to_revoke:
                    await context.bot.revoke_chat_invite_link(chat_id=target_chat, invite_link=invite_link_to_revoke)
                
                print(f"User {user_id} ko expiry message bhej diya gaya aur remove kar diya gaya.")
            except Exception as e:
                logger.error(f"Error processing expiry for user {user_id}: {e}")

            await mega_subs_col.update_one({"user_id": user_id}, {"$set": {"status": "expired"}})
    except Exception as e:
        logger.error(f"Error in check_expiring_subscriptions job: {e}")


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return

    await update.message.reply_text("📢 Send the text or photo you want to broadcast to all users:")
    return WAITING_FOR_BROADCAST


async def execute_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await is_admin(update.message.from_user.id):
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
    if not await is_admin(update.message.from_user.id):
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
            # Mega States
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
