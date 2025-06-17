import os
import random
import string
import logging
import re
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes, ConversationHandler
)
from telegram.error import NetworkError, BadRequest
from captcha.image import ImageCaptcha

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    TOKEN = "7705065990:AAFMbJzLFX3Q_e3qbEjv5-tJRW3CZmCo41Q"
    logger.warning("Bot token not found in environment variables, using hardcoded token")
if not TOKEN:
    raise RuntimeError("Bot token must be set in the environment variable BOT_TOKEN or hardcoded.")

# Admin configuration
ADMIN_ID = os.getenv('ADMIN_ID')
if ADMIN_ID:
    ADMIN_ID = int(ADMIN_ID)
else:
    ADMIN_ID = 7070505030  # Default or fallback ADMIN_ID
    logger.warning("Admin ID not found in environment variables, using hardcoded ID")
ADMIN_USERNAME = "@Git_Cash_Bot"  # Replace with your Telegram username

REWARD_PER_CAPTCHA = 100  # $0.005 per CAPTCHA
MIN_WITHDRAWAL = 5.00     # Minimum withdrawal amount

# In-memory storage (replace with database in production)
# WARNING: Data stored here will be lost when the bot restarts.
user_balances = {}
active_captchas = {}
pending_withdrawals = {}  # Store pending withdrawal requests
user_work_state = {}  # Track user work state

# Conversation states
WALLET_ADDRESS = 1

# Store user wallet addresses temporarily
user_withdrawal_state = {}

# Payment method configurations
PAYMENT_METHODS = {
    'webmoney': {
        'name': 'Webmoney (WMZ)',
        'emoji': '💰',
        'min_withdrawal': 0.50,
        'fee': -0.10,  # -10% means bonus
        'address_pattern': r'^Z\d{12}$',  # Basic Webmoney WMZ pattern
        'address_example': 'Z123456789012',
    },
    'payeer': {
        'name': 'Payeer',
        'emoji': '💳',
        'min_withdrawal': 0.50,
        'fee': 0,
        'address_pattern': r'^P\d{7,}$',  # Basic Payeer pattern
        'address_example': 'P1234567',
    },
    'airtm': {
        'name': 'AirTM',
        'emoji': '✈️',
        'min_withdrawal': 1.00,
        'fee': 0,
        'address_pattern': r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$',  # Email pattern
        'address_example': 'user@email.com',
    },
    'bitcoincash': {
        'name': 'Bitcoin Cash',
        'emoji': '💎',
        'min_withdrawal': 0.25,
        'fee': 0,
        'address_pattern': r'^(bitcoincash:)?(q|p)[a-z0-9]{41}$',  # BCH address pattern
        'address_example': 'bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a',
    },
    'usdttrc20': {
        'name': 'USDT TRC20',
        'emoji': '💲',
        'min_withdrawal': 30.00,
        'fee': 0,
        'address_pattern': r'^T[A-Za-z1-9]{33}$',  # USDT TRC20 address pattern
        'address_example': 'TQJvQqQ2Q2Q2Q2Q2Q2Q2Q2Q2Q2Q2Q2Q2Q2',
    }
}

def get_main_menu(user_id=None):
    """Create the main reply keyboard menu"""
    buttons = []
    if user_id and user_id in user_work_state and user_work_state[user_id]:
        # Only show Stop Work and New Captcha buttons when work is active
        buttons.append(["⏹️ Stop Work", "🔄 New Captcha"])
    else:
        buttons.append(["▶️ Start Work", "📊 My Balance"])
        buttons.append(["💳 Withdraw", "ℹ️ Help"])
        buttons.append(["📋 Withdrawal List"])
    
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all text messages"""
    if not update.message or not update.effective_user or update.message.text is None:
        return
    text = update.message.text
    user_id = update.effective_user.id

    if text == "▶️ Start Work":
        user_work_state[user_id] = True
        await update.message.reply_text(
            "⏳ Waiting for captcha...",
            reply_markup=get_main_menu(user_id)
        )
        await start_work(update, user_id)
    elif text == "⏹️ Stop Work":
        user_work_state[user_id] = False
        if user_id in active_captchas:
            del active_captchas[user_id]
        await update.message.reply_text(
            "⏹️ Work session stopped!",
            reply_markup=get_main_menu(user_id)
        )
    elif text == "🔄 New Captcha":
        if user_id in user_work_state and user_work_state[user_id]:
            await update.message.reply_text(
                "⏳ Waiting for captcha...",
                reply_markup=get_main_menu(user_id)
            )
            await send_captcha(update, user_id)
        else:
            await update.message.reply_text(
                "❌ Please start work first!",
                reply_markup=get_main_menu(user_id)
            )
    elif text == "📊 My Balance":
        await show_balance(update, user_id)
    elif text == "💳 Withdraw":
        await handle_withdraw(update, user_id)
    elif text == "ℹ️ Help":
        await show_help(update)
    elif text == "📋 Withdrawal List":
        await show_withdrawal_list(update, user_id)
    elif user_id in active_captchas:
        await verify_captcha(update, context)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    if not update.message or not update.effective_user:
        return
    await update.message.reply_text(
        "🤑 *Welcome to CAPTCHA Earning Bot!*\n\n"
        "Earn real money by solving simple CAPTCHA tasks anytime, anywhere. "
        "No skills needed—just tap, solve, and get paid daily. 💸\n\n"
        "Perfect for students, freelancers, or anyone looking to make extra income on the side.\n"
        "Fast, secure, and user-friendly.\n"
        "Join thousands already earning online with ease.\n\n"
        f"💰 Current rate: ${REWARD_PER_CAPTCHA:.3f} per CAPTCHA\n"
        f"💳 Minimum withdrawal: ${MIN_WITHDRAWAL:.2f}\n\n"
        "✅ Start now and turn your clicks into cash!",
        parse_mode='Markdown',
        reply_markup=get_main_menu(update.effective_user.id)
    )

async def show_help(update: Update):
    """Show help information"""
    if not update.message or not update.effective_user:
        return
    await update.message.reply_text(
        "▶️ *1. Start Working*\n"
        "Tap Start Work to begin solving CAPTCHAs.\n\n"
        "🧩 *2. Solve CAPTCHAs – Get Paid*\n"
        f"Each completed CAPTCHA earns you 💰 ${REWARD_PER_CAPTCHA:.3f} – fast and easy!\n\n"
        "📊 *3. Check Your Balance*\n"
        "Tap My Balance anytime to see your current earnings.\n\n"
        "💸 *4. Withdraw Your Earnings*\n"
        f"Once you reach ${MIN_WITHDRAWAL:.2f}, you can request a withdrawal directly in the app.\n\n"
        "📋 *5. Track Your Withdrawals*\n"
        "See all your pending and completed withdrawal requests in the Withdrawal List.",
        parse_mode='Markdown',
        reply_markup=get_main_menu(update.effective_user.id)
    )

def generate_captcha(user_id):
    """Generate a new CAPTCHA challenge"""
    captcha_text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    image = ImageCaptcha()
    image_path = f"captcha_{user_id}.png"
    image.write(captcha_text, image_path)
    active_captchas[user_id] = captcha_text
    return image_path

async def start_work(update: Update, user_id: int):
    await send_captcha(update, user_id)

async def send_captcha(update: Update, user_id: int):
    try:
        image_path = generate_captcha(user_id)
        with open(image_path, 'rb') as photo:
            if hasattr(update, 'callback_query') and update.callback_query and update.callback_query.message and isinstance(update.callback_query.message, Message):
                msg = update.callback_query.message
                await msg.reply_photo(
                    photo=photo,
                    caption=f"Type the characters you see to earn ${REWARD_PER_CAPTCHA:.3f}",
                    reply_markup=get_main_menu(user_id)
                )
            elif update.message and isinstance(update.message, Message):
                await update.message.reply_photo(
                    photo=photo,
                    caption=f"Type the characters you see to earn ${REWARD_PER_CAPTCHA:.3f}",
                    reply_markup=get_main_menu(user_id)
                )
            else:
                logger.error("Neither update.callback_query.message nor update.message is available.")
        os.remove(image_path)
    except Exception as e:
        logger.error(f"Error sending CAPTCHA: {str(e)}")
        if hasattr(update, 'callback_query') and update.callback_query and update.callback_query.message and isinstance(update.callback_query.message, Message):
            msg = update.callback_query.message
            await msg.reply_text(
                "❌ Error generating CAPTCHA. Please try again.",
                reply_markup=get_main_menu(user_id)
            )
        elif update.message and isinstance(update.message, Message):
            await update.message.reply_text(
                "❌ Error generating CAPTCHA. Please try again.",
                reply_markup=get_main_menu(user_id)
            )
        else:
            logger.error("Could not send error message as no valid update.message or callback_query.message was found.")

async def verify_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or update.message.text is None:
        return
    user_id = update.effective_user.id
    user_answer = update.message.text.upper()
    correct_answer = active_captchas.get(user_id)

    if user_answer == correct_answer:
        user_balances[user_id] = user_balances.get(user_id, 0) + REWARD_PER_CAPTCHA
        del active_captchas[user_id]
        await update.message.reply_text(
            f"✅ Correct! You earned ${REWARD_PER_CAPTCHA:.3f}",
            reply_markup=get_main_menu(user_id)
        )
        await update.message.reply_text(
            "⏳ Waiting for captcha...",
            reply_markup=get_main_menu(user_id)
        )
        await send_captcha(update, user_id)
    else:
        await update.message.reply_text(
            "❌ Incorrect. Try again.",
            reply_markup=get_main_menu(user_id)
        )

async def show_balance(update: Update, user_id: int):
    if not update.message:
        return
    balance = user_balances.get(user_id, 0)
    await update.message.reply_text(
        f"💰 *Your Balance*\n\n"
        f"Current Balance: ${balance:.3f}\n"
        f"Minimum Withdrawal: ${MIN_WITHDRAWAL:.2f}",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Withdraw", callback_data='show_withdrawal')]
        ])
    )

async def handle_withdraw(update: Update, user_id: int):
    if not update.message:
        return
    balance = user_balances.get(user_id, 0)
    if balance >= MIN_WITHDRAWAL:
        await update.message.reply_text(
            "Select withdrawal method:",
            reply_markup=get_withdrawal_menu()
        )
    else:
        await update.message.reply_text(
            "❌ Minimum withdrawal is ${:.2f}\n"
            "Your balance: ${:.3f}".format(MIN_WITHDRAWAL, balance)
        )

def validate_wallet_address(address: str, wallet_type: str) -> bool:
    if wallet_type not in PAYMENT_METHODS:
        return False
    pattern = PAYMENT_METHODS[wallet_type]['address_pattern']
    return bool(re.match(pattern, address))

def get_withdrawal_menu():
    buttons = []
    header_text = "💳 Select Payment Method 💳"
    buttons.append([InlineKeyboardButton(header_text, callback_data='header_none')])
    method_buttons = [
        ("webmoney", "💰 Webmoney"),
        ("payeer", "💳 Payeer"),
        ("airtm", "✈️ AirTM"),
        ("bitcoincash", "💎 Bitcoin Cash"),
        ("usdttrc20", "💲 USDT TRC20")
    ]
    for method_id, button_text in method_buttons:
        buttons.append([InlineKeyboardButton(button_text, callback_data=f'withdraw_{method_id}')])
    buttons.append([
        InlineKeyboardButton("❌ Cancel", callback_data='cancel_withdraw'),
        InlineKeyboardButton("ℹ️ Info", callback_data='withdrawal_help')
    ])
    return InlineKeyboardMarkup(buttons)

def get_captcha_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 New CAPTCHA", callback_data='new_captcha')]
    ])

async def show_withdrawal_list(update: Update, user_id: int):
    if not update.message:
        return
    user_withdrawals = [w for w_id, w in pending_withdrawals.items() if w_id == user_id]
    if not user_withdrawals:
        await update.message.reply_text(
            "📋 *Withdrawal History*\n\n"
            "You have no pending withdrawal requests.\n\n"
            "💡 To make a withdrawal, click '💳 Withdraw' when your balance reaches the minimum amount.",
            parse_mode='Markdown',
            reply_markup=get_main_menu(user_id)
        )
        return
    message = "📋 *Your Withdrawal Requests*\n\n"
    for withdrawal in user_withdrawals:
        method_info = PAYMENT_METHODS[withdrawal['method']]
        status = "⏳ Pending Admin Approval"
        message += (
            f"🔹 *Request Details:*\n"
            f"├ Amount: ${withdrawal['amount']:.2f}\n"
            f"├ Final Amount: ${withdrawal['final_amount']:.2f}\n"
            f"├ Method: {method_info['emoji']} {method_info['name']}\n"
            f"├ Address: `{withdrawal['address']}`\n"
            f"└ Status: {status}\n\n"
        )
    message += "ℹ️ Admin will process your request soon."
    await update.message.reply_text(
        message,
        parse_mode='Markdown',
        reply_markup=get_main_menu(user_id)
    )

async def show_withdrawal_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return
    help_text = "💳 *Available Payment Methods*\n\n"
    for method_id, info in PAYMENT_METHODS.items():
        fee_text = "🎁 +10% Bonus" if info['fee'] == -0.10 else "No fee"
        help_text += (
            f"{info['emoji']} *{info['name']}*\n"
            f"├ Min: ${info['min_withdrawal']:.2f}\n"
            f"└ {fee_text}\n\n"
        )
    help_text += (
        "📝 *How to Withdraw:*\n"
        "1️⃣ Select payment method\n"
        "2️⃣ Enter your wallet address\n"
        "3️⃣ Wait for admin approval\n\n"
        "⚠️ Double-check your wallet address!"
    )
    await update.callback_query.edit_message_text(
        text=help_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='show_withdrawal_menu')]])
    )

async def request_wallet_address(update: Update, context: ContextTypes.DEFAULT_TYPE, payment_method: str):
    if not update.callback_query or not update.callback_query.from_user:
        return
    query = update.callback_query
    user_id = query.from_user.id
    user_withdrawal_state[user_id] = {
        'method': payment_method,
        'amount': user_balances.get(user_id, 0)
    }
    method_info = PAYMENT_METHODS[payment_method]
    fee_text = "🎁 +10% Bonus" if method_info['fee'] == -0.10 else "No fee"
    message = (
        f"{method_info['emoji']} *{method_info['name']} Withdrawal*\n\n"
        f"💰 Your Balance: ${user_balances.get(user_id, 0):.2f}\n"
        f"📊 Minimum: ${method_info['min_withdrawal']:.2f}\n"
        f"🔄 Fee: {fee_text}\n\n"
        f"📝 Enter your {method_info['name']} address:\n"
        f"Example: `{method_info['address_example']}`"
    )
    await query.edit_message_text(
        text=message,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data='cancel_withdraw')]])
    )
    return WALLET_ADDRESS

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

async def notify_admin_withdrawal(user_id: int, amount: float, method: str, address: str, bot):
    try:
        withdrawal_info = pending_withdrawals[user_id]
        user = withdrawal_info['user']
        original_amount = withdrawal_info['amount']
        method_info = PAYMENT_METHODS[withdrawal_info['method']]
        fee_text = "🎁 +10% Bonus" if method_info['fee'] == -0.10 else "No fee"
        message = (
            f"🔔 *New Withdrawal Request*\n\n"
            f"👤 *User Information:*\n"
            f"├ Name: {user.first_name}\n"
            f"├ Username: @{user.username}\n"
            f"└ ID: `{user_id}`\n\n"
            f"💰 *Transaction Details:*\n"
            f"├ Method: {method_info['emoji']} {method}\n"
            f"├ Original Amount: ${original_amount:.2f}\n"
            f"├ Final Amount: ${amount:.2f}\n"
            f"├ Fee: {fee_text}\n"
            f"└ Address: `{address}`\n\n"
            f"Use buttons below to approve or reject:"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f'approve_{user_id}'),
                InlineKeyboardButton("❌ Reject", callback_data=f'reject_{user_id}')
            ]
        ])
        if not bot:
            logger.error(f"No bot context found for user {user_id}")
            return False
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=message,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
        logger.info(f"Successfully sent withdrawal notification to admin for user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Error sending admin notification: {str(e)}")
        return False

async def handle_admin_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query or not update.callback_query.from_user or not update.callback_query.data:
        return
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    if not isinstance(data, str):
        return
    if not is_admin(user_id):
        await query.answer("You are not authorized to perform this action.", show_alert=True)
        return
    if not data or '_' not in data:
        return
    parts = data.split('_', 1) if data else []
    if len(parts) != 2:
        return
    action, requester_id = parts
    try:
        requester_id = int(requester_id)
    except Exception:
        return
    if requester_id not in pending_withdrawals:
        await query.edit_message_text("This withdrawal request is no longer valid.")
        return
    withdrawal_info = pending_withdrawals[requester_id]
    method_info = PAYMENT_METHODS[withdrawal_info['method']]
    if action == 'approve':
        user_balances[requester_id] = 0
        message_to_user = (
            f"✅ Your withdrawal request has been approved!\n\n"
            f"💰 *Transaction Details:*\n"
            f"├ Amount: ${withdrawal_info['amount']:.2f}\n"
            f"├ Final Amount: ${withdrawal_info['final_amount']:.2f}\n"
            f"├ Method: {method_info['emoji']} {method_info['name']}\n"
            f"└ Address: `{withdrawal_info['address']}`"
        )
        admin_message = (
            f"✅ Withdrawal approved and processed\n\n"
            f"👤 User ID: `{requester_id}`\n"
            f"💰 Amount: ${withdrawal_info['final_amount']:.2f}\n"
            f"🏦 Method: {method_info['emoji']} {method_info['name']}"
        )
    else:
        user_balances[requester_id] = withdrawal_info['amount']
        message_to_user = (
            "❌ Your withdrawal request has been rejected by admin.\n"
            "The amount has been returned to your balance."
        )
        admin_message = (
            f"❌ Withdrawal rejected\n\n"
            f"👤 User ID: `{requester_id}`\n"
            f"💰 Amount: ${withdrawal_info['amount']:.2f}\n"
            f"🏦 Method: {method_info['emoji']} {method_info['name']}"
        )
    await query.edit_message_text(
        text=admin_message,
        parse_mode='Markdown'
    )
    try:
        await context.bot.send_message(
            chat_id=requester_id,
            text=message_to_user,
            parse_mode='Markdown',
            reply_markup=get_main_menu(requester_id)
        )
    except Exception as e:
        logger.error(f"Failed to send notification to user {requester_id}: {str(e)}")
    del pending_withdrawals[requester_id]

async def process_withdrawal_with_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or update.message.text is None:
        return False
    user_id = update.effective_user.id
    if not context or not context.bot:
        logger.error("No bot context available")
        await update.message.reply_text(
            "❌ An error occurred. Please try again later.",
            reply_markup=get_main_menu(user_id)
        )
        return False
    try:
        method = user_withdrawal_state[user_id]['method']
        if method not in PAYMENT_METHODS:
            await update.message.reply_text("Invalid payment method selected.")
            return False
        payment_info = PAYMENT_METHODS[method]
        amount = user_withdrawal_state[user_id]['amount']
        min_withdrawal = payment_info['min_withdrawal']
        address = update.message.text.strip()
        if amount >= min_withdrawal:
            fee_multiplier = 1 + payment_info['fee']
            final_amount = amount * fee_multiplier
            pending_withdrawals[user_id] = {
                'amount': amount,
                'final_amount': final_amount,
                'method': method,
                'address': address,
                'user': update.effective_user,
            }
            if await notify_admin_withdrawal(user_id, final_amount, payment_info['name'], address, context.bot):
                await update.message.reply_text(
                    f"✅ Withdrawal request sent to admin\n"
                    f"Amount: ${amount:.3f}\n"
                    f"Method: {payment_info['name']}",
                    reply_markup=get_main_menu(user_id)
                )
                return True
            else:
                await update.message.reply_text(
                    "❌ Could not process withdrawal. Please try again later.",
                    reply_markup=get_main_menu(user_id)
                )
                return False
        else:
            await update.message.reply_text(
                f"❌ Minimum withdrawal for {payment_info['name']} is ${min_withdrawal:.2f}\n"
                f"Your balance: ${amount:.3f}"
            )
            return False
    except Exception as e:
        logger.error(f"Error processing withdrawal: {str(e)}")
        await update.message.reply_text(
            "❌ An error occurred. Please try again later.",
            reply_markup=get_main_menu(user_id)
        )
        return False

async def handle_wallet_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or update.message.text is None:
        return ConversationHandler.END
    user_id = update.effective_user.id
    address = update.message.text.strip()
    if user_id not in user_withdrawal_state:
        await update.message.reply_text("Please start the withdrawal process again.",
                                    reply_markup=get_main_menu(user_id))
        return ConversationHandler.END
    payment_method = user_withdrawal_state[user_id]['method']
    amount = user_withdrawal_state[user_id]['amount']
    if not validate_wallet_address(address, payment_method):
        method_name = PAYMENT_METHODS[payment_method]['name']
        await update.message.reply_text(
            f"Invalid {method_name} address format. Please try again or cancel.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data='cancel_withdraw')]])
        )
        return WALLET_ADDRESS
    if await process_withdrawal_with_address(update, context):
        del user_withdrawal_state[user_id]
        fee_multiplier = 1 + PAYMENT_METHODS[payment_method]['fee']
        final_amount = amount * fee_multiplier
        fee_text = "🎁 +10% Bonus" if PAYMENT_METHODS[payment_method]['fee'] == -0.10 else "No fee"
        await update.message.reply_text(
            f"✅ Withdrawal request submitted!\n"
            f"Amount: ${amount:.2f}\n"
            f"Final Amount: ${final_amount:.2f}\n"
            f"Method: {PAYMENT_METHODS[payment_method]['name']}\n"
            f"Fee: {fee_text}\n"
            f"Address: {address}\n\n"
            f"Please wait for admin approval.",
            reply_markup=get_main_menu(user_id)
        )
    else:
        await update.message.reply_text(
            "❌ Withdrawal failed. Please try again later.",
            reply_markup=get_main_menu(user_id)
        )
    return ConversationHandler.END

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query or not update.callback_query.from_user or not update.callback_query.data:
        return
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    if not isinstance(data, str):
        return
    try:
        if data == 'show_withdrawal':
            balance = user_balances.get(user_id, 0)
            if balance >= MIN_WITHDRAWAL:
                await query.edit_message_text(
                    "Select withdrawal method:",
                    reply_markup=get_withdrawal_menu()
                )
            else:
                await query.answer(
                    f"Minimum withdrawal is ${MIN_WITHDRAWAL:.2f}. Your balance: ${balance:.3f}",
                    show_alert=True
                )
        elif data and isinstance(data, str) and data.startswith('withdraw_'):
            payment_method = data.replace('withdraw_', '')
            if payment_method in PAYMENT_METHODS:
                return await request_wallet_address(update, context, payment_method)
        elif data == 'withdrawal_help':
            await show_withdrawal_help(update, context)
        elif data == 'header_none':
            await query.answer()
        elif data == 'show_withdrawal_menu':
            await query.edit_message_text(
                "Select withdrawal method:",
                reply_markup=get_withdrawal_menu()
            )
    except Exception as e:
        logger.error(f"Error in handle_callback: {str(e)}")

async def test_admin_notification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("This command is only available to admins.")
        return
    try:
        if ADMIN_ID is None:
            logger.error("ADMIN_ID is not set, cannot send test notification.")
            await update.message.reply_text("❌ Admin ID is not configured. Cannot send test notification.")
            return
        await context.bot.send_message(
            chat_id=int(ADMIN_ID),
            text="🔔 *Test Notification*\n\nIf you see this message, admin notifications are working correctly!",
            parse_mode='Markdown'
        )
        await update.message.reply_text("✅ Test notification sent! Check if you received it.")
    except Exception as e:
        logger.error(f"Error testing admin notification: {str(e)}")
        await update.message.reply_text("❌ Error sending test notification. Check logs for details.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.warning(f'Update "{update}" caused error "{context.error}"')
    if isinstance(context.error, NetworkError):
        print("Network error occurred. Please check your internet connection")
    elif isinstance(context.error, BadRequest):
        print(f"Bad request error: {context.error}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    await update.message.reply_text("Operation cancelled.", reply_markup=get_main_menu(update.effective_user.id))
    return ConversationHandler.END

async def cancel_withdrawal_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query or not update.callback_query.from_user:
        return ConversationHandler.END
    query = update.callback_query
    user_id = query.from_user.id
    if user_id in user_withdrawal_state:
        del user_withdrawal_state[user_id]
    await query.edit_message_text("❌ Withdrawal cancelled")
    return ConversationHandler.END

def main():
    logger.info("Starting bot...")
    logger.info(f"Admin ID configured as: {ADMIN_ID}")
    application = ApplicationBuilder().token(str(TOKEN)).build()
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_callback, pattern='^withdraw_')],
        states={
            WALLET_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_wallet_address),
                CallbackQueryHandler(cancel_withdrawal_conversation, pattern='^cancel_withdraw$')
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("testadmin", test_admin_notification))
    application.add_handler(CallbackQueryHandler(handle_admin_response, pattern='^(approve|reject)_[0-9]+$'))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_error_handler(error_handler)
    logger.info("Bot is running...")
    print(f"Bot is running... Admin ID: {ADMIN_ID}")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
