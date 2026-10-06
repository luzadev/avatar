// LuZa remoto: WebSocket per stato/registro/audio, microfono via MediaRecorder, audio via AudioContext.
const q = new URLSearchParams(location.search);
if (q.get('k')) { try { localStorage.setItem('luza_key', q.get('k')); } catch (e) {} history.replaceState(null, '', location.pathname); }
let KEY = ''; try { KEY = localStorage.getItem('luza_key') || ''; } catch (e) {}
const $ = id => document.getElementById(id);
let ws, actx, playing = false, queue = [], rec, chunks = [], recording = false, currentAva = null;

async function savePair() {
  const v = $('key').value.trim();
  if (!v) return;
  if (/^\d{6}$/.test(v)) {   // PIN mostrato da Remote Control: lo scambio con la chiave
    try {
      const r = await fetch(`/pair?pin=${v}`, {cache: 'no-store'}); const j = await r.json();
      if (!r.ok || !j.token) { addMsg(j.error || 'PIN rifiutato', 'err'); return; }
      KEY = j.token;
    } catch (e) { addMsg('rete: ' + e.message, 'err'); return; }
  } else KEY = v;
  try { localStorage.setItem('luza_key', KEY); } catch (e) {}
  $('key').value = ''; connect();
}
function setConn(t, ok) {
  if (ok) document.querySelectorAll('#log .m.err').forEach(e => e.remove()); $('conn').textContent = t + (mode === 'sse' ? ' · eventi' : mode === 'poll' ? ' · interrogazione' : ''); $('conn').style.color = ok ? '#00ff88' : '#ff3366'; }
const HUD = {state: 'LISTENING', level: 0, rec: false};
function setState(s) {
  const base = s.split(' ')[0], detail = s.includes(' · ') ? s.split(' · ').slice(1).join(' · ') : '';
  HUD.state = base; updateStop();
  $('stbase').textContent = base; $('stdetail').textContent = detail ? ' · ' + detail : ({LISTENING: ' · in ascolto', THINKING: ' · penso', PROCESSING: ' · elaboro', SPEAKING: ' · parlo', SLEEPING: ' · a riposo'}[base] || '');
}
function addMsg(text, cls) {
  const d = document.createElement('div'); d.className = 'm ' + cls; d.textContent = text; $('log').appendChild(d);
  while ($('log').children.length > 200) $('log').removeChild($('log').firstChild);
  $('log').scrollTop = $('log').scrollHeight; return d;
}
function onLog(line) {
  const m = line.match(/^(You(?: \(telefono\))?|SYS|ERR|FILE|[^:]{1,24}): ?([\s\S]*)$/);
  if (!m) { addMsg(line, 'sys'); return; }
  const who = m[1], txt = m[2];
  if (who.startsWith('You')) addMsg(txt, 'you');
  else if (who === 'SYS' || who === 'FILE') addMsg(txt, 'sys');
  else if (who === 'ERR') addMsg(txt, 'err');
  else addMsg(txt, 'ava');
}
let wsFailures = 0, es = null, mode = 'ws';
function handle(d) {
  if (d.type === 'state') setState(d.state);
  else if (d.type === 'log') onLog(d.text);
  else if (d.type === 'audio') enqueueAudio(d.wav);
  else if (d.type === 'confirm') { $('ct').textContent = d.title; $('cd').textContent = d.detail || ''; $('confirm').style.display = 'block'; }
  else if (d.type === 'confirm_hide') $('confirm').style.display = 'none';
  else if (d.type === 'image') addImage(d.url, d.name);
  else if (d.type === 'file') { const m = addMsg('', 'ava file'); const a = document.createElement('a'); a.href = d.url; a.target = '_blank'; a.textContent = '📄 ' + d.name; m.appendChild(a); }
}
function addImage(url, name) {
  const d = document.createElement('div'); d.className = 'm ava img';
  const img = document.createElement('img'); img.src = url; img.alt = name || 'immagine'; img.loading = 'lazy';
  img.onclick = () => { const v = document.createElement('div'); v.className = 'viewer'; const big = document.createElement('img'); big.src = url; v.appendChild(big); v.onclick = () => v.remove(); document.body.appendChild(v); };
  d.appendChild(img); $('log').appendChild(d); $('log').scrollTop = $('log').scrollHeight;
}
function unpair(msg) { KEY = ''; try { localStorage.removeItem('luza_key'); } catch (e) {} $('pair').style.display = 'flex'; setConn(msg || 'chiave rifiutata', false); addMsg('Chiave non valida: inquadra di nuovo il QR dal Mac oppure incolla la chiave.', 'err'); }
async function connect() {
  if (!KEY) { $('pair').style.display = 'flex'; setConn('non abbinato', false); return; }
  $('pair').style.display = 'none';
  try {   // verifica della chiave con una richiesta breve, prima di aprire i canali
    const r = await fetch(`/auth?k=${encodeURIComponent(KEY)}`, {cache: 'no-store'});
    if (r.status === 401) return unpair('chiave rifiutata');
  } catch (e) { setConn('rete non raggiungibile, riprovo…', false); return setTimeout(connect, 3000); }
  if (mode === 'poll') return startPoll();
  if (mode === 'sse') return connectSSE();
  try { ws && ws.close(); } catch (e) {}
  let opened = false;
  ws = new WebSocket(`wss://${location.host}/ws?k=${encodeURIComponent(KEY)}`);
  ws.onopen = () => { opened = true; wsFailures = 0; setConn('collegato', true); };
  ws.onclose = ev => {
    if (!opened) wsFailures++;
    if (ev.code === 1008) return unpair();
    if (wsFailures >= 2) { mode = 'sse'; return connectSSE(); }   // WebSocket bloccato (app sulla Home di iOS): canale di riserva
    setConn('scollegato, riprovo…', false); setTimeout(connect, 2000);
  };
  ws.onerror = () => {};
  ws.onmessage = ev => handle(JSON.parse(ev.data));
}
let sseFailures = 0, CID = Math.random().toString(36).slice(2), polling = false;
function connectSSE() {
  try { es && es.close(); } catch (e) {}
  es = new EventSource(`/events?k=${encodeURIComponent(KEY)}`);
  es.onopen = () => { sseFailures = 0; setConn('collegato', true); };
  es.onmessage = ev => handle(JSON.parse(ev.data));
  es.onerror = () => {
    es.close(); sseFailures++;
    if (sseFailures >= 2) { mode = 'poll'; return startPoll(); }   // anche gli eventi sono bloccati: interrogazione ripetuta
    setConn('scollegato, riprovo…', false); setTimeout(connectSSE, 2500);
  };
}
async function startPoll() {
  if (polling) return; polling = true;
  setConn('collegato', true);
  while (mode === 'poll') {
    try {
      const r = await fetch(`/poll?k=${encodeURIComponent(KEY)}&cid=${CID}`, {cache: 'no-store'});
      if (r.status === 401) { polling = false; return unpair(); }
      const j = await r.json(); (j.events || []).forEach(handle); setConn('collegato', true);
    } catch (e) { setConn('scollegato, riprovo…', false); await new Promise(res => setTimeout(res, 2500)); }
  }
  polling = false;
}
function send(obj) {
  if (mode === 'ws' && ws && ws.readyState === 1) { ws.send(JSON.stringify(obj)); return; }
  fetch(`/cmd?k=${encodeURIComponent(KEY)}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(obj)})
    .then(r => { if (r.status === 401) unpair(); })
    .catch(() => addMsg('non collegato', 'err'));
}
function sendText() { const t = $('txt').value.trim(); if (!t) return; unlockAudio(); send({type: 'say', text: t}); $('txt').value = ''; onTyping(); }
function onTyping() { $('sendb').classList.toggle('on', $('txt').value.trim().length > 0); }
function updateStop() { const busy = playing || ['THINKING', 'PROCESSING', 'SPEAKING'].includes(HUD.state); $('stopb').classList.toggle('on', busy); }
function doConfirm(ok) { send({type: 'confirm', ok}); $('confirm').style.display = 'none'; }

// ── audio in uscita: coda di frasi WAV riprodotte in sequenza ─────────────
let analyser = null, adata = null, micAnalyser = null, mdata = null;
function unlockAudio() {
  if (!actx) { actx = new (window.AudioContext || window.webkitAudioContext)(); analyser = actx.createAnalyser(); analyser.fftSize = 256; analyser.connect(actx.destination); adata = new Uint8Array(analyser.frequencyBinCount); }
  if (actx.state === 'suspended') actx.resume();
}
function enqueueAudio(b64) { queue.push(b64); if (!playing) playNext(); }
async function playNext() {
  if (!queue.length) { playing = false; updateStop(); return; }
  playing = true; updateStop();
  try {
    unlockAudio();
    const b64 = queue.shift(); const bin = atob(b64); const buf = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
    const audio = await actx.decodeAudioData(buf.buffer);
    const src = actx.createBufferSource(); src.buffer = audio; src.connect(analyser); src.onended = playNext; src.start();
  } catch (e) { addMsg('audio non riproducibile: tocca lo schermo e riprova', 'err'); playing = false; }
}

// ── microfono: tocca per registrare, tocca di nuovo per inviare ────────────
async function toggleMic() {
  unlockAudio();
  if (recording) { recording = false; rec.stop(); $('mic').classList.remove('rec'); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    const mime = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm'].find(m => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || '';
    try { const msrc = actx.createMediaStreamSource(stream); micAnalyser = actx.createAnalyser(); micAnalyser.fftSize = 256; msrc.connect(micAnalyser); mdata = new Uint8Array(micAnalyser.frequencyBinCount); } catch (e) { micAnalyser = null; }
    rec = new MediaRecorder(stream, mime ? {mimeType: mime} : undefined); chunks = [];
    rec.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
    rec.onstop = async () => {
      stream.getTracks().forEach(t => t.stop()); micAnalyser = null; HUD.rec = false;
      const blob = new Blob(chunks, {type: rec.mimeType || 'audio/webm'});
      $('stbase').textContent = 'PROCESSING'; $('stdetail').textContent = ' · trascrivo';
      try {
        const r = await fetch(`/stt?k=${encodeURIComponent(KEY)}&run=1`, {method: 'POST', body: blob, headers: {'Content-Type': blob.type || 'application/octet-stream'}});
        const j = await r.json();
        if (!j.text) addMsg(j.error || 'non ho capito, riprova', 'err');
      } catch (e) { addMsg('invio audio fallito: ' + e, 'err'); }
    };
    rec.start(); recording = true; HUD.rec = true; $('mic').classList.add('rec'); $('stbase').textContent = 'REC'; $('stdetail').textContent = ' · registro, tocca di nuovo per inviare';
  } catch (e) { addMsg('microfono non disponibile: serve HTTPS con certificato accettato (' + e.message + ')', 'err'); }
}
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
document.addEventListener('touchstart', unlockAudio, {once: true, passive: true});
if (navigator.standalone) addMsg('app sulla Home', 'sys');
if (q.get('demo')) { setTimeout(() => { addMsg('Ciao LuZa, che tempo fa oggi a Pescara?', 'you'); addMsg('Lavoro sul Mac (meteo)…', 'sys'); addMsg('Oggi a Pescara è prevalentemente sereno, con una massima di 22 gradi. Domani un po\' di nuvole.', 'ava'); setState('THINKING · genero la risposta · 120 token · 59/s'); HUD.level = 0.55; }, 300); }
fetch('/health').then(r => r.ok ? null : addMsg('server risponde ' + r.status, 'err')).catch(e => addMsg('rete: ' + e.message, 'err'));
connect();

// ── impostazioni: motore, modello, ragionamento, voce ─────────────────────
const LABELS = {provider: 'Motore', effort: 'Profondità (Claude)', mlx_model: 'Modello interno', mlx_thinking: 'Ragionamento (interno)', claudecode_model: 'Modello Claude Code',
  tts_engine: 'Voce', kokoro_voice: 'Voce Kokoro', qwen_voce: 'Voce clonata', voicebox_profile_id: 'Profilo Voicebox', immagini_famiglia: 'Immagini: famiglia', immagini_modello: 'Immagini: modello'};
const SHOW_IF = {mlx_model: v => v.provider === 'mlx', mlx_thinking: v => v.provider === 'mlx', claudecode_model: v => v.provider === 'claudecode', effort: v => v.provider !== 'mlx' && v.provider !== 'local',
  kokoro_voice: v => v.tts_engine === 'kokoro', qwen_voce: v => v.tts_engine === 'qwen', voicebox_profile_id: v => v.tts_engine === 'voicebox'};
let sdata = null;
async function openSettings() {
  $('settings').style.display = 'block'; $('shint').textContent = 'carico…';
  try { const r = await fetch(`/settings?k=${encodeURIComponent(KEY)}`, {cache: 'no-store'}); sdata = await r.json(); } catch (e) { $('shint').textContent = 'errore: ' + e.message; return; }
  renderSettings(); $('shint').textContent = '';
}
function renderSettings() {
  const f = $('sform'); f.innerHTML = ''; const v = sdata.values;
  for (const k of Object.keys(LABELS)) {
    const opts = sdata.options[k]; if (!opts || (SHOW_IF[k] && !SHOW_IF[k](v))) continue;
    const lab = document.createElement('label'); lab.textContent = LABELS[k]; f.appendChild(lab);
    const sel = document.createElement('select'); sel.dataset.key = k;
    let found = false;
    for (const [val, name] of opts) { const o = document.createElement('option'); o.value = val; o.textContent = name; if (String(val) === String(v[k] ?? '')) { o.selected = true; found = true; } sel.appendChild(o); }
    if (!found && v[k]) { const o = document.createElement('option'); o.value = v[k]; o.textContent = v[k] + ' (attuale)'; o.selected = true; sel.appendChild(o); }
    sel.onchange = () => { v[k] = sel.value; renderSettings(); }; f.appendChild(sel);
  }
}
async function saveSettings() {
  if (!sdata) return; const out = {}; document.querySelectorAll('#sform select').forEach(s => out[s.dataset.key] = s.value);
  $('shint').textContent = 'applico…';
  try { const r = await fetch(`/settings?k=${encodeURIComponent(KEY)}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(out)}); const j = await r.json();
    $('shint').textContent = j.ok ? 'applicato: il Mac ricarica motore e voce' : ('errore: ' + j.error); } catch (e) { $('shint').textContent = 'errore: ' + e.message; }
}

// ── HUD: nucleo con archi rotanti e forma d'onda ──────────────────────────
const COL = {LISTENING: '#00ff88', THINKING: '#ffcc00', PROCESSING: '#ffcc00', SPEAKING: '#ff6b00', SLEEPING: '#3a8a9a', REC: '#ff3366'};
const ringC = $('ring'), waveC = $('wave');
function fitCanvas(c) { const r = window.devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight; if (c.width !== w * r || c.height !== h * r) { c.width = w * r; c.height = h * r; } return [c.getContext('2d'), w, h, r]; }
function level() {
  if (HUD.rec && micAnalyser) { micAnalyser.getByteTimeDomainData(mdata); let m = 0; for (const v of mdata) m = Math.max(m, Math.abs(v - 128)); return Math.min(1, m / 60); }
  if (playing && analyser) { analyser.getByteTimeDomainData(adata); let m = 0; for (const v of adata) m = Math.max(m, Math.abs(v - 128)); return Math.min(1, m / 50); }
  return 0;
}
let t0 = performance.now();
function drawHUD(now) {
  const t = (now - t0) / 1000; const lv = level(); HUD.level += (lv - HUD.level) * 0.35;
  const st = HUD.rec ? 'REC' : (playing ? 'SPEAKING' : HUD.state); const col = COL[st] || '#00d4ff';
  const busy = st === 'THINKING' || st === 'PROCESSING';
  // nucleo
  let [g, w, h, r] = fitCanvas(ringC); g.setTransform(r, 0, 0, r, 0, 0); g.clearRect(0, 0, w, h);
  const cx = w / 2, cy = h / 2 - 8, R = Math.min(w, h) * 0.34;
  const speed = busy ? 2.2 : st === 'SPEAKING' ? 1.6 : 0.5;
  g.lineCap = 'round';
  const arcs = [[1.0, 0.9, 1, 1.4], [0.86, -0.6, 2, 1.1], [0.72, 1.3, 1, 0.8], [0.58, -1.9, 3, 0.6]];
  for (const [rr, dir, n, span] of arcs) {
    for (let i = 0; i < n; i++) {
      const a0 = t * speed * dir + i * (Math.PI * 2 / n); g.beginPath(); g.arc(cx, cy, R * rr, a0, a0 + span); g.strokeStyle = col; g.globalAlpha = 0.85 - (1 - rr) * 0.9; g.lineWidth = 2; g.shadowColor = col; g.shadowBlur = 10; g.stroke();
    }
  }
  g.globalAlpha = 1; g.shadowBlur = 0;
  // tacche
  g.strokeStyle = 'rgba(0,212,255,.35)'; g.lineWidth = 1;
  for (let i = 0; i < 60; i++) { const a = i / 60 * Math.PI * 2 + t * 0.15; const l = i % 5 === 0 ? 8 : 4; g.beginPath(); g.moveTo(cx + Math.cos(a) * (R * 1.12), cy + Math.sin(a) * (R * 1.12)); g.lineTo(cx + Math.cos(a) * (R * 1.12 + l), cy + Math.sin(a) * (R * 1.12 + l)); g.stroke(); }
  // sfera centrale che respira con l'audio
  const rad = R * 0.42 * (1 + HUD.level * 0.35 + (busy ? Math.sin(t * 6) * 0.04 : Math.sin(t * 1.5) * 0.02));
  const grad = g.createRadialGradient(cx, cy, rad * 0.1, cx, cy, rad); grad.addColorStop(0, col); grad.addColorStop(0.55, col + '66'); grad.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = grad; g.beginPath(); g.arc(cx, cy, rad, 0, Math.PI * 2); g.fill();
  g.strokeStyle = col; g.lineWidth = 1.5; g.shadowColor = col; g.shadowBlur = 16; g.beginPath(); g.arc(cx, cy, rad * 0.9, 0, Math.PI * 2); g.stroke(); g.shadowBlur = 0;
  // forma d'onda
  [g, w, h, r] = fitCanvas(waveC); g.setTransform(r, 0, 0, r, 0, 0); g.clearRect(0, 0, w, h);
  const N = 42, bw = w / N;
  for (let i = 0; i < N; i++) {
    const env = Math.pow(1 - Math.abs(i - (N - 1) / 2) / ((N - 1) / 2), 0.7);
    const idle = 2 + 2 * Math.sin(t * 2.2 + i * 0.6);
    const hh = Math.max(2, Math.min(h - 4, idle + HUD.level * (h - 6) * env * (0.6 + 0.4 * Math.sin(t * 9 + i))));
    g.fillStyle = HUD.level > 0.05 ? col : 'rgba(0,212,255,.25)'; g.fillRect(i * bw + 1, (h - hh) / 2, bw - 2, hh);
  }
  requestAnimationFrame(drawHUD);
}
requestAnimationFrame(drawHUD);
