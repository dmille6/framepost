"""Token encryption helpers (brief: Security & Operational → Token encryption).

TOKEN_ENCRYPTION_KEY is a comma-separated list of URL-safe base64 32-byte Fernet keys.
First key encrypts; all keys decrypt — supports rotation via MultiFernet.
Generate keys with: docker compose exec backend python -m admin generate-encryption-key
"""
from cryptography.fernet import Fernet, MultiFernet

from config import settings


def _build_fernet() -> MultiFernet:
    raw = settings.token_encryption_key.strip()
    if not raw:
        raise RuntimeError(
            "TOKEN_ENCRYPTION_KEY is not set. Generate one with: "
            "docker compose exec backend python -m admin generate-encryption-key"
        )
    keys = [Fernet(k.strip().encode()) for k in raw.split(",") if k.strip()]
    if not keys:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY parsed to zero keys.")
    return MultiFernet(keys)


def encrypt_token(plaintext: str) -> str:
    return _build_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _build_fernet().decrypt(ciphertext.encode()).decode()
