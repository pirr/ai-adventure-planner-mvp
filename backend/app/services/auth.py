from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.config import settings
from app.schemas import AuthUser
from app.services.net import http_client
from app.services.storage import storage


_password_hasher = PasswordHasher()
_GOOGLE_SCOPES = "openid email profile"
_OAUTH_STATE_MINUTES = 10
_OAUTH_STATE_COOKIE_SUFFIX = "_oauth_state"


class AuthError(RuntimeError):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class SessionContext:
    token_hash: str
    account_id: int
    email: str
    email_verified: bool
    provider: str
    csrf_token: str

    @property
    def user(self) -> AuthUser:
        return AuthUser(
            id=self.account_id,
            email=self.email,
            email_verified=self.email_verified,
            provider="google" if self.provider == "google" else "email",
        )


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerificationError, VerifyMismatchError):
        return False


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _expires_at(days: int) -> str:
    return (datetime.utcnow() + timedelta(days=days)).isoformat()


def google_oauth_enabled() -> bool:
    return bool(
        settings.google_oauth_client_id
        and settings.google_oauth_client_secret
        and settings.google_oauth_redirect_uri
    )


def auth_user_from_account(account: dict) -> AuthUser:
    provider = storage.accounts.account_provider(int(account["id"]))
    return AuthUser(
        id=int(account["id"]),
        email=account["email"],
        email_verified=bool(account["email_verified"]),
        provider="google" if provider == "google" else "email",
    )


def session_context_from_request(request) -> SessionContext | None:
    token = request.cookies.get(settings.auth_session_cookie_name)
    if not token:
        return None
    token_hash = _hash_token(token)
    session = storage.auth.session_for_token_hash(token_hash)
    if not session:
        return None
    return SessionContext(
        token_hash=token_hash,
        account_id=int(session["account_id"]),
        email=session["email"],
        email_verified=bool(session["email_verified"]),
        provider=session.get("provider") or "email",
        csrf_token=session["csrf_token"],
    )


def start_session(response, account: dict, *, user_agent: str | None = None) -> SessionContext:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    storage.auth.create_session(
        token_hash=token_hash,
        account_id=int(account["id"]),
        csrf_token=csrf_token,
        expires_at=_expires_at(settings.auth_session_days),
        user_agent=user_agent,
    )
    response.set_cookie(
        settings.auth_session_cookie_name,
        token,
        max_age=settings.auth_session_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )
    return SessionContext(
        token_hash=token_hash,
        account_id=int(account["id"]),
        email=account["email"],
        email_verified=bool(account["email_verified"]),
        provider=storage.accounts.account_provider(int(account["id"])),
        csrf_token=csrf_token,
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(
        settings.auth_session_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
    )


def oauth_state_cookie_name() -> str:
    return f"{settings.auth_session_cookie_name}{_OAUTH_STATE_COOKIE_SUFFIX}"


def set_oauth_state_cookie(response, state: str) -> None:
    response.set_cookie(
        oauth_state_cookie_name(),
        state,
        max_age=_OAUTH_STATE_MINUTES * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )


def clear_oauth_state_cookie(response) -> None:
    response.delete_cookie(
        oauth_state_cookie_name(),
        path="/",
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
    )


def require_oauth_state_cookie(request, state: str) -> None:
    cookie_state = request.cookies.get(oauth_state_cookie_name())
    if not cookie_state or not secrets.compare_digest(cookie_state, state):
        raise AuthError("invalid_oauth_state", status_code=400)


def require_csrf(request, session: SessionContext) -> None:
    header = request.headers.get("x-csrf-token")
    if not header or not secrets.compare_digest(header, session.csrf_token):
        raise AuthError("csrf_required", status_code=403)


def register_email(email: str, password: str, anonymous_id: str | None, locale: str | None = None) -> dict:
    if len(password) < settings.auth_password_min_length:
        raise AuthError("password_too_short", status_code=422)
    if storage.accounts.get_account_by_email(email):
        raise AuthError("email_already_registered", status_code=409)
    try:
        account = storage.accounts.create_email_account(email, hash_password(password), locale)
    except Exception as exc:  # sqlite uniqueness races should stay user-safe
        if exc.__class__.__name__ == "IntegrityError":
            raise AuthError("email_already_registered", status_code=409) from exc
        raise
    storage.lifecycle.merge_anonymous_into_account(anonymous_id, int(account["id"]))
    return account


def login_email(email: str, password: str, anonymous_id: str | None) -> dict:
    account = storage.accounts.get_account_by_email(email)
    if not account or not verify_password(account.get("password_hash"), password):
        raise AuthError("invalid_credentials", status_code=401)
    storage.lifecycle.merge_anonymous_into_account(anonymous_id, int(account["id"]))
    return account


def create_google_authorization_url(anonymous_id: str | None = None) -> tuple[str, str]:
    if not google_oauth_enabled():
        raise AuthError("google_oauth_disabled", status_code=404)
    state = secrets.token_urlsafe(32)
    expires_at = (datetime.utcnow() + timedelta(minutes=_OAUTH_STATE_MINUTES)).isoformat()
    storage.auth.create_oauth_state(_hash_token(state), anonymous_id, expires_at)
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": _GOOGLE_SCOPES,
        "state": state,
        "prompt": "select_account",
    }
    return f"{settings.google_oauth_authorization_url}?{urlencode(params)}", state


async def exchange_google_code(code: str) -> dict:
    if not google_oauth_enabled():
        raise AuthError("google_oauth_disabled", status_code=404)
    async with http_client(settings.http_timeout_seconds) as client:
        response = await client.post(
            settings.google_oauth_token_url,
            data={
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": settings.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise AuthError("google_oauth_failed", status_code=502) from exc
    return response.json()


def verify_google_id_token(id_token: str) -> dict:
    if not settings.google_oauth_client_id:
        raise AuthError("google_oauth_disabled", status_code=404)
    try:
        return google_id_token.verify_oauth2_token(
            id_token,
            google_requests.Request(),
            settings.google_oauth_client_id,
        )
    except Exception as exc:  # noqa: BLE001 - provider details should not leak
        raise AuthError("google_oauth_failed", status_code=502) from exc


def _google_email_verified(email: str, claims: dict) -> bool:
    if not claims.get("email_verified"):
        return False
    normalized = email.strip().lower()
    return normalized.endswith("@gmail.com") or bool(claims.get("hd"))


async def finish_google_login(code: str, state: str) -> tuple[dict, str | None]:
    state_row = storage.auth.consume_oauth_state(_hash_token(state))
    if state_row is None:
        raise AuthError("invalid_oauth_state", status_code=400)
    token_payload = await exchange_google_code(code)
    raw_id_token = token_payload.get("id_token")
    if not raw_id_token:
        raise AuthError("google_oauth_failed", status_code=502)
    claims = verify_google_id_token(raw_id_token)
    subject = claims.get("sub")
    email = claims.get("email")
    if not subject or not email:
        raise AuthError("google_oauth_failed", status_code=502)
    account = storage.accounts.create_or_link_google_account(
        provider_subject=str(subject),
        email=str(email),
        email_verified=_google_email_verified(str(email), claims),
    )
    storage.lifecycle.merge_anonymous_into_account(state_row.get("anonymous_id"), int(account["id"]))
    return account, state_row.get("anonymous_id")
