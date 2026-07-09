"use strict";

// ------------------------------------------------------------------ state
const S = {
  config: null,
  models: {},          // id -> model
  model: null,         // current model id
  tab: "generate",     // generate | edit
  sizeStrategy: "combo", // combo (ratio×definition) | custom | match
  ratio: "1:1",
  def: "1k",           // definition tier (long-edge target)
  quality: "high",
  format: "png",
  fidelity: "low",
  moderation: "auto",
  sources: [],         // {file, url} for edit
  inputDims: null,     // {w,h} of first source image
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };

// ------------------------------------------------------------------ i18n
const I18N = {
  fr: {
    tab_generate: "Génération", tab_edit: "Édition · Inpainting", options: "Options",
    f_ratio: "Ratio", f_definition: "Définition", match_input: "Garder la taille de l'image source (au plus proche)",
    ph_width: "larg.", ph_height: "haut.", f_quality: "Qualité", f_transparent: "Fond transparent",
    f_format: "Format de sortie", f_compression: "Compression JPEG", hint_compression: "0 = max qualité",
    f_n: "Nombre d'images", f_fidelity: "Fidélité d'entrée", hint_fidelity: "préserve visages/détails",
    f_moderation: "Modération", edit_sources: "Images source", hint_edit_sources: "glisser-déposer · multi-images OK",
    drop_zone: "Cliquez ou déposez une ou plusieurs images ici",
    mask_label: "Masque d'inpainting — peignez la zone à modifier (le reste est préservé)",
    brush: "Pinceau", clear: "Effacer", use_mask: "Utiliser le masque", logout: "Déconnexion",
    empty_state: "Les images générées apparaîtront ici.", run_generate: "Générer", run_edit: "Éditer",
    ph_prompt_generate: "Décrivez l'image à générer… (jusqu'à 32 000 caractères)",
    ph_prompt_edit: "Décrivez la modification à appliquer…",
    status_generating: "Génération en cours… (10–30 s)", err_prompt_required: "Le prompt est requis.",
    err_need_source: "Ajoutez au moins une image source.", err_unknown: "Erreur inconnue.", err_generic: "Erreur",
    revised_label: "Prompt révisé :", dl: "⬇ Télécharger", edit_btn: "✎ Éditer",
    trans_native: "alpha natif (PNG)", trans_cutout: "détourage approx. (PNG)", hint_native: "natif",
    hint_posttraite: "post-traité", hint_edit_15only: "édition : 1.5 seult", hint_unavail: "indispo",
    lock_15_fixed: "1.5 : ratios fixes", tip_20_only: "Disponible seulement sur GPT-image 2.0",
    lock_15_native: "1.5 : tailles natives", tip_def_native: "Définition réglable sur GPT-image 2.0 uniquement",
    size_constraint: "Côtés ×{edge} · côté long ≤ {max}px · ratio ≤ {ratio}:1",
    suffix_source: " · d'après la source", suffix_custom: " · perso", perso: "Perso.", perso_sub: "W×H",
    rtag_square: "carré", rtag_landscape: "paysage", rtag_portrait: "portrait", rtag_photo: "photo",
    rtag_photo_v: "photo↕", rtag_wide: "large", rtag_story: "story", rtag_cine: "ciné",
    tagline_2: "Haute résolution & 4K, édition améliorée, ratios libres",
    tagline_15: "Réalisme, respect du prompt, transparence native (alpha)",
    avail_GA: "GA", avail_Preview: "Aperçu",
  },
  en: {
    tab_generate: "Generate", tab_edit: "Edit · Inpainting", options: "Options",
    f_ratio: "Ratio", f_definition: "Resolution", match_input: "Match the source image size (closest valid)",
    ph_width: "width", ph_height: "height", f_quality: "Quality", f_transparent: "Transparent background",
    f_format: "Output format", f_compression: "JPEG compression", hint_compression: "0 = max quality",
    f_n: "Number of images", f_fidelity: "Input fidelity", hint_fidelity: "preserves faces/details",
    f_moderation: "Moderation", edit_sources: "Source images", hint_edit_sources: "drag & drop · multi-image OK",
    drop_zone: "Click or drop one or more images here",
    mask_label: "Inpainting mask — paint the area to change (the rest is preserved)",
    brush: "Brush", clear: "Clear", use_mask: "Use mask", logout: "Sign out",
    empty_state: "Generated images will appear here.", run_generate: "Generate", run_edit: "Edit",
    ph_prompt_generate: "Describe the image to generate… (up to 32,000 characters)",
    ph_prompt_edit: "Describe the change to apply…",
    status_generating: "Generating… (10–30 s)", err_prompt_required: "A prompt is required.",
    err_need_source: "Add at least one source image.", err_unknown: "Unknown error.", err_generic: "Error",
    revised_label: "Revised prompt:", dl: "⬇ Download", edit_btn: "✎ Edit",
    trans_native: "native alpha (PNG)", trans_cutout: "approx. cutout (PNG)", hint_native: "native",
    hint_posttraite: "post-processed", hint_edit_15only: "editing: 1.5 only", hint_unavail: "unavailable",
    lock_15_fixed: "1.5: fixed ratios", tip_20_only: "Available only on GPT-image 2.0",
    lock_15_native: "1.5: native sizes", tip_def_native: "Resolution adjustable on GPT-image 2.0 only",
    size_constraint: "Edges ×{edge} · long side ≤ {max}px · ratio ≤ {ratio}:1",
    suffix_source: " · from source", suffix_custom: " · custom", perso: "Custom", perso_sub: "W×H",
    rtag_square: "square", rtag_landscape: "landscape", rtag_portrait: "portrait", rtag_photo: "photo",
    rtag_photo_v: "photo↕", rtag_wide: "wide", rtag_story: "story", rtag_cine: "cinema",
    tagline_2: "High-res & 4K, improved editing, free aspect ratios",
    tagline_15: "Realism, prompt-following, native transparency (alpha)",
    avail_GA: "GA", avail_Preview: "Preview",
  },
};
let LANG = localStorage.getItem("lang") || ((navigator.language || "fr").toLowerCase().startsWith("en") ? "en" : "fr");
function t(key, params) {
  let s = (I18N[LANG] && I18N[LANG][key]) ?? I18N.fr[key] ?? key;
  if (params) for (const k in params) s = s.replace(`{${k}}`, params[k]);
  return s;
}
function applyI18nStatic() {
  document.documentElement.lang = LANG;
  document.querySelectorAll("[data-i18n]").forEach((e) => { e.textContent = t(e.dataset.i18n); });
  document.querySelectorAll("[data-i18n-ph]").forEach((e) => { e.placeholder = t(e.dataset.i18nPh); });
}
function updatePromptPlaceholder() {
  $("prompt").placeholder = S.tab === "edit" ? t("ph_prompt_edit") : t("ph_prompt_generate");
}
function setLang(lang) {
  LANG = lang;
  localStorage.setItem("lang", lang);
  document.querySelectorAll("#langToggle button").forEach((b) => b.classList.toggle("active", b.dataset.lang === lang));
  applyI18nStatic();
  renderModelToggle();
  updatePromptPlaceholder();
  applyCaps();
}
function bindLang() {
  document.querySelectorAll("#langToggle button").forEach((b) => { b.onclick = () => setLang(b.dataset.lang); });
}

// Aspect ratios. `preset` = a native size the fixed-size models (1.5) support;
// ratios without it are only reachable on free-size models (2.0).
const RATIOS = [
  { id: "1:1",  w: 1,  h: 1,  preset: "1024x1024", tag: "rtag_square" },
  { id: "3:2",  w: 3,  h: 2,  preset: "1536x1024", tag: "rtag_landscape" },
  { id: "2:3",  w: 2,  h: 3,  preset: "1024x1536", tag: "rtag_portrait" },
  { id: "4:3",  w: 4,  h: 3,  tag: "rtag_photo" },
  { id: "3:4",  w: 3,  h: 4,  tag: "rtag_photo_v" },
  { id: "16:9", w: 16, h: 9,  tag: "rtag_wide" },
  { id: "9:16", w: 9,  h: 16, tag: "rtag_story" },
  { id: "21:9", w: 21, h: 9,  tag: "rtag_cine" },
];

// Definition tiers = target for the LONG edge (px). Only meaningful on free-size
// models (2.0); on 1.5 the size is locked to the native preset for the ratio.
const DEFINITIONS = [
  { id: "1k",  long: 1024, label: "1K",      sub: "1024 px" },
  { id: "fhd", long: 1920, label: "Full HD", sub: "1920 px" },
  { id: "2k",  long: 2560, label: "2K",      sub: "2560 px" },
  { id: "4k",  long: 3840, label: "4K",      sub: "3840 px" },
];
// Combine a ratio + a long-edge target into a valid free-size (clamped to limits).
function sizeFor(ratioId, defLong) {
  const r = RATIOS.find((x) => x.id === ratioId) || RATIOS[0];
  const ratio = r.w / r.h;
  let W, H;
  if (r.w >= r.h) { W = defLong; H = defLong / ratio; }
  else { H = defLong; W = defLong * ratio; }
  return clampFree(R16(W), R16(H));
}

const R16 = (x) => Math.round(x / 16) * 16;
// Pick a valid concrete size for a ratio on a free-size model (~1.6 MP target).
function bestSize(rw, rh) {
  const ratio = rw / rh, target = 1600000;
  let w = R16(Math.sqrt(target * ratio)), h = R16(Math.sqrt(target / ratio));
  return clampFree(w, h);
}
// Snap arbitrary WxH to the nearest valid free-size (×16, long≤3840, ratio≤3:1, 0.65–8.3 MP).
function snapFree(w, h) { return clampFree(R16(w), R16(h)); }
function clampFree(w, h) {
  let W = Math.max(512, w), H = Math.max(512, h);
  if (Math.max(W, H) / Math.min(W, H) > 3) { if (W > H) W = R16(H * 3); else H = R16(W * 3); }
  const sd = 3840 / Math.max(W, H); if (sd < 1) { W = R16(W * sd); H = R16(H * sd); }
  if (W * H > 8294400) { const s = Math.sqrt(8294400 / (W * H)); W = R16(W * s); H = R16(H * s); }
  if (W * H < 655360) { const s = Math.sqrt(655360 / (W * H)) * 1.03; W = R16(W * s); H = R16(H * s); }
  return `${Math.max(512, W)}x${Math.max(512, H)}`;
}
function nearestPreset(presets, w, h) {
  const ir = w / h;
  return presets.map((p) => { const [pw, ph] = p.split("x").map(Number); return { p, d: Math.abs(pw / ph - ir) }; })
    .sort((a, b) => a.d - b.d)[0].p;
}

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
  bindLang();
  selectModel(S.model);
  setLang(LANG);  // apply translations to static + dynamic UI
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
    const tagKey = m.id === "gpt-image-2" ? "tagline_2" : "tagline_15";
    b.innerHTML = `<span class="mt-name">${m.label}</span>
      <span class="mt-tag">${t(tagKey)}</span>
      <span class="mt-badge">${t("avail_" + m.availability)}</span>`;
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
  if (caps.sizeMode === "preset" && S.sizeStrategy === "custom") S.sizeStrategy = "combo";
  const rr = RATIOS.find((r) => r.id === S.ratio);
  if (caps.sizeMode === "preset" && rr && !rr.preset) S.ratio = "1:1";

  applyCaps();
}

// ------------------------------------------------------------------ capability-driven UI
function applyCaps() {
  const caps = S.models[S.model].caps;

  // ---- sizes / ratios
  renderSizeControls(caps);

  // ---- quality
  buildSeg("qualitySeg", caps.qualities, S.quality, (v) => { S.quality = v; applyCaps(); },
    { low: "low", medium: "medium", high: "high" });

  // ---- transparency
  const canTrans = caps.nativeTransparency || (S.tab === "generate" && caps.transparencyFallback);
  toggleField("fTransparent", canTrans);
  if (canTrans) {
    $("transLabel").textContent = caps.nativeTransparency ? t("trans_native") : t("trans_cutout");
    $("transHint").innerHTML = caps.nativeTransparency
      ? `<span style="color:var(--m15)">${t("hint_native")}</span>`
      : `<span style="color:var(--m2)">${t("hint_posttraite")}</span>`;
  } else {
    $("transparent").checked = false;
    $("transHint").innerHTML = S.tab === "edit"
      ? `<span class="lock">${t("hint_edit_15only")}</span>`
      : `<span class="lock">${t("hint_unavail")}</span>`;
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

  $("runBtn").textContent = S.tab === "edit" ? t("run_edit") : t("run_generate");
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

function renderSizeControls(caps) {
  const free = caps.sizeMode === "free";
  const matching = S.sizeStrategy === "match";
  const custom = S.sizeStrategy === "custom";

  // ---- RATIO selector
  const rseg = $("ratioSeg"); rseg.innerHTML = "";
  const ratioOK = (r) => free || !!(r && r.preset);
  if (!ratioOK(RATIOS.find((r) => r.id === S.ratio))) S.ratio = "1:1";
  RATIOS.forEach((r) => {
    const on = ratioOK(r);
    const b = el("button", on ? "" : "off", `${r.id}<small>${t(r.tag)}</small>`);
    if (!on) b.title = t("tip_20_only");
    if (!custom && !matching && S.ratio === r.id && on) b.classList.add("active");
    if (on) b.onclick = () => { S.ratio = r.id; if (custom) S.sizeStrategy = "combo"; applyCaps(); };
    rseg.appendChild(b);
  });
  rseg.style.opacity = (matching || custom) ? ".4" : "1";
  rseg.style.pointerEvents = (matching || custom) ? "none" : "auto";
  $("ratioHint").innerHTML = free ? "" : `<span class="lock">${t("lock_15_fixed")}</span>`;

  // ---- DEFINITION selector (long-edge target) — free models only
  const dseg = $("defSeg"); dseg.innerHTML = "";
  DEFINITIONS.forEach((d) => {
    const b = el("button", free ? "" : "off", `${d.label}<small>${d.sub}</small>`);
    if (!free) b.title = t("tip_def_native");
    if (free && !custom && !matching && S.def === d.id) b.classList.add("active");
    if (free) b.onclick = () => { S.def = d.id; if (matching || custom) S.sizeStrategy = "combo"; applyCaps(); };
    dseg.appendChild(b);
  });
  if (free) {
    const c = el("button", "", `${t("perso")}<small>${t("perso_sub")}</small>`);
    if (custom) c.classList.add("active");
    c.onclick = () => { S.sizeStrategy = "custom"; syncCustomSize(); applyCaps(); };
    dseg.appendChild(c);
  }
  dseg.style.opacity = matching ? ".4" : "1";
  dseg.style.pointerEvents = matching ? "none" : "auto";
  $("defHint").innerHTML = free ? "" : `<span class="lock">${t("lock_15_native")}</span>`;

  // ---- custom W×H inputs (free + custom strategy)
  $("customSize").classList.toggle("hidden", !(free && custom));

  // ---- match-source toggle (edit mode with a loaded source)
  const canMatch = S.tab === "edit" && S.sources.length > 0;
  $("matchInputWrap").classList.toggle("hidden", !canMatch);
  if (!canMatch && matching) S.sizeStrategy = "combo";
  $("matchInput").checked = S.sizeStrategy === "match";

  // ---- constraint + resolved
  $("sizeConstraint").textContent = free
    ? t("size_constraint", { edge: caps.freeSize.edgeMultiple, max: caps.freeSize.maxLongEdge, ratio: caps.freeSize.maxRatio })
    : "";
  const resolved = currentSize();
  const suffix = matching ? t("suffix_source") : (custom ? t("suffix_custom") : "");
  $("sizeResolved").textContent = resolved ? `→ ${resolved}${suffix}` : "";
}

function syncCustomSize() {
  const caps = S.models[S.model].caps;
  const d = DEFINITIONS.find((x) => x.id === S.def) || DEFINITIONS[0];
  const r = RATIOS.find((x) => x.id === S.ratio) || RATIOS[0];
  const base = caps.sizeMode === "free" ? sizeFor(S.ratio, d.long) : (r.preset || "1024x1024");
  const [w, h] = base.split("x");
  $("sizeW").value = w; $("sizeH").value = h;
}

function currentSize() {
  const caps = S.models[S.model].caps;
  if (S.sizeStrategy === "match" && S.inputDims) {
    return caps.sizeMode === "preset"
      ? nearestPreset(caps.presets, S.inputDims.w, S.inputDims.h)
      : snapFree(S.inputDims.w, S.inputDims.h);
  }
  if (caps.sizeMode === "free" && S.sizeStrategy === "custom") {
    const w = parseInt($("sizeW").value || "1024", 10);
    const h = parseInt($("sizeH").value || "1024", 10);
    return `${w}x${h}`;
  }
  // combo: ratio × definition
  const r = RATIOS.find((x) => x.id === S.ratio) || RATIOS[0];
  if (caps.sizeMode === "preset") return r.preset || caps.presets[0];
  const d = DEFINITIONS.find((x) => x.id === S.def) || DEFINITIONS[0];
  return sizeFor(S.ratio, d.long);
}

// ------------------------------------------------------------------ tabs
function bindTabs() {
  document.querySelectorAll(".tab").forEach((tabEl) => {
    tabEl.onclick = () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      tabEl.classList.add("active");
      S.tab = tabEl.dataset.tab;
      $("editSources").classList.toggle("hidden", S.tab !== "edit");
      updatePromptPlaceholder();
      applyCaps();
    };
  });
}

function bindStaticInputs() {
  $("prompt").addEventListener("input", (e) => { $("promptCount").textContent = e.target.value.length; });
  $("compression").addEventListener("input", (e) => { $("compressionVal").textContent = e.target.value; });
  $("nImages").addEventListener("input", (e) => { $("nVal").textContent = e.target.value; });
  $("transparent").addEventListener("change", applyCaps);
  const onCustom = () => { S.sizeStrategy = "custom"; applyCaps(); };
  $("sizeW").addEventListener("input", onCustom);
  $("sizeH").addEventListener("input", onCustom);
  $("matchInput").addEventListener("change", (e) => {
    S.sizeStrategy = e.target.checked ? "match" : "combo"; applyCaps();
  });
  $("runBtn").addEventListener("click", run);
}

// ------------------------------------------------------------------ run
async function run() {
  const prompt = $("prompt").value.trim();
  if (!prompt) { showErr(t("err_prompt_required")); return; }
  if (S.tab === "edit" && S.sources.length === 0) { showErr(t("err_need_source")); return; }

  setBusy(true); showErr(""); showRevised("");
  try {
    const data = S.tab === "edit" ? await runEdit(prompt) : await runGenerate(prompt);
    renderResults(data);
    if (data.revised_prompt) showRevised(data.revised_prompt);
  } catch (e) {
    showErr(e.message || t("err_unknown"));
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
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${t("err_generic")} ${r.status}`);
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
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${t("err_generic")} ${r.status}`);
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
    const dl = el("button", "", t("dl"));
    dl.onclick = () => downloadDataUrl(src, `imagelab-${data.model}-${Date.now()}-${i + 1}.${data.format}`);
    const toEdit = el("button", "", t("edit_btn"));
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
  const wrap = $("thumbs"); wrap.innerHTML = "";
  S.sources.forEach((s, i) => {
    const d = el("div", "t");
    const img = el("img"); img.src = s.url;
    const x = el("button", "", "×");
    x.onclick = () => {
      S.sources.splice(i, 1); renderThumbs();
      if (S.sources[0]) { setupMask(S.sources[0].url); }
      else { $("maskEditor").classList.add("hidden"); S.inputDims = null; applyCaps(); }
    };
    d.append(img, x); wrap.appendChild(d);
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
    S.inputDims = { w: img.naturalWidth, h: img.naturalHeight };
    // display size caps at container width
    const stage = $("maskStage");
    stage.style.width = Math.min(img.naturalWidth, 520) + "px";
    $("maskEditor").classList.remove("hidden");
    bindPaint();
    applyCaps();  // refresh resolved size / match toggle now that we know dims
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
  $("status").textContent = b ? t("status_generating") : "";
}
function showErr(m) { $("errBox").innerHTML = m ? `<div class="err">${m}</div>` : ""; }
function showRevised(m) { $("revisedBox").innerHTML = m ? `<div class="revised"><b>${t("revised_label")}</b> ${m}</div>` : ""; }

boot();
