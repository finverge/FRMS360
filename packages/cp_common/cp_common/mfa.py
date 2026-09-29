"""Second-factor and session primitives.

Three separate concerns that are easy to conflate:

**The access token** is short-lived, self-contained and unrevocable. That is the trade
that makes it fast to verify without a database round trip on every request.

**The session** is the revocable half. It holds a refresh token, and it is what a logout,
an account suspension or a detected theft actually ends. Access tokens outstanding at that
moment stay valid until they expire, which is why they are short.

**The second factor** proves possession of a device, not knowledge of a secret. It is
checked once per session, not per request; a session records whether it was satisfied so
that fact survives token rotation.

Refresh tokens rotate on every use. The rotation chain is what makes theft detectable: a
token presented after it has already been exchanged means two parties hold it, and the
correct response is to end the whole family rather than to serve either of them.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import pyotp

#: Number of single-use recovery codes issued at enrolment.
BACKUP_CODE_COUNT = 10
#: Codes are shown once. Grouped for legibility when a user writes them down.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no I/O/0/1 - transcription errors


def new_totp_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    """The otpauth:// URI an authenticator app consumes."""
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)


def qr_svg(uri: str) -> str:
    """Render the provisioning URI as an inline SVG.

    Server-rendered on purpose: a QR library in the browser is another asset to load, and
    several target deployments are air-gapped with no CDN reachable.
    """
    import io

    import qrcode
    import qrcode.image.svg

    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=10,
                      border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def verify_totp(secret: str, code: str, *, window: int = 1) -> bool:
    """Check a 6-digit code.

    ``window=1`` accepts the adjacent 30-second steps. Clock drift between a phone and a
    server is ordinary; refusing a code that was correct two seconds ago generates support
    tickets and teaches users to distrust the control.
    """
    if not secret or not code:
        return False
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=window)


def generate_backup_codes(count: int = BACKUP_CODE_COUNT) -> list[str]:
    out = []
    for _ in range(count):
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        out.append(f"{raw[:5]}-{raw[5:]}")
    return out


def normalise_backup_code(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch in _ALPHABET)


def hash_backup_code(code: str) -> str:
    """SHA-256 of the normalised code.

    Not bcrypt: these are 50 bits of uniform randomness from a known alphabet, so there is
    no low-entropy guess to slow down, and a user recovering access should not wait on a
    deliberately slow hash ten times over.
    """
    return hashlib.sha256(normalise_backup_code(code).encode()).hexdigest()


def new_refresh_token() -> str:
    """Opaque, 256 bits. Never a JWT: the point of this token is that it is revocable,
    and a self-contained token cannot be."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def expiry(days: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


def is_expired(when: datetime | None, *, now: datetime | None = None) -> bool:
    if when is None:
        return False
    now = now or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when < now


def mfa_required_for(policy: str, role: str) -> bool:
    """Whether this tenant's policy mandates a second factor for this role.

    ``privileged`` is the sensible default: the roles that can move a case, unmask a
    customer or change configuration. A board member with read-only aggregate access is a
    different risk, and forcing enrolment on them is how a bank ends up with shared
    devices.
    """
    from .rbac import get_role

    if policy == "all":
        return True
    if policy != "privileged":
        return False
    r = get_role(role)
    return bool(r.can_admin_tenant or r.can_reveal_pii or role == "platform_admin")
