#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import RPi.GPIO as GPIO # type: ignore
import time
from datetime import datetime
import threading

# =====================[ НАСТРОЙКИ ЖЕЛЕЗА ]=====================
# Если твоя релейка включается НИЗКИМ уровнем (LOW-trigger) -> True
# Если ВЫСОКИМ уровнем (HIGH-trigger) -> False
RELAY_ACTIVE_LOW = True

# BCM-распиновка реле (логические имена -> GPIO)
RELAY_PINS = {
    "R01_PIT":     5,   # Питатель винтов (импульс)
    "R02_C1_UP":   6,   # Подъём основного цилиндра
    "R03_C1_DOWN": 13,  # Опускание основного цилиндра
    "R04_C2":      19,  # Цилиндр отвёртки (ON=вниз, OFF=вверх)
    "R05_DI4_FREE":  26,  # Свободный ход отвёртки (держать ON = крутится)
    "R06_DI1_POT":   16,  # Режим «по моменту» (держать ON до ОК)
    "R07_DI5_TSK0":  20,  # Выбор задачи 0 (импульс 700 мс)
    "R08":           21,  # запас
}

# BCM-распиновка датчиков (герконов). True=CLOSE (замкнут на GND)
SENSOR_PINS = {
    "GER_C1_UP":    17,  # верх C1
    "GER_C1_DOWN":  27,  # низ C1
    "GER_C2_UP":    22,  # верх отвёртки
    "GER_C2_DOWN":  23,  # низ отвёртки
    "IND_SCRW":     12,  # <— ИНДУКТИВНЫЙ: «винт прошёл»
    "DO2_OK":       25,  # Ответ от драйвера что винт закручен с нудным моментом, успех
    "PED_START":    18,  # педалька для старта цикла
}

# Пары реле, которые нельзя держать включёнными одновременно
MUTEX_GROUPS = [
    ("R02_C1_UP", "R03_C1_DOWN"),
]

# Антидребезг датчиков и параметры опроса (мс)
SENSOR_BOUNCE_MS = 20
POLL_INTERVAL_MS = 5

# =====================[ ВСПОМОГАТЕЛЬНОЕ ]======================
def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def relay_gpio_value(on: bool) -> int:
    """Преобразование логического on/off в уровень GPIO с учётом полярности реле."""
    if RELAY_ACTIVE_LOW:
        return GPIO.LOW if on else GPIO.HIGH
    else:
        return GPIO.HIGH if on else GPIO.LOW

# =====================[ КОНТРОЛЛЕР IO ]========================
class IOController:
    def __init__(self):
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)

        # Реле: настраиваем OUTPUT и гарантированно выключаем
        for name, pin in RELAY_PINS.items():
            GPIO.setup(pin, GPIO.OUT, initial=relay_gpio_value(False))

        # Датчики: входы с подтяжкой вверх (замыкание на GND = CLOSE/LOW)
        for name, pin in SENSOR_PINS.items():
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        self.relays = {name: False for name in RELAY_PINS.keys()}

        # Пробуем повесить аппаратные события на оба фронта
        self._use_poll_fallback = False
        self._last_state = {}
        edge_ok = True
        for name, pin in SENSOR_PINS.items():
            try:
                try:
                    GPIO.remove_event_detect(pin)
                except Exception:
                    pass
                GPIO.add_event_detect(pin, GPIO.BOTH, callback=self._sensor_event, bouncetime=SENSOR_BOUNCE_MS)
                self._last_state[name] = (GPIO.input(pin) == GPIO.LOW)  # True=CLOSE
            except Exception as e:
                print(f"[{ts()}] WARN: Edge detect failed on {name} (GPIO{pin}): {e}")
                edge_ok = False

        if not edge_ok:
            print(f"[{ts()}] INFO: Switching to polling fallback for sensors.")
            self._use_poll_fallback = True
            for name, pin in SENSOR_PINS.items():
                self._last_state[name] = (GPIO.input(pin) == GPIO.LOW)
            self._poll_stop = threading.Event()
            self._poll_thr = threading.Thread(target=self._poll_loop, daemon=True)
            self._poll_thr.start()

        print(f"[{ts()}] IO init done. All relays OFF. Edge={'ON' if not self._use_poll_fallback else 'OFF/Polling'}")

    def cleanup(self):
        # Безопасно выключаем всё и освобождаем GPIO
        for name in list(self.relays.keys()):
            self._apply_relay(name, False)
        if getattr(self, "_use_poll_fallback", False):
            self._poll_stop.set()
            if hasattr(self, "_poll_thr"):
                self._poll_thr.join(timeout=0.5)
        GPIO.cleanup()

    # -------- Управление реле --------
    def _apply_relay(self, relay_name: str, on: bool):
        pin = RELAY_PINS[relay_name]
        GPIO.output(pin, relay_gpio_value(on))
        self.relays[relay_name] = on

    def set_relay(self, relay_name: str, on: bool):
        if relay_name not in RELAY_PINS:
            raise ValueError(f"Unknown relay '{relay_name}'")
        # Взаимоблокировки: перед включением отключить "антагониста"
        if on:
            for a, b in MUTEX_GROUPS:
                if relay_name == a and self.relays.get(b, False):
                    self._apply_relay(b, False)
                    print(f"[{ts()}] Interlock: OFF {b} before ON {a}")
                if relay_name == b and self.relays.get(a, False):
                    self._apply_relay(a, False)
                    print(f"[{ts()}] Interlock: OFF {a} before ON {b}")
        self._apply_relay(relay_name, on)
        print(f"[{ts()}] {relay_name} -> {'ON' if on else 'OFF'}")

    def pulse(self, relay_name: str, ms: int = 150):
        self.set_relay(relay_name, True)
        time.sleep(ms/1000.0)
        self.set_relay(relay_name, False)

    # ---- Удобные обёртки для отвёртки ----
    def screwdriver_free_run(self, on: bool):
        """DI4 FREE-RUN: держать ON = крутится; OFF = остановка."""
        self.set_relay("R05_DI4_FREE", on)

    def screwdriver_torque_mode(self, on: bool):
        """DI1 TORQUE (по моменту): держать ON до подтверждения ОК (датчик добавим позже)."""
        self.set_relay("R06_DI1_POT", on)

    def screwdriver_select_task0(self, pulse_ms: int = 700):
        """DI5 TASK0: импульс выбора задачи (700 мс по ТЗ)."""
        self.pulse("R07_DI5_TSK0", ms=pulse_ms)

    # -------- Датчики --------
    def sensor_state(self, sensor_name: str) -> bool:
        """True = CLOSE (LOW), False = OPEN (HIGH)."""
        pin = SENSOR_PINS[sensor_name]
        return GPIO.input(pin) == GPIO.LOW

    # ---- Edge callback path ----
    def _sensor_event(self, channel_pin: int):
        name = None
        for n, p in SENSOR_PINS.items():
            if p == channel_pin:
                name = n
                break
        if name is None:
            return
        closed = (GPIO.input(channel_pin) == GPIO.LOW)
        self._emit_sensor(name, closed)

    # ---- Polling fallback path ----
    def _poll_loop(self):
        stable_required = max(1, SENSOR_BOUNCE_MS // POLL_INTERVAL_MS)
        counters = {name: 0 for name in SENSOR_PINS.keys()}
        while not self._poll_stop.is_set():
            for name, pin in SENSOR_PINS.items():
                closed_now = (GPIO.input(pin) == GPIO.LOW)
                if closed_now != self._last_state[name]:
                    counters[name] += 1
                    if counters[name] >= stable_required:
                        self._last_state[name] = closed_now
                        counters[name] = 0
                        self._emit_sensor(name, closed_now)
                else:
                    counters[name] = 0
            time.sleep(POLL_INTERVAL_MS / 1000.0)

    # ---- Common emitter ----
    def _emit_sensor(self, name: str, closed: bool):
        print(f"[{ts()}] SENSOR {name}: {'CLOSE' if closed else 'OPEN'}")

# =====================[ ЛОГИКА ШАГОВ ]=========================
# Поставь None, если хочешь ждать бесконечно
TIMEOUT_SEC = 5.0

def wait_sensor(io: IOController, sensor_name: str, target_close: bool, timeout: float | None) -> bool:
    """
    Ждём, пока датчик станет нужным состоянием.
    target_close=True  -> ждём CLOSE (уровень LOW)
    target_close=False -> ждём OPEN  (уровень HIGH)
    Возвращает True при успехе, False при таймауте.
    """
    start = time.time()
    wanted = "CLOSE" if target_close else "OPEN"
    while True:
        if io.sensor_state(sensor_name) == target_close:
            return True
        if timeout is not None and (time.time() - start) > timeout:
            print(f"[wait_sensor] TIMEOUT: {sensor_name} не достиг состояния {wanted} за {timeout} с")
            return False
        time.sleep(0.01)

def wait_new_press(io: IOController, sensor_name: str, timeout: float | None) -> bool:
    """
    Ждём ИМЕННО НОВОЕ нажатие (OPEN -> CLOSE).
    Сначала убеждаемся, что педаль отжата (OPEN), затем ждём CLOSE.
    """
    start = time.time()
    # 1) дождаться OPEN (если сейчас уже нажата)
    while True:
        if not io.sensor_state(sensor_name):  # OPEN
            break
        if timeout is not None and (time.time() - start) > timeout:
            print(f"[wait_new_press] TIMEOUT: {sensor_name} не вернулась в OPEN")
            return False
        time.sleep(0.01)

    # 2) дождаться CLOSE (новое нажатие)
    start = time.time()
    while True:
        if io.sensor_state(sensor_name):  # CLOSE
            return True
        if timeout is not None and (time.time() - start) > timeout:
            print(f"[wait_new_press] TIMEOUT: {sensor_name} не нажата")
            return False
        time.sleep(0.01)


def main():
    io = IOController()
    try:
        print("=== Старт скрипта ===")

        # ---------------- ИНИЦИАЛИЗАЦИЯ (шаги 2–4) ----------------
        # 2. Проверяем GER_C1_UP; если OPEN — включаем R02_C1_UP и ждём CLOSE.
        if not io.sensor_state("GER_C1_UP"):  # OPEN
            io.set_relay("R02_C1_UP", True)
            ok = wait_sensor(io, "GER_C1_UP", True, TIMEOUT_SEC)
            io.set_relay("R02_C1_UP", False)
            if not ok:
                return

        # 3. Включаем R04_C2 и держим включённым, пока GER_C2_DOWN не станет CLOSE
        io.set_relay("R04_C2", True)
        ok = wait_sensor(io, "GER_C2_DOWN", True, TIMEOUT_SEC)
        if not ok:
            io.set_relay("R04_C2", False)
            return

        # 4. Выключаем R04_C2 и ждём, пока GER_C2_UP станет CLOSE.
        io.set_relay("R04_C2", False)
        ok = wait_sensor(io, "GER_C2_UP", True, TIMEOUT_SEC)
        if not ok:
            return

        # ---------------- ОСНОВНОЙ ЦИКЛ (с шага 5) ----------------
        c1_hold_down = False  # Флаг, что R03_C1_DOWN сейчас держится
        while True:
            # 5. Ждём нажатия педальки PED_START — первое нажатие в этом цикле
            ok = wait_new_press(io, "PED_START", None)  # None -> ждать без таймаута
            if not ok:
                break

            # 6. Включаем R03_C1_DOWN и держим включённым, пока GER_C1_DOWN не станет CLOSE.
            io.set_relay("R03_C1_DOWN", True)
            ok = wait_sensor(io, "GER_C1_DOWN", True, TIMEOUT_SEC)
            
            if not ok:
                io.set_relay("R03_C1_DOWN", False)
                break
            # ВАЖНО: НЕ выключаем R03_C1_DOWN здесь — поджим держим до подъёма (шаг 12)
            c1_hold_down = True   # <-- добавь эту строку (переменная локальная в main/цикле)

            # 7. Ждём нажатия педальки PED_START — второе нажатие в этом цикле
            ok = wait_new_press(io, "PED_START", None)
            if not ok:
                break

            # 8. Даем импульс (700 мс) на R01_PIT
            io.pulse("R01_PIT", ms=700)

            # 9. Включаем R06_DI1_POT (режим по моменту)
            io.set_relay("R06_DI1_POT", True)

            # 10. Включаем R04_C2 и держим до DO2_OK=CLOSE
            io.set_relay("R04_C2", True)
            ok = wait_sensor(io, "DO2_OK", True, TIMEOUT_SEC)

            if not ok:
                # === АВАРИЙНАЯ ВЕТКА при отсутствии OK по моменту ===
                # 10a. Поднимаем основной цилиндр до верха
                io.set_relay("R02_C1_UP", True)
                ok_up = wait_sensor(io, "GER_C1_UP", True, TIMEOUT_SEC)
                io.set_relay("R02_C1_UP", False)

                # 10b. Поднимаем отвертку: выключаем R04_C2 и ждём верх
                io.set_relay("R04_C2", False)
                ok_c2_up = wait_sensor(io, "GER_C2_UP", True, TIMEOUT_SEC)

                # Логично также отключить моментный режим (иначе драйвер будет крутить)
                io.set_relay("R06_DI1_POT", False)

                # Переходим к следующему циклу (возврат к п.5)
                continue

            # === НОРМАЛЬНЫЙ ПУТЬ: момент достигнут ===
            # 11. Выключаем R04_C2 и R06_DI1_POT и ждём, пока GER_C2_UP станет CLOSE
            io.set_relay("R04_C2", False)
            io.set_relay("R06_DI1_POT", False)
            ok = wait_sensor(io, "GER_C2_UP", True, TIMEOUT_SEC)
            if not ok:
                break

            # 12. Включаем R02_C1_UP и ждём, пока GER_C1_UP станет CLOSE.
            io.set_relay("R02_C1_UP", True)
            ok = wait_sensor(io, "GER_C1_UP", True, TIMEOUT_SEC)
            io.set_relay("R02_C1_UP", False)
            c1_hold_down = False  # <-- сбрось флаг, т.к. подъём завершён
            if not ok:
                break

            # 13. Повторяем с пункта 5 (while True крутит дальше)

    except KeyboardInterrupt:
        pass
    finally:
        io.cleanup()
        print("=== Остановлено. GPIO освобождены ===")


if __name__ == "__main__":
    main()
