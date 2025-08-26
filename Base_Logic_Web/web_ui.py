#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import time
import threading
from functools import wraps
from flask import Flask, request, jsonify, Response
from cycle_onefile import IOController, RELAY_PINS, SENSOR_PINS

# ---------------------- Инициализация железа ----------------------
io_lock = threading.Lock()
io = IOController()  # один общий контроллер

app = Flask(__name__)

def with_io_lock(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with io_lock:
            return fn(*args, **kwargs)
    return wrapper

# ---------------------- API ----------------------
@app.route("/api/status", methods=["GET"])
@with_io_lock
def api_status():
    # реле: словарь имя -> True/False
    relays = dict(io.relays)
    # датчики: имя -> True(CLOSE)/False(OPEN)
    sensors = {name: io.sensor_state(name) for name in SENSOR_PINS.keys()}
    return jsonify({
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "relays": relays,
        "sensors": sensors,
        "relay_names": list(RELAY_PINS.keys()),
        "sensor_names": list(SENSOR_PINS.keys()),
    })

@app.route("/api/relay", methods=["POST"])
@with_io_lock
def api_relay():
    """
    JSON:
    { "name": "R02_C1_UP", "action": "on"|"off"|"pulse", "ms": 150 }
    """
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
        io.set_relay(name, True)
    elif action == "off":
        io.set_relay(name, False)
    elif action == "pulse":
        io.pulse(name, ms=ms)
    else:
        return jsonify({"error": "action must be 'on' | 'off' | 'pulse'"}), 400

    # вернуть актуальное состояние после команды
    return api_status()

# Пример спец-команд для отвёртки (если уже добавил шорткаты в cycle_onefile.py):
# from cycle_onefile import ...
# @app.route("/api/screwdriver/task0", methods=["POST"])
# @with_io_lock
# def api_task0():
#     io.screwdriver_select_task0()  # 700 мс
#     return api_status()

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
</style>
</head>
<body>
  <h1>RPi IO Panel</h1>
  <div class="small" id="statusTime"></div>

  <div class="row">
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
  if(!res.ok){throw new Error('relay HTTP '+res.status)}
  return await res.json();
}

function render(data){
  document.getElementById('statusTime').textContent = 'Обновлено: ' + data.time;

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
  try{
    const data = await postRelay(name, action, ms?parseInt(ms,10):undefined);
    render(data);
  }catch(e){
    alert('Ошибка: '+e.message);
  }
}

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
    # Запускаем Flask в однопоточном режиме (для предсказуемой работы с GPIO)
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=False)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    finally:
        with io_lock:
            io.cleanup()
