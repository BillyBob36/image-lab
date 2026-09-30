"use strict";

const G = {items: [], cursor: null, total: 0, request: 0, loading: false, loaded: false, viewing: [], index: 0};

function bindGallery() {
  Object.assign(I18N.fr, {
    tab_gallery: "Ma galerie", gallery_intro: "Vos créations sont enregistrées automatiquement. Retrouvez-les ici à tout moment.",
    gallery_import: "Ajouter des images", gallery_search_label: "Rechercher", gallery_search: "Prompt, nom ou modèle…",
    gallery_refresh: "Actualiser", gallery_more: "Afficher la suite", gallery_empty: "Votre première image vous attend",
    gallery_empty_hint: "Générez une image ou ajoutez celles que vous aviez déjà téléchargées.", gallery_count: "{n} image(s)",
    gallery_no_match: "Aucune image ne correspond à cette recherche.", gallery_loading: "Chargement des images…",
    gallery_importing: "Enregistrement des images…", gallery_imported: "{n} image(s) ajoutée(s) à votre galerie.",
    gallery_failed: "Impossible de charger la galerie. Utilisez Actualiser pour réessayer.",
    preview: "Agrandir l'image", close: "Fermer", download_original: "Télécharger l'original", zoom_original: "Taille réelle",
    zoom_fit: "Adapter à l'écran", reuse_prompt: "Réutiliser le prompt", previous: "Précédente", next: "Suivante",
    imported_image: "Image importée", generated_image: "Image générée", edited_image: "Image éditée",
    no_prompt: "Le prompt n'est pas disponible pour cette image importée.", image_unavailable: "L'image est indisponible. Fermez l'aperçu puis actualisez la galerie.",
    image_loading: "Chargement de l'original…", saved_to_gallery: "Images enregistrées · Voir ma galerie",
    storage_warning: "Ces images n'ont pas pu être enregistrées dans la galerie. Téléchargez-les maintenant avant de fermer cette page.",
  });
  Object.assign(I18N.en, {
    tab_gallery: "My gallery", gallery_intro: "Your creations are saved automatically. Find them here whenever you need them.",
    gallery_import: "Add images", gallery_search_label: "Search", gallery_search: "Prompt, name or model…",
    gallery_refresh: "Refresh", gallery_more: "Load more", gallery_empty: "Your first image awaits",
    gallery_empty_hint: "Generate an image or add ones you have already downloaded.", gallery_count: "{n} image(s)",
    gallery_no_match: "No images match this search.", gallery_loading: "Loading images…",
    gallery_importing: "Saving images…", gallery_imported: "{n} image(s) added to your gallery.",
    gallery_failed: "Unable to load the gallery. Use Refresh to try again.",
    preview: "Enlarge image", close: "Close", download_original: "Download original", zoom_original: "Actual size",
    zoom_fit: "Fit to screen", reuse_prompt: "Reuse prompt", previous: "Previous", next: "Next",
    imported_image: "Imported image", generated_image: "Generated image", edited_image: "Edited image",
    no_prompt: "The prompt is unavailable for this imported image.", image_unavailable: "This image is unavailable. Close the preview and refresh the gallery.",
    image_loading: "Loading original…", saved_to_gallery: "Images saved · View my gallery",
    storage_warning: "These images could not be saved to the gallery. Download them now before closing this page.",
  });
  let debounce;
  $("gallerySearch").oninput = () => { clearTimeout(debounce); debounce = setTimeout(() => loadGallery(), 250); };
  $("galleryRefresh").onclick = () => loadGallery();
  $("galleryMore").onclick = () => loadGallery(true);
  $("galleryCreate").onclick = () => selectTab("generate");
  $("galleryImport").onclick = () => $("galleryFiles").click();
  $("galleryFiles").onchange = async () => {
    const files = [...$("galleryFiles").files];
    $("galleryFiles").value = "";
    if (!files.length) return;
    $("galleryImport").disabled = true;
    galleryMessage(t("gallery_importing"));
    let count = 0, failure = "";
    try {
      for (const file of files) {
        if (file.size > 50 * 1024 * 1024) throw new Error(file.name + " : 50 Mo maximum.");
        const body = new FormData(); body.append("image", file);
        const r = await api("/api/gallery/import", {method: "POST", body});
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || t("err_unknown"));
        count++;
      }
    } catch (e) { failure = e.message; }
    await loadGallery();
    galleryMessage((count ? t("gallery_imported", {n: count}) + " " : "") + failure, !!failure);
    $("galleryImport").disabled = false;
  };
  $("viewerClose").onclick = () => $("imageViewer").close();
  $("imageViewer").addEventListener("close", () => { document.body.style.overflow = ""; });
  $("imageViewer").addEventListener("click", (event) => { if (event.target === $("imageViewer")) $("imageViewer").close(); });
  $("imageViewer").addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault(); moveViewer(event.key === "ArrowLeft" ? -1 : 1);
    }
  });
  $("viewerPrev").onclick = () => moveViewer(-1);
  $("viewerNext").onclick = () => moveViewer(1);
  $("viewerZoom").onclick = () => {
    const zoomed = $("viewerStage").classList.toggle("zoomed");
    $("viewerZoom").textContent = t(zoomed ? "zoom_fit" : "zoom_original");
    $("viewerZoom").setAttribute("aria-pressed", String(zoomed));
  };
  $("viewerEdit").onclick = async () => {
    $("viewerEdit").disabled = true;
    try { await sendToEdit(G.viewing[G.index].url); $("imageViewer").close(); }
    catch (e) { $("viewerImageStatus").textContent = e.message; }
    finally { $("viewerEdit").disabled = false; }
  };
  $("viewerReuse").onclick = () => {
    const item = G.viewing[G.index];
    if (S.models[item.model]) selectModel(item.model);
    $("prompt").value = item.prompt;
    $("promptCount").textContent = item.prompt.length;
    $("imageViewer").close(); selectTab("generate"); $("prompt").focus();
  };
}

function galleryMessage(message, error=false) {
  $("galleryMessage").textContent = message;
  $("galleryMessage").classList.toggle("gallery-error", error);
}

async function loadGallery(append=false) {
  if (append && (G.loading || !G.cursor)) return;
  const sequence = ++G.request;
  G.loading = true;
  $("galleryGrid").setAttribute("aria-busy", "true");
  $("galleryMore").disabled = true;
  galleryMessage(t("gallery_loading"));
  const params = new URLSearchParams({q: $("gallerySearch").value.trim(), limit: "24"});
  if (append) params.set("before", G.cursor);
  try {
    const response = await api("/api/gallery?" + params);
    if (!response.ok) throw new Error(t("gallery_failed"));
    const data = await response.json();
    if (sequence !== G.request) return;
    G.items = append ? [...G.items, ...data.items] : data.items;
    G.cursor = data.next_cursor; G.total = data.total; G.loaded = true;
    renderGallery();
    galleryMessage(!G.items.length && params.get("q") ? t("gallery_no_match") : "");
  } catch (e) {
    if (sequence === G.request) galleryMessage(e.message, true);
  } finally {
    if (sequence === G.request) {
      G.loading = false;
      $("galleryGrid").setAttribute("aria-busy", "false");
      $("galleryMore").disabled = false;
    }
  }
}

function imageTitle(item) {
  return item.prompt || item.original_name || t(item.mode === "edit" ? "edited_image" : item.mode === "import" ? "imported_image" : "generated_image");
}

function imageMeta(item) {
  const parts = [];
  if (item.created_at) parts.push(new Intl.DateTimeFormat(LANG, {dateStyle: "medium", timeStyle: "short"}).format(new Date(item.created_at)));
  if (item.width) parts.push(`${item.width} × ${item.height}`);
  if (item.bytes) parts.push(new Intl.NumberFormat(LANG, {maximumFractionDigits: 1}).format(item.bytes / 1e6) + " Mo");
  if (item.model) parts.push(S.models[item.model]?.label || item.model);
  return parts.join(" · ");
}

function renderGallery() {
  if (!$("galleryGrid") || !G.loaded) return;
  $("galleryCount").textContent = t("gallery_count", {n: G.total});
  $("galleryEmpty").classList.toggle("hidden", G.items.length > 0 || !!$("gallerySearch").value.trim());
  $("galleryMore").classList.toggle("hidden", !G.cursor);
  $("galleryGrid").replaceChildren();
  G.items.forEach((item, index) => {
    const card = el("article", "gallery-card");
    const preview = el("button", "gallery-preview");
    preview.setAttribute("aria-label", t("preview") + " : " + imageTitle(item).slice(0, 100));
    preview.onclick = () => openViewer(G.items, index);
    const img = el("img"); img.src = item.thumbnail_url; img.alt = imageTitle(item).slice(0, 200);
    img.loading = "lazy"; img.decoding = "async"; img.width = item.width; img.height = item.height;
    preview.append(img);
    const caption = el("div", "gallery-caption");
    const title = el("p", "gallery-caption-title"); title.textContent = imageTitle(item); title.title = imageTitle(item);
    const meta = el("p", "hint"); meta.textContent = imageMeta(item);
    const actions = el("div", "gallery-card-actions");
    const open = el("button", "btn-ghost"); open.textContent = t("preview"); open.onclick = preview.onclick;
    const download = el("a", "btn-ghost"); download.href = item.download_url; download.download = ""; download.textContent = t("dl");
    actions.append(open, download); caption.append(title, meta, actions); card.append(preview, caption);
    $("galleryGrid").append(card);
  });
}

function openViewer(items, index) {
  G.viewing = items; G.index = index;
  renderViewer();
  $("imageViewer").showModal();
  document.body.style.overflow = "hidden";
}

function moveViewer(delta) {
  const index = G.index + delta;
  if (index < 0 || index >= G.viewing.length) return;
  G.index = index; renderViewer();
}

function renderViewer() {
  const item = G.viewing[G.index];
  if (!item) return;
  $("viewerTitle").textContent = t("preview");
  $("viewerPosition").textContent = `${G.index + 1} / ${G.viewing.length}`;
  $("viewerMeta").textContent = imageMeta(item);
  $("viewerPrompt").textContent = item.prompt || (item.mode === "import" ? t("no_prompt") : "");
  $("viewerDownload").href = item.download_url;
  $("viewerDownload").download = `imagelab-${item.id || Date.now()}.${item.format}`;
  $("viewerReuse").classList.toggle("hidden", !item.prompt);
  $("viewerPrev").disabled = G.index === 0;
  $("viewerNext").disabled = G.index === G.viewing.length - 1;
  $("viewerStage").classList.remove("zoomed");
  $("viewerStage").scrollTo(0, 0);
  $("viewerZoom").textContent = t("zoom_original");
  $("viewerZoom").setAttribute("aria-pressed", "false");
  const img = $("viewerImage");
  img.alt = imageTitle(item).slice(0, 300);
  img.style.width = item.width ? item.width + "px" : "auto";
  $("viewerImageStatus").textContent = t("image_loading");
  img.onload = () => { $("viewerImageStatus").textContent = ""; };
  img.onerror = () => { $("viewerImageStatus").textContent = t("image_unavailable"); };
  img.src = item.url;
  if (img.complete && img.naturalWidth) $("viewerImageStatus").textContent = "";
}
