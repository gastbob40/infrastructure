"""Verify all archive contents and reopen its SQLite databases in isolation."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
import sys
import tarfile
import tempfile


def check_integrity(connection):
    try:
        result = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.OperationalError as error:
        if "no such tokenizer: calibre" not in str(error):
            raise
        # Calibre's notes/full-text databases require its own FTS5 tokenizer.
        # Run with calibre-debug, which supplies the matching native libraries.
        from calibre_extensions import sqlite_extension
        connection.enable_load_extension(True)
        try:
            connection.load_extension(sqlite_extension.__file__)
        finally:
            connection.enable_load_extension(False)
        result = connection.execute("PRAGMA integrity_check").fetchall()
    return result == [("ok",)]


def verify(path):
    with tempfile.TemporaryDirectory() as temporary, tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)) or any(
            not member.isfile() or PurePosixPath(member.name).is_absolute()
            or ".." in PurePosixPath(member.name).parts for member in members
        ):
            raise RuntimeError("Unsafe or duplicate archive member")
        manifest = json.load(archive.extractfile("manifest.json"))
        if set(names) != set(manifest["files"]) | {"manifest.json"}:
            raise RuntimeError("Archive inventory mismatch")
        for name, expected in manifest["files"].items():
            if archive.getmember(name).size != expected["size"]:
                raise RuntimeError(f"Size mismatch: {name}")
            with archive.extractfile(name) as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected["sha256"]:
                raise RuntimeError(f"Checksum mismatch: {name}")
        for number, name in enumerate(manifest["databases"]):
            destination = Path(temporary) / f"database-{number}.db"
            with archive.extractfile(name) as source, destination.open("wb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
            with sqlite3.connect(destination) as db:
                if not check_integrity(db):
                    raise RuntimeError(f"SQLite integrity failure: {name}")
                if name == "library/metadata.db":
                    books = db.execute("SELECT id, path, has_cover FROM books").fetchall()
                    if len(books) != manifest["book_count"]:
                        raise RuntimeError("Book count mismatch")
                    for book_id, directory, cover in books:
                        if cover and f"library/{directory}/cover.jpg" not in manifest["files"]:
                            raise RuntimeError(f"Missing cover for book {book_id}")
                        for filename, extension in db.execute(
                            "SELECT name, format FROM data WHERE book=?", (book_id,)
                        ):
                            if f"library/{directory}/{filename}.{extension.lower()}" not in manifest["files"]:
                                raise RuntimeError(f"Missing format for book {book_id}")
        return {"verified": True, "files": len(manifest["files"]),
                "databases": len(manifest["databases"]), "books": manifest["book_count"]}


if __name__ == "__main__":
    print(json.dumps(verify(Path(sys.argv[1]))))
