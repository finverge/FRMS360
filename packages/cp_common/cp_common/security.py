"""Password hashing and JWT issue/verify. Stateless auth shared by all services."""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .settings import settings


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def create_access_token(
    *, subject: str, role: str, tenant_id: str | None = None, scope: str = "full"
) -> str:
    """Issue a JWT.

    ``scope='full'``          — normal access.
    ``scope='password_reset'`` — may ONLY call the change-password endpoint. Issued when a
    user still has ``must_change_password`` set, so a temporary password can never be used
    to reach the rest of the API.

    ``scope='mfa_pending'`` — the password was correct but the second factor has not been
    presented. May ONLY call the MFA verification endpoints.
    """
    now = datetime.now(timezone.utc)
    # Tokens issued mid-authentication are short-lived: they exist only to complete the
    # step they were issued for, and a five-minute window is ample to type six digits.
    minutes = settings.jwt_expire_minutes
    if scope == "password_reset":
        minutes = 15
    elif scope == "mfa_pending":
        minutes = 5
    payload = {
        "sub": subject,
        "role": role,
        "tenant_id": tenant_id,
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
