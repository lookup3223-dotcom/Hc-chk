"""
proxy_pool.py — Owner-managed shared proxy pool for the /msh module.

Users never add proxies. The owner/admin sets them once (/proxyset) and every
user's session pulls from this pool automatically. Health is tracked globally:
a proxy that fails with a real proxy error sinks for everyone, not just for the
session that saw it.

Optional DB persistence:
    PROXY_POOL_BACKEND=db  +  table
    CREATE TABLE IF NOT EXISTS proxy_pool (
        proxy      TEXT PRIMARY KEY,
        added_by   BIGINT,
        ok_count   INT DEFAULT 0,
        fail_count INT DEFAULT 0,
        last_ok    TIMESTAMPTZ,
        last_fail  TIMESTAMPTZ,
        added_at   TIMESTAMPTZ DEFAULT NOW()
    );
Default backend is a file (proxy_pool.txt) next to this module.

Standalone smoke test:
    python proxy_pool.py
"""

import os
import time
import asyncio
import logging
import threading as _threading
from typing import List, Optional, Tuple
from datetime import datetime

from database import get_db_connection

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────

PROXY_POOL_BACKEND = (os.getenv("PROXY_POOL_BACKEND", "file") or "file").strip().lower()
PROXY_POOL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy_pool.txt")
PROXY_ADMIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy_admins.txt")
MAX_PROXIES_PER_SESSION = int(os.getenv("MAX_PROXIES_PER_SESSION", "150") or 150)

PROXY_ADMIN_OWNER = int(os.getenv("OWNER_ID", "0") or 0)
PROXY_ADMIN_GROUP_ID = int(os.getenv("PROXY_ADMIN_GROUP_ID", "0") or 0)

OWNER_IDS = set()
for _oid in (os.getenv("OWNER_IDS", "") or "").split(","):
    _oid = _oid.strip()
    if _oid.lstrip("-").isdigit():
        OWNER_IDS.add(int(_oid))
if PROXY_ADMIN_OWNER:
    OWNER_IDS.add(PROXY_ADMIN_OWNER)

PROXY_POOL = {}
_pool_lock = _threading.Lock()

DB_ADMIN_CACHE = {}
_DB_ADMIN_TTL = 300


# ──────────────────────────────────────────────────────────────────────────────
# NORMALIZER
# ──────────────────────────────────────────────────────────────────────────────

def normalize_proxy_line(proxy: str) -> Optional[str]:
    """Normalize every accepted proxy shape to http://user:pass@host:port."""
    if not proxy:
        return None
    proxy = proxy.strip()
    if not proxy:
        return None
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


# ──────────────────────────────────────────────────────────────────────────────
# ADMIN CHECK
# ──────────────────────────────────────────────────────────────────────────────

def _is_db_admin(user_id) -> bool:
    now = time.time()
    cached = DB_ADMIN_CACHE.get(user_id)
    if cached and (now - cached[1]) < _DB_ADMIN_TTL:
        return cached[0]
    is_admin = False
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT is_admin FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            is_admin = bool(row[0])
    except Exception:
        is_admin = False
    DB_ADMIN_CACHE[user_id] = (is_admin, now)
    return is_admin


def is_proxy_admin(user_id) -> bool:
    if not user_id:
        return False
    if user_id in OWNER_IDS:
        return True
    if PROXY_ADMIN_OWNER and user_id == PROXY_ADMIN_OWNER:
        return True
    return _is_db_admin(user_id)


async def _is_proxy_admin_async(user_id) -> bool:
    if user_id in OWNER_IDS or (PROXY_ADMIN_OWNER and user_id == PROXY_ADMIN_OWNER):
        return True
    return await asyncio.to_thread(is_proxy_admin, user_id)


def _load_proxy_admins():
    admins = set()
    try:
        if os.path.exists(PROXY_ADMIN_FILE):
            with open(PROXY_ADMIN_FILE, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line.lstrip("-").isdigit():
                        admins.add(int(line))
    except Exception as e:
        logging.error(f"[ProxyAdmin] Error reading proxy_admins.txt: {e}")
    return admins


def _save_proxy_admin(user_id):
    try:
        with open(PROXY_ADMIN_FILE, "a", encoding="utf-8") as f:
            f.write(f"{user_id}\n")
    except Exception as e:
        logging.error(f"[ProxyAdmin] Error writing proxy_admins.txt: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# BACKEND: load / save / mutate
# ──────────────────────────────────────────────────────────────────────────────

def _pool_row(proxy, added_by=None):
    return {
        'ok': 0,
        'fail': 0,
        'last_ok': None,
        'last_fail': None,
        'added_by': added_by,
        'added_at': time.time(),
    }


def _pool_load_backend():
    if PROXY_POOL_BACKEND == 'db':
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT proxy, ok_count, fail_count, last_ok, last_fail, added_by FROM proxy_pool"
            )
            rows = cursor.fetchall()
            conn.close()
            for row in rows:
                proxy = row[0]
                entry = _pool_row(proxy, row[5] if len(row) > 5 else None)
                entry['ok'] = row[1] or 0
                entry['fail'] = row[2] or 0
                entry['last_ok'] = row[3].timestamp() if row[3] else None
                entry['last_fail'] = row[4].timestamp() if row[4] else None
                PROXY_POOL[proxy] = entry
            logging.info(f"[ProxyPool] Loaded {len(PROXY_POOL)} proxies from database")
            return
        except Exception as e:
            logging.warning(f"[ProxyPool] DB load failed ({e}) — falling back to file")

    try:
        if os.path.exists(PROXY_POOL_FILE):
            with open(PROXY_POOL_FILE, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    proxy = normalize_proxy_line(line)
                    if proxy:
                        PROXY_POOL.setdefault(proxy, _pool_row())
            logging.info(f"[ProxyPool] Loaded {len(PROXY_POOL)} proxies from file")
    except Exception as e:
        logging.error(f"[ProxyPool] Error loading pool file: {e}")


def _pool_save_backend():
    if PROXY_POOL_BACKEND == 'db':
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM proxy_pool")
            for proxy, e in PROXY_POOL.items():
                cursor.execute(
                    "INSERT INTO proxy_pool (proxy, ok_count, fail_count, last_ok, last_fail, added_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (proxy) DO NOTHING",
                    (proxy, e['ok'], e['fail'],
                     datetime.fromtimestamp(e['last_ok']) if e.get('last_ok') else None,
                     datetime.fromtimestamp(e['last_fail']) if e.get('last_fail') else None,
                     e.get('added_by'))
                )
            conn.commit()
            conn.close()
            return
        except Exception as e:
            logging.warning(f"[ProxyPool] DB save failed ({e}) — falling back to file")

    try:
        with open(PROXY_POOL_FILE, "w", encoding="utf-8") as f:
            for proxy in PROXY_POOL:
                f.write(proxy + "\n")
    except Exception as e:
        logging.error(f"[ProxyPool] Error saving pool file: {e}")


def pool_add_proxies(text: str, added_by=None) -> Tuple[int, int]:
    added = 0
    skipped = 0
    with _pool_lock:
        for raw_line in (text or "").splitlines():
            proxy = normalize_proxy_line(raw_line)
            if not proxy:
                continue
            if proxy in PROXY_POOL:
                skipped += 1
                continue
            PROXY_POOL[proxy] = _pool_row(proxy, added_by)
            added += 1
        if added:
            _pool_save_backend()
    return added, skipped


def pool_remove_proxies(text: str) -> int:
    removed = 0
    with _pool_lock:
        for raw_line in (text or "").splitlines():
            proxy = normalize_proxy_line(raw_line)
            if proxy and proxy in PROXY_POOL:
                del PROXY_POOL[proxy]
                removed += 1
        if removed:
            _pool_save_backend()
    return removed


def pool_clear() -> int:
    with _pool_lock:
        count = len(PROXY_POOL)
        PROXY_POOL.clear()
        _pool_save_backend()
    return count


def pool_report(proxy: str, is_ok: bool):
    if not proxy:
        return
    with _pool_lock:
        entry = PROXY_POOL.get(proxy)
        if entry is None:
            return
        now = time.time()
        if is_ok:
            entry['ok'] += 1
            entry['last_ok'] = now
        else:
            entry['fail'] += 1
            entry['last_fail'] = now


# ──────────────────────────────────────────────────────────────────────────────
# SELECTION
# ──────────────────────────────────────────────────────────────────────────────

def _pool_health_score(entry) -> float:
    ok = entry.get('ok', 0)
    fail = entry.get('fail', 0)
    total = ok + fail
    if total == 0:
        return 0.5
    return (ok + 1) / (total + 2)          # Laplace-smoothed success rate


def get_pool_proxies(limit: int = None) -> List[str]:
    with _pool_lock:
        items = list(PROXY_POOL.items())

    if not items:
        return []

    now = time.time()

    def sort_key(item):
        proxy, entry = item
        score = _pool_health_score(entry)
        last_fail = entry.get('last_fail') or 0
        if last_fail and (now - last_fail) < 120:
            score -= 0.75                  # just-failed sinks for every user
        return score

    items.sort(key=sort_key, reverse=True)
    cap = limit or MAX_PROXIES_PER_SESSION
    cap = max(1, min(cap, len(items)))
    return [proxy for proxy, _ in items[:cap]]


def pool_stats() -> dict:
    with _pool_lock:
        items = list(PROXY_POOL.items())
    total = len(items)
    working = sum(1 for _, e in items if e.get('ok', 0) > 0)
    healthy = sum(1 for _, e in items if _pool_health_score(e) >= 0.5 and e.get('ok', 0) > 0)
    return {
        'total': total,
        'working': working,
        'healthy': healthy,
        'backend': PROXY_POOL_BACKEND,
    }


# ──────────────────────────────────────────────────────────────────────────────
# LOAD ON IMPORT
# ──────────────────────────────────────────────────────────────────────────────

_pool_load_backend()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(pool_stats())
    print(get_pool_proxies(5))
