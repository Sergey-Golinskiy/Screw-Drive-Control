const qs = (s) => document.querySelector(s);
const qsa = (s) => Array.from(document.querySelectorAll(s));

async function postJSON(url, body){
  const r = await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  if(!r.ok){ throw new Error(await r.text()); }
  return r.json();
}

// Live статус через SSE
try{
  const es = new EventSource('/events');
  es.onmessage = (e)=>{
    const d = JSON.parse(e.data);
    if (d.error) return;
    renderStatus(d);
  };
}catch{}

function renderStatus(d){
  qs('#modeText').textContent = d.mode_text || d.mode;
  qs('#faultText').textContent = (d.fault===0)?'OK':('0x'+Number(d.fault).toString(16).padStart(4,'0'));
  qs('#speed').textContent = d.speed ?? 0;
  qs('#taskCur').textContent = d.task_current ?? '—';
  qs('#di').textContent = '0b'+(Number(d.di)>>>0).toString(2).padStart(16,'0');
  qs('#do').textContent = '0b'+(Number(d.do)>>>0).toString(2).padStart(16,'0');
  qs('#auxMask').textContent = '0x'+(Number(d.aux)>>>0).toString(16).padStart(4,'0');
  if (d.last){
    const tq = (d.last.torque_mNm||0)/1000.0;
    const ang = (d.last.angle_decideg||0)/10.0;
    const tms = ((d.last.time_ms_hi||0)<<16) | (d.last.time_ms_lo||0);
    const map={0:'OK',1:'FLOAT',2:'STRIP',3:'NG'};
    qs('#lastTq').textContent = tq.toFixed(3);
    qs('#lastAng').textContent = ang.toFixed(1);
    qs('#lastTime').textContent = tms;
    qs('#lastRes').textContent = map[d.last.result] ?? d.last.result;
  }
}

// Переключение режима
qs('#btnModeRS').onclick = ()=> postJSON('/api/set_mode',{rs:true});
qs('#btnModeIO').onclick = ()=> postJSON('/api/set_mode',{rs:false});

qs('#btnRestart').onclick = async ()=>{
  const el = qs('#restartInfo');
  el.textContent = '…';
  try{
    const r = await postJSON('/api/restart',{});
    const fault = r.status?.fault, mode = r.status?.mode;
    el.textContent = `OK: MODE=${mode}, FAULT=0x${Number(fault||0).toString(16).padStart(4,'0')}`;
  }catch(e){
    el.textContent = 'ERR: ' + e.message;
  }
};

qs('#btnFaultReset').onclick = async ()=>{
  const el = qs('#restartInfo');
  el.textContent = '…';
  try{
    const r = await postJSON('/api/fault_reset',{});
    el.textContent = `Fault=0x${Number(r.fault||0).toString(16).padStart(4,'0')}`;
  }catch(e){
    el.textContent = 'ERR: ' + e.message;
  }
};


// Вирт. DI
qsa('[data-di]').forEach(btn=>{
  btn.onclick = async ()=>{
    const bit = parseInt(btn.dataset.di,10);
    const curTxt = qs('#auxMask').textContent.replace('0x','');
    let mask = parseInt(curTxt,16) || 0;
    const newVal = (mask & (1<<bit)) === 0;
    try{
      const r = await postJSON('/api/aux',{bit,value:newVal});
      if (r.mask !== undefined) qs('#auxMask').textContent = '0x'+r.mask.toString(16).padStart(4,'0');
    }catch(e){ alert('Ошибка /api/aux: '+e.message); }
  }
});
qs('#btnClr').onclick = async ()=>{
  try{ const r = await postJSON('/api/aux',{mask:0}); qs('#auxMask').textContent='0x0000'; }
  catch(e){ alert('Ошибка /api/aux: '+e.message); }
};

// Free-Run быстрый
qs('#fr2Set').onclick = async ()=>{
  const rpm = parseInt(qs('#fr2Rpm').value||'0',10);
  const tqtxt = qs('#fr2Tq').value.trim();
  const body = { rpm };
  if (tqtxt !== '') body.torque = parseFloat(tqtxt);
  try{
    const res = await postJSON('/api/fr_set', body);
    const rb = res.readback || {};
    qs('#fr2Info').textContent = `set rpm=${res.written?.rpm}`+
      (res.written?.torque_mNm!=null?`, tq=${res.written.torque_mNm}mN·m`:``)+
      ` | readback: E138 raw=${rb.fr_speed_raw}, signed=${rb.fr_speed_signed}; E139=${rb.fr_torque_limit}mN·m`;
  }catch(e){ qs('#fr2Info').textContent = 'Ошибка /api/fr_set: '+e.message; }
};
qs('#fr2Start').onclick = async ()=>{
  try{ const r=await postJSON('/api/fr_start'); qs('#fr2Info').textContent=`AUX=0x${(r.aux>>>0).toString(16).padStart(4,'0')}`; }
  catch(e){ qs('#fr2Info').textContent = 'Ошибка /api/fr_start: '+e.message; }
};
qs('#fr2Stop').onclick = async ()=>{
  try{ const r=await postJSON('/api/fr_stop'); qs('#fr2Info').textContent=`AUX=0x${(r.aux>>>0).toString(16).padStart(4,'0')}`; }
  catch(e){ qs('#fr2Info').textContent = 'Ошибка /api/fr_stop: '+e.message; }
};

// Параметры задач
qs('#taskLoad').onclick = async ()=>{
  const task = parseInt(qs('#taskSel').value,10);
  try{
    const js = await (await fetch(`/api/task_params?task=${task}`)).json();
    const t = js.task || {}; const g = js.globals || {};
    const m = (t.method && t.method.raw!==null)? t.method.raw : g.method;
    if (m!==undefined) qs('#pMethod').value = m;
    const tq_raw = (t.torque && t.torque.raw!==null)? t.torque.raw : g.torque_mNm;
    if (tq_raw!==undefined) qs('#pTorque').value = (tq_raw/1000.0).toFixed(3);
    const sp_raw = (t.speed && t.speed.raw!==null)? t.speed.raw : g.speed_rpm;
    if (sp_raw!==undefined) qs('#pSpeed').value = sp_raw;
    if (t.angle_lo && t.angle_hi && t.angle_lo.raw!==null && t.angle_hi.raw!==null){
      qs('#pAngle').value = ((t.angle_hi.raw<<16) | t.angle_lo.raw);
    }else{ qs('#pAngle').value = 0; }
    if (t.time_ms && t.time_ms.raw!==null){ qs('#pTime').value = t.time_ms.raw; } else { qs('#pTime').value = 0; }
  }catch(e){ alert('Ошибка загрузки task: '+e.message); }
};

qs('#taskSave').onclick = async ()=>{
  const body = {
    task: parseInt(qs('#taskSel').value,10),
    method: parseInt(qs('#pMethod').value,10),
    torque: parseFloat(qs('#pTorque').value||'0'),
    speed: parseInt(qs('#pSpeed').value||'0',10),
    angle: parseInt(qs('#pAngle').value||'0',10),
    time_ms: parseInt(qs('#pTime').value||'0',10),
  };
  try{ await postJSON('/api/task_params', body); }
  catch(e){ alert('Ошибка сохранения task: '+e.message); }
};

qs('#taskRun').onclick = async ()=>{
  const task = parseInt(qs('#taskSel').value,10);
  const hold = parseInt(qs('#pHold').value||'1000',10);
  try{ await postJSON('/api/run_task',{task, action:'tighten', hold_ms: hold}); }
  catch(e){ alert('Ошибка запуска tighten: '+e.message); }
};

function renderLog(list){
  const el = qs('#log');
  if (!el) return;
  el.innerHTML = '';
  list.forEach(ev=>{
    const row = document.createElement('div');
    row.className = 'row';
    row.innerHTML = `<span class="ts">${ev.ts}</span><span class="kind">${ev.kind}</span><span class="msg">${ev.msg}</span>`;
    el.appendChild(row);
  });
}

// периодически дергаем лог
setInterval(async ()=>{
  try{
    const r = await fetch('/api/ops');
    if (r.ok){
      const js = await r.json();
      renderLog(js.events || []);
    }
  }catch(_){}
}, 800);
