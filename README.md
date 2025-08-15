# Screw-Drive-Control — управление сервоприводной отвёрткой E350 по RS-485

> Веб-панель и REST API для задания параметров, запуска/остановки и онлайн-мониторинга привода серии E350 через USB–RS-485 (Raspberry Pi/ПК). Репозиторий содержит backend (FastAPI), статические файлы UI и список зависимостей.

---

## Возможности

- **Free-Run (холостой ход):** установка скорости (E138) и лимита момента (E139, если поддерживается прошивкой), пуск/стоп через виртуальный DI4.
- **Задачи (Tasks 1–4):** чтение/запись основных параметров задачи (метод затяжки, целевой момент, скорость и т. п.), запуск Tighten.
- **Онлайн-статус:** текущая скорость, DI/DO/AUX, код ошибки, результат последней затяжки.
- **Событийный лог:** FR_STARTED/FR_STOPPED, TIGHTEN_DONE (OK/FLOAT/STRIP/NG), FAULT_ON/OFF.
- **Перезапуск узла связи:** софт-рестарт (сброс Fault → MODE=RS → реконнект порта).

---

## Структура репозитория

```
Screw-Drive-Control/
├─ app.py            # основной сервер FastAPI (backend)
├─ requirements.txt  # зависимости (FastAPI, Uvicorn, Pymodbus)
└─ static/           # фронтенд: index.html, app.js, styles.css
```

---

## Требования

- Python 3.10+
- USB–RS-485 конвертер (A+ / B− / GND)
- Raspberry Pi 4B или x86-ПК (Linux)
- Драйвер E350 с включённым Modbus RTU

---

## Подключение (RS-485)

1. **A+ конвертера → A(+) драйвера**, **B− → B(−)**, **GND ↔ GND** (общая земля).
2. Терминатор 120 Ω на конце линии (по необходимости).
3. Питание силовой части привода (48 В и т. п.) должно быть подано.

---

## Установка и запуск

```bash
git clone https://github.com/Sergey-Golinskiy/Screw-Drive-Control.git
cd Screw-Drive-Control

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Переменные окружения

```bash
export E350_PORT=/dev/ttyUSB0  # ваш USB–RS485
export E350_BAUD=115200
export E350_PARITY=N
export E350_STOPBITS=1
export E350_UNIT=1
```

### Запуск сервера

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

Откройте в браузере:  
`http://<IP_устройства>:8000/`

---

## Веб-интерфейс

- **Состояние:** MODE, FAULT, скорость, DI/DO/AUX, последний результат.
- **Free-Run:** поля «Скорость (RPM)», «Лимит момента (Н·м)», кнопки **Записать**, **Пуск**, **Стоп**.
- **Задачи (Task 1–4):** загрузка/сохранение параметров, запуск затяжки.
- **Лог событий:** список последних операций и изменений состояния.

---

## REST API

Все ответы — JSON. Ошибки: `HTTP 500` с полем `detail`.

### Статус

```bash
GET /api/status
GET /api/ops
GET /events  # Server-Sent Events
```

### Free-Run

```bash
POST /api/fr_set
# { "rpm": 1200, "torque": 0.20 }

POST /api/fr_start
POST /api/fr_stop
```

### Задачи

```bash
GET /api/task_params?task=<1..4>

POST /api/task_params
# {
#   "task": 1,
#   "params": { "method": 1, "torque_mNm": 200, "speed_rpm": 500, "angle_decideg": 0, "ok_time_ms": 10000 }
# }

POST /api/run_task
```

### Перезапуск и сброс ошибок

```bash
POST /api/restart
POST /api/fault_reset
```

---

## Карта ключевых регистров

| Регистр | Назначение | Тип |
|---|---|---|
| `0xE002` | Режим управления (0=I/O, 1=RS-485/232) | R/W |
| `0xE138` | Free-Run скорость (RPM, signed16) | R/W |
| `0xE139` | Free-Run лимит момента (мН·м) | R/W* |
| `0x2098` | Виртуальные DI (бит3=DI4 Пуск) | R/W |
| `0x20E6` | Текущая скорость (RPM) | R |
| `0x20F1` | DI состояние | R |
| `0x20F2` | DO состояние | R |
| `0x20F4` | Код ошибки (0=OK) | R |
| `0x2005` | Сброс ошибки | W |

---

## Диагностика

- **Нет связи:** проверить порт, UNIT, A+/B−, питание, `fuser /dev/ttyUSB0`.
- **Скорость 0 при FAULT=0:** проверить питание силовой части, межзамки.
- **Лимит момента не записывается:** возможно, нет регистра `0xE139`.
- **DO не меняется:** аппаратные выходы, виртуальных DO нет.

---

## Безопасность

- Добавьте авторизацию или ограничьте доступ через firewall.
- При управлении двигателем используйте защитные зоны и аварийные кнопки.

---

## Лицензия

См. `LICENSE` (если будет добавлен).

---

## Шпаргалка (curl)

```bash
curl -s http://127.0.0.1:8000/api/status
curl -s -X POST http://127.0.0.1:8000/api/fr_set -H 'Content-Type: application/json' -d '{"rpm":1500,"torque":0.2}'
curl -s -X POST http://127.0.0.1:8000/api/fr_start
curl -s -X POST http://127.0.0.1:8000/api/fr_stop
curl -s -X POST http://127.0.0.1:8000/api/restart
curl -s -X POST http://127.0.0.1:8000/api/fault_reset
```