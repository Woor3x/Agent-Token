"""
单元测试：dpop/validator.py 中 JWK Thumbprint (RFC 7638) 计算
"""
import base64
import hashlib
import json

import pytest
from dpop.validator import jwk_thumbprint
from errors import DpopInvalid


def _sha256_b64u(data: dict) -> str:
    """手动计算 thumbprint 作为参考"""
    canonical = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    digest = hashlib.sha256(canonical).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class TestJwkThumbprint:
    def test_rsa_key_rfc7638_example(self):
        """
        RFC 7638 Section 3.1 的官方测试向量。
        Expected thumbprint: NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs
        """
        jwk = {
            "e": "AQAB",
            "kty": "RSA",
            "n": (
                "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAt"
                "VT86zwu1RK7aPFFxuhDR1L6tSoc_BJECPebWKRXjBZCiFV4n3oknjhMstn6"
                "4tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5v-65YGjQR0_F"
                "DW2QvzqY368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n9"
                "1CbOpbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINH"
                "aQ-G_xBniIqbw0Ls1jF44-csFCur-kEgU8awapJzKnqDKgw"
            ),
        }
        result = jwk_thumbprint(jwk)
        assert result == "NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs"

    def test_rsa_key_uses_only_required_members(self):
        """只用 e, kty, n 参与 thumbprint，其余字段忽略"""
        jwk_with_extras = {
            "kty": "RSA",
            "kid": "my-key-id",          # 不参与 thumbprint
            "use": "sig",                  # 不参与
            "alg": "RS256",               # 不参与
            "e": "AQAB",
            "n": "somerandomn",
        }
        jwk_minimal = {"kty": "RSA", "e": "AQAB", "n": "somerandomn"}
        assert jwk_thumbprint(jwk_with_extras) == jwk_thumbprint(jwk_minimal)

    def test_unsupported_kty_raises(self):
        with pytest.raises(DpopInvalid, match="Unsupported key type"):
            jwk_thumbprint({"kty": "oct", "k": "abc"})

    def test_missing_required_field_raises(self):
        """RSA key 缺少 n 字段"""
        with pytest.raises(DpopInvalid, match="missing required fields"):
            jwk_thumbprint({"kty": "RSA", "e": "AQAB"})

    def test_deterministic(self):
        """相同输入多次计算结果一致"""
        jwk = {"kty": "RSA", "e": "AQAB", "n": "test_n_value"}
        t1 = jwk_thumbprint(jwk)
        t2 = jwk_thumbprint(jwk)
        assert t1 == t2
