import base64
import io
import os
import zipfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from argus.config import settings

_SALT_LEN = 16
_KDF_ITERATIONS = 480_000  # OWASP's current PBKDF2-SHA256 minimum recommendation


class WrongPassphraseOrCorruptBackup(Exception):
    pass


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=_KDF_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _backup_sources() -> list[Path]:
    """Everything worth backing up, all under data_dir -- deliberately
    never includes .env (lives outside data_dir anyway, but worth being
    explicit): a memory backup is meant to be portable/storable, and that
    file holds live API keys that shouldn't travel with it."""
    sources = []
    db = settings.data_dir / "argus.db"
    if db.exists():
        sources.append(db)
    chroma = settings.data_dir / "chroma"
    if chroma.exists():
        sources.append(chroma)
    if settings.workspace_dir.exists():
        sources.append(settings.workspace_dir)
    return sources


def create_backup(dest_path: Path, passphrase: str) -> dict:
    """Zips the sqlite db + Chroma vector store + sandboxed workspace and
    encrypts the archive with a passphrase-derived key (PBKDF2-SHA256 ->
    Fernet, which is AES-128-CBC + HMAC-SHA256 authentication -- tamper-
    evident, not just confidential). The passphrase itself is never
    written anywhere; only the caller (a human, via getpass) ever holds it."""
    buf = io.BytesIO()
    entry_count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for source in _backup_sources():
            if source.is_file():
                zf.write(source, arcname=str(source.relative_to(settings.data_dir)))
                entry_count += 1
            else:
                for path in source.rglob("*"):
                    if path.is_file():
                        zf.write(path, arcname=str(path.relative_to(settings.data_dir)))
                        entry_count += 1

    salt = os.urandom(_SALT_LEN)
    key = _derive_key(passphrase, salt)
    encrypted = Fernet(key).encrypt(buf.getvalue())

    dest_path = Path(dest_path)
    dest_path.write_bytes(salt + encrypted)
    return {"path": dest_path, "entries": entry_count, "size_bytes": dest_path.stat().st_size}


def restore_backup(src_path: Path, passphrase: str) -> dict:
    """Decrypts and extracts a backup back into data_dir, overwriting
    whatever's already there at each path. Caller is responsible for
    confirming this with the user first -- this function itself doesn't
    ask, so it stays easily testable against a throwaway data_dir."""
    raw = Path(src_path).read_bytes()
    salt, encrypted = raw[:_SALT_LEN], raw[_SALT_LEN:]
    key = _derive_key(passphrase, salt)
    try:
        decrypted = Fernet(key).decrypt(encrypted)
    except InvalidToken:
        raise WrongPassphraseOrCorruptBackup("Wrong passphrase, or the backup file is corrupted.")

    with zipfile.ZipFile(io.BytesIO(decrypted)) as zf:
        zf.extractall(settings.data_dir)
        entry_count = len(zf.namelist())
    return {"entries": entry_count}
