"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const S = {
  mode: "shoot",
  state: null,
  handledJob: 0,
  handledLab: 0,
  zebra: false,
  looks: null,
  dr: { files: [], folder: "all", selected: new Set(), anchor: null, open: null, look: "original" },
  mem: { otd: [], loaded: false },
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
  $("#viewer").hidden = !["shoot", "night", "stack", "timelapse"].includes(m);
  $("#gallery").hidden = m !== "card";
  $("#darkroom").hidden = m !== "darkroom";
  $("#memories").hidden = m !== "memories";
  $("#live").hidden = m !== "live";
  if (m === "card") loadCard();
  if (m === "live") scanDevices();
  if (m === "darkroom") loadLibrary();
  if (m === "memories") loadMemories();
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
  renderLab(st.lab, prev?.lab);
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

/* ---------------- job bars ---------------- */
const KIND = { shoot: "Shoot", night: "Night", stack: "Long exposure", timelapse: "Timelapse", card: "Reading card",
  import: "Import", look: "Darkroom", hdr: "HDR fusion", panorama: "Panorama", "night merge": "Night merge",
  album: "Album", slideshow: "Slideshow" };
const labBusy = () => S.state?.lab.status === "running";

function renderBar(section, job) {
  const bar = $(`.jobbar[data-section="${section}"]`);
  const running = job.status === "running";
  bar.hidden = !running;
  if (!running) return;
  $(".job-kind", bar).textContent = KIND[job.kind] || job.kind;
  $(".job-msg", bar).textContent = job.message || "";
  $(".job-progress", bar).classList.toggle("indet", !job.total || job.kind === "shoot");
  $(".bar", bar).style.width = job.total ? (100 * job.done / job.total) + "%" : "0";
  $(".job-count", bar).textContent = job.total && job.kind !== "shoot" ? `${job.done} / ${job.total}` : elapsed(job);
  const c = $(".job-cancel", bar);
  if (c) c.hidden = !["stack", "timelapse", "import", "night"].includes(job.kind);
}

function renderJob(job, prevJob) {
  const running = job.status === "running";
  renderBar("job", job);
  $$("#btn-shoot, #btn-night, #btn-stack, #btn-tl, #btn-import, #btn-list, #btn-list-empty, #btn-sync").forEach(b => {
    b.disabled = running || (b.id === "btn-import" && !S.selected.size);
  });
  $("#viewer .frame").classList.toggle("busy", running);
  if (job.kind === "stack") renderPips("#stack-pips", job);
  if (job.kind === "night") renderPips("#night-pips", job, 1);
  if (running && job.preview && job.preview !== prevJob?.preview && ["stack", "timelapse", "night"].includes(job.kind)) {
    showImage(job.preview, job.kind === "stack" ? `Merging · ${job.done}/${job.total}`
      : job.kind === "night" ? `Frame ${job.done} of ${job.total - 1}` : `Frame ${job.done}/${job.total}`);
  }
  if (!running && job.id && job.id !== S.handledJob && job.status !== "idle") {
    S.handledJob = job.id;
    if (prevJob) finishJob(job); // ignore jobs that finished before this page loaded
  }
}
function renderLab(job, prevJob) {
  renderBar("lab", job);
  drButtons();
  $$("#btn-mem-album, #btn-mem-slides").forEach(b => b.disabled = labBusy());
  if (job.status !== "running" && job.id && job.id !== S.handledLab && job.status !== "idle") {
    S.handledLab = job.id;
    if (prevJob) finishLab(job);
  }
}
function elapsed(job) {
  return job.started ? Math.round(Date.now() / 1000 - job.started) + " s" : "";
}
setInterval(() => {
  for (const sec of ["job", "lab"]) {
    const j = S.state?.[sec];
    if (j?.status === "running" && (!j.total || j.kind === "shoot"))
      $(`.jobbar[data-section="${sec}"] .job-count`).textContent = elapsed(j);
  }
}, 1000);

function finishJob(job) {
  if (job.status === "error") return toast(esc(job.error), "error", 9000);
  const r = job.result || {};
  if (job.status === "cancelled") toast("Stopped.");
  switch (job.kind) {
    case "shoot":
      $("#viewer .frame").classList.add("flash");
      setTimeout(() => $("#viewer .frame").classList.remove("flash"), 400);
      addShot({ url: r.photo, path: r.path, kind: "photo", label: `${r.name} · ${r.width}×${r.height} · ${(r.size / 1048576).toFixed(1)} MB${r.kept_on_card ? " · on card" : " · Mac only"}` });
      break;
    case "night":
      if (r.photo) { addShot({ url: r.photo, path: r.path, kind: "night", label: r.label }); toast(esc(r.label), "ok"); }
      break;
    case "stack":
      if (r.photo) {
        addShot({ url: r.photo, path: r.path, kind: "stack", label: `${r.mode.toUpperCase()} · ${r.frames} frames · ${r.seconds} s` });
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
function finishLab(job) {
  if (job.status === "error") return toast(esc(job.error), "error", 9000);
  const r = job.result || {};
  if (r.path) {   // darkroom results
    if (S.mode === "darkroom") loadLibrary(r.path);
    else { S.dr.files = []; toast(`${esc(r.label)} saved. <a href="#" onclick="setMode('darkroom');loadLibrary('${esc(r.path)}');return false">Open in Darkroom</a>`, "ok", 9000); return; }
    toast(`${esc(r.label)} saved to captures/${esc(r.path)}`, "ok", 7000);
  } else if (r.album) {
    const a = $("#mem-album-link"); a.href = r.album; a.hidden = false;
    toast(`Album ready. <a href="${r.album}" target="_blank">Open it</a>`, "ok", 12000);
  } else if (r.video) {
    showMemVideo(r.video);
    toast(`${esc(r.label)} ready. <a href="${r.video}" target="_blank">Open MP4</a>`, "ok", 9000);
  }
}
$(".job-cancel").onclick = () => run("/api/cancel", {});

/* ---------------- info strip (histogram, clipping, zebra, sharpness) ---------------- */
function exifLine(x) {
  if (!x || (!x.shutter && !x.iso)) return "No camera EXIF";
  const parts = [x.shutter && `${x.shutter} s`, x.f_number && `f/${x.f_number}`, x.iso && `ISO ${x.iso}`,
    x.focal_length_35mm && `${x.focal_length_35mm} mm eq.${x.zoom_x && x.zoom_x > 1.05 ? ` (${x.zoom_x}×)` : ""}`,
    x.exposure_bias_ev ? `${x.exposure_bias_ev > 0 ? "+" : ""}${x.exposure_bias_ev} EV` : null,
    `flash ${x.flash_fired ? "fired" : "off"}`];
  return parts.filter(Boolean).join(" · ");
}
async function loadInfo(box, path, img, origUrl) {
  box._img = img; box._orig = origUrl; box._report = null;
  box.dataset.path = path || "";
  if (!path) { box.hidden = true; return; }
  box.hidden = false; box.className = "infostrip loading"; box.textContent = "Reading histogram…";
  const r = await api(`/api/report?path=${encodeURIComponent(path)}`).catch(() => null);
  if (box.dataset.path !== path) return;
  if (!r) { box.hidden = true; return; }
  const e = r.exposure, sh = r.sharpness;
  const lvl = (v, w, b) => v >= b ? "bad" : v >= w ? "warn" : "";
  box.className = "infostrip";
  box.innerHTML = `<img class="hist" src="${r.histogram}" alt="Histogram (R, G, B and brightness)">
    <div class="facts">
      <span class="fact ${lvl(e.highlights_clipped_pct, 0.5, 3)}" title="Pixels at pure white"><span class="k">Blown</span>${e.highlights_clipped_pct}%</span>
      <span class="fact ${lvl(e.shadows_clipped_pct, 5, 25)}" title="Pixels at pure black"><span class="k">Crushed</span>${e.shadows_clipped_pct}%</span>
      <span class="fact" title="Average brightness, 0–255"><span class="k">Mean</span>${e.mean_luma}</span>
      <span class="fact" title="Visible grain (lower is cleaner)"><span class="k">Noise</span>σ ${r.noise_sigma ?? "–"}</span>
      <span class="exif">${esc(exifLine(r.exif))}</span>
      <span class="verdict">Exposure ${esc(e.verdict)} · ${r.size_px[0]}×${r.size_px[1]}</span>
    </div>
    <div class="tools">
      <span class="badge-sharp ${sh.label}" title="Focus/shake check: detail score ${sh.subject} (sharp ≥ 150, blurry < 60)">${sh.label}</span>
      <button class="btn small zebra-btn" title="Zebra (Z): red stripes on blown highlights, blue on crushed shadows"><svg class="ic"><use href="#i-zebra"/></svg>Zebra</button>
    </div>`;
  box._report = r;
  $(".zebra-btn", box).onclick = () => { S.zebra = !S.zebra; applyZebra(box); };
  applyZebra(box);
}
function applyZebra(box) {
  if (!box._report || box.hidden) return;
  if (box.id === "dr-info" && (S.dr.look !== "original" || $("#dr-date").checked)) {
    if (S.zebra) toast("Zebra shows the original. Pick Original (and no date stamp) to see it.", "", 3000);
    return;
  }
  $(".zebra-btn", box)?.classList.toggle("on", S.zebra);
  const want = S.zebra ? box._report.zebra : box._orig;
  if (box._img && want && !box._img.src.endsWith(want)) box._img.src = want;
}
function visibleInfo() {
  return [$("#infostrip"), $("#dr-info")].find(b => !b.hidden && b.offsetParent);
}

/* ---------------- viewer ---------------- */
function addShot(shot) {
  S.shots.unshift(shot);
  showShot(shot);
  renderFilmstrip();
}
function showShot(shot) {
  S.shown = shot;
  if (shot.video) showVideo(shot.url, shot.label); else showImage(shot.url, shot.label, shot.path);
  renderFilmstrip();
}
function showImage(url, label, path) {
  const img = $("#view-img");
  const pre = new Image();
  pre.onload = () => { img.src = url; img.hidden = false; $("#view-video").hidden = true; $("#view-empty").hidden = true;
    if (path) loadInfo($("#infostrip"), path, img, url); };
  pre.src = url;
  if (!path) $("#infostrip").hidden = true, $("#infostrip").dataset.path = "";
  $("#osd-bl").textContent = label || "";
  const open = $("#osd-open");
  open.hidden = url.startsWith("/api/");
  open.href = url;
}
function showVideo(url, label) {
  const v = $("#view-video");
  v.src = url; v.hidden = false; v.play().catch(() => {});
  $("#view-img").hidden = true; $("#view-empty").hidden = true; $("#infostrip").hidden = true;
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
    b.innerHTML = s.video ? `<svg style="width:100%;height:60%;margin-top:10%;color:var(--accent)"><use href="#i-timelapse"/></svg>`
      : `<img src="${s.path ? `/api/thumb?path=${encodeURIComponent(s.path)}&size=200` : s.url}" loading="lazy" alt="">`;
    b.onclick = () => showShot(s);
    fs.append(b);
  });
}
function renderViewerEmpty() {
  const phone = matchMedia("(max-width: 700px)").matches;
  const txt = { shoot: phone ? "Tap the shutter below" : "Press <kbd>Space</kbd> to shoot", night: "Each frame shows here, then the merged night photo",
                stack: "The merged image builds up here, frame by frame",
                timelapse: "Each new frame shows here; the MP4 plays when done" }[S.mode];
  if (txt) $("#view-empty-text").innerHTML = txt;
  $("#osd-tl").textContent = { shoot: "AUTO", night: "NIGHT · MULTI-FRAME", stack: "LONG EXP · " + S.stackMode.toUpperCase(),
                               timelapse: "INTERVAL" }[S.mode] || "";
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
function renderPips(sel, job, extra = 0) {
  const p = $(sel);
  if (job.status !== "running") { p.innerHTML = ""; return; }
  p.innerHTML = Array.from({ length: job.total - extra }, (_, i) =>
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

/* ---------------- night ---------------- */
function nightEst() {
  const n = +$("#night-frames").value;
  $("#night-frames-out").textContent = n;
  $("#night-est").textContent = `≈ ${fmtDur(n * 5 + 20)} (about 5 s per frame, then the merge)`;
}
$("#night-frames").oninput = nightEst; nightEst();
$("#btn-night").onclick = () => run("/api/night", { frames: +$("#night-frames").value });

/* ---------------- darkroom ---------------- */
const enc = encodeURIComponent;
const thumbUrl = (p, size = 320) => `/api/thumb?path=${enc(p)}&size=${size}`;
function drCategory(f) {
  const top = f.path.split("/")[0];
  if (/^\d{8}-\d{6}-/.test(top) || top === "fake") return "Shots";
  if (top === "card-import") return "From card";
  if (top === "darkroom") return "Darkroom";
  return f.folder ? "Other" : "Other";
}
async function loadLibrary(openPath) {
  const r = await api("/api/library").catch(() => null);
  if (!r) return;
  S.dr.files = r.files;
  for (const p of [...S.dr.selected]) if (!r.files.some(f => f.path === p)) S.dr.selected.delete(p);
  if (openPath) return drOpen(openPath);
  renderDarkroom();
}
function drVisible() {
  return S.dr.files.filter(f => S.dr.folder === "all" || drCategory(f) === S.dr.folder);
}
function renderDarkroom() {
  const open = S.dr.open, has = S.dr.files.length > 0;
  $("#dr-empty").hidden = has || !!open;
  $("#dr-grid").hidden = !has || !!open;
  $("#dr-open").hidden = !open;
  $("#dr-back").hidden = !open;
  $("#dr-filter").hidden = !!open;
  const cats = ["Shots", "From card", "Darkroom", "Other"].filter(c => S.dr.files.some(f => drCategory(f) === c));
  if (S.dr.folder !== "all" && !cats.includes(S.dr.folder)) S.dr.folder = "all";
  $("#dr-filter").innerHTML = has ? [["all", "All", S.dr.files.length], ...cats.map(c => [c, c, S.dr.files.filter(f => drCategory(f) === c).length])]
    .map(([v, l, n]) => `<button data-v="${esc(v)}" class="${v === S.dr.folder ? "active" : ""}">${esc(l)} <span class="dim">${n}</span></button>`).join("") : "";
  $$("#dr-filter button").forEach(b => b.onclick = () => { S.dr.folder = b.dataset.v; renderDarkroom(); });
  const vis = drVisible();
  $("#dr-count").textContent = open ? open : has ? `${vis.length} photos on the Mac` : "";
  if (!open) {
    $("#dr-grid").innerHTML = vis.map((f, i) => `<div class="tile${S.dr.selected.has(f.path) ? " selected" : ""}" data-p="${esc(f.path)}" data-i="${i}" title="${esc(f.path)}">
      <img alt="" loading="lazy" src="${thumbUrl(f.path)}" onload="this.classList.add('loaded')">
      <div class="sel"><svg><use href="#i-check"/></svg></div>
      <div class="meta"><span>${esc(f.name)}</span><span class="f">${esc(f.folder.split("/").pop())}</span></div></div>`).join("");
    $("#dr-grid").classList.toggle("selecting", S.dr.selected.size > 0);
  }
  $("#dr-none").hidden = !!open || S.dr.selected.size > 0;
  $("#dr-single").hidden = !open;
  $("#dr-multi").hidden = !!open || S.dr.selected.size === 0;
  $("#dr-sel-count").textContent = S.dr.selected.size;
  drButtons();
}
function drRenderSel() {   // selection changes only touch classes, so thumbnails don't reload
  $$("#dr-grid .tile").forEach(t => t.classList.toggle("selected", S.dr.selected.has(t.dataset.p)));
  $("#dr-grid").classList.toggle("selecting", S.dr.selected.size > 0);
  $("#dr-none").hidden = !!S.dr.open || S.dr.selected.size > 0;
  $("#dr-multi").hidden = !!S.dr.open || S.dr.selected.size === 0;
  $("#dr-sel-count").textContent = S.dr.selected.size;
  drButtons();
}
function drButtons() {
  const busyLab = labBusy(), n = S.dr.selected.size;
  $("#btn-dr-save").disabled = busyLab || !S.dr.open || S.dr.look === "original" && !$("#dr-date").checked;
  $("#btn-dr-hdr").disabled = busyLab || n < 2 || n > 9;
  $("#btn-dr-pano").disabled = busyLab || n < 2 || n > 16;
  $("#btn-dr-night").disabled = busyLab || n < 2;
}
$("#dr-grid").addEventListener("click", e => {
  const t = e.target.closest(".tile");
  if (!t) return;
  const p = t.dataset.p, i = +t.dataset.i;
  if (e.target.closest(".sel") || e.metaKey || e.ctrlKey || e.shiftKey || S.dr.selected.size) {
    if (e.shiftKey && S.dr.anchor !== null) {
      const [a, b] = [Math.min(S.dr.anchor, i), Math.max(S.dr.anchor, i)];
      drVisible().slice(a, b + 1).forEach(f => S.dr.selected.add(f.path));
    } else {
      S.dr.selected.has(p) ? S.dr.selected.delete(p) : S.dr.selected.add(p);
      S.dr.anchor = i;
    }
    drRenderSel();
  } else drOpen(p);
});
async function drOpen(path) {
  S.dr.open = path; S.dr.look = "original"; S.dr.selected.clear();
  if (!S.dr.files.some(f => f.path === path)) await loadLibrary();
  renderDarkroom();
  if (!S.looks) S.looks = (await api("/api/looks")).looks;
  $("#dr-looks").innerHTML = S.looks.map(l => `<button data-look="${l.name}" class="${l.name === "original" ? "active" : ""}" title="${esc(l.blurb)}">
      <img alt="" src="/api/look?path=${enc(path)}&look=${l.name}&size=240"><span>${esc(l.title)}</span></button>`).join("");
  $$("#dr-looks button").forEach(b => b.onclick = () => drLook(b.dataset.look));
  drShow();
  loadInfo($("#dr-info"), path, $("#dr-img"), `/files/${path.split("/").map(enc).join("/")}`);
}
function drShow() {
  const p = S.dr.open, date = $("#dr-date").checked;
  const plain = S.dr.look === "original" && !date;
  const url = plain ? `/files/${p.split("/").map(enc).join("/")}` : `/api/look?path=${enc(p)}&look=${S.dr.look}&size=1600&date=${date ? 1 : 0}`;
  const img = $("#dr-img");
  $("#dr-spin").hidden = false;
  const pre = new Image();
  pre.onload = () => {
    if (S.dr.open !== p) return;
    const box = $("#dr-info");
    img.src = plain && S.zebra && box._report ? box._report.zebra : url;   // zebra describes the original only
    $("#dr-spin").hidden = true;
  };
  pre.onerror = () => { $("#dr-spin").hidden = true; toast("Couldn't render that look.", "error"); };
  pre.src = url;
  const look = S.looks?.find(l => l.name === S.dr.look);
  $("#dr-osd").textContent = `${p.split("/").pop()}${plain ? "" : ` · ${look?.title}${date ? " · date stamp" : ""} (preview)`}`;
  $("#dr-full").href = url.replace("size=1600", "size=2400");
  // zebra and the histogram describe the original; leave zebra off while previewing a look
  const box = $("#dr-info");
  box._orig = url;
  $(".zebra-btn", box)?.classList.toggle("on", S.zebra && plain);
  drButtons();
}
function drLook(name) {
  S.dr.look = name;
  $$("#dr-looks button").forEach(b => b.classList.toggle("active", b.dataset.look === name));
  drShow();
}
$("#dr-date").onchange = drShow;
$("#dr-back").onclick = () => { S.dr.open = null; $("#dr-info").hidden = true; renderDarkroom(); };
$("#btn-dr-save").onclick = () => run("/api/lab/look", { path: S.dr.open, look: S.dr.look, datestamp: $("#dr-date").checked });
const drSel = () => drVisible().filter(f => S.dr.selected.has(f.path)).map(f => f.path).reverse(); // oldest first
$("#btn-dr-hdr").onclick = () => run("/api/lab/fusion", { paths: drSel() });
$("#btn-dr-pano").onclick = () => run("/api/lab/panorama", { paths: drSel() });
$("#btn-dr-night").onclick = () => run("/api/lab/night", { paths: drSel() });
$("#btn-dr-clear").onclick = () => { S.dr.selected.clear(); drRenderSel(); };

/* ---------------- memories ---------------- */
async function loadMemories() {
  const r = await api("/api/memories/folders").catch(() => null);
  if (!r) return;
  const sel = $("#mem-folder"), prev = sel.value;
  sel.innerHTML = r.folders.map(f => `<option value="${esc(f.path)}">${esc(f.path)} (${f.count})</option>`).join("");
  if (prev && r.folders.some(f => f.path === prev)) sel.value = prev;
  const none = !r.folders.length;
  $$("#btn-mem-otd, #btn-mem-album, #btn-mem-slides").forEach(b => b.disabled = none || (b.id !== "btn-mem-otd" && labBusy()));
  if (!$("#mem-date").value) $("#mem-date").value = new Date().toLocaleDateString("en-CA");
  if (none) {
    $("#mem-grid").innerHTML = ""; $("#mem-empty").hidden = false;
    $("#mem-empty-text").textContent = "No photos on the Mac yet. Import some from the Card tab first.";
    $("#mem-sub").textContent = "";
    return;
  }
  if (!S.mem.loaded) { S.mem.loaded = true; loadOTD(); }
}
async function loadOTD() {
  const folder = $("#mem-folder").value, date = $("#mem-date").value, win = $("#mem-window").value;
  if (!folder) return;
  const r = await run(`/api/memories/onthisday?folder=${enc(folder)}&date=${enc(date)}&window=${win}`);
  if (!r) return;
  S.mem.otd = r.items;
  const d = new Date(r.date + "T12:00");
  $("#mem-title").textContent = `On this day · ${d.toLocaleDateString(undefined, { day: "numeric", month: "long" })}`;
  $("#mem-sub").textContent = r.items.length
    ? `${r.items.length} from other years${+win ? ` (± ${win} days)` : ""} in ${folder}` : "";
  $("#mem-empty").hidden = r.items.length > 0;
  $("#mem-empty-text").textContent = "Nothing from this day in other years.";
  $("#mem-grid").innerHTML = r.items.map(it => `<div class="tile" data-p="${esc(it.path)}" data-kind="${it.kind}" title="${esc(it.path)} · ${it.when.replace("T", " ")}${it.date_source === "mtime" ? " (file date)" : ""}">
      ${it.kind === "photo" ? `<img alt="" loading="lazy" src="${thumbUrl(it.path)}" onload="this.classList.add('loaded')">` : `<div class="empty" style="margin:auto">▶ video</div>`}
      <span class="years">${it.years_ago} yr${it.years_ago === 1 ? "" : "s"} ago</span>
      <div class="meta"><span>${it.when.slice(0, 10)}</span><span class="f">${esc(it.name)}</span></div></div>`).join("");
}
$("#mem-grid").addEventListener("click", e => {
  const t = e.target.closest(".tile");
  if (!t) return;
  if (t.dataset.kind === "photo") { setMode("darkroom"); drOpen(t.dataset.p); }
  else window.open(`/files/${t.dataset.p.split("/").map(enc).join("/")}`, "_blank");
});
$("#btn-mem-otd").onclick = loadOTD;
$("#mem-folder").onchange = () => { $("#mem-album-link").hidden = true; loadOTD(); };
$("#mem-date").onchange = loadOTD;
$("#mem-window").onchange = loadOTD;
$("#btn-mem-album").onclick = () => run("/api/memories/album", { folder: $("#mem-folder").value, title: $("#mem-album-title").value });
$("#mem-secs").oninput = () => $("#mem-secs-out").textContent = $("#mem-secs").value;
$("#btn-mem-slides").onclick = () => {
  const only = $("#mem-otd-only").checked;
  const paths = only ? S.mem.otd.filter(i => i.kind === "photo").map(i => i.path) : null;
  if (only && !paths.length) return toast("No 'On this day' photos to use. Untick the option to use the whole folder.");
  run("/api/memories/slideshow", { folder: $("#mem-folder").value, seconds: +$("#mem-secs").value, paths });
};
function showMemVideo(url) {
  if (S.mode !== "memories") setMode("memories");
  const v = $("#mem-video");
  v.src = url; v.hidden = false; v.play().catch(() => {});
}

/* ---------------- phone remote ---------------- */
$("#btn-phone").onclick = async () => {
  const r = await run("/api/remote");
  if (!r) return;
  $("#phone").hidden = false;
  $("#phone-on").hidden = !(r.enabled && r.qr);
  $("#phone-off").hidden = !!(r.enabled && r.qr);
  if (r.qr) $("#phone-qr").innerHTML = r.qr;
};
$("#phone-close").onclick = () => $("#phone").hidden = true;
$("#phone").onclick = e => { if (e.target.id === "phone") $("#phone").hidden = true; };

/* ---------------- keyboard ---------------- */
document.addEventListener("keydown", e => {
  if (e.target.matches("input[type=number], input[type=text], input[type=date], select, textarea") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (!$("#phone").hidden) { if (e.key === "Escape") $("#phone").hidden = true; return; }
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
  const modes = ["shoot", "night", "stack", "timelapse", "card", "darkroom", "memories", "live"];
  if (/^[1-8]$/.test(e.key)) setMode(modes[+e.key - 1]);
  else if (e.key === " " && !["card", "darkroom", "memories"].includes(S.mode)) { if (S.mode !== "shoot") setMode("shoot"); shoot(); }
  else if ((e.key === "z" || e.key === "Z") && visibleInfo()) { S.zebra = !S.zebra; applyZebra(visibleInfo()); }
  else if (e.key === "Escape" && S.mode === "darkroom" && S.dr.selected.size) { S.dr.selected.clear(); drRenderSel(); }
  else if (e.key === "Escape" && S.mode === "darkroom" && S.dr.open) $("#dr-back").click();
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
  if (st) { S.state = st; S.handledJob = st.job.id; S.handledLab = st.lab.id; renderHud(st); renderJob(st.job); renderLab(st.lab); }
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
