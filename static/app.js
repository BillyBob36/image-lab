"use strict";

// ------------------------------------------------------------------ state
const S = {
  config: null,
  models: {},          // id -> model
  model: null,         // current model id
  tab: "generate",     // generate | edit
  sizeMode: "preset",  // preset selection vs custom (free models)
  size: "1024x1024",
  quality: "high",
  format: "png",
  fidelity: "low",
  moderation: "auto",
  sources: [],         // {file, url} for edit
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };

// global fetch: bounce to login on 401 page-side
async function api(url, opts) {
  const r = await fetch(url, opts);
  if (r.status === 401) {
    if (S.config?.auth?.enabled) { location.href = "/login.html"; throw new Error("auth"); }
  }
  return r;
}

// ------------------------------------------------------------------ boot
async function boot() {
  try {
    const meR = await fetch("/api/me");
    if (meR.ok) {
      const me = await meR.json();
      if (me.auth_enabled && me.authenticated) renderUser(me);
    } else if (meR.status === 401) { location.href = "/login.html"; return; }
  } catch (_) {}

  const cfg = await (await fetch("/api/config")).json();
  S.config = cfg;
  cfg.models.forEach((m) => (S.models[m.id] = m));
  S.model = cfg.defaultModel;

  renderModelToggle();
  bindTabs();
  bindStaticInputs();
  bindEdit();
  selectModel(S.model);
}

function renderUser(me) {
  $("userChip").classList.remove("hidden");
  $("userEmail").textContent = me.email;
  if (me.picture) $("userPic").src = me.picture; else $("userPic").style.display = "none";
  $("logoutBtn").onclick = async () => { await fetch("/auth/logout", { method: "POST" }); location.href = "/login.html"; };
}

// ------------------------------------------------------------------ model toggle
function renderModelToggle() {
  const wrap = $("modelToggle");
  wrap.innerHTML = "";
  Object.values(S.models).forEach((m) => {
    const b = el("button", "model-btn");
    b.dataset.model = m.id;
    b.innerHTML = `<span class="mt-name">${m.label}</span>
      <span class="mt-tag">${m.tagline}</span>
      <span class="mt-badge">${m.availability}</span>`;
    b.onclick = () => selectModel(m.id);
    wrap.appendChild(b);
  });
}

function selectModel(id) {
  S.model = id;
  document.querySelectorAll(".model-btn").forEach((b) => b.classList.toggle("active", b.dataset.model === id));
  const caps = S.models[id].caps;
  $("optModelName").textContent = "· " + S.models[id].label;
  $("modelMeta").textContent = S.models[id].label;

  // defaults per model
  if (!caps.qualities.includes(S.quality)) S.quality = caps.defaultQuality;
  if (!caps.formats.includes(S.format)) S.format = caps.formats[0];
  if (caps.sizeMode === "preset" && S.sizeMode === "custom") S.sizeMode = "preset";
  if (!caps.presets.includes(S.size) && caps.sizeMode === "preset") S.size = caps.presets[0];

  applyCaps();
}

// ------------------------------------------------------------------ capability-driven UI
function applyCaps() {
  const caps = S.models[S.model].caps;

  // ---- sizes
  const presets = $("sizePresets");
  presets.innerHTML = "";
  const dims = { "1024x1024": "carré", "1536x1024": "paysage", "1024x1536": "portrait" };
  caps.presets.forEach((p) => {
    const b = el("button", "", `${p}<small>${dims[p] || ""}</small>`);
    b.classList.toggle("active", S.sizeMode === "preset" && S.size === p);
    b.onclick = () => { S.sizeMode = "preset"; S.size = p; applyCaps(); };
    presets.appendChild(b);
  });
  const free = caps.sizeMode === "free";
  // "Personnalisé" chip for free models
  if (free) {
    const c = el("button", "", "Perso.<small>libre / 4K</small>");
    c.classList.toggle("active", S.sizeMode === "custom");
    c.onclick = () => { S.sizeMode = "custom"; syncCustomSize(); applyCaps(); };
    presets.appendChild(c);
  }
  $("customSize").style.display = (free && S.sizeMode === "custom") ? "flex" : "none";
  if (free) {
    const c = caps.freeSize;
    $("sizeConstraint").textContent = `Côtés multiples de ${c.edgeMultiple} px · côté long ≤ ${c.maxLongEdge} · ratio ≤ ${c.maxRatio}:1`;
    $("sizeHint").textContent = "presets ou résolution libre";
    $("fSize").querySelector(".lock")?.remove();
  } else {
    $("sizeConstraint").textContent = "";
    $("sizeHint").innerHTML = `<span class="lock">1.5 : tailles fixes</span>`;
  }

  // ---- quality
  buildSeg("qualitySeg", caps.qualities, S.quality, (v) => { S.quality = v; applyCaps(); },
    { low: "low", medium: "medium", high: "high" });

  // ---- transparency
  const canTrans = caps.nativeTransparency || (S.tab === "generate" && caps.transparencyFallback);
  toggleField("fTransparent", canTrans);
  if (canTrans) {
    $("transLabel").textContent = caps.nativeTransparency ? "alpha natif (PNG)" : "détourage approx. (PNG)";
    $("transHint").innerHTML = caps.nativeTransparency
      ? `<span style="color:var(--m15)">natif</span>`
      : `<span style="color:var(--m2)">post-traité</span>`;
  } else {
    $("transparent").checked = false;
    $("transHint").innerHTML = S.tab === "edit"
      ? `<span class="lock">édition : 1.5 seult</span>`
      : `<span class="lock">indispo</span>`;
  }
  const transparent = canTrans && $("transparent").checked;

  // ---- format (forced png when transparent)
  buildSeg("formatSeg", caps.formats, transparent ? "png" : S.format,
    (v) => { S.format = v; applyCaps(); }, { png: "PNG", jpeg: "JPEG" });
  toggleField("fFormat", !transparent);
  const effFormat = transparent ? "png" : S.format;

  // ---- compression (jpeg only)
  toggleField("fCompression", effFormat === "jpeg" && caps.jpegCompression);

  // ---- n
  $("nImages").max = caps.maxImages;

  // ---- fidelity (edit only)
  toggleField("fFidelity", S.tab === "edit" && caps.inputFidelity);
  buildSeg("fidelitySeg", ["low", "high"], S.fidelity, (v) => { S.fidelity = v; }, { low: "low", high: "high" });

  // ---- moderation
  buildSeg("moderationSeg", caps.moderation, S.moderation, (v) => { S.moderation = v; },
    { auto: "auto", low: "low" });

  $("runBtn").textContent = S.tab === "edit" ? "Éditer" : "Générer";
}

function buildSeg(id, values, current, onPick, labels) {
  const seg = $(id); seg.innerHTML = "";
  values.forEach((v) => {
    const b = el("button", "", (labels && labels[v]) || v);
    b.classList.toggle("active", v === current);
    b.onclick = () => onPick(v);
    seg.appendChild(b);
  });
}
function toggleField(id, enabled) { $(id).classList.toggle("disabled", !enabled); }

function syncCustomSize() {
  const [w, h] = (S.size.includes("x") ? S.size : "1024x1024").split("x");
  if (!$("sizeW").value) $("sizeW").value = w;
  if (!$("sizeH").value) $("sizeH").value = h;
}
function currentSize() {
  const caps = S.models[S.model].caps;
  if (caps.sizeMode === "free" && S.sizeMode === "custom") {
    const w = parseInt($("sizeW").value || "1024", 10);
    const h = parseInt($("sizeH").value || "1024", 10);
    return `${w}x${h}`;
  }
  return S.size;
}

// ------------------------------------------------------------------ tabs
function bindTabs() {
  document.querySelectorAll(".tab").forEach((t) => {
    t.onclick = () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      S.tab = t.dataset.tab;
      $("editSources").classList.toggle("hidden", S.tab !== "edit");
      $("prompt").placeholder = S.tab === "edit"
        ? "Décrivez la modification à appliquer…"
        : "Décrivez l'image à générer… (jusqu'à 32 000 caractères)";
      applyCaps();
    };
  });
}

function bindStaticInputs() {
  $("prompt").addEventListener("input", (e) => { $("promptCount").textContent = e.target.value.length; });
  $("compression").addEventListener("input", (e) => { $("compressionVal").textContent = e.target.value; });
  $("nImages").addEventListener("input", (e) => { $("nVal").textContent = e.target.value; });
  $("transparent").addEventListener("change", applyCaps);
  $("sizeW").addEventListener("change", () => { S.size = currentSize(); });
  $("sizeH").addEventListener("change", () => { S.size = currentSize(); });
  $("runBtn").addEventListener("click", run);
}

// ------------------------------------------------------------------ run
async function run() {
  const prompt = $("prompt").value.trim();
  if (!prompt) { showErr("Le prompt est requis."); return; }
  if (S.tab === "edit" && S.sources.length === 0) { showErr("Ajoutez au moins une image source."); return; }

  setBusy(true); showErr(""); showRevised("");
  try {
    const data = S.tab === "edit" ? await runEdit(prompt) : await runGenerate(prompt);
    renderResults(data);
    if (data.revised_prompt) showRevised(data.revised_prompt);
  } catch (e) {
    showErr(e.message || "Erreur inconnue.");
  } finally {
    setBusy(false);
  }
}

async function runGenerate(prompt) {
  const caps = S.models[S.model].caps;
  const transparent = ($("transparent").checked) && !$("fTransparent").classList.contains("disabled");
  const body = {
    model: S.model,
    prompt,
    size: currentSize(),
    quality: S.quality,
    n: parseInt($("nImages").value, 10),
    format: transparent ? "png" : S.format,
    transparent,
    moderation: S.moderation,
  };
  if (!transparent && S.format === "jpeg") body.compression = parseInt($("compression").value, 10);
  const r = await api("/api/generate", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Erreur ${r.status}`);
  return r.json();
}

async function runEdit(prompt) {
  const caps = S.models[S.model].caps;
  const transparent = ($("transparent").checked) && !$("fTransparent").classList.contains("disabled") && caps.nativeTransparency;
  const fd = new FormData();
  fd.append("model", S.model);
  fd.append("prompt", prompt);
  fd.append("size", currentSize());
  fd.append("quality", S.quality);
  fd.append("n", $("nImages").value);
  fd.append("format", transparent ? "png" : S.format);
  fd.append("transparent", transparent ? "true" : "false");
  fd.append("moderation", S.moderation);
  if (S.tab === "edit" && caps.inputFidelity) fd.append("input_fidelity", S.fidelity);
  S.sources.forEach((s) => fd.append("images", s.file, s.file.name));

  // mask from editor (first image)
  if ($("maskEnabled")?.checked && !$("maskEditor").classList.contains("hidden")) {
    const blob = await exportMask();
    if (blob) fd.append("mask", blob, "mask.png");
  }
  const r = await api("/api/edit", { method: "POST", body: fd });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Erreur ${r.status}`);
  return r.json();
}

// ------------------------------------------------------------------ results
function renderResults(data) {
  const grid = $("results");
  grid.innerHTML = "";
  $("emptyState").classList.add("hidden");
  data.images.forEach((src, i) => {
    const card = el("div", "card");
    const wrap = el("div", "imgwrap");
    const img = el("img"); img.src = src; img.alt = `résultat ${i + 1}`;
    wrap.appendChild(img);
    const actions = el("div", "actions");
    const dl = el("button", "", "⬇ Télécharger");
    dl.onclick = () => downloadDataUrl(src, `imagelab-${data.model}-${Date.now()}-${i + 1}.${data.format}`);
    const toEdit = el("button", "", "✎ Éditer");
    toEdit.onclick = () => sendToEdit(src);
    actions.append(dl, toEdit);
    card.append(wrap, actions);
    grid.appendChild(card);
  });
}
function downloadDataUrl(url, name) { const a = el("a"); a.href = url; a.download = name; a.click(); }

async function sendToEdit(dataUrl) {
  const blob = await (await fetch(dataUrl)).blob();
  const file = new File([blob], `source-${Date.now()}.png`, { type: blob.type || "image/png" });
  addSources([file]);
  document.querySelector('.tab[data-tab="edit"]').click();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ------------------------------------------------------------------ edit sources + mask editor
function bindEdit() {
  const drop = $("drop"), input = $("fileInput");
  drop.onclick = () => input.click();
  input.onchange = () => { addSources([...input.files]); input.value = ""; };
  ["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("hover"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("hover"); }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer?.files) addSources([...e.dataTransfer.files]); });
  $("brushSize").addEventListener("input", (e) => (MASK.brush = +e.target.value));
  $("maskClear").onclick = () => { if (MASK.pctx) MASK.pctx.clearRect(0, 0, MASK.paint.width, MASK.paint.height); };
}

function addSources(files) {
  files.filter((f) => f.type.startsWith("image/")).forEach((f) => {
    S.sources.push({ file: f, url: URL.createObjectURL(f) });
  });
  renderThumbs();
  if (S.sources.length) setupMask(S.sources[0].url);
}
function renderThumbs() {
  const t = $("thumbs"); t.innerHTML = "";
  S.sources.forEach((s, i) => {
    const d = el("div", "t");
    const img = el("img"); img.src = s.url;
    const x = el("button", "", "×");
    x.onclick = () => { S.sources.splice(i, 1); renderThumbs(); if (S.sources[0]) setupMask(S.sources[0].url); else $("maskEditor").classList.add("hidden"); };
    d.append(img, x); t.appendChild(d);
  });
}

// ---- mask editor
const MASK = { base: null, paint: null, bctx: null, pctx: null, brush: 40, drawing: false };
function setupMask(url) {
  const img = new Image();
  img.onload = () => {
    MASK.base = $("maskBase"); MASK.paint = $("maskPaint");
    MASK.base.width = img.naturalWidth; MASK.base.height = img.naturalHeight;
    MASK.paint.width = img.naturalWidth; MASK.paint.height = img.naturalHeight;
    MASK.bctx = MASK.base.getContext("2d");
    MASK.pctx = MASK.paint.getContext("2d");
    MASK.bctx.drawImage(img, 0, 0);
    MASK.pctx.clearRect(0, 0, MASK.paint.width, MASK.paint.height);
    // display size caps at container width
    const stage = $("maskStage");
    stage.style.width = Math.min(img.naturalWidth, 520) + "px";
    $("maskEditor").classList.remove("hidden");
    bindPaint();
  };
  img.src = url;
}
function bindPaint() {
  const c = MASK.paint;
  const pos = (e) => {
    const r = c.getBoundingClientRect();
    const p = e.touches ? e.touches[0] : e;
    return { x: (p.clientX - r.left) * (c.width / r.width), y: (p.clientY - r.top) * (c.height / r.height) };
  };
  const stroke = (e) => {
    if (!MASK.drawing) return;
    const { x, y } = pos(e);
    const r = c.getBoundingClientRect();
    const scale = c.width / r.width;              // natural px per displayed px
    const radius = (MASK.brush * scale) / 2;      // brush is in displayed px
    MASK.pctx.fillStyle = "rgba(109,140,255,0.55)";
    MASK.pctx.beginPath();
    MASK.pctx.arc(x, y, radius, 0, Math.PI * 2);
    MASK.pctx.fill();
    e.preventDefault();
  };
  const start = (e) => { MASK.drawing = true; stroke(e); };
  const end = () => (MASK.drawing = false);
  c.onmousedown = start; c.onmousemove = stroke; window.addEventListener("mouseup", end);
  c.ontouchstart = start; c.ontouchmove = stroke; c.ontouchend = end;
}
async function exportMask() {
  if (!MASK.paint || !MASK.pctx) return null;
  const w = MASK.paint.width, h = MASK.paint.height;
  const src = MASK.pctx.getImageData(0, 0, w, h).data;
  // check something was painted
  let painted = false;
  for (let i = 3; i < src.length; i += 4) { if (src[i] > 0) { painted = true; break; } }
  if (!painted) return null;
  // build mask: painted area -> transparent (alpha 0 = edit); rest opaque
  const out = document.createElement("canvas"); out.width = w; out.height = h;
  const octx = out.getContext("2d");
  const id = octx.createImageData(w, h);
  for (let i = 0; i < src.length; i += 4) {
    const paintedPx = src[i + 3] > 10;
    id.data[i] = 0; id.data[i + 1] = 0; id.data[i + 2] = 0;
    id.data[i + 3] = paintedPx ? 0 : 255;
  }
  octx.putImageData(id, 0, 0);
  return await new Promise((res) => out.toBlob(res, "image/png"));
}

// ------------------------------------------------------------------ ui helpers
function setBusy(b) {
  $("runBtn").disabled = b;
  $("status").textContent = b ? "Génération en cours… (10–30 s)" : "";
}
function showErr(m) { $("errBox").innerHTML = m ? `<div class="err">${m}</div>` : ""; }
function showRevised(m) { $("revisedBox").innerHTML = m ? `<div class="revised"><b>Prompt révisé :</b> ${m}</div>` : ""; }

boot();
