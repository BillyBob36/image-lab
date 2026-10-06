"""Private image library: original bytes + thumbnails + transactional SQLite index."""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import logging
from io import BytesIO
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 50 * 1024 * 1024
MARKER = "image-lab-storage-v1"
FORMATS = {"PNG": ("png", "image/png"), "JPEG": ("jpeg", "image/jpeg"), "WEBP": ("webp", "image/webp")}


class StorageUnavailable(Exception):
    pass


class ImageStore:
    def __init__(self, root: Path, required=False):
        self.root = Path(root)
        self.required = required
        self.lock = threading.RLock()

    def _check_root(self):
        # Never create a replacement directory when the production mount is absent.
        if self.required:
            try:
                if (self.root / ".image-lab-storage").read_text().strip() != MARKER:
                    raise OSError("invalid storage marker")
            except OSError as exc:
                raise StorageUnavailable("Le stockage des images est indisponible. Réessayez plus tard.") from exc
        else:
            self.root.mkdir(parents=True, exist_ok=True)

    def _connect(self):
        self._check_root()
        db = sqlite3.connect(self.root / "gallery.sqlite3", timeout=20)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS images (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, created_at TEXT NOT NULL,
                prompt TEXT NOT NULL, revised_prompt TEXT NOT NULL, model TEXT NOT NULL,
                mode TEXT NOT NULL, quality TEXT NOT NULL, format TEXT NOT NULL,
                width INTEGER NOT NULL, height INTEGER NOT NULL, bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL, original_name TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS images_owner_date ON images(owner, created_at DESC, id DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS images_owner_hash ON images(owner, sha256)")
            db.commit()
            return db
        except Exception:
            db.close()
            raise

    def ensure_writable(self, count=1):
        try:
            with self.lock, closing(self._connect()) as db:
                if shutil.disk_usage(self.root).free < (256 + count * 64) * 1024 * 1024:
                    raise StorageUnavailable("Espace insuffisant pour enregistrer les images. Libérez de l'espace avant de générer.")
                db.execute("BEGIN IMMEDIATE")
                db.rollback()
                with tempfile.TemporaryFile(dir=self.root) as probe:
                    probe.write(b"storage-check")
                    probe.flush()
                    os.fsync(probe.fileno())
        except (OSError, sqlite3.Error) as exc:
            raise StorageUnavailable("Impossible d'enregistrer les images pour le moment. Réessayez plus tard.") from exc

    @staticmethod
    def _prepare(raw):
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise ValueError("Image vide ou supérieure à 50 Mo.")
        try:
            with Image.open(BytesIO(raw)) as image:
                if image.format not in FORMATS or image.width * image.height > 40_000_000:
                    raise ValueError("Utilisez une image PNG, JPEG ou WebP de 40 mégapixels maximum.")
                fmt, mime = FORMATS[image.format]
                width, height = image.size
                image.load()
                thumb = ImageOps.exif_transpose(image).convert("RGBA")
                thumb.thumbnail((640, 640), Image.Resampling.LANCZOS)
                buffer = BytesIO()
                thumb.save(buffer, "WEBP", quality=82)
            return fmt, width, height, buffer.getvalue()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("Fichier image invalide.") from exc

    @staticmethod
    def _write(path, data):
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _sync_dir(path):
        if os.name != "nt":
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    @staticmethod
    def public(row):
        item = dict(row)
        item.pop("owner", None)
        prefix = "/api/gallery/" + item["id"]
        item.update(url=prefix + "/image", thumbnail_url=prefix + "/thumbnail", download_url=prefix + "/download")
        return item

    def save(self, raws, *, owner, prompt="", revised_prompt="", model="", mode="import",
             quality="", original_name="", deduplicate=False):
        self.ensure_writable(len(raws))
        prepared = [(raw, self._prepare(raw)) for raw in raws]
        made = []
        result = []
        with self.lock, closing(self._connect()) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                for raw, (fmt, width, height, thumbnail) in prepared:
                    sha = hashlib.sha256(raw).hexdigest()
                    if deduplicate:
                        existing = db.execute("SELECT * FROM images WHERE owner=? AND sha256=?", (owner, sha)).fetchone()
                        if existing:
                            result.append(self.public(existing))
                            continue
                    image_id = uuid.uuid4().hex
                    folder = self.root / image_id
                    folder.mkdir()
                    made.append(folder)
                    self._write(folder / ("original." + fmt), raw)
                    self._write(folder / "thumbnail.webp", thumbnail)
                    self._sync_dir(folder)
                    item = dict(id=image_id, owner=owner, created_at=datetime.now(timezone.utc).isoformat(),
                                prompt=prompt, revised_prompt=revised_prompt or "", model=model, mode=mode,
                                quality=quality, format=fmt, width=width, height=height, bytes=len(raw),
                                sha256=sha, original_name=original_name[:200])
                    db.execute("INSERT INTO images VALUES (" + ",".join("?" for _ in item) + ")", tuple(item.values()))
                    result.append(self.public(item))
                self._sync_dir(self.root)
                db.commit()
                return result
            except Exception:
                db.rollback()
                # Only newly-created UUID folders from this uncommitted batch.
                for folder in made:
                    shutil.rmtree(folder)
                raise

    def save_data_urls(self, urls, **metadata):
        raws = [base64.b64decode(url.split(",", 1)[1], validate=True) for url in urls]
        return self.save(raws, **metadata)

    def list(self, owner, *, query="", before="", limit=24):
        where = "owner=?"
        values = [owner]
        if query:
            where += " AND (prompt LIKE ? ESCAPE '\\' OR original_name LIKE ? ESCAPE '\\' OR model LIKE ? ESCAPE '\\')"
            term = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            values.extend([term] * 3)
        with closing(self._connect()) as db:
            total = db.execute("SELECT COUNT(*) FROM images WHERE " + where, values).fetchone()[0]
            if before:
                try:
                    date, image_id = before.split("|", 1)
                    datetime.fromisoformat(date)
                    if not re.fullmatch(r"[a-f0-9]{32}", image_id):
                        raise ValueError()
                except ValueError:
                    raise ValueError("Pagination invalide.")
                where += " AND (created_at, id) < (?, ?)"
                values.extend([date, image_id])
            rows = db.execute("SELECT * FROM images WHERE " + where + " ORDER BY created_at DESC, id DESC LIMIT ?", [*values, limit + 1]).fetchall()
            more = len(rows) > limit
            items = [self.public(r) for r in rows[:limit]]
            cursor = items[-1]["created_at"] + "|" + items[-1]["id"] if more else None
            return dict(items=items, total=total, next_cursor=cursor)

    def get(self, owner, image_id):
        if not re.fullmatch(r"[a-f0-9]{32}", image_id):
            return None
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM images WHERE owner=? AND id=?", (owner, image_id)).fetchone()
            return self.public(row) if row else None

    def delete(self, owner, image_id):
        if not re.fullmatch(r"[a-f0-9]{32}", image_id):
            return False
        with self.lock, closing(self._connect()) as db:
            folder = self.root / image_id
            retired = None
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT id FROM images WHERE owner=? AND id=?", (owner, image_id)).fetchone()
                if not row:
                    db.rollback()
                    return False
                # Only retire this owned UUID folder, within the actual storage root.
                if folder.is_symlink() or folder.resolve().parent != self.root.resolve():
                    raise OSError("invalid image folder")
                if folder.exists():
                    retired = self.root / (".deleted-" + uuid.uuid4().hex)
                    folder.rename(retired)
                db.execute("DELETE FROM images WHERE owner=? AND id=?", (owner, image_id))
                self._sync_dir(self.root)
                db.commit()
            except Exception:
                db.rollback()
                if retired is not None and retired.exists():
                    retired.rename(folder)
                raise
            if retired is not None:
                try:
                    shutil.rmtree(retired)
                    self._sync_dir(self.root)
                except OSError:
                    # The committed deletion stays inaccessible even if disk cleanup fails.
                    logging.getLogger(__name__).exception("Could not clean a retired image folder")
            return True

    def file(self, owner, image_id, thumbnail=False):
        item = self.get(owner, image_id)
        if not item:
            return None
        name = "thumbnail.webp" if thumbnail else "original." + item["format"]
        path = self.root / image_id / name
        if not path.is_file():
            return None
        return path, item
