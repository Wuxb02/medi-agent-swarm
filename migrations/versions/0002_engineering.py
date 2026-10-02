"""新增跨实例运行计数，保持已有业务表兼容。"""

from alembic import op
from mediZJ.infrastructure.schema import metric_counters

revision = "0002_engineering"
down_revision = "0001_mysql"
branch_labels = None
depends_on = None


def upgrade():
    metric_counters.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError("请使用备份恢复，禁止删除运行计数")
