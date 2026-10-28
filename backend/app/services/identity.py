"""Verify API access tokens against operator-pinned public keys; never token URLs."""

import json
import math

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.config import Settings


class IdentityVerifier:
    def __init__(self, settings: Settings):
        self.issuer = settings.AUTH_ISSUER
        self.audience = settings.AUTH_AUDIENCE
        self.keys = {}
        try:
            if len(settings.AUTH_JWKS_JSON.encode()) > 262_144:
                raise ValueError()
            keys = json.loads(settings.AUTH_JWKS_JSON)["keys"]
            if not isinstance(keys, list) or not 1 <= len(keys) <= 32:
                raise ValueError()
            for data in keys:
                kid, alg = data.get("kid"), data.get("alg")
                if (
                    not isinstance(kid, str)
                    or not 1 <= len(kid) <= 128
                    or kid in self.keys
                    or alg not in {"RS256", "ES256"}
                    or data.get("use", "sig") != "sig"
                    or data.get("key_ops", ["verify"]) != ["verify"]
                    or any(field in data for field in ("d", "p", "q", "dp", "dq", "qi", "oth", "k"))
                ):
                    raise ValueError()
                key = jwt.PyJWK.from_dict(data, algorithm=alg).key
                if alg == "RS256":
                    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
                        raise ValueError()
                elif not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
                    key.curve, ec.SECP256R1
                ):
                    raise ValueError()
                self.keys[kid] = (alg, key)
        except Exception:
            raise ValueError("Invalid public identity signing-key configuration") from None

    def verify(self, token: str) -> dict | None:
        try:
            if not isinstance(token, str) or len(token) > 8192:
                return None
            header = jwt.get_unverified_header(token)
            if any(field in header for field in ("crit", "jku", "jwk", "x5u", "zip")):
                return None
            configured = self.keys.get(header.get("kid"))
            if configured is None or header.get("alg") != configured[0]:
                return None
            claims = jwt.decode(
                token,
                configured[1],
                algorithms=[configured[0]],
                issuer=self.issuer,
                audience=self.audience,
                options={"require": ["iss", "aud", "sub", "exp", "iat"]},
            )
            if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 255:
                return None
            if len(claims["sub"].encode("utf-8")) > 255:
                return None
            if "\x00" in claims["sub"]:
                return None
            for name in ("exp", "iat", "nbf"):
                if name in claims and (
                    type(claims[name]) not in {int, float}
                    or not math.isfinite(claims[name])
                    or not 0 <= claims[name] <= 253_402_300_799
                ):
                    return None
            if claims["exp"] <= claims["iat"]:
                return None
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
            return None
