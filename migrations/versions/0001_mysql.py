"""全新 MySQL 业务结构。"""

from alembic import op

from mediZJ.infrastructure.schema import metadata

revision = "0001_mysql"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    metadata.create_all(op.get_bind())


def downgrade():
    raise RuntimeError("初始结构不支持破坏性降级；请恢复备份")
