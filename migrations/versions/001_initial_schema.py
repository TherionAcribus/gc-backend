"""Initial schema - Development

Revision ID: 001_initial_schema
Revises: 
Create Date: 2025-11-02 09:26:00.000000

Cette migration unique crée la table plugins.
Les autres tables (zone, geocache, waypoint) sont créées automatiquement
par db.create_all() dans database.py.

Pour le développement, cette approche simplifiée évite les conflits
entre les migrations Alembic et les lightweight migrations.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import JSON


# revision identifiers, used by Alembic.
revision = '001_initial_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """
    Crée uniquement la table plugins.
    
    Note: Les tables zone, geocache et waypoint sont créées automatiquement
    par SQLAlchemy via db.create_all() dans le fichier database.py.
    """
    # Vérifier si la table existe déjà (pour éviter les erreurs)
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()
    
    if 'plugins' not in existing_tables:
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
            sa.PrimaryKeyConstraint('id')
        )
        
        # Index pour les recherches fréquentes
        op.create_index('ix_plugins_name', 'plugins', ['name'], unique=True)
        op.create_index('ix_plugins_source', 'plugins', ['source'], unique=False)
        op.create_index('ix_plugins_enabled', 'plugins', ['enabled'], unique=False)


def downgrade():
    """
    Supprime la table plugins et ses index.
    """
    op.drop_index('ix_plugins_enabled', table_name='plugins')
    op.drop_index('ix_plugins_source', table_name='plugins')
    op.drop_index('ix_plugins_name', table_name='plugins')
    op.drop_table('plugins')
