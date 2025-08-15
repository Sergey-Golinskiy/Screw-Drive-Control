import os, json, time, threading, asyncio
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Request # type: ignore
from fastapi.responses import FileResponse, StreamingResponse # type: ignore
from fastapi.staticfiles import StaticFiles # type: ignore
from pydantic import BaseModel # pyright: ignore[reportMissingImports]
from pymodbus.client import ModbusSerialClient # type: ignore
from collections import deque
from datetime import datetime

# ================= RS485/серверные настройки =================
CFG = {
    "port": os.environ.get("E350_PORT", "/dev/ttyUSB0"),
    "baud": int(os.environ.get("E350_BAUD", "115200")),
    "parity": os.environ.get("E350_PARITY", "N"),
    "stopbits": int(os.environ.get("E350_STOPBITS", "1")),
    "unit": int(os.environ.get("E350_UNIT", "1")),
    # База задач и шаг адресов (если прошивка хранит задачи «пачками»)
    # Можно задать одно число в task_bases и stride, либо массив баз для каждой задачи
    "task_bases": [0xE120],
    "task_stride": 0x20,
}

# ================ Карта регистров (валидна для нашей серии E350) ================
REG: Dict[str, int] = {
    # Режим/связь
    "MODE": 0xE002,         # 0=I/O, 1=RS485/232, 3=CAN, 4=ECAT
    "SLAVE": 0xE064,        # Modbus slave id
    "RS_CFG": 0xE065,       # код скорости/формата

    # Глобальные параметры затяжки (работают надёжно)
    "METHOD": 0xE130,       # 1=Torque, 0=Angle (по нашей практике)
    "TQ": 0xE12C,           # момент (mN·m)
    "TSPD": 0xE15E,         # скорость (RPM)

    # Free-Run
    "FR_SPD": 0xE138,       # signed16 RPM (-2000..2000)
    "FR_TQL": 0xE139,       # mN·m (если поддерживается)

    # IO/статусы
    "AUX_DI": 0x2098,       # виртуальные DI (bit3=DI4 – Free-Run)
    "TASK_CUR": 0x20C8,     # текущая задача (RO)
    "LAST_TQ": 0x20C9,      # далее 5 регов: момент, угол, t_hi, t_lo, result
    "REAL_SPD": 0x20E6,     # текущая скорость (RPM)
    "DI": 0x20F1,           # DI биты
    "DO": 0x20F2,           # DO биты
    "FAULT": 0x20F4,        # код ошибки
    "FAULT_RST": 0x2005,    # запись любого значения = сброс ошибки
}

# Оффсеты полей внутри блока «задача» (если поддерживается прошивкой)
TASK_OFF = {
    "method": 0x0000,   # метод (как правило)
    "torque": 0x0002,   # момент (mN·m)
    "angle_lo": 0x0008, # угол (младшее слово)
    "angle_hi": 0x0009, # угол (старшее слово)
    "time_ms": 0x000A,  # лимит времени (ms)
    "speed": 0x0032,    # скорость RPM
}

# ========================= Вспомогательное =========================
def s16_from_u16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if (v & 0x8000) else v

# ========================= Класс драйвера =========================
class E350:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.client = ModbusSerialClient(
            method="rtu",
            port=cfg["port"], baudrate=cfg["baud"],
            parity=cfg["parity"], stopbits=cfg["stopbits"],
            bytesize=8, timeout=1.2
        )
        if not self.client.connect():
            print("[WARN] serial not open yet; will retry on first call")

    # ---- Низкоуровневые операции ----
    def _ensure(self):
        if not self.client or not self.client.connected:
            self.client.connect()

    def r1(self, a: int) -> int:
        with self.lock:
            self._ensure()
            rr = self.client.read_holding_registers(address=a, count=1, slave=self.cfg["unit"])
        if rr.isError():
            raise RuntimeError(str(rr))
        return rr.registers[0]

    def rN(self, a: int, n: int) -> list:
        with self.lock:
            self._ensure()
            rr = self.client.read_holding_registers(address=a, count=n, slave=self.cfg["unit"])
        if rr.isError():
            raise RuntimeError(str(rr))
        return rr.registers

    def w1(self, a: int, v: int):
        with self.lock:
            self._ensure()
            wr = self.client.write_register(address=a, value=int(v) & 0xFFFF, slave=self.cfg["unit"])
        if wr.isError():
            raise RuntimeError(str(wr))

    # ---- Утилиты ----
    def get_task_base(self, task: int) -> Optional[int]:
        bases = self.cfg.get("task_bases", [])
        stride = int(self.cfg.get("task_stride", 0x20))
        if not bases:
            return None
        if len(bases) > 1 and task < len(bases) and isinstance(bases[task], int):
            return int(bases[task])
        return int(bases[0] + stride * task)


    def soft_restart(self) -> Dict[str, Any]:
        """Мягкий рестарт: сброс Fault, MODE=RS, реинициализация порта, snapshot."""
        # 1) сброс ошибок
        try:
            self.w1(REG["FAULT_RST"], 1)
            time.sleep(0.05)
        except Exception:
            pass

        # 2) RS-режим
        try:
            self.w1(REG["MODE"], 1)
        except Exception:
            pass

        # 3) реинициализация serial-клиента
        with self.lock:
            try:
                if self.client:
                    try:
                        self.client.close()
                    except Exception:
                        pass
            finally:
                from pymodbus.client import ModbusSerialClient  # на случай импорта выше
                self.client = ModbusSerialClient(
                    method="rtu",
                    port=self.cfg["port"],
                    baudrate=self.cfg["baud"],
                    parity=self.cfg["parity"],
                    stopbits=self.cfg["stopbits"],
                    bytesize=8,
                    timeout=1.2,
                )
                self.client.connect()

        time.sleep(0.1)

        # 4) вернуть текущий статус
        return self.snapshot()
    # ---- Глобальные параметры затяжки ----
    def set_globals(self, method: Optional[int] = None,
                    torque_nm: Optional[float] = None,
                    speed_rpm: Optional[int] = None):
        if method is not None:
            self.w1(REG["METHOD"], int(method))
        if torque_nm is not None:
            self.w1(REG["TQ"], int(round(float(torque_nm) * 1000)))
        if speed_rpm is not None:
            self.w1(REG["TSPD"], int(speed_rpm))

    def read_globals(self) -> Dict[str, int]:
        return {
            "method": self.r1(REG["METHOD"]),
            "torque_mNm": self.r1(REG["TQ"]),
            "speed_rpm": self.r1(REG["TSPD"]),
        }

    # ---- Параметры задач (мягкая запись с проверкой адресов) ----
    def write_task_params(self, task: int, params: Dict[str, Any]) -> Dict[str, Any]:
        base = self.get_task_base(task)
        if base is None:
            raise RuntimeError("Task base not configured; adjust CFG['task_bases']/['task_stride']")
        results: Dict[str, Any] = {}
        for key, off in TASK_OFF.items():
            if key not in params:
                continue
            addr = base + off
            val = params[key]
            if key == "torque":
                val = int(round(float(val) * 1000))  # Н·м -> мН·м
            # проба на чтение
            try:
                _ = self.r1(addr)
            except Exception as e:
                results[key] = {"addr": addr, "ok": False, "error": f"probe_read: {e}"}
                continue
            # запись
            try:
                self.w1(addr, int(val))
                results[key] = {"addr": addr, "ok": True}
            except Exception as e:
                results[key] = {"addr": addr, "ok": False, "error": str(e)}
        return results

    def read_task_params(self, task: int) -> Dict[str, Any]:
        base = self.get_task_base(task)
        if base is None:
            raise RuntimeError("Task base not configured")
        out: Dict[str, Any] = {}
        for k, off in TASK_OFF.items():
            addr = base + off
            try:
                raw = self.r1(addr)
            except Exception:
                raw = None
            out[k] = {"addr": addr, "raw": raw}
        return out

    # ---- Free-Run helpers ----
    def set_mode(self, rs: bool):
        self.w1(REG["MODE"], 1 if rs else 0)

    def set_freerun_speed(self, rpm: int):
        self.w1(REG["FR_SPD"], int(rpm) & 0xFFFF)

    def set_freerun_torque_limit_mNm(self, mNm: int) -> bool:
        try:
            _ = self.r1(REG["FR_TQL"])  # проверим, что адрес есть
        except Exception:
            return False
        self.w1(REG["FR_TQL"], int(mNm) & 0xFFFF)
        return True

    def set_aux_mask(self, mask: int):
        self.w1(REG["AUX_DI"], mask & 0xFFFF)

    def set_aux_di4(self, on: bool) -> int:
        try:
            cur = self.r1(REG["AUX_DI"])
        except Exception:
            cur = 0
        mask = (cur | (1 << 3)) if on else (cur & ~(1 << 3))
        self.w1(REG["AUX_DI"], mask)
        return mask

    # ---- Снимок состояния ----
    def snapshot(self) -> Dict[str, Any]:
        mode = self.r1(REG["MODE"])  # 0/1/3/4
        fault = self.r1(REG["FAULT"])
        di = self.r1(REG["DI"])
        do = self.r1(REG["DO"])
        spd = self.r1(REG["REAL_SPD"])
        aux = self.r1(REG["AUX_DI"]) if True else 0
        tcur = self.r1(REG["TASK_CUR"])
        last = self.rN(REG["LAST_TQ"], 5)
        snap = {
            "mode": mode, "fault": fault, "di": di, "do": do, "speed": spd, "aux": aux,
            "task_current": tcur,
            "last": {
                "torque_mNm": last[0],
                "angle_decideg": last[1],
                "time_ms_hi": last[2],
                "time_ms_lo": last[3],
                "result": last[4]
            }
        }
        # Free-Run read-back
        try:
            fr_raw = self.r1(REG["FR_SPD"])
            fr_signed = (fr_raw - 0x10000) if (fr_raw & 0x8000) else fr_raw
        except Exception:
            fr_raw = None
            fr_signed = None
        try:
            fr_tql = self.r1(REG["FR_TQL"])
        except Exception:
            fr_tql = None
        snap["fr_speed_raw"] = fr_raw
        snap["fr_speed_signed"] = fr_signed
        snap["fr_torque_limit"] = fr_tql
        return snap

# ========================= FastAPI =========================
ctrl = E350(CFG)
app = FastAPI(title="E350 Full Web Controller")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ---- Простой событийный лог ----
EVENTS = deque(maxlen=200)
_state = {
    "speed": 0,
    "last_res": None,
    "fault": 0,
}

def _add_event(kind: str, msg: str, extra: dict | None = None):
    EVENTS.appendleft({
        "ts": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "kind": kind,
        "msg": msg,
        "extra": extra or {}
    })

def _monitor_loop():
    # пороги детектирования, чтобы не дрожало
    SPEED_ON = 80     # RPM: считаем «поехал», если выше этого
    SPEED_OFF = 40    # RPM: считаем «остановился», если ниже этого
    prev_sp = 0
    prev_res = None
    prev_fault = 0
    while True:
        try:
            snap = ctrl.snapshot()
            sp = int(snap.get("speed") or 0)
            res = (snap.get("last") or {}).get("result")
            fault = int(snap.get("fault") or 0)

            # переходы скорости -> события фрирана
            if sp >= SPEED_ON and prev_sp < SPEED_ON:
                _add_event("FR_STARTED", f"free-run started, speed={sp} RPM")
            if sp <= SPEED_OFF and prev_sp > SPEED_OFF:
                _add_event("FR_STOPPED", f"free-run stopped, speed={sp} RPM")

            # завершение цикла tighten по изменению last.result
            if res is not None and res != prev_res:
                res_map = {0:"OK",1:"FLOAT",2:"STRIP",3:"NG"}
                _add_event("TIGHTEN_DONE", f"result={res_map.get(res, res)}",
                           {"result": res,
                            "torque_mNm": (snap.get("last") or {}).get("torque_mNm"),
                            "angle_decideg": (snap.get("last") or {}).get("angle_decideg")})
            # fault on/off
            if fault != prev_fault:
                if fault != 0:
                    _add_event("FAULT_ON", f"fault=0x{fault:04X}")
                else:
                    _add_event("FAULT_OFF", "fault cleared")

            prev_sp = sp
            prev_res = res
            prev_fault = fault
        except Exception as e:
            _add_event("MON_ERR", str(e))
        time.sleep(0.2)

# Запускаем монитор в фоне
threading.Thread(target=_monitor_loop, daemon=True).start()

@app.get("/api/ops")
def api_ops():
    # последние 50 для фронта
    return {"events": list(EVENTS)[:50]}

@app.post("/api/restart")
def api_restart():
    try:
        snap = ctrl.soft_restart()
        return {"ok": True, "status": snap}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.post("/api/fault_reset")
def api_fault_reset():
    try:
        ctrl.w1(REG["FAULT_RST"], 1)
        time.sleep(0.05)
        return {"ok": True, "fault": ctrl.r1(REG["FAULT"])}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@app.get("/")
def index():
    return FileResponse("static/index.html")

# --------- Модели запросов ---------
class ModeBody(BaseModel):
    rs: bool

class AuxBody(BaseModel):
    mask: Optional[int] = None
    bit: Optional[int] = None
    value: Optional[bool] = None

class FreeRunSetBody(BaseModel):
    rpm: int
    torque: Optional[float] = None  # Н·м (опционально)

class TaskParamsBody(BaseModel):
    task: int
    method: Optional[int] = None
    torque: Optional[float] = None
    speed: Optional[int] = None
    angle: Optional[int] = None
    time_ms: Optional[int] = None

class RunTaskBody(BaseModel):
    task: int
    action: str  # 'tighten' | 'freerun'
    hold_ms: int = 1000
    rpm: Optional[int] = None

# --------- Эндпоинты общего статуса ---------
@app.get("/api/status")
def status():
    snap = ctrl.snapshot()
    snap["mode_text"] = {0: "I/O", 1: "RS485/232", 3: "CAN", 4: "ECAT"}.get(snap["mode"], snap["mode"])
    return snap

# --------- Управление режимом и AUX ---------
@app.post("/api/set_mode")
def set_mode(body: ModeBody):
    ctrl.set_mode(body.rs)
    return {"ok": True}

@app.post("/api/aux")
def aux(body: AuxBody):
    if body.mask is not None:
        ctrl.set_aux_mask(body.mask)
        return {"ok": True, "mask": body.mask}
    if body.bit is None or body.value is None:
        raise HTTPException(400, "Provide mask OR bit+value")
    cur = ctrl.snapshot().get("aux", 0)
    new = (cur | (1 << body.bit)) if body.value else (cur & ~(1 << body.bit))
    ctrl.set_aux_mask(new)
    return {"ok": True, "mask": new}

# --------- Free-Run: set / start / stop ---------
@app.post("/api/fr_set")
def api_fr_set(body: FreeRunSetBody):
    try:
        ctrl.set_mode(True)  # RS
        rpm = max(-2000, min(2000, int(body.rpm)))
        ctrl.set_freerun_speed(rpm)
        lim_ok = None
        if body.torque is not None:
            lim_ok = ctrl.set_freerun_torque_limit_mNm(int(round(body.torque * 1000)))
        snap = ctrl.snapshot()
        return {
            "ok": True,
            "written": {
                "rpm": rpm,
                "torque_mNm": (None if body.torque is None else int(round(body.torque * 1000)))
            },
            "readback": {
                "fr_speed_raw": snap["fr_speed_raw"],
                "fr_speed_signed": snap["fr_speed_signed"],
                "fr_torque_limit": snap["fr_torque_limit"],
            },
            "limit_written": lim_ok
        }
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.post("/api/fr_start")
def api_fr_start():
    try:
        ctrl.set_mode(True)
        mask = ctrl.set_aux_di4(True)
        return {"ok": True, "aux": mask}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

@app.post("/api/fr_stop")
def api_fr_stop():
    try:
        mask = ctrl.set_aux_di4(False)
        return {"ok": True, "aux": mask}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

# --------- Параметры задач и запуск Tighten ---------
@app.post("/api/task_params")
def set_task_params(body: TaskParamsBody):
    params = {}
    if body.method is not None:
        params["method"] = int(body.method)
    if body.torque is not None:
        params["torque"] = float(body.torque)
    if body.speed is not None:
        params["speed"] = int(body.speed)
    if body.time_ms is not None:
        params["time_ms"] = int(body.time_ms)
    if body.angle is not None:
        params["angle_lo"] = int(body.angle) & 0xFFFF
        params["angle_hi"] = (int(body.angle) >> 16) & 0xFFFF

    details = ctrl.write_task_params(body.task, params)

    # Дублируем в глобальные регистры (по опыту — надёжнее)
    ctrl.set_globals(
        method=(body.method if body.method is not None else None),
        torque_nm=(body.torque if body.torque is not None else None),
        speed_rpm=(body.speed if body.speed is not None else None),
    )

    return {"ok": True, "details": details, "globals": ctrl.read_globals()}

@app.get("/api/task_params")
def get_task_params(task: int):
    return {"task": ctrl.read_task_params(task), "globals": ctrl.read_globals()}

@app.post("/api/run_task")
def run_task(body: RunTaskBody):
    ctrl.set_mode(True)
    t = int(body.task)
    # по нашей легенде: DI5=TSK0, DI6=TSK1
    task_bits = 0
    if t & 1:
        task_bits |= (1 << 4)
    if t & 2:
        task_bits |= (1 << 5)

    if body.action == "tighten":
        # выставляем выбор задачи + DI1, держим hold_ms, отпускаем DI1, оставляем выбор задачи как был
        cur_mask = ctrl.r1(REG["AUX_DI"]) if True else 0
        mask_on = (cur_mask | task_bits | (1 << 0))
        ctrl.set_aux_mask(mask_on)
        time.sleep(max(0.05, body.hold_ms / 1000.0))
        ctrl.set_aux_mask(mask_on & ~(1 << 0))
        return {"ok": True}

    if body.action == "freerun":
        if body.rpm is not None:
            ctrl.set_freerun_speed(int(body.rpm))
        ctrl.set_aux_mask(task_bits | (1 << 3))  # DI4
        return {"ok": True}

    raise HTTPException(400, "action must be 'tighten' or 'freerun'")

# --------- SSE события ---------
@app.get("/events")
async def events(request: Request):
    async def gen():
        while True:
            if await request.is_disconnected():
                break
            try:
                payload = json.dumps(ctrl.snapshot())
            except Exception as e:
                payload = json.dumps({"error": str(e)})
            yield f"data: {payload}\n\n"
            await asyncio.sleep(0.5)
    return StreamingResponse(gen(), media_type="text/event-stream")