#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import time
import threading
from functools import wraps
from flask import Flask, request, jsonify, Response
from cycle_onefile import IOController, RELAY_PINS, SENSOR_PINS

# ---------------------- Инициализация железа ----------------------
io_lock = threading.Lock()
io = IOController()  # единый контроллер GPIO

app = Flask(__name__)

# ---------- Состояние цикла (фонового выполнения) ----------
cycle_thread = None
cycle_stop = threading.Event()
cycle_running = False

TIMEOUT_SEC = 5.0  # тот же таймаут, что и в твоём файле

def with_io_lock(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with io_lock:
            return fn(*args, **kwargs)
    return wrapper

# ---------- Утилиты ожидания (безопасные для потока) ----------
def _sensor_state(name: str) -> bool:
    # True = CLOSE (LOW), False = OPEN (HIGH)
    with io_lock:
        return io.sensor_state(name)

def _set_relay(name: str, on: bool):
    with io_lock:
        io.set_relay(name, on)

def _pulse(name: str, ms: int):
    with io_lock:
        io.pulse(name, ms=ms)

def wait_sensor(sensor_name: str, target_close: bool, timeout: float | None) -> bool:
    """
    Ждём, пока датчик станет нужным состоянием.
    target_close=True -> CLOSE; False -> OPEN.
    Учитываем запрос остановки цикла.
    """
    start = time.time()
    while True:
        if cycle_stop.is_set():
            return False
        if _sensor_state(sensor_name) == target_close:
            return True
        if timeout is not None and (time.time() - start) > timeout:
            print(f"[wait_sensor] TIMEOUT: {sensor_name} != {'CLOSE' if target_close else 'OPEN'}")
            return False
        time.sleep(0.01)

def wait_close_pulse_ui(sensor_name: str, window_ms: int = 300) -> bool:
    """
    Ждём, что датчик станет CLOSE хотя бы на миг в течение window_ms.
    Учитываем запрос остановки цикла.
    """
    t_end = time.time() + (window_ms / 1000.0)
    while time.time() < t_end:
        if cycle_stop.is_set():
            return False
        if _sensor_state(sensor_name):  # CLOSE
            return True
        time.sleep(0.005)
    return False


def wait_new_press(sensor_name: str, timeout: float | None) -> bool:
    """
    Ждём новое нажатие педали: OPEN -> CLOSE.
    Сначала дожидаемся OPEN (если уже нажата), затем CLOSE.
    """
    start = time.time()
    # дождаться OPEN
    while True:
        if cycle_stop.is_set():
            return False
        if not _sensor_state(sensor_name):  # OPEN
            break
        if timeout is not None and (time.time() - start) > timeout:
            print(f"[wait_new_press] TIMEOUT: {sensor_name} не вернулась в OPEN")
            return False
        time.sleep(0.01)
    # дождаться CLOSE
    start = time.time()
    while True:
        if cycle_stop.is_set():
            return False
        if _sensor_state(sensor_name):  # CLOSE
            return True
        if timeout is not None and (time.time() - start) > timeout:
            print(f"[wait_new_press] TIMEOUT: {sensor_name} не нажата")
            return False
        time.sleep(0.01)

# ---------- Сам цикл (шаги 2–13) ----------
def cycle_worker():
    global cycle_running
    try:
        cycle_running = True
        print("[cycle] start")
        # --- Инициализация (шаги 2–4) ---
        # 2. Проверяем GER_C1_UP; если OPEN — поднимаем до CLOSE.
        if not _sensor_state("GER_C1_UP"):  # OPEN
            _set_relay("R02_C1_UP", True)
            if not wait_sensor("GER_C1_UP", True, TIMEOUT_SEC):
                _set_relay("R02_C1_UP", False)
                return
            _set_relay("R02_C1_UP", False)

        # 3. Включаем R04_C2 до GER_C2_DOWN=CLOSE
        _set_relay("R04_C2", True)
        if not wait_sensor("GER_C2_DOWN", True, TIMEOUT_SEC):
            _set_relay("R04_C2", False)
            return

        # 4. Выключаем R04_C2, ждём GER_C2_UP=CLOSE
        _set_relay("R04_C2", False)
        if not wait_sensor("GER_C2_UP", True, TIMEOUT_SEC):
            return

        # --- Основной цикл (с шага 5) ---
        while not cycle_stop.is_set():
            # 5. Ждём нажатия педали (первое нажатие)
            if not wait_new_press("PED_START", None):
                break

            # 6. Опускаем C1 до GER_C1_DOWN=CLOSE
            _set_relay("R03_C1_DOWN", True)
            if not wait_sensor("GER_C1_DOWN", True, TIMEOUT_SEC):
                _set_relay("R03_C1_DOWN", False)
                break
            #_set_relay("R03_C1_DOWN", False)

            # 7. Ждём второе нажатие педали
            if not wait_new_press("PED_START", None):
                break

            # 8. Импульс на R01_PIT (700 мс)
           #_pulse("R01_PIT", ms=700)
            SCREW_FEED_MAX_RETRIES = None  # None = без ограничений; можно задать ч
            attempts = 0
            while not cycle_stop.is_set():
                _pulse("R01_PIT", ms=700)  # п.8
                if wait_close_pulse_ui("IND_SCRW", window_ms=300):  # п.8.1
                    break
                attempts += 1
                if SCREW_FEED_MAX_RETRIES is not None and attempts >= SCREW_FEED_MAX_RETRIES:
                    print("[cycle] Нет импульса IND_SCRW после нескольких попыток")
                    # реши, что делать при исчерпании попыток:
                    # 1) прервать цикл:
                    return
                    # 2) или перейти к началу цикла (п.5): break

            # 9. Включаем R06_DI1_POT
            _set_relay("R06_DI1_POT", True)

            # 10. Включаем R04_C2 и держим до DO2_OK=CLOSE
            _set_relay("R04_C2", True)
            if not wait_sensor("DO2_OK", True, TIMEOUT_SEC):
                _set_relay("R04_C2", False)
                _set_relay("R06_DI1_POT", False)
                break

            # 11. Выключаем R04_C2 и R06_DI1_POT и ждём GER_C2_UP=CLOSE
            _set_relay("R04_C2", False)
            _set_relay("R06_DI1_POT", False)
            if not wait_sensor("GER_C2_UP", True, TIMEOUT_SEC):
                break

            # 12. Поднимаем C1 до GER_C1_UP=CLOSE
            _set_relay("R02_C1_UP", True)
            if not wait_sensor("GER_C1_UP", True, TIMEOUT_SEC):
                _set_relay("R02_C1_UP", False)
                break
            _set_relay("R02_C1_UP", False)

            # 13. Повтор с шага 5 (while крутит дальше)

    finally:
        # Гарантированно отпускаем всё при выходе
        with io_lock:
            # Ничего специально не выключаем поголовно, т.к. логика уже выключает.
            # При необходимости можно добавить общий "всё OFF" тут.
            pass
        cycle_running = False
        print("[cycle] stop")

# ---------------------- Status builder ----------------------
def build_status():
    # Чтобы не дёргать GPIO во время цикла слишком часто, делаем это под замком
    with io_lock:
        relays = dict(io.relays)
        sensors = {name: io.sensor_state(name) for name in SENSOR_PINS.keys()}
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "relays": relays,
        "sensors": sensors,
        "relay_names": list(RELAY_PINS.keys()),
        "sensor_names": list(SENSOR_PINS.keys()),
        "cycle_running": cycle_running,
    }

# ---------------------- API ----------------------
@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify(build_status())

@app.route("/api/relay", methods=["POST"])
def api_relay():
    if cycle_running:
        return jsonify({"error": "cycle_running", "message": "Цикл запущен — ручное управление реле временно заблокировано."}), 409
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "invalid json"}), 400

    name = data.get("name")
    action = data.get("action")
    ms = int(data.get("ms", 150))

    if name not in RELAY_PINS:
        return jsonify({"error": f"unknown relay '{name}'"}), 400

    if action == "on":
        _set_relay(name, True)
    elif action == "off":
        _set_relay(name, False)
    elif action == "pulse":
        _pulse(name, ms=ms)
    else:
        return jsonify({"error": "action must be 'on' | 'off' | 'pulse'"}), 400

    return jsonify(build_status())

@app.route("/api/cycle/start", methods=["POST"])
def api_cycle_start():
    global cycle_thread
    if cycle_running:
        return jsonify(build_status())
    # сбросим флаг остановки и стартанём поток
    cycle_stop.clear()
    cycle_thread = threading.Thread(target=cycle_worker, daemon=True)
    cycle_thread.start()
    # дадим потоку стартануть
    time.sleep(0.05)
    return jsonify(build_status())

@app.route("/api/cycle/stop", methods=["POST"])
def api_cycle_stop():
    if not cycle_running:
        return jsonify(build_status())
    cycle_stop.set()
    # подождём завершения корректно
    if cycle_thread is not None:
        cycle_thread.join(timeout=1.0)
    return jsonify(build_status())

# ---------------------- UI (HTML+JS) ----------------------
INDEX_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>RPi IO Panel</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,"Helvetica Neue",Arial,sans-serif;margin:20px;line-height:1.4}
  h1{margin:0 0 12px}
  .row{display:flex;gap:24px;flex-wrap:wrap}
  .card{border:1px solid #ddd;border-radius:12px;padding:16px;box-shadow:0 2px 6px rgba(0,0,0,.05);min-width:280px}
  table{width:100%;border-collapse:collapse}
  th,td{padding:8px;border-bottom:1px solid #eee;font-size:14px}
  .ok{color:#0a7a1f;font-weight:600}
  .off{color:#a00;font-weight:600}
  .btn{padding:6px 10px;border:1px solid #ccc;border-radius:8px;background:#fafafa;cursor:pointer}
  .btn:hover{background:#f0f0f0}
  .btn.on{border-color:#0a7a1f}
  .btn.off{border-color:#a00}
  .small{font-size:12px;color:#666}
  .controls{display:flex;gap:8px;flex-wrap:wrap}
  input[type=number]{width:80px;padding:6px;border:1px solid #ccc;border-radius:8px}
  .badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;background:#eee}
  .pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px}
  .pill.green{background:#d9f5df;color:#0a7a1f;border:1px solid #a7e5b2}
  .pill.gray{background:#eee;color:#333;border:1px solid #ddd}
  .muted{color:#777;font-size:12px}
  .disabled{opacity:.5;pointer-events:none}
</style>
</head>
<body>
  <h1>RPi IO Panel</h1>
  <div class="small" id="statusTime"></div>

  <div class="row">
    <div class="card" style="flex:1">
      <h3>Управление циклом</h3>
      <div id="cycleState" class="muted">Статус: неизвестно</div>
      <div class="controls" style="margin-top:8px">
        <button id="btnStart" class="btn">START</button>
        <button id="btnStop"  class="btn">STOP</button>
      </div>
      <div class="muted" style="margin-top:8px">Во время работы цикла ручное управление реле отключено.</div>
    </div>

    <div class="card" style="flex:1">
      <h3>Датчики (герконы)</h3>
      <table id="sensorsTbl">
        <thead><tr><th>Имя</th><th>Состояние</th></tr></thead>
        <tbody></tbody>
      </table>
    </div>

    <div class="card" style="flex:1">
      <h3>Реле</h3>
      <table id="relaysTbl">
        <thead><tr><th>Имя</th><th>Состояние</th><th>Управление</th></tr></thead>
        <tbody></tbody>
      </table>
      <div class="muted">Если цикл запущен, кнопки будут отключены.</div>
    </div>
  </div>

<script>
async function getStatus(){
  const res = await fetch('/api/status');
  if(!res.ok){throw new Error('status HTTP '+res.status)}
  return await res.json();
}
async function postRelay(name, action, ms){
  const payload = { name, action };
  if(action==='pulse' && ms) payload.ms = ms;
  const res = await fetch('/api/relay', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  if(res.status===409){
    const data = await res.json();
    alert(data.message || 'Цикл запущен. Ручное управление недоступно.');
    return null;
  }
  if(!res.ok){throw new Error('relay HTTP '+res.status)}
  return await res.json();
}
async function postCycle(action){
  const url = action==='start' ? '/api/cycle/start' : '/api/cycle/stop';
  const res = await fetch(url, {method:'POST'});
  if(!res.ok){throw new Error('cycle HTTP '+res.status)}
  return await res.json();
}

function renderCycleRunning(isRunning){
  const state = document.getElementById('cycleState');
  state.innerHTML = 'Статус: ' + (isRunning ? '<span class="pill green">RUNNING</span>' : '<span class="pill gray">STOPPED</span>');
  document.getElementById('relaysTbl').classList.toggle('disabled', isRunning);
}

function render(data){
  document.getElementById('statusTime').textContent = 'Обновлено: ' + data.time;
  renderCycleRunning(!!data.cycle_running);

  // sensors
  const sbody = document.querySelector('#sensorsTbl tbody');
  sbody.innerHTML = '';
  for(const name of data.sensor_names){
    const val = data.sensors[name];
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><span class="badge">${name}</span></td>
      <td>${val ? '<span class="ok">CLOSE</span>' : '<span class="off">OPEN</span>'}</td>
    `;
    sbody.appendChild(tr);
  }

  // relays
  const rbody = document.querySelector('#relaysTbl tbody');
  rbody.innerHTML = '';
  for(const name of data.relay_names){
    const val = data.relays[name];
    const tr = document.createElement('tr');
    const pulseId = 'pulse_'+name;
    tr.innerHTML = `
      <td><span class="badge">${name}</span></td>
      <td>${val ? '<span class="ok">ON</span>' : '<span class="off">OFF</span>'}</td>
      <td>
        <div class="controls">
          <button class="btn on"  onclick="cmd('${name}','on')">ON</button>
          <button class="btn off" onclick="cmd('${name}','off')">OFF</button>
          <input type="number" id="${pulseId}" min="20" value="150" title="Pulse, ms">
          <button class="btn" onclick="cmd('${name}','pulse', document.getElementById('${pulseId}').value)">PULSE</button>
        </div>
      </td>
    `;
    rbody.appendChild(tr);
  }
}

async function refresh(){
  try{
    const data = await getStatus();
    render(data);
  }catch(e){
    console.error(e);
  }
}
async function cmd(name, action, ms){
  const data = await postRelay(name, action, ms?parseInt(ms,10):undefined);
  if(data) render(data);
}

document.getElementById('btnStart').addEventListener('click', async ()=>{
  try{ render(await postCycle('start')); }catch(e){ alert('Ошибка запуска: '+e.message); }
});
document.getElementById('btnStop').addEventListener('click', async ()=>{
  try{ render(await postCycle('stop')); }catch(e){ alert('Ошибка остановки: '+e.message); }
});

refresh();
setInterval(refresh, 1000);
</script>
</body>
</html>
"""

@app.route("/", methods=["GET"])
def index():
    return Response(INDEX_HTML, mimetype="text/html")

# ---------------------- Запуск ----------------------
def main():
    # Тише в логах dev-сервера? Раскомментируй 2 строки ниже.
    # import logging
    # logging.getLogger('werkzeug').disabled = True
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=False, use_reloader=False)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        with io_lock:
            io.cleanup()
