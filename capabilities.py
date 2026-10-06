"""Single source of truth for per-model capabilities.

Served verbatim to the frontend (drives which controls are enabled/greyed) AND
used server-side to validate requests. Deployment names are overridable via env
so the same code runs against any Azure resource.

Capability facts come from Microsoft's official docs:
  - GPT-image-2: GA. Arbitrary resolutions (edges ×16, long edge ≤3840 / 4K,
    ratio ≤3:1, pixels 655360–8294400). Reworked low/medium/high quality.
    Does NOT support native background=transparent on Azure (rejected) →
    we offer a post-processed cutout fallback instead.
  - GPT-image-1.5: preview, gpt-image-1 series. Fixed sizes (1024², 1024×1536,
    1536×1024). Supports native background=transparent (requires PNG).
  Both: n 1–10, edits+mask+multi-image, input_fidelity, moderation, png/jpeg,
  jpeg compression, prompt ≤32000 chars.
"""
from __future__ import annotations

import os

PRESET_SIZES = ["1024x1024", "1536x1024", "1024x1536"]

FREE_SIZE_CONSTRAINTS = {
    "edgeMultiple": 16,
    "minEdge": 512,
    "maxLongEdge": 3840,
    "maxRatio": 3.0,
    "minPixels": 655360,
    "maxPixels": 8294400,
}

MODELS: dict[str, dict] = {
    "qwen-image-2.1": {
        "id":"qwen-image-2.1", "label":"Qwen Image 2.1", "provider":"qwen",
        "tagline":"Références multiples, seed et génération sur votre GPU", "availability":"A100",
        "caps": {
            "sizeMode":"free", "presets":["1024x1024","2048x1152","1152x2048"],
            "freeSize":{"edgeMultiple":32,"minEdge":256,"maxLongEdge":2048,"maxRatio":8.0,"minPixels":65536,"maxPixels":4194304},
            "definitions":[{"id":"1k","long":1024,"label":"1K","sub":"1024 px"},{"id":"2k","long":2048,"label":"2K","sub":"2048 px"}],
            "qualities":[],"defaultQuality":"","nativeTransparency":False,"transparencyFallback":False,
            "formats":["png"],"jpegCompression":False,"maxImages":6,"moderation":[],
            "edit":True,"mask":False,"inputFidelity":False,"multiImage":True,"maxReferences":8,
            "maxPromptChars":16000,"seed":True,"steps":True,"guidance":True,"negativePrompt":True,"queue":True,
        },
    },
    "gpt-image-2": {
        "id": "gpt-image-2",
        "label": "GPT-image 2.0",
        "tagline": "Haute résolution & 4K, édition améliorée, ratios libres",
        "deployment": os.environ.get("AZURE_DEPLOYMENT_GPTIMAGE2", "gpt-image-2-claude-skills"),
        "availability": "GA",
        "caps": {
            "sizeMode": "free",              # presets + arbitrary resolutions
            "presets": PRESET_SIZES,
            "freeSize": FREE_SIZE_CONSTRAINTS,
            "qualities": ["low", "medium", "high"],
            "defaultQuality": "high",
            "nativeTransparency": False,     # Azure rejects background=transparent on 2.0
            "transparencyFallback": True,    # Pillow cutout instead
            "formats": ["png", "jpeg"],
            "jpegCompression": True,
            "maxImages": 10,
            "moderation": ["auto", "low"],
            "edit": True,
            "mask": True,
            "inputFidelity": True,
            "multiImage": True,
            "maxPromptChars": 32000,
        },
    },
    "gpt-image-1.5": {
        "id": "gpt-image-1.5",
        "label": "GPT-image 1.5",
        "tagline": "Réalisme, respect du prompt, transparence native (alpha)",
        "deployment": os.environ.get("AZURE_DEPLOYMENT_GPTIMAGE15", "gpt-image-1.5-app-Assets"),
        "availability": "Preview",
        "caps": {
            "sizeMode": "preset",            # fixed sizes only
            "presets": PRESET_SIZES,
            "freeSize": None,
            "qualities": ["low", "medium", "high"],
            "defaultQuality": "high",
            "nativeTransparency": True,      # background=transparent, PNG
            "transparencyFallback": False,
            "formats": ["png", "jpeg"],
            "jpegCompression": True,
            "maxImages": 10,
            "moderation": ["auto", "low"],
            "edit": True,
            "mask": True,
            "inputFidelity": True,
            "multiImage": True,
            "maxPromptChars": 32000,
        },
    },
}

DEFAULT_MODEL = "gpt-image-2"


def public_models() -> list[dict]:
    """Frontend-facing list (no secrets — deployment name is safe to expose)."""
    return [
        {
            "id": m["id"],
            "label": m["label"],
            "tagline": m["tagline"],
            "availability": m["availability"],
            "caps": m["caps"],
        }
        for m in MODELS.values()
    ]


# --------------------------------------------------------------- validation
def validate_size(model_id: str, size: str) -> str:
    """Raise ValueError if `size` is not allowed for the model. Returns size."""
    caps = MODELS[model_id]["caps"]
    if size == "auto":
        if MODELS[model_id].get('provider')=='qwen':raise ValueError('Choisis des dimensions explicites pour Qwen.')
        return size
    if caps["sizeMode"] == "preset":
        if size not in caps["presets"]:
            raise ValueError(
                f"{MODELS[model_id]['label']} n'accepte que {', '.join(caps['presets'])}."
            )
        return size
    # free mode (gpt-image-2)
    if size in caps["presets"]:
        return size
    try:
        w, h = (int(x) for x in size.lower().split("x"))
    except (ValueError, AttributeError):
        raise ValueError(f"Taille invalide '{size}'. Format attendu LARGEURxHAUTEUR.")
    c = caps["freeSize"]
    if w % c["edgeMultiple"] or h % c["edgeMultiple"]:
        raise ValueError(f"Chaque côté doit être un multiple de {c['edgeMultiple']} px (reçu {w}x{h}).")
    if min(w, h) < c["minEdge"]:
        raise ValueError(f"Côté minimum {c['minEdge']} px (reçu {w}x{h}).")
    if max(w, h) > c["maxLongEdge"]:
        raise ValueError(f"Côté le plus long ≤ {c['maxLongEdge']} px (reçu {max(w, h)}).")
    if max(w, h) / min(w, h) > c["maxRatio"]:
        raise ValueError(f"Ratio ≤ {c['maxRatio']:.0f}:1 (reçu {max(w, h) / min(w, h):.2f}:1).")
    if not (c["minPixels"] <= w * h <= c["maxPixels"]):
        raise ValueError(
            f"Nombre de pixels entre {c['minPixels']} et {c['maxPixels']} (reçu {w * h})."
        )
    return size
