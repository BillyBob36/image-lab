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
import sys
import threading
import urllib.parse
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from azure_client import AzureImageClient, AzureImageError
from bg_remove import remove_uniform_bg, CUTOUT_HINT
from capabilities import MODELS, DEFAULT_MODEL, public_models, validate_size

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

    try:
        with _sem:
            resp = client.generate(
                m["deployment"], prompt=prompt, size=size, quality=quality, n=n,
                output_format=output_format, background=background,
                output_compression=comp, moderation=moderation,
            )
    except AzureImageError as e:
        raise HTTPException(e.status, e.message)

    images = _data_urls(resp, output_format, cutout=cutout)
    if not images:
        raise HTTPException(502, "Réponse Azure vide (aucune image).")
    revised = (resp.get("data") or [{}])[0].get("revised_prompt")
    return {"images": images, "model": model_id, "size": size, "format": output_format,
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

    try:
        with _sem:
            resp = client.edit(
                m["deployment"], prompt=prompt.strip(), images=img_parts, mask=mask_part,
                size=size, quality=quality, n=n, output_format=output_format,
                background=background, input_fidelity=fidelity, moderation=mod,
            )
    except AzureImageError as e:
        raise HTTPException(e.status, e.message)

    out = _data_urls(resp, output_format)
    if not out:
        raise HTTPException(502, "Réponse Azure vide (aucune image).")
    return {"images": out, "model": model, "size": size, "format": output_format,
            "usage": resp.get("usage")}


# ---------------------------------------------------------------- Auth
_PUBLIC_PREFIXES = (
    "/auth/", "/login.html", "/style.css", "/app.js",
    "/favicon", "/openapi.json", "/docs", "/redoc",
)
_PUBLIC_EXACT = {"/api/me", "/api/config"}


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
