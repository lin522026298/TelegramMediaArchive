import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from telethon.tl.types import Photo, PhotoSize, PhotoSizeProgressive, PhotoSizeEmpty, PhotoCachedSize

import tg_media_archive as archive


class PhotoVersionTests(unittest.TestCase):
    def photo(self, sizes=None, photo_id=12):
        return Photo(id=photo_id, access_hash=34, file_reference=b"reference", date=datetime.now(timezone.utc),
                     sizes=sizes or [PhotoSize("large", 20, 20, 12), PhotoSize("last", 10, 10, 8)], dc_id=2)

    def record(self):
        return archive.MediaRecord(1, 2, 0, datetime(2026, 1, 1, tzinfo=timezone.utc), "photo", "test.jpg", 12)

    def test_selection_does_not_assume_last_size_is_largest(self):
        variant = archive.select_photo_variant(self.photo())
        self.assertEqual((variant.thumb_type, variant.size), ("large", 12))

    def test_progressive_uses_maximum_size_not_last_progression(self):
        photo = self.photo([PhotoSizeProgressive("p", 20, 20, [4, 20, 8]), PhotoSize("x", 10, 10, 12)])
        self.assertEqual(archive.select_photo_variant(photo).size, 20)

    def test_index_uses_same_explicit_variant(self):
        photo = self.photo()
        message = SimpleNamespace(id=2, date=self.record().date_utc, photo=photo, media=object(),
                                  file=SimpleNamespace(size=999, ext=".jpg", name=None, mime_type="image/jpeg"))
        self.assertEqual(archive.message_media_record(1, message).size, 12)

    def test_invalid_photo_does_not_fall_back_to_unrelated_byte_count(self):
        with self.assertRaises(ValueError):
            archive.select_photo_variant(self.photo([PhotoSizeEmpty("empty")]))

    def test_cached_photo_has_exact_local_size(self):
        photo = self.photo([PhotoCachedSize("c", 2, 2, b"cached")])
        variant = archive.select_photo_variant(photo)
        self.assertEqual((variant.size, variant.cached_data), (6, b"cached"))

    def test_unknown_part_and_final_are_preserved_once_and_identity_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = self.record()
            final = root / archive.media_relative_path(record, ZoneInfo("UTC"))
            final.parent.mkdir(parents=True)
            part = final.with_name(final.name + ".part")
            final.write_bytes(b"old-final")
            part.write_bytes(b"old-part")
            db = archive.ArchiveDB(root / "state" / "archive.sqlite3")
            db.upsert_media(record)
            variant = archive.select_photo_variant(self.photo())
            archive.prepare_photo_source(root, db, record, variant, final, part)
            directories = list((root / "state" / "photo-quarantine").iterdir())
            self.assertEqual(len(directories), 1)
            self.assertEqual((directories[0] / "0").read_bytes(), b"old-final")
            self.assertEqual((directories[0] / "1").read_bytes(), b"old-part")
            self.assertEqual(json.loads((directories[0] / "provenance.json").read_text())["reason"], "unknown-or-changed-photo-source")
            part.write_bytes(b"new-part")
            db.close()
            db = archive.ArchiveDB(root / "state" / "archive.sqlite3")
            archive.prepare_photo_source(root, db, record, variant, final, part)
            self.assertEqual(part.read_bytes(), b"new-part")
            self.assertEqual(len(list((root / "state" / "photo-quarantine").iterdir())), 1)
            archive.prepare_photo_source(root, db, record, archive.select_photo_variant(self.photo(photo_id=13)), final, part)
            self.assertFalse(part.exists())
            self.assertEqual(len(list((root / "state" / "photo-quarantine").iterdir())), 2)
            db.close()

    def test_explicit_location_dc_size_and_same_version_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = self.record()
            db = archive.ArchiveDB(root / "state" / "archive.sqlite3")
            db.upsert_media(record)
            final = root / archive.media_relative_path(record, ZoneInfo("UTC"))
            final.parent.mkdir(parents=True)
            part = final.with_name(final.name + ".part")
            photo = self.photo()
            archive.prepare_photo_source(root, db, record, archive.select_photo_variant(photo), final, part)
            part.write_bytes(b"12345678")
            calls = []
            async def chunks():
                yield b"9012"
            def download(location, **kwargs):
                calls.append((location, kwargs))
                return chunks()
            client = SimpleNamespace(get_messages=AsyncMock(return_value=SimpleNamespace(photo=photo, media=object())), iter_download=download)
            self.assertTrue(asyncio.run(archive.download_one(client, None, root, ZoneInfo("UTC"), db, record, 4)))
            location, options = calls[0]
            self.assertEqual(location.thumb_size, "large")
            self.assertEqual(options["dc_id"], 2)
            self.assertEqual(options["file_size"], 12)
            self.assertEqual(options["offset"], 8)
            self.assertEqual(final.read_bytes(), b"123456789012")
            db.close()

    def test_explicit_photo_still_rejects_short_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = self.record()
            db = archive.ArchiveDB(root / "state" / "archive.sqlite3")
            db.upsert_media(record)
            async def chunks():
                yield b"short"
            client = SimpleNamespace(get_messages=AsyncMock(return_value=SimpleNamespace(photo=self.photo(), media=object())),
                                     iter_download=lambda *args, **kwargs: chunks())
            self.assertFalse(asyncio.run(archive.download_one(client, None, root, ZoneInfo("UTC"), db, record, 4)))
            self.assertEqual(db.count_by_status(), {"error": 1})
            db.close()


if __name__ == "__main__":
    unittest.main()
