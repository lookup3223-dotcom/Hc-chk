from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

router = Router()

# Each page will be padded to this many entry slots so all pages are the same height.
ENTRIES_PER_PAGE = 6

PAGES = [
    {
        "title": "Auth Gates",
        "entries": [
            {"gate": "Stripe Auth 0$",     "cmd": "/chk",  "limit": None,   "premium": False},
            {"gate": "Braintree 0$",       "cmd": "/b3",   "limit": None,   "premium": False},
            {"gate": "Braintree VBV 0$",   "cmd": "/vbv",  "limit": None,   "premium": False},
        ],
    },
    {
        "title": "Charge Gates",
        "entries": [
            {"gate": "Stripe 1$",          "cmd": "/st",   "limit": None,   "premium": False},
            {"gate": "PayPal 0.10$",       "cmd": "/pp",   "limit": None,   "premium": False},
            {"gate": "Shopify 5$",         "cmd": "/hc",   "limit": None,   "premium": False},
            {"gate": "Shopify 1$",         "cmd": "/sp",   "limit": None,   "premium": False},
            {"gate": "Razorpay 1₹",        "cmd": "/rz",   "limit": None,   "premium": False},
        ],
    },
    {
        "title": "Mass Gates",
        "entries": [
            {"gate": "Stripe Mass 0$",     "cmd": "/mchk", "limit": "20",   "premium": True},
            {"gate": "Shopify Mass 0-20$", "cmd": "/msh",  "limit": "10000", "premium": True},
            {"gate": "Stripe Mass 1$",     "cmd": "/mst",  "limit": "2000", "premium": True},
            {"gate": "Shopify Mass 1$",    "cmd": "/sh",   "limit": "50",   "premium": False},
            {"gate": "Razorpay Mass",      "cmd": "/mrz",  "limit": "5000", "premium": True},
            {"gate": "Stripe Multi Mass",  "cmd": "/mstr", "limit": "2000", "premium": True},
        ],
    },
    {
        "title": "Tools",
        "entries": [
            {"gate": "BIN Lookup",         "cmd": "/bin",        "limit": None, "premium": False},
            {"gate": "Set Proxy",          "cmd": "/proxy",      "limit": None, "premium": False},
            {"gate": "Check Proxy",        "cmd": "/checkproxy", "limit": None, "premium": False},
            {"gate": "Clear Proxy",        "cmd": "/clearproxy", "limit": None, "premium": False},
        ],
    },
    {
        "title": "Account",
        "entries": [
            {"gate": "Statistics",         "cmd": "/stats",  "limit": None, "premium": False},
            {"gate": "Account Info",       "cmd": "/info",   "limit": None, "premium": False},
            {"gate": "Buy Plan",           "cmd": "/buy",    "limit": None, "premium": False},
            {"gate": "Claim Code",         "cmd": "/claim",  "limit": None, "premium": False},
            {"gate": "Give Feedback",      "cmd": "/fb",     "limit": None, "premium": False},
        ],
    },
]

TOTAL_PAGES = len(PAGES)
_SEP = "━━━━━━━━━━━━━━━━"
_SP  = "\u00a0"  # non-breaking space used as invisible filler line

def _build_text(i: int) -> str:
    page = PAGES[i]
    lines = [_SEP, f"<b>{page['title']}  ·  {i + 1} / {TOTAL_PAGES}</b>", _SEP]
    for e in page["entries"]:
        lines.append(f"<b>𝗚𝗮𝘁𝗲  ➛  {e['gate']}</b>")
        lines.append(f"<b>𝗖𝗺𝗱   ➛  {e['cmd']}</b>")
        if e["limit"] is not None:
            lines.append(f"<b>𝗟𝗶𝗺𝗶𝘁 ➛  {e['limit']}</b>")
        lines.append(f"<b>𝗧𝘆𝗽𝗲  ➛  {'𝗣𝗿𝗲𝗺𝗶𝘂𝗺' if e['premium'] else '𝗙𝗿𝗲𝗲'}</b>")
        lines.append(_SEP)
    # Pad shorter pages so every page has the same visual height
    pad = ENTRIES_PER_PAGE - len(page["entries"])
    for _ in range(pad):
        lines.append(f"<b>{_SP}</b>")
        lines.append(f"<b>{_SP}</b>")
        lines.append(f"<b>{_SP}</b>")
        lines.append(_SEP)
    return "\n".join(lines)

def _build_kb(i: int) -> InlineKeyboardMarkup:
    nav = []
    if i > 0:
        nav.append(InlineKeyboardButton(text="« Prev", callback_data=f"cmds_page_{i - 1}", icon_custom_emoji_id="5039753786638205957"))
    nav.append(InlineKeyboardButton(text=f"{i + 1} / {TOTAL_PAGES}", callback_data="cmds_noop"))
    if i < TOTAL_PAGES - 1:
        nav.append(InlineKeyboardButton(text="Next »", callback_data=f"cmds_page_{i + 1}", icon_custom_emoji_id="5042020176455795565"))
    return InlineKeyboardMarkup(inline_keyboard=[nav])

# All texts and keyboards built once at import — zero runtime cost per button press
_CACHE: list[tuple[str, InlineKeyboardMarkup]] = [
    (_build_text(i), _build_kb(i)) for i in range(TOTAL_PAGES)
]

@router.message(Command("cmds"))
async def cmds_command(message: types.Message):
    text, kb = _CACHE[0]
    await message.reply(text=text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

@router.callback_query(F.data.startswith("cmds_page_"))
async def cmds_page_callback(callback: types.CallbackQuery):
    await callback.answer()
    try:
        idx = int(callback.data[10:])
    except ValueError:
        return
    if not (0 <= idx < TOTAL_PAGES):
        return
    text, kb = _CACHE[idx]
    try:
        await callback.message.edit_text(text=text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        pass

@router.callback_query(F.data == "cmds_noop")
async def cmds_noop(callback: types.CallbackQuery):
    await callback.answer()
