"""Uniform-background → alpha conversion (in-memory).

Fallback path for GPT-image 2.0, which does NOT support native
`background=transparent` on Azure. We ask the model for a clean cutout on a
solid background, then map near-background pixels to alpha here. Works well for
icons / logos / isolated subjects; not for complex photos.
"""
from __future__ import annotations

import io

from PIL import Image

CUTOUT_HINT = (
    ", isolated subject centered on a plain solid white background, "
    "no shadow, sharp clean edges, studio cutout style, no text"
)


def remove_uniform_bg(png_bytes: bytes, tolerance: int = 32, feather: int = 18) -> bytes:
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    w, h = img.size
    px = img.load()
    samples = [
        px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1],
        px[w // 2, 0], px[w // 2, h - 1], px[0, h // 2], px[w - 1, h // 2],
    ]
    rs = sorted(s[0] for s in samples)
    gs = sorted(s[1] for s in samples)
    bs = sorted(s[2] for s in samples)
    bg = (rs[len(rs) // 2], gs[len(gs) // 2], bs[len(bs) // 2])

    out = img.copy()
    out_px = out.load()
    t2 = tolerance * tolerance
    span = max(feather, 1)
    span_sq_inv = 1.0 / (span * span)
    for y in range(h):
        for x in range(w):
            r, g, b, _ = px[x, y]
            dr, dg, db = r - bg[0], g - bg[1], b - bg[2]
            d2 = dr * dr + dg * dg + db * db
            if d2 <= t2:
                a = 0
            else:
                ratio = (d2 - t2) * span_sq_inv
                a = 255 if ratio >= 1 else int(255 * ratio)
            out_px[x, y] = (r, g, b, a)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()
