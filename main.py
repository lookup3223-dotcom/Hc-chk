import os
import re
import database # Import database first to guarantee psycopg2 is mocked/patched before anything else imports it
import logging
import asyncio
import time
from aiohttp import web
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from typing import Callable, Dict, Any, Awaitable

from aiogram import Bot, Dispatcher, types, F, Router, BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database import get_user, create_user, DB_CONFIG, get_db_connection
from gate import on_command, off_command
from fb import setup_feedback_handler, feedback_cmd, router as fb_router
from stats import stats_command
from tools.binn import binn_command
from cmds import cmds_command, router as cmds_router
from broad import broad_command, router as broad_router
from ban import ban_command, unban_command, BanMiddleware, router as ban_router
from status import vps_command, router as status_router

from sub import (
    sub_command, rc_command, suball_command, g_code_command,
    claim_command, info_command, rsub_command, buy_command, adcr_command,
    Rohit_admin_command, gen_command
)
from proxy import proxy_command, checkproxy_command, clearproxy_command

from gates.st import st_command
from gates.str import router as str_router, str_command
from gates.sh import sh_command, sh_callback_handler
from gates.sp import sp_command
from gates.hc import hc_command
from gates.vbv import vbv_command
from gates.pp import pp_command
from gates.rz import rz_command, router as rz_router
from gates.sh import router as sh_router
from gates.chk import chk_command
from gates.b3 import b3_command


from mass_gates.msh import (
    router as msh_router, MshStopCallback, MshResultCallback,
    handle_stop_callback as msh_stop_handler,
    handle_result_callback as msh_result_handler,
)
from mass_gates.mrz import (
    router as mrz_router, MrzStopCallback, MrzResultCallback,
    handle_stop_callback as mrz_stop_handler,
    handle_result_callback as mrz_result_handler,
)
from mass_gates.mst import (
    router as mst_router, MstStopCallback, MstResultCallback,
    handle_stop_callback as mst_stop_handler,
    handle_result_callback as mst_result_handler,
    mst_command
)
from mass_gates.mstr import (
    router as mstr_router, MstrStopCallback, MstrResultCallback,
    handle_stop_callback as mstr_stop_handler,
    handle_result_callback as mstr_result_handler,
    mstr_command
)
from mass_gates.sitechk import (
    sitechk_command, addsite_command, siteall_command,
    removeall_command, dedupe_command, proxyinfo_command, resetproxy_command,
    remsite_command
)

import payments as pay_sys

BOT_TOKEN = "8994355658:AAHFJkFILCabtUIDmkgAms2glXCk_Gt4iJw"
WEBHOOK_URL = f"https://cxchk.site/{BOT_TOKEN}"
WEBHOST = "0.0.0.0"
WEBPORT = 8080

LOG_CHANNEL_ID =  -1004420032759
BOT_LINK = "https://t.me/HchkCxcMainbot"

REQUIRED_CHANNEL_ID = -1004420032759
REQUIRED_GROUP_ID = -1004450624623
CHANNEL_LINK = "https://t.me/ToxiczNetwork"
GROUP_LINK = "https://t.me/+1rRt3_JcSaI1NTJl"

JOIN_TEXT = (
    "<blockquote><b><tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> 𝗔𝗰𝗰𝗲𝘀𝘀 𝗥𝗲𝘀𝘁𝗿𝗶𝗰𝘁𝗲𝗱</b>\n\n"
    "<b>𝗬𝗼𝘂 𝗺𝘂𝘀𝘁 𝗷𝗼𝗶𝗻 𝗼𝘂𝗿 𝗰𝗵𝗮𝗻𝗻𝗲𝗹 𝗮𝗻𝗱 𝗴𝗿𝗼𝘂𝗽 𝘁𝗼 𝘂𝘀𝗲 𝘁𝗵𝗶𝘀 𝗯𝗼𝘁.</b>\n\n"
    "<b><tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji> 𝗧𝗮𝗽 𝘁𝗵𝗲 𝗯𝘂𝘁𝘁𝗼𝗻𝘀 𝗯𝗲𝗹𝗼𝘄 𝘁𝗼 𝗷𝗼𝗶𝗻, 𝘁𝗵𝗲𝗻 𝘁𝗮𝗽 𝗩𝗲𝗿𝗶𝗳𝘆.</b></blockquote>"
)

PRICING_TEXT = (
    "<b>┌── <tg-emoji emoji-id='5039623284056917259'>💳</tg-emoji> 𝗣𝗥𝗜𝗖𝗜𝗡𝗚 𝗣𝗟𝗔𝗡𝗦 ──┐</b>\n\n"
    "<b><tg-emoji emoji-id='5042274086332400375'>🛠️</tg-emoji> 𝗖𝗢𝗥𝗘 𝗣𝗟𝗔𝗡</b>\n"
    "<b>├ 𝗗𝘂𝗿𝗮𝘁𝗶𝗼𝗻 ➛</b> 𝟳 Days\n"
    "<b>└ 𝗣𝗿𝗶𝗰𝗲 ➛</b> 𝟱$\n\n"
    "<b><tg-emoji emoji-id='5278751923338490157'>⭐</tg-emoji> 𝗘𝗟𝗜𝗧𝗘 𝗣𝗟𝗔𝗡</b>\n"
    "<b>├ 𝗗𝘂𝗿𝗮𝘁𝗶𝗼𝗻 ➛</b> 𝟭𝟱 Days\n"
    "<b>└ 𝗣𝗿𝗶𝗰𝗲 ➛</b> 𝟳$\n\n"
    "<b><tg-emoji emoji-id='5039727497143387500'>👑</tg-emoji> 𝗥𝗢𝗢𝗧 𝗣𝗟𝗔𝗡</b>\n"
    "<b>├ 𝗗𝘂𝗿𝗮𝘁𝗶𝗼𝗻 ➛</b> 𝟯𝟬 Days\n"
    "<b>└ 𝗣𝗿𝗶𝗰𝗲 ➛</b> 𝟭𝟱$\n"
    "<b>└──────────────────┘</b>"
)

import json
from aiogram.client.session.aiohttp import AiohttpSession

def custom_dumps(obj, *args, **kwargs):
    return json.dumps(obj, *args, **kwargs)

session = AiohttpSession(json_dumps=custom_dumps)
bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

dp.include_router(msh_router)
dp.include_router(mrz_router)
dp.include_router(mst_router)
dp.include_router(str_router)
dp.include_router(mstr_router)
dp.include_router(cmds_router)
dp.include_router(fb_router)
dp.include_router(ban_router)
dp.include_router(broad_router)
dp.include_router(status_router)
dp.include_router(rz_router)
dp.include_router(sh_router)

from mass_gates.mchk import router as mchk_router

router = Router()
dp.include_router(mchk_router)
dp.include_router(router)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MEMBERSHIP CACHE — avoids Telegram API calls on every message
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Cache structure: {user_id: (is_member: bool, expires_at: float)}
_MEMBERSHIP_CACHE: Dict[int, tuple] = {}
_MEMBERSHIP_CACHE_TTL = 60  # seconds — positive results cached 60s, negatives 15s
_MEMBERSHIP_LOCK: Dict[int, asyncio.Lock] = {}
_MEMBERSHIP_LOCK_MAP_LOCK = asyncio.Lock()

_VALID_STATUSES = {"member", "administrator", "creator"}


async def _get_user_lock(user_id: int) -> asyncio.Lock:
    async with _MEMBERSHIP_LOCK_MAP_LOCK:
        if user_id not in _MEMBERSHIP_LOCK:
            _MEMBERSHIP_LOCK[user_id] = asyncio.Lock()
        return _MEMBERSHIP_LOCK[user_id]


async def check_membership(user_id: int, bot_instance: Bot) -> bool:
    now = time.monotonic()

    # Fast path: serve from cache if still valid
    cached = _MEMBERSHIP_CACHE.get(user_id)
    if cached is not None:
        is_member, expires_at = cached
        if now < expires_at:
            return is_member

    # Deduplicate concurrent checks for the same user via per-user lock
    lock = await _get_user_lock(user_id)
    async with lock:
        # Re-check cache inside the lock — another coroutine may have filled it
        cached = _MEMBERSHIP_CACHE.get(user_id)
        if cached is not None:
            is_member, expires_at = cached
            if now < expires_at:
                return is_member

        try:
            ch, gr = await asyncio.gather(
                bot_instance.get_chat_member(REQUIRED_CHANNEL_ID, user_id),
                bot_instance.get_chat_member(REQUIRED_GROUP_ID, user_id),
            )
            is_member = ch.status in _VALID_STATUSES and gr.status in _VALID_STATUSES
        except Exception as e:
            logging.warning(f"Membership check failed for {user_id}: {e}")
            # On error, assume member to avoid blocking legitimate users temporarily
            is_member = True

        # Cache positives for 60s, negatives for 15s (so denied users don't wait long)
        ttl = _MEMBERSHIP_CACHE_TTL if is_member else 15
        _MEMBERSHIP_CACHE[user_id] = (is_member, now + ttl)
        return is_member


def invalidate_membership_cache(user_id: int):
    """Call this after a user verifies membership so the cache is cleared immediately."""
    _MEMBERSHIP_CACHE.pop(user_id, None)


_JOIN_KB = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="𝗝𝗼𝗶𝗻 𝗖𝗵𝗮𝗻𝗻𝗲𝗹", url=CHANNEL_LINK, icon_custom_emoji_id="5042274086332400375", style="primary"),
        InlineKeyboardButton(text="𝗝𝗼𝗶𝗻 𝗚𝗿𝗼𝘂𝗽", url=GROUP_LINK, icon_custom_emoji_id="5039653765439816618", style="primary")
    ],
    [
        InlineKeyboardButton(text="𝗩𝗲𝗿𝗶𝗳𝘆 𝗝𝗼𝗶𝗻𝗲𝗱", callback_data="verify_membership", icon_custom_emoji_id="5039844895779455925", style="success")
    ]
])

ADMIN_IDS = {8987937105}


class MembershipMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: types.Message, data: Dict[str, Any]):
        user = event.from_user
        if not user:
            return await handler(event, data)
        if user.id in ADMIN_IDS:
            return await handler(event, data)
        
        # If the user runs /start, force a fresh check by clearing the cache
        if event.text and event.text.lower().startswith("/start"):
            invalidate_membership_cache(user.id)

        if not await check_membership(user.id, data["bot"]):
            await event.reply(text=JOIN_TEXT, parse_mode="HTML", reply_markup=_JOIN_KB, disable_web_page_preview=True)
            return
        return await handler(event, data)


dp.message.middleware(MembershipMiddleware())
dp.message.middleware(BanMiddleware())


@router.callback_query(F.data == "verify_membership")
async def verify_membership_callback(callback: types.CallbackQuery):
    # Invalidate cache so the live check runs fresh
    invalidate_membership_cache(callback.from_user.id)
    joined = await check_membership(callback.from_user.id, callback.bot)
    if joined:
        await callback.answer("🎁 𝗩𝗲𝗿𝗶𝗳𝗶𝗲𝗱! 𝗬𝗼𝘂 𝗻𝗼𝘄 𝗵𝗮𝘃𝗲 𝗳𝘂𝗹𝗹 𝗮𝗰𝗰𝗲𝘀𝘀.", show_alert=True)
        try:
            await callback.message.delete()
        except Exception:
            pass
    else:
        await callback.answer(
            "<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> 𝗬𝗼𝘂 𝗵𝗮𝘃𝗲𝗻'𝘁 𝗷𝗼𝗶𝗻𝗲𝗱 𝘆𝗲𝘁!\n\n𝗣𝗹𝗲𝗮𝘀𝗲 𝗷𝗼𝗶𝗻 𝗯𝗼𝘁𝗵 𝘁𝗵𝗲 𝗰𝗵𝗮𝗻𝗻𝗲𝗹 𝗮𝗻𝗱 𝗴𝗿𝗼𝘂𝗽 𝗳𝗶𝗿𝘀𝘁, 𝘁𝗵𝗲𝗻 𝗰𝗹𝗶𝗰𝗸 𝗩𝗲𝗿𝗶𝗳𝘆.",
            show_alert=True
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DB HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _db_conn():
    return get_db_connection()

def _ensure_user_sync(user_id, username):
    try:
        if not get_user(user_id):
            create_user(user_id, username or "unknown")
    except Exception as e:
        logging.error(f"ensure_user {user_id}: {e}")

async def ensure_user(user_id, username="unknown"):
    await asyncio.to_thread(_ensure_user_sync, user_id, username)

def _status_sync(user_id):
    conn = _db_conn()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    plan, joined_str = "𝗧𝗿𝗶𝗮𝗹", "N/A"
    try:
        cur.execute("SELECT is_premium, premium_expiry, joined_at FROM users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        if row:
            if row['joined_at']:
                joined_str = row['joined_at'].strftime('%Y-%m-%d')
            if row['is_premium'] == 1 and row['premium_expiry'] and datetime.now() < row['premium_expiry']:
                cur.execute("SELECT plan FROM receipts WHERE user_id = %s ORDER BY purchased_on DESC LIMIT 1", (user_id,))
                r = cur.fetchone()
                plan = r['plan'] if r else "𝗣𝗿𝗲𝗺𝗶𝘂𝗺"
    except Exception as e:
        logging.error(f"status {user_id}: {e}")
    finally:
        conn.close()
    bold_map = {
        'a':'𝗮','b':'𝗯','c':'𝗰','d':'𝗱','e':'𝗲','f':'𝗳','g':'𝗴','h':'𝗵','i':'𝗶','j':'𝗷','k':'𝗸','l':'𝗹','m':'𝗺','n':'𝗻','o':'𝗼','p':'𝗽','q':'𝗾','r':'𝗿','s':'𝘀','t':'𝘁','u':'𝘂','v':'𝘃','w':'𝘄','x':'𝗅','y':'𝘆','z':'𝘇',
        'A':'𝗔','B':'𝗕','C':'𝗖','D':'𝗗','E':'𝗘','F':'𝗙','G':'𝗚','H':'𝗛','I':'𝗜','J':'𝗝','K':'𝗞','L':'𝗟','M':'𝗠','N':'𝗡','O':'𝗢','P':'𝗣','Q':'𝗤','R':'𝗥','S':'𝗦','T':'𝗧','U':'𝗨','V':'𝗩','W':'𝗪','X':'𝗫','Y':'𝗬','Z':'𝗭'
    }
    if plan.lower() == "Rohit":
        plan_formatted = "Rohit<tg-emoji emoji-id='5039727497143387500'>👑</tg-emoji>"
    else:
        plan_formatted = "".join(bold_map.get(c, c) for c in plan)
    return plan_formatted, joined_str

async def _get_caption(user) -> str:
    access_str, joined_str = await asyncio.to_thread(_status_sync, user.id)
    ul = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    dl = '<a href="https://t.me/RohitRich">—͟͞͞𒆜𓄂︪︫︠ 𓆩 Ꭱ𝑂𝐻𝐼𝑇 𓆪↬𔘓༌ᤩ༗༗</a>'
    return (
        f"<tg-emoji emoji-id='6237927637906364256'>👤</tg-emoji> 𝗨𝘀𝗲𝗿 ➛ {ul}\n"
        f"<tg-emoji emoji-id='6237822905128851025'>🆔</tg-emoji> 𝗨𝘀𝗲𝗿 𝗜𝗗 ➛ <code>{user.id}</code>\n"
        f"<tg-emoji emoji-id='5039727497143387500'>👑</tg-emoji> 𝗔𝗰𝗰𝗲𝘀𝘀 ➛ <b>{access_str}</b>\n"
        f"<tg-emoji emoji-id='6147637448135414816'>📅</tg-emoji> 𝗝𝗼𝗶𝗻𝗲𝗱 ➛ <b>{joined_str}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<tg-emoji emoji-id='5039653765439816618'>🐈‍⬛</tg-emoji> 𝗗𝗲𝘃 ➛ {dl}"
    )

def _loading_caption(user) -> str:
    ul = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    dl = '<a href="https://t.me/RohitRich">—͟͞͞𒆜𓄂︪︫︠ 𓆩 Ꭱ𝑂𝐻𝐼𝑇 𓆪↬𔘓༌ᤩ༗༗</a>'
    return (
        f"<tg-emoji emoji-id='6237927637906364256'>👤</tg-emoji> 𝗨𝘀𝗲𝗿 ➛ {ul}\n"
        f"<tg-emoji emoji-id='6237822905128851025'>🆔</tg-emoji> 𝗨𝘀𝗲𝗿 𝗜𝗗 ➛ <code>{user.id}</code>\n"
        f"<tg-emoji emoji-id='5039727497143387500'>👑</tg-emoji> 𝗔𝗰𝗰𝗲𝘀𝘀 ➛ <b>Loading…</b>\n"
        f"<tg-emoji emoji-id='6147637448135414816'>📅</tg-emoji> 𝗝𝗼𝗶𝗻𝗲𝗱 ➛ <b>Loading…</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<tg-emoji emoji-id='5039653765439816618'>🐈‍⬛</tg-emoji> 𝗗𝗲𝘃 ➛ {dl}"
    )

def mask_receipt_id(receipt_id):
    parts = receipt_id.split('-')
    if len(parts) == 3:
        m = parts[1]
        if len(m) >= 2:
            return f"{parts[0]}-{m[:2]}XX{m[4:]}-{parts[2]}"
    return receipt_id

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PRE-BUILT KEYBOARDS (built once at startup — zero runtime cost)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_MAIN_KB = {
    "inline_keyboard": [
        [{"text": " 𝗖𝗵𝗲𝗰𝗸𝗲𝗿", "callback_data": "menu_gates", "icon_custom_emoji_id": "5039623284056917259", "style": "primary"},
         {"text": " 𝗕𝘂𝘆 𝗡𝗼𝘄", "callback_data": "menu_pricing", "icon_custom_emoji_id": "5039727497143387500", "style": "primary"}],
        [{"text": " 𝗨𝗽𝗱𝗮𝘁𝗲𝘀", "url": "https://t.me/+9rfg58NMCsM3N2Rl", "icon_custom_emoji_id": "5042274086332400375", "style": "primary"},
         {"text": " 𝗚𝗿𝗼𝘂𝗽", "url": "https://t.me/+hLUNXPhlUpMyMjg1", "icon_custom_emoji_id": "5039653765439816618", "style": "primary"}],
        [{"text": " 𝗦𝘂𝗽𝗽𝗼𝗿𝘁", "url": "https://t.me/RohitRich", "icon_custom_emoji_id": "5040030395416969985", "style": "primary"},
         {"text": " 𝗣𝗿𝗼𝘅𝘆", "callback_data": "menu_proxy", "icon_custom_emoji_id": "5039895103947146186", "style": "primary"}]
    ]
}

def _back(target):
    return {
        "inline_keyboard": [
            [{"text": "𝗕𝗮𝗰𝗸", "callback_data": target, "icon_custom_emoji_id": "5456140674028019486", "style": "danger"}]
        ]
    }

_KB_BACK_MAIN   = _back("back_main")
_KB_BACK_GATES  = _back("menu_gates")
_KB_BACK_MASS   = _back("menu_mass_in_gates")
_KB_BACK_AUTH   = _back("menu_auth")
_KB_BACK_CHARGE = _back("menu_charge")

_KB_PRICING = {
    "inline_keyboard": [
        [{"text": " 𝗣𝗮𝘆 𝗩𝗶𝗮", "callback_data": "menu_payment_methods", "icon_custom_emoji_id": "5039539210072097557", "style": "primary"}],
        _KB_BACK_MAIN["inline_keyboard"][0]
    ]
}

_KB_GATES = {
    "inline_keyboard": [
        [{"text": " 𝗔𝘂𝘁𝗵", "callback_data": "menu_auth", "icon_custom_emoji_id": "5042328396193864923", "style": "primary"},
         {"text": " 𝗖𝗵𝗮𝗿𝗴𝗲", "callback_data": "menu_charge", "icon_custom_emoji_id": "5042050649248760772", "style": "primary"}],
        [{"text": " 𝗠𝗮𝘀𝘀", "callback_data": "menu_mass_in_gates", "icon_custom_emoji_id": "5041975203853239332", "style": "primary"}],
        _KB_BACK_MAIN["inline_keyboard"][0]
    ]
}

_KB_MASS = {
    "inline_keyboard": [
        [{"text": " 𝗦𝗵𝗼𝗽𝗶𝗳𝘆 𝟬-𝟱$", "callback_data": "info_msh_gate", "icon_custom_emoji_id": "5039531487720899631", "style": "primary"},
         {"text": " 𝗦𝘁𝗿𝗶𝗽𝗲 𝟭$", "callback_data": "info_mst_gate", "icon_custom_emoji_id": "5042297717242463211", "style": "primary"}],
        [{"text": " 𝗥𝗮𝘇𝗼𝗿𝗽𝗮𝘆 𝗠𝗮𝘀𝘀", "callback_data": "info_mrz_gate", "icon_custom_emoji_id": "5041902056265221148", "style": "primary"}],
        [{"text": " 𝗦𝘁𝗿𝗶𝗽𝗲 𝗠𝘂𝗹𝘁𝗶 (𝗠𝗮𝘀𝘀)", "callback_data": "info_mstr_gate", "icon_custom_emoji_id": "5042003580702164014", "style": "primary"}],
        _KB_BACK_GATES["inline_keyboard"][0]
    ]
}

_KB_AUTH = {
    "inline_keyboard": [
        [{"text": "𝗦𝘁𝗿𝗶𝗽𝗲", "callback_data": "info_auth_stripe", "icon_custom_emoji_id": "5039980479307055915", "style": "primary"},
         {"text": "𝗕𝗿𝗮𝗶𝗻𝘁𝗿𝗲𝗲", "callback_data": "info_auth_braintree", "icon_custom_emoji_id": "5042101437237036298", "style": "primary"}],
        [{"text": "𝗕𝗿𝗮𝗶𝗻𝘁𝗿𝗲𝗲 𝗩𝗕𝗩", "callback_data": "info_auth_braintree_vbv", "icon_custom_emoji_id": "5039810295522919687", "style": "primary"}],
        _KB_BACK_GATES["inline_keyboard"][0]
    ]
}

_KB_CHARGE = {
    "inline_keyboard": [
        [{"text": " 𝗦𝘁𝗿𝗶𝗽𝗲", "callback_data": "info_charge_stripe", "icon_custom_emoji_id": "5042334757040423886", "style": "primary"},
         {"text": " 𝗦𝘁𝗿𝗶𝗽𝗲 𝗠𝘂𝗹𝘁𝗶", "callback_data": "info_charge_str", "icon_custom_emoji_id": "5039607036195636113", "style": "primary"}],
        [{"text": " 𝗣𝗮𝘆𝗽𝗮𝗹", "callback_data": "info_charge_paypal", "icon_custom_emoji_id": "5042302287087666158", "style": "primary"}],
        [{"text": " 𝗦𝗵𝗼𝗽𝗶𝗳𝘆", "callback_data": "info_charge_shopify", "icon_custom_emoji_id": "5039544445637231745", "style": "primary"},
         {"text": " 𝗥𝗮𝘇𝗼𝗿𝗽𝗮𝘆", "callback_data": "info_charge_razorpay", "icon_custom_emoji_id": "5039670898064360513", "style": "primary"}],
        _KB_BACK_GATES["inline_keyboard"][0]
    ]
}

_SEP = "━━━━━━━━━━━━━━━━"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STATIC MENU LOOKUP — O(1) dict, text+keyboard pre-built
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_PAYMENT_SELECT_TEXT = "<b><tg-emoji emoji-id='5039623284056917259'>💳</tg-emoji> 𝗦𝗲𝗹𝗲𝗰𝘁 𝗬𝗼𝘂𝗿 𝗣𝗹𝗮𝗻\n\nChoose a plan to proceed with\nsecure crypto payment</b>"

STATIC_MENU_MAP: dict = {
    "menu_pricing": (PRICING_TEXT, _KB_PRICING),
    "menu_gates": (
        "<b>┌── <tg-emoji emoji-id='5424818078833715060'>📢</tg-emoji> 𝗚𝗔𝗧𝗘𝗦 𝗦𝗧𝗔𝗧𝗨𝗦 ──┐</b>\n"
        "<b>├ 𝗔𝘂𝘁𝗵 𝗚𝗮𝘁𝗲𝘀 ➛</b> <b>3</b>\n"
        "<b>├ 𝗠𝗮𝘀𝘀 𝗚𝗮𝘁𝗲𝘀 ➛</b> <b>4</b>\n"
        "<b>├ 𝗖𝗵𝗮𝗿𝗴𝗲 𝗚𝗮𝘁𝗲𝘀 ➛</b> <b>5</b>\n"
        "<b>└──────────────────┘</b>\n"
        "<b>𝗦𝗲𝗹𝗲𝗰𝘁 𝗮 𝗚𝗮𝘁𝗲 𝗖𝗮𝘁𝗲𝗴𝗼𝗿𝘆 𝗯𝗲𝗹𝗼𝘄:</b>",
        _KB_GATES
    ),
    "menu_mass_in_gates": ("<b><tg-emoji emoji-id='5039727497143387500'>👑</tg-emoji> 𝗦𝗲𝗹𝗲𝗰𝘁 𝗮 𝗠𝗮𝘀𝘀 𝗚𝗮𝘁𝗲</b>", _KB_MASS),
    "menu_auth":          ("<b><tg-emoji emoji-id='5039623284056917259'>💳</tg-emoji> 𝗦𝗲𝗹𝗲𝗰𝘁 𝗔𝘂𝘁𝗵 𝗠𝗲𝘁𝗵𝗼𝗱</b>", _KB_AUTH),
    "menu_charge":        ("<b><tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> 𝗦𝗲𝗹𝗲𝗰𝘁 𝗖𝗵𝗮𝗿𝗴𝗲 𝗠𝗲𝘁𝗵𝗼𝗱</b>", _KB_CHARGE),
    "menu_proxy": (
        "<b>┌── <tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> 𝗣𝗥𝗢𝘅𝗬 𝗠𝗔𝗡𝗔𝗚𝗘𝗠𝗘𝗡𝗧 ──┐</b>\n\n"
        "<b><tg-emoji emoji-id='5271604874419647061'>🔧</tg-emoji> 𝗦𝗲𝘁 𝗣𝗿𝗼𝘅𝘆</b>\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/proxy</code>\n"
        "<b>└ 𝗧𝘆𝗽𝗲 ➛</b> 𝗙𝗿𝗲𝗲\n\n"
        "<b><tg-emoji emoji-id='5388632425314140043'>🔍</tg-emoji> 𝗖𝗵𝗲𝗰𝗸 𝗣𝗿𝗼𝘅𝘆</b>\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/checkproxy</code>\n"
        "<b>└ 𝗧𝘆𝗽𝗲 ➛</b> 𝗙𝗿𝗲𝗲\n\n"
        "<b><tg-emoji emoji-id='5042329873662609701'>🗑️</tg-emoji> 𝗖𝗹𝗲𝗮𝗿 𝗣𝗿𝗼𝘅𝘆</b>\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/clearproxy</code>\n"
        "<b>└ 𝗧𝘆𝗽𝗲 ➛</b> 𝗙𝗿𝗲𝗲\n"
        "<b>└──────────────────────┘</b>",
        _KB_BACK_MAIN
    ),
    "menu_payment_methods": (_PAYMENT_SELECT_TEXT, None),  # kb injected at runtime
    "info_msh_gate": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧𝗘 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Shopify 0-5$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/msh</code>\n"
        "<b>├ 𝗟𝗶𝗺𝗶𝘁 ➛</b> 10,000\n"
        "<b>├ 𝗧𝘆𝗽𝗲 ➛</b> Mass Checker\n"
        "<b>└ 𝗦𝘁𝗼𝗽 ➛</b> <tg-emoji emoji-id='5456140674028019486'>🛑</tg-emoji> Button\n"
        "<b>└────────────────┘</b>\n"
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧𝗘 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Shopify Mass\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/sh</code>\n"
        "<b>└ 𝗟𝗶𝗺𝗶𝘁 ➛</b> 50 Cards\n"
        "<b>└────────────────┘</b>", _KB_BACK_MASS),
    "info_mst_gate": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧𝗘 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Stripe 1$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/mst</code>\n"
        "<b>├ 𝗟𝗶𝗺𝗶𝘁 ➛</b> 2,000\n"
        "<b>├ 𝗧𝘆𝗽𝗲 ➛</b> Mass Checker\n"
        "<b>└ 𝗦𝘁𝗼𝗽 ➛</b> <tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> Button\n"
        "<b>└────────────────┘</b>", _KB_BACK_MASS),
    "info_mstr_gate": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧Ｅ 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Stripe Multi Mass\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/mstr</code>\n"
        "<b>├ 𝗟𝗶𝗺𝗶𝘁 ➛</b> 2,000\n"
        "<b>├ 𝗧𝘆𝗽𝗲 ➛</b> Mass Checker\n"
        "<b>└ 𝗦𝘁𝗼𝗽 ➛</b> <tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> Button\n"
        "<b>└────────────────┘</b>", _KB_BACK_MASS),
    "info_stco_gate": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧𝗘 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Stripe Hitter\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/stco</code>\n"
        "<b>├ 𝗧𝘆𝗽𝗲 ➛</b> Auto Hitter\n"
        "<b>└ 𝗦𝘁𝗼𝗽 ➛</b> <tg-emoji emoji-id='5456140674028019486'>🛑</tg-emoji> Button\n"
        "<b>└────────────────┘</b>", _KB_BACK_MASS),
    "info_mrz_gate": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧𝗘 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Razorpay Mass\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/mrz</code>\n"
        "<b>├ 𝗟𝗶𝗺𝗶𝘁 ➛</b> 5,000\n"
        "<b>├ 𝗧𝘆𝗽𝗲 ➛</b> Mass Checker\n"
        "<b>└ 𝗦𝘁𝗼𝗽 ➛</b> <tg-emoji emoji-id='5456140674028019486'>🛑</tg-emoji> Button\n"
        "<b>└────────────────┘</b>\n"
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧𝗘 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Razorpay\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/rz</code>\n"
        "<b>└ 𝗟𝗶𝗺𝗶𝘁 ➛</b> 50 Cards\n"
        "<b>└────────────────┘</b>", _KB_BACK_MASS),
    "info_auth_stripe": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧𝗘 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Stripe 0$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/chk</code>\n"
        "<b>├ 𝗦𝗶𝘁𝗲𝘀 𝗟𝗼𝗮𝗱𝗲𝗱 ➛</b> 16\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>", _KB_BACK_AUTH),
    "info_auth_braintree": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧𝗘 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Braintree 0$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/b3</code>\n"
        "<b>├ 𝗦𝗶𝘁𝗲𝘀 𝗟𝗼𝗮𝗱𝗲𝗱 ➛</b> 2\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>", _KB_BACK_AUTH),
    "info_auth_braintree_vbv": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧𝗘 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Braintree VBV\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/vbv</code>\n"
        "<b>├ 𝗦𝗶𝘁𝗲𝘀 𝗟𝗼𝗮𝗱𝗲𝗱 ➛</b> 1\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>", _KB_BACK_AUTH),
    "info_charge_stripe": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧Ｅ 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Stripe 1$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/st</code>\n"
        "<b>├ 𝗦𝗶𝘁𝗲𝘀 𝗟𝗼𝗮𝗱𝗲𝗱 ➛</b> 4\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>", _KB_BACK_CHARGE),
    "info_charge_str": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧Ｅ 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Stripe Multi\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/str</code>\n"
        "<b>├ 𝗦𝗶??𝗲𝘀 𝗟𝗼𝗮??𝗲?? ➛</b> 6\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>", _KB_BACK_CHARGE),
    "info_charge_paypal": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧Ｅ 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> PayPal 0.10$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/pp</code>\n"
        "<b>├ 𝗦𝗶𝘁𝗲𝘀 𝗟𝗼𝗮𝗱𝗲𝗱 ➛</b> 7\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>", _KB_BACK_CHARGE),
    "info_charge_shopify": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧Ｅ 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Shopify 5$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/hc</code>\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>\n"
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧Ｅ 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Shopify 1$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/sp</code>\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>", _KB_BACK_CHARGE),
    "info_charge_payfast": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧Ｅ 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> PayFast 0.30$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/pf</code>\n"
        "<b>├ 𝗦𝗶𝘁𝗲𝘀 𝗟𝗼𝗮𝗱𝗲𝗱 ➛</b> 1\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>", _KB_BACK_CHARGE),
    "info_charge_fatzebra": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧Ｅ 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> FatZebra 4$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/ft</code>\n"
        "<b>├ 𝗦𝗶𝘁𝗲𝘀 𝗟𝗼𝗮𝗱𝗲𝗱 ➛</b> 1\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>", _KB_BACK_CHARGE),
    "info_charge_nmi": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧Ｅ 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> NMI 1$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/nmi</code>\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>\n"
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧Ｅ 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> NMI2 1$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/nmi2</code>\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>", _KB_BACK_CHARGE),
    "info_charge_bluepay": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧Ｅ 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> BluePay 20$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/bl</code>\n"
        "<b>├ 𝗦𝗶𝘁𝗲𝘀 𝗟𝗼𝗮𝗱𝗲𝗱 ➛</b> 1\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>", _KB_BACK_CHARGE),
    "info_charge_authnet": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧Ｅ 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Authorize.net 1$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/at</code>\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>", _KB_BACK_CHARGE),
    "info_charge_payway": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧Ｅ 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> PayWay 1$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/pw</code>\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>", _KB_BACK_CHARGE),
    "info_charge_razorpay": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧Ｅ 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> Razorpay 1₹\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/rz</code>\n"
        "<b>├ 𝗦𝗶𝘁𝗲𝘀 𝗟𝗼𝗮𝗱𝗲𝗱 ➛</b> 5\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>", _KB_BACK_CHARGE),
    "info_charge_payu": (
        "<b>┌── <tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> 𝗚𝗔𝗧Ｅ 𝗜𝗡𝗙𝗢 ──┐</b>\n"
        "<b>├ 𝗚𝗮𝘁𝗲 ➛</b> PayU 1$\n"
        "<b>├ 𝗖𝗼𝗺𝗺𝗮𝗻𝗱 ➛</b> <code>/pyu</code>\n"
        "<b>└ 𝗚𝗮𝘁𝗲 𝗛𝗲𝗮𝗹𝘁𝗵 ➛</b> 100%\n"
        "<b>└────────────────┘</b>", _KB_BACK_CHARGE),
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FAST INLINE EDIT HELPER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _edit(msg: types.Message, text: str, kb: InlineKeyboardMarkup):
    try:
        if msg.caption is not None:
            await msg.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
        else:
            await msg.edit_text(text=text, reply_markup=kb, parse_mode="HTML")
    except TypeError:
        pass
    except Exception as e:
        if "Message is not modified" not in str(e):
            logging.warning(f"_edit: {e}")

async def _safe_answer(cb: types.CallbackQuery, text: str = "", **kw):
    try:
        await cb.answer(text, **kw)
    except Exception:
        pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /start
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@router.message(Command("start"))
async def start(message: types.Message):
    user = message.from_user
    asyncio.create_task(ensure_user(user.id, user.username))

    if message.text and len(message.text.split()) > 1 and message.text.split()[1] == "buy":
        await buy_command(message)
        return

    quick = _loading_caption(user)
    caption_task = asyncio.create_task(_get_caption(user))

    sent = await message.reply(text=quick, reply_markup=_MAIN_KB)

    try:
        full = await caption_task
        await sent.edit_text(text=full, reply_markup=_MAIN_KB)
    except Exception:
        pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DOT COMMANDS (.chk, .st, etc.)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DOT_COMMAND_MAP = {
    "chk": chk_command, "st": st_command, "sh": sh_command,
    "sp": sp_command, "hc": hc_command,
    "vbv": vbv_command,
    "pp": pp_command,
    "rz": rz_command, "b3": b3_command,

    "mst": mst_command, "mstr": mstr_command, "str": str_command, "bin": binn_command, "binn": binn_command,
    "sub": sub_command, "rc": rc_command, "suball": suball_command,
    "g_code": g_code_command, "claim": claim_command, "info": info_command,
    "rsub": rsub_command, "buy": buy_command, "adcr": adcr_command,
    "gen": gen_command,
    "on": on_command, "off": off_command, "stats": stats_command,
    "proxy": proxy_command, "checkproxy": checkproxy_command,
    "clearproxy": clearproxy_command, "sitechk": sitechk_command,
    "addsite": addsite_command, "siteall": siteall_command,
    "removeall": removeall_command, "dedupe": dedupe_command,
    "proxyinfo": proxyinfo_command, "resetproxy": resetproxy_command,
    "remsite": remsite_command,
    "cmds": cmds_command, "fb": feedback_cmd, "broad": broad_command,
    "ban": ban_command, "unban": unban_command, "vps": vps_command,
}

@router.message(Command("eid"))
async def eid_command(message: types.Message):
    if not message.entities:
        return await message.reply("𝗣𝗹𝗲𝗮𝘀𝗲 𝘀𝗲𝗻𝗱 𝗮 𝗽𝗿𝗲𝗺𝗶𝘂𝗺 𝗲𝗺𝗼𝗷𝗶 𝘄𝗶𝘁𝗵 𝘁𝗵𝗲 𝗰𝗼𝗺𝗺𝗮𝗻𝗱.\n𝗘𝘅𝗮𝗺𝗽𝗹𝗲: /eid <tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji>")
    for entity in message.entities:
        if entity.type == "custom_emoji":
            return await message.reply(f"𝗖𝘂𝘀𝘁𝗼𝗺 𝗘𝗺𝗼𝗷𝗶 𝗜𝗗: <code>{entity.custom_emoji_id}</code>\n\n<i>Give this ID to me (Antigravity) so I can add it to your buttons!</i>", parse_mode="HTML")
    await message.reply("<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> 𝗡𝗼 𝗽𝗿𝗲𝗺𝗶𝘂𝗺 𝗰𝘂𝘀𝘁𝗼𝗺 𝗲𝗺𝗼𝗷𝗶 𝗳𝗼𝘂𝗻𝗱 𝗶𝗻 𝘆𝗼𝘂𝗿 𝗺𝗲𝘀𝘀𝗮𝗴𝗲!")

@router.message(F.text.regexp(r'^\.\w+'))
async def dot_command_handler(message: types.Message):
    cmd = message.text.strip().split()[0][1:].lower()
    h = DOT_COMMAND_MAP.get(cmd)
    if h:
        await h(message)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CALLBACK HANDLER — MAXIMUM SPEED
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_MASS_PREFIXES = ("mshs_", "mshr_", "msts_", "mstr_", "mstsr_", "mstss_", "fb_", "cmds_")

@router.callback_query()
async def button_handler(callback: types.CallbackQuery):
    data = callback.data or ""

    # These are owned by other routers — drop immediately, no answer needed
    if data.startswith(_MASS_PREFIXES):
        return

    user_id = callback.from_user.id
    msg = callback.message
    if not msg:
        return

    # If in group, check if the clicker is the person mentioned in the message/start command
    if msg.chat.type in ("group", "supergroup"):
        owner_id = None
        # Try finding a text_link entity pointing to tg://user?id=
        entities = msg.entities or msg.caption_entities or []
        for entity in entities:
            if entity.type == "text_link" and entity.url and entity.url.startswith("tg://user?id="):
                try:
                    owner_id = int(entity.url.split("id=")[1])
                    break
                except (ValueError, IndexError):
                    pass
            elif entity.type == "text_mention" and entity.user:
                owner_id = entity.user.id
                break
        
        # If not found via entities, fall back to regex
        if owner_id is None:
            msg_text = msg.text or msg.caption or ""
            owner_id_match = re.search(r'(?:𝗨𝘀𝗲𝗿 𝗜𝗗|User ID)\s*➛\s*(\d+)', msg_text)
            if owner_id_match:
                owner_id = int(owner_id_match.group(1))

        if owner_id is not None and user_id != owner_id:
            await _safe_answer(callback, "<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> 𝗬𝗼𝘂 𝗰𝗮𝗻𝗻𝗼𝘁 𝗶𝗻𝘁𝗲𝗿𝗮𝗰𝘁 𝘄𝗶𝘁𝗵 𝘁𝗵𝗶𝘀 𝗺𝗲𝗻𝘂.", show_alert=True)
            return

    # ── STATIC MENU (most common path) ─────────────────────────────────────
    static = STATIC_MENU_MAP.get(data)
    if static is not None:
        text, kb = static
        if kb is None:  # menu_payment_methods — kb computed at runtime
            kb = pay_sys.get_plan_selection_keyboard()
        asyncio.create_task(_safe_answer(callback))   # answer instantly — no waiting
        asyncio.create_task(_edit(msg, text, kb))     # edit in background
        return

    # ── BACK TO MAIN ───────────────────────────────────────────────────────
    if data == "back_main":
        user = callback.from_user
        quick = _loading_caption(user)
        caption_task = asyncio.create_task(_get_caption(user))
        await _safe_answer(callback)   # answer instantly — no waiting
        try:
            if msg.caption is not None:
                await msg.edit_caption(caption=quick, reply_markup=_MAIN_KB, parse_mode="HTML")
            else:
                await msg.edit_text(text=quick, reply_markup=_MAIN_KB, parse_mode="HTML")
        except TypeError:
            pass
        except Exception as e:
            if "Message is not modified" not in str(e):
                logging.warning(f"back_main load: {e}")
        try:
            full = await caption_task
            if msg.caption is not None:
                await msg.edit_caption(caption=full, reply_markup=_MAIN_KB, parse_mode="HTML")
            else:
                await msg.edit_text(text=full, reply_markup=_MAIN_KB, parse_mode="HTML")
        except TypeError:
            pass
        except Exception:
            pass
        return

    # ── BUY PLANS (inline group context) ───────────────────────────────────
    if data == "show_buy_plans":
        buy_url_kb = {
            "inline_keyboard": [[
                {"text": " 𝗧𝗮𝗽 𝗛𝗲𝗿𝗲 𝘁𝗼 𝗕𝘂𝘆", "url": f"{BOT_LINK}?start=buy", "icon_custom_emoji_id": "6242135305697106689"}
            ]]
        }
        await _safe_answer(callback)
        try:
            await msg.edit_reply_markup(reply_markup=buy_url_kb)
        except Exception:
            pass
        return

    # ── PAYMENT PLAN SELECTION ──────────────────────────────────────────────
    if data.startswith("pay_plan_"):
        plan = data[9:]
        if plan not in pay_sys.PLANS:
            await _safe_answer(callback)
            await msg.answer("Invalid plan!")
            return
        pi = pay_sys.PLANS[plan]
        pay_sys.set_user_session(user_id, plan)
        text = (
            f"<b>{pi['display']} 𝗣𝗹𝗮𝗻</b>\n"
            f"<b>𝗣𝗿𝗶𝗰𝗲 ➛</b> ${pi['price']}\n"
            f"<b>𝗗𝘂𝗿𝗮𝘁𝗶𝗼𝗻 ➛</b> {pi['days']} Days\n"
            f"<b>𝗦𝗲𝗹𝗲𝗰𝘁 𝗣𝗮𝘆𝗺𝗲𝗻𝘁 𝗠𝗲𝘁𝗵𝗼𝗱:</b>"
        )
        await _safe_answer(callback)
        await _edit(msg, text, pay_sys.get_network_selection_keyboard(user_id))
        return

    # ── BACK TO PLAN LIST ───────────────────────────────────────────────────
    if data.startswith("pay_back_plans_"):
        try:
            owner_id = int(data[15:])
        except ValueError:
            await _safe_answer(callback, "<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> Error", show_alert=True)
            return
        if user_id != owner_id:
            await _safe_answer(callback, "<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> No permission", show_alert=True)
            return
        await _safe_answer(callback)
        await _edit(msg, _PAYMENT_SELECT_TEXT, pay_sys.get_plan_selection_keyboard())
        return

    # ── DIRECT PAYMENT INITIATION ───────────────────────────────────────────
    if data.startswith("pay_direct_"):
        net_key = data[11:]
        session = pay_sys.get_user_session(user_id)
        if not session or not session.get("plan"):
            await _safe_answer(callback)
            await msg.answer("Session expired!")
            return
        plan = session["plan"]
        net_info = pay_sys.DIRECT_NETWORKS.get(net_key)
        if not net_info:
            await _safe_answer(callback)
            await msg.answer("Invalid network!")
            return
        pay_sys.cancel_user_active_payment(user_id)
        payment_data = await asyncio.to_thread(
            pay_sys.create_payment, user_id, plan, net_info["currency"], net_info["network"]
        )
        if not payment_data:
            await asyncio.gather(
                _safe_answer(callback),
                _edit(msg, "<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> <b>Payment Failed</b>\n\nPlease try again later.",
                      _back("menu_pricing")),
            )
            return
        track_id = payment_data["track_id"]
        pay_sys.register_payment(track_id, user_id, plan)
        caption = pay_sys.format_payment_caption(payment_data, plan)
        kb = pay_sys.get_paid_button_keyboard(track_id, user_id)
        await _safe_answer(callback)
        try:
            sent_msg = await msg.answer(text=caption, reply_markup=kb, disable_web_page_preview=True)
        except Exception as e:
            logging.error(f"pay_direct send: {e}")
            return
        try:
            await msg.delete()
        except Exception:
            pass
        if sent_msg:
            pay_sys.active_payments[track_id].update({
                "chat_id": sent_msg.chat.id,
                "message_id": sent_msg.message_id,
                "original_text": caption,
            })
        return

    # ── PAYMENT CHECK (I Paid button) ───────────────────────────────────────
    if data.startswith("pay_check_"):
        track_id = data[10:]
        payment = pay_sys.active_payments.get(track_id)
        if not payment or payment.get("user_id") != user_id:
            await _safe_answer(callback, "<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> No permission", show_alert=True)
            return
        bot_i = pay_sys.get_bot()
        if not bot_i:
            await _safe_answer(callback, "<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> Bot error", show_alert=True)
            return
        try:
            status = await asyncio.to_thread(pay_sys.check_payment_status, track_id)
            logging.info(f"pay_check {track_id}: {status}")
            if status and status.lower() == "paid":
                await asyncio.to_thread(pay_sys.activate_plan, user_id, payment["plan"])
                receipt = pay_sys.get_receipt_for_user(user_id, payment["plan"])
                pi = pay_sys.PLANS.get(payment["plan"], {})
                dn = callback.from_user.first_name or callback.from_user.username or "User"
                ul = f'<a href="tg://user?id={user_id}">{dn}</a>'
                mid = mask_receipt_id(receipt['receipt_id']) if receipt else "N/A"
                log_kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="𝗕𝘂𝘆 𝗡𝗼𝘄", url=f"{BOT_LINK}?start=buy", icon_custom_emoji_id="5447453226498552490")
                ]])
                try:
                    await bot_i.send_message(
                        chat_id=LOG_CHANNEL_ID, parse_mode="HTML", reply_markup=log_kb,
                        text=(f"<b>🛒 NEW PLAN PURCHASED</b>\n<b>User ➛</b> {ul}\n"
                              f"<b>Access ➛</b> <b>{pi.get('display','')}</b>\n"
                              f"<b>Amount ➛</b> <b>{pi.get('price',0)} USD</b>\n"
                              f"<b>Receipt ID ➛</b> <code>{mid}</code>")
                    )
                except Exception as le:
                    logging.error(f"log channel: {le}")
                success = (
                    f"<tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji> <b>𝗧𝗿𝗮𝗻𝘀𝗮𝗰𝘁𝗶𝗼𝗻 𝗦𝘂𝗰𝗰𝗲𝘀𝘀!</b>\n\n"
                    f" <b><tg-emoji emoji-id='6242135305697106689'>🎁</tg-emoji> 𝗣𝗹𝗮𝗻 ➛</b> {pi.get('display','')}\n"
                    f" <b>𝗗𝘂𝗿𝗮𝘁𝗶𝗼𝗻 ➛</b> {pi.get('days',0)} Days\n"
                    f" <b>𝗬𝗼𝘂𝗿 𝗣𝗹𝗮𝗻 𝗵𝗮𝘀 𝗯𝗲𝗲𝗻 𝗮𝗰𝘁𝗶𝘃𝗮𝘁𝗲𝗱!</b>"
                )
                dm_kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="𝗦𝘂𝗽𝗽𝗼𝗿𝘁", url="https://t.me/RealamonCC", icon_custom_emoji_id="6237927637906364256", style="primary")
                ]])
                if receipt:
                    dm = (f"𝐂𝐨𝐧𝐠𝐫𝐚𝐭𝐮𝐥𝐚𝐭𝐢𝐨𝐧𝐬! 🎉 𝐘𝐨𝐮𝐫 𝐚𝐜𝐜𝐞𝐬𝐬 𝐡𝐚𝐬 𝐛𝐞𝐞𝐧 𝐚𝐜𝐭𝐢𝐯𝐚𝐭𝐞𝐝.\n"
                          f"<tg-emoji emoji-id='6237927637906364256'>👤</tg-emoji> 𝗨𝘀𝗲𝗿 ➛ {ul}\n<tg-emoji emoji-id='5039727497143387500'>👑</tg-emoji> 𝗔𝗰𝗰𝗲𝘀𝘀 ➛ <b>{receipt['plan_name']}</b>\n"
                          f"𝗗𝘂𝗿𝗮𝘁𝗶𝗼𝗻 ➛ {receipt['days']} Days\n"
                          f"<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> 𝗥𝗲𝗰𝗲𝗶𝗽𝘁 𝗜𝗗 ➛ <code>{receipt['receipt_id']}</code>\n"
                          f"𝗣𝗹𝗲𝗮𝘀𝗲 𝘀𝗮𝘃𝗲 𝘁𝗵𝗶𝘀 𝗿𝗲𝗰𝗲𝗶𝗽𝘁 𝗜𝗗.")
                else:
                    dm = (f"𝐂𝐨𝐧𝐠𝐫𝐚𝐭𝐮𝐥𝐚𝐭𝐢𝐨𝐧𝐬! 🎉 𝐘𝐨𝐮𝐫 𝐚𝐜𝐜𝐞𝐬𝐬 𝐡𝐚𝐬 𝐛𝐞𝐞𝐧 𝐚𝐜𝐭𝐢𝐯𝐚𝐭𝐞𝐝.\n"
                          f"<tg-emoji emoji-id='5039727497143387500'>👑</tg-emoji> 𝗔𝗰𝗰𝗲𝘀𝘀 ➛ <b>{pi.get('display','')}</b>\n"
                          f"𝗗𝘂𝗿𝗮𝘁𝗶𝗼𝗻 ➛ {pi.get('days',0)} Days\n"
                          f"𝗬𝗼𝘂𝗿 𝗽𝗹𝗮𝗻 𝗵𝗮𝘀 𝗯𝗲𝗲𝗻 𝗮𝗰𝘁𝗶𝘃𝗮𝘁𝗲𝗱!")
                await asyncio.gather(
                    _safe_answer(callback, "<tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji> Payment Confirmed! Plan activated.", show_alert=True),
                    bot_i.edit_message_text(chat_id=payment["chat_id"],
                                            message_id=payment["message_id"], text=success),
                    bot_i.send_message(chat_id=user_id, text=dm, parse_mode="HTML", reply_markup=dm_kb),
                )
                pay_sys._cleanup_payment(track_id, user_id)

            elif status and status.lower() == "expired":
                await asyncio.gather(
                    _safe_answer(callback, "⏰ Payment Expired!", show_alert=True),
                    bot_i.edit_message_text(
                        chat_id=payment["chat_id"], message_id=payment["message_id"],
                        text="<b>Payment Expired</b>\n\nThe payment window has closed.\nPlease start a new payment."
                    ),
                )
                pay_sys._cleanup_payment(track_id, user_id)

            else:
                await _safe_answer(callback, "<tg-emoji emoji-id='5039579582764680065'>⏳</tg-emoji> Payment not detected yet.\nEnsure exact amount is sent.", show_alert=True)
                cur_text = payment.get("original_text", "")
                if "Payment not detected yet" not in cur_text:
                    pending = (f"{cur_text}\n\n<tg-emoji emoji-id='5039579582764680065'>⏳</tg-emoji> <b>Payment not detected yet.</b>\n"
                               f"<i>Ensure exact amount is sent. Click 'Paid' again to recheck.</i>")
                    try:
                        await bot_i.edit_message_text(
                            chat_id=payment["chat_id"], message_id=payment["message_id"],
                            text=pending, reply_markup=pay_sys.get_paid_button_keyboard(track_id, user_id)
                        )
                        payment["original_text"] = pending
                    except Exception as e:
                        if "not modified" not in str(e):
                            logging.error(f"pending edit: {e}")
        except Exception as e:
            logging.error(f"pay_check error: {e}")
            await _safe_answer(callback, "<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> Network error. Try again.", show_alert=True)
        return

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EXPLICIT CALLBACK REGISTRATIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
dp.callback_query.register(msh_stop_handler, MshStopCallback.filter())
dp.callback_query.register(msh_result_handler, MshResultCallback.filter())
dp.callback_query.register(mst_stop_handler, MstStopCallback.filter())
dp.callback_query.register(mst_result_handler, MstResultCallback.filter())
dp.callback_query.register(mstr_stop_handler, MstrStopCallback.filter())
dp.callback_query.register(mstr_result_handler, MstrResultCallback.filter())
dp.callback_query.register(mrz_stop_handler, MrzStopCallback.filter())
dp.callback_query.register(mrz_result_handler, MrzResultCallback.filter())
dp.callback_query.register(sh_callback_handler, F.data.startswith("sh_"))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMMAND REGISTRATIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
for _cmd, _fn in [
    ("pp", pp_command),
    ("b3", b3_command), ("chk", chk_command), ("sh", sh_command), ("st", st_command),
    ("hc", hc_command), ("sp", sp_command), ("vbv", vbv_command),
    
    ("rz", rz_command), ("sub", sub_command), ("rc", rc_command),
    ("suball", suball_command), ("g_code", g_code_command), ("claim", claim_command),
    ("info", info_command), ("rsub", rsub_command), ("buy", buy_command),
    ("adcr", adcr_command), ("on", on_command), ("off", off_command),
    ("mst", mst_command), ("mstr", mstr_command), ("str", str_command), ("sitechk", sitechk_command), ("addsite", addsite_command),
    ("siteall", siteall_command), ("removeall", removeall_command), ("dedupe", dedupe_command),
    ("proxyinfo", proxyinfo_command), ("resetproxy", resetproxy_command),
    ("stats", stats_command), ("proxy", proxy_command), ("checkproxy", checkproxy_command),
    ("clearproxy", clearproxy_command), ("bin", binn_command), ("binn", binn_command),
    ("Rohit", Rohit_admin_command), ("eren", Rohit_admin_command), ("remsite", remsite_command),
    ("gen", gen_command)
]:
    dp.message.register(_fn, Command(_cmd))

setup_feedback_handler(dp)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# POLLING STARTUP & WEB SERVER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_dummy_server():
    try:
        app = web.Application()
        app.router.add_get('/', handle_ping)
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.environ.get("PORT", 8080))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logging.info(f"Dummy web server started on port {port} to keep Render alive")
    except OSError as e:
        logging.warning(f"Could not bind to dummy web server port (already in use?): {e}. Continuing bot polling anyway.")
    except Exception as e:
        logging.warning(f"Failed to start dummy web server: {e}. Continuing bot polling anyway.")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    pay_sys.set_bot(bot)
    
    # Start the dummy web server so Render detects an open port
    await start_dummy_server()

    logging.info("Bot starting via Long Polling…")
    # Wrap polling in an auto-restart loop
    while True:
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        except Exception as e:
            logging.error(f"Polling error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped.")
