"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const S = {
  mode: "shoot",
  state: null,
  handledJob: 0,
  shots: [],            // this session's results: {url, kind, label, video?}
  shown: null,
  files: [],
  cardStamp: -1,
  folder: "all",
  selected: new Set(),
  anchor: null,
  focusIdx: -1,
  lbIdx: -1,
  stackMode: "lighten",
};

/* ---------------- api ---------------- */
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(j.error || r.statusText); e.busy = j.busy; throw e; }
  return j;
}
async function run(path, body) {
  try { return await api(path, body); }
  catch (e) { toast(e.message, e.busy ? "" : "error"); return null; }
}

function toast(msg, kind = "", ms = 5000) {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.innerHTML = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), ms);
}
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const busy = () => S.state?.job.status === "running";
const fmtMB = mb => mb >= 1024 ? (mb / 1024).toFixed(1) + " GB" : mb + " MB";
const fmtDur = s => s < 90 ? Math.round(s) + " s" : s < 5400 ? Math.round(s / 60) + " min" : (s / 3600).toFixed(1) + " h";

/* ---------------- modes ---------------- */
function setMode(m) {
  S.mode = m;
  $$(".dial button").forEach(b => b.classList.toggle("active", b.dataset.mode === m));
  $$(".pane").forEach(p => p.hidden = p.dataset.pane !== m);
  $("#viewer").hidden = !["shoot", "stack", "timelapse"].includes(m);
  $("#gallery").hidden = m !== "card";
  $("#live").hidden = m !== "live";
  if (m === "card") loadCard();
  if (m === "live") scanDevices();
  try { localStorage.setItem("l120-mode", m); } catch {}
  renderViewerEmpty();
}
$$(".dial button").forEach(b => b.onclick = () => setMode(b.dataset.mode));

/* ---------------- live state (SSE) ---------------- */
function connectEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = ev => onState(JSON.parse(ev.data));
  es.onerror = () => { $("#hud-link .label").textContent = "Studio offline"; };
}

function onState(st) {
  const prev = S.state;
  S.state = st;
  renderHud(st);
  renderJob(st.job, prev?.job);
  if (st.card.stamp !== S.cardStamp && (S.mode === "card" || S.files.length)) loadCard();
}

function renderHud(st) {
  const link = $("#hud-link");
  link.classList.toggle("on", st.connected && !busy());
  link.classList.toggle("busy", busy());
  $(".label", link).textContent = busy() ? "Working" : st.connected ? "USB linked" : "Ready";
  link.title = st.connected ? "The Studio is using the camera; it lets go a few seconds after each operation."
                            : "Not holding the camera. It connects on demand, so other apps can use it in between.";
  const s = st.status;
  if (!s) return;
  const b = $("#hud-battery");
  // PTP battery level on this camera is coarse (often 0/50/100 style)
  $(".batt-fill", b).style.width = Math.max(4, Math.min(100, s.battery)) + "%";
  $(".num", b).textContent = s.battery + "%";
  b.classList.toggle("low", s.battery <= 20);
  const c = $("#hud-card");
  if (s.storage.card) {
    $(".shots", c).textContent = `[ ${String(s.storage.free_shots).padStart(4, " ")} ]`;
    $(".free", c).textContent = `${fmtMB(s.storage.free_mb)} free`;
    c.title = `Card: ${fmtMB(s.storage.free_mb)} free of ${fmtMB(s.storage.capacity_mb)}, ~${s.storage.free_shots} shots left`;
  } else {
    $(".shots", c).textContent = "NO CARD";
    $(".free", c).textContent = "";
  }
  const clock = $("#hud-clock");
  if (s.clock) {
    const cam = new Date(s.clock), readAt = new Date(s.read_at);
    const drift = Math.abs(cam - readAt) / 1000;
    $(".clock", clock).textContent = s.clock.replace("T", " ").slice(0, 16).replace(/-/g, ".");
    clock.classList.toggle("warn", drift > 120);
    clock.title = drift > 120 ? `Camera clock is off by ${fmtDur(drift)}. Photos get wrong dates. Click "Sync to Mac".`
                              : "Camera clock matches this Mac";
  } else {
    $(".clock", clock).textContent = s.clock_raw || "--";
  }
  $("#hud-fw").textContent = `${s.model.replace("COOLPIX ", "")} · FW ${s.firmware}`;
}

async function refreshStatus(quiet) {
  const r = quiet ? await api("/api/status", {}).catch(() => null) : await run("/api/status", {});
  return r;
}
$("#btn-refresh").onclick = () => refreshStatus();
$("#btn-sync").onclick = async () => {
  const r = await run("/api/clock/sync", {});
  if (r) toast("Camera clock set to this Mac's time.", "ok");
};

/* ---------------- job bar ---------------- */
const KIND = { shoot: "Shoot", stack: "Long exposure", timelapse: "Timelapse", card: "Reading card", import: "Import" };

function renderJob(job, prevJob) {
  const bar = $("#jobbar");
  const running = job.status === "running";
  bar.hidden = !running;
  $$("#btn-shoot, #btn-stack, #btn-tl, #btn-import, #btn-list, #btn-list-empty, #btn-sync").forEach(b => {
    b.disabled = running || (b.id === "btn-import" && !S.selected.size);
  });
  $("#viewer .frame").classList.toggle("busy", running);
  if (running) {
    $("#job-kind").textContent = KIND[job.kind] || job.kind;
    $("#job-msg").textContent = job.message || "";
    const indet = !job.total || (job.kind === "shoot");
    $(".job-progress").classList.toggle("indet", indet);
    $("#job-bar").style.width = job.total ? (100 * job.done / job.total) + "%" : "0";
    $("#job-count").textContent = job.total && job.kind !== "shoot" ? `${job.done} / ${job.total}` : elapsed(job);
    $("#btn-cancel").hidden = !["stack", "timelapse", "import"].includes(job.kind);
  }
  if (job.kind === "stack") renderPips(job);
  if (running && job.preview && job.preview !== prevJob?.preview && (job.kind === "stack" || job.kind === "timelapse")) {
    showImage(job.preview, job.kind === "stack" ? `Merging · ${job.done}/${job.total}` : `Frame ${job.done}/${job.total}`);
  }
  if (!running && job.id && job.id !== S.handledJob && job.status !== "idle") {
    S.handledJob = job.id;
    if (prevJob) finishJob(job); // ignore jobs that finished before this page loaded
  }
}
function elapsed(job) {
  return job.started ? Math.round(Date.now() / 1000 - job.started) + " s" : "";
}
setInterval(() => { if (busy() && S.state.job.kind === "shoot") $("#job-count").textContent = elapsed(S.state.job); }, 1000);

function finishJob(job) {
  if (job.status === "error") return toast(esc(job.error), "error", 9000);
  const r = job.result || {};
  if (job.status === "cancelled") toast("Stopped.");
  switch (job.kind) {
    case "shoot":
      $("#viewer .frame").classList.add("flash");
      setTimeout(() => $("#viewer .frame").classList.remove("flash"), 400);
      addShot({ url: r.photo, kind: "photo", label: `${r.name} · ${r.width}×${r.height} · ${(r.size / 1048576).toFixed(1)} MB${r.kept_on_card ? " · on card" : " · Mac only"}` });
      break;
    case "stack":
      if (r.photo) {
        addShot({ url: r.photo, kind: "stack", label: `${r.mode.toUpperCase()} · ${r.frames} frames · ${r.seconds} s` });
        toast(`Long exposure saved (${r.frames} frames).`, "ok");
      }
      break;
    case "timelapse":
      if (r.video) {
        addShot({ url: r.video, kind: "video", label: `Timelapse · ${r.frames} frames`, video: true });
        toast(`Timelapse ready: ${r.frames} frames. <a href="${r.video}" target="_blank">Open MP4</a>`, "ok", 9000);
      } else toast(`Timelapse stopped after ${r.frames} frame(s); too few for a video.`);
      break;
    case "import":
      toast(`Imported ${r.copied} file(s) to ${esc(r.folder)}` + (r.skipped ? `, ${r.skipped} already there` : "") +
            (r.failed?.length ? `. <b>${r.failed.length} failed</b>: ${esc(r.failed[0])}` : "."), r.failed?.length ? "error" : "ok", 9000);
      break;
  }
}
$("#btn-cancel").onclick = () => run("/api/cancel", {});

/* ---------------- viewer ---------------- */
function addShot(shot) {
  S.shots.unshift(shot);
  showShot(shot);
  renderFilmstrip();
}
function showShot(shot) {
  S.shown = shot;
  if (shot.video) showVideo(shot.url, shot.label); else showImage(shot.url, shot.label);
  renderFilmstrip();
}
function showImage(url, label) {
  const img = $("#view-img");
  const pre = new Image();
  pre.onload = () => { img.src = url; img.hidden = false; $("#view-video").hidden = true; $("#view-empty").hidden = true; };
  pre.src = url;
  $("#osd-bl").textContent = label || "";
  const open = $("#osd-open");
  open.hidden = url.startsWith("/api/");
  open.href = url;
}
function showVideo(url, label) {
  const v = $("#view-video");
  v.src = url; v.hidden = false; v.play().catch(() => {});
  $("#view-img").hidden = true; $("#view-empty").hidden = true;
  $("#osd-bl").textContent = label || "";
  $("#osd-open").hidden = false; $("#osd-open").href = url;
}
function renderFilmstrip() {
  const fs = $("#filmstrip");
  fs.innerHTML = "";
  S.shots.slice(0, 30).forEach(s => {
    const b = document.createElement("button");
    b.className = s === S.shown ? "active" : "";
    b.title = s.label;
    b.innerHTML = s.video ? `<svg style="width:100%;height:60%;margin-top:10%;color:var(--accent)"><use href="#i-timelapse"/></svg>` : `<img src="${s.url}" loading="lazy" alt="">`;
    b.onclick = () => showShot(s);
    fs.append(b);
  });
}
function renderViewerEmpty() {
  const txt = { shoot: "Press <kbd>Space</kbd> to shoot", stack: "The merged image builds up here, frame by frame",
                timelapse: "Each new frame shows here; the MP4 plays when done" }[S.mode];
  if (txt) $("#view-empty-text").innerHTML = txt;
  $("#osd-tl").textContent = { shoot: "AUTO", stack: "LONG EXP · " + S.stackMode.toUpperCase(), timelapse: "INTERVAL" }[S.mode] || "";
}

/* ---------------- shoot ---------------- */
async function shoot() {
  if (busy()) return toast("Busy: wait for the current job to finish.");
  const btn = $("#btn-shoot");
  btn.classList.add("pressed"); setTimeout(() => btn.classList.remove("pressed"), 150);
  await run("/api/shoot", { keep_on_card: $("#keep-on-card").checked });
}
$("#btn-shoot").onclick = shoot;
try { const k = localStorage.getItem("l120-keep"); if (k !== null) $("#keep-on-card").checked = k === "1"; } catch {}
$("#keep-on-card").onchange = e => { try { localStorage.setItem("l120-keep", e.target.checked ? "1" : "0"); } catch {} };

/* ---------------- stack ---------------- */
$$("#stack-modes button").forEach(b => b.onclick = () => {
  S.stackMode = b.dataset.v;
  $$("#stack-modes button").forEach(x => x.classList.toggle("active", x === b));
  renderViewerEmpty();
});
function stackEst() {
  const n = +$("#stack-frames").value;
  $("#stack-frames-out").textContent = n;
  $("#stack-est").textContent = `≈ ${fmtDur(n * 5 + 5)} total (about 5 s per frame)`;
}
$("#stack-frames").oninput = stackEst; stackEst();
$("#btn-stack").onclick = () => run("/api/stack", { frames: +$("#stack-frames").value, mode: S.stackMode });
function renderPips(job) {
  const p = $("#stack-pips");
  if (job.status !== "running") { p.innerHTML = ""; return; }
  p.innerHTML = Array.from({ length: job.total }, (_, i) =>
    `<i class="${i < job.done ? "done" : i === job.done ? "now" : ""}"></i>`).join("");
}

/* ---------------- timelapse ---------------- */
function tlEst() {
  const n = +$("#tl-count").value, iv = Math.max(5, +$("#tl-interval").value), fps = +$("#tl-fps").value || 24;
  $("#tl-est").textContent = `Shoots for ≈ ${fmtDur(n * iv)} → ${(n / fps).toFixed(1)} s of video`;
}
$$("#tl-count, #tl-interval, #tl-fps").forEach(i => i.oninput = tlEst); tlEst();
$("#btn-tl").onclick = () => {
  const iv = +$("#tl-interval").value;
  if (iv < 5) return toast("Interval must be at least 5 s (the camera's shot-to-shot time).", "error");
  run("/api/timelapse", { count: +$("#tl-count").value, interval: iv, fps: +$("#tl-fps").value });
};

/* ---------------- card gallery ---------------- */
async function loadCard() {
  const r = await api("/api/card").catch(() => null);
  if (!r) return;
  S.cardStamp = r.stamp;
  S.files = r.files;
  for (const h of [...S.selected]) if (!S.files.some(f => f.handle === h)) S.selected.delete(h);
  renderGallery();
}
function visibleFiles() {
  return S.files.filter(f => S.folder === "all" || (S.folder === "new" ? !f.on_mac : f.folder === S.folder));
}
function renderGallery() {
  const listing = S.state?.card.listing;
  const has = S.files.length > 0;
  $("#gallery-empty").hidden = has;
  $("#grid").hidden = !has;
  $("#gallery-empty p").textContent = listing ? "Reading the card…" : S.state?.card.count === 0 ? "The card is empty." : "The card hasn't been read yet.";
  $("#btn-list-empty").hidden = !!listing;
  const folders = [...new Set(S.files.map(f => f.folder))].sort();
  const opts = [["all", "All"], ...folders.map(f => [f, f]), ["new", "Not on Mac"]];
  if (!opts.some(o => o[0] === S.folder)) S.folder = "all";
  $("#folder-filter").innerHTML = has ? opts.map(([v, l]) =>
    `<button data-v="${esc(v)}" class="${v === S.folder ? "active" : ""}">${esc(l)} <span class="dim">${
      v === "all" ? S.files.length : v === "new" ? S.files.filter(f => !f.on_mac).length : S.files.filter(f => f.folder === v).length}</span></button>`).join("") : "";
  $$("#folder-filter button").forEach(b => b.onclick = () => { S.folder = b.dataset.v; renderGallery(); });
  const vis = visibleFiles();
  $("#gallery-count").textContent = has ? `${vis.length} files` : "";
  const grid = $("#grid");
  grid.innerHTML = "";
  const frag = document.createDocumentFragment();
  vis.forEach((f, i) => {
    const t = document.createElement("div");
    t.className = "tile" + (S.selected.has(f.handle) ? " selected" : "");
    t.dataset.h = f.handle;
    t.dataset.i = i;
    t.title = `${f.folder}/${f.name}\n${f.captured ? f.captured.replace("T", " ") : ""}\n${(f.size / 1048576).toFixed(1)} MB`;
    const cached = thumbCache.get(thumbKey(f));
    t.innerHTML = `<img alt="" ${cached ? `src="${cached}" class="loaded"` : `data-src="/api/card/thumb/${f.handle}" data-key="${esc(thumbKey(f))}"`}>
      <div class="badges">${f.type === "mov" ? '<span class="badge mov">MOV</span>' : ""}${f.on_mac ? '<span class="badge mac">✓ Mac</span>' : ""}</div>
      <div class="sel"><svg><use href="#i-check"/></svg></div>
      <div class="meta"><span>${esc(f.name)}</span><span class="f">${esc(f.folder.replace("NIKON", ""))}</span></div>`;
    frag.append(t);
  });
  grid.append(frag);
  grid.classList.toggle("selecting", S.selected.size > 0);
  $$("img[data-src]", grid).forEach(img => thumbObserver.observe(img));
  renderSel();
}
function renderSel() {
  $("#sel-count").textContent = S.selected.size;
  $("#btn-import").disabled = !S.selected.size || busy();
  $("#grid").classList.toggle("selecting", S.selected.size > 0);
  $$(".tile").forEach(t => t.classList.toggle("selected", S.selected.has(+t.dataset.h)));
}

// thumbnails: lazy, at most 2 in flight (they share one USB link), kept for re-renders
const thumbCache = new Map();
const thumbKey = f => `${f.handle}|${f.name}|${f.size}`;
const thumbQ = [];
let thumbsInFlight = 0;
const thumbObserver = new IntersectionObserver(entries => {
  entries.forEach(e => { if (e.isIntersecting) { thumbObserver.unobserve(e.target); thumbQ.push(e.target); } });
  pumpThumbs();
}, { root: $("#grid"), rootMargin: "300px" });
function pumpThumbs() {
  while (thumbsInFlight < 2 && thumbQ.length) {
    if (busy()) { setTimeout(pumpThumbs, 3000); return; }
    const img = thumbQ.shift();
    if (!img.isConnected) continue;
    thumbsInFlight++;
    fetch(img.dataset.src).then(r => {
      if (r.status === 409) { thumbQ.unshift(img); throw new Error("busy"); }
      if (!r.ok) throw new Error("thumb");
      return r.blob();
    }).then(b => {
      const url = URL.createObjectURL(b);
      thumbCache.set(img.dataset.key, url);
      img.onload = () => img.classList.add("loaded");
      img.src = url;
    }).catch(e => { if (e.message === "busy") setTimeout(pumpThumbs, 3000); })
      .finally(() => { thumbsInFlight--; pumpThumbs(); });
  }
}

$("#grid").addEventListener("click", e => {
  const t = e.target.closest(".tile");
  if (!t) return;
  const h = +t.dataset.h, i = +t.dataset.i;
  S.focusIdx = i;
  if (e.target.closest(".sel") || e.metaKey || e.ctrlKey || e.shiftKey || S.selected.size) {
    if (e.shiftKey && S.anchor !== null) {
      const vis = visibleFiles();
      const [a, b] = [Math.min(S.anchor, i), Math.max(S.anchor, i)];
      vis.slice(a, b + 1).forEach(f => S.selected.add(f.handle));
    } else {
      S.selected.has(h) ? S.selected.delete(h) : S.selected.add(h);
      S.anchor = i;
    }
    renderSel();
  } else openLightbox(i);
});
$("#btn-sel-all").onclick = () => { visibleFiles().forEach(f => S.selected.add(f.handle)); renderSel(); };
$("#btn-sel-new").onclick = () => { S.files.filter(f => !f.on_mac).forEach(f => S.selected.add(f.handle)); renderSel(); };
$("#btn-sel-none").onclick = () => { S.selected.clear(); renderSel(); };
$("#btn-import").onclick = async () => {
  const r = await run("/api/card/import", { handles: [...S.selected] });
  if (r) S.selected.clear(), renderSel();
};
const listCard = () => run("/api/card/list", {});
$("#btn-list").onclick = listCard;
$("#btn-list-empty").onclick = listCard;

/* lightbox */
function openLightbox(i) {
  const vis = visibleFiles();
  if (!vis[i]) return;
  S.lbIdx = i;
  const f = vis[i];
  $("#lightbox").hidden = false;
  $("#lb-name").textContent = `${f.folder}/${f.name}`;
  $("#lb-meta").textContent = ` · ${f.width}×${f.height} · ${(f.size / 1048576).toFixed(1)} MB · ${f.captured ? f.captured.replace("T", " ") : ""} · handle ${f.handle}`;
  $("#lb-download").href = `/api/card/file/${f.handle}?download=1`;
  const img = $("#lb-img"), vid = $("#lb-video");
  $("#lb-spin").hidden = false;
  img.removeAttribute("src"); vid.removeAttribute("src");
  img.hidden = f.type === "mov"; vid.hidden = f.type !== "mov";
  const thumb = $(`.tile[data-h="${f.handle}"] img`)?.src;
  if (f.type === "mov") {
    vid.src = `/api/card/file/${f.handle}`;
    vid.onloadeddata = () => $("#lb-spin").hidden = true;
    vid.onerror = () => { $("#lb-spin").hidden = true; toast("Couldn't load that video (camera busy?).", "error"); };
  } else {
    if (thumb) img.src = thumb;
    const full = new Image();
    full.onload = () => { if (S.lbIdx === i) { img.src = full.src; $("#lb-spin").hidden = true; } };
    full.onerror = () => { $("#lb-spin").hidden = true; if (S.lbIdx === i) toast("Couldn't download that file (camera busy?). Try again.", "error"); };
    full.src = `/api/card/file/${f.handle}`;
  }
  // it is now on the Mac
  setTimeout(() => { f.on_mac = true; }, 0);
}
function closeLightbox() { $("#lightbox").hidden = true; $("#lb-video").pause(); S.lbIdx = -1; renderGallery(); }
$("#lb-close").onclick = closeLightbox;

/* ---------------- live view ---------------- */
let liveStream = null;
async function scanDevices() {
  const sel = $("#live-device");
  const [srv, browser] = await Promise.all([
    api("/api/liveview/devices").catch(() => ({ devices: [] })),
    navigator.mediaDevices?.enumerateDevices().catch(() => []) ?? [],
  ]);
  const vids = browser.filter(d => d.kind === "videoinput");
  const prev = sel.value;
  sel.innerHTML = `<option value="">— choose —</option>` + srv.devices.filter(d => !/Capture screen/i.test(d.name)).map(d => {
    const match = vids.find(v => v.label && (v.label === d.name || v.label.startsWith(d.name) || d.name.startsWith(v.label)));
    return `<option value="${esc(match?.deviceId || "name:" + d.name)}">${esc(d.name)}${d.likely_capture ? "  ★ capture device" : ""}</option>`;
  }).join("");
  // devices the browser sees but ffmpeg didn't list (unlikely) — still offer them
  vids.filter(v => v.label && !srv.devices.some(d => d.name === v.label)).forEach(v => {
    sel.insertAdjacentHTML("beforeend", `<option value="${esc(v.deviceId)}">${esc(v.label)}</option>`);
  });
  const likely = srv.devices.filter(d => d.likely_capture);
  $("#live-detected").innerHTML = likely.length
    ? `Found a likely capture device: <b>${esc(likely[0].name)}</b>.`
    : `No capture dongle detected. Only the Mac's own cameras are connected (${srv.devices.length} video device${srv.devices.length === 1 ? "" : "s"}).`;
  if (prev) sel.value = prev;
  else {
    let saved = null; try { saved = localStorage.getItem("l120-live"); } catch {}
    const opt = [...sel.options].find(o => o.textContent.startsWith(saved || "\u0000")) || [...sel.options].find(o => o.textContent.includes("★"));
    if (opt) sel.value = opt.value;
  }
}
async function startLive() {
  const sel = $("#live-device");
  if (!sel.value) return toast("Choose a video source first.");
  const name = sel.selectedOptions[0].textContent.replace("  ★ capture device", "");
  try { localStorage.setItem("l120-live", name); } catch {}
  stopLive();
  try {
    let id = sel.value;
    if (id.startsWith("name:")) {
      // labels are hidden until permission is granted: ask once, then match by name
      const tmp = await navigator.mediaDevices.getUserMedia({ video: true });
      tmp.getTracks().forEach(t => t.stop());
      const v = (await navigator.mediaDevices.enumerateDevices()).find(d => d.kind === "videoinput" && d.label.startsWith(name));
      if (!v) throw new Error(`The browser can't see "${name}".`);
      id = v.deviceId;
    }
    liveStream = await navigator.mediaDevices.getUserMedia({
      video: { deviceId: { exact: id }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false,
    });
    $("#live-video").srcObject = liveStream;
    $("#live-empty").hidden = true; $("#live-osd").hidden = false;
    scanDevices();
  } catch (e) {
    toast(`Couldn't open the video source: ${esc(e.message || e.name)}`, "error");
  }
}
function stopLive() {
  liveStream?.getTracks().forEach(t => t.stop());
  liveStream = null;
  $("#live-video").srcObject = null;
  $("#live-empty").hidden = false; $("#live-osd").hidden = true;
}
$("#btn-live-start").onclick = startLive;
$("#btn-live-stop").onclick = stopLive;
$("#btn-live-scan").onclick = scanDevices;
$("#live-device").onchange = () => { if (liveStream) startLive(); };

/* ---------------- keyboard ---------------- */
document.addEventListener("keydown", e => {
  if (e.target.matches("input[type=number], select, textarea") || e.metaKey || e.ctrlKey || e.altKey) return;
  const lb = !$("#lightbox").hidden;
  if (lb) {
    const vis = visibleFiles();
    if (e.key === "Escape") closeLightbox();
    else if (e.key === "ArrowRight") openLightbox(Math.min(S.lbIdx + 1, vis.length - 1));
    else if (e.key === "ArrowLeft") openLightbox(Math.max(S.lbIdx - 1, 0));
    else if (e.key === " ") { const h = vis[S.lbIdx].handle; S.selected.has(h) ? S.selected.delete(h) : S.selected.add(h); renderSel(); toast(S.selected.has(h) ? "Selected" : "Unselected", "", 1200); }
    else return;
    return e.preventDefault();
  }
  const modes = ["shoot", "stack", "timelapse", "card", "live"];
  if (/^[1-5]$/.test(e.key)) setMode(modes[+e.key - 1]);
  else if (e.key === " " && S.mode !== "card") { if (S.mode !== "shoot") setMode("shoot"); shoot(); }
  else if (e.key === "Escape" && busy()) run("/api/cancel", {});
  else if (e.key === "Escape" && S.mode === "card") { S.selected.clear(); renderSel(); }
  else if ((e.key === "r" || e.key === "R") && !busy()) refreshStatus();
  else if ((e.key === "a" || e.key === "A") && S.mode === "card") { visibleFiles().forEach(f => S.selected.add(f.handle)); renderSel(); }
  else if (S.mode === "card" && ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) cardKeys(e);
  else return;
  e.preventDefault();
});
function cardKeys(e) {
  const tiles = $$(".tile");
  if (!tiles.length) return;
  const cols = Math.max(1, Math.round($("#grid").clientWidth / (tiles[0].offsetWidth + 10)));
  let i = S.focusIdx < 0 ? 0 : S.focusIdx;
  if (e.key === "Enter") return openLightbox(i);
  if (e.key === " ") { const h = +tiles[i].dataset.h; S.selected.has(h) ? S.selected.delete(h) : S.selected.add(h); return renderSel(); }
  i += { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols }[e.key];
  S.focusIdx = Math.max(0, Math.min(tiles.length - 1, i));
  tiles.forEach((t, j) => t.classList.toggle("focus", j === S.focusIdx));
  tiles[S.focusIdx].scrollIntoView({ block: "nearest" });
}

/* ---------------- boot ---------------- */
(async function boot() {
  let m = "shoot"; try { m = localStorage.getItem("l120-mode") || m; } catch {}
  setMode(m === "live" ? "shoot" : m);
  const st = await api("/api/state").catch(() => null);
  if (st) { S.state = st; S.handledJob = st.job.id; renderHud(st); renderJob(st.job); }
  connectEvents();
  firstStatus();
})();
// Read status once at start; if another program holds the camera, retry quietly.
async function firstStatus() {
  if (S.state?.status) return;
  if (!busy()) {
    try { await api("/api/status", {}); return; }
    catch (e) { $("#hud-link .label").textContent = e.busy ? "In use by another app" : "Camera not reachable"; $("#hud-link").title = e.message; }
  }
  setTimeout(firstStatus, 20000);
}
