import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@dataclass
class SigningKey:
    kid: str
    private_pem: bytes
    public_jwk: dict
    alg: str = "RS256"


def _derive_fernet(passphrase: str) -> Fernet:
    raw = hashlib.sha256(passphrase.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _rsa_pub_to_jwk(public_key, kid: str) -> dict:
    pub_numbers = public_key.public_numbers()
    def b64url_uint(val: int) -> str:
        length = (val.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(val.to_bytes(length, "big")).rstrip(b"=").decode()

    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": b64url_uint(pub_numbers.n),
        "e": b64url_uint(pub_numbers.e),
    }


class KMSStore:
    def __init__(self, passphrase: str, keys_dir: str):
        self._fernet = _derive_fernet(passphrase)
        self._keys_dir = Path(keys_dir) / "idp_sign"
        self._keys_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self._keys_dir / "meta.json"

    def _load_meta(self) -> dict:
        if self._meta_path.exists():
            return json.loads(self._meta_path.read_text())
        return {"active": None, "previous": None}

    def _save_meta(self, meta: dict) -> None:
        self._meta_path.write_text(json.dumps(meta, indent=2))

    def generate_key(self, kid: str) -> SigningKey:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        encrypted = self._fernet.encrypt(private_pem)
        (self._keys_dir / f"{kid}.enc").write_bytes(encrypted)

        pub_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        (self._keys_dir / f"{kid}.pub.pem").write_bytes(pub_pem)

        public_jwk = _rsa_pub_to_jwk(private_key.public_key(), kid)
        return SigningKey(kid=kid, private_pem=private_pem, public_jwk=public_jwk)

    def load_key(self, kid: str) -> Optional[SigningKey]:
        enc_path = self._keys_dir / f"{kid}.enc"
        if not enc_path.exists():
            return None
        encrypted = enc_path.read_bytes()
        private_pem = self._fernet.decrypt(encrypted)

        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        private_key = load_pem_private_key(private_pem, password=None)
        public_jwk = _rsa_pub_to_jwk(private_key.public_key(), kid)
        return SigningKey(kid=kid, private_pem=private_pem, public_jwk=public_jwk)

    def get_active_signing_key(self) -> SigningKey:
        meta = self._load_meta()
        if not meta.get("active"):
            sk = self._initialize_first_key()
            return sk
        sk = self.load_key(meta["active"])
        if sk is None:
            raise RuntimeError(f"Active key {meta['active']} not found on disk")
        return sk

    def get_all_public_keys(self) -> list[dict]:
        meta = self._load_meta()
        keys = []
        for status_key in ("active", "previous"):
            kid = meta.get(status_key)
            if not kid:
                continue
            sk = self.load_key(kid)
            if sk:
                keys.append(sk.public_jwk)
        return keys

    def _initialize_first_key(self) -> SigningKey:
        from datetime import date
        kid = f"idp-sign-{date.today().strftime('%Y%m%d')}-v1"
        sk = self.generate_key(kid)
        meta = {"active": kid, "previous": None}
        self._save_meta(meta)
        return sk

    def rotate(self) -> tuple[str, Optional[str]]:
        meta = self._load_meta()
        old_active = meta.get("active")
        old_previous = meta.get("previous")

        from datetime import date
        import re
        version = 1
        if old_active:
            m = re.search(r"-v(\d+)$", old_active)
            if m:
                version = int(m.group(1)) + 1
        new_kid = f"idp-sign-{date.today().strftime('%Y%m%d')}-v{version}"
        self.generate_key(new_kid)

        new_meta = {"active": new_kid, "previous": old_active}
        self._save_meta(new_meta)
        return new_kid, old_active


_kms: Optional[KMSStore] = None


def init_kms(passphrase: str, keys_dir: str) -> None:
    global _kms
    _kms = KMSStore(passphrase=passphrase, keys_dir=keys_dir)
    _kms.get_active_signing_key()


def get_kms() -> KMSStore:
    if _kms is None:
        raise RuntimeError("KMS not initialized")
    return _kms
