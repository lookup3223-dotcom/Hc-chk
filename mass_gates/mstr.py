import asyncio
import random
import re
import logging
import aiohttp
import time
import string
import os
import psycopg2.extras
from datetime import datetime
from typing import Optional, Tuple, List
from io import BytesIO
import html

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AIOGRAM IMPORTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from aiogram import types, F, Router, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from utils_send import safe_send_message, safe_send_animation
from aiogram.filters.callback_data import CallbackData

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOCAL IMPORTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from database import is_gate_enabled, update_user_stats, get_db_connection
from bin import get_bin_info
from sub import get_premium_status
from gates.stripecheck import check_card
from mass_gates.msh import ProxyManager, get_user_proxies

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIGURATION & URLS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HIT_LOG_GROUP_ID = -1004450624623
EXTRA_CHARGED_GROUP_ID = -1004482288209
BUTTON_LOCK_SECONDS = 30

CUSTOM_CHARGED_EMOJI_ID = "5042050649248760772"
CUSTOM_APPROVED_EMOJI_ID = "5039844895779455925"
CUSTOM_DECLINED_EMOJI_ID = "4915853119839011973"

BTN_CHARGED_EMOJI_ID = "5042050649248760772"
BTN_DEAD_EMOJI_ID = "4915853119839011973"
BTN_LIVE_EMOJI_ID = "5039844895779455925"
BTN_STOP_EMOJI_ID = "5040042498634810056"
BTN_ALL_EMOJI_ID = "5447602197439218445"

MSTR_SESSIONS = {}
MSTR_SESSION_LOCKS = {}

# ── Session GC: prune stale sessions every 10 min to prevent memory leaks ─────
import threading as _mstr_threading
import time as _mstr_time

_MSTR_SESSION_MAX_AGE = 3600

def _cleanup_mstr_sessions():
    cutoff = _mstr_time.time() - _MSTR_SESSION_MAX_AGE
    dead = [sid for sid, s in list(MSTR_SESSIONS.items())
            if s.get('status') in ('FINISHED', 'STOPPED') and s.get('start_time', 0) < cutoff]
    for sid in dead:
        MSTR_SESSIONS.pop(sid, None)
        MSTR_SESSION_LOCKS.pop(sid, None)

def _start_mstr_gc():
    def _loop():
        while True:
            _mstr_time.sleep(600)
            try: _cleanup_mstr_sessions()
            except Exception: pass
    _mstr_threading.Thread(target=_loop, daemon=True, name='mstr_gc').start()

_start_mstr_gc()


router = Router()

REACTIONS = [
    "airkiss", "blush", "brofist", "celebrate", "cheers", "clap",
    "cool", "cuddle", "dance", "handhold", "happy", "hug",
    "kiss", "laugh", "lick", "love", "nervous", "nom",
    "nuzzle", "nyah", "pat", "peek", "shy", "sip",
    "sleep", "smile", "smug", "sorry", "thumbsup",
    "tickle", "tired", "wave", "wink", "yay", "yes"
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CALLBACK DATA (unique prefixes to avoid conflict with mst.py)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MstrResultCallback(CallbackData, prefix="mstsr"):
    session_id: str
    result_type: str

class MstrStopCallback(CallbackData, prefix="mstss"):
    session_id: str

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PROXY HELPER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def format_api_proxy(proxy: str) -> str:
    if not proxy:
        return ""
    proxy = proxy.strip()
    if proxy.startswith("https://"):
        return "http://" + proxy[8:]
    if not proxy.startswith("http://"):
        return "http://" + proxy
    return proxy

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CARD EXTRACTION FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def parse_card_details(card_string: str) -> Optional[Tuple[str, str, str, str]]:
    card_string = card_string.strip()
    patterns = [
        r'^(\d{13,19})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})$',
        r'^(\d{13,19})/(\d{1,2})/(\d{2,4})/(\d{3,4})$',
        r'^(\d{13,19}):(\d{1,2}):(\d{2,4}):(\d{3,4})$',
        r'^(\d{13,19})\s+(\d{1,2})\s+(\d{2,4})\s+(\d{3,4})$',
        r'^(\d{13,19})/(\d{1,2})\|(\d{2,4})\|(\d{3,4})$',
        r'^(\d{13,19})\|(\d{1,2})/(\d{2,4})\|(\d{3,4})$',
        r'^(\d{13,19})\|(\d{1,2}):(\d{2,4}):(\d{3,4})$',
        r'^(\d{13,19})\s*\|\s*(\d{1,2})\s*\|\s*(\d{2,4})\s*\|\s*(\d{3,4})$',
        r'^(\d{13,19})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})\s*/\s*(\d{3,4})$',
        r'^(\d{13,19})\s*:\s*(\d{1,2})\s*:\s*(\d{2,4})\s*:\s*(\d{3,4})$',
        r'^(\d{13,19})\s*=\s*(\d{1,2})\s*=\s*(\d{2,4})\s*=\s*(\d{3,4})$',
        r'^\/[a-zA-Z]+\s+(\d{13,19})\s*\|\s*(\d{1,2})\s*\|\s*(\d{2,4})\s*\|\s*(\d{3,4})',
        r'^\/[a-zA-Z]+\s+(\d{13,19})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})\s*/\s*(\d{3,4})',
        r'^\/[a-zA-Z]+\s+(\d{13,19})\s+(\d{1,2})\s+(\d{2,4})\s+(\d{3,4})',
        r'(\d{13,19})\s*\|\s*(\d{1,2})\s*\|\s*(\d{2,4})\s*\|\s*(\d{3,4})',
        r'(\d{13,19})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})\s*/\s*(\d{3,4})',
        r'(\d{13,19})\s*:\s*(\d{1,2})\s*:\s*(\d{2,4})\s*:\s*(\d{3,4})',
        r'(\d{13,19})\s+(\d{1,2})\s+(\d{2,4})\s+(\d{3,4})',
        r'(\d{13,19})\s*=\s*(\d{1,2})\s*=\s*(\d{2,4})\s*=\s*(\d{3,4})',
    ]
    for pattern in patterns:
        match = re.search(pattern, card_string)
        if match:
            groups = match.groups()
            if len(groups) == 5 and pattern.startswith(r'^\/[a-zA-Z]+\s+'):
                cc, mm, yy, cvv = groups[1], groups[2], groups[3], groups[4]
            elif len(groups) == 4:
                cc, mm, yy, cvv = groups
            else:
                continue
            month = mm.zfill(2)
            if len(yy) == 4:
                yy = yy[2:]
            return cc, month, yy, cvv
    return None

def extract_cards_from_text(text: str) -> List[str]:
    patterns = [
        r'(\d{13,19})\s*\|\s*(\d{1,2})\s*\|\s*(\d{2,4})\s*\|\s*(\d{3,4})',
        r'(\d{13,19})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})\s*/\s*(\d{3,4})',
        r'(\d{13,19})\s*:\s*(\d{1,2})\s*:\s*(\d{2,4})\s*:\s*(\d{3,4})',
        r'(\d{13,19})\s+(\d{1,2})\s+(\d{2,4})\s+(\d{3,4})',
        r'(\d{13,19})\s*=\s*(\d{1,2})\s*=\s*(\d{2,4})\s*=\s*(\d{3,4})',
        r'(\d{13,19})\s*/\s*(\d{1,2})\s*\|\s*(\d{2,4})\s*\|\s*(\d{3,4})',
        r'(\d{13,19})\s\|\s*(\d{1,2})\s*/\s*(\d{2,4})\s*\|\s*(\d{3,4})',
        r'(\d{13,19})\s(\d{1,2})/(\d{2,4})\s(\d{3,4})',
    ]
    cards = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            if len(match) == 4:
                card_number, month, year, cvv = match
                month = month.zfill(2)
                if len(year) == 4:
                    year = year[2:]
                card_string = f"{card_number}|{month}|{year}|{cvv}"
                if card_string not in cards:
                    cards.append(card_string)
    return cards

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def log_hit_to_mstr(user_id, username, first_name):
    try:
        with open("mstr.txt", "a", encoding="utf-8") as f:
            u_name = username if username else "None"
            f_name = first_name if first_name else "Unknown"
            f.write(f"{user_id}|{u_name}|{f_name}\n")
    except Exception as e:
        logging.error(f"Error writing to mstr.txt: {e}")

def is_session_stopped(session_id: str) -> bool:
    session = MSTR_SESSIONS.get(session_id)
    if not session:
        return True
    return session.get('status') == "STOPPED"

def is_buttons_locked(session_id: str) -> bool:
    session = MSTR_SESSIONS.get(session_id)
    if not session:
        return False
    elapsed = time.time() - session.get('start_time', 0)
    return elapsed < BUTTON_LOCK_SECONDS

def get_remaining_lock(session_id: str) -> int:
    session = MSTR_SESSIONS.get(session_id)
    if not session:
        return 0
    elapsed = time.time() - session.get('start_time', 0)
    remaining = BUTTON_LOCK_SECONDS - elapsed
    return max(0, int(remaining) + 1)

def get_user_display(user_obj, plan_name):
    return f"{user_obj.first_name} ({plan_name})"

def build_user_link(user_obj) -> str:
    name = html.escape(user_obj.first_name or "User")
    if user_obj.username:
        return f'<a href="https://t.me/{user_obj.username}">{name}</a>'
    return f'<a href="tg://user?id={user_obj.id}">{name}</a>'

async def get_anime_gif():
    reaction = random.choice(REACTIONS)
    api_url = f"https://api.otakugifs.xyz/gif?reaction={reaction}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("url")
    except Exception as e:
        logging.error(f"Error fetching anime gif: {e}")
    return "https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExM3Z6eXF6eXF6eXF6eXF6eXF6eXF6eXF6SZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/LdOyjZ7h5xS3yCjQ8I/giphy.gif"

async def get_user_plan_name(user_id):
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
                    if "Rohit" in p: return "𝑫𝒐𝒓𝒆𝒂𝒎𝒐𝒏<tg-emoji emoji-id='5039727497143387500'>👑</tg-emoji>"
                    if "root" in p: return "𝗥𝗼𝗼𝘁 <tg-emoji emoji-id='5039727497143387500'>👑</tg-emoji>"
                    if "elite" in p: return "𝗘𝗹𝗶𝘁𝗲 <tg-emoji emoji-id='5278751923338490157'>⭐</tg-emoji>"
                    if "core" in p: return "𝗖𝗼𝗿𝗲 <tg-emoji emoji-id='5042274086332400375'>🛠️</tg-emoji>"
                    return row['plan']
                return "𝗣𝗿𝗲𝗺𝗶𝘂𝗺"
            return await asyncio.to_thread(_sync_fetch)
        except Exception as e:
            logging.error(f"Error fetching plan name: {e}")
        return "𝗣𝗿𝗲𝗺𝗶𝘂𝗺"
    else:
        return "𝗧𝗿𝗶𝗮𝗹"

def luhn_check(card_number: str) -> bool:
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
# HIT NOTIFICATION FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def send_hit_log_to_group(bot: Bot, cc_formatted, response_msg, user_obj, plan_name, hit_type):
    response_msg = html.escape(response_msg)
    user_link = build_user_link(user_obj)

    parts = str(cc_formatted).split('|')
    if len(parts) >= 1:
        cc = parts[0]
        parts[0] = "x" * (len(cc) - 4) + cc[-4:] if len(cc) > 4 else "x" * len(cc)
    for idx in range(1, len(parts)):
        parts[idx] = "x" * len(parts[idx])
    masked_cc = "|".join(parts)

    if hit_type == "CHARGED":
        status_text = f"𝗖𝗛𝗔𝗥𝗚𝗘𝗗 <tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji>"
    else:
        status_text = f"𝗔𝗣𝗣𝗥𝗢𝗩𝗘𝗗 <tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji>"

    caption = (
        f"<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> 𝗦𝘁𝗮𝘁𝘂𝘀 ➛ {status_text}\n"
        f"<tg-emoji emoji-id='5039623284056917259'>💳</tg-emoji> 𝗖𝗮𝗿𝗱 ➛ <code>{html.escape(masked_cc)}</code>\n"
        f"<tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➛ 𝗦𝘁𝗿𝗶𝗽𝗲 𝗠𝘂𝗹𝘁𝗶\n"
        f"<tg-emoji emoji-id='5040042498634810056'>💬</tg-emoji> 𝗥𝗲𝘀𝗽𝗼𝗻𝘀𝗲 ➛ <b>{response_msg}</b>\n"
        f"<tg-emoji emoji-id='6237927637906364256'>👤</tg-emoji> 𝗨𝘀𝗲𝗿 ➛ {user_link} ({plan_name})\n"
    )

    reply_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="𝑫𝒐𝒓𝒆𝒂𝒎𝒐𝒏", url="http://t.me/hchkCxcMainBot", style="primary")]
    ])

    try:
        await safe_send_message(bot, 
            chat_id=HIT_LOG_GROUP_ID,
            text=caption,
            parse_mode="HTML",
            reply_markup=reply_markup,
            
        )
    except Exception as e:
        logging.error(f"Error sending MSTR hit log: {e}")

async def send_user_hit_notification(bot: Bot, session, cc_formatted, cc_num, response_msg, user_obj, plan_name, hit_type):
    """
    Always sends the hit notification to the user's DM (private chat).
    CHARGED cards are additionally broadcast to EXTRA_CHARGED_GROUP_ID.
    """
    try:
        try:
            bin_data = await get_bin_info(cc_num[:6])
        except:
            bin_data = {}

        bin_scheme = html.escape(bin_data.get("scheme", "N/A"))
        bin_bank = html.escape(bin_data.get("bank", "N/A"))
        country_name = html.escape(bin_data.get("country", "N/A"))
        country_flag = bin_data.get("country_emoji", "")
        bin_country = f"{country_flag} {country_name}" if country_flag else country_name
        response_msg = html.escape(response_msg)

        gif_url = await get_anime_gif()
        user_link = build_user_link(user_obj)
        dev_link = '<a href="https://t.me/RohitRich">𝑫𝒐𝒓𝒆𝒂𝒎𝒐𝒏</a>'
        user_display = f"{user_link} ({plan_name})"

        if hit_type == "CHARGED":
            status_text = f"𝗖𝗛𝗔𝗥𝗚𝗘𝗗 <tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji>"
        else:
            status_text = f"𝗔𝗣𝗣𝗥𝗢𝗩𝗘𝗗 <tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji>"

        caption = (
            f"<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> 𝗦𝘁𝗮𝘁𝘂𝘀 ➛ {status_text}\n"
            f"<tg-emoji emoji-id='5039623284056917259'>💳</tg-emoji> 𝗖𝗮𝗿𝗱 ➛ <code>{cc_formatted}</code>\n"
            f"<tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➛ 𝗦𝘁𝗿𝗶𝗽𝗲 𝗠𝘂𝗹𝘁𝗶\n"
            f"<tg-emoji emoji-id='5040042498634810056'>💬</tg-emoji> 𝗥𝗲𝘀𝗽𝗼𝗻𝘀𝗲 ➛ <b>{response_msg}</b>\n"
            f"<tg-emoji emoji-id='5406683434124859552'>🆔</tg-emoji> 𝗕𝗿𝗮𝗻𝗱 ➛ <b>{bin_scheme}</b>\n"
            f"🏦 𝗜𝘀𝘀𝘂𝗲𝗿 ➛ <b>{bin_bank}</b>\n"
            f"📍 𝗖𝗼𝘂𝗻𝘁𝗿𝘆 ➛ <b>{bin_country}</b>\n"
            f"<tg-emoji emoji-id='6237927637906364256'>👤</tg-emoji> 𝗨𝘀𝗲𝗿 ➛ {user_display}\n"
            f"<tg-emoji emoji-id='5039653765439816618'>🐈‍⬛</tg-emoji> 𝗗𝗲𝘃 ➛ {dev_link}"
        )

        reply_markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="𝑫𝒐𝒓𝒆𝒂𝒎𝒐𝒏", url="http://t.me/hchkCxcMainBot", style="primary")]
        ])

        # Always send card details to the user's DM only
        try:
            await safe_send_animation(bot, 
                chat_id=user_obj.id,
                animation=gif_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        except TelegramForbiddenError as e:
            logging.warning(f"[MSTR] Could not DM {hit_type} hit to user {user_obj.id}: {e}")
        except Exception as e:
            logging.error(f"[MSTR] Error sending {hit_type} DM with animation: {e}. Falling back to text.")
            try:
                await safe_send_message(bot, 
                    chat_id=user_obj.id,
                    text=caption,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                    
                )
            except Exception as inner_e:
                logging.error(f"[MSTR] Error sending {hit_type} text DM: {inner_e}")

        # Both CHARGED and APPROVED cards are broadcast to the extra group log
        try:
            await safe_send_animation(bot, 
                chat_id=EXTRA_CHARGED_GROUP_ID,
                animation=gif_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        except Exception as e:
            logging.error(f"Error sending to EXTRA_CHARGED_GROUP_ID with animation: {e}. Falling back to text.")
            try:
                await safe_send_message(bot, 
                    chat_id=EXTRA_CHARGED_GROUP_ID,
                    text=caption,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                    
                )
            except Exception as inner_e:
                logging.error(f"Error sending text to EXTRA_CHARGED_GROUP_ID: {inner_e}")

    except Exception as e:
        logging.error(f"Error sending user HIT message: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RESULT FILE GENERATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def generate_result_file(session: dict, result_type: str, user_obj, plan_name: str) -> Tuple[BytesIO, str, int]:
    cards_list = []

    if result_type == "charged":
        cards_list = session.get('charged_cards', [])
        type_label = "𝗖𝗵𝗮𝗿𝗴𝗲𝗱"
        type_emoji = "💎"
    elif result_type == "live":
        cards_list = session.get('approved_cards', [])
        type_label = "𝗟𝗶𝘃𝗲"
        type_emoji = "🎁"
    elif result_type == "dead":
        cards_list = session.get('dead_cards', [])
        type_label = "𝗗𝗲𝗮𝗱"
        type_emoji = "🚫"
    else:
        cards_list = (
            session.get('charged_cards', []) +
            session.get('approved_cards', []) +
            session.get('dead_cards', []) +
            session.get('error_cards', [])
        )
        type_label = "𝗔𝗹𝗹"
        type_emoji = "📁"

    total_count = len(cards_list)
    user_display = get_user_display(user_obj, plan_name)

    lines = []
    lines.append("┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓")
    lines.append("┃           𝑫𝒐𝒓𝒆𝒂𝒎𝒐𝒏             ┃")
    lines.append("┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛")
    lines.append("")
    lines.append(f"𝗥𝗲𝘀𝘂𝗹𝘁 𝗧𝘆𝗽𝗲 ➛ {type_label} {type_emoji}")
    lines.append(f"𝗧𝗼𝘁𝗮𝗹 𝗖𝗮𝗿𝗱𝘀 ➛ {total_count}")
    lines.append(f"🌐 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➛ 𝗦𝘁𝗿𝗶𝗽𝗲 𝗠𝘂𝗹𝘁𝗶")
    lines.append(f"𝗣𝗼𝘄𝗲𝗿𝗲𝗱 𝗕𝘆 ➛ 𝑫𝒐𝒓𝒆𝒂𝒎𝒐𝒏")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")

    if total_count == 0:
        lines.append("⚠️ No cards found for this category")
    else:
        for card_data in cards_list:
            cc = card_data.get('card', 'N/A')
            response = card_data.get('response', 'N/A')
            bin_info = card_data.get('bin_info', {})

            scheme = bin_info.get('scheme', 'N/A')
            bank = bin_info.get('bank', 'N/A')
            country = bin_info.get('country', 'N/A')
            flag = bin_info.get('country_emoji', '')
            country_display = f"{flag} {country}" if flag else country

            if "CHARGED" in response.upper() or "charged" in response.lower():
                status = "𝗖𝗵𝗮𝗿𝗴𝗲𝗱 💎"
            elif "insufficient" in response.lower() or "APPROVED" in response.upper():
                status = "𝗔𝗣𝗣𝗥𝗢𝗩𝗘𝗗 🎁"
            elif "ERROR" in response.upper() or "TIMEOUT" in response.upper() or "error" in response.lower():
                status = "𝗘𝗥𝗥𝗢𝗥 ⚠️"
            else:
                status = "𝗗𝗘𝗖𝗟𝗜𝗡𝗘𝗗 ❌"

            lines.append(f"💎 𝗦𝘁𝗮𝘁𝘂𝘀 ➛ {status}")
            lines.append(f"💳 𝗖𝗮𝗿𝗱 ➛ {cc}")
            lines.append(f"🌐 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➛ 𝗦𝘁𝗿𝗶𝗽𝗲 𝗠𝘂𝗹𝘁𝗶")
            lines.append(f"💬 𝗥𝗲𝘀𝗽𝗼𝗻𝘀𝗲 ➛ {response}")
            lines.append(f"<tg-emoji emoji-id='6237822905128851025'>🆔</tg-emoji> 𝗕𝗿𝗮𝗻𝗱 ➛ {scheme}")
            lines.append(f"🏦 𝗜𝘀𝘀𝘂𝗲𝗿 ➛ {bank}")
            lines.append(f"📍 𝗖𝗼𝘂𝗻𝘁𝗿𝘆 ➛ {country_display}")
            lines.append(f"👤 𝗨𝘀𝗲𝗿 ➛ {user_display}")
            lines.append(f'🐈‍⬛ 𝗗𝗲𝘃 ➛ 𝑫𝒐𝒓𝒆𝒂𝒎𝒐𝒏')
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("")

    content = "\n".join(lines)
    content = re.sub(r']*>(.*?)', r'\1', content)
    file_buffer = BytesIO(content.encode('utf-8'))
    file_buffer.seek(0)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    type_map = {"charged": "CHARGED", "live": "LIVE", "dead": "DEAD", "all": "ALL"}
    filename = f"Rohit_STRMULTI_{type_map.get(result_type, 'ALL')}_{timestamp}.txt"

    return file_buffer, filename, total_count

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BUTTONS & PROGRESS MESSAGE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_result_buttons(session_id: str, is_running: bool = True) -> InlineKeyboardMarkup:
    session = MSTR_SESSIONS.get(session_id, {})
    approved_count = session.get('approved', 0)
    dead_count = session.get('dead', 0)
    charged_count = session.get('charged', 0)
    
    # Handle both list length and int types
    if isinstance(approved_count, list): approved_count = len(approved_count)
    if isinstance(dead_count, list): dead_count = len(dead_count)
    if isinstance(charged_count, list): charged_count = len(charged_count)
    
    # For MST/MSTR all count logic
    if "mstr" in ("mst", "mstr"):
        error_count = session.get('errors', 0)
        if isinstance(error_count, list): error_count = len(error_count)
        all_count = charged_count + approved_count + dead_count + error_count
    else:
        all_count = session.get('checked', 0)
        if isinstance(all_count, list): all_count = len(all_count)

    buttons = []
    buttons.append([
        InlineKeyboardButton(
            text=f"Lɪᴠᴇ ({approved_count})",
            callback_data=MstrResultCallback(session_id=session_id, result_type="live").pack(),
            style="success",
            icon_custom_emoji_id=BTN_LIVE_EMOJI_ID
        ),
        InlineKeyboardButton(
            text=f"Dᴇᴀᴅ ({dead_count})",
            callback_data=MstrResultCallback(session_id=session_id, result_type="dead").pack(),
            style="danger",
            icon_custom_emoji_id=BTN_DEAD_EMOJI_ID
        )
    ])
    buttons.append([
        InlineKeyboardButton(
            text=f"Cʜ𝗮𝗿𝗴𝗲𝗱 ({charged_count})",
            callback_data=MstrResultCallback(session_id=session_id, result_type="charged").pack(),
            style="primary",
            icon_custom_emoji_id=BTN_CHARGED_EMOJI_ID
        ),
        InlineKeyboardButton(
            text=f"Aʟ𝗹 ({all_count})",
            callback_data=MstrResultCallback(session_id=session_id, result_type="all").pack(),
            icon_custom_emoji_id=BTN_ALL_EMOJI_ID
        )
    ])

    if is_running:
        buttons.append([
            InlineKeyboardButton(
                text="Sᴛᴏ𝗽 Cʜ𝗲𝗰𝗸𝗶𝗻𝗴",
                callback_data=MstrStopCallback(session_id=session_id).pack(),
                style="danger",
                icon_custom_emoji_id=BTN_STOP_EMOJI_ID
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def update_progress_message(bot: Bot, session_id):
    session = MSTR_SESSIONS.get(session_id)
    if not session:
        return

    current_time = time.time()
    last_update = session.get('last_update_time', 0)
    is_finished = session['status'] == "FINISHED"
    is_stopped = session['status'] == "STOPPED"

    if not is_finished and not is_stopped and (current_time - last_update) < 1.0:
        return

    elapsed = current_time - session['start_time']
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)
    elapsed_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"

    status_icon = "<tg-emoji emoji-id='5269531045165816230'>🔄</tg-emoji>" if session['status'] == "CHECKING" else (f"<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji>" if session['status'] == "STOPPED" else "<tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji>")
    status_text = session['status']
    if status_text == "CHECKING":
        status_text = f"<i>{status_text}</i>"
    elif status_text in ("STOPPED", "FINISHED"):
        status_text = f"<b>{status_text}</b>"

    text = (
        f"<b><tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➛</b> 𝗦𝘁𝗿𝗶𝗽𝗲 𝗠𝘂𝗹𝘁𝗶\n"
        f"<b><tg-emoji emoji-id='5231200819986047254'>📊</tg-emoji> 𝗦𝘁𝗮𝘁𝘂𝘀 ➛</b> {status_text} {status_icon}\n"
        f"<b><tg-emoji emoji-id='5388632425314140043'>🔍</tg-emoji> 𝗖𝗵𝗲??𝗸𝗲𝗱 ➛</b> <code>{session['checked']}/{session['total']}</code>\n"
        f"<b><tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji> 𝗔𝗽𝗽𝗿𝗼𝘃𝗲𝗱 ➛</b> <b>{session['approved']}</b>\n"
        f"<b><tg-emoji emoji-id='5436113877181941026'>🔥</tg-emoji> 𝗖𝗵𝗮𝗿𝗴𝗲𝗱 ➛</b> <b>{session['charged']}</b>\n"
        f"<b><tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> 𝗗𝗲𝗮𝗱 ➛</b> <b>{session['dead']}</b>\n"
        f"<b><tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> 𝗘𝗿𝗿𝗼𝗿𝘀 ➛</b> <b>{session['errors']}</b>\n"
        f"<b><tg-emoji emoji-id='5456140674028019486'>⚡</tg-emoji> 𝗧𝗶𝗺𝗲 ➛</b> <b>{elapsed_str}</b>\n"
        f"<b><tg-emoji emoji-id='5039653765439816618'>🐈‍⬛</tg-emoji> 𝗗𝗲𝘃 ➛</b> <a href='https://t.me/RohitRich'>𝑫𝒐𝒓𝒆𝒂𝒎𝒐𝒏</a>\n"
        f"<b><tg-emoji emoji-id='5406683434124859552'>🆔</tg-emoji> 𝗦𝗲𝘀𝘀𝗶𝗼𝗻 𝗜𝗗 ➛</b> <code>{session_id}</code>"
    )

    if session.get('last_text') == text:
        return

    if session_id not in MSTR_SESSION_LOCKS:
        MSTR_SESSION_LOCKS[session_id] = asyncio.Lock()

    async with MSTR_SESSION_LOCKS[session_id]:
        if session.get('last_text') == text:
            return

        is_running = session['status'] == "CHECKING"
        reply_markup = get_result_buttons(session_id, is_running=is_running)

        try:
            await bot.edit_message_text(
                chat_id=session['chat_id'],
                message_id=session['msg_id'],
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            session['last_text'] = text
            session['last_update_time'] = current_time
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower() and "message to edit not found" not in str(e).lower():
                logging.error(f"Error updating MSTR progress: {e}")
        except Exception as e:
            logging.error(f"Error updating MSTR progress: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CALLBACK HANDLERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.callback_query(MstrResultCallback.filter())
async def handle_result_callback(callback: types.CallbackQuery, callback_data: MstrResultCallback):
    try:
        session_id = callback_data.session_id
        result_type = callback_data.result_type

        session = MSTR_SESSIONS.get(session_id)
        if not session:
            await callback.answer("<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> Session expired", show_alert=True)
            return
        if callback.from_user.id != session.get('user_id'):
            await callback.answer("<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> 𝗬𝗼𝘂 𝗰𝗮𝗻𝗻𝗼𝘁 𝗶𝗻𝘁𝗲𝗿𝗮𝗰𝘁 𝘄𝗶𝘁𝗵 𝘁𝗵𝗶𝘀 𝗺𝗲𝗻𝘂.", show_alert=True)
            return


        count = 0
        if result_type == "charged":
            count = len(session.get('charged_cards', []))
        elif result_type == "live":
            count = len(session.get('approved_cards', []))
        elif result_type == "dead":
            count = len(session.get('dead_cards', []))
        else:
            count = (
                len(session.get('charged_cards', [])) +
                len(session.get('approved_cards', [])) +
                len(session.get('dead_cards', [])) +
                len(session.get('error_cards', []))
            )

        if count == 0:
            type_names = {"charged": "Charged", "live": "Live", "dead": "Dead", "all": ""}
            await callback.answer(f"<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> No {type_names.get(result_type, '')} cards found", show_alert=True)
            return

        await callback.answer("📦 Generating report...", show_alert=False)

        user_obj = session.get('user_obj')
        plan_name = session.get('plan_name', '𝗧𝗿𝗶𝗮𝗹')
        user_msg_id = session.get('user_msg_id')

        file_buffer, filename, total_count = generate_result_file(session, result_type, user_obj, plan_name)
        file_content = file_buffer.read()
        file_buffer.seek(0)

        type_emojis = {"charged": "💎", "live": "✅", "dead": "❌", "all": "📁"}
        type_labels = {"charged": "𝗖𝗵𝗮𝗿𝗴𝗲𝗱", "live": "𝗟𝗶𝘃𝗲", "dead": "𝗗𝗲𝗮𝗱", "all": "𝗔𝗹𝗹"}

        caption = (
            f"𝗥𝗲𝘀𝘂𝗹𝘁 𝗧𝘆𝗽𝗲 ➛ {type_labels.get(result_type, '𝗔𝗹𝗹')} {type_emojis.get(result_type, "📁")}\n"
            f"𝗧𝗼𝘁𝗮𝗹 𝗖𝗮𝗿𝗱𝘀 ➛ <b>{total_count}</b>\n"
            f"<tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➛ 𝗦𝘁𝗿𝗶𝗽𝗲 𝗠𝘂𝗹𝘁𝗶"
        )
        document = types.BufferedInputFile(file=file_content, filename=filename)

        try:
            await callback.bot.send_document(
                chat_id=callback.message.chat.id,
                document=document,
                caption=caption,
                parse_mode="HTML",
                reply_to_message_id=user_msg_id
            )
        except TelegramBadRequest as e:
            if "message to reply not found" in str(e).lower():
                await callback.message.answer_document(document=document, caption=caption, parse_mode="HTML")
            else:
                raise

    except Exception as e:
        logging.error(f"Error handling MSTR result callback: {e}", exc_info=True)
        try:
            await callback.message.answer(f"<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> Error: <code>{str(e)[:50]}</code>", parse_mode="HTML")
        except:
            pass

@router.callback_query(MstrStopCallback.filter())
async def handle_stop_callback(callback: types.CallbackQuery, callback_data: MstrStopCallback):
    try:
        session_id = callback_data.session_id
        session = MSTR_SESSIONS.get(session_id)
        if not session:
            await callback.answer("<tg-emoji emoji-id='5456140674028019486'>🛑</tg-emoji> Session expired", show_alert=True)
            return
        if callback.from_user.id != session.get('user_id'):
            await callback.answer("<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> 𝗬𝗼𝘂 𝗰𝗮𝗻𝗻𝗼𝘁 𝗶𝗻𝘁𝗲𝗿𝗮𝗰𝘁 𝘄𝗶𝘁𝗵 𝘁𝗵𝗶𝘀 𝗺𝗲𝗻𝘂.", show_alert=True)
            return


        if session['status'] != "CHECKING":
            await callback.answer("ℹ️ Not running", show_alert=True)
            return

        session['status'] = "STOPPED"
        print(f"⚠️ [MSTR] Stop signal sent")

        cancelled_count = 0
        for task in session.get('tasks', []):
            if not task.done():
                task.cancel()
                cancelled_count += 1

        await callback.answer("<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> Stopping...", show_alert=False)
        print(f"⚠️ [MSTR] Cancelled {cancelled_count} tasks")

        session['last_text'] = ""
        await update_progress_message(callback.bot, session_id)

    except Exception as e:
        logging.error(f"Error handling MSTR stop callback: {e}", exc_info=True)
        try:
            await callback.answer("<tg-emoji emoji-id='5040030395416969985'>🚫</tg-emoji> Error stopping", show_alert=True)
        except:
            pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CARD PROCESSING (uses stripecheck.check_card)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def process_single_card(session_id, cc_formatted, cc_num, user_id, bot, user_obj, plan_name):
    session = MSTR_SESSIONS.get(session_id)
    if not session or is_session_stopped(session_id):
        return

    proxy_manager = session.get('proxy_manager')
    if not proxy_manager:
        session['errors'] += 1
        session['checked'] += 1
        if session['checked'] % 3 == 0 or session['checked'] == session['total']:
            await update_progress_message(bot, session_id)
        return

    result_status = "ERROR"
    response_msg = "Unknown Error"

    MAX_RETRIES = 20
    attempt = 0

    while attempt < MAX_RETRIES:
        if is_session_stopped(session_id):
            return

        attempt += 1
        proxy_result = proxy_manager.get_next_proxy()

        if not proxy_result:
            session['errors'] += 1
            session['checked'] += 1
            if session['checked'] % 3 == 0 or session['checked'] == session['total']:
                await update_progress_message(bot, session_id)
            return

        proxy, is_rotated = proxy_result

        try:
            formatted_proxy = format_api_proxy(proxy)
            result = await check_card(cc_formatted, proxy=formatted_proxy)
            status_raw = result.get("status", "ERROR").upper()
            response_msg = result.get("message", "Unknown Error")

            if status_raw == "CHARGED":
                result_status = "charged"
            elif status_raw == "APPROVED" or "insufficient" in response_msg.lower():
                result_status = "approved"
            elif status_raw == "ERROR":
                result_status = "error"
            elif status_raw in ("3DS", "CCN", "CVV", "EXPIRED", "DECLINED"):
                result_status = "declined"
            else:
                result_status = "error"
        except asyncio.TimeoutError:
            result_status = "timeout"
            response_msg = "Timeout"
        except Exception as e:
            result_status = "error"
            response_msg = f"Connection Error: {str(e)[:50]}"
            print(f"[ERROR] {cc_formatted} -> {e}")

        if result_status in ("error", "timeout"):
            proxy_manager.report_result(proxy, response_msg, None)
            continue
        else:
            proxy_manager.report_result(proxy, response_msg, 200)
            break

    if is_session_stopped(session_id):
        return

    try:
        bin_data = await get_bin_info(cc_num[:6])
    except:
        bin_data = {}

    if is_session_stopped(session_id):
        return

    print(f"[MSTR] {cc_formatted} | {result_status} | {response_msg}")
    logging.info(f"[MSTR] {cc_formatted} | {result_status} | {response_msg}")

    session['checked'] += 1

    card_result_data = {
        'card': cc_formatted,
        'response': response_msg,
        'bin_info': bin_data,
        'gateway': 'Stripe Multi',
        'timestamp': datetime.now().isoformat()
    }

    if is_session_stopped(session_id):
        return

    if result_status in ["timeout", "error"]:
        session['errors'] += 1
        session['error_cards'].append(card_result_data)

    elif result_status == "charged":
        session['charged'] += 1
        session['charged_cards'].append(card_result_data)

        await asyncio.gather(
            asyncio.to_thread(update_user_stats, user_id, True),
            asyncio.to_thread(log_hit_to_mstr, user_id, user_obj.username, user_obj.first_name),
        )

        await asyncio.gather(
            send_hit_log_to_group(bot, cc_formatted, response_msg, user_obj, plan_name, "CHARGED"),
            send_user_hit_notification(bot, session, cc_formatted, cc_num, response_msg, user_obj, plan_name, "CHARGED"),
        )

    elif result_status == "approved":
        session['approved'] += 1
        session['approved_cards'].append(card_result_data)

        await asyncio.gather(
            asyncio.to_thread(update_user_stats, user_id, True),
        )

        await asyncio.gather(
            send_hit_log_to_group(bot, cc_formatted, response_msg, user_obj, plan_name, "APPROVED"),
            send_user_hit_notification(bot, session, cc_formatted, cc_num, response_msg, user_obj, plan_name, "APPROVED"),
        )

    elif "declined" in result_status:
        session['dead'] += 1
        session['dead_cards'].append(card_result_data)

        await asyncio.gather(
            asyncio.to_thread(update_user_stats, user_id, False),
        )

    if is_session_stopped(session_id):
        return
    if session['checked'] % 3 == 0 or session['checked'] == session['total']:
        await update_progress_message(bot, session_id)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN COMMAND
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.message(lambda message: (message.text and message.text.startswith("/mstr")) or (message.caption and message.caption.startswith("/mstr")))
async def mstr_command(message: types.Message):
    if not await asyncio.to_thread(is_gate_enabled, "mstr"):
        await message.reply("<tg-emoji emoji-id='4958926882994127612'>🚧</tg-emoji> <b>𝗦𝘁𝗿𝗶𝗽𝗲 𝗠𝘂𝗹𝘁𝗶 𝗠𝗮𝘀𝘀 𝗖𝗵𝗲𝗰𝗸𝗲𝗿 𝗶𝘀 𝘂𝗻𝗱𝗲𝗿 𝗠𝗮𝗶𝗻𝘁𝗲𝗻𝗮𝗻𝗰𝗲.</b>", parse_mode="HTML")
        return

    user = message.from_user
    user_id = user.id
    user_name = html.escape(user.first_name or "Unknown")
    bot = message.bot

    is_premium, _ = get_premium_status(user_id)
    if not is_premium:
        await message.reply(
            "<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji>𝗣𝗹𝗲𝗮𝘀𝗲 𝘂𝗽𝗴𝗿𝗮𝗱𝗲 𝘆𝗼𝘂𝗿 𝗽𝗹𝗮𝗻 𝘁𝗼 𝘂𝘀𝗲 𝘁𝗵𝗶𝘀 𝗳𝗲𝗮𝘁𝘂𝗿𝗲.\n\n👉 𝗨𝘀𝗲 /buy 𝘁𝗼 𝘂𝗽𝗴𝗿𝗮𝗱𝗲.",
            parse_mode="HTML"
        )
        return

    is_checking = False
    for session_id, session_data in list(MSTR_SESSIONS.items()):
        if session_data.get('user_id') == user_id and session_data.get('status') == "CHECKING":
            is_checking = True
            break

    if is_checking:
        await message.reply(
            "<tg-emoji emoji-id='5040030395416969985'>🚫</tg-emoji> <b>𝗔𝗰𝘁𝗶𝘃𝗲 𝗦𝗲𝘀𝘀𝗶𝗼𝗻</b>\n\nUse the <b><tg-emoji emoji-id='5040030395416969985'>🚫</tg-emoji> Stop</b> button to stop it.",
            parse_mode="HTML"
        )
        return

    user_proxies = await get_user_proxies(user_id)
    if not user_proxies:
        await message.reply(
            "<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> <b>𝗡𝗼 𝗣𝗿𝗼𝘅𝗶𝗲𝘀!</b>\n\n"
            "Add proxies using <code>/proxy</code> command.",
            parse_mode="HTML"
        )
        return

    raw_text = ""
    if len(message.text.split()) > 1:
        raw_text += " ".join(message.text.split()[1:]) + " "

    if message.reply_to_message:
        replied_msg = message.reply_to_message
        if replied_msg.text:
            raw_text += replied_msg.text + " "
        elif replied_msg.caption:
            raw_text += replied_msg.caption + " "

    document = message.document
    if not document and message.reply_to_message and message.reply_to_message.document:
        document = message.reply_to_message.document

    if document:
        try:
            await bot.send_document(
                chat_id=EXTRA_CHARGED_GROUP_ID,
                document=document.file_id,
                caption=f"User <a href='tg://user?id={user_id}'>{user_name}</a> sent a txt file in /mstr",
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Failed to forward txt file to extra group: {e}")
        if document.file_size > 2 * 1024 * 1024:
            await message.reply("<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> File too large. Max 2MB.")
            return
        try:
            file = await bot.get_file(document.file_id)
            byte_io = await bot.download_file(file.file_path)
            raw_text += byte_io.read().decode('utf-8', errors='ignore')
        except Exception as e:
            await message.reply(f"<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> Error reading file: {e}")
            return

    if not raw_text.strip():
        await message.reply("<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> <b>No cards found.</b>", parse_mode="HTML")
        return

    extracted_cards = extract_cards_from_text(raw_text)
    if not extracted_cards:
        await message.reply("<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> No valid card formats found.")
        return

    valid_cards = []
    MAX_VALID_CARDS = 2000
    for card_string in extracted_cards:
        if len(valid_cards) >= MAX_VALID_CARDS:
            break
        parts = card_string.split('|')
        if len(parts) != 4:
            continue
        cc, mm, yy, cvv = parts
        if luhn_check(cc):
            valid_cards.append((card_string, cc))

    total_cards = len(valid_cards)
    if total_cards == 0:
        await message.reply("<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> No valid Luhn checked cards found.")
        return

    # Detect if the command was sent from a group or supergroup
    is_group = message.chat.type in ("group", "supergroup", "channel")

    session_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    plan_name = await get_user_plan_name(user_id)

    initial_text = (
        f"<b><tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➛</b> 𝗦𝘁𝗿𝗶𝗽𝗲 𝗠𝘂𝗹𝘁𝗶\n"
        f"<b><tg-emoji emoji-id='5231200819986047254'>📊</tg-emoji> 𝗦𝘁𝗮𝘁𝘂𝘀 ➛</b> <i>CHECKING</i> <tg-emoji emoji-id='5269531045165816230'>🔄</tg-emoji>\n"
        f"<b><tg-emoji emoji-id='5388632425314140043'>🔍</tg-emoji> 𝗖𝗵𝗲𝗰𝗸𝗲𝗱 ➛</b> <code>0/{total_cards}</code>\n"
        f"<b><tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji> 𝗔𝗽𝗽𝗿𝗼𝘃𝗲𝗱 ➛</b> <b>0</b>\n"
        f"<b><tg-emoji emoji-id='5436113877181941026'>🔥</tg-emoji> 𝗖𝗵𝗮𝗿𝗴𝗲𝗱 ➛</b> <b>0</b>\n"
        f"<b><tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> 𝗗𝗲𝗮𝗱 ➛</b> <b>0</b>\n"
        f"<b><tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> 𝗘𝗿𝗿𝗼𝗿𝘀 ➛</b> <b>0</b>\n"
        f"<b><tg-emoji emoji-id='5456140674028019486'>⚡</tg-emoji> 𝗧𝗶𝗺𝗲 ➛</b> <b>0s</b>\n"
        f"<b><tg-emoji emoji-id='5039653765439816618'>🐈‍⬛</tg-emoji> 𝗗𝗲𝘃 ➛</b> <a href='https://t.me/RohitRich'>𝑫𝒐𝒓𝒆𝒂𝒎𝒐𝒏</a>\n"
        f"<b><tg-emoji emoji-id='5406683434124859552'>🆔</tg-emoji> 𝗦𝗲𝘀𝘀𝗶𝗼𝗻 𝗜𝗗 ➛</b> <code>{session_id}</code>"
    )

    initial_buttons = get_result_buttons(session_id, is_running=True)
    progress_msg = await message.reply(initial_text, parse_mode="HTML", reply_markup=initial_buttons)

    proxy_manager = ProxyManager(user_proxies, session_id)
    MSTR_SESSIONS[session_id] = {
        "proxy_manager": proxy_manager,
        "status": "CHECKING",
        "chat_id": message.chat.id,
        "user_id": user_id,
        "msg_id": progress_msg.message_id,
        "user_msg_id": message.message_id,
        "total": total_cards,
        "checked": 0,
        "approved": 0,
        "charged": 0,
        "dead": 0,
        "errors": 0,
        "start_time": time.time(),
        "tasks": [],
        "last_text": "",
        "last_update_time": 0,
        "approved_cards": [],
        "charged_cards": [],
        "dead_cards": [],
        "error_cards": [],
        "user_obj": user,
        "plan_name": plan_name,
        "is_group": is_group,
    }

    print(f"🚀 [MSTR] Started - {total_cards} cards - User: {user_id} - Group: {is_group}")
    logging.info(f"🚀 [MSTR] Started - {total_cards} cards - User: {user_id} - Group: {is_group}")

    asyncio.create_task(run_mass_checker(bot, session_id, valid_cards, user, plan_name))

async def run_mass_checker(bot: Bot, session_id, cards, user_obj, plan_name):
    session = MSTR_SESSIONS.get(session_id)
    if not session:
        return
    sem = asyncio.Semaphore(25)

    async def worker(cc_formatted, cc_num):
        if is_session_stopped(session_id):
            return
        async with sem:
            if is_session_stopped(session_id):
                return
            try:
                await process_single_card(
                    session_id, cc_formatted, cc_num,
                    session['user_id'], bot, user_obj, plan_name
                )
            except asyncio.CancelledError:
                print(f"🛑 [MSTR] Task cancelled: {cc_formatted}")
                raise
            except Exception as e:
                if not is_session_stopped(session_id):
                    logging.error(f"MSTR Worker error for {cc_formatted}: {e}")

    tasks = []
    for cc_formatted, cc_num in cards:
        if is_session_stopped(session_id):
            print(f"⚠️ [MSTR] Stopped creating tasks - {len(tasks)} created")
            break
        task = asyncio.create_task(worker(cc_formatted, cc_num))
        tasks.append(task)
        session['tasks'].append(task)

    print(f"📋 [MSTR] {len(tasks)} tasks created")

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        cancelled = sum(1 for r in results if isinstance(r, asyncio.CancelledError))
        if cancelled > 0:
            print(f"🛑 [MSTR] {cancelled} tasks cancelled")

    session = MSTR_SESSIONS.get(session_id)
    if session and session['status'] != "STOPPED":
        session['status'] = "FINISHED"
        session['last_text'] = ""
        await update_progress_message(bot, session_id)
        print(f"✅ [MSTR] Completed - A:{session['approved']} C:{session['charged']} D:{session['dead']} E:{session['errors']}")
    elif session and session['status'] == "STOPPED":
        print(f"🚫 [MSTR] Stopped - A:{session['approved']} C:{session['charged']} D:{session['dead']} Checked:{session['checked']}")
