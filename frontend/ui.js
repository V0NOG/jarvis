const $ = (id) => document.getElementById(id);

let es = null;
let reconnectTimer = null;
let lastPingAt = null;
let backoffMs = 1000;

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
  if(es){ es.close(); es = null; }
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
      let txt = (ev.data || "").trim();
      let wake = false;

      if (type === 'transcript') {
        let text = data;
        let wake = false;
        try {
          const obj = JSON.parse(data);
          text = obj.text ?? '';
          wake = !!obj.wake;
        } catch (_) {
          // old server format: plain string
        }

        // Always log a line (so you can see what was heard),
        // but only bubble it to the big HUD if wake=true
        addLog('transcript', text + (wake ? ' [wake]' : ''));
        if (wake) {
          showYou(text);
          ringFlash();
        }
        return;
      }

      if (type === 'debug') {
        addLog('debug', data);
        return;
      }

      // Backend now sends JSON: {"text":"...","wake":true|false}
      try {
          const obj = JSON.parse(txt);
          txt = String(obj.text ?? "");
          wake = !!obj.wake;
      } catch (_) {
          // old/plain format; keep txt as-is and infer wake via regex
          wake = patterns.some(p => p.test(txt));
      }

      // Always log what was heard
      log("info", "transcript", txt + (wake ? " [wake]" : ""));

      // Only promote to the HUD if wake was actually detected
      if (wake) {
          transcriptEl.textContent = txt;
          flashRing();
      }
    });

    es.addEventListener("debug", (ev)=>{
        log("debug", "debug", ev.data || "");
    });

  es.addEventListener("reply", (ev)=>{
    const txt = (ev.data || "").trim();
    typeReply(txt);
    log("info","reply", txt);
  });

  es.addEventListener("action", (ev)=>{
    log("action","action", ev.data || "");
  });

  es.addEventListener("error", (ev)=>{
    log("err","error", ev.data || "unknown error");
    statusBadge.className = "badge err";
    statusBadge.textContent = "ERROR";
  });

  es.addEventListener("ping", (ev)=>{
    lastPingAt = performance.now();
    // The server sends human time in data, we compute latency as “time since event”
    setTimeout(()=>{
      const ms = Math.floor(performance.now() - lastPingAt);
      latencyEl.textContent = `${ms} ms`;
    }, 0);
    // keep alive log light
  });

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
      i += Math.max(1, Math.floor(text.length/120)); // faster for long lines
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

// Animated background grid/particles (subtle)
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

  // grid
  ctx.strokeStyle = "rgba(23,48,85,0.35)";
  ctx.lineWidth = 1;
  const gx = 60 * devicePixelRatio, gy = 60 * devicePixelRatio;
  for(let x=0;x<w; x+=gx){
    ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,h); ctx.stroke();
  }
  for(let y=0;y<h; y+=gy){
    ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(w,y); ctx.stroke();
  }

  // particles
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