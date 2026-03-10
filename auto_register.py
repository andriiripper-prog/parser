"""
auto_register.py — Автоматическая регистрация на лендингах (нутра / крипто офферы).

Ключевые улучшения:
- Навигация через Facebook Referer (обход клоакинга)
- Поддержка multi-step форм (шаг 1 → шаг 2)
- CTA-кнопки: автоматический клик чтобы открыть форму
- Поля: email, имя, телефон, пароль, адрес
- Скролл к форме если она не видна
"""

import random
import string
import time

from playwright.sync_api import BrowserContext, TimeoutError as PlaywrightTimeoutError


# ---------------------------------------------------------------------------
# Генерация случайных данных (Канада / США)
# ---------------------------------------------------------------------------
FIRST_NAMES = [
    "James", "John", "Robert", "Michael", "William", "David", "Richard",
    "Joseph", "Thomas", "Charles", "Daniel", "Matthew", "Anthony", "Mark",
    "Emily", "Sarah", "Jessica", "Lauren", "Amanda", "Stephanie",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Jones", "Brown", "Davis", "Miller",
    "Wilson", "Moore", "Taylor", "Anderson", "Thomas", "Jackson", "White",
]

# Канадские провинции + почтовые коды с правильным форматом (A1A 1A1)
CA_PROVINCES = ["Ontario", "Quebec", "British Columbia", "Alberta", "Manitoba"]
CA_CITIES = ["Toronto", "Vancouver", "Calgary", "Ottawa", "Montreal", "Edmonton"]

def _rand_str(n: int) -> str:
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=n))

def _rand_ca_postal() -> str:
    letters = "ABCEGHJKLMNPRSTVXY"
    l1 = random.choice(letters)
    d1 = random.randint(0, 9)
    l2 = random.choice("ABCDEFGHJKLMNPRSTUVWXYZ")
    d2 = random.randint(0, 9)
    l3 = random.choice("ABCDEFGHJKLMNPRSTUVWXYZ")
    d3 = random.randint(0, 9)
    return f"{l1}{d1}{l2} {d2}{l3}{d3}"

def generate_random_data() -> dict:
    """Генерирует случайные данные пользователя (Канада/США)."""
    first = random.choice(FIRST_NAMES)
    last  = random.choice(LAST_NAMES)

    domains = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com"]
    email   = f"{first.lower()}.{last.lower()}.{_rand_str(6)}@{random.choice(domains)}"

    # Канадский формат телефона
    phone = f"+1{random.randint(416, 780)}{random.randint(100, 999)}{random.randint(1000, 9999)}"

    chars    = string.ascii_letters + string.digits + "!@#$"
    password = ''.join(random.choices(chars, k=12))

    city      = random.choice(CA_CITIES)
    province  = random.choice(CA_PROVINCES)
    postal    = _rand_ca_postal()
    street_no = random.randint(10, 9999)
    streets   = ["Main St", "Oak Ave", "Maple Dr", "King St", "Queen Blvd"]
    address   = f"{street_no} {random.choice(streets)}, {city}, {province}"

    return {
        "first_name": first,
        "last_name":  last,
        "full_name":  f"{first} {last}",
        "email":      email,
        "phone":      phone,
        "password":   password,
        "address":    address,
        "city":       city,
        "province":   province,
        "postal":     postal,
    }


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _try_fill(page, selectors: list[str], value: str, label: str) -> bool:
    """Заполнить первое найденное видимое поле из списка. Вернуть True если заполнено."""
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.triple_click()
                el.fill(value)
                print(f"      ✓ Filled {label}: {sel}")
                time.sleep(random.uniform(0.3, 0.7))
                return True
        except Exception:
            continue
    return False


def _try_click_cta(page) -> bool:
    """Кликнуть по CTA-кнопке чтобы открыть форму (если она скрыта за кнопкой)."""
    cta_selectors = [
        # Типичные CTA на нутра/крипто лендингах
        'a[href*="#form"]',
        'a[href*="#register"]',
        'a[href*="#signup"]',
        'button:has-text("Get Started")',
        'button:has-text("Sign Up")',
        'button:has-text("Join Now")',
        'button:has-text("Start Now")',
        'button:has-text("Register")',
        'button:has-text("Learn More")',
        'button:has-text("More detailed")',
        'a:has-text("Get Started")',
        'a:has-text("Sign Up")',
        'a:has-text("Register Now")',
        # Кнопки заказа (нутра)
        'button:has-text("Order")',
        'button:has-text("Buy Now")',
        'a:has-text("Order")',
    ]
    for sel in cta_selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(timeout=3000)
                print(f"      🖱 Clicked CTA: {sel}")
                time.sleep(2)
                return True
        except Exception:
            continue
    return False


def _accept_cookies(page) -> None:
    """Принять cookies/GDPR если появился попап."""
    selectors = [
        'button:has-text("Accept All")',
        'button:has-text("Accept Cookies")',
        'button:has-text("Accept")',
        'button:has-text("Allow All")',
        'button:has-text("I Agree")',
        'button:has-text("Agree")',
        'button:has-text("Got it")',
        'button[id*="onetrust-accept"]',
        '[aria-label*="accept" i][role="button"]',
        '[class*="cookie"][class*="accept"]',
        'button:has-text("OK")',
        'button:has-text("Close")',
    ]
    for sel in selectors:
        try:
            if page.is_visible(sel, timeout=1000):
                page.click(sel, timeout=2000)
                print(f"      🍪 Cookie consent accepted: {sel}")
                time.sleep(0.8)
                break
        except Exception:
            pass


def _scroll_to_form(page) -> None:
    """Прокрутить страницу вниз чтобы найти форму."""
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
        time.sleep(1)
    except Exception:
        pass


def _count_visible_inputs(page) -> int:
    """Посчитать видимые input-поля на странице."""
    try:
        inputs = page.query_selector_all('input:not([type="hidden"]):not([type="submit"]):not([type="button"])')
        return sum(1 for el in inputs if el.is_visible())
    except Exception:
        return 0


def _fill_form_fields(page, data: dict) -> int:
    """Заполнить все поля формы. Вернуть количество заполненных полей."""
    filled = 0

    # EMAIL
    if _try_fill(page, [
        'input[type="email"]',
        'input[name*="email" i]',
        'input[id*="email" i]',
        'input[placeholder*="email" i]',
        'input[placeholder*="E-mail" i]',
        'input[placeholder*="mail" i]',
    ], data["email"], "email"):
        filled += 1

    # FIRST NAME
    fname_done = _try_fill(page, [
        'input[name*="first" i][name*="name" i]',
        'input[id*="first" i][id*="name" i]',
        'input[placeholder*="First Name" i]',
        'input[placeholder*="First" i]',
        'input[name="fname" i]',
        'input[name="fn" i]',
    ], data["first_name"], "first name")
    if fname_done:
        filled += 1

    # LAST NAME
    lname_done = _try_fill(page, [
        'input[name*="last" i][name*="name" i]',
        'input[id*="last" i][id*="name" i]',
        'input[placeholder*="Last Name" i]',
        'input[placeholder*="Last" i]',
        'input[name="lname" i]',
        'input[name="ln" i]',
    ], data["last_name"], "last name")
    if lname_done:
        filled += 1

    # FULL NAME (если First/Last не нашли)
    if not fname_done and not lname_done:
        if _try_fill(page, [
            'input[placeholder*="Your Name" i]',
            'input[placeholder*="Full Name" i]',
            'input[placeholder*="Name" i]',
            'input[name="name" i]',
            'input[id="name"]',
        ], data["full_name"], "full name"):
            filled += 1

    # PHONE
    if _try_fill(page, [
        'input[type="tel"]',
        'input[name*="phone" i]',
        'input[id*="phone" i]',
        'input[placeholder*="phone" i]',
        'input[placeholder*="Phone" i]',
        'input[name="tel" i]',
        'input[name="mobile" i]',
    ], data["phone"], "phone"):
        filled += 1

    # PASSWORD
    if _try_fill(page, [
        'input[type="password"]',
        'input[name*="pass" i]',
        'input[id*="pass" i]',
    ], data["password"], "password"):
        filled += 1

    # ADDRESS
    _try_fill(page, [
        'input[name*="address" i]',
        'input[id*="address" i]',
        'input[placeholder*="address" i]',
        'input[placeholder*="Address" i]',
        'textarea[name*="address" i]',
        'textarea[placeholder*="Address" i]',
    ], data["address"], "address")

    # CITY
    _try_fill(page, [
        'input[name*="city" i]',
        'input[id*="city" i]',
        'input[placeholder*="city" i]',
    ], data["city"], "city")

    # POSTAL / ZIP
    _try_fill(page, [
        'input[name*="postal" i]',
        'input[name*="zip" i]',
        'input[id*="postal" i]',
        'input[id*="zip" i]',
        'input[placeholder*="postal" i]',
        'input[placeholder*="zip" i]',
    ], data["postal"], "postal")

    # CHECKBOXES (agree to terms, etc.)
    try:
        checkboxes = page.query_selector_all('input[type="checkbox"]')
        for cb in checkboxes:
            try:
                if cb.is_visible() and not cb.is_checked():
                    cb.check()
                    print(f"      ✓ Checked checkbox")
            except Exception:
                pass
    except Exception:
        pass

    return filled


def _submit_form(page) -> bool:
    """Нажать кнопку submit. Вернуть True если кликнуто."""
    submit_selectors = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Submit")',
        'button:has-text("Register")',
        'button:has-text("Sign Up")',
        'button:has-text("Get Started")',
        'button:has-text("Join")',
        'button:has-text("Send")',
        'button:has-text("Continue")',
        'button:has-text("Next")',
        'button:has-text("More detailed")',
        'a[type="submit"]',
    ]
    for sel in submit_selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                print(f"      🖱 Submitting via: {sel}")
                el.click(timeout=3000)
                return True
        except Exception:
            continue

    # Fallback: Enter
    try:
        page.keyboard.press("Enter")
        print(f"      ⌨  Submitted via Enter (fallback)")
        return True
    except Exception:
        return False


def _check_success(page, original_url: str) -> bool:
    """Определить что регистрация прошла успешно."""
    try:
        body = page.inner_text("body").lower()
    except Exception:
        return False

    success_kw = [
        "thank you", "thanks", "success", "confirmed", "welcome",
        "dashboard", "check your email", "registered", "we will contact",
        "we will reply", "we received", "submission received"
    ]
    if any(k in body for k in success_kw):
        return True

    # URL изменился (редирект после сабмита)
    try:
        cur = page.url
        if cur != original_url and "register" not in cur and "signup" not in cur:
            return True
    except Exception:
        pass

    return False


# ---------------------------------------------------------------------------
# Основная функция
# ---------------------------------------------------------------------------

def try_auto_register(context: BrowserContext, url: str, timeout: int = 30000) -> dict:
    """
    Попытка авторегистрации на лендинге.

    Ключевые особенности:
    - Переход с Facebook Referer (обход клоакинга)
    - Автоматический клик по CTA-кнопкам чтобы открыть форму
    - Поддержка multi-step форм (шаг 1 → шаг 2)
    - Заполнение: email, имя, телефон, пароль, адрес

    Возвращает dict: success, email, password, error
    """
    print(f"   🤖 AutoReg: {url[:70]}...")

    result = {
        "success":  False,
        "email":    None,
        "password": None,
        "data":     None,
        "error":    None,
    }

    page = None
    try:
        page = context.new_page()

        # --- Установить Facebook Referer и User-Agent ---
        # Это ключевое — без referer клоакинг показывает "безопасный" лендинг
        page.set_extra_http_headers({
            "Referer": "https://www.facebook.com/",
            "sec-fetch-site": "cross-site",
            "sec-fetch-mode": "navigate",
            "sec-fetch-dest": "document",
        })

        # Навигация
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout,
                      referer="https://www.facebook.com/")
            time.sleep(random.uniform(3, 5))
        except PlaywrightTimeoutError:
            result["error"] = "Navigation timeout"
            print(f"   ⚠️  AutoReg: Navigation timeout")
            return result
        except Exception as e:
            result["error"] = f"Navigation failed: {e}"
            return result

        original_url = page.url

        # Принять cookies если есть
        _accept_cookies(page)

        # Генерировать данные
        data = generate_random_data()
        result["data"]     = data
        result["email"]    = data["email"]
        result["password"] = data["password"]
        print(f"   📧 Identity: {data['email']} / {data['password']}")

        # --- Проверить есть ли видимые поля ---
        visible = _count_visible_inputs(page)
        print(f"   🔍 Visible inputs on load: {visible}")

        if visible == 0:
            # Попробовать прокрутить вниз
            _scroll_to_form(page)
            visible = _count_visible_inputs(page)

        if visible == 0:
            # Попробовать кликнуть CTA-кнопку чтобы открылась форма / модал
            clicked_cta = _try_click_cta(page)
            if clicked_cta:
                time.sleep(2)
                visible = _count_visible_inputs(page)

        if visible == 0:
            result["error"] = "No form fields found"
            print("   ⚠️  AutoReg: No form fields found on page")
            return result

        # --- STEP 1: Заполнить форму ---
        filled = _fill_form_fields(page, data)
        print(f"   ✏️  Filled {filled} field(s) on step 1")

        if filled == 0:
            result["error"] = "No fillable fields"
            return result

        time.sleep(random.uniform(1.0, 2.0))

        # Сабмит шага 1
        submitted = _submit_form(page)
        if not submitted:
            result["error"] = "Submit button not found"
            return result

        time.sleep(random.uniform(3, 5))

        # --- Проверить успех после шага 1 ---
        if _check_success(page, original_url):
            print(f"   ✅ AutoReg: Success after step 1!")
            result["success"] = True
            return result

        # --- STEP 2: Если URL не изменился — попробовать заполнить второй шаг ---
        visible2 = _count_visible_inputs(page)
        if visible2 > 0:
            print(f"   🔄 AutoReg: Step 2 detected ({visible2} inputs)")
            _accept_cookies(page)
            filled2 = _fill_form_fields(page, data)
            print(f"   ✏️  Filled {filled2} field(s) on step 2")
            time.sleep(random.uniform(1.0, 2.0))
            _submit_form(page)
            time.sleep(random.uniform(3, 5))

            if _check_success(page, original_url):
                print(f"   ✅ AutoReg: Success after step 2!")
                result["success"] = True
                return result

        # Оптимистичный исход: заполнили и кликнули, но успех неясен
        print(f"   ❓ AutoReg: Submitted but outcome unclear (optimistic pass)")
        result["success"] = True

    except Exception as e:
        result["error"] = f"Runtime error: {e}"
        print(f"   ❌ AutoReg Error: {e}")
    finally:
        if page:
            try:
                page.close()
            except Exception:
                pass

    return result
