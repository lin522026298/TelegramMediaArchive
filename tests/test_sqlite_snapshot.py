import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from sqlite_snapshot import create_daily_snapshot, snapshot_name, verify_sqlite_snapshot


class SqliteSnapshotTests(unittest.TestCase):
    def test_snapshot_uses_sqlite_backup_and_creates_verified_mirror(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "live.sqlite3"
            primary_dir = root / "snapshots"
            mirror_dir = root / "mirror"

            with closing(sqlite3.connect(source_path)) as live:
                live.execute("pragma journal_mode = wal")
                live.execute("create table media (id integer primary key, name text not null)")
                live.execute("insert into media(name) values ('first'), ('second')")
                live.commit()

                result = create_daily_snapshot(
                    source_path,
                    primary_dir,
                    mirror_dir=mirror_dir,
                    day=date(2026, 7, 31),
                )

            self.assertTrue(result.primary_created)
            self.assertTrue(result.mirror_created)
            self.assertEqual(result.primary.name, "archive-2026-07-31.sqlite3")
            self.assertEqual(result.mirror, mirror_dir / "archive-2026-07-31.sqlite3")
            verify_sqlite_snapshot(result.primary)
            verify_sqlite_snapshot(result.mirror)
            with closing(sqlite3.connect(result.primary)) as snapshot:
                count = snapshot.execute("select count(*) from media").fetchone()[0]
            self.assertEqual(count, 2)

    def test_existing_daily_snapshot_is_not_rewritten_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "live.sqlite3"
            with closing(sqlite3.connect(source_path)) as live:
                live.execute("create table values_table (value integer)")
                live.execute("insert into values_table values (1)")
                live.commit()

            first = create_daily_snapshot(source_path, root / "snapshots", day=date(2026, 7, 31))
            original_bytes = first.primary.read_bytes()

            with closing(sqlite3.connect(source_path)) as live:
                live.execute("insert into values_table values (2)")
                live.commit()

            skipped = create_daily_snapshot(source_path, root / "snapshots", day=date(2026, 7, 31))
            self.assertFalse(skipped.primary_created)
            self.assertEqual(skipped.primary.read_bytes(), original_bytes)

            replaced = create_daily_snapshot(
                source_path,
                root / "snapshots",
                day=date(2026, 7, 31),
                force=True,
            )
            self.assertTrue(replaced.primary_created)
            with closing(sqlite3.connect(replaced.primary)) as snapshot:
                count = snapshot.execute("select count(*) from values_table").fetchone()[0]
            self.assertEqual(count, 2)

    def test_snapshot_name_uses_requested_day(self):
        self.assertEqual(snapshot_name(date(2026, 1, 2)), "archive-2026-01-02.sqlite3")

    def test_missing_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                create_daily_snapshot(root / "missing.sqlite3", root / "snapshots")


if __name__ == "__main__":
    unittest.main()
