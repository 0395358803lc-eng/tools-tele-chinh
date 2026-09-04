"""Deduplicate Telegram security messages and enforce idempotent inserts."""

from alembic import op

revision = "20260830_0002"
down_revision = "20260830_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "DELETE FROM security_messages WHERE id NOT IN ("
        "SELECT MIN(id) FROM security_messages GROUP BY account_id, tg_msg_id)"
    )
    op.create_index(
        "uq_security_account_tg_msg",
        "security_messages",
        ["account_id", "tg_msg_id"],
        unique=True,
    )


def downgrade():
    op.drop_index("uq_security_account_tg_msg", table_name="security_messages")
