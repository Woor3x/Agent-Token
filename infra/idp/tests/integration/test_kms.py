"""
集成测试：KMS 密钥生成、加载、轮换
"""
import json

import pytest
from jose import jwt as jose_jwt

from kms.store import KMSStore


class TestKMSStore:
    def test_generate_and_load_key(self, tmp_path):
        kms = KMSStore("test-pass", str(tmp_path))
        sk1 = kms.generate_key("test-kid-001")

        assert sk1.kid == "test-kid-001"
        assert sk1.private_pem.startswith(b"-----BEGIN RSA PRIVATE KEY-----")
        assert sk1.public_jwk["kty"] == "RSA"
        assert sk1.public_jwk["kid"] == "test-kid-001"

        # 重新加载验证 Fernet 解密
        sk_loaded = kms.load_key("test-kid-001")
        assert sk_loaded.kid == "test-kid-001"
        assert sk_loaded.private_pem == sk1.private_pem

    def test_get_active_key_creates_if_missing(self, tmp_path):
        kms = KMSStore("test-pass", str(tmp_path))
        sk = kms.get_active_signing_key()
        assert sk.kid is not None
        assert "idp-sign" in sk.kid

    def test_rotate_creates_new_active(self, tmp_path):
        kms = KMSStore("test-pass", str(tmp_path))
        old_sk = kms.get_active_signing_key()
        old_kid = old_sk.kid

        new_kid, prev_kid = kms.rotate()

        assert new_kid != old_kid
        assert prev_kid == old_kid

        # 两个 key 都应该在 get_all_public_keys 中
        all_keys = kms.get_all_public_keys()
        kids = [k["kid"] for k in all_keys]
        assert new_kid in kids
        assert old_kid in kids

    def test_jwt_sign_and_verify(self, tmp_path):
        """用 KMS 私钥签发 JWT，用公钥验签"""
        kms = KMSStore("test-pass", str(tmp_path))
        sk = kms.get_active_signing_key()

        token = jose_jwt.encode(
            {"sub": "test", "exp": 9999999999},
            sk.private_pem,
            algorithm="RS256",
            headers={"kid": sk.kid},
        )

        decoded = jose_jwt.decode(
            token, sk.public_jwk, algorithms=["RS256"],
            options={"verify_aud": False, "verify_exp": False},
        )
        assert decoded["sub"] == "test"

    def test_wrong_passphrase_fails_to_load(self, tmp_path):
        """不同 passphrase 无法解密私钥"""
        from cryptography.fernet import InvalidToken

        kms1 = KMSStore("passphrase-A", str(tmp_path))
        kms1.generate_key("enc-test-kid")

        kms2 = KMSStore("passphrase-B", str(tmp_path))
        with pytest.raises(Exception):  # Fernet 解密失败
            kms2.load_key("enc-test-kid")
