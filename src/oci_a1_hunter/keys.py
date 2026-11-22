"""OCI API signing keys, SSH keys, and parsing the console's config snippet."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

OCI_DIR = Path.home() / ".oci"
API_KEY_PATH = OCI_DIR / "a1_hunter_api_key.pem"
SSH_KEY_PATH = Path.home() / ".ssh" / "oci_a1_hunter"


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def _fingerprint(public_key) -> str:
    der = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return ":".join(f"{b:02x}" for b in hashlib.md5(der).digest())  # noqa: S324 - OCI's format


def load_api_key(path: Path) -> tuple[str, str]:
    """Return (public key PEM, fingerprint) for an existing private key file."""
    key = serialization.load_pem_private_key(path.expanduser().read_bytes(), password=None)
    public = key.public_key()
    pem = public.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return pem, _fingerprint(public)


def ensure_api_key(path: Path = API_KEY_PATH) -> tuple[Path, str, str]:
    """Create an RSA API key unless one already exists. Returns (path, public PEM, fingerprint)."""
    if not path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        _write_private(
            path,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
    pem, fp = load_api_key(path)
    path.with_name(path.stem + "_public.pem").write_text(pem)
    return path, pem, fp


def ensure_ssh_key(path: Path = SSH_KEY_PATH) -> Path:
    """Create an ed25519 SSH key pair unless it exists. Returns the public key path."""
    pub = path.with_suffix(".pub")
    if not path.exists():
        key = ed25519.Ed25519PrivateKey.generate()
        _write_private(
            path,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.OpenSSH,
                serialization.NoEncryption(),
            ),
        )
        public = key.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        pub.write_text(public.decode() + " a1-hunter\n")
    return pub


def default_ssh_public_key() -> Path | None:
    for name in ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub", "oci_a1_hunter.pub"):
        p = Path.home() / ".ssh" / name
        if p.exists():
            return p
    return None


SNIPPET_KEYS = ("user", "fingerprint", "tenancy", "region", "key_file")


def parse_console_snippet(text: str) -> dict[str, str]:
    """Pick user/fingerprint/tenancy/region out of the console's 'Configuration file preview'."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"\s*(\w+)\s*=\s*(\S+)", line)
        if m and m.group(1).lower() in SNIPPET_KEYS:
            found[m.group(1).lower()] = m.group(2)
    return found
