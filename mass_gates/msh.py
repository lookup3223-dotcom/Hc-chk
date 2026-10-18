import asyncio
import aiohttp
import threading as _threading
import random
import re
import logging
import time
import string
import psycopg2.extras
import os
from datetime import datetime
from typing import Optional, Tuple, List
from io import BytesIO
from collections import deque
from html import escape as html_escape

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AIogram Imports
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
from proxypool import get_pool_proxies, pool_add_proxies, pool_remove_proxies, pool_clear, pool_report, pool_stats, normalize_proxy_line, is_proxy_admin, _is_proxy_admin_async, _pool_admin_ok, _pool_usage_text, PROXY_POOL_BACKEND, MAX_PROXIES_PER_SESSION
from sub import get_premium_status
