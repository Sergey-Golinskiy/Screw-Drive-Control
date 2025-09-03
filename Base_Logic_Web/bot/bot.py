import asyncio
import socket
import time
import requests
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup
from config import TELEGRAM_TOKEN, API_BASE

# Опциональные настройки из config.py (безопасные дефолты)
try:
    from config import WHITELIST_USERNAMES  # пример: {"sergey_golinskiy", "my_admin"}
except Exception:
    WHITELIST_USERNAMES = set()

try:
    from config import SAD_STICKER_ID       # file_id стикера, можно оставить пустым
except Exception:
    SAD_STICKER_ID = ""

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# --- helpers ---
def api_get(path):
    return requests.get(f"{API_BASE}/{path}").json()

def api_post(path, payload=None):
    return requests.post(f"{API_BASE}/{path}", json=payload).json()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "Unknown"

def normalize_username(u: str | None) -> str:
    if not u:
        return ""
    u = u.strip()
    if u.startswith("@"):
        u = u[1:]
    return u.lower()

def is_allowed_username(username: str | None) -> bool:
    allowed = {normalize_username(x) for x in WHITELIST_USERNAMES}
    user = normalize_username(username)
    return bool(user) and (user in allowed)

async def sad_reply_generic(message: Message, text: str = "Доступ запрещён 😔"):
    """
    Отправляем грустный стикер + пояснительный текст.
    Если стикер не задан или не удалось отправить — только текст.
    """
    sent = False
    if SAD_STICKER_ID:
        try:
            await message.answer_sticker(SAD_STICKER_ID)
            sent = True
        except Exception:
            pass
    # всегда шлём текст, даже если стикер отправился
    await message.answer(text if text else "Доступ запрещён 😔")

# --- middleware глобальной безопасности ---
# Блокирует любой апдейт от неразрешённых логинов
from aiogram import BaseMiddleware

class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        # Пропускаем только тех, у кого username в белом списке
        msg = event if isinstance(event, Message) else None
        username = None
        if msg and msg.from_user:
            username = msg.from_user.username

        if not is_allowed_username(username):
            # Если это сообщение — отправим стикер/текст и НЕ будем вызывать хендлеры
            if msg:
                await sad_reply_generic(msg)
            return  # блокируем обработку дальше

        # Разрешён — отдаём управление хендлерам
        return await handler(event, data)

# Подключаем middleware для всех сообщений
dp.message.middleware(AccessMiddleware())

# --- клавиатуры ---
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🚀 Старт"), KeyboardButton(text="🔌 Реле")],
        [KeyboardButton(text="📊 Статус"), KeyboardButton(text="⏱ Цикл")],
        [KeyboardButton(text="🌐 IP")],
    ],
    resize_keyboard=True
)

cycle_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="▶️ Старт цикла"), KeyboardButton(text="⏹ Стоп цикла")],
        [KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True
)

def build_relays_kb():
    st = api_get("status")
    rows = []
    for name in st["relay_names"]:
        rows.append([
            KeyboardButton(text=f"{name} ON"),
            KeyboardButton(text=f"{name} OFF"),
            KeyboardButton(text=f"{name} PULSE")
        ])
    rows.append([KeyboardButton(text="⬅️ Назад")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

# --- обработчики (для разрешённых пользователей) ---

@dp.message(F.text == "/start")
async def start_command(msg: Message):
    await msg.answer("Привет! Я бот для управления GPIO.\nВыбирай действие 👇", reply_markup=main_kb)

@dp.message(F.text == "🚀 Старт")
async def start_btn(msg: Message):
    await msg.answer("Главное меню 👇", reply_markup=main_kb)

@dp.message(F.text == "📊 Статус")
async def status_cmd(msg: Message):
    st = api_get("status")
    relays = "\n".join([f"{k}: {'ON' if v else 'OFF'}" for k, v in st["relays"].items()])
    sensors = "\n".join([f"{k}: {'CLOSE' if v else 'OPEN'}" for k, v in st["sensors"].items()])
    text = (
        f"⏰ {st['time']}\n\n"
        f"🔌 Реле:\n{relays}\n\n"
        f"📟 Датчики:\n{sensors}\n\n"
        f"📂 Скрипт: {'RUNNING' if st['external_running'] else 'STOPPED'}"
    )
    await msg.answer(text)

@dp.message(F.text == "🔌 Реле")
async def relays_cmd(msg: Message):
    await msg.answer("Управление реле:", reply_markup=build_relays_kb())

@dp.message(F.text.regexp(r"^(\w+) (ON|OFF|PULSE)$"))
async def relay_action(msg: Message):
    parts = msg.text.split()
    name, action = parts[0], parts[1].lower()
    if action == "on":
        api_post("relay", {"name": name, "action": "on"})
    elif action == "off":
        api_post("relay", {"name": name, "action": "off"})
    elif action == "pulse":
        api_post("relay", {"name": name, "action": "pulse", "ms": 150})
    await msg.answer(f"✅ Реле {name} → {action.upper()}", reply_markup=build_relays_kb())

@dp.message(F.text == "⏱ Цикл")
async def cycle_cmd(msg: Message):
    await msg.answer("Управление циклом:", reply_markup=cycle_kb)

@dp.message(F.text == "▶️ Старт цикла")
async def start_cycle(msg: Message):
    api_post("ext/start")
    await msg.answer("▶️ Цикл запущен. Ожидаю нажатие педальки...")
    sensor_name = list(api_get("status")["sensor_names"])[0]  # берём первый сенсор
    start_time = time.time()

    while True:
        st = api_get("status")
        if st["sensors"][sensor_name]:
            elapsed = time.time() - start_time
            await msg.answer(f"⏱ Педаль сработала! Время реакции: {elapsed:.2f} сек\nОжидаю завершения цикла...")
            break
        await asyncio.sleep(0.5)

    await asyncio.sleep(2)
    await msg.answer("✅ Цикл завершён. Ожидаю устройство...", reply_markup=cycle_kb)

@dp.message(F.text == "⏹ Стоп цикла")
async def stop_cycle(msg: Message):
    api_post("ext/stop")
    await msg.answer("⏹ Цикл остановлен.", reply_markup=cycle_kb)

@dp.message(F.text == "🌐 IP")
async def ip_cmd(msg: Message):
    local_ip = get_local_ip()
    url = f"http://{local_ip}:8000"
    await msg.answer(f"🌐 Web UI доступен по адресу:\n{url}")

@dp.message(F.text == "⬅️ Назад")
async def back_cmd(msg: Message):
    await msg.answer("Возврат в главное меню", reply_markup=main_kb)

# --- запуск ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
