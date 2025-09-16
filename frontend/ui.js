// frontend/ui.js
const $ = (id) => document.getElementById(id);

let es = null;
let reconnectTimer = null;
let lastPingAt = null;
let backoffMs = 1000;
let uiCooldownUntil = 0; // optional UI backoff window after LLM rate limits

const statusBadge = $("statusBadge");
const logStream = $("logStream");
const transcriptEl = $("transcript");
const replyEl = $("reply");
const latencyEl = $("latency");
const stateEl = $("connectionState");

const patterns = [
  /\b(hey\s+)?jarvis\b/i,
  /\bcomputer\b/i,
];

function setStatus(state){
  statusBadge.className = "badge";
  if(state === "idle"){ statusBadge.classList.add("idle"); statusBadge.textContent = "IDLE"; }
  if(state === "listening"){ statusBadge.classList.add("listen"); statusBadge.textContent = "LISTENING"; }
  if(state === "processing"){ statusBadge.classList.add("proc"); statusBadge.textContent = "PROCESSING"; }
}

function log(kind, k, v){
  const div = document.createElement("div");
  div.className = `log-item ${kind || ""}`;
  const t = new Date().toLocaleTimeString();
  div.innerHTML = `<span class="k">[${t}] ${k}:</span> <span class="v">${escapeHtml(String(v))}</span>`;
  logStream.appendChild(div);
  logStream.scrollTop = logStream.scrollHeight;
}

function escapeHtml(s){
  return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function setConnectedUI(connected){
  $("connectBtn").disabled = connected;
  $("disconnectBtn").disabled = !connected;
  stateEl.textContent = connected ? "connected" : "disconnected";
}

function connect(){
  const base = $("serverInput").value.trim() || "http://localhost:8000";
  if(es){ try{ es.close(); }catch{} es = null; }
  setConnectedUI(true);
  backoffMs = 1000;

  es = new EventSource(`${base}/events`);
  log("info","connect",`opening ${base}/events`);

  es.addEventListener("status", (ev)=>{
    const s = (ev.data || "").trim();
    setStatus(s);
    log("info","status", s);
  });

  es.addEventListener("transcript", (ev)=>{
    const obj = JSON.parse(ev.data || "{}");
    const text = String(obj.text || "");
    const wake = !!obj.wake;
    transcriptEl.textContent = text;   // always show
    if (wake) flashRing();
    log("info", "transcript", text + (wake ? " [wake]" : ""));
  });

  es.addEventListener("reply", (ev)=>{
    const txt = (ev.data || "").trim();
    typeReply(txt);
    log("info","reply", txt);
  });

  es.addEventListener("action", (ev)=>{
    log("action","action", ev.data || "");
  });

  // Important: the server emits 'error' events but keeps SSE alive.
  // We reflect in the UI without dropping the connection.
  es.addEventListener("app_error", (ev)=>{
    const msg = (ev.data || "unknown error").trim();
    log("err","error", msg);
    statusBadge.className = "badge err";
    statusBadge.textContent = "ERROR";

    // Optional client-side backoff if rate-limited
    const m = msg.match(/rate_limited.*?(\d+)s/);
    if (m) {
      const seconds = parseInt(m[1], 10) || 0;
      uiCooldownUntil = Date.now() + seconds * 1000;
      log("info","cooldown", `UI cooling down for ${seconds}s`);
    }
  });

  es.addEventListener("debug", (ev)=>{
    log("debug", "debug", ev.data || "");
  });

  es.addEventListener("ping", (ev)=>{
    lastPingAt = performance.now();
    setTimeout(()=>{
      const ms = Math.floor(performance.now() - lastPingAt);
      latencyEl.textContent = `${ms} ms`;
    }, 0);
  });

  // Do NOT close on server 'error' events (we keep SSE alive).
  // Only reconnect if the transport itself errors out:
  es.onerror = () => {
    log("err","sse","connection lost");
    setConnectedUI(false);
    try { es && es.close(); } catch {}
    es = null;
    scheduleReconnect();
  };

  es.onopen = () => {
    log("info","sse","open");
  };
}

function scheduleReconnect(){
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(()=>{
    backoffMs = Math.min(backoffMs * 1.6, 15000);
    connect();
  }, backoffMs);
  log("info","reconnect", `in ${backoffMs}ms`);
}

function disconnect(){
  clearTimeout(reconnectTimer);
  if(es){ es.close(); es = null; }
  setConnectedUI(false);
  log("info","disconnect","closed");
}

$("connectBtn").onclick = connect;
$("disconnectBtn").onclick = disconnect;

$("listenOnceBtn").onclick = async ()=>{
  // Optional: respect UI cooldown (user feedback, not required)
  if (uiCooldownUntil && Date.now() < uiCooldownUntil) {
    const left = Math.max(0, Math.ceil((uiCooldownUntil - Date.now())/1000));
    log("info","listen-once","cooldown active, skipping");
    typeReply(`Cooling down… try again in ${left}s`);
    return;
  }

  const base = $("serverInput").value.trim() || "http://localhost:8000";
  try{
    const r = await fetch(`${base}/listen-once`);
    const j = await r.json();
    transcriptEl.textContent = j.transcript || "";
    typeReply(j.wake ? "Wake phrase detected." : "No wake phrase.");
    log("info","listen-once", JSON.stringify(j));
  }catch(e){
    log("err","listen-once", e.message || String(e));
  }
};

// “typing” effect for Jarvis replies
function typeReply(text){
  replyEl.textContent = "";
  let i = 0;
  const tick = () => {
    if(i <= text.length){
      replyEl.textContent = text.slice(0,i);
      i += Math.max(1, Math.floor(text.length/120));
      requestAnimationFrame(tick);
    }
  };
  tick();
}

// center ring flash
function flashRing(){
  const ring = document.querySelector(".ring");
  ring.style.filter = "drop-shadow(0 0 30px rgba(83,255,165,.7))";
  setTimeout(()=> ring.style.filter = "", 180);
}

// Animated background (unchanged)
const canvas = $("bg");
const ctx = canvas.getContext("2d", { alpha: true });
let w=0,h=0, t=0; const parts = [];
function resize(){
  w = canvas.width = window.innerWidth * devicePixelRatio;
  h = canvas.height = window.innerHeight * devicePixelRatio;
}
window.addEventListener("resize", resize); resize();

for(let i=0;i<80;i++){
  parts.push({
    x: Math.random()*1,
    y: Math.random()*1,
    z: Math.random()*0.7 + 0.3,
    s: Math.random()*0.6 + 0.2,
    p: Math.random()*Math.PI*2
  });
}
function draw(){
  t += 0.0025;
  ctx.clearRect(0,0,w,h);

  ctx.strokeStyle = "rgba(23,48,85,0.35)";
  ctx.lineWidth = 1;
  const gx = 60 * devicePixelRatio, gy = 60 * devicePixelRatio;
  for(let x=0;x<w; x+=gx){
    ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,h); ctx.stroke();
  }
  for(let y=0;y<h; y+=gy){
    ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(w,y); ctx.stroke();
  }

  for(const p of parts){
    const px = (p.x * w + Math.sin(t + p.p) * 40) ;
    const py = (p.y * h + Math.cos(t*1.2 + p.p) * 40) ;
    ctx.beginPath();
    ctx.arc(px, py, p.s*2*devicePixelRatio, 0, Math.PI*2);
    ctx.fillStyle = "rgba(57,192,255,0.6)";
    ctx.fill();
  }

  requestAnimationFrame(draw);
}
draw();

// auto-connect on load
connect();