import hashlib
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import AuthToken


def test_logout_revokes_token_and_prevents_replay(client, auth_headers):
    session_id = client.post("/v1/sessions", json={}, headers=auth_headers).json()["id"]
    payload = {"request_id": "logout-retry", "text": "hello"}
    assert (
        client.post(
            f"/v1/sessions/{session_id}/messages", json=payload, headers=auth_headers
        ).status_code
        == 200
    )
    assert client.post("/v1/auth/logout", headers=auth_headers).status_code == 204
    assert client.get(f"/v1/sessions/{session_id}", headers=auth_headers).status_code == 401
    assert (
        client.post(
            f"/v1/sessions/{session_id}/messages", json=payload, headers=auth_headers
        ).status_code
        == 401
    )


def test_expired_token_cannot_load_session(client, auth_headers, db_session):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    session_id = client.post("/v1/sessions", json={}, headers=auth_headers).json()["id"]
    token_hash = hashlib.sha256(auth_headers["Authorization"].split()[1].encode()).hexdigest()
    db_session.execute(
        update(AuthToken)
        .where(AuthToken.token_hash == token_hash)
        .values(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )
    db_session.commit()
    assert client.get(f"/v1/sessions/{session_id}", headers=auth_headers).status_code == 401


def test_anonymous_registration(client: TestClient, db_session: Session):
    response = client.post("/v1/auth/register")
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"

    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert "expires_at" in data

    token = data["access_token"]
    expected_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    # Verify that ONLY the SHA-256 hash was stored, never the raw token
    stored_token = db_session.scalars(
        select(AuthToken).where(AuthToken.token_hash == expected_hash)
    ).first()
    assert stored_token is not None
    assert stored_token.token_hash == expected_hash


def test_anonymous_registration_requires_explicit_production_opt_in(client, monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "ALLOW_ANONYMOUS_REGISTRATION", False)
    assert client.post("/v1/auth/register").status_code == 403
    monkeypatch.setattr(settings, "ALLOW_ANONYMOUS_REGISTRATION", True)
    assert client.post("/v1/auth/register").status_code == 201


def test_unauthorized_when_missing_token(client: TestClient):
    response = client.post("/v1/sessions", json={})
    assert response.status_code == 401
    assert "error" in response.json()
    assert response.json()["error"]["code"] == "unauthorized"


def test_unauthorized_when_invalid_token(client: TestClient):
    headers = {"Authorization": "Bearer invalid_nonexistent_token_123"}
    response = client.post("/v1/sessions", json={}, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


@pytest.fixture
def signed_identity(monkeypatch):
    from app.config import get_settings
    from app.services.auth_service import AuthService

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public.update(kid="offline-test", alg="RS256", use="sig")
    config = get_settings()
    monkeypatch.setattr(config, "AUTH_MODE", "oidc")
    monkeypatch.setattr(config, "AUTH_ISSUER", "https://identity.example.test")
    monkeypatch.setattr(config, "AUTH_AUDIENCE", "shopping-api")
    monkeypatch.setattr(config, "AUTH_JWKS_JSON", json.dumps({"keys": [public]}))
    service = AuthService()

    def sign(overrides=None, headers=None):
        now = int(time.time())
        claims = dict(
            iss=config.AUTH_ISSUER,
            aud=config.AUTH_AUDIENCE,
            sub="alice",
            iat=now - 5,
            exp=now + 600,
        )
        claims.update(overrides or {})
        return jwt.encode(
            claims, private, algorithm="RS256", headers=headers or {"kid": "offline-test"}
        )

    return service, sign, public


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://attacker.example.test"},
        {"aud": "another-api"},
        {"exp": 1},
        {"iat": 4_000_000_000},
        {"nbf": 4_000_000_000},
        {"sub": ""},
        {"sub": 123},
        {"exp": "4000000000"},
        {"sub": "x\x00y"},
        {"sub": "x" * 256},
    ],
)
def test_identity_rejects_invalid_claims(signed_identity, changes):
    service, sign, _ = signed_identity
    assert service.verifier.verify(sign(changes)) is None


def test_identity_pinned_key_and_algorithm(signed_identity):
    service, sign, _ = signed_identity
    assert service.verifier.verify(sign())["sub"] == "alice"
    for header in (
        {"kid": "unknown"},
        {"kid": "offline-test", "jku": "https://attacker.test/keys"},
        {"kid": "offline-test", "crit": ["made-up"]},
    ):
        assert service.verifier.verify(sign(headers=header)) is None
    token = jwt.encode(
        {"sub": "alice"}, "a" * 32, algorithm="HS256", headers={"kid": "offline-test"}
    )
    assert service.verifier.verify(token) is None
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        jwt.decode(sign(), options={"verify_signature": False}),
        other,
        algorithm="RS256",
        headers={"kid": "offline-test"},
    )
    assert service.verifier.verify(token) is None


def test_identity_rotation_configuration_rejects_private_duplicate_and_weak_keys(signed_identity):
    from app.config import get_settings
    from app.services.identity import IdentityVerifier

    _, _, public = signed_identity
    config = get_settings().model_copy()
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    weak_public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(weak.public_key()))
    weak_public.update(kid="weak", alg="RS256")
    for keys in ([public, public], [{**public, "d": "private-material"}], [weak_public], []):
        config.AUTH_JWKS_JSON = json.dumps({"keys": keys})
        with pytest.raises(ValueError, match="Invalid public identity"):
            IdentityVerifier(config)


def test_verified_identity_ownership_revocation_and_no_anonymous_downgrade(
    client, signed_identity, db_session
):
    from app.models.user import ExternalIdentity, User

    service, sign, _ = signed_identity
    client.app.state.auth_service = service
    alice = {"Authorization": "Bearer " + sign({"email": "same@example.test"})}
    bob = {"Authorization": "Bearer " + sign({"sub": "bob", "email": "same@example.test"})}
    assert client.post("/v1/auth/register").status_code == 403
    response = client.post("/v1/sessions", json={}, headers=alice)
    assert response.status_code == 201
    session_id = response.json()["id"]
    assert client.get(f"/v1/sessions/{session_id}", headers=alice).status_code == 200
    assert client.get(f"/v1/sessions/{session_id}", headers=bob).status_code == 404
    identities = db_session.scalars(select(ExternalIdentity)).all()
    assert len(identities) == 2
    assert len({identity.user_id for identity in identities}) == 2
    assert all(not db_session.get(User, identity.user_id).is_anonymous for identity in identities)
    assert client.post("/v1/auth/logout", headers=alice).status_code == 204
    assert client.get(f"/v1/sessions/{session_id}", headers=alice).status_code == 401
    renewed = {"Authorization": "Bearer " + sign({"jti": "fresh-access-token"})}
    assert client.get(f"/v1/sessions/{session_id}", headers=renewed).status_code == 200


def test_invalid_identity_does_not_touch_database(signed_identity):
    service, _, _ = signed_identity

    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError("Invalid token reached database")

    assert service.authenticate_token(NoDatabase(), "invalid") is None


def test_identity_es256_rotation_and_removed_key(signed_identity):
    from cryptography.hazmat.primitives.asymmetric import ec

    from app.config import get_settings
    from app.services.identity import IdentityVerifier

    _, sign, old_public = signed_identity
    private = ec.generate_private_key(ec.SECP256R1())
    public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()))
    public.update(kid="next-key", alg="ES256")
    config = get_settings().model_copy()
    config.AUTH_JWKS_JSON = json.dumps({"keys": [old_public, public]})
    verifier = IdentityVerifier(config)
    old_token = sign()
    claims = jwt.decode(old_token, options={"verify_signature": False})
    new_token = jwt.encode(claims, private, algorithm="ES256", headers={"kid": "next-key"})
    assert verifier.verify(old_token) is not None
    assert verifier.verify(new_token) is not None
    config.AUTH_JWKS_JSON = json.dumps({"keys": [public]})
    rotated = IdentityVerifier(config)
    assert rotated.verify(old_token) is None
    assert rotated.verify(new_token) is not None
    for required in ("exp", "iat", "sub", "iss", "aud"):
        incomplete = {k: v for k, v in claims.items() if k != required}
        assert (
            rotated.verify(
                jwt.encode(incomplete, private, algorithm="ES256", headers={"kid": "next-key"})
            )
            is None
        )


def test_concurrent_first_identity_login_has_one_owner_and_no_orphan(signed_identity):
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import create_engine, delete

    from app.config import get_settings
    from app.models.user import User

    service, sign, _ = signed_identity
    token = sign({"sub": "race-" + uuid.uuid4().hex})
    engine = create_engine(
        get_settings().TEST_DATABASE_URL, connect_args={"prepare_threshold": None}
    )
    barrier, created = Barrier(2), []
    original = service.user_repo.create_user

    def create(db, is_anonymous=True):
        barrier.wait(timeout=10)
        user = original(db, is_anonymous=is_anonymous)
        created.append(user.id)
        return user

    service.user_repo.create_user = create

    def login():
        with Session(engine) as db:
            return service.authenticate_token(db, token).id

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            owners = list(pool.map(lambda _: login(), range(2)))
        assert owners[0] == owners[1]
        assert len(created) == 2
        with Session(engine) as db:
            assert db.scalars(select(User.id).where(User.id.in_(created))).all() == [owners[0]]
    finally:
        with Session(engine) as db:
            db.execute(delete(User).where(User.id.in_(created)))
            db.commit()
        engine.dispose()
