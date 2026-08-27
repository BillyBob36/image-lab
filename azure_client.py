"""Azure OpenAI image client for the two-model Image Lab.

Wraps the `images/generations` and `images/edits` REST endpoints for the
GPT-image series deployments (gpt-image-2 and gpt-image-1.5) on a single
Azure Cognitive Services resource. One API key covers both deployments.

Docs:
  https://learn.microsoft.com/en-us/azure/foundry/openai/reference-preview
  https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/dall-e
"""
from __future__ import annotations

import io
import json
import time
import uuid
from urllib import error, request

# ---------------------------------------------------------------- exceptions
class AzureImageError(Exception):
    """Raised with a user-safe message + HTTP status when Azure rejects a call."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------------- low level
def _request_with_retry(url: str, body: bytes, headers: dict, max_retries: int = 4) -> dict:
    last: Exception | None = None
    for attempt in range(max_retries):
        try:
            req = request.Request(url, data=body, headers=headers, method="POST")
            with request.urlopen(req, timeout=240) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            if e.code == 429 and attempt < max_retries - 1:
                wait = int(e.headers.get("Retry-After", "6"))
                time.sleep(wait)
                last = e
                continue
            if 500 <= e.code < 600 and attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                last = e
                continue
            # Surface the Azure error message if we can parse it.
            msg = raw
            try:
                parsed = json.loads(raw)
                err = parsed.get("error") or {}
                msg = err.get("message") or err.get("code") or raw
            except (json.JSONDecodeError, AttributeError):
                pass
            raise AzureImageError(f"Azure {e.code}: {msg}", status=e.code if e.code < 500 else 502)
        except error.URLError as e:
            last = e
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise AzureImageError(f"Réseau: {e}", status=504)
    raise AzureImageError(f"Échec après {max_retries} tentatives: {last}", status=502)


def _multipart(fields: list[tuple[str, str]], files: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    """fields: [(name, value)]. files: [(name, filename, bytes)]. name may repeat."""
    boundary = f"----imagelab{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    for name, filename, data in files:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "webp": "image/webp"}.get(ext, "application/octet-stream")
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8")
        )
        parts.append(data)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary


class AzureImageClient:
    def __init__(self, endpoint: str, api_key: str, api_version: str):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.api_version = api_version

    def _url(self, deployment: str, action: str) -> str:
        return (f"{self.endpoint}/openai/deployments/{deployment}"
                f"/images/{action}?api-version={self.api_version}")

    # ----------------------------------------------------------- generations
    def generate(self, deployment: str, *, prompt: str, size: str, quality: str,
                 n: int, output_format: str, background: str | None = None,
                 output_compression: int | None = None,
                 moderation: str | None = None) -> dict:
        payload: dict = {
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "n": n,
            "output_format": output_format,
        }
        if background:
            payload["background"] = background
        if output_compression is not None and output_format == "jpeg":
            payload["output_compression"] = output_compression
        if moderation:
            payload["moderation"] = moderation
        body = json.dumps(payload).encode("utf-8")
        headers = {"api-key": self.api_key, "Content-Type": "application/json"}
        return _request_with_retry(self._url(deployment, "generations"), body, headers)

    # ------------------------------------------------------------------ edits
    def edit(self, deployment: str, *, prompt: str, images: list[tuple[str, bytes]],
             mask: tuple[str, bytes] | None, size: str, quality: str, n: int,
             output_format: str | None = None, background: str | None = None,
             input_fidelity: str | None = None,
             moderation: str | None = None) -> dict:
        fields: list[tuple[str, str]] = [
            ("prompt", prompt),
            ("size", size),
            ("quality", quality),
            ("n", str(n)),
        ]
        if output_format:
            fields.append(("output_format", output_format))
        if background:
            fields.append(("background", background))
        if input_fidelity:
            fields.append(("input_fidelity", input_fidelity))
        if moderation:
            fields.append(("moderation", moderation))

        # Azure rejects a repeated "image" part ("Duplicate parameter"); a multi-image
        # compose has to use the PHP-style array field name instead.
        field = "image[]" if len(images) > 1 else "image"
        files: list[tuple[str, str, bytes]] = [(field, fn, data) for fn, data in images]
        if mask is not None:
            files.append(("mask", mask[0], mask[1]))

        body, boundary = _multipart(fields, files)
        headers = {"api-key": self.api_key,
                   "Content-Type": f"multipart/form-data; boundary={boundary}"}
        return _request_with_retry(self._url(deployment, "edits"), body, headers)
