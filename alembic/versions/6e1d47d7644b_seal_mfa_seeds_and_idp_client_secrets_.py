"""seal MFA seeds and IdP client secrets at rest (BR-712)

Revision ID: 6e1d47d7644b
Revises: b68531fc8d4e
Create Date: 2026-08-03 21:39:05.621911

Widens the two MFA seed columns to TEXT. They were varchar(64), sized for a raw base32
TOTP seed; AES-256-GCM ciphertext plus its nonce and key-version prefix is roughly ninety
characters, so the first enrolment after sealing would have failed on insert.

Autogenerate rendered the Python-side ``EncryptedSecret`` type into the DDL, which is both
unimportable here and missing its required argument. The database only needs to know the
column is TEXT; the encryption lives entirely in the application type.

Existing rows are left as they are - still plaintext, still readable - and are converted
by ``scripts/seal_secrets.py --commit``. Doing it here would put key material in a
migration, and a migration that cannot run without the encryption key is a migration that
cannot be replayed to rebuild a database.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "6e1d47d7644b"
down_revision: Union[str, None] = "b68531fc8d4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("platform_users", "tenant_users"):
        op.alter_column(table, "mfa_secret",
                        existing_type=sa.VARCHAR(length=64),
                        type_=sa.Text(),
                        existing_nullable=False,
                        existing_server_default=sa.text("''::character varying"),
                        schema="tenant")


def downgrade() -> None:
    # Only safe once the seeds are back in plaintext; sealed values will not fit.
    for table in ("tenant_users", "platform_users"):
        op.alter_column(table, "mfa_secret",
                        existing_type=sa.Text(),
                        type_=sa.VARCHAR(length=64),
                        existing_nullable=False,
                        existing_server_default=sa.text("''::character varying"),
                        schema="tenant")
