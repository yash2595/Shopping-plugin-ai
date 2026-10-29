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


def test_erasure_is_owned_cascades_runtime_and_preserves_daily_limits(client, auth_headers, db_session):
    import uuid
    from datetime import datetime, timezone
    from sqlalchemy import func
    from app.models.user import User
    from app.models.product import ProductSnapshot, PriceObservation
    from app.models.review import ReviewSummary
    from app.models.alert import PriceAlert
    from app.models.context import ShoppingContext
    from app.models.session import AIConversation, AIEvent, AIDispatch, Session as ChatSession
    from app.repositories.usage_repo import UsageRepository
    from app.services.auth_service import AuthService

    auth = AuthService()
    owner = auth.authenticate_token(db_session, auth_headers['Authorization'].split()[1]).id
    other = {'Authorization': 'Bearer ' + client.post('/v1/auth/register').json()['access_token']}
    source = {'retailer': 'amazon', 'retailer_product_id': uuid.uuid4().hex, 'title': 'Erase fixture', 'price': '100', 'availability': 'in_stock'}
    listing = client.post('/v1/snapshots', headers=auth_headers, json=source).json()['listing_id']
    assert client.post('/v1/snapshots', headers=other, json=source).status_code == 201
    reviews = {'reviews': [{'rating': 2, 'text': 'Battery lasts a day'}, {'rating': 3, 'text': 'Sound is clear'}, {'rating': 4, 'text': 'Build is solid'}]}
    assert client.post(f'/v1/listings/{listing}/review-summary', headers=auth_headers, json=reviews).status_code == 200
    assert client.post(f'/v1/listings/{listing}/review-summary', headers=other, json=reviews).status_code == 200
    assert client.post('/v1/alerts', headers=auth_headers, json={'listing_id': listing, 'target_price': '90'}).status_code == 201
    own_session = client.post('/v1/sessions', headers=auth_headers, json={}).json()['id']
    other_session = client.post('/v1/sessions', headers=other, json={}).json()['id']
    runtime = client.post(f'/v1/ai/sessions/{own_session}/conversation', headers=auth_headers, json={'language': 'en'})
    assert runtime.status_code == 200
    conv = runtime.json()
    result = client.post(f"/v1/ai/conversations/{conv['conversation_id']}/events", headers=auth_headers, json={'event_id': 'erase-event', 'expected_version': conv['version'], 'kind': 'text', 'message': 'hello'})
    assert result.status_code == 200
    # A real pending outbox record must disappear, including its executable payload.
    db_session.add(AIDispatch(dispatch_id=uuid.uuid4().hex, conversation_id=conv['conversation_id'], user_id=owner, session_id=uuid.UUID(own_session), kind='browser', payload={'private': 'pending'}, origin='https://www.amazon.in', version=result.json()['version'], deadline_at=datetime.now(timezone.utc)))
    UsageRepository().increment_and_get_count(db_session, owner, datetime.now(timezone.utc).date(), 'ai_daily')
    db_session.commit()
    before = client.get('/v1/auth/usage', headers=auth_headers).json()
    assert before['ai_requests'] >= 1
    erased = client.request('DELETE', '/v1/auth/data', headers=auth_headers, json={'confirm': True})
    assert erased.status_code == 204
    assert client.get(f'/v1/sessions/{own_session}', headers=auth_headers).status_code == 404
    assert client.get(f'/v1/sessions/{other_session}', headers=other).status_code == 200
    for model in (ProductSnapshot, PriceObservation, PriceAlert, ShoppingContext, ChatSession, AIConversation, AIEvent, AIDispatch):
        assert db_session.scalar(select(func.count()).select_from(model).where(model.user_id == owner)) == 0
    assert db_session.scalar(select(func.count()).select_from(ReviewSummary).where(ReviewSummary.summary['owner_id'].astext == str(owner))) == 0
    assert client.get(f'/v1/listings/{listing}/review-summary', headers=other).status_code == 200
    assert db_session.get(User, owner) is not None
    assert client.get('/v1/auth/usage', headers=auth_headers).json() == before
    assert client.request('DELETE', '/v1/auth/data', headers=auth_headers, json={'confirm': True}).status_code == 204


def test_erasure_requires_authentication_and_explicit_confirmation(client, auth_headers):
    assert client.request('DELETE', '/v1/auth/data', json={'confirm': True}).status_code == 401
    for body in ({}, {'confirm': False}, {'confirm': 1}, {'confirm': 'true'}, {'confirm': True, 'user_id': 'other'}):
        assert client.request('DELETE', '/v1/auth/data', headers=auth_headers, json=body).status_code == 422


def test_usage_is_private_bounded_and_aggregate_requires_operator_secret(client, auth_headers, db_session):
    from app.config import get_settings
    from app.repositories.usage_repo import UsageRepository
    from app.services.auth_service import AuthService
    from datetime import datetime, timezone
    owner = AuthService().authenticate_token(db_session, auth_headers['Authorization'].split()[1]).id
    today = datetime.now(timezone.utc).date()
    repo = UsageRepository()
    repo.increment_and_get_count(db_session, owner, today, 'ai_daily')
    db_session.commit()
    usage = client.get('/v1/auth/usage', headers=auth_headers)
    assert usage.status_code == 200 and usage.headers['Cache-Control'] == 'no-store'
    assert usage.json()['ai_requests'] == 1
    other = {'Authorization': 'Bearer ' + client.post('/v1/auth/register').json()['access_token']}
    assert client.get('/v1/auth/usage', headers=other).json()['ai_requests'] == 0
    assert client.get('/v1/auth/usage').status_code == 401
    assert client.get('/internal/jobs/usage', headers=auth_headers).status_code == 401
    aggregate = client.get('/internal/jobs/usage', headers={'X-Job-Secret': get_settings().JOB_SECRET})
    assert aggregate.status_code == 200 and aggregate.headers['Cache-Control'] == 'no-store'
    assert aggregate.json()['ai_requests'] >= 1
    assert set(aggregate.json()) == {'day', 'ai_requests', 'speech_requests', 'measurement'}


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
