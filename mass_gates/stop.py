import logging
import asyncio

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AIogram Imports
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from aiogram import types, F, Router

# Router for this module
router = Router()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# IMPORTS (SHARED STATE FROM GATE MODULES)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Import MAU (Mass Auth) Sessions and UI Updater
try:
    from mass_gates.mau import MAU_SESSIONS, update_progress_message as update_mau_message
except ImportError:
    logging.warning("[STOP.PY] Error importing from mau.py.")
    MAU_SESSIONS = {}
    update_mau_message = None

# Import MSH (Mass Shopify) Sessions and UI Updater
try:
    from mass_gates.msh import MSH_SESSIONS, update_progress_message as update_msh_message
except ImportError:
    logging.warning("[STOP.PY] Error importing from msh.py.")
    MSH_SESSIONS = {}
    update_msh_message = None

# Import MST (Mass Stripe) Sessions and UI Updater
try:
    from mass_gates.mst import MST_SESSIONS, update_progress_message as update_mst_message
except ImportError:
    logging.warning("[STOP.PY] Error importing from mst.py.")
    MST_SESSIONS = {}
    update_mst_message = None



@router.message(F.text.startswith("/stop"))
async def stop_command(message: types.Message):
    user_id = message.from_user.id
    
    # 1. Parse Arguments
    parts = message.text.split(maxsplit=1)
    
    if not parts or len(parts) < 2:
        await message.answer(
            "<tg-emoji emoji-id='4915853119839011973'>⚠️</tg-emoji> 𝗨𝘀𝗮𝗴𝗲 ➛ /stop <code>{session_id}</code>\n"
            "𝗙𝗶𝗻𝗱 𝘁𝗵𝗲 𝗦𝗲𝘀𝘀𝗶𝗼𝗻 𝗜𝗗 𝗶𝗻 𝘆𝗼𝘂𝗿 𝗽𝗿𝗼𝗴𝗿𝗲𝘀𝘀 𝗺𝗲𝘀𝘀𝗮𝗴𝗲.",
            parse_mode="HTML"
        )
        return

    session_id = parts[1].strip()

    # 2. Locate Session in all dictionaries
    session = MAU_SESSIONS.get(session_id)
    session_type = "MAU" 
    update_func = update_mau_message
    
    if not session:
        session = MSH_SESSIONS.get(session_id)
        session_type = "MSH"
        update_func = update_msh_message
    
    if not session:
        session = MST_SESSIONS.get(session_id)
        session_type = "MST"
        update_func = update_mst_message


    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 3. Check if session exists
    if not session:
        await message.answer(
            f"<tg-emoji emoji-id='5040030395416969985'>🚫</tg-emoji> 𝗦𝗲𝘀𝘀𝗶𝗼𝗻 𝗡𝗼𝘁 𝗙𝗼𝘂𝗻𝗱\n\n"
            f"𝗜𝗗 ➛ <code>{session_id}</code>\n"
            f"𝗥𝗲𝗮𝘀𝗼𝗻 ➛ 𝗘𝘅𝗽𝗶𝗿𝗲𝗱 𝗼𝗿 𝗜𝗻𝘃𝗮𝗹𝗶𝗱",
            parse_mode="HTML"
        )
        return

    # 4. Authorization Check
    if session['user_id'] != user_id:
        await message.answer(
            "<tg-emoji emoji-id='6237864166879663987'>❌</tg-emoji> 𝗔𝗰𝗰𝗲𝘀𝘀 𝗗𝗲𝗻𝗶𝗲𝗱\n\n"
            "𝗬𝗼𝘂 𝗰𝗮𝗻𝗻𝗼𝘁 𝘀𝘁𝗼𝗽 𝗮 𝘀𝗲𝘀𝘀𝗶𝗼𝗻 𝘀𝘁𝗮𝗿𝘁𝗲𝗱 𝗯𝘆 𝗮𝗻𝗼𝘁𝗵𝗲𝗿 𝘂𝘀𝗲𝗿.",
            parse_mode="HTML"
        )
        return

    # 5. Check if already stopped or finished
    if session['status'] in ["STOPPED", "FINISHED"]:
        status_icon = "<tg-emoji emoji-id='5456140674028019486'>🛑</tg-emoji>" if session['status'] == "STOPPED" else "<tg-emoji emoji-id='5039844895779455925'>🍾</tg-emoji>"
        await message.answer(
            f"<tg-emoji emoji-id='5456140674028019486'>🛑</tg-emoji> 𝗦𝗲𝘀𝘀𝗶𝗼𝗻 𝗔𝗹𝗿𝗲𝗮𝗱𝘆 {session['status']}\n\n"
            f"𝗜𝗗 ➛ <code>{session_id}</code>\n"
            f"<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> 𝗦𝘁𝗮𝘁𝘂𝘀 ➛ {session['status']} {status_icon}",
            parse_mode="HTML"
        )
        return

    # 6. SET STOPPED STATUS
    session['status'] = "STOPPED"

    # 7. INSTANTLY CANCEL ONGOING REQUESTS
    active_tasks = session.get('tasks', [])
    for task in active_tasks:
        if not task.done():
            task.cancel()

    # 8. Update the Progress Message immediately
    if update_func:
        try:
            # MST, MNMI, and MBL use (bot, session_id) signature
            # MAU and MSH traditionally use (message, session_id)
            if session_type in ["MST"]:
                await update_func(message.bot, session_id)
            else:
                await update_func(message, session_id)
        except Exception as e:
            logging.error(f"Error updating progress on stop: {e}")

    # 9. Confirmation Message
    await message.answer(
        f"<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> 𝗦𝘁𝗮𝘁𝘂𝘀 ➛ 𝗧𝗲𝗿𝗺𝗶𝗻𝗮𝘁𝗲𝗱 <tg-emoji emoji-id='5456140674028019486'>🛑</tg-emoji>\n\n"
        f"<tg-emoji emoji-id='5042050649248760772'>💎</tg-emoji> 𝗦𝗲𝘀𝘀𝗶𝗼𝗻 𝗜𝗗 ➛ <code>{session_id}</code>\n"
        f"𝗧𝘆𝗽𝗲 ➛ {session_type}\n"
        f"𝗧𝗮𝘀𝗸𝘀 ➛ 𝗖𝗮𝗻𝗰𝗲𝗹𝗹𝗲𝗱 𝗦𝘂𝗰𝗰𝗲𝘀𝘀𝗳𝘂𝗹𝗹𝘆",
        parse_mode="HTML"
    )
