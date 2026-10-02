"""人工评审使用毫秒时间戳版本，扩展为 64 位整数。"""

from alembic import op
from sqlalchemy import BigInteger, Integer

revision = "0003_feedback_versions"
down_revision = "0002_engineering"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("evaluation_jobs", "feedback_version",
                    existing_type=Integer(), type_=BigInteger(), existing_nullable=False)


def downgrade():
    raise RuntimeError("64 位版本不能安全缩小，请恢复备份")
