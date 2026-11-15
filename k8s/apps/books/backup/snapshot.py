"""Create a verified, consistent Calibre snapshot without modifying its volumes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
from verify import check_integrity, verify


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(roots):
    files, databases = {}, {}
    for label, root in roots.items():
        if not root.is_dir():
            raise RuntimeError(f"Missing source volume: {root}")

        def fail(error):
            raise error

        for directory, dirs, names in os.walk(root, onerror=fail):
            dirs[:] = sorted(d for d in dirs if not (
                label == "config" and (d.startswith("backup-before-") or d in {"thumbnails", ".cache"})
            ))
            if any((Path(directory) / name).is_symlink() for name in dirs):
                raise RuntimeError(f"Unsupported directory symlink: {directory}")
            for name in sorted(names):
                if name.endswith(("-wal", "-shm", "-journal")) or (label == "config" and ".bak" in name):
                    continue
                source = Path(directory) / name
                if source.is_symlink() or not source.is_file():
                    raise RuntimeError(f"Unsupported source entry: {source}")
                key = str(Path(label) / source.relative_to(root))
                with source.open("rb") as stream:
                    is_db = stream.read(16) == b"SQLite format 3\x00"
                if is_db:
                    databases[key] = source
                else:
                    stat = source.stat()
                    files[key] = (source, stat.st_size, stat.st_mtime_ns, stat.st_ino)
    return files, databases


def copy_snapshot(roots, target):
    files, databases = inventory(roots)
    connections = []
    try:
        for key, path in databases.items():
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
            version = connection.execute("PRAGMA data_version").fetchone()[0]
            connections.append((connection, version))
            destination = target / key
            destination.parent.mkdir(parents=True, exist_ok=True)
            deadline = time.monotonic() + 120

            def progress(*_):
                if time.monotonic() > deadline:
                    raise RuntimeError("SQLite backup timed out")

            with sqlite3.connect(destination) as output:
                connection.backup(output, pages=256, progress=progress)
                output.execute("PRAGMA journal_mode=DELETE")
                if not check_integrity(output):
                    raise RuntimeError(f"SQLite integrity failure: {key}")
        for key, (source, *_) in files.items():
            destination = target / key
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        if inventory(roots) != (files, databases) or any(
            db.execute("PRAGMA data_version").fetchone()[0] != version
            for db, version in connections
        ):
            raise RuntimeError("Source changed during snapshot; retry required")
    finally:
        for db, _ in connections:
            db.close()
    # Thumbnail files are rebuildable; remove their stale references in the copy.
    with sqlite3.connect(target / "config/app.db") as app:
        if app.execute("SELECT 1 FROM sqlite_master WHERE name='thumbnail'").fetchone():
            app.execute("DELETE FROM thumbnail")
    with sqlite3.connect(target / "library/metadata.db") as library:
        count = library.execute("SELECT count(*) FROM books").fetchone()[0]
    entries = {
        str(path.relative_to(target)): {"size": path.stat().st_size, "sha256": digest(path)}
        for path in sorted(target.rglob("*")) if path.is_file()
    }
    (target / "manifest.json").write_text(json.dumps({
        "format": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "book_count": count, "databases": sorted(databases), "files": entries,
    }, ensure_ascii=False, indent=2))


def create_archive(roots, output):
    for attempt in range(3):
        with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
            target = Path(temporary)
            try:
                copy_snapshot(roots, target)
            except (RuntimeError, FileNotFoundError, sqlite3.OperationalError) as error:
                if attempt == 2:
                    raise
                print(f"Retrying snapshot ({attempt + 1}/3): {error}", file=sys.stderr, flush=True)
                time.sleep(5)
                continue
            with tarfile.open(output, "w:gz", compresslevel=1) as archive:
                for path in sorted(target.rglob("*")):
                    if path.is_file():
                        archive.add(path, arcname=str(path.relative_to(target)), recursive=False)
        return verify(output)


if __name__ == "__main__":
    output = Path("/backup/calibre.tar.gz")
    result = create_archive({
        "config": Path("/source/config"), "library": Path("/source/library"),
        "ingest": Path("/source/ingest"),
    }, output)
    checksum = digest(output)
    output.with_suffix(output.suffix + ".sha256").write_text(checksum + "\n")
    print(json.dumps({**result, "archive_bytes": output.stat().st_size, "sha256": checksum}), flush=True)
