import asyncio
import html
import re
import logging
import aiohttp
import time
import psycopg2.extras

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AIogram Imports
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from aiogram import types, F, Router
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOCAL IMPORTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from database import is_gate_enabled, get_db_connection, create_user
from bin import get_bin_info
from sub import get_premium_status

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIGURATION & URLS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

B3_API_URL = "https://paypal-1-1.onrender.com/gate=b3/cc={cc}"

# In-memory dictionary to store last command times for rate limiting
user_last_command_time = {}

# Router for this module
def to_math_bold(s: str) -> str:
    bold_map = {
        'a':'𝗮','b':'𝗯','c':'𝗰','d':'𝗱','e':'𝗲','f':'𝗳','g':'𝗴','h':'𝗵','i':'𝗶','j':'𝗷','k':'𝗸','l':'𝗹','m':'𝗺','n':'𝗻','o':'𝗼','p':'𝗽','q':'𝗾','r':'𝗿','s':'𝘀','t':'𝘁','u':'𝘂','v':'𝘃','w':'𝘄','x':'𝘅','y':'𝘆','z':'𝘇',
        'A':'𝗔','B':'𝗕','C':'𝗖','D':'𝗗','E':'𝗘','F':'𝗙','G':'𝗚','H':'𝗛','I':'𝗜','J':'𝗝','K':'𝗞','L':'𝗟','M':'𝗠','N':'𝗡','O':'𝗢','P':'𝗣','Q':'𝗤','R':'𝗥','S':'𝗦','T':'𝗧','U':'𝗨','V':'𝗩','W':'𝗪','X':'𝗫','Y':'𝗬','Z':'𝗭',
        '0':'𝟬','1':'𝟭','2':'𝟮','3':'𝟯','4':'𝟰','5':'𝟱','6':'𝟲','7':'𝟳','8':'𝟴','9':'𝟵'
    }
    return "".join(bold_map.get(c, c) for c in str(s))

router = Router()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATABASE INITIALIZATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def ensure_stats_columns():
    """Ensures cc_checked and cc_charged columns exist in the users table."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS cc_checked INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS cc_charged INTEGER DEFAULT 0")
        except Exception:
            pass
        conn.commit()
        conn.close()
        print("[DB] B3 Stats columns checked/created.")
    except Exception as e:
        print(f"[DB] Error checking stats columns: {e}")

# Run check on import
ensure_stats_columns()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def update_user_stats(user_id, is_charged):
    """
    Increments cc_checked and optionally cc_charged in the database.
    Wrapped in asyncio.to_thread for non-blocking execution.
    """
    def _sync_update():
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Always increment total checked
        cursor.execute("UPDATE users SET cc_checked = cc_checked + 1 WHERE user_id = %s", (user_id,))
        
        # If it was a hit (approved), increment charged count
        if is_charged:
            cursor.execute("UPDATE users SET cc_charged = cc_charged + 1 WHERE user_id = %s", (user_id,))
            
        conn.commit()
        conn.close()

    await asyncio.to_thread(_sync_update)

async def get_user_plan_name(user_id):
    """Fetches the specific plan name from receipts if premium, else Trial."""
    is_premium, _ = get_premium_status(user_id)
    
    if is_premium:
        try:
            def _sync_fetch():
                conn = get_db_connection()
                cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cursor.execute("SELECT plan FROM receipts WHERE user_id = %s ORDER BY purchased_on DESC LIMIT 1", (user_id,))
                row = cursor.fetchone()
                conn.close()
                if row:
                    p = row['plan'].lower()
                    if "laveyan" in p: return '𝑫𝒐𝒓𝒆𝒂𝒎𝒐𝒏<tg-emoji emoji-id="5039727497143387500">👑</tg-emoji>'
                    if "laveyan" in p: return "𝑫𝒐𝒓𝒆𝒂𝒎𝒐𝒏<tg-emoji emoji-id='5039727497143387500'>👑</tg-emoji>"
                    if "root" in p: return '𝗥𝗼𝗼𝘁 <tg-emoji emoji-id="5039727497143387500">👑</tg-emoji>'
                    if "elite" in p: return '𝗘𝗹𝗶𝘁𝗲 ⭐'
                    if "core" in p: return '𝗖𝗼𝗿𝗲 <tg-emoji emoji-id="5042274086332400375">🛠️</tg-emoji>'
                    return row['plan']
                return "PREMIUM"
            
            return await asyncio.to_thread(_sync_fetch)
        except Exception as e:
            logging.error(f"Error fetching plan name: {e}")
        return "PREMIUM"
    else:
        return "TRIAL"

def luhn_check(card_number: str) -> bool:
    """
    Validates a credit card number using the Luhn Algorithm.
    Returns True if valid, False otherwise.
    """
    card_number = str(card_number).strip()
    if not card_number.isdigit():
        return False
    
    total = 0
    reverse_digits = card_number[::-1]
    
    for i, char in enumerate(reverse_digits):
        digit = int(char)
        
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
                
        total += digit
        
    return total % 10 == 0

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMMAND HANDLER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.message(F.text.startswith("/b3"))
async def b3_command(message: types.Message):
    # 1. GATE CHECK
    if not is_gate_enabled("b3"):
        await message.reply(
            "<tg-emoji emoji-id='4958926882994127612'>🚧</tg-emoji> <b>𝗕𝗿𝗮𝗶𝗻𝘁𝗿𝗲𝗲 𝗔𝘂𝘁𝗵 𝗶𝘀 𝘂𝗻𝗱𝗲𝗿 𝗺𝗮𝗶𝗻𝘁𝗲𝗻𝗮𝗻𝗰𝗲.</b>\n"
            "𝗜𝘁 𝘄𝗶𝗹𝗹 𝗯𝗲 𝗯𝗮𝗰𝗸 𝘀𝗵𝗼𝗿𝘁𝗹𝘆 𝘄𝗶𝘁𝗵 𝗲𝘅𝗰𝗶𝘁𝗶𝗻𝗴 𝗶𝗺𝗽𝗿𝗼𝘃𝗲𝗺𝗲𝗻𝘁𝘀.<tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji>",
            parse_mode="HTML"
        )
        return

    user = message.from_user
    user_id = user.id
    current_time = time.time()

    # 2. GET PREMIUM STATUS
    # get_premium_status is synchronous, run in thread
    is_premium, _ = await asyncio.to_thread(get_premium_status, user_id)

    # 3. RATE LIMITING CHECK
    if not is_premium:
        # Apply 10s cooldown only to FREE users
        if user_id in user_last_command_time:
            elapsed = current_time - user_last_command_time[user_id]
            if elapsed < 10:
                remaining_time = round(10 - elapsed, 1)
                await message.reply(
                    f"<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> <b>𝗦𝗹𝗼𝘄 𝗗𝗼𝘄𝗻!</b>\n"
                    f"𝗣𝗹𝗲𝗮𝘀𝗲 𝘄𝗮𝗶𝘁 <code>{remaining_time}</code> 𝘀𝗲𝗰𝗼𝗻𝗱𝘀 𝗯𝗲𝗳𝗼𝗿𝗲 𝗰𝗼𝗻𝘁𝗶𝗻𝘂𝗶𝗻𝗴.\n"
                    f"<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> 𝗨𝗻𝗹𝗼𝗰𝗸 𝗣𝗿𝗲𝗺𝗶𝘂𝗺 𝗳𝗼𝗿 𝗶𝗻𝘀𝘁𝗮𝗻𝘁, 𝘂𝗻𝗹𝗶𝗺𝗶𝘁𝗲𝗱 𝘂𝘀𝗲.",
                    parse_mode="HTML"
                )
                return
        
        user_last_command_time[user_id] = current_time
    
    # 4. Extract Card Details
    # Manually parse args from message.text since Aiogram doesn't have context.args
    parts = message.text.split(maxsplit=1)
    raw_text = ""
    
    if len(parts) > 1:
        raw_text = parts[1].strip()
    elif message.reply_to_message:
        raw_text = message.reply_to_message.text or message.reply_to_message.caption
        
    if not raw_text:
        await message.reply(
            "<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> <b>Usage:</b> /b3 <code>cc|mm|yy|cvv</code>\n"
            "Or reply to a message containing card details.",
            parse_mode="HTML"
        )
        return

    # Regex to find CC (Supports 15 and 16 digits)
    pattern = r'\b(\d{15,16})[|\s/?\\:]+(\d{2,4})[|\s/?\\:]+(\d{2,4})[|\s/?\\:]+(\d{3,4})\b'
    match = re.search(pattern, raw_text)

    if not match:
        await message.reply(
            "<tg-emoji emoji-id='5040030395416969985'>🚫</tg-emoji> <b>Invalid Card Format.</b>\n"
            "Please provide CC as <code>4242424242424242|05|27|123</code>",
            parse_mode="HTML"
        )
        return

    cc, mm, yy_raw, cvv = match.groups()

    # Normalize Year
    if len(yy_raw) == 4:
        yy = yy_raw[2:]
    else:
        yy = yy_raw

    formatted_cc = f"{cc}|{mm}|{yy}|{cvv}"

    # 5. LUHN CHECK
    if not luhn_check(cc):
        await message.reply(
            "<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> <b>Invalid Card</b>\n"
            "Your card number is incorrect.",
            parse_mode="HTML"
        )
        return

    # 7. Fetch Plan Name
    plan_name = await get_user_plan_name(user_id)

    # 8. Send Processing Message
    user_link = f"<a href='tg://user?id={user_id}'>{user.first_name}</a>"
    proc_msg = await message.reply(
        f"𝗧𝗼𝘁𝗮𝗹 𝗖𝗮𝗿𝗱𝘀 ➛ <code>1</code>\n"
        f"<tg-emoji emoji-id='5456140674028019486'>⚡</tg-emoji> 𝗧𝗶𝗺𝗲 ➛ <code>0.0s</code>\n"
        f"<tg-emoji emoji-id='6237927637906364256'>👤</tg-emoji> 𝗨𝘀𝗲𝗿 ➛ {user_link}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<tg-emoji emoji-id='5039579582764680065'>⏳</tg-emoji> <b>𝗦𝘁𝗮𝗿𝘁𝗶𝗻𝗴 𝗖𝗵𝗲𝗰𝗸...</b>",
        parse_mode="HTML"
    )

    # 9. Execute Background Task
    asyncio.create_task(
        process_b3_check(
            message, proc_msg, user, user_id, formatted_cc, cc, plan_name
        )
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BACKGROUND PROCESSOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def process_b3_check(message, proc_msg, user, user_id, formatted_cc, cc, plan_name):
    import time
    start_time = time.time()
    """
    Handles the Braintree API calls, DB updates, and final message editing.
    """
    
    # A. ENSURE USER EXISTS IN DB (Critical Fix)
    try:
        await asyncio.to_thread(create_user, user_id, user.username)
    except Exception as e:
        logging.error(f"Error ensuring user exists: {e}")

    # B. Auth API (Braintree)
    api_result = {"status": "ERROR", "response": "Unknown Error"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(B3_API_URL.format(cc=formatted_cc), timeout=90) as resp:
                if resp.status == 200:
                    api_result = await resp.json()
                else:
                    api_result["response"] = f"API Error: {resp.status}"
    except asyncio.TimeoutError:
        logging.error(f"Braintree API Timeout")
        api_result["response"] = "API Timed Out (Server took too long to respond)"
    except Exception as e:
        logging.error(f"Braintree API Error: {e}")
        api_result["response"] = f"Connection Error: {str(e)}"

    # C. Bin Lookup
    try:
        bin_info = await get_bin_info(cc[:6])
    except Exception:
        bin_info = {}

    bin_scheme = bin_info.get("scheme", "N/A")
    card_type = bin_info.get("type", "N/A")
    level = bin_info.get("level", "N/A")
    bin_bank = bin_info.get("bank", "N/A")
    country_name = bin_info.get("country", "N/A")
    country_flag = bin_info.get("country_emoji", "")
    bin_country = f"{country_flag} {country_name}" if country_flag else country_name

    # D. Determine Final Status & Check if Charged
    status_raw = api_result.get("status", "").upper()
    api_response_msg = api_result.get("response", "N/A")
    
    is_charged = False
    
    # Custom Emoji ID
    CUSTOM_DECLINED_EMOJI_ID = "4915853119839011973"
    CUSTOM_APPROVED_EMOJI_ID = "5039844895779455925"
    
    if "APPROVED" in status_raw:
        final_status = f'𝗔𝗣𝗣𝗥𝗢𝗩𝗘𝗗 <tg-emoji emoji-id=\"{CUSTOM_APPROVED_EMOJI_ID}\">🍾</tg-emoji>'
        is_charged = True
    elif "DECLINED" in status_raw:
        final_status = f'𝗗𝗘𝗖𝗟𝗜𝗡𝗘𝗗 <tg-emoji emoji-id="4915853119839011973">⚠️</tg-emoji>'
    else:
        final_status = "𝗘𝗥𝗥𝗢𝗥 / 𝗧𝗜𝗠𝗘𝗢𝗨𝗧 <tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji>"

    # E. UPDATE STATS (Checked + Charged)
    try:
        await update_user_stats(user_id, is_charged)
    except Exception as e:
        logging.error(f"Failed to update stats: {e}")

    pass

    # F. Build Final Caption
    user_link = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    dev_link = '<a href="https://t.me/RohitRich">𝑫𝒐𝒓𝒆𝒂𝒎𝒐𝒏</a>'
    
    # Show plan name next to user
    user_display = f"{user_link} ({plan_name})"
    
    elapsed = round(time.time() - start_time, 1)
    final_caption = (
        f"<b><tg-emoji emoji-id='5386367538735104399'>🆕</tg-emoji> {final_status}!</b>\n\n"
        f"<b><tg-emoji emoji-id='5039623284056917259'>💳</tg-emoji> 𝗖𝗖:</b> <code><b>{formatted_cc}</b></code>\n"
        f"<b><tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> 𝗚𝗮𝘁𝗲:</b> {to_math_bold("Braintree 0$")}\n"
        f"<b><tg-emoji emoji-id='5040042498634810056'>💬</tg-emoji> 𝗥𝗲𝘀𝗽𝗼𝗻𝘀𝗲:</b> {to_math_bold(html.escape(str(api_response_msg or 'N/A')))}\n\n"
        f"<b><tg-emoji emoji-id='5042329873662609701'>🗑️</tg-emoji> 𝗕𝗜𝗡 𝗜𝗻𝗳𝗼:</b>\n"
        f"<b><tg-emoji emoji-id='5039753786638205957'>🔹</tg-emoji> 𝗕𝗿𝗮𝗻𝗱:</b> {to_math_bold(html.escape(str(bin_scheme or 'N/A')))}\n"
        f"<b><tg-emoji emoji-id='5042020176455795565'>🔸</tg-emoji> 𝗧𝘆𝗽𝗲:</b> {to_math_bold(html.escape(str(card_type or 'N/A')))}\n"
        f"<b><tg-emoji emoji-id='5042097984083330584'>🔘</tg-emoji> 𝗟𝗲𝘃𝗲𝗹:</b> {to_math_bold(html.escape(str(level or 'N/A')))}\n"
        f"<b><tg-emoji emoji-id='5343636681473935403'>🏛️</tg-emoji> 𝗕𝗮𝗻𝗸:</b> {to_math_bold(html.escape(str(bin_bank or 'N/A')))}\n"
        f"<b><tg-emoji emoji-id='5042176294222037888'>🌍</tg-emoji> 𝗖𝗼𝘂𝗻𝘁𝗿𝘆:</b> {to_math_bold(html.escape(str(bin_country or 'N/A')))}\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"<b><tg-emoji emoji-id='5039579582764680065'>⏳</tg-emoji> 𝗧𝗶𝗺𝗲 ➛</b> <code>{elapsed}s</code>\n"
        f"<tg-emoji emoji-id='6237927637906364256'>👤</tg-emoji> <b>𝗨𝘀𝗲𝗿 ➛</b> {user_display}\n"
        f"<tg-emoji emoji-id='5039653765439816618'>🐈‍⬛</tg-emoji> <b>𝗗𝗲𝘃 ➛</b> {dev_link}"
    )

    # G. Keyboard (Aiogram 3.x Syntax)
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="𝑫𝒐𝒓𝒆𝒂𝒎𝒐𝒏", url="http://t.me/HchkCxcMainbot", icon_custom_emoji_id="5042097984083330584", style="primary")]
    ])

    # H. Edit the Processing Message with Result
    try:
        await proc_msg.edit_text(
            text=final_caption,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    except Exception as e:
        logging.error(f"Error editing message: {e}")
        # Fallback: If editing fails (rare), send a new message as a reply
        await message.reply(
            text=final_caption,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
