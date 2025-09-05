# Screw-Drive Control — TouchDesk (PyQt5) + Web UI + Cycle Script

Полная инструкция по установке «с нуля» и работе трёх компонентов на Raspberry Pi c сенсорным экраном 1920×1080:

- `cycle_onefile.py` — внешний сценарий цикла (управление реле/датчиками GPIO, логика цикла, триггер).
- `web_ui.py` — локальный REST API и простая веб-панель (старт/стоп внешнего процесса, ручное управление).
- `touchdesk.py` — полноэкранный тач-интерфейс (PyQt5) с вкладками **WORK / START / SERVICE**, цветной «рамкой-индикатором», скрытым курсором и экранной клавиатурой.

> Все пути и команды ниже даны для пользователя `smartgrow`. Если у тебя другой пользователь — замени имя в путях и в unit-файлах.

---

## 1) Архитектура и взаимодействие

### Компоненты

- **Внешний цикл** (`cycle_onefile.py`)
  - Работает с GPIO (реле/датчики) по BCM-нумерации.
  - Имеет **локальный TCP-триггер** на `127.0.0.1:8765`: если туда отправить `START\n`, выполняется один цикл.
  - Во время активного цикла выставляет флаг занятости (например, через файл-флаг `BUSY`), по завершении — снимает.

- **Web API + панель** (`web_ui.py`)
  - Поднимает локальный HTTP-сервер на `0.0.0.0:8000`.
  - Эндпоинты:
    - `GET /api/status` — текущий статус: `relays`, `sensors`, `external_running` и т. п.
    - `POST /api/ext/start` — запускает `cycle_onefile.py` как внешний процесс (владение GPIO отдаётся внешнему процессу).
    - `POST /api/ext/stop` — останавливает внешний процесс и возвращает управление GPIO в API.
    - `POST /api/relay` — **ручное** управление реле (`on/off/pulse`) — **блокируется**, если внешний процесс запущен.
    - (Опционально) `POST /api/trigger/start` — отправляет `START\n` на `127.0.0.1:8765` (эмуляция педали).
  - Веб-страница даёт минимальные кнопки: старт/стоп внешнего процесса, отправка `START`.

- **TouchDesk** (`touchdesk.py`)
  - Полноэкранное PyQt5-приложение. Автоматически выбирает `eglfs`, если нет `$DISPLAY/$WAYLAND_DISPLAY` (то есть может работать без рабочего стола, прямо на фреймбуфере).
  - Вкладки:
    - **WORK** — IP, большая кнопка эмуляции `START`, статус/подсветка.
    - **START** — большие кнопки **START program** / **STOP program** (старт/стоп внешнего процесса). У `STOP` отдельный «красный» стиль.
    - **SERVICE** — паролем защищённый доступ к статусам концевиков и сенсоров, управлению реле (ON/OFF/PULSE), доступ к Arduino через Serial (не закрывающаяся сессия, лог, отправка команд). Экранная клавиатура появляется при фокусе в поле ввода.
  - «Рамка-индикатор» по периметру экрана:
    - **зелёная** — цикл выполняется;
    - **жёлтая** — ожидание;
    - **красная** — обнаружен аварийный сигнал (любой сенсор с именем, содержащим `alarm/emerg/fault/error/e_stop` и значением `True`).
  - `API_BASE` по умолчанию `http://127.0.0.1:8000/api`, можно переопределить переменной окружения.

---

## 2) Аппаратные подключения

- Конкретные BCM-пины для реле/датчиков заданы в верхней части `cycle_onefile.py`.
- Реле обычно **активны уровнем LOW** (`RELAY_ACTIVE_LOW=True`), датчики читаются уровнем `True/False`.

---

## 3) Установка на чистую Raspberry Pi OS (Bullseye/Bookworm)

### 3.1. Системные пакеты

```bash
sudo apt update
sudo apt install -y \
  python3 python3-pip python3-venv \
  python3-flask python3-requests python3-serial python3-rpi.gpio \
  python3-pyqt5 qt5-qpa-platform-plugins \
  fonts-dejavu-core libxkbcommon-x11-0 libxcb-cursor0
```

### 3.2. Права пользователя

```bash
sudo usermod -aG gpio,dialout $USER
newgrp gpio
```

### 3.3. Каталог проекта

```bash
mkdir -p ~/Screw-Drive-Control/Base_Logic_Web
cd ~/Screw-Drive-Control/Base_Logic_Web
# Скопируй сюда: cycle_onefile.py, web_ui.py, touchdesk.py, (опционально logo.png)
```

### 3.4. (Опционально) Виртуальное окружение

```bash
python3 -m venv --system-site-packages ~/touchdesk-venv
source ~/touchdesk-venv/bin/activate
pip install --upgrade pip
```

---

## 4) Ручной запуск

### 4.1. Запуск Web API

```bash
cd ~/Screw-Drive-Control/Base_Logic_Web
python3 web_ui.py
# Открой http://<IP_RPi>:8000 в браузере
```

### 4.2. Запуск TouchDesk

```bash
cd ~/Screw-Drive-Control/Base_Logic_Web
python3 touchdesk.py
```

---

## 5) Автозапуск через systemd

### 5.1. Web API — `/etc/systemd/system/web-ui@.service`

```ini
[Unit]
Description=RPi IO Web API (Flask) for %i
After=network-online.target
Wants=network-online.target

[Service]
User=%i
WorkingDirectory=/home/%i/Screw-Drive-Control/Base_Logic_Web
ExecStart=/usr/bin/python3 /home/%i/Screw-Drive-Control/Base_Logic_Web/web_ui.py
Restart=always
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

### 5.2. TouchDesk — `/etc/systemd/system/touchdesk.service`

```ini
[Unit]
Description=TouchDesk (PyQt5) fullscreen UI
After=SD.service web-ui@smartgrow.service network-online.target
Wants=web-ui@smartgrow.service

[Service]
User=smartgrow
WorkingDirectory=/home/smartgrow/Screw-Drive-Control/Base_Logic_Web
Environment=QT_QPA_PLATFORM=eglfs
Environment=API_BASE=http://127.0.0.1:8000/api
ExecStart=/usr/bin/python3 /home/smartgrow/Screw-Drive-Control/Base_Logic_Web/touchdesk.py
Restart=always
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

---

## 6) Диагностика

```bash
journalctl -u web-ui@smartgrow.service -f
journalctl -u touchdesk.service -f
```

---

## 7) Структура проекта

```
Screw-Drive-Control/
└─ Base_Logic_Web/
   ├─ cycle_onefile.py
   ├─ web_ui.py
   ├─ touchdesk.py
   └─ logo.png
```

---
