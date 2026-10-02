"""保存知识版本分块数量，文档列表无需读取向量库全文。"""

from alembic import op
from sqlalchemy import Column, Integer, inspect, text

revision = "0004_knowledge_chunk_count"
down_revision = "0003_feedback_versions"
branch_labels = None
depends_on = None


def upgrade():
    # 初始迁移使用当前 metadata；新部署可能已经创建该列。
    columns = inspect(op.get_bind()).get_columns("knowledge_documents")
    if not any(column["name"] == "chunk_count" for column in columns):
        op.add_column(
            "knowledge_documents",
            Column("chunk_count", Integer, nullable=False, server_default=text("0")),
        )
    # 历史索引统一使用 1024 字符块、100 字符重叠，与 _chunk_text 一致。
    op.execute(
        "UPDATE knowledge_documents SET chunk_count = CASE "
        "WHEN CHAR_LENGTH(content) <= 1024 THEN 1 "
        "ELSE CEIL(CHAR_LENGTH(content) / 924.0) END "
        "WHERE chunk_count=0 AND status IN ('active','archived','expired')"
    )


def downgrade():
    raise RuntimeError("请恢复备份，禁止删除知识版本分块统计")
