import asyncio
import socket
import time
import requests
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from config import TELEGRAM_TOKEN, API_BASE

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

def get_external_ip():
    try:
        return requests.get("https://api.ipify.org").text
    except:
        return "Unknown"

# --- старт ---
@dp.message(F.text == "/start")
async def start_cmd(msg: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статус", callback_data="status")],
        [InlineKeyboardButton(text="⚡ Реле", callback_data="relays")],
        [InlineKeyboardButton(text="▶️ Start cycle", callback_data="ext_start"),
         InlineKeyboardButton(text="⏹ Stop cycle", callback_data="ext_stop")],
        [InlineKeyboardButton(text="🌐 IP", callback_data="ip")],
        [InlineKeyboardButton(text="⏱ Замер цикла", callback_data="measure")],
    ])
    await msg.answer("Привет! Я бот для управления GPIO.", reply_markup=kb)

# --- статус ---
@dp.callback_query(F.data == "status")
async def show_status(cb: CallbackQuery):
    st = api_get("status")
    relays = "\n".join([f"{k}: {'ON' if v else 'OFF'}" for k,v in st["relays"].items()])
    sensors = "\n".join([f"{k}: {'CLOSE' if v else 'OPEN'}" for k,v in st["sensors"].items()])
    text = f"⏰ {st['time']}\n\n🔌 Реле:\n{relays}\n\n📟 Датчики:\n{sensors}\n\n📂 Скрипт: {'RUNNING' if st['external_running'] else 'STOPPED'}"
    await cb.message.edit_text(text)

# --- управление реле ---
@dp.callback_query(F.data == "relays")
async def relays_menu(cb: CallbackQuery):
    st = api_get("status")
    kb = []
    for name in st["relay_names"]:
        kb.append([InlineKeyboardButton(text=f"{name} ON", callback_data=f"relay_on:{name}"),
                   InlineKeyboardButton(text=f"{name} OFF", callback_data=f"relay_off:{name}"),
                   InlineKeyboardButton(text=f"{name} PULSE", callback_data=f"relay_pulse:{name}")])
    await cb.message.edit_text("Выбери реле:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("relay_"))
async def relay_action(cb: CallbackQuery):
    action, name = cb.data.split(":")
    if action == "relay_on":
        api_post("relay", {"name": name, "action": "on"})
    elif action == "relay_off":
        api_post("relay", {"name": name, "action": "off"})
    elif action == "relay_pulse":
        api_post("relay", {"name": name, "action": "pulse", "ms": 150})
    await cb.answer("OK")
    await show_status(cb)

# --- запуск/остановка цикла ---
@dp.callback_query(F.data == "ext_start")
async def ext_start(cb: CallbackQuery):
    api_post("ext/start")
    await cb.answer("Cycle started")
    await show_status(cb)

@dp.callback_query(F.data == "ext_stop")
async def ext_stop(cb: CallbackQuery):
    api_post("ext/stop")
    await cb.answer("Cycle stopped")
    await show_status(cb)

# --- IP ---
@dp.callback_query(F.data == "ip")
async def ip_info(cb: CallbackQuery):
    local_ip = get_local_ip()
    ext_ip = get_external_ip()
    await cb.message.edit_text(f"🌐 Local IP: {local_ip}\n🌍 External IP: {ext_ip}")

# --- замер цикла ---
@dp.callback_query(F.data == "measure")
async def measure_cycle(cb: CallbackQuery):
    await cb.message.edit_text("⏱ Жду нажатия педальки...")

    start_time = time.time()
    sensor_name = list(api_get("status")["sensor_names"])[0]  # допустим первая педалька
    while True:
        st = api_get("status")
        if st["sensors"][sensor_name]:
            elapsed = time.time() - start_time
            await cb.message.answer(f"Педаль сработала! Время реакции: {elapsed:.2f} сек")
            break
        await asyncio.sleep(0.5)

# --- запуск ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
