
"""Create plugins table (legacy)

Revision ID: create_plugins_table
Revises: 001_initial_schema
Create Date: 2025-11-10 00:00:00.000000

Migration de compatibilité.
Certaines installations ont un historique Alembic qui référence cette révision.
Elle est idempotente et ne crée la table `plugins` que si elle n'existe pas.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON


# revision identifiers, used by Alembic.
revision = 'create_plugins_table'
down_revision = '001_initial_schema'
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect

    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'plugins' in existing_tables:
        return

    op.create_table(
        'plugins',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('version', sa.String(length=32), nullable=False),
        sa.Column('plugin_api_version', sa.String(length=16), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('author', sa.String(length=128), nullable=True),
        sa.Column('plugin_type', sa.String(length=32), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('path', sa.String(length=512), nullable=False),
        sa.Column('entry_point', sa.String(length=256), nullable=True),
        sa.Column('categories', JSON(), nullable=True),
        sa.Column('input_types', JSON(), nullable=True),
        sa.Column('heavy_cpu', sa.Boolean(), nullable=True),
        sa.Column('needs_network', sa.Boolean(), nullable=True),
        sa.Column('needs_filesystem', sa.Boolean(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_index('ix_plugins_name', 'plugins', ['name'], unique=True)
    op.create_index('ix_plugins_source', 'plugins', ['source'], unique=False)
    op.create_index('ix_plugins_enabled', 'plugins', ['enabled'], unique=False)


def downgrade():
    from sqlalchemy import inspect

    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'plugins' not in existing_tables:
        return

    op.drop_index('ix_plugins_enabled', table_name='plugins')
    op.drop_index('ix_plugins_source', table_name='plugins')
    op.drop_index('ix_plugins_name', table_name='plugins')
    op.drop_table('plugins')
