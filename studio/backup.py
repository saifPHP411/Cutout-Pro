"""Create encrypted maintenance snapshots and verify recovery in fresh storage."""
import contextlib
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import threading
from pathlib import Path, PurePosixPath

import sqlalchemy as sa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import config

MAGIC = b"PeakCutBackup1\0"
LIMIT = 512 * 1024**2
LOCK = threading.Lock()
EXCLUDED = {"tmp", "models", "download-cache", "cache", ".work", ".recovery"}


def _key(passphrase, salt):
    if not 14 <= len(passphrase) <= 256:
        raise ValueError("Use a passphrase between 14 and 256 characters.")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def _crypt(source, output, passphrase, decrypt=False):
    with source.open("rb") as inp, output.open("xb") as out:
        if decrypt:
            header = inp.read(len(MAGIC) + 28)
            count = source.stat().st_size - len(header) - 16
            if not header.startswith(MAGIC) or len(header) != len(MAGIC)+28 or count < 0:
                raise ValueError("Invalid encrypted backup format.")
            inp.seek(-16, 2)
            tag = inp.read(16)
            inp.seek(len(header))
            cipher = Cipher(algorithms.AES(_key(passphrase, header[len(MAGIC):-12])), modes.GCM(header[-12:], tag)).decryptor()
        else:
            salt, nonce = os.urandom(16), os.urandom(12)
            header = MAGIC + salt + nonce
            cipher = Cipher(algorithms.AES(_key(passphrase, salt)), modes.GCM(nonce)).encryptor()
            count = source.stat().st_size
            out.write(header)
        cipher.authenticate_additional_data(header)
        while count:
            chunk = inp.read(min(1024**2, count))
            if not chunk:
                raise ValueError("Truncated backup.")
            out.write(cipher.update(chunk))
            count -= len(chunk)
        out.write(cipher.finalize())
        if not decrypt:
            out.write(cipher.tag)


def _database(target):
    url = sa.engine.make_url(config.DATABASE_URL)
    if url.get_backend_name() == "sqlite":
        dest = target / "db.sqlite"
        uri = Path(url.database).resolve().as_uri() + "?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True)) as source, contextlib.closing(sqlite3.connect(dest)) as restored:
            source.backup(restored)
        return
    if url.get_backend_name() != "postgresql" or not shutil.which("pg_dump"):
        raise RuntimeError("PostgreSQL client is unavailable. Use the host backup procedure.")
    env = {**os.environ, "PGHOST": url.host or "", "PGPORT": str(url.port or 5432),
           "PGUSER": url.username or "", "PGDATABASE": url.database or "", "PGPASSWORD": url.password or "",
           "PGCONNECT_TIMEOUT": "10"}
    proc = subprocess.run(["pg_dump", "-Fc", "--no-owner", "--no-acl", "-f", str(target / "db.dump")],
                          env=env, capture_output=True, timeout=180)
    if proc.returncode:
        raise RuntimeError("PostgreSQL dump failed. Check the local client and the database connection.")


def _members():
    db_url = sa.engine.make_url(config.DATABASE_URL)
    local_db = Path(db_url.database).resolve() if db_url.get_backend_name() == "sqlite" else None
    db_files = {local_db, Path(str(local_db) + "-wal"), Path(str(local_db) + "-shm")} if local_db else set()
    for root, folders, files in os.walk(config.DATA_DIR):
        base = Path(root)
        folders[:] = [f for f in folders if f not in EXCLUDED and not (base / f).is_symlink()]
        for name in files:
            path = base / name
            if path.is_symlink() or not path.is_file() or path.resolve() in db_files:
                continue
            yield path


def create(folder, passphrase):
    # Scrypt input is checked before any database reads.
    if not 14 <= len(passphrase) <= 256:
        raise ValueError("Use a passphrase between 14 and 256 characters.")
    files = list(_members())
    size = sum(p.stat().st_size for p in files)
    if size > LIMIT or shutil.disk_usage(folder).free < 2 * (size + 128 * 1024**2):
        raise ValueError("The snapshot exceeds the browser backup limit or available disk space. Use the host backup procedure.")
    _database(folder)
    clear, encrypted = folder / "workspace.tar", folder / "peakcut.pcb"
    with tarfile.open(clear, "w") as tar:
        for f in files:
            tar.add(f, arcname="data/" + f.relative_to(config.DATA_DIR).as_posix(), recursive=False)
        for f in folder.glob("db.*"):
            tar.add(f, arcname=f.name, recursive=False)
    if clear.stat().st_size > LIMIT:
        raise ValueError("The full snapshot exceeds the browser backup limit. Use the host backup procedure.")
    _crypt(clear, encrypted, passphrase)
    clear.unlink()
    return encrypted


def check(encrypted, folder, passphrase):
    """Authenticate first, then stage into private temporary files and verify the database."""
    from cryptography.exceptions import InvalidTag

    clear = folder / "workspace.tar"
    try:
        _crypt(encrypted, clear, passphrase, decrypt=True)
    except InvalidTag as exc:
        raise ValueError("Incorrect passphrase or altered backup.") from exc
    staged = folder / "restore"
    staged.mkdir(mode=0o700)
    seen, total = set(), 0
    with tarfile.open(clear, "r:") as tar:
        for member in tar:
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or ".." in path.parts or "\\" in member.name
                    or path in seen or not path.parts or (path.parts[0] != "data" and path.as_posix() not in {"db.sqlite", "db.dump"})):
                raise ValueError("The archive contains an unsafe or repeated path.")
            total += member.size
            if total > LIMIT or len(seen) >= 100000:
                raise ValueError("The archive exceeds the recovery size limit.")
            seen.add(path)
            dest = staged.joinpath(*path.parts)
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with dest.open("xb") as out, tar.extractfile(member) as inp:
                shutil.copyfileobj(inp, out)
            dest.chmod(0o600)
    databases = [p for p in ("db.sqlite", "db.dump") if (staged / p).is_file()]
    if len(databases) != 1:
        raise ValueError("A backup must contain exactly one database.")
    if databases[0] == "db.sqlite":
        with contextlib.closing(sqlite3.connect((staged / "db.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("SQLite integrity check failed.")
            return {"files": len(seen), "database": "sqlite", "verification": "integrity_check"}
    if not shutil.which("pg_restore"):
        raise ValueError("PostgreSQL restore client unavailable. Verify on the isolated host recovery stack.")
    result = subprocess.run(["pg_restore", "--list", str(staged / "db.dump")], capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError("Invalid PostgreSQL backup catalog.")
    return {"files": len(seen), "database": "postgresql", "verification": "archive_catalog"}


def recover_file(encrypted, destination, passphrase):
    """Host recovery prepares an empty directory; importing the database stays an explicit step."""
    destination = destination.resolve()
    if destination.exists() or destination.is_relative_to(config.DATA_DIR.resolve()):
        raise ValueError("Use a new directory outside the running app data.")
    destination.mkdir(mode=0o700)
    try:
        result = check(encrypted, destination, passphrase)
        (destination / "workspace.tar").unlink()
        return result
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def temp():
    return Path(tempfile.mkdtemp(prefix="peakcut-backup-", dir=config.TMP_DIR))
