"""Baseline schema: the complete app data model.

This is the foundation every later migration builds on. It makes Alembic the
source of truth for the schema: `alembic upgrade head` on a truly empty database
produces the whole application schema here (and `downgrade` reproduces an empty
DB). Fresh and pre-existing databases converge to the same head revision before
any 0002+ migration runs.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260830_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("tg_user_id", sa.Integer(), nullable=True),
        sa.Column("first_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("last_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("username", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("bio", sa.String(length=140), nullable=False, server_default=""),
        sa.Column("session_file", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="disconnected"),
        sa.Column("has_2fa", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_online", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_accounts_phone", "accounts", ["phone"], unique=True)

    op.create_table(
        "security_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tg_msg_id", sa.Integer(), nullable=False),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("received_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "gone_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("tg_user_id", sa.Integer(), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("first_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("last_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("username", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("old_serial", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=16), nullable=False, server_default="removed"),
        sa.Column("gone_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_gone_accounts_phone", "gone_accounts", ["phone"], unique=False)

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )

    op.create_table(
        "pending_logins",
        sa.Column("phone", sa.String(length=32), primary_key=True),
        sa.Column("phone_code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("pending_logins")
    op.drop_table("app_settings")
    op.drop_index("ix_gone_accounts_phone", table_name="gone_accounts")
    op.drop_table("gone_accounts")
    op.drop_table("security_messages")
    op.drop_index("ix_accounts_phone", table_name="accounts")
    op.drop_table("accounts")
