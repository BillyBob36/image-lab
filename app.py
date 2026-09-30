"""Image Lab — two-model Azure image studio (GPT-image 2.0 + 1.5).

Endpoints:
  GET  /api/config                 -> models capability matrix + auth state
  POST /api/generate               -> text-to-image (JSON)
  POST /api/edit                   -> edit / inpaint / compose (multipart)
  GET  /api/me                     -> current user (auth)
  GET  /auth/google/login|callback -> Google OAuth
  POST /auth/logout
  /                                -> static SPA (index.html), gated by auth
"""
from __future__ import annotations

import base64
import os
import secrets
import sqlite3
import sys
import threading
import urllib.parse
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Query
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.concurrency import run_in_threadpool

from azure_client import AzureImageClient, AzureImageError
from bg_remove import remove_uniform_bg, CUTOUT_HINT
from capabilities import MODELS, DEFAULT_MODEL, public_models, validate_size
from gallery import ImageStore, StorageUnavailable, MAX_IMAGE_BYTES

# ---------------------------------------------------------------- config
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

AZURE_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
AZURE_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
AZURE_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY", "")
MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY", "4"))

# ---- Auth config
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
ALLOWED_EMAILS = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip() or secrets.token_urlsafe(48)
AUTH_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and ALLOWED_EMAILS)

if not all([AZURE_ENDPOINT, AZURE_API_KEY]):
    print("WARN: AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY missing.", file=sys.stderr)

if AUTH_ENABLED:
    if not PUBLIC_URL:
        print("WARN: AUTH_ENABLED but PUBLIC_URL missing — OAuth redirect URI relative.", file=sys.stderr)
    print(f"[auth] Google OAuth enabled, allowlist: {sorted(ALLOWED_EMAILS)}", file=sys.stderr)
else:
    print("[auth] DISABLED (open) — set GOOGLE_CLIENT_ID/SECRET + ALLOWED_EMAILS to enable", file=sys.stderr)

client = AzureImageClient(AZURE_ENDPOINT, AZURE_API_KEY, AZURE_API_VERSION)
_sem = threading.Semaphore(MAX_CONCURRENCY)

app = FastAPI(title="Image Lab")
store = ImageStore(Path(os.environ.get("IMAGE_STORAGE_DIR", str(BASE_DIR / "data"))),
                   required=os.environ.get("IMAGE_STORAGE_REQUIRED") == "1")


def _owner(request):
    return request.session["user_email"] if AUTH_ENABLED else "anonymous"


def _provider(method, **kwargs):
    with _sem:
        return getattr(client, method)(**kwargs)


async def _persist(request, images, **metadata):
    try:
        items = await run_in_threadpool(store.save_data_urls, images, owner=_owner(request), **metadata)
        return {"images": [item["url"] for item in items], "saved_images": items}
    except (OSError, sqlite3.Error, StorageUnavailable, ValueError):
        # Preserve a paid result even if storage fails after the preflight check.
        print("[gallery] Could not save provider result; returning originals to the browser", file=sys.stderr)
        return {"images": images, "saved_images": [], "storage_warning": True}


@app.exception_handler(StorageUnavailable)
async def storage_unavailable(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=503)


# ---------------------------------------------------------------- helpers
def _data_urls(resp: dict, fmt: str, *, cutout: bool = False) -> list[str]:
    mime = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}.get(fmt, "image/png")
    out: list[str] = []
    for item in resp.get("data") or []:
        b64 = item.get("b64_json")
        if not b64:
            continue
        if cutout:
            raw = remove_uniform_bg(base64.b64decode(b64))
            b64 = base64.b64encode(raw).decode("ascii")
            mime = "image/png"
        out.append(f"data:{mime};base64,{b64}")
    return out


def _model_or_400(model_id: str) -> dict:
    m = MODELS.get(model_id)
    if not m:
        raise HTTPException(400, f"Modèle inconnu: {model_id}")
    return m


def _check_common(m: dict, prompt: str, quality: str, n: int, output_format: str):
    caps = m["caps"]
    if not prompt or not prompt.strip():
        raise HTTPException(400, "Le prompt est requis.")
    if len(prompt) > caps["maxPromptChars"]:
        raise HTTPException(400, f"Prompt trop long (max {caps['maxPromptChars']} caractères).")
    if quality not in caps["qualities"]:
        raise HTTPException(400, f"Qualité invalide pour {m['label']}.")
    if not (1 <= n <= caps["maxImages"]):
        raise HTTPException(400, f"Nombre d'images entre 1 et {caps['maxImages']}.")
    if output_format not in caps["formats"]:
        raise HTTPException(400, f"Format invalide pour {m['label']}.")


# ---------------------------------------------------------------- API: config
@app.get("/api/config")
def api_config():
    return {
        "models": public_models(),
        "defaultModel": DEFAULT_MODEL,
        "auth": {"enabled": AUTH_ENABLED},
        "apiVersion": AZURE_API_VERSION,
    }


# ---------------------------------------------------------------- API: generate
@app.post("/api/generate")
async def api_generate(request: Request):
    body = await request.json()
    model_id = body.get("model", DEFAULT_MODEL)
    m = _model_or_400(model_id)
    caps = m["caps"]

    prompt = (body.get("prompt") or "").strip()
    quality = body.get("quality") or caps["defaultQuality"]
    n = int(body.get("n") or 1)
    output_format = (body.get("format") or "png").lower()
    size = body.get("size") or "1024x1024"
    moderation = body.get("moderation")
    if moderation not in (None, "auto", "low"):
        moderation = None
    transparent = bool(body.get("transparent"))
    compression = body.get("compression")

    _check_common(m, prompt, quality, n, output_format)
    try:
        size = validate_size(model_id, size)
    except ValueError as e:
        raise HTTPException(400, str(e))

    background = None
    cutout = False
    if transparent:
        if caps["nativeTransparency"]:
            background = "transparent"
            output_format = "png"
        elif caps["transparencyFallback"]:
            cutout = True
            output_format = "png"
            if CUTOUT_HINT not in prompt:
                prompt = prompt + CUTOUT_HINT
        else:
            raise HTTPException(400, f"{m['label']} ne supporte pas la transparence.")

    comp = None
    if output_format == "jpeg" and caps["jpegCompression"] and compression is not None:
        try:
            comp = max(0, min(100, int(compression)))
        except (TypeError, ValueError):
            comp = None

    await run_in_threadpool(store.ensure_writable, n)
    try:
        resp = await run_in_threadpool(_provider, "generate",
                deployment=m["deployment"], prompt=prompt, size=size, quality=quality, n=n,
                output_format=output_format, background=background,
                output_compression=comp, moderation=moderation,
            )
    except AzureImageError as e:
        raise HTTPException(e.status, e.message)

    images = await run_in_threadpool(_data_urls, resp, output_format, cutout=cutout)
    if not images:
        raise HTTPException(502, "Réponse Azure vide (aucune image).")
    revised = (resp.get("data") or [{}])[0].get("revised_prompt")
    saved = await _persist(request, images, prompt=body.get("prompt", "").strip(),
                           revised_prompt=revised, model=model_id, mode="generate", quality=quality)
    return {**saved, "model": model_id, "size": size, "format": output_format,
            "revised_prompt": revised, "usage": resp.get("usage")}


# ---------------------------------------------------------------- API: edit
@app.post("/api/edit")
async def api_edit(
    request: Request,
    model: str = Form(DEFAULT_MODEL),
    prompt: str = Form(...),
    size: str = Form("1024x1024"),
    quality: str = Form(""),
    n: int = Form(1),
    format: str = Form("png"),
    transparent: bool = Form(False),
    input_fidelity: str = Form(""),
    moderation: str = Form(""),
    images: list[UploadFile] = File(...),
    mask: UploadFile | None = File(None),
):
    m = _model_or_400(model)
    caps = m["caps"]
    if not caps["edit"]:
        raise HTTPException(400, f"{m['label']} ne supporte pas l'édition.")
    quality = quality or caps["defaultQuality"]
    output_format = (format or "png").lower()

    _check_common(m, prompt, quality, n, output_format)
    try:
        size = validate_size(model, size)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if not images:
        raise HTTPException(400, "Au moins une image source est requise.")
    if len(images) > 1 and not caps["multiImage"]:
        raise HTTPException(400, f"{m['label']} n'accepte qu'une seule image.")

    img_parts: list[tuple[str, bytes]] = []
    for up in images:
        data = await up.read()
        if not data:
            raise HTTPException(400, "Image source vide.")
        if len(data) > 50 * 1024 * 1024:
            raise HTTPException(400, "Image > 50 Mo.")
        img_parts.append((up.filename or "image.png", data))

    mask_part = None
    if mask is not None:
        md = await mask.read()
        if md:
            if len(md) > 4 * 1024 * 1024:
                raise HTTPException(400, "Masque > 4 Mo.")
            mask_part = (mask.filename or "mask.png", md)

    background = None
    if transparent:
        if caps["nativeTransparency"]:
            background = "transparent"
            output_format = "png"
        else:
            raise HTTPException(400, f"La transparence native n'est disponible que sur les modèles qui la supportent (pas {m['label']}).")

    fidelity = input_fidelity if (input_fidelity in ("low", "high") and caps["inputFidelity"]) else None
    mod = moderation if moderation in ("auto", "low") else None

    await run_in_threadpool(store.ensure_writable, n)
    try:
        resp = await run_in_threadpool(_provider, "edit",
                deployment=m["deployment"], prompt=prompt.strip(), images=img_parts, mask=mask_part,
                size=size, quality=quality, n=n, output_format=output_format,
                background=background, input_fidelity=fidelity, moderation=mod,
            )
    except AzureImageError as e:
        raise HTTPException(e.status, e.message)

    out = await run_in_threadpool(_data_urls, resp, output_format)
    if not out:
        raise HTTPException(502, "Réponse Azure vide (aucune image).")
    saved = await _persist(request, out, prompt=prompt.strip(), model=model, mode="edit", quality=quality)
    return {**saved, "model": model, "size": size, "format": output_format,
            "usage": resp.get("usage")}


# ---------------------------------------------------------------- Private gallery
@app.get("/api/health")
def health():
    store._check_root()
    return {"status": "ok"}


@app.get("/api/gallery")
def gallery_list(request: Request, q: str = Query("", max_length=200),
                 before: str = Query("", max_length=100), limit: int = Query(24, ge=1, le=60)):
    try:
        result = store.list(_owner(request), query=q.strip(), before=before, limit=limit)
        return JSONResponse(result, headers={"Cache-Control": "private, no-store"})
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "La galerie est indisponible pour le moment.")


@app.post("/api/gallery/import")
async def gallery_import(request: Request, image: UploadFile = File(...)):
    raw = await image.read(MAX_IMAGE_BYTES + 1)
    try:
        items = await run_in_threadpool(store.save, [raw], owner=_owner(request),
                                        original_name=image.filename or "", deduplicate=True)
        return {"item": items[0]}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except (OSError, sqlite3.Error):
        raise HTTPException(503, "Impossible d'enregistrer l'image pour le moment.")


@app.get("/api/gallery/{image_id}/{variant}")
def gallery_file(request: Request, image_id: str, variant: str):
    if variant not in {"image", "thumbnail", "download"}:
        raise HTTPException(404, "Image introuvable.")
    result = store.file(_owner(request), image_id, thumbnail=variant == "thumbnail")
    if result is None:
        raise HTTPException(404, "Image introuvable.")
    path, item = result
    mime = "image/webp" if variant == "thumbnail" else "image/" + item["format"]
    filename = f"imagelab-{item['created_at'][:10]}-{image_id[:8]}.{item['format']}"
    return FileResponse(path, media_type=mime,
                        filename=filename if variant == "download" else None,
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- Auth
_PUBLIC_PREFIXES = (
    "/auth/", "/login.html", "/style.css", "/app.js",
    "/logo.png", "/favicon", "/openapi.json", "/docs", "/redoc",
)
_PUBLIC_EXACT = {"/api/me", "/api/config", "/api/health"}


def _is_public_path(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    return any(path.startswith(p) for p in _PUBLIC_PREFIXES)


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    if not AUTH_ENABLED:
        return await call_next(request)
    path = request.url.path
    if _is_public_path(path):
        return await call_next(request)
    email = request.session.get("user_email")
    authorized = bool(email) and email in ALLOWED_EMAILS
    if path.startswith("/api/"):
        if not authorized:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        return await call_next(request)
    if not authorized:
        return RedirectResponse("/login.html", status_code=302)
    return await call_next(request)


def _redirect_uri() -> str:
    return f"{PUBLIC_URL or ''}/auth/google/callback"


@app.get("/auth/google/login")
def google_login(request: Request):
    if not AUTH_ENABLED:
        raise HTTPException(503, "auth not configured")
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return RedirectResponse(url, status_code=302)


@app.get("/auth/google/callback")
def google_callback(request: Request, code: str | None = None,
                    state: str | None = None, error: str | None = None):
    if not AUTH_ENABLED:
        raise HTTPException(503, "auth not configured")
    if error:
        return RedirectResponse(f"/login.html?error={urllib.parse.quote(error)}", status_code=302)
    expected = request.session.pop("oauth_state", None)
    if not code or not state or state != expected:
        return RedirectResponse("/login.html?error=invalid_state", status_code=302)
    try:
        tok = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        tok.raise_for_status()
        access_token = tok.json().get("access_token")
        if not access_token:
            raise RuntimeError("no access_token in token response")
        info = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        info.raise_for_status()
        user = info.json()
    except (requests.RequestException, RuntimeError, ValueError) as e:
        print(f"[auth] OAuth exchange failed: {e}", file=sys.stderr)
        return RedirectResponse("/login.html?error=token_exchange", status_code=302)

    email = (user.get("email") or "").lower()
    verified = bool(user.get("email_verified"))
    if not email or not verified:
        return RedirectResponse("/login.html?error=unverified", status_code=302)
    if email not in ALLOWED_EMAILS:
        q = urllib.parse.urlencode({"error": "unauthorized", "email": email})
        return RedirectResponse(f"/login.html?{q}", status_code=302)

    request.session["user_email"] = email
    request.session["user_name"] = user.get("name", "")
    request.session["user_picture"] = user.get("picture", "")
    return RedirectResponse("/", status_code=302)


@app.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    if not AUTH_ENABLED:
        return {"authenticated": True, "email": "anonymous", "auth_enabled": False}
    email = request.session.get("user_email")
    if not email or email not in ALLOWED_EMAILS:
        return JSONResponse({"authenticated": False, "auth_enabled": True}, status_code=401)
    return {
        "authenticated": True,
        "auth_enabled": True,
        "email": email,
        "name": request.session.get("user_name", ""),
        "picture": request.session.get("user_picture", ""),
    }


# Static SPA mounted LAST so API/auth routes win. html=True serves index.html at /.
app.mount("/", StaticFiles(directory=str(BASE_DIR / "static"), html=True), name="static")

# SessionMiddleware added AFTER the decorator so it wraps outermost (populates
# request.session before auth_gate runs). See google_oauth_recipe pitfall #1.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=PUBLIC_URL.startswith("https://"),
    same_site="lax",
    max_age=14 * 24 * 3600,
)
