"""
manager.py — Multi-Account Manager with Telegram Inline Keyboard

Flow:
  /start → Главное меню: выбор ГЕО + Support
  [🇨🇦 Canada] → Список аккаунтов Canada + кнопки Start/Stop per account
  [▶️ Запустить все] → Запускает до 2 аккаунтов для ГЕО
  [🔄 Обновить] → Обновляет сообщение с текущим статусом
  [◀️ Назад] → Возврат в главное меню
  Ротация: каждое ГЕО крутит до max_concurrent аккаунтов независимо.
  Если в ГЕО остался 1 аккаунт — крутится один, не захватывает слоты другого ГЕО.
"""

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import telebot
from telebot import types
import yaml
from dotenv import load_dotenv

from adspower import stop_browser

# ─────────────────────────── Config ────────────────────────────────────────

CONFIG_FILE = "accounts.yaml"
ROTATION_STATE_FILE = "rotation_state.json"
PYTHON_INTERPRETER = sys.executable

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Разрешённые админы (из запроса пользователя)
ADMIN_IDS = {"8281455914", "5667795591"}

# Если в .env задан дополнительный личный ID — добавляем
env_admin = str(os.getenv("TELEGRAM_ADMIN_ID", "")).strip()
if env_admin:
    ADMIN_IDS.add(env_admin)

ADS_POWER_URL = os.getenv("ADS_POWER_URL", "http://127.0.0.1:50325")

bot = telebot.TeleBot(BOT_TOKEN) if BOT_TOKEN else None

# ГЕО конфигурация
GEOS = {
    "canada":  {"flag": "🇨🇦", "label": "Canada"},
    "turkey":  {"flag": "🇹🇷", "label": "Turkey"},
    "belgium": {"flag": "🇧🇪", "label": "Belgium"},
}

# ─────────────────────────── Helpers ───────────────────────────────────────

def load_accounts():
    if not os.path.exists(CONFIG_FILE):
        return [], {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("accounts", []), data.get("settings", {})


def load_rotation_state():
    if not os.path.exists(ROTATION_STATE_FILE):
        return {}
    try:
        with open(ROTATION_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_rotation_state(state):
    try:
        with open(ROTATION_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"⚠️ save_rotation_state: {e}")


def fmt_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h}h{m}m"
    if m:
        return f"{m}m"
    return f"{s}s"


def is_allowed(message_or_call) -> bool:
    """Проверяет что команда идёт от разрешённого пользователя."""
    # Достаём user_id
    if hasattr(message_or_call, "from_user"):
        uid = message_or_call.from_user.id if message_or_call.from_user else None
    elif hasattr(message_or_call, "from"):
        uid = getattr(message_or_call, "from", {}).get("id") if isinstance(getattr(message_or_call, "from", None), dict) else None
    else:
        uid = None

    if uid is None and hasattr(message_or_call, "chat"):
        fu = getattr(message_or_call, "from_user", None)
        uid = fu.id if fu else None
        
    if not ADMIN_IDS:
        return True
        
    return str(uid) in ADMIN_IDS


def send_tg(text: str):
    """Отправить алерт админу, который запустил парсер."""
    if not bot or not active_admin_id:
        return
    try:
        bot.send_message(active_admin_id, text)
    except Exception as e:
        print(f"⚠️ TG send error: {e}")


# ─────────────────────────── AccountProcess ────────────────────────────────

class AccountProcess:
    def __init__(self, account_config: dict):
        self.config = account_config
        self.id = account_config["id"]
        self.type = account_config.get("type", "desktop")
        self.geo = account_config.get("geo", "canada")
        self.process = None
        self.start_time: datetime | None = None
        self.session_deadline: datetime | None = None
        self.log_file = None

    def start(self, session_minutes: int = 120) -> bool:
        script = "mobile_main.py" if self.type == "mobile" else "main.py"
        args = [PYTHON_INTERPRETER, script, "--profile", self.id]
        if self.type == "mobile":
            args.append("--mobile")

        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        try:
            self.log_file = open(log_dir / f"{self.id}.log", "a", encoding="utf-8")
        except Exception:
            self.log_file = None

        print(f"🚀 Starting {self.id} ({self.type}) → {' '.join(args)}")
        try:
            cwd = str(Path(__file__).parent)
            self.process = subprocess.Popen(
                args,
                stdout=self.log_file,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                env=os.environ.copy(),
            )
            self.start_time = datetime.now()
            self.session_deadline = self.start_time + timedelta(minutes=session_minutes)
            return True
        except Exception as e:
            print(f"❌ Failed to start {self.id}: {e}")
            return False

    def is_running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    def session_expired(self) -> bool:
        if self.session_deadline is None:
            return False
        return datetime.now() >= self.session_deadline

    def uptime(self) -> str:
        if not self.start_time:
            return "—"
        return fmt_duration((datetime.now() - self.start_time).total_seconds())

    def time_left(self) -> str:
        if not self.session_deadline:
            return "—"
        remaining = (self.session_deadline - datetime.now()).total_seconds()
        if remaining <= 0:
            return "expired"
        return fmt_duration(remaining)

    def stop(self):
        if self.process and self.is_running():
            print(f"🛑 Stopping {self.id}...")
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()

        if self.log_file:
            try:
                self.log_file.close()
            except Exception:
                pass

        try:
            ok = stop_browser(ADS_POWER_URL, self.id)
            if ok:
                print(f"🔒 Browser closed: {self.id}")
        except Exception as e:
            print(f"⚠️ stop_browser {self.id}: {e}")


# ─────────────────────────── Global State ──────────────────────────────────
# Глобальное состояние
active: dict[str, AccountProcess] = {}
pinned: set[str] = set()
# Множество ГЕО, которые ОСТАНОВЛЕНЫ (не запущены через TG). По умолчанию — все.
# Если гео НЕ в этом множестве — оно активно и ротируется.
paused_geos: set[str] = set(GEOS.keys())  # изначально все остановлены
# ID админа, который последним нажал "Запустить" — ему будут идти уведомления
active_admin_id: str | None = None
lock = threading.Lock()


# ─────────────────────────── Keyboards ─────────────────────────────────────

def main_menu_kb() -> types.ReplyKeyboardMarkup:
    """Главное меню — выбор ГЕО (Reply Keyboard)"""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    geo_buttons = []
    for geo_key, geo_info in GEOS.items():
        geo_buttons.append(
            types.KeyboardButton(f"{geo_info['flag']} {geo_info['label']}")
        )
    kb.add(*geo_buttons)
    kb.add(types.KeyboardButton("🆘 Support"))
    return kb


def geo_menu_kb(geo_key: str) -> types.InlineKeyboardMarkup:
    """Меню управления конкретным ГЕО (упрощённое)"""
    kb = types.InlineKeyboardMarkup(row_width=2)

    # Топ-ряд: Запустить + Остановить (для всего ГЕО)
    kb.add(
        types.InlineKeyboardButton(
            "▶️ Запустить",
            callback_data=f"run_all:{geo_key}"
        ),
        types.InlineKeyboardButton(
            "⏹ Остановить",
            callback_data=f"stop_all:{geo_key}"
        ),
    )

    # Нижний ряд: Обновить + Закрыть
    kb.add(
        types.InlineKeyboardButton("🔄 Обновить", callback_data=f"refresh:{geo_key}"),
        types.InlineKeyboardButton("❌ Закрыть", callback_data="close_msg"),
    )
    return kb


def main_menu_text() -> str:
    return "🌍 *Выбери ГЕО:*"


def geo_menu_text(geo_key: str) -> str:
    accounts, settings = load_accounts()
    geo_info = GEOS.get(geo_key, {"flag": "🌍", "label": geo_key.title()})
    geo_accounts = [a for a in accounts if a.get("geo") == geo_key]
    max_c = settings.get("max_concurrent_browsers", 2)

    running_count = 0
    with lock:
        for acc in geo_accounts:
            proc = active.get(acc["id"])
            if proc and proc.is_running():
                running_count += 1
                
    geo_paused = geo_key in paused_geos
    if running_count > 0:
        status_text = "🟢 *Активно*"
    elif not geo_paused:
        status_text = "🟡 *Запускается...*"
    else:
        status_text = "🔴 *Неактивно*"

    return (
        f"{geo_info['flag']} *{geo_info['label']}* — аккаунты\n\n"
        f"{status_text}"
    )


# ─────────────────────────── TG Handlers ───────────────────────────────────

@bot.message_handler(commands=["myid"])
def cmd_myid(message):
    """Показывает user_id — добавь его в .env как TELEGRAM_ADMIN_ID"""
    uid = message.from_user.id if message.from_user else "?"
    cid = message.chat.id
    bot.reply_to(
        message,
        f"👤 Твой *User ID*: `{uid}`\n"
        f"💬 Chat ID: `{cid}`\n\n"
        f"Добавь в `.env`:\n`TELEGRAM_ADMIN_ID={uid}`",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=["start", "help", "menu"])
def cmd_start(message):
    if not is_allowed(message):
        bot.reply_to(message, f"⛔ Нет доступа. Твой ID: `{message.from_user.id}`\nДобавь в .env: `TELEGRAM_ADMIN_ID={message.from_user.id}`", parse_mode="Markdown")
        return
    bot.send_message(
        message.chat.id,
        main_menu_text(),
        parse_mode="Markdown",
        reply_markup=main_menu_kb()
    )


@bot.message_handler(func=lambda message: True)
def text_commands_handler(message):
    """Обработчик текстовых кнопок из ReplyKeyboardMarkup"""
    if not is_allowed(message):
        return

    text = message.text

    # Ищем нажатое ГЕО
    for geo_key, geo_info in GEOS.items():
        if text == f"{geo_info['flag']} {geo_info['label']}":
            bot.send_message(
                message.chat.id,
                geo_menu_text(geo_key),
                parse_mode="Markdown",
                reply_markup=geo_menu_kb(geo_key)
            )
            return

    if text == "🆘 Support":
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("❌ Закрыть", callback_data="close_msg"))
        bot.send_message(
            message.chat.id,
            "🆘 *Support*\n\nВ разработке.",
            parse_mode="Markdown",
            reply_markup=kb
        )
        return

    # Если нажата неизвестная кнопка — показываем меню
    bot.send_message(
        message.chat.id,
        main_menu_text(),
        parse_mode="Markdown",
        reply_markup=main_menu_kb()
    )

def cmd_status(message):
    if not is_allowed(message):
        return
    accounts, settings = load_accounts()
    lines = ["📊 *Статус всех аккаунтов:*\n"]
    with lock:
        for acc in accounts:
            aid = acc["id"]
            geo = acc.get("geo", "?")
            geo_info = GEOS.get(geo, {"flag": "🌍", "label": geo})
            icon = "📱" if acc.get("type") == "mobile" else "🖥"
            proc = active.get(aid)
            if proc and proc.is_running():
                lines.append(f"{icon} {geo_info['flag']} `{aid}` 🟢 {proc.uptime()} / ⏱{proc.time_left()}")
            else:
                lines.append(f"{icon} {geo_info['flag']} `{aid}` 🔴")
    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")


@bot.message_handler(commands=["stop"])
def cmd_stop_all(message):
    if not is_allowed(message):
        return
    # Останавливаем все гео
    paused_geos.update(GEOS.keys())
    stopped = []
    with lock:
        for aid, proc in list(active.items()):
            proc.stop()
            stopped.append(aid)
        active.clear()
        pinned.clear()
    bot.reply_to(
        message,
        f"⏹ Остановлено: {', '.join(f'`{i}`' for i in stopped) or 'ничего не работало'}\n"
        "⏸ Авторотация остановлена. `/menu` чтобы вернуться.",
        parse_mode="Markdown"
    )


# ─────────────────────────── Callback Handlers ─────────────────────────────

@bot.callback_query_handler(func=lambda c: True)
def callback_handler(call: types.CallbackQuery):
    if not is_allowed(call):
        bot.answer_callback_query(call.id, "⛔ Нет доступа")
        return

    global active_admin_id
    data = call.data
    accounts, settings = load_accounts()
    max_c = settings.get("max_concurrent_browsers", 2)
    session_min = settings.get("session_duration_minutes", 120)

    # ── geo:canada / geo:turkey ──────────────────────────────────────────
    if data.startswith("geo:"):
        geo_key = data.split(":", 1)[1]
        bot.edit_message_text(
            geo_menu_text(geo_key),
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=geo_menu_kb(geo_key)
        )
        bot.answer_callback_query(call.id)

    # ── refresh:canada ───────────────────────────────────────────────────
    elif data.startswith("refresh:"):
        geo_key = data.split(":", 1)[1]
        try:
            bot.edit_message_text(
                geo_menu_text(geo_key),
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=geo_menu_kb(geo_key)
            )
            bot.answer_callback_query(call.id, "🔄 Обновлено")
        except Exception:
            bot.answer_callback_query(call.id, "Уже актуально")

    # ── back:main / close_msg ────────────────────────────────────────────
    elif data == "back:main" or data == "close_msg":
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            try:
                bot.edit_message_text(
                    "Закрыто.",
                    call.message.chat.id,
                    call.message.message_id
                )
            except Exception:
                pass
        bot.answer_callback_query(call.id)

    # ── run_all:canada ───────────────────────────────────────────────────
    elif data.startswith("run_all:"):
        geo_key = data.split(":", 1)[1]

        # Убираем гео из «остановленных» — включаем авторотацию для него
        paused_geos.discard(geo_key)

        # Запоминаем ID админа, который нажал кнопку (ему пойдут уведомления)
        active_admin_id = str(call.from_user.id)

        geo_accounts = [a for a in accounts if a.get("geo") == geo_key and a.get("enabled")]
        started = []
        skipped = []

        with lock:
            rot_state = load_rotation_state()
            # Считаем сколько уже запущено для этого гео
            running_in_geo = sum(
                1 for aid, proc in active.items()
                if proc.is_running() and
                any(a["id"] == aid and a.get("geo") == geo_key for a in accounts)
            )
            # Сортируем по времени последнего запуска (самые давние — первые)
            candidates = sorted(
                [a for a in geo_accounts if a["id"] not in active],
                key=lambda a: rot_state.get(a["id"], 0)
            )
            for acc in candidates:
                if running_in_geo >= max_c:
                    skipped.append(acc["id"])
                    continue
                proc = AccountProcess(acc)
                if proc.start(session_min):
                    active[acc["id"]] = proc
                    rot_state[acc["id"]] = time.time()
                    started.append(acc["id"])
                    running_in_geo += 1
            save_rotation_state(rot_state)

        if started:
            bot.answer_callback_query(call.id, "▶️ Запущено")
        elif skipped:
            bot.answer_callback_query(call.id, f"⚠️ Лимит {max_c} для {geo_key}")
        else:
            bot.answer_callback_query(call.id, "ℹ️ Все уже запущены")
        # Обновляем меню
        try:
            bot.edit_message_text(
                geo_menu_text(geo_key),
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=geo_menu_kb(geo_key)
            )
        except Exception:
            pass

    # ── stop_all:canada ──────────────────────────────────────────────────
    elif data.startswith("stop_all:"):
        geo_key = data.split(":", 1)[1]
        # Добавляем гео в «остановленные» — отключаем авторотацию для него
        paused_geos.add(geo_key)
        geo_accounts = [a for a in accounts if a.get("geo") == geo_key]
        geo_ids = {a["id"] for a in geo_accounts}
        
        # Сначала отвечаем телеграму, чтобы избежать TimeOut Error (Bad Request: query is too old)
        bot.answer_callback_query(call.id, "⏹ Остановлено")

        stopped = []
        with lock:
            for aid in list(active.keys()):
                if aid in geo_ids:
                    active[aid].stop()
                    del active[aid]
                    pinned.discard(aid)
                    stopped.append(aid)

        try:
            bot.edit_message_text(
                geo_menu_text(geo_key),
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=geo_menu_kb(geo_key)
            )
        except Exception:
            pass

    # ── run_one:account_id ───────────────────────────────────────────────
    elif data.startswith("run_one:"):
        aid = data.split(":", 1)[1]
        target = next((a for a in accounts if a["id"] == aid), None)
        if not target:
            bot.answer_callback_query(call.id, "❌ Аккаунт не найден")
            return

        geo_key = target.get("geo", "canada")

        with lock:
            if aid in active and active[aid].is_running():
                bot.answer_callback_query(call.id, f"⚠️ {aid} уже работает")
                return
            if len(active) >= max_c:
                bot.answer_callback_query(call.id, f"⚠️ Лимит {max_c} аккаунтов. Сначала останови один.")
                return

            proc = AccountProcess(target)
            if proc.start(session_min):
                active[aid] = proc
                pinned.add(aid)
                rot_state = load_rotation_state()
                rot_state[aid] = time.time()
                save_rotation_state(rot_state)
                bot.answer_callback_query(call.id, "▶️ Запущено")
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка запуска")
                return

        try:
            bot.edit_message_text(
                geo_menu_text(geo_key),
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=geo_menu_kb(geo_key)
            )
        except Exception:
            pass

    # ── stop_one:account_id ──────────────────────────────────────────────
    elif data.startswith("stop_one:"):
        aid = data.split(":", 1)[1]
        target = next((a for a in accounts if a["id"] == aid), None)
        geo_key = target.get("geo", "canada") if target else "canada"

        bot.answer_callback_query(call.id, "⏹ Остановлено")

        with lock:
            proc = active.get(aid)
            if proc:
                proc.stop()
                del active[aid]
                pinned.discard(aid)

        try:
            bot.edit_message_text(
                geo_menu_text(geo_key),
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=geo_menu_kb(geo_key)
            )
        except Exception:
            pass

    # ── support (inline) ─────────────────────────────────────────────────
    elif data == "support":
        bot.answer_callback_query(call.id, "🆘 Support — скоро...")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("❌ Закрыть", callback_data="close_msg"))
        try:
            bot.edit_message_text(
                "🆘 *Support*\n\nРаздел в разработке. Скоро здесь будет помощь.",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=kb
            )
        except Exception:
            pass

    # ── noop (клик по инфо-кнопке аккаунта) ────────────────────────────
    elif data.startswith("noop:"):
        bot.answer_callback_query(call.id)

    else:
        bot.answer_callback_query(call.id, "❓ Неизвестная команда")


# ─────────────────────────── Rotation Loop ─────────────────────────────────

def rotation_loop():
    while True:
        try:
            accounts, settings = load_accounts()
            max_c = settings.get("max_concurrent_browsers", 2)
            session_min = settings.get("session_duration_minutes", 120)

            with lock:
                # Удаляем упавшие процессы
                for aid in list(active.keys()):
                    proc = active[aid]
                    if not proc.is_running():
                        print(f"⚠️ {aid} завершился. код: {proc.process.returncode}")
                        proc.stop()
                        del active[aid]
                        pinned.discard(aid)
                        send_tg(f"⚠️ `{aid}` завершился неожиданно")

                # Ротация истёкших сессий (не pinned) — только для активных ГЕО
                for aid in list(active.keys()):
                    proc = active[aid]
                    if aid in pinned:
                        continue
                    if proc.session_expired():
                        # Проверяем, не остановлено ли это ГЕО — если да, не трогаем
                        acc_geo = next(
                            (a.get("geo") for a in accounts if a["id"] == aid), None
                        )
                        if acc_geo and acc_geo in paused_geos:
                            continue  # ГЕО остановлено — не ротируем
                        print(f"⏰ Сессия {aid} истекла — ротируем")
                        proc.stop()
                        del active[aid]

                # ── Дозаполняем слоты для каждого НЕ-остановленного ГЕО ───────
                # Собираем, сколько аккаунтов сейчас активно в каждом гео
                geo_active_count: dict[str, int] = {}
                for aid in active:
                    for a in accounts:
                        if a["id"] == aid:
                            g = a.get("geo", "")
                            geo_active_count[g] = geo_active_count.get(g, 0) + 1
                            break

                # Перебираем все ГЕО, которые разрешены (не в paused_geos)
                rot_state = load_rotation_state()
                added = []

                for geo in GEOS:
                    if geo in paused_geos:
                        continue  # ГЕО остановлено — пропускаем

                    running_in_geo = geo_active_count.get(geo, 0)
                    if running_in_geo >= max_c:
                        continue  # слоты уже заняты

                    # Кандидаты: аккаунты этого гео, не запущенные, enabled
                    candidates = sorted(
                        [
                            a for a in accounts
                            if a.get("enabled")
                            and a.get("geo") == geo
                            and a["id"] not in active
                        ],
                        key=lambda a: rot_state.get(a["id"], 0),
                    )

                    for acc in candidates:
                        if running_in_geo >= max_c:
                            break
                        proc = AccountProcess(acc)
                        if proc.start(session_min):
                            active[acc["id"]] = proc
                            rot_state[acc["id"]] = time.time()
                            added.append(acc["id"])
                            running_in_geo += 1

                if added:
                    save_rotation_state(rot_state)

        except Exception as e:
            print(f"❌ rotation_loop error: {e}")

        time.sleep(settings.get("check_interval", 30) if 'settings' in dir() else 30)


# ─────────────────────────── Main ──────────────────────────────────────────

def main():
    print("🤖 Multi-Account Manager started")

    if not bot:
        print("⚠️ BOT_TOKEN не найден — TG управление недоступно")

    rot_thread = threading.Thread(target=rotation_loop, daemon=True)
    rot_thread.start()

    if bot:
        print("🤖 Telegram bot polling...")
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=20)
        except Exception as e:
            print(f"❌ Bot error: {e}")
    else:
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            pass

    print("\n👋 Остановка...")
    with lock:
        for proc in active.values():
            proc.stop()
    print("✅ Всё остановлено.")


if __name__ == "__main__":
    main()
