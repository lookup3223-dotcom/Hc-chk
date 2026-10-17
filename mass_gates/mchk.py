import asyncio
import re
import logging
import time
import psycopg2.extras
import random
import string
import os

from aiogram import types, F, Router, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters.callback_data import CallbackData

from database import is_gate_enabled, get_db_connection
from bin import get_bin_info
from sub import get_premium_status
from mass_gates.stripeauth import process_stripe_auth

router = Router()

MCHK_SESSIONS = {}

class MchkResultCallback(CallbackData, prefix="mchkr"):
    session_id: str
    result_type: str

class MchkStopCallback(CallbackData, prefix="mchks"):
    session_id: str

def luhn_check(card_number: str) -> bool:
    card_number = str(card_number).strip()
    if not card_number.isdigit(): return False
    total = 0
    reverse_digits = card_number[::-1]
    for i, char in enumerate(reverse_digits):
        digit = int(char)
        if i % 2 == 1:
            digit *= 2
            if digit > 9: digit -= 9
        total += digit
    return total % 10 == 0

def extract_cards_from_text(text: str) -> list:
    pattern = r'\b(\d{15,16})[|\s/?\\:]+(\d{2,4})[|\s/?\\:]+(\d{2,4})[|\s/?\\:]+(\d{3,4})\b'
    matches = re.findall(pattern, text)
    cards = []
    for match in matches:
        cc, mm, yy, cvv = match
        if len(yy) == 4: yy = yy[2:]
        cards.append(f"{cc}|{mm}|{yy}|{cvv}")
    return list(dict.fromkeys(cards))

def get_mchk_buttons(session_id: str, is_running: bool = True):
    if is_running:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Stop", style="danger", icon_custom_emoji_id="5039937555403899813", callback_data=MchkStopCallback(session_id=session_id).pack())]
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Approved", style="success", icon_custom_emoji_id="5039844895779455925", callback_data=MchkResultCallback(session_id=session_id, result_type="approved").pack()),
         InlineKeyboardButton(text="Dead", style="danger", icon_custom_emoji_id="5040042498634810056", callback_data=MchkResultCallback(session_id=session_id, result_type="dead").pack())]
    ])

@router.message(lambda message: (message.text and message.text.startswith("/mchk")) or (message.caption and message.caption.startswith("/mchk")))
async def mchk_command(message: types.Message):
    if not await asyncio.to_thread(is_gate_enabled, "chk"):
        await message.reply("<tg-emoji emoji-id='4958926882994127612'>🚧</tg-emoji> <b>𝗠𝗮𝘀𝘀 𝗦𝘁𝗿𝗶𝗽𝗲 𝟬$ 𝗶𝘀 𝘂𝗻𝗱𝗲𝗿 𝗺𝗮𝗶𝗻𝘁𝗲𝗻𝗮𝗻𝗰𝗲.</b>", parse_mode="HTML")
        return

    user = message.from_user
    user_id = user.id

    is_premium, _ = get_premium_status(user_id)
    if not is_premium:
        await message.reply("<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> 𝗣𝗹𝗲𝗮𝘀𝗲 𝘂𝗽𝗴𝗿𝗮𝗱𝗲 𝘆𝗼𝘂𝗿 𝗽𝗹𝗮𝗻 𝘁𝗼 𝘂𝘀𝗲 𝘁𝗵𝗶𝘀 𝗳𝗲𝗮𝘁𝘂𝗿𝗲.", parse_mode="HTML")
        return

    is_checking = any(s.get('user_id') == user_id and s.get('status') == "CHECKING" for s in MCHK_SESSIONS.values())
    if is_checking:
        await message.reply("<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> <b>𝗔𝗰𝘁𝗶𝘃𝗲 𝗦𝗲𝘀𝘀𝗶𝗼𝗻</b>\nYou have a check running. Stop it first.", parse_mode="HTML")
        return

    cmd_text = message.text or message.caption or ""
    raw_text = cmd_text.split(maxsplit=1)[1] + " " if len(cmd_text.split(maxsplit=1)) > 1 else ""
    if message.reply_to_message:
        replied_msg = message.reply_to_message
        if replied_msg.text: raw_text += replied_msg.text + " "
        elif replied_msg.caption: raw_text += replied_msg.caption + " "

    document = message.document or (message.reply_to_message.document if message.reply_to_message else None)
    if document:
        if document.file_size > 2 * 1024 * 1024:
            await message.reply("<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> File too large.")
            return
        try:
            file_info = await message.bot.get_file(document.file_id)
            byte_content = await message.bot.download_file(file_info.file_path)
            raw_text += byte_content.read().decode('utf-8', errors='ignore')
        except:
            await message.reply("<tg-emoji emoji-id='5040030395416969985'>🚫</tg-emoji> Error reading file.")
            return

    if not raw_text.strip():
        await message.reply("<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> <b>𝗡𝗼 𝗰𝗮𝗿𝗱𝘀 𝗳𝗼𝘂𝗻𝗱.</b>\nUsage: <code>/mchk cards</code>", parse_mode="HTML")
        return

    extracted_cards = extract_cards_from_text(raw_text)
    valid_cards = [c for c in extracted_cards if luhn_check(c.split('|')[0])]
    
    if not valid_cards:
        await message.reply("<tg-emoji emoji-id='5456140674028019486'>🛑</tg-emoji> No valid cards to check.", parse_mode="HTML")
        return

    current_credits = 999999999
    if current_credits < len(valid_cards):
        await message.reply(f"<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> <b>𝗜𝗻𝘀𝘂𝗳𝗳𝗶𝗰𝗶𝗲𝗻𝘁 𝗖𝗿𝗲𝗱𝗶𝘁𝘀</b>\nYou need {len(valid_cards)} credits. Balance: {current_credits}.", parse_mode="HTML")
        return

    asyncio.create_task(process_mchk_background(message, message.bot, valid_cards, user))

async def process_mchk_background(message: types.Message, bot: Bot, valid_cards: list, user):
    user_id = user.id
    total_cards = len(valid_cards)
    session_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    
    initial_text = (
        f"<b><tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➛</b> Stripe Auth 0$\n"
        f"<b><tg-emoji emoji-id='5231200819986047254'>📊</tg-emoji> 𝗦𝘁𝗮𝘁𝘂𝘀 ➛</b> <i>CHECKING</i> <tg-emoji emoji-id='5269531045165816230'>🔄</tg-emoji>\n"
        f"<b><tg-emoji emoji-id='5388632425314140043'>🔍</tg-emoji> 𝗖𝗵𝗲𝗰𝗸𝗲𝗱 ➛</b> <code>0/{total_cards}</code>\n"
        f"<b><tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji> 𝗔𝗽𝗽𝗿𝗼𝘃𝗲𝗱 ➛</b> <b>0</b>\n"
        f"<b><tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> 𝗗𝗲𝗮𝗱 ➛</b> <b>0</b>\n"
        f"<b><tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> 𝗘𝗿𝗿𝗼𝗿𝘀 ➛</b> <b>0</b>\n"
        f"<b><tg-emoji emoji-id='5406683434124859552'>🆔</tg-emoji> 𝗦𝗲𝘀𝘀𝗶𝗼𝗻 𝗜𝗗 ➛</b> <code>{session_id}</code>"
    )

    progress_msg = await message.reply(initial_text, parse_mode="HTML", reply_markup=get_mchk_buttons(session_id, True))

    MCHK_SESSIONS[session_id] = {
        'status': "CHECKING", 'user_id': user_id, 'cards_total': total_cards,
        'cards_checked': 0, 'approved': [], 'dead': [], 'errors': [],
        'progress_msg_id': progress_msg.message_id, 'chat_id': progress_msg.chat.id
    }

    session = MCHK_SESSIONS[session_id]
    
    for i, card in enumerate(valid_cards):
        if session['status'] == "STOPPED": break

        await asyncio.sleep(1) # Prevent rate limits
        api_result = await process_stripe_auth(card)
        
        status_raw = api_result.get("status", "").upper()
        cc = card.split('|')[0]
        bin_info = await get_bin_info(cc[:6])
        scheme = bin_info.get("scheme", "N/A")
        bank = bin_info.get("bank", "N/A")
        country = bin_info.get("country", "N/A")
        flag = bin_info.get("country_emoji", "")
        country_str = f"{flag} {country}" if flag else country
        
        resp_msg = api_result.get("response", "N/A")
        
        if "APPROVED" in status_raw:
            session['approved'].append((card, resp_msg, scheme, bank, country_str))
            await send_mchk_hit(bot, message.chat.id, user, card, resp_msg, scheme, bank, country_str)
        elif "DECLINED" in status_raw:
            session['dead'].append((card, resp_msg))
        else:
            session['errors'].append((card, resp_msg))

        session['cards_checked'] += 1

        if session['cards_checked'] % 2 == 0 or session['cards_checked'] == total_cards:
            try:
                await bot.edit_message_text(
                    chat_id=session['chat_id'], message_id=session['progress_msg_id'],
                    text=(
                        f"<b><tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➛</b> Stripe Auth 0$\n"
                        f"<b><tg-emoji emoji-id='5231200819986047254'>📊</tg-emoji> 𝗦𝘁𝗮𝘁𝘂𝘀 ➛</b> <i>CHECKING</i> <tg-emoji emoji-id='5269531045165816230'>🔄</tg-emoji>\n"
                        f"<b><tg-emoji emoji-id='5388632425314140043'>🔍</tg-emoji> 𝗖𝗵𝗲𝗰𝗸𝗲𝗱 ➛</b> <code>{session['cards_checked']}/{total_cards}</code>\n"
                        f"<b><tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji> 𝗔𝗽𝗽𝗿𝗼𝘃𝗲𝗱 ➛</b> <b>{len(session['approved'])}</b>\n"
                        f"<b><tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> 𝗗𝗲𝗮𝗱 ➛</b> <b>{len(session['dead'])}</b>\n"
                        f"<b><tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> 𝗘𝗿𝗿𝗼𝗿𝘀 ➛</b> <b>{len(session['errors'])}</b>\n"
                        f"<b><tg-emoji emoji-id='5406683434124859552'>🆔</tg-emoji> 𝗦𝗲𝘀𝘀𝗶𝗼𝗻 𝗜𝗗 ➛</b> <code>{session_id}</code>"
                    ),
                    parse_mode="HTML", reply_markup=get_mchk_buttons(session_id, True)
                )
            except: pass

    session['status'] = "FINISHED"
    try:
        await bot.edit_message_text(
            chat_id=session['chat_id'], message_id=session['progress_msg_id'],
            text=(
                f"<b><tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➛</b> Stripe Auth 0$\n"
                f"<b><tg-emoji emoji-id='5231200819986047254'>📊</tg-emoji> 𝗦𝘁𝗮𝘁𝘂𝘀 ➛</b> <i>FINISHED</i> <tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji>\n"
                f"<b><tg-emoji emoji-id='5388632425314140043'>🔍</tg-emoji> 𝗖𝗵𝗲𝗰𝗸𝗲𝗱 ➛</b> <code>{session['cards_checked']}/{total_cards}</code>\n"
                f"<b><tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji> 𝗔𝗽𝗽𝗿𝗼𝘃𝗲𝗱 ➛</b> <b>{len(session['approved'])}</b>\n"
                f"<b><tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> 𝗗𝗲𝗮𝗱 ➛</b> <b>{len(session['dead'])}</b>\n"
                f"<b><tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> 𝗘𝗿𝗿𝗼𝗿𝘀 ➛</b> <b>{len(session['errors'])}</b>\n"
                f"<b><tg-emoji emoji-id='5406683434124859552'>🆔</tg-emoji> 𝗦𝗲𝘀𝘀𝗶𝗼𝗻 𝗜𝗗 ➛</b> <code>{session_id}</code>"
            ),
            parse_mode="HTML", reply_markup=get_mchk_buttons(session_id, False)
        )
    except: pass

async def send_mchk_hit(bot, chat_id, user, cc, msg, scheme, bank, country):
    text = (
        f"<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> 𝗦𝘁𝗮𝘁𝘂𝘀 ➛ 𝗔𝗣𝗣𝗥𝗢𝗩𝗘𝗗 <tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji>\n"
        f"<tg-emoji emoji-id='5039623284056917259'>💳</tg-emoji> 𝗖𝗮𝗿𝗱 ➛ <code>{cc}</code>\n"
        f"<tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➛ Stripe 0$\n"
        f"<tg-emoji emoji-id='5040042498634810056'>💬</tg-emoji> 𝗥𝗲𝘀𝗽𝗼𝗻𝘀𝗲 ➛ <b>{msg}</b>\n"
        f"<tg-emoji emoji-id='5406683434124859552'>🆔</tg-emoji> 𝗕𝗿𝗮𝗻𝗱 ➛ <b>{scheme}</b>\n"
        f"🏦 𝗜𝘀𝘀𝘂𝗲𝗿 ➛ <b>{bank}</b>\n"
        f"📍 𝗖𝗼𝘂𝗻𝘁𝗿𝘆 ➛ <b>{country}</b>\n"
        f"<tg-emoji emoji-id='6237927637906364256'>👤</tg-emoji> 𝗨𝘀𝗲𝗿 ➛ <a href='tg://user?id={user.id}'>{user.first_name}</a>"
    )
    await bot.send_message(chat_id, text, parse_mode="HTML")

@router.callback_query(MchkStopCallback.filter())
async def mchk_stop_cb(call: types.CallbackQuery, callback_data: MchkStopCallback):
    session_id = callback_data.session_id
    session = MCHK_SESSIONS.get(session_id)
    if not session:
        await call.answer("Session not found or expired.", show_alert=True)
        return
    if session['user_id'] != call.from_user.id:
        await call.answer("<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> 𝗬𝗼𝘂 𝗰𝗮𝗻𝗻𝗼𝘁 𝗶𝗻𝘁𝗲𝗿𝗮𝗰𝘁 𝘄𝗶𝘁𝗵 𝘁𝗵𝗶𝘀 𝗺𝗲𝗻𝘂.", show_alert=True)
        return
    
    session['status'] = "STOPPED"
    await call.answer("Check stopped.")
    try:
        await call.message.edit_text(
            f"<b><tg-emoji emoji-id='5039895103947146186'>🌐</tg-emoji> 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➛</b> Stripe Auth 0$\n"
            f"<b><tg-emoji emoji-id='5231200819986047254'>📊</tg-emoji> 𝗦𝘁𝗮𝘁𝘂𝘀 ➛</b> <i>STOPPED</i> <tg-emoji emoji-id='5456140674028019486'>🛑</tg-emoji>\n"
            f"<b><tg-emoji emoji-id='5388632425314140043'>🔍</tg-emoji> 𝗖𝗵𝗲𝗰𝗸𝗲𝗱 ➛</b> <code>{session['cards_checked']}/{session['cards_total']}</code>\n"
            f"<b><tg-emoji emoji-id='5341715473882955310'>✅</tg-emoji> 𝗔𝗽𝗽𝗿𝗼𝘃𝗲𝗱 ➛</b> <b>{len(session['approved'])}</b>\n"
            f"<b><tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> 𝗗𝗲𝗮𝗱 ➛</b> <b>{len(session['dead'])}</b>\n"
            f"<b><tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> 𝗘𝗿𝗿𝗼𝗿𝘀 ➛</b> <b>{len(session['errors'])}</b>\n"
            f"<b><tg-emoji emoji-id='5406683434124859552'>🆔</tg-emoji> 𝗦𝗲𝘀𝘀𝗶𝗼𝗻 𝗜𝗗 ➛</b> <code>{session_id}</code>",
            parse_mode="HTML", reply_markup=get_mchk_buttons(session_id, False)
        )
    except: pass

@router.callback_query(MchkResultCallback.filter())
async def mchk_result_cb(call: types.CallbackQuery, callback_data: MchkResultCallback):
    session_id = callback_data.session_id
    res_type = callback_data.result_type
    session = MCHK_SESSIONS.get(session_id)
    if not session:
        await call.answer("Session expired.", show_alert=True)
        return
    if session['user_id'] != call.from_user.id:
        await call.answer("<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> 𝗬𝗼𝘂 𝗰𝗮𝗻𝗻𝗼𝘁 𝗶𝗻𝘁𝗲𝗿𝗮𝗰𝘁 𝘄𝗶𝘁𝗵 𝘁𝗵𝗶𝘀 𝗺𝗲𝗻𝘂.", show_alert=True)
        return
    
    data = session.get(res_type, [])
    if not data:
        await call.answer(f"No {res_type} cards found.", show_alert=True)
        return
    
    content = "\n".join([c[0] if isinstance(c, tuple) else c for c in data])
    if len(content) > 4000:
        content = content[:3900] + "\n...[Truncated]"
        
    await call.message.reply(f"<b>{res_type.upper()} CARDS:</b>\n<code>{content}</code>", parse_mode="HTML")
    await call.answer()
