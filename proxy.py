import re
import logging
import aiohttp
import asyncio
import sqlite3
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
import io

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AIogram Imports
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from aiogram import types, F, Router
from aiogram.types import BufferedInputFile

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOCAL IMPORTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from database import get_db_connection, create_user

# ── FIX: get_premium_status function direct define ──
def get_premium_status(user_id: int) -> tuple[bool, str]:
    """Simple premium check - all users free"""
    return True, "FREE"

# Router for this module
router = Router()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MAX_CONCURRENT_CHECKS = 5
PROXY_TIMEOUT = 8
IPIFY_API_URL = "https://api.ipify.org?format=json"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PARSING HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def parse_proxy_input(proxy_input):
    s = proxy_input.strip()
    protocol = 'http' 
    
    protocol_match = re.match(r'^(?P<p>http|https|socks4|socks5)://', s, re.IGNORECASE)
    if protocol_match:
        protocol = protocol_match.group('p').lower()
        s = s[len(protocol_match.group('p'))+3:]

    def is_valid(ip, port):
        return port and port.isdigit()

    match = re.match(r'^([^:@]+):([^:@]+)@([^:@]+):(\d+)$', s)
    if match:
        user, password, ip, port = match.groups()
        if is_valid(ip, port): return build_dict(user, password, ip, port, protocol, proxy_input)

    match = re.match(r'^([^:@]+):([^:@]+)\s+([^:@]+):(\d+)$', s)
    if match:
        user, password, ip, port = match.groups()
        if is_valid(ip, port): return build_dict(user, password, ip, port, protocol, proxy_input)

    match = re.match(r'^([^:@]+):(\d+)\s+([^:@]+)\s+([^:@]+)$', s)
    if match:
        ip, port, user, password = match.groups()
        if is_valid(ip, port): return build_dict(user, password, ip, port, protocol, proxy_input)

    match = re.match(r'^([^:@]+)\s+([^:@]+)\s+([^:@]+):(\d+)$', s)
    if match:
        user, password, ip, port = match.groups()
        if is_valid(ip, port): return build_dict(user, password, ip, port, protocol, proxy_input)

    match = re.match(r'^([^:@]+)\s+([^:@]+)\s+([^:@]+)\s+(\d+)$', s)
    if match:
        user, password, ip, port = match.groups()
        if is_valid(ip, port): return build_dict(user, password, ip, port, protocol, proxy_input)

    match = re.match(r'^([^:@]+):([^:@]+):([^:@]+):(\d+)$', s)
    if match:
        user, password, ip, port = match.groups()
        if is_valid(ip, port): return build_dict(user, password, ip, port, protocol, proxy_input)

    match = re.match(r'^([^:@]+):(\d+):([^:@]+):([^:@]+)$', s)
    if match:
        ip, port, user, password = match.groups()
        if is_valid(ip, port): return build_dict(user, password, ip, port, protocol, proxy_input)

    return None

def build_dict(user, password, ip, port, protocol, original_input):
    user = user.strip()
    password = password.strip()
    ip = ip.strip()
    port = port.strip()
    
    encoded_user = quote(user, safe='')
    encoded_pass = quote(password, safe='')
    return {
        "user": user,
        "password": password,
        "ip": ip,
        "port": port,
        "original_format": original_input,
        "url_format": f"{protocol}://{encoded_user}:{encoded_pass}@{ip}:{port}",
        "db_format": f"{user} {password} {ip} {port}",
        "http_format": f"http://{user}:{password}@{ip}:{port}"
    }

async def check_proxy_live(proxy_url, session=None, timeout=PROXY_TIMEOUT):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json'
    }
    
    client_timeout = aiohttp.ClientTimeout(total=timeout, connect=timeout/2)
    
    close_session = False
    if session is None:
        session = aiohttp.ClientSession(timeout=client_timeout, headers=headers)
        close_session = True
    
    try:
        async with session.get(IPIFY_API_URL, proxy=proxy_url, ssl=False) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    ip = data.get('ip')
                    if ip:
                        return True, {"ip": ip}
                    return False, {"error": "No IP in response"}
                except Exception:
                    text_data = await resp.text()
                    if text_data.strip():
                        return True, {"ip": text_data.strip()}
                    return False, {"error": "Empty response"}
            elif resp.status == 429:
                return False, {"error": "Rate limited"}
            else:
                return False, {"error": f"HTTP {resp.status}"}
    except asyncio.TimeoutError:
        return False, {"error": "Timeout"}
    except aiohttp.ClientProxyConnectionError:
        return False, {"error": "Connection failed"}
    except aiohttp.ClientConnectorError as e:
        return False, {"error": f"DNS/Connection error"}
    except aiohttp.ClientError as e:
        return False, {"error": f"Client error: {str(e)[:50]}"}
    except Exception as e:
        return False, {"error": str(e)[:80]}
    finally:
        if close_session:
            await session.close()

async def check_proxies_parallel(proxies_list, max_concurrent=MAX_CONCURRENT_CHECKS):
    semaphore = asyncio.Semaphore(max_concurrent)
    results = []
    
    async def check_with_semaphore(proxy_data):
        async with semaphore:
            is_live, info = await check_proxy_live(proxy_data['url_format'])
            return (proxy_data, is_live, info)
    
    tasks = [check_with_semaphore(p) for p in proxies_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    processed_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            processed_results.append((proxies_list[i], False, {"error": str(result)}))
        else:
            processed_results.append(result)
    
    return processed_results

async def run_db_operation(func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BACKGROUND TASKS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def process_proxies_background(bot, message: types.Message, valid_proxies: list, is_premium: bool):
    user_id = message.from_user.id
    chat_id = message.chat.id
    total_count = len(valid_proxies)
    
    status_msg = await message.reply(
        f"⏳ <b>Processing...</b>\n\n"
        f"<code>{total_count}</code> proxies to check\n"
        f"<i>Checking</i>",
        parse_mode="HTML"
    )
    
    results_list = await check_proxies_parallel(valid_proxies)
    
    live_count = sum(1 for _, is_live, _ in results_list if is_live)
    dead_count = total_count - live_count
    
    to_save = [proxy_data['db_format'] for proxy_data, is_live, _ in results_list if is_live]
    
    added_count = 0
    clone_count = 0
    if to_save:
        try:
            def save_to_db():
                conn = None
                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    added = 0
                    clones = 0
                    for p_str in to_save:
                        try:
                            cursor.execute("SELECT 1 FROM proxies WHERE user_id = %s AND proxy = %s", (user_id, p_str))
                            if cursor.fetchone():
                                clones += 1
                            else:
                                cursor.execute("INSERT INTO proxies (user_id, proxy) VALUES (%s, %s)", (user_id, p_str))
                                added += 1
                        except Exception as insert_error:
                            logging.error(f"Insert error for proxy {p_str}: {insert_error}")
                            clones += 1
                    conn.commit()
                    return added, clones
                finally:
                    if conn:
                        conn.close()
            
            added_count, clone_count = await run_db_operation(save_to_db)
        except Exception as e:
            logging.error(f"DB Error during save: {e}")
            added_count = 0
            clone_count = 0

    is_bulk = total_count > 1
    
    if is_bulk:
        caption = (
            f"✅ <b>Complete!</b>\n\n"
            f"<b>Total ➛</b> <code>{total_count}</code>\n"
            f"<b>Live ➛</b> <b>{live_count}</b> ✅\n"
            f"<b>Added ➛</b> <b>{added_count}</b> 💾\n"
            f"<b>Clones ➛</b> <b>{clone_count}</b> 🔁\n"
            f"<b>Dead ➛</b> <b>{dead_count}</b> ⚠️"
        )
    else:
        if results_list:
            proxy_data, is_live, info = results_list[0]
        else:
            proxy_data, is_live, info = valid_proxies[0], False, {}
        
        if is_live:
            ip = info.get("ip") or proxy_data['ip']
            if added_count > 0:
                caption = (
                    f"✅ <b>Success!</b>\n\n"
                    f"<b>Status ➛ Live ✅</b>\n"
                    f"<b>IP ➛</b> <code>{ip}</code>\n\n"
                    f"💾 <b>Saved to database</b>"
                )
            else:
                caption = (
                    f"⚠️ <b>Warning!</b>\n\n"
                    f"<b>Status ➛ Live 💎</b>\n"
                    f"<b>IP ➛</b> <code>{ip}</code>\n\n"
                    f"🔁 <b>Already exists in DB</b>"
                )
        else:
            error_msg = info.get("error", "Unknown")
            caption = (
                f"❌ <b>Failed!</b>\n\n"
                f"<b>Status ➛ Dead 🛑</b>\n"
                f"<b>Reason ➛</b> <code>{error_msg}</code>\n\n"
                f"❌ <b>Not added.</b>"
            )
    
    try:
        await bot.edit_message_text(
            chat_id=status_msg.chat.id,
            message_id=status_msg.message_id,
            text=caption,
            parse_mode="HTML"
        )
    except Exception:
        await message.reply(caption, parse_mode="HTML")

async def check_db_proxies_background(bot, message: types.Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    def fetch_proxies():
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, proxy FROM proxies WHERE user_id = %s", (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return rows
    
    try:
        rows = await run_db_operation(fetch_proxies)
    except Exception as e:
        logging.error(f"DB Error: {e}")
        await message.reply("❌ <b>DB Error.</b>", parse_mode="HTML")
        return

    if not rows:
        await message.reply("📭 <b>No proxies saved.</b>", parse_mode="HTML")
        return

    total_count = len(rows)
    
    status_msg = await message.reply(
        f"⏳ <b>Checking...</b>\n\n"
        f"<code>{total_count}</code> saved proxies\n"
        f"<i>Testing {min(MAX_CONCURRENT_CHECKS, total_count)} at a time...</i>",
        parse_mode="HTML"
    )
    
    proxies_to_check = []
    proxy_id_map = {}
    
    for idx, (proxy_id, proxy_str) in enumerate(rows):
        parsed = parse_proxy_input(proxy_str)
        if parsed:
            proxies_to_check.append(parsed)
            proxy_id_map[idx] = proxy_id
    
    results_list = await check_proxies_parallel(proxies_to_check)
    
    dead_ids = []
    live_proxies = []
    
    for idx, (proxy_data, is_live, info) in enumerate(results_list):
        db_id = proxy_id_map.get(idx)
        if is_live:
            live_proxies.append(proxy_data['db_format'])
        else:
            if db_id:
                dead_ids.append(db_id)
    
    if dead_ids:
        def delete_dead():
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM proxies WHERE id = ANY(%s)", (dead_ids,))
            conn.commit()
            conn.close()
        
        try:
            await run_db_operation(delete_dead)
        except Exception as e:
            logging.error(f"DB Delete Error: {e}")

    caption = (
        f"✅ <b>Check Complete!</b>\n\n"
        f"<b>Total ➛</b> <code>{total_count}</code>\n"
        f"<b>Live ➛</b> <b>{len(live_proxies)}</b> ✅\n"
        f"<b>Removed ➛</b> <b>{len(dead_ids)}</b> 🗑️"
    )

    if not live_proxies:
        caption += "\n\n⚠️ <b>No live proxies remaining.</b>"
        
        try:
            await bot.edit_message_text(
                chat_id=status_msg.chat.id,
                message_id=status_msg.message_id,
                text=caption,
                parse_mode="HTML"
            )
        except Exception:
            await message.reply(caption, parse_mode="HTML")
        return

    file_content = ""
    for p_str in live_proxies:
        parsed = parse_proxy_input(p_str)
        if parsed:
            file_content += parsed['http_format'] + "\n"
        else:
            file_content += p_str + "\n"

    txt_file = BufferedInputFile(
        file=file_content.encode('utf-8'),
        filename=f"live_proxies_{len(live_proxies)}.txt"
    )

    try:
        await bot.delete_message(
            chat_id=status_msg.chat.id, 
            message_id=status_msg.message_id
        )
    except Exception:
        pass

    try:
        await message.reply_document(
            document=txt_file,
            caption=caption,
            parse_mode="HTML",
            reply_to_message_id=message.message_id
        )
    except Exception as e:
        logging.error(f"File send error: {e}")
        await message.reply(caption, parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMMANDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.message(F.text.startswith("/proxy"))
async def proxy_command(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    
    is_premium, expiry = True, "FREE"

    raw_text = ""
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1: raw_text += parts[1].strip() + "\n"
    if message.reply_to_message and message.reply_to_message.text: raw_text += message.reply_to_message.text + "\n"
    if message.reply_to_message and message.reply_to_message.caption: raw_text += message.reply_to_message.caption + "\n"

    document = message.document
    if document:
        if document.file_size > 2 * 1024 * 1024:
            await message.reply("⚠️ <b>File too large. Max 2MB.</b>", parse_mode="HTML")
            return
        try:
            file = await message.bot.get_file(document.file_id)
            byte_content = await file.download_as_bytearray()
            raw_text += byte_content.decode('utf-8', errors='ignore')
        except Exception as e:
            await message.reply(f"🛑 <b>Error reading file: {e}</b>", parse_mode="HTML")
            return

    if not raw_text.strip():
        await message.reply("❌ <b>Invalid Usage!</b>", parse_mode="HTML")
        return

    lines = raw_text.strip().split('\n')
    valid_proxies = []
    for line in lines:
        proxy_data = parse_proxy_input(line)
        if proxy_data: valid_proxies.append(proxy_data)
    
    logging.info(f"User {user_id} submitted {len(valid_proxies)} proxies for processing")
            
    if not valid_proxies:
        await message.reply("⚠️ <b>No valid proxies found.</b>", parse_mode="HTML")
        return

    asyncio.create_task(process_proxies_background(message.bot, message, valid_proxies, is_premium))

@router.message(F.text.startswith("/checkproxy"))
async def checkproxy_command(message: types.Message):
    asyncio.create_task(check_db_proxies_background(message.bot, message))

@router.message(F.text.startswith("/clearproxy"))
async def clearproxy_command(message: types.Message):
    user_id = message.from_user.id
    
    def clear_proxies():
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM proxies WHERE user_id = %s", (user_id,))
        count = cursor.fetchone()[0]
        
        if count > 0:
            cursor.execute("DELETE FROM proxies WHERE user_id = %s", (user_id,))
            conn.commit()
            conn.close()
            return count
        else:
            conn.close()
            return 0
    
    try:
        count = await run_db_operation(clear_proxies)
        if count > 0:
            msg = f"✅ <b>Success!</b>\n\n<b>Deleted {count} proxies.</b>"
        else:
            msg = "📭 <b>Database is already empty.</b>"
        await message.reply(msg, parse_mode="HTML")
    except Exception as e:
        logging.error(f"DB Error: {e}")
        await message.reply("❌ <b>Error.</b>", parse_mode="HTML")

@router.message(F.text.startswith("/myproxies"))
async def myproxies_command(message: types.Message):
    user_id = message.from_user.id
    
    def count_proxies():
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM proxies WHERE user_id = %s", (user_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count
    
    try:
        count = await run_db_operation(count_proxies)
        if count > 0:
            msg = (
                f"📊 <b>Your Proxies</b>\n\n"
                f"<b>Total Saved:</b> <b>{count}</b> proxies\n\n"
                f"Use <code>/checkproxy</code> to test them\n"
                f"Use <code>/clearproxy</code> to remove all"
            )
        else:
            msg = (
                f"📭 <b>No Proxies</b>\n\n"
                f"You haven't saved any proxies yet.\n\n"
                f"Use <code>/proxy</code> to add some!"
            )
        await message.reply(msg, parse_mode="HTML")
    except Exception as e:
        logging.error(f"DB Error: {e}")
        await message.reply("🚫 <b>Error.</b>", parse_mode="HTML")