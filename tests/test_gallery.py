import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from PIL import Image

import app as module
from gallery import ImageStore, MARKER, StorageUnavailable


def picture(fmt="PNG"):
    stream = BytesIO()
    Image.new("RGBA" if fmt != "JPEG" else "RGB", (96, 64), (44, 66, 200, 100) if fmt != "JPEG" else (44, 66, 200)).save(stream, fmt)
    return stream.getvalue()


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ImageStore(Path(self.temp.name))
        self.raw = picture()

    def test_original_bytes_thumbnail_and_reopen(self):
        item = self.store.save([self.raw], owner="a")[0]
        reopened = ImageStore(self.store.root)
        path, stored = reopened.file("a", item["id"])
        self.assertEqual(path.read_bytes(), self.raw)
        self.assertNotIn("owner", stored)
        thumb, _ = reopened.file("a", item["id"], thumbnail=True)
        with Image.open(thumb) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertIn("A", image.getbands())
        self.assertEqual(reopened.list("a")["total"], 1)

    def test_formats_are_detected_from_bytes(self):
        for fmt, extension in [("PNG", "png"), ("JPEG", "jpeg"), ("WEBP", "webp")]:
            item = self.store.save([picture(fmt)], owner="a")[0]
            self.assertEqual(item["format"], extension)

    def test_ownership_and_path_traversal(self):
        item = self.store.save([self.raw], owner="a")[0]
        self.assertIsNone(self.store.file("b", item["id"]))
        self.assertEqual(self.store.list("b")["total"], 0)
        for unsafe in ["../gallery.sqlite3", "../../etc/passwd", "A" * 32, ""]:
            self.assertIsNone(self.store.get("a", unsafe))

    def test_cursor_search_and_literal_sql_wildcards(self):
        for prompt in ["chat bleu", "chat rouge", "100%_fin"]:
            self.store.save([self.raw], owner="a", prompt=prompt)
        first = self.store.list("a", limit=2)
        second = self.store.list("a", limit=2, before=first["next_cursor"])
        self.assertEqual(len(first["items"] + second["items"]), 3)
        self.assertEqual(len({i["id"] for i in first["items"] + second["items"]}), 3)
        self.assertEqual(self.store.list("a", query="chat")["total"], 2)
        self.assertEqual(self.store.list("a", query="%_")["total"], 1)
        with self.assertRaises(ValueError):
            self.store.list("a", before="not-a-cursor")

    def test_import_deduplication_is_per_owner(self):
        first = self.store.save([self.raw], owner="a", deduplicate=True)[0]
        second = self.store.save([self.raw], owner="a", deduplicate=True)[0]
        other = self.store.save([self.raw], owner="b", deduplicate=True)[0]
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["id"], other["id"])

    def test_delete_removes_owned_original_thumbnail_and_index(self):
        item, keep = self.store.save([self.raw, self.raw], owner="a")
        other = self.store.save([self.raw], owner="b")[0]
        self.assertFalse(self.store.delete("b", item["id"]))
        for unsafe in ["../gallery.sqlite3", "A" * 32, ""]:
            self.assertFalse(self.store.delete("a", unsafe))
        self.assertTrue(self.store.delete("a", item["id"]))
        self.assertFalse((self.store.root / item["id"]).exists())
        self.assertIsNone(self.store.file("a", item["id"]))
        self.assertFalse(self.store.delete("a", item["id"]))
        self.assertEqual(self.store.list("a")["total"], 1)
        self.assertEqual(self.store.file("a", keep["id"])[0].read_bytes(), self.raw)
        self.assertEqual(self.store.file("b", other["id"])[0].read_bytes(), self.raw)
        replacement = self.store.save([self.raw], owner="a", deduplicate=True)[0]
        self.assertNotEqual(replacement["id"], item["id"])

    def test_delete_failure_restores_files_and_index(self):
        item = self.store.save([self.raw], owner="a")[0]
        with patch.object(self.store, "_sync_dir", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.store.delete("a", item["id"])
        self.assertEqual(self.store.list("a")["total"], 1)
        self.assertEqual(self.store.file("a", item["id"])[0].read_bytes(), self.raw)
        self.assertFalse(any(p.name.startswith(".deleted-") for p in self.store.root.iterdir()))

    def test_invalid_image_does_not_create_files_or_index_rows(self):
        with self.assertRaises(ValueError):
            self.store.save([b"<svg>not a raster</svg>"], owner="a")
        self.assertEqual(self.store.list("a")["total"], 0)
        self.assertFalse(any(p.is_dir() for p in self.store.root.iterdir()))

    def test_partial_batch_failure_rolls_back_files_and_index(self):
        write = self.store._write
        calls = 0
        def fail_later(path, data):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise OSError("disk full")
            write(path, data)
        with patch.object(self.store, "_write", side_effect=fail_later):
            with self.assertRaises(OSError):
                self.store.save([self.raw, self.raw], owner="a")
        self.assertEqual(self.store.list("a")["total"], 0)
        self.assertFalse(any(p.is_dir() for p in self.store.root.iterdir()))

    def test_missing_mount_never_creates_a_fallback_directory(self):
        path = self.store.root / "missing-mount"
        store = ImageStore(path, required=True)
        with self.assertRaises(StorageUnavailable):
            store.ensure_writable()
        self.assertFalse(path.exists())
        path.mkdir()
        (path / ".image-lab-storage").write_text(MARKER)
        store.ensure_writable()
        (path / ".image-lab-storage").unlink()
        with self.assertRaises(StorageUnavailable):
            store.list("a")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ImageStore(Path(self.temp.name))
        for name, value in [("store", self.store), ("AUTH_ENABLED", True), ("ALLOWED_EMAILS", {"a@example.com", "b@example.com"})]:
            p = patch.object(module, name, value); p.start(); self.addCleanup(p.stop)
        self.client = TestClient(module.app, base_url="https://testserver")
        self.addCleanup(self.client.close)
        self.login("a@example.com")
        self.raw = picture()
        self.response = {"data": [{"b64_json": base64.b64encode(self.raw).decode()}]}

    def login(self, email):
        self.client.cookies.clear()
        cookie = TimestampSigner(module.SESSION_SECRET).sign(base64.b64encode(json.dumps({"user_email": email}).encode())).decode()
        self.client.cookies.set("session", cookie)

    def test_generate_persists_and_download_is_exact(self):
        with patch.object(module.client, "generate", return_value=self.response) as provider:
            response = self.client.post("/api/generate", json={"prompt": "<script>alert(1)</script> chat bleu", "n": 1})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json(); item = data["saved_images"][0]
        provider.assert_called_once()
        self.assertEqual(data["images"], [item["url"]])
        self.assertEqual(item["mode"], "generate")
        self.assertIn("<script>", item["prompt"])
        download = self.client.get(item["download_url"])
        self.assertEqual(download.content, self.raw)
        self.assertIn("attachment", download.headers["content-disposition"])
        self.assertEqual(download.headers["cache-control"], "private, no-store")
        self.assertEqual(self.client.get(item["thumbnail_url"]).headers["content-type"], "image/webp")
        self.login("b@example.com")
        self.assertEqual(self.client.get(item["url"]).status_code, 404)
        self.assertEqual(self.client.get("/api/gallery").json()["total"], 0)
        self.client.cookies.clear()
        self.assertEqual(self.client.get(item["url"]).status_code, 401)

    def test_edit_output_persists(self):
        with patch.object(module.client, "edit", return_value=self.response):
            response = self.client.post("/api/edit", data={"prompt": "fond bleu", "model": "gpt-image-1.5"}, files=[("images", ("input.png", self.raw, "image/png"))])
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["saved_images"][0]
        self.assertEqual(item["mode"], "edit")
        self.assertEqual(self.client.get(item["url"]).content, self.raw)

    def test_storage_failure_prevents_paid_generation(self):
        with patch.object(self.store, "ensure_writable", side_effect=StorageUnavailable("offline")), patch.object(module.client, "generate") as provider:
            response = self.client.post("/api/generate", json={"prompt": "chat"})
        self.assertEqual(response.status_code, 503)
        provider.assert_not_called()

    def test_storage_failure_after_generation_preserves_downloadable_result(self):
        with patch.object(self.store, "save_data_urls", side_effect=OSError("disk full")), patch.object(module.client, "generate", return_value=self.response):
            response = self.client.post("/api/generate", json={"prompt": "chat"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["storage_warning"])
        self.assertEqual(base64.b64decode(response.json()["images"][0].split(",")[1]), self.raw)

    def test_import_and_refresh_are_idempotent(self):
        ids = []
        for _ in range(2):
            response = self.client.post("/api/gallery/import", files={"image": ("archive.png", self.raw, "image/png")})
            self.assertEqual(response.status_code, 200, response.text)
            ids.append(response.json()["item"]["id"])
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(self.client.get("/api/gallery").json()["total"], 1)
        self.assertEqual(self.client.get("/api/gallery?limit=1000").status_code, 422)
        self.assertEqual(self.client.get("/api/gallery?before=bad").status_code, 400)

    def test_gallery_requires_authentication(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/gallery").status_code, 401)
        self.assertEqual(self.client.post("/api/gallery/import", files={"image": ("a.png", self.raw)}).status_code, 401)

    def test_delete_api_requires_session_origin_and_owner(self):
        item = self.store.save([self.raw], owner="a@example.com")[0]
        path = "/api/gallery/" + item["id"]
        self.assertEqual(self.client.delete(path).status_code, 403)
        token = self.client.get("/api/gallery/session").json()["token"]
        headers = {"X-Gallery-Token": token}
        self.assertEqual(self.client.delete(path, headers={**headers, "Origin": "https://untrusted.example"}).status_code, 403)
        self.login("b@example.com")
        other_token = self.client.get("/api/gallery/session").json()["token"]
        self.assertEqual(self.client.delete(path, headers={"X-Gallery-Token": other_token}).status_code, 404)
        self.assertTrue((self.store.root / item["id"]).exists())
        self.login("a@example.com")
        headers = {"X-Gallery-Token": self.client.get("/api/gallery/session").json()["token"], "Origin": "https://testserver"}
        response = self.client.delete(path, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"deleted": item["id"]})
        self.assertEqual(self.client.get("/api/gallery").json()["total"], 0)
        for variant in ["image", "thumbnail", "download"]:
            self.assertEqual(self.client.get(path + "/" + variant).status_code, 404)
        self.client.cookies.clear()
        self.assertEqual(self.client.delete(path, headers=headers).status_code, 401)
        self.assertEqual(self.client.get("/api/gallery/session").status_code, 401)


if __name__ == "__main__":
    unittest.main()
