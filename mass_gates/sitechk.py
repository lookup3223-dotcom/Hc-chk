import asyncio
import random
import aiohttp
import os
import time
import re
import logging
import io

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AIogram Imports
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from aiogram import types, F, Router, Bot
from aiogram.filters import Command
from aiogram.types import FSInputFile
from database import get_db_connection

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ADD YOUR TELEGRAM ID HERE
ADMIN_IDS = [8662263918] 

# Test card to verify site functionality
TEST_CARD = "4000223372377978|05|29|651"

# API Configuration - SAME AS msh.py
API_URLS = [
    "https://web-production-0919d.up.railway.app/shopify"
]
API_TIMEOUT = 90

# Proxy List - Rotated randomly for each check
PROXY_LIST = []

# Bad proxies that failed - removed from rotation
BAD_PROXIES = set()

# DEAD ERRORS LIST (Including all step failures)
DEAD_ERRORS = [
    # Original errors
    'site error! status: 404', 'site error! status: 500', 'site error! status: 402', 
    'site error! status: 502', 'site error! 503', 'site error! status: 503',
    'site not supported for now!', 'site not supported', 'connection error', 'connection error!',
    'error processing card', 'failed to get token', 'failed to get checkout', 
    'failed to add to cart', 'site overloaded', 'site rate limited',
    'failed to get session token', 'unable to get payment token', 'no valid products', 
    'site error! status: 403', 'payment method is not shopify!', 'not shopify!', 
    'site error! status: 401', 'site requires login!',
    'site error! status: 429', 'cart failed with status 429', 'returned status 429',
    'too many requests', 'http 429', '429',
    'validation_custom', 'payments_payment_flexibility_terms_id_mismatch',
    'timeout', 'http error', 'json', 'proxy', 'curl error', 'could not resolve',
    'connect tunnel failed', 'max retries', 'GENERIC_ERROR',
    'invalid json in submit response', 'invalid json response', 'unknown result', 'payments_credit_card_generic',
    'payments_positive_amount_expected', 'inventoryreservationfailure',
    
    # Step failures (Site Broken/Dead)
    'step 1 failed', 'step 0 failed', 'step 2 failed', 'step 3 failed', 'step 4 failed',
    'step 5 failed', 'step 6 failed', 'step 7 failed', 'step 9 failed', 'step 10 failed',
    'missing stableid', 'missing buildid', 'missing sourcetoken',
    'could not extract private_access_token',
    'could not find actions js url',
    'missing proposal', 'missing submit id',
    'retryable: inventory reservation failure',
    'exceeded 30 poll attempts',
    'could not extract queuetoken',
    'could not extract identification signature',
    'could not extract session id',
    'could not extract delivery handle',
    'could not extract signedhandles',
    'could not extract shipping amount',
    'could not extract total amount',
    'could not extract receiptid',
    'could not extract sessiontoken',
    'errstoreincompatible', 'errmissingreceiptid', 'fetch products',
    'payments_credit_card_brand_not_supported', 'delivery_delivery_line_detail_changed',
    'delivery_no_delivery_strategy_available_for_mercha', 'delivery_address'
]

# List of valid gateway responses (Site is Alive)
SUCCESS_RESPONSES = [
    'CARD_DECLINED', 'INVALID_CVC', 'INCORRECT_CVV', 'INSUFFICIENT_FUNDS', 
    'GENERIC_DECLINE', 'DO NOT HONOR', 'UNKNOWN_ERROR', 'Processing Error', 'EXPIRED_CARD',
    'PICK_UP_CARD', 'DECISION_RULE_BLOCK', 'FRAUD_SUSPECTED', '3DS_REQUIRED', 'AMOUNT_TOO_SMALL',
    'INVALID_PURCHASE_TYPE', 'INVALID_PAYMENT_METHOD', 'ORDER_PAID', 'INCORRECT_NUMBER',
    'OTP_REQUIRED', 'ORDER_PLACED', 'insufficient_funds', 'invalid_cvc'
]

# Router for this module
router = Router()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELPER FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_user_proxies(user_id):
    """Load proxies for the given user from the database."""
    global PROXY_LIST, BAD_PROXIES
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT proxy FROM proxies WHERE user_id = %s", (user_id,))
        PROXY_LIST = [str(row[0]).strip() for row in cursor.fetchall() if row[0] and str(row[0]).strip()]
        conn.close()
    except Exception as e:
        logging.error(f"Error loading proxies from db: {e}")
        PROXY_LIST = []
    BAD_PROXIES.clear()

def is_admin(user_id):
    """Check if user is admin"""
    return user_id in ADMIN_IDS

SITES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sites.txt")

def read_sites():
    """Read sites from sites.txt file - returns list without duplicates"""
    if not os.path.exists(SITES_FILE):
        return []
    with open(SITES_FILE, "r", encoding="utf-8") as f:
        # Automatically remove duplicates when reading
        sites = list(set([line.strip() for line in f if line.strip()]))
    return sites

def write_sites(sites_list):
    """Write sites to sites.txt file - ENSURES NO DUPLICATES"""
    # Convert to set to remove duplicates, then back to list
    unique_sites = list(set(sites_list))
    
    with open(SITES_FILE, "w", encoding="utf-8") as f:
        for site in unique_sites:
            f.write(f"{site}\n")
    
    return len(unique_sites)

def normalize_url(url: str) -> str:
    """
    Normalize URL to prevent duplicates with slight variations
    
    Examples:
    - https://example.com/ -> https://example.com
    - https://EXAMPLE.COM -> https://example.com
    - https://example.com/// -> https://example.com
    """
    url = url.strip().lower()
    
    # Remove trailing slash
    url = url.rstrip('/')
    
    # Remove www. prefix for consistency
    if url.startswith('www.'):
        url = url[4:]
    
    return url

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

def get_random_proxy():
    """Get a random proxy that isn't in the bad list"""
    global BAD_PROXIES
    if not PROXY_LIST:
        return ""
    
    available = [p for p in PROXY_LIST if p not in BAD_PROXIES]
    if not available:
        # Reset bad proxies if all are bad
        BAD_PROXIES.clear()
        available = PROXY_LIST
        
    return random.choice(available) if available else ""

def mark_proxy_bad(proxy):
    """Mark a proxy as bad"""
    global BAD_PROXIES
    BAD_PROXIES.add(proxy)

_SITECHK_HTTP_SESSION = None

def _get_sitechk_http_session():
    global _SITECHK_HTTP_SESSION
    if _SITECHK_HTTP_SESSION is None or _SITECHK_HTTP_SESSION.closed:
        _SITECHK_HTTP_SESSION = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120),
            connector=aiohttp.TCPConnector(limit=1000, ssl=False)
        )
    return _SITECHK_HTTP_SESSION

async def call_site_check_api(site_url: str, cc_formatted: str, proxy: str) -> dict:
    """
    Call the cxchk.shopii API for site checking
    
    API Endpoint: https://wasatan.onrender.com/shopify?site=<url>&cc=<card>&proxy=<proxy>
    
    Response Format:
    {
      "Gateway": "Shopify Payments",
      "Price": "1.95",
      "Proxy": "Live",
      "Response": "GENERIC_ERROR",
      "Status": "false",
      "cc": "5402563006271217|08|30|140"
    }
    
    Returns dict with keys: success, response, price, proxy_status, gateway, error
    """
    try:
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        api_proxy = normalize_proxy(proxy)
        
        api_proxy_clean = api_proxy
        if api_proxy_clean.startswith("http://"):
            api_proxy_clean = api_proxy_clean[7:]
        elif api_proxy_clean.startswith("https://"):
            api_proxy_clean = api_proxy_clean[8:]
        elif api_proxy_clean.startswith("socks5://"):
            api_proxy_clean = api_proxy_clean[9:]

        params = {
            "site": site_url,
            "cc": cc_formatted,
            "proxy": api_proxy_clean
        }
        
        _http = _get_sitechk_http_session()
        current_api = random.choice(API_URLS)
        async with _http.get(
            current_api,
            params=params,
            timeout=timeout,
            ssl=False
        ) as resp:
                if resp.status != 200:
                    return {
                        "success": False,
                        "response": f"HTTP Error {resp.status}",
                        "price": "-1.0",
                        "proxy_status": "Dead",
                        "gateway": "",
                        "error": f"HTTP_{resp.status}"
                    }
                
                try:
                    data = await resp.json()
                except Exception:
                    return {
                        "success": False,
                        "response": "Invalid JSON Response",
                        "price": "-1.0",
                        "proxy_status": "Dead",
                        "gateway": "",
                        "error": "JSON_PARSE_ERROR"
                    }
                
                response_msg = data.get("Response", "Unknown")
                price_str = data.get("Price", "-1.0")
                proxy_raw = data.get("Proxy", "Live")
                gateway = data.get("Gateway", "")
                status = data.get("Status", False)
                
                if isinstance(status, str):
                    status_bool = status.lower() == "true"
                else:
                    status_bool = bool(status)
                
                if "live" in str(proxy_raw).lower():
                    proxy_status = "Live"
                else:
                    proxy_status = "Dead"
                
                return {
                    "success": True,
                    "response": response_msg,
                    "price": price_str,
                    "proxy_status": proxy_status,
                    "gateway": gateway,
                    "status": status_bool,
                    "error": None
                }
                
    except asyncio.TimeoutError:
        return {
            "success": False,
            "response": "Timeout Error",
            "price": "-1.0",
            "proxy_status": "Dead",
            "gateway": "",
            "error": "TIMEOUT"
        }
    except aiohttp.ClientConnectorError as e:
        error_str = str(e).lower()
        if "proxy" in error_str or "tunnel" in error_str:
            return {
                "success": False,
                "response": f"Proxy Error: {str(e)[:60]}",
                "price": "-1.0",
                "proxy_status": "Dead",
                "gateway": "",
                "error": "PROXY_ERROR"
            }
        return {
            "success": False,
            "response": f"Connection Error: {str(e)[:60]}",
            "price": "-1.0",
            "proxy_status": "Dead",
            "gateway": "",
            "error": "CONNECTION_ERROR"
        }
    except aiohttp.ClientError as e:
        return {
            "success": False,
            "response": f"Client Error: {str(e)[:60]}",
            "price": "-1.0",
            "proxy_status": "Dead",
            "gateway": "",
            "error": "CLIENT_ERROR"
        }
    except Exception as e:
        return {
            "success": False,
            "response": f"Error: {str(e)[:60]}",
            "price": "-1.0",
            "proxy_status": "Dead",
            "gateway": "",
            "error": "UNKNOWN_ERROR"
        }

async def check_site_status(site_url: str) -> tuple:
    """
    Checks a single site using the API with a test card.
    Uses an advanced fake charge detection system (checks with dead cards if charge response is got)
    and limits valid site prices to $10.00 or less.
    
    Returns: (site_url, status, data_dict, final_response_string)
    - status: "KEEP", "REMOVE", "ERROR"
    """
    MAX_RETRIES = 3
    
    for attempt in range(MAX_RETRIES):
        proxy = get_random_proxy()
        
        result = await call_site_check_api(
            site_url=site_url,
            cc_formatted=TEST_CARD,
            proxy=proxy
        )
        
        response_msg = result.get("response", "Unknown")
        price_str = result.get("price", "-1.0")
        proxy_status = result.get("proxy_status", "Dead")
        error_type = result.get("error")
        
        if proxy_status and proxy_status.lower() != "live":
            mark_proxy_bad(proxy)
            
            if error_type in ["PROXY_ERROR", "TIMEOUT", "CONNECTION_ERROR"]:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(0.5)
                    continue
        
        # Check for Hard Errors (Site Dead)
        is_dead = False
        response_lower = response_msg.lower()
        
        for err in DEAD_ERRORS:
            if err.lower() in response_lower:
                is_dead = True
                break
        
        if not result.get("success"):
            if error_type in ["JSON_PARSE_ERROR", "HTTP_500", "HTTP_502", "HTTP_503", "HTTP_404"]:
                is_dead = True
        
        if is_dead:
            return site_url, "REMOVE", {"Price": -1.0}, response_msg
        
        # Check for Valid Gateway Response (Site is Alive)
        if any(x in response_msg.upper() for x in SUCCESS_RESPONSES):
            actual_price = -1.0
            if price_str and price_str != "-1.0":
                clean_price = re.sub(r'[^\d.]', '', str(price_str))
                if clean_price:
                    try:
                        actual_price = float(clean_price)
                    except ValueError:
                        actual_price = -1.0
            
            # 1. PRICE CONSTRAINT: Must be between $0 and $10.00
            if not (0.00 <= actual_price <= 4.00):
                return site_url, "REMOVE", {"Price": actual_price}, f"Price ${actual_price:.2f} (> $10.00 Rejected) | {response_msg}"

            # 2. ADVANCED FAKE CHARGE DETECTION SYSTEM
            # Run 2 fake test cards on this site. If ANY fake card gets approved/order placed, reject immediately.
            # Only keep the site if BOTH fake cards decline.
            FAKE_CARDS = [
                "4003035140199121|11|29|470",
                "4400666318254873|03|27|336"
            ]
            fake_charged = 0
            for fake_cc in FAKE_CARDS:
                try:
                    f_result = await call_site_check_api(
                        site_url=site_url,
                        cc_formatted=fake_cc,
                        proxy=proxy
                    )
                    f_resp = f_result.get("response", "").lower()
                    if any(k in f_resp for k in ["thank you", "order_placed", "charged", "order_paid"]):
                        fake_charged += 1
                        break # Instant reject
                except Exception:
                    pass
            
            if fake_charged >= 1:
                return site_url, "REMOVE", {"Price": actual_price}, f"Fake Charge Detected (Fake Card Approved) | {response_msg}"

            msg_display = f"${actual_price:.2f} | {response_msg}"
            return site_url, "KEEP", {"Price": actual_price}, msg_display
        # Fallback - if we got a valid response but don't recognize it
        if result.get("success"):
            return site_url, "KEEP", {"Price": 0.0}, f"Unknown Response: {response_msg}"
        
        return site_url, "REMOVE", {"Price": -1.0}, response_msg

    return site_url, "ERROR", {"Price": -1.0}, "Max Retries Reached"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BACKGROUND WORKER FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def run_site_checker(bot: Bot, chat_id: int, sites_to_check, command_name="Audit", status_message_id=None):
    """Run site checking process in background with DUPLICATE PREVENTION"""
    global BAD_PROXIES
    # Reset bad proxies for fresh start
    BAD_PROXIES.clear()
    
    total_sites = len(sites_to_check)
    valid_sites = []
    working_sites_content = []
    checked_count = 0
    live_count = 0
    dead_count = 0
    duplicate_count = 0
    
    last_edit_time = 0
    MIN_EDIT_INTERVAL = 2.0
    CHECKS_PER_UPDATE = 10 
    sem = asyncio.Semaphore(20)
    
    # Get existing sites for duplicate checking (for /addsite command)
    existing_sites = set()
    if command_name == "Adding":
        existing_sites = set(await asyncio.to_thread(read_sites))
        print(f"[SITECHK] Found {len(existing_sites)} existing sites for duplicate check")

    async def worker(site):
        async with sem:
            return await check_site_status(site)
    
    tasks = [worker(site) for site in sites_to_check]
    
    for future in asyncio.as_completed(tasks):
        try:
            site, status, data, resp_msg = await future
        except Exception as e:
            checked_count += 1
            dead_count += 1
            print(f"[LOG] {site} | Error: {e}")
            continue
            
        checked_count += 1
        print(f"[LOG] {site} | {resp_msg}")

        if status == "KEEP":
            # NORMALIZE URL FOR DUPLICATE CHECK
            normalized_site = normalize_url(site)
            
            # Check if already exists (for Adding mode)
            if command_name == "Adding":
                normalized_existing = {normalize_url(s) for s in existing_sites}
                if normalized_site in normalized_existing:
                    duplicate_count += 1
                    print(f"[DUPLICATE SKIPPED] {site} already exists!")
                    continue
                
                # Also check if already added in this batch
                normalized_valid = {normalize_url(s) for s in valid_sites}
                if normalized_site in normalized_valid:
                    duplicate_count += 1
                    print(f"[DUPLICATE SKIPPED] {site} duplicate in batch!")
                    continue
            
            live_count += 1
            valid_sites.append(site)
            price = data.get("Price", "0.00") if isinstance(data, dict) else "0.00"
            if isinstance(price, float):
                price = f"${price:.2f}"
            working_sites_content.append(f"{site} | Price: {price} | Response: {resp_msg}")
        else:
            dead_count += 1

        current_time = time.time()
        if (current_time - MIN_EDIT_INTERVAL > last_edit_time) or (checked_count % CHECKS_PER_UPDATE == 0):
            try:
                if status_message_id:
                    dup_text = ""
                    if duplicate_count > 0:
                        dup_text = f"\n🔄 <b>Duplicates Skipped:</b> <code>{duplicate_count}</code>"
                    
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=status_message_id,
                        text=f"🔄 <b>{command_name}ing {total_sites} Sites...</b>\n"
                        f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
                        f"<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> <b>Kept ($0-10):</b> <code>{live_count}</code>\n"
                        f"<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> <b>Rejected:</b> <code>{dead_count}</code>\n"
                        f"🔄 <b>Checked:</b> <code>{checked_count}/{total_sites}</code>\n"
                        f"<tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> <b>Proxies Available:</b> <code>{len(PROXY_LIST) - len(BAD_PROXIES)}/{len(PROXY_LIST)}</code>"
                        f"{dup_text}",
                        parse_mode="HTML"
                    )
                    last_edit_time = current_time
            except Exception:
                pass 

    # FINAL DEDUPLICATION before saving
    final_unique_sites = []
    seen_normalized = set()
    
    for site in valid_sites:
        normalized = normalize_url(site)
        if normalized not in seen_normalized:
            seen_normalized.add(normalized)
            final_unique_sites.append(site)
    
    removed_dupes = len(valid_sites) - len(final_unique_sites)
    if removed_dupes > 0:
        print(f"[SITECHK] Removed {removed_dupes} internal duplicates before saving")
    
    # Save results based on command type
    if command_name == "Audit":
        saved_count = await asyncio.to_thread(write_sites, final_unique_sites)
    elif command_name == "Adding":
        # Merge with existing, ensuring no duplicates
        existing = await asyncio.to_thread(read_sites)
        existing_normalized = {normalize_url(s) for s in existing}
        
        combined = list(existing)
        for new_site in final_unique_sites:
            norm_new = normalize_url(new_site)
            if norm_new not in existing_normalized:
                combined.append(new_site)
                existing_normalized.add(norm_new)
        
        saved_count = await asyncio.to_thread(write_sites, combined)

    # Generate report file
    filename = f"report_{command_name.lower()}_{int(time.time())}.txt"
    
    file_content = "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    file_content += f"TOTAL CHECKED: {total_sites}\n"
    file_content += f"WORKING SITES (Price $0-10): {len(final_unique_sites)}\n"
    file_content += f"REJECTED (Dead/High Price): {dead_count}\n"
    if duplicate_count > 0 or removed_dupes > 0:
        file_content += f"DUPLICATES SKIPPED: {duplicate_count + removed_dupes}\n"
    file_content += f"PROXIES USED: {len(PROXY_LIST)} | BAD: {len(BAD_PROXIES)}\n"
    file_content += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if working_sites_content:
        file_content += "\n".join(working_sites_content)
    else:
        file_content += "No valid sites found within price range!"

    try:
        def _write_report():
            with open(filename, "w", encoding="utf-8") as f:
                f.write(file_content)
        
        await asyncio.to_thread(_write_report)
        
        if status_message_id:
            try:
                dup_final = ""
                if (duplicate_count + removed_dupes) > 0:
                    dup_final = f"\n<tg-emoji emoji-id='5040030395416969985'>🚫</tg-emoji> <b>Duplicates Blocked:</b> <code>{duplicate_count + removed_dupes}</code>"
                
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    text=f"<tg-emoji emoji-id='6242135305697106689'>🎁</tg-emoji> <b>{command_name} Complete!</b>\n\n"
                    f"<b>Total Checked:</b> {total_sites}\n"
                    f"<b>Valid ($0-10):</b> {len(final_unique_sites)} <tg-emoji emoji-id='5039844895779455925'>🍾</tg-emoji>\n"
                    f"<b>Rejected:</b> {dead_count} <tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji>\n"
                    f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
                    f"<tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> <b>Proxies Used:</b> {len(PROXY_LIST)} | <b>Bad:</b> {len(BAD_PROXIES)}"
                    f"{dup_final}",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        await bot.send_document(
            chat_id=chat_id,
            document=FSInputFile(filename),
            caption=f"📜 <b>{command_name} Report (Deduplicated)</b>",
            parse_mode="HTML"
        )
        
        try:
            os.remove(filename)
        except:
            pass
            
    except Exception as e:
        await bot.send_message(chat_id=chat_id, text=f"<tg-emoji emoji-id='5040030395416969985'>🚫</tg-emoji> <b>Error:</b> {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMMAND 1: /sitechk (Audit & Clean Sites)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.message(Command("sitechk"))
async def sitechk_command(message: types.Message):
    """Audit existing sites - removes dead ones and deduplicates"""
    user_id = message.from_user.id
    from sub import get_premium_status
    is_premium, _ = get_premium_status(user_id)
    if not is_premium:
        await message.reply("<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> 𝗣𝗹𝗲𝗮𝘀𝗲 𝘂𝗽𝗴𝗿𝗮𝗱𝗲 𝘆𝗼𝘂𝗿 𝗽𝗹𝗮𝗻 𝘁𝗼 𝘂𝘀𝗲 𝘁𝗵𝗶𝘀 𝗳𝗲𝗮𝘁𝘂𝗿𝗲.", parse_mode="HTML")
        return
    if not is_admin(user_id):
        await message.answer("⛔ <b>You are not authorized.</b>", parse_mode="HTML")
        return

    load_user_proxies(user_id)

    bot = message.bot
    chat_id = message.chat.id

    sites = await asyncio.to_thread(read_sites)
    if not sites:
        await message.answer("📭 <b>No sites found in sites.txt</b>", parse_mode="HTML")
        return

    status_msg = await message.answer(
        f"🔄 <b>Starting Audit on {len(sites)} Sites...</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"🔄 <b>Checked:</b> <code>0/{len(sites)}</code>\n"
        f"<tg-emoji emoji-id='5039844895779455925'>🍾</tg-emoji> <b>Kept ($0-10):</b> <code>0</code>\n"
        f"<tg-emoji emoji-id='5040030395416969985'>🚫</tg-emoji> <b>Rejected:</b> <code>0</code>\n"
        f"<tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> <b>Proxies:</b> <code>{len(PROXY_LIST)}</code>",
        parse_mode="HTML"
    )

    asyncio.create_task(
        run_site_checker(
            bot, 
            chat_id,
            sites,
            command_name="Audit",
            status_message_id=status_msg.message_id
        )
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMMAND 2: /addsite (Add & Verify New Sites)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.message(Command("addsite"))
async def addsite_command(message: types.Message):
    """Add new sites from uploaded file - automatically skips duplicates"""
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("⛔ <b>You are not authorized.</b>", parse_mode="HTML")
        return

    load_user_proxies(user_id)

    bot = message.bot
    chat_id = message.chat.id

    doc = message.document
    if not doc:
        if message.reply_to_message:
            doc = message.reply_to_message.document
    
    if not doc:
        await message.answer(
            "<tg-emoji emoji-id='5040030395416969985'>🚫</tg-emoji> <b>Please reply to a file or upload a file containing sites with /addsite.</b>",
            parse_mode="HTML"
        )
        return

    # Download file content
    try:
        file_info = await bot.get_file(doc.file_id)
        
        destination = io.BytesIO()
        await bot.download_file(file_info.file_path, destination)
        
        destination.seek(0)
        byte_content = destination.read()
        text = byte_content.decode('utf-8', errors='ignore')
        
        # Extract URLs
        new_sites = []
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # Try to find url with scheme
            url_pattern = r'(https?://\S+)'
            match = re.search(url_pattern, line)
            if match:
                url = match.group(1)
            else:
                # If no scheme, get the first word and if it looks like a domain, add https://
                first_word = line.split()[0]
                if '.' in first_word and not first_word.startswith(('http://', 'https://')):
                    url = f"https://{first_word}"
                else:
                    continue

            url = url.rstrip('.,;:!?)\'"')
            new_sites.append(url)
        
        # Remove duplicates from uploaded file itself
        new_sites = list(set(new_sites))
        
        if not new_sites:
            await message.answer("<tg-emoji emoji-id='5456140674028019486'>🛑</tg-emoji> <b>No valid sites found in file.</b>", parse_mode="HTML")
            return
            
    except Exception as e:
        logging.error(f"Error downloading file: {e}", exc_info=True)
        await message.answer(f"<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> <b>Error reading file:</b> {e}", parse_mode="HTML")
        return

    # Send initial status
    status_msg = await message.answer(
        f"🔄 <b>Starting Addition of {len(new_sites)} Sites...</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"🔄 <b>Checked:</b> <code>0/{len(new_sites)}</code>\n"
        f"<tg-emoji emoji-id='6242135305697106689'>🎁</tg-emoji> <b>Added ($0-10):</b> <code>0</code>\n"
        f"<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> <b>Rejected:</b> <code>0</code>\n"
        f"<tg-emoji emoji-id='5040030395416969985'>🚫</tg-emoji> <b>Duplicates:</b> <code>0</code>\n"
        f"<tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> <b>Proxies:</b> <code>{len(PROXY_LIST)}</code>",
        parse_mode="HTML"
    )

    asyncio.create_task(
        run_site_checker(
            bot,
            chat_id,
            new_sites,
            command_name="Adding",
            status_message_id=status_msg.message_id
        )
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMMAND 3: /siteall (List All Sites)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.message(Command("siteall"))
async def siteall_command(message: types.Message):
    """Download full list of all sites (automatically deduplicated)"""
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("⛔ <b>You are not authorized.</b>", parse_mode="HTML")
        return

    sites = await asyncio.to_thread(read_sites)
    if not sites:
        await message.answer("📭 <b>sites.txt is empty.</b>", parse_mode="HTML")
        return

    filename = f"full_sites_list_{int(time.time())}.txt"
    
    try:
        def _write_file():
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"Total Sites: {len(sites)} (Deduplicated)\n\n")
                f.write("\n".join(sites))
        
        await asyncio.to_thread(_write_file)
        
        await message.answer_document(
            document=FSInputFile(filename),
            caption=f"📜 <b>Total Sites:</b> <code>{len(sites)}</code> ✨ (No Duplicates)",
            parse_mode="HTML"
        )
        os.remove(filename)
    except Exception as e:
        await message.answer(f"<tg-emoji emoji-id='5040030395416969985'>🚫</tg-emoji> <b>Error:</b> {e}", parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMMAND 4: /removeall (Remove All Sites)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.message(Command("removeall"))
async def removeall_command(message: types.Message):
    """Clear all sites from sites.txt"""
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("⛔ <b>You are not authorized.</b>", parse_mode="HTML")
        return

    sites = await asyncio.to_thread(read_sites)
    if not sites:
        await message.answer("📭 <b>sites.txt is already empty.</b>", parse_mode="HTML")
        return

    try:
        def _clear_file():
            with open(SITES_FILE, "w", encoding="utf-8") as f:
                pass
        
        await asyncio.to_thread(_clear_file)
        
        await message.answer(
            f"<tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji> <b>All sites have been successfully removed.</b>\n\n"
            f"<b>Removed:</b> <code>{len(sites)}</code> sites",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"<tg-emoji emoji-id='5456140674028019486'>🛑</tg-emoji> <b>Error:</b> {e}", parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMMAND 5: /dedupe (Force Deduplicate)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.message(Command("dedupe"))
async def dedupe_command(message: types.Message):
    """Force deduplicate sites.txt"""
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("⛔ <b>You are not authorized.</b>", parse_mode="HTML")
        return
    
    sites = await asyncio.to_thread(read_sites)
    
    if not sites:
        await message.answer("📭 <b>sites.txt is empty.</b>", parse_mode="HTML")
        return
    
    original_count = len(sites)
    
    # Force write (which auto-deduplicates)
    final_count = await asyncio.to_thread(write_sites, sites)
    
    removed = original_count - final_count
    
    if removed > 0:
        await message.answer(
            f"✨ <b>Deduplication Complete!</b>\n\n"
            f"<b>Original:</b> <code>{original_count}</code>\n"
            f"<b>Removed:</b> <code>{removed}</code> duplicates\n"
            f"<b>Final:</b> <code>{final_count}</code> unique sites",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            f"<tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji> <b>No duplicates found!</b>\n\n"
            f"<b>Total Sites:</b> <code>{final_count}</code> (All Unique)",
            parse_mode="HTML"
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMMAND 6: /proxyinfo (Check Proxy Status)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.message(Command("proxyinfo"))
async def proxyinfo_command(message: types.Message):
    """Show proxy statistics"""
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("⛔ <b>You are not authorized.</b>", parse_mode="HTML")
        return

    available = len(PROXY_LIST) - len(BAD_PROXIES)
    
    text = (
        f"<tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> <b>Proxy Information</b>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"📊 <b>Total Proxies:</b> <code>{len(PROXY_LIST)}</code>\n"
        f"<tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji> <b>Available:</b> <code>{available}</code>\n"
        f"<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> <b>Bad/Dead:</b> <code>{len(BAD_PROXIES)}</code>\n"
        f"<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
    )
    
    for i, proxy in enumerate(PROXY_LIST, 1):
        status = "<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> Dead" if proxy in BAD_PROXIES else "<tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji> Live"
        if "@" in proxy:
            parts = proxy.split("@")
            host_part = parts[1] if len(parts) > 1 else proxy
            text += f"<code>{i}.</code> {host_part} - {status}\n"
        else:
            text += f"<code>{i}.</code> {proxy[:30]}... - {status}\n"
    
    await message.answer(text, parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMMAND 7: /resetproxy (Reset Bad Proxies)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.message(Command("resetproxy"))
async def resetproxy_command(message: types.Message):
    """Reset all bad proxies back to available"""
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("⛔ <b>You are not authorized.</b>", parse_mode="HTML")
        return

    global BAD_PROXIES
    cleared_count = len(BAD_PROXIES)
    BAD_PROXIES.clear()
    
    await message.answer(
        f"<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> <b>Proxy List Reset!</b>\n\n"
        f"<b>Cleared:</b> <code>{cleared_count}</code> bad proxies\n"
        f"<b>Available Now:</b> <code>{len(PROXY_LIST)}</code>",
        parse_mode="HTML"
    )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMMAND 8: /remsite (Remove and Ban Site)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BANNED_SITES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "banned_sites.json")

@router.message(Command("remsite"))
async def remsite_command(message: types.Message):
    """Remove site from sites.txt and add to banned_sites.json"""
    import json
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.answer("⛔ <b>You are not authorized.</b>", parse_mode="HTML")
        return

    args = message.text.split()[1:]
    if not args:
        await message.answer("<tg-emoji emoji-id='5040030395416969985'>🚫</tg-emoji> <b>Usage:</b> /remsite {site_url}\nExample: /remsite example.com", parse_mode="HTML")
        return

    target_site = normalize_url(args[0])
    
    # Read existing sites
    sites = read_sites()
    removed_from_active = False
    
    # Filter it out
    filtered_sites = []
    for s in sites:
        if normalize_url(s) == target_site:
            removed_from_active = True
        else:
            filtered_sites.append(s)

    if removed_from_active:
        write_sites(filtered_sites)

    # Load/update banned_sites.json
    banned_sites = []
    if os.path.exists(BANNED_SITES_FILE):
        try:
            with open(BANNED_SITES_FILE, "r", encoding="utf-8") as f:
                banned_sites = json.load(f)
        except Exception:
            banned_sites = []

    if target_site not in banned_sites:
        banned_sites.append(target_site)
        try:
            with open(BANNED_SITES_FILE, "w", encoding="utf-8") as f:
                json.dump(banned_sites, f, indent=4)
        except Exception as e:
            await message.answer(f"<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> <b>Error writing to banned_sites.json:</b> <code>{str(e)}</code>", parse_mode="HTML")
            return

    active_removed_str = "and removed from active list" if removed_from_active else "(was not in active list)"
    await message.answer(
        f"<tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji> <b>Site Banned successfully!</b>\n\n"
        f"<tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> <b>Site:</b> <code>{target_site}</code>\n"
        f"<tg-emoji emoji-id='5040030395416969985'>🚫</tg-emoji> Site is now added to <code>banned_sites.json</code> {active_removed_str} and will never be used again.",
        parse_mode="HTML"
    )
