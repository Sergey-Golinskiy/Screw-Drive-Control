from io_base import IOController
import time

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
    while True:
        if io.sensor_state(sensor_name) == target_close:
            return True
        if timeout is not None and (time.time() - start) > timeout:
            print(f"[wait_sensor] TIMEOUT: {sensor_name} не достиг состояния "
                  f"{'CLOSE' if target_close else 'OPEN'} за {timeout} с")
            return False
        time.sleep(0.01)

def main():
    io = IOController()
    try:
        print("=== Старт скрипта ===")
# 2. Проверяем GER_C1_UP; если OPEN — включаем R02_C1_UP и ждём CLOSE
        if not io.sensor_state("GER_C1_UP"):  # OPEN
            io.set_relay("R02_C1_UP", True)
            ok = wait_sensor(io, "GER_C1_UP", True, TIMEOUT_SEC)
                # по достижении верхнего датчика — ОБЯЗАТЕЛЬНО выключаем катушку
            io.set_relay("R02_C1_UP", False)
            if not ok:
                return   # аварийный выход при таймауте
        
                    # 5. Включаем R04_C2 и держим, пока GER_C2_DOWN не станет CLOSE
            io.set_relay("R04_C2", True)
            ok = wait_sensor(io, "GER_C2_DOWN", True, TIMEOUT_SEC)
            if not ok:
                io.set_relay("R04_C2", False)  # на всякий
                return

            # 6. Выключаем R04_C2 и ждём, пока GER_C2_UP станет CLOSE
            io.set_relay("R04_C2", False)
            ok = wait_sensor(io, "GER_C2_UP", True, TIMEOUT_SEC)
            if not ok:
                return

        while True:

            # 3. Импульс 50 мс на R01_PIT
            io.pulse("R01_PIT", ms=700)

            #time.sleep(1)

            # 4. Включаем R03_C1_DOWN и держим, пока GER_C1_DOWN не станет CLOSE
            io.set_relay("R03_C1_DOWN", True)
            ok = wait_sensor(io, "GER_C1_DOWN", True, TIMEOUT_SEC)
            io.set_relay("R03_C1_DOWN", False)
            if not ok:
                break

            # 5. Включаем R04_C2 и держим, пока GER_C2_DOWN не станет CLOSE
            io.set_relay("R04_C2", True)
            ok = wait_sensor(io, "GER_C2_DOWN", True, TIMEOUT_SEC)
            if not ok:
                io.set_relay("R04_C2", False)  # на всякий
                break

            # 6. Выключаем R04_C2 и ждём, пока GER_C2_UP станет CLOSE
            io.set_relay("R04_C2", False)
            ok = wait_sensor(io, "GER_C2_UP", True, TIMEOUT_SEC)
            if not ok:
                break

            # 7. Включаем R02_C1_UP и ждём CLOSE по GER_C1_UP
            io.set_relay("R02_C1_UP", True)
            ok = wait_sensor(io, "GER_C1_UP", True, TIMEOUT_SEC)
            io.set_relay("R02_C1_UP", False)
            if not ok:
                break

            # 8. Повторяем с пункта 2 (просто продолжаем while True)
            # Можно вставить небольшую паузу, если нужно
            # time.sleep(0.05)

    except KeyboardInterrupt:
        pass
    finally:
        io.cleanup()
        print("=== Остановлено. GPIO освобождены ===")

if __name__ == "__main__":
    main()
