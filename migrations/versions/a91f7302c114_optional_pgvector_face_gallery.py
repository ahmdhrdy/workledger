"""Optional PostgreSQL face gallery; SQLite keeps the encrypted local gallery.
Revision ID: a91f7302c114
Revises: 8f1b84c3737c
"""

from alembic import op

revision = "a91f7302c114"
down_revision = "8f1b84c3737c"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("""CREATE TABLE employee_embeddings (
        company_id varchar(36) NOT NULL,site_id varchar(36) NOT NULL,
        model_id text NOT NULL,gallery_version varchar(64) NOT NULL,
        employee_id varchar(36) NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
        sample_id varchar(64) NOT NULL,variant_kind varchar(32) NOT NULL,pose_type varchar(32) NOT NULL,
        embedding vector(512) NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(company_id,site_id,model_id,sample_id,variant_kind))""")
    op.execute(
        "CREATE INDEX employee_embeddings_scope ON employee_embeddings(company_id,site_id,model_id,gallery_version)"
    )
    op.execute(
        "CREATE INDEX employee_embeddings_hnsw ON employee_embeddings USING hnsw(embedding vector_cosine_ops)"
    )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TABLE employee_embeddings")
