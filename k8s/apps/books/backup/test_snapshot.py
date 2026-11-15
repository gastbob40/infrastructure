import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import snapshot
from verify import verify


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.roots = {name: self.root / name for name in ("config", "library", "ingest")}
        for root in self.roots.values():
            root.mkdir()
        app = sqlite3.connect(self.roots["config"] / "app.db")
        self.addCleanup(app.close)
        app.execute("PRAGMA journal_mode=WAL")
        app.execute("CREATE TABLE thumbnail (filename TEXT)")
        app.execute("INSERT INTO thumbnail VALUES ('old.jpg')")
        app.execute("CREATE TABLE settings (value TEXT)")
        app.execute("INSERT INTO settings VALUES ('committed-in-wal')")
        app.commit()
        with sqlite3.connect(self.roots["library"] / "metadata.db") as library:
            library.executescript("""
                CREATE TABLE books (id INTEGER, path TEXT, has_cover INTEGER);
                INSERT INTO books VALUES (1, 'Author/Book (1)', 1);
                CREATE TABLE data (book INTEGER, name TEXT, format TEXT);
                INSERT INTO data VALUES (1, 'Book - Author', 'EPUB');
            """)
        directory = self.roots["library"] / "Author/Book (1)"
        directory.mkdir(parents=True)
        (directory / "cover.jpg").write_bytes(b"cover fixture")
        (directory / "Book - Author.epub").write_bytes(b"epub fixture")
        for excluded in ("backup-before-test", "thumbnails", ".cache"):
            directory = self.roots["config"] / excluded
            directory.mkdir()
            (directory / "excluded").write_text("excluded")

    def test_wal_restore_and_exclusions(self):
        archive = self.root / "snapshot.tar.gz"
        self.assertEqual(snapshot.create_archive(self.roots, archive)["books"], 1)
        with tarfile.open(archive) as source:
            manifest = json.load(source.extractfile("manifest.json"))
            self.assertEqual(len(manifest["databases"]), 2)
            self.assertFalse(any("excluded" in name for name in source.getnames()))
            restored = self.root / "restored-app.db"
            restored.write_bytes(source.extractfile("config/app.db").read())
        with sqlite3.connect(restored) as app:
            self.assertEqual(app.execute("SELECT value FROM settings").fetchone()[0], "committed-in-wal")
            self.assertEqual(app.execute("SELECT count(*) FROM thumbnail").fetchone()[0], 0)

    def test_concurrent_changes_are_rejected(self):
        output = self.root / "staging"
        output.mkdir()
        original = snapshot.shutil.copy2

        def changing_copy(source, target):
            original(source, target)
            with sqlite3.connect(self.roots["config"] / "app.db") as writer:
                writer.execute("INSERT INTO settings VALUES ('concurrent change')")

        with patch.object(snapshot.shutil, "copy2", side_effect=changing_copy):
            with self.assertRaisesRegex(RuntimeError, "Source changed"):
                snapshot.copy_snapshot(self.roots, output)

    def test_missing_book_format_is_rejected(self):
        (self.roots["library"] / "Author/Book (1)/Book - Author.epub").unlink()
        with self.assertRaisesRegex(RuntimeError, "Missing format"):
            snapshot.create_archive(self.roots, self.root / "invalid.tar.gz")

    def test_corrupted_content_is_rejected(self):
        good, bad = self.root / "good.tar.gz", self.root / "bad.tar.gz"
        snapshot.create_archive(self.roots, good)
        import io
        with tarfile.open(good) as source, tarfile.open(bad, "w:gz") as output:
            for member in source.getmembers():
                data = source.extractfile(member).read()
                if member.name.endswith("cover.jpg"):
                    data = b"X" * len(data)
                output.addfile(member, io.BytesIO(data))
        with self.assertRaisesRegex(RuntimeError, "Checksum mismatch"):
            verify(bad)


if __name__ == "__main__":
    unittest.main()
