"""Envelope encryption for secrets held in the database (BR-712).

**What this is and is not.** Encryption at rest has two layers and they defend against
different things. Full-disk or tablespace encryption protects a stolen disk or a discarded
backup tape, and it is an infrastructure control - Postgres TDE, LUKS, or the cloud
provider's volume encryption. It does *not* protect against anything that can read the
database, because to that reader the data is already decrypted. This module is the second
layer: specific columns whose plaintext is dangerous to anyone holding a SELECT.

The column that made this urgent is ``mfa_secret``. A TOTP seed in plaintext means a
database reader can generate a valid second factor for every user in the platform - which
makes multi-factor authentication decorative. A read-only replica, a support export or a
misplaced dump would all do it.

**Design.**

* AES-256-GCM. Authenticated, so a tampered ciphertext fails rather than decrypting to
  something attacker-chosen.
* **Additional authenticated data binds the ciphertext to where it lives.** A secret
  lifted from one column and pasted into another - or into another user's row - fails to
  decrypt instead of silently working. This is cheap and it defeats a whole family of
  attacks that encryption alone does not.
* **Key version travels with the ciphertext**, so rotation does not require rewriting
  every row before the new key can be used. Old keys stay available for decryption.
* **Fail closed, always.** If a key is missing or a ciphertext will not authenticate,
  this raises. It never falls back to returning the stored bytes, because a system that
  degrades to plaintext under error is a system that is plaintext.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

#: Prefix marking a value this module produced. Anything without it is legacy plaintext,
#: which the column types below handle explicitly rather than by guessing.
PREFIX = "enc:v"
NONCE_BYTES = 12


class CryptoError(Exception):
    """Refusal. Never carries key material or plaintext."""


class KeyUnavailable(CryptoError):
    pass


@dataclass(frozen=True)
class Key:
    version: int
    material: bytes


class KeyProvider:
    """Where key material comes from.

    Deliberately an interface. A bank will want its own KMS or an HSM, and that decision
    should not require touching any call site - only a different provider here.
    """

    def active(self) -> Key:
        raise NotImplementedError

    def by_version(self, version: int) -> Key:
        raise NotImplementedError


class EnvKeyProvider(KeyProvider):
    """Keys from the environment. Suitable for development and for a single-tenant
    install where the operator manages the key file themselves.

    ``CP_ENCRYPTION_KEYS`` is ``version:base64key`` entries separated by commas, highest
    version active::

        CP_ENCRYPTION_KEYS=1:Base64Of32Bytes...,2:Base64Of32Bytes...

    It refuses to invent a key. A platform that silently generated one at boot would
    encrypt today's rows with a key that vanishes on restart, and every one of them would
    become permanently unreadable - a data-loss bug wearing a security feature's clothes.
    """

    def __init__(self, spec: str | None = None):
        self._keys: dict[int, Key] = {}
        if spec is not None:
            raw = spec
        else:
            # os.environ first so a deployment can override without touching .env; then
            # settings, which is what actually reads .env. Reading only os.environ meant
            # a key configured in .env was invisible, and every write raised.
            raw = os.environ.get("CP_ENCRYPTION_KEYS", "")
            if not raw:
                try:
                    from cp_common.settings import settings

                    raw = settings.cp_encryption_keys or ""
                except Exception:  # noqa: BLE001
                    raw = ""
        for part in (p.strip() for p in raw.split(",") if p.strip()):
            if ":" not in part:
                raise CryptoError("CP_ENCRYPTION_KEYS entries must be 'version:base64'")
            ver, b64 = part.split(":", 1)
            try:
                material = base64.b64decode(b64, validate=True)
            except Exception:
                raise CryptoError(f"Key {ver} is not valid base64")
            if len(material) != 32:
                raise CryptoError(
                    f"Key {ver} is {len(material)} bytes; AES-256 needs 32.")
            self._keys[int(ver)] = Key(int(ver), material)

    @property
    def configured(self) -> bool:
        return bool(self._keys)

    def active(self) -> Key:
        if not self._keys:
            raise KeyUnavailable(
                "No encryption key is configured. Set CP_ENCRYPTION_KEYS "
                "('version:base64-of-32-bytes'). Generate one with:\n"
                "  python -c \"import os,base64;"
                "print('1:'+base64.b64encode(os.urandom(32)).decode())\"")
        return self._keys[max(self._keys)]

    def by_version(self, version: int) -> Key:
        try:
            return self._keys[version]
        except KeyError:
            raise KeyUnavailable(
                f"Ciphertext was written with key version {version}, which is not "
                f"configured. Retire a key only once nothing references it.")


_provider: KeyProvider = EnvKeyProvider()


def set_provider(provider: KeyProvider) -> None:
    global _provider
    _provider = provider


def provider() -> KeyProvider:
    return _provider


def available() -> bool:
    try:
        _provider.active()
        return True
    except CryptoError:
        return False


def seal(plaintext: str, *, aad: str) -> str:
    """Encrypt. ``aad`` binds the result to its column and row."""
    if plaintext is None:
        raise CryptoError("Cannot seal None")
    key = _provider.active()
    nonce = os.urandom(NONCE_BYTES)
    ct = AESGCM(key.material).encrypt(nonce, plaintext.encode("utf-8"),
                                      aad.encode("utf-8"))
    return f"{PREFIX}{key.version}:{base64.b64encode(nonce + ct).decode()}"


def unseal(token: str, *, aad: str) -> str:
    """Decrypt, or raise. Never returns the input on failure."""
    if not is_sealed(token):
        raise CryptoError("Value is not sealed ciphertext")
    body = token[len(PREFIX):]
    ver_s, _, b64 = body.partition(":")
    try:
        version = int(ver_s)
    except ValueError:
        raise CryptoError("Malformed ciphertext: bad key version")
    key = _provider.by_version(version)
    try:
        blob = base64.b64decode(b64, validate=True)
    except Exception:
        raise CryptoError("Malformed ciphertext: bad base64")
    if len(blob) <= NONCE_BYTES:
        raise CryptoError("Malformed ciphertext: too short")
    try:
        pt = AESGCM(key.material).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:],
                                          aad.encode("utf-8"))
    except InvalidTag:
        # Either the ciphertext was altered, or it was moved somewhere the AAD does not
        # match - a secret copied into another row or column. Both are refusals.
        raise CryptoError(
            "Ciphertext failed authentication. It was modified, or it belongs to a "
            "different record than the one it was found in.")
    return pt.decode("utf-8")


def is_sealed(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def key_version(token: str) -> int | None:
    if not is_sealed(token):
        return None
    try:
        return int(token[len(PREFIX):].partition(":")[0])
    except ValueError:
        return None


# --------------------------------------------------------------------- SQLAlchemy
from sqlalchemy import Text  # noqa: E402
from sqlalchemy.types import TypeDecorator  # noqa: E402

#: Explicit, visible opt-out for a deployment that has not configured a key yet. It has
#: to be set deliberately, and it is named so that it shows up in a configuration review.
#: Without it, writing a secret with no key raises rather than quietly storing plaintext.
ALLOW_PLAINTEXT_ENV = "CP_ALLOW_PLAINTEXT_SECRETS"


def _plaintext_allowed() -> bool:
    return os.environ.get(ALLOW_PLAINTEXT_ENV, "").lower() in ("1", "true", "yes")


class EncryptedSecret(TypeDecorator):
    """A text column sealed at rest.

    ``context`` is the column's qualified name and becomes the AAD, so a ciphertext
    lifted into a different column fails to authenticate rather than decrypting.

    **Legacy plaintext reads through.** Rows written before this column was encrypted are
    returned as-is, because a migration cannot be instantaneous and refusing to read
    existing data would take the platform down. That is a *known* plaintext value, still
    visible in the database, and the backfill script converts it. What is never tolerated
    is the other direction: a value that claims to be sealed and fails to authenticate
    raises, and is not handed back as though it were plaintext.
    """

    impl = Text
    cache_ok = True

    def __init__(self, context: str, **kw):
        self.context = context
        super().__init__(**kw)

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return value
        if is_sealed(value):
            return value                      # already sealed; do not double-wrap
        if not available():
            if _plaintext_allowed():
                return value
            raise KeyUnavailable(
                f"Refusing to store {self.context} as plaintext: no encryption key is "
                f"configured. Set CP_ENCRYPTION_KEYS, or set {ALLOW_PLAINTEXT_ENV}=true "
                f"to acknowledge storing secrets in the clear.")
        return seal(str(value), aad=self.context)

    def process_result_value(self, value, dialect):
        if value is None or value == "":
            return value
        if not is_sealed(value):
            return value                      # pre-encryption row; see docstring
        return unseal(value, aad=self.context)
