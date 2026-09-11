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
from bson import ObjectId

Logging Setup

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(name)

Environment Variables & Configuration

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

Default fallback configurations (will be overridden/initialized from MongoDB settings)

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

Admin & Product Conversation States

(
    SETTING_UPI,
    SETTING_PRICE,
    SETTING_LINK,
    SETTING_WELCOME,
    SETTING_HOWTO,
    SETTING_SUPPORT,
    SETTING_ADD_ADMIN,
    SETTING_REMOVE_ADMIN,
    SETTING_ADD_PROD_NAME,
    SETTING_ADD_PROD_PRICE,
    SETTING_ADD_PROD_LINK,
    SETTING_EDIT_PROD_CHOICE,
    SETTING_EDIT_PROD_VALUE,
    SETTING_REMOVE_PROD,
) = range(10, 24)

Initialize MongoDB via Motor

client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = client["premium_access_hub"]
users_col = db["users"]
purchases_col = db["purchases"]
settings_col = db["settings"]
admins_col = db["admins"]
products_col = db["products"]

ADDED FOR UPTIMEROBOT: Flask app for keep-alive

app_flask = Flask(name)

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

Initialize Admins

admin_count = await admins_col.count_documents({})
if admin_count == 0:
    default_admins = [1936430807, 8720701910]
    for aid in default_admins:
        await admins_col.update_one({"user_id": aid}, {"$set": {"user_id": aid}}, upsert=True)

Initialize Products

prod_count = await products_col.count_documents({})
if prod_count == 0:
    await products_col.insert_one({
        "name": "PREMIUM ACCESS",
        "price": DEFAULT_SETTINGS["price"],
        "link": DEFAULT_SETTINGS["group_link"]
    })

async def is_admin(user_id: int) -> bool:
    doc = await admins_col.find_one({"user_id": user_id})
    return doc is not None

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

    if await is_admin(user.id):
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

    if not await is_admin(user.id):
        await query.answer("Unauthorized!", show_alert=True)
        return

    admin_text = "👑 <b>ADMIN PANEL</b>\n\nChoose an action below:"
    keyboard = [
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats"), InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("💳 Change UPI ID", callback_data="set_upi"), InlineKeyboardButton("💰 Change Price", callback_data="set_price")],
        [InlineKeyboardButton("🔗 Change Link", callback_data="set_link"), InlineKeyboardButton("📝 Change Welcome", callback_data="set_welcome")],
        [InlineKeyboardButton("🎥 Change HowTo Video", callback_data="set_howto_menu"), InlineKeyboardButton("🆘 Change Support", callback_data="set_support")],
        [InlineKeyboardButton("📦 View Products", callback_data="view_products"), InlineKeyboardButton("➕ Add Product", callback_data="add_product")],
        [InlineKeyboardButton("✏️ Edit Product", callback_data="edit_product_menu"), InlineKeyboardButton("❌ Remove Product", callback_data="remove_product_menu")],
        [InlineKeyboardButton("👥 View Admins", callback_data="view_admins"), InlineKeyboardButton("➕ Add Admin", callback_data="add_admin")],
        [InlineKeyboardButton("➖ Remove Admin", callback_data="remove_admin")],
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
        if await is_admin(user.id):  
          keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])  

        try:  
          await query.message.edit_caption(caption=start_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)  
        except Exception:  
          await query.message.delete()  
          await query.message.reply_photo(photo=PHOTO_ID if "PHOTO_ID" in globals() else "AgACAgUAAxkBAAICYGqawsPSsd-rVZF8QNyGGavXiRnYAAJ0FGsbNXDQVB25ko4WD9yEAQADAgADeAADPQQ", caption=start_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)  
        return ConversationHandler.END

    elif query.data == "admin_panel":
        if not await is_admin(user.id):
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

        # Fetch default/first product for payment  
        product = await products_col.find_one({})  
        if product:  
          current_price = product["price"]  

        qr_bio = generate_upi_qr(current_upi, current_price)  
        payment_text = (  
            "✦ <b>𝗣𝗥𝗘𝗠𝗜𝗨𝗠 𝗣𝗔𝗬𝗠𝗘𝗡𝗧</b>\n\n"  
            f"📦 Product: {product['name'] if product else 'PREMIUM ACCESS'}\n"  
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
        support_username = await get_setting("support_username")
        settings_doc = await settings_col.find_one({"key": "how_to_video"})
        if settings_doc and "file_id" in settings_doc:
            price = await get_setting("price")
            await query.message.reply_video(
                video=settings_doc["file_id"],
                caption=(
                    "🎥 How To Buy\n\n"
                    "1️⃣ Click Buy Premium\n\n"
                    f"2️⃣ Pay ₹{price} via QR/UPI\n\n"
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
        await query.message.edit_caption(caption=stats_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)  
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
        await query.message.reply_text("💰 Send the new Price (numeric value only):")
        return SETTING_PRICE

    elif query.data == "set_link":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("🔗 Send the new Premium Group Link:")
        return SETTING_LINK

    elif query.data == "set_welcome":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("📝 Send the new Welcome Message template (use {price} for price dynamic tag):")
        return SETTING_WELCOME

    elif query.data == "set_howto_menu":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("🎥 Send the video file with command /sethowto or reply here with the video.")
        return SETTING_HOWTO

    elif query.data == "set_support":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("🆘 Send the new Support Username (e.g., @Vidsell6):")
        return SETTING_SUPPORT

    elif query.data == "view_admins":
        if not await is_admin(user.id):
            return ConversationHandler.END
        admins = await admins_col.find({}).to_list(length=100)
        text = "👥 <b>Current Admins:</b>\n\n"
        for idx, adm in enumerate(admins, 1):
            text += f"{idx}. ID: <code>{adm['user_id']}</code>\n"
        keyboard = [[InlineKeyboardButton("🔙 Back to Panel", callback_data="admin_panel")]]
        await query.message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    elif query.data == "add_admin":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("➕ Send the Telegram User ID of the new admin:")
        return SETTING_ADD_ADMIN

    elif query.data == "remove_admin":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("➖ Send the Telegram User ID of the admin to remove:")
        return SETTING_REMOVE_ADMIN

    elif query.data == "view_products":
        if not await is_admin(user.id):
            return ConversationHandler.END
        products = await products_col.find({}).to_list(length=100)
        text = "📦 <b>Available Products:</b>\n\n"
        for idx, prod in enumerate(products, 1):
            text += f"{idx}. <b>{prod['name']}</b>\n   Price: ₹{prod['price']}\n   Link: {prod['link']}\n   ID: <code>{prod['_id']}</code>\n\n"
        keyboard = [[InlineKeyboardButton("🔙 Back to Panel", callback_data="admin_panel")]]
        await query.message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    elif query.data == "add_product":
        if not await is_admin(user.id):
            return ConversationHandler.END
        await query.message.reply_text("📦 Send the new Product Name:")
        return SETTING_ADD_PROD_NAME

    elif query.data == "edit_product_menu":
        if not await is_admin(user.id):
            return ConversationHandler.END
        products = await products_col.find({}).to_list(length=100)
        if not products:
            await query.message.reply_text("⚠️ No products found.")
            return ConversationHandler.END
        keyboard = []
        for prod in products:
            keyboard.append([InlineKeyboardButton(f"Edit: {prod['name']}", callback_data=f"editprod_{prod['_id']}")])
        keyboard.append([InlineKeyboardButton("🔙 Back to Panel", callback_data="admin_panel")])
        await query.message.edit_caption(caption="✏️ Select a product to edit:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    elif query.data.startswith("editprod_"):
        if not await is_admin(user.id):
            return ConversationHandler.END
        prod_id = query.data.split("_")[1]
        context.user_data["editing_prod_id"] = prod_id
        keyboard = [
            [InlineKeyboardButton("Change Name", callback_data="edit_pfield_name")],
            [InlineKeyboardButton("Change Price", callback_data="edit_pfield_price")],
            [InlineKeyboardButton("Change Link", callback_data="edit_pfield_link")],
            [InlineKeyboardButton("🔙 Back", callback_data="edit_product_menu")]
        ]
        await query.message.edit_caption(caption="Select field to edit:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    elif query.data.startswith("edit_pfield_"):
        if not await is_admin(user.id):
            return ConversationHandler.END
        field = query.data.split("_")[2]
        context.user_data["editing_prod_field"] = field
        await query.message.reply_text(f"Send the new value for product {field}:")
        return SETTING_EDIT_PROD_VALUE

    elif query.data == "remove_product_menu":
        if not await is_admin(user.id):
            return ConversationHandler.END
        products = await products_col.find({}).to_list(length=100)
        if not products:
            await query.message.reply_text("⚠️ No products found.")
            return ConversationHandler.END
        keyboard = []
        for prod in products:
            keyboard.append([InlineKeyboardButton(f"Remove: {prod['name']}", callback_data=f"remprod_{prod['_id']}")])
        keyboard.append([InlineKeyboardButton("🔙 Back to Panel", callback_data="admin_panel")])
        await query.message.edit_caption(caption="❌ Select a product to remove:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    elif query.data.startswith("remprod_"):
        if not await is_admin(user.id):
            return ConversationHandler.END
        prod_id = query.data.split("_")[1]
        await products_col.delete_one({"_id": ObjectId(prod_id)})
        await query.message.reply_text("✅ Product removed successfully!")
        return ConversationHandler.END

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

        await products_col.update_one(
            {"name": "PREMIUM ACCESS"},
            {"$set": {"price": new_price}}
        )

        await update.message.reply_text(f"✅ Price Updated Successfully to: ₹{new_price}")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid price. Please send a valid number.")
    return ConversationHandler.END

async def admin_set_link_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    new_link = update.message.text.strip()
    await set_setting("group_link", new_link)

    await products_col.update_one(
        {"name": "PREMIUM ACCESS"},
        {"$set": {"link": new_link}}
    )

    await update.message.reply_text("✅ Link Updated Successfully")
    return ConversationHandler.END

async def admin_set_welcome_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    new_welcome = update.message.text.strip()
    await set_setting("welcome_text", new_welcome)
    await update.message.reply_text("✅ Updated Successfully")
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
    await update.message.reply_text("✅ Support Username Updated Successfully")
    return ConversationHandler.END

async def admin_add_admin_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    try:
        new_admin_id = int(update.message.text.strip())
        await admins_col.update_one({"user_id": new_admin_id}, {"$set": {"user_id": new_admin_id}}, upsert=True)
        await update.message.reply_text(f"✅ Admin ID {new_admin_id} added successfully!")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid User ID. Please send a numeric Telegram User ID.")
    return ConversationHandler.END

async def admin_remove_admin_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    try:
        rem_admin_id = int(update.message.text.strip())

        admin_count = await admins_col.count_documents({})

        if admin_count <= 1:
            await update.message.reply_text(
                "⚠️ Last admin cannot be removed."
            )
            return ConversationHandler.END

        await admins_col.delete_one({"user_id": rem_admin_id})

        await update.message.reply_text(
            f"✅ Admin ID {rem_admin_id} removed successfully!"
        )
    except ValueError:
        await update.message.reply_text("⚠️ Invalid User ID. Please send a numeric Telegram User ID.")
    return ConversationHandler.END

async def admin_add_prod_name_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    context.user_data["new_prod_name"] = update.message.text.strip()
    await update.message.reply_text("Send the Price for this product (numeric value only):")
    return SETTING_ADD_PROD_PRICE

async def admin_add_prod_price_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    try:
        price = int(update.message.text.strip())
        context.user_data["new_prod_price"] = price
        await update.message.reply_text("Send the Group/Product Link:")
        return SETTING_ADD_PROD_LINK
    except ValueError:
        await update.message.reply_text("⚠️ Invalid price. Send a numeric value:")
        return SETTING_ADD_PROD_PRICE

async def admin_add_prod_link_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    link = update.message.text.strip()
    name = context.user_data.get("new_prod_name")
    price = context.user_data.get("new_prod_price")

    await products_col.insert_one({
        "name": name,
        "price": price,
        "link": link
    })
    await update.message.reply_text(f"✅ Product '{name}' added successfully!")
    return ConversationHandler.END

async def admin_edit_prod_value_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.message.from_user.id):
        return ConversationHandler.END
    prod_id = context.user_data.get("editing_prod_id")
    field = context.user_data.get("editing_prod_field")
    val = update.message.text.strip()

    if field == "price":
        try:
            val = int(val)
        except ValueError:
            await update.message.reply_text("⚠️ Invalid price. Send a numeric value:")
            return SETTING_EDIT_PROD_VALUE

    await products_col.update_one({"_id": ObjectId(prod_id)}, {"$set": {field: val}})
    await update.message.reply_text(f"✅ Product {field} updated successfully!")
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
        await update.message.reply_text("⚠️ Please send a valid image/screenshot of your payment!")
        return WAITING_FOR_SCREENSHOT

    user = update.effective_user
    existing_pending = await purchases_col.find_one({"user_id": user.id, "status": "pending"})
    if existing_pending:
        await update.message.reply_text("⚠️ You already have a payment verification pending with admins.")
        return ConversationHandler.END

    photo_id = update.message.photo[-1].file_id
    username = f"@{user.username}" if user.username else "No Username"

    product = await products_col.find_one({})
    product_id = str(product["_id"]) if product else "default"
    product_name = product["name"] if product else "PREMIUM ACCESS"
    current_price = product["price"] if product else await get_setting("price")

    purchase_doc = {
        "user_id": user.id,
        "username": username,
        "product_id": product_id,
        "product_name": product_name,
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
        f"📦 Product: {product_name}\n"
        f"💰 Amount: ₹{current_price}\n\n"
        f"Please check the screenshot below:"
    )

    admins = await admins_col.find({}).to_list(length=100)
    for adm in admins:
        try:
            await context.bot.send_photo(
                chat_id=adm["user_id"],
                photo=photo_id,
                caption=forward_caption,
                reply_markup=InlineKeyboardMarkup(admin_keyboard),
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Failed to forward screenshot to admin {adm['user_id']}: {e}")

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

    Fetch product link based on purchase info

    group_link = await get_setting("group_link")
    if "product_id" in purchase and purchase["product_id"] != "default":
        try:
            prod_doc = await products_col.find_one({"_id": ObjectId(purchase["product_id"])})
            if prod_doc and "link" in prod_doc:
                group_link = prod_doc["link"]
        except Exception:
            pass

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
        [InlineKeyboardButton("💳 Change UPI ID", callback_data="set_upi"), InlineKeyboardButton("💰 Change Price", callback_data="set_price")],
        [InlineKeyboardButton("🔗 Change Link", callback_data="set_link"), InlineKeyboardButton("📝 Change Welcome", callback_data="set_welcome")],
        [InlineKeyboardButton("🎥 Change HowTo Video", callback_data="set_howto_menu"), InlineKeyboardButton("🆘 Change Support", callback_data="set_support")],
        [InlineKeyboardButton("📦 View Products", callback_data="view_products"), InlineKeyboardButton("➕ Add Product", callback_data="add_product")],
        [InlineKeyboardButton("✏️ Edit Product", callback_data="edit_product_menu"), InlineKeyboardButton("❌ Remove Product", callback_data="remove_product_menu")],
        [InlineKeyboardButton("👥 View Admins", callback_data="view_admins"), InlineKeyboardButton("➕ Add Admin", callback_data="add_admin")],
        [InlineKeyboardButton("➖ Remove Admin", callback_data="remove_admin")],
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
                pattern="^(buy|how|main_menu|admin_panel|admin_stats|admin_broadcast|set_upi|set_price|set_link|set_welcome|set_howto_menu|set_support|view_admins|add_admin|remove_admin|view_products|add_product|edit_product_menu|remove_product_menu|editprod_.*|edit_pfield_.*|remprod_.*)$"
            ),
            CommandHandler("broadcast", broadcast_command),
            CommandHandler("admin", admin_command),
        ],
        states={
            WAITING_FOR_SCREENSHOT: [MessageHandler(filters.PHOTO, receive_screenshot)],
            WAITING_FOR_BROADCAST: [MessageHandler(filters.TEXT | filters.PHOTO, execute_broadcast)],
            SETTING_UPI: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_upi_receive)],
            SETTING_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_price_receive)],
            SETTING_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_link_receive)],
            SETTING_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_welcome_receive)],
            SETTING_HOWTO: [MessageHandler(filters.VIDEO, admin_set_howto_receive)],
            SETTING_SUPPORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_support_receive)],
            SETTING_ADD_ADMIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_admin_receive)],
            SETTING_REMOVE_ADMIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_remove_admin_receive)],
            SETTING_ADD_PROD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_prod_name_receive)],
            SETTING_ADD_PROD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_prod_price_receive)],
            SETTING_ADD_PROD_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_prod_link_receive)],
            SETTING_EDIT_PROD_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_prod_value_receive)],
        },
        fallbacks=[CommandHandler("cancel", start)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("sethowto", set_howto_command))
    app.add_handler(CallbackQueryHandler(admin_action, pattern="^(approve|reject)_"))

    logger.info("Starting bot...")
    app.run_polling()

if name == "__main__":
    main()
