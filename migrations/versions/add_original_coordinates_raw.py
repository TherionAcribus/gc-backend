
"""Add original coordinates fields to geocache table

Revision ID: add_original_coordinates_raw
Revises: create_plugins_table
Create Date: 2025-11-15 00:00:00.000000

Ajoute les champs suivants à la table geocache:
- original_latitude
- original_longitude
- original_coordinates_raw

Migration idempotente pour éviter les erreurs si la colonne existe déjà.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_original_coordinates_raw'
down_revision = 'create_plugins_table'
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect

    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'geocache' not in existing_tables:
        return

    geocache_columns = [col['name'] for col in inspector.get_columns('geocache')]

    if 'original_latitude' not in geocache_columns:
        op.add_column('geocache', sa.Column('original_latitude', sa.Float(), nullable=True))

    if 'original_longitude' not in geocache_columns:
        op.add_column('geocache', sa.Column('original_longitude', sa.Float(), nullable=True))

    if 'original_coordinates_raw' not in geocache_columns:
        op.add_column('geocache', sa.Column('original_coordinates_raw', sa.String(length=100), nullable=True))


def downgrade():
    from sqlalchemy import inspect

    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'geocache' not in existing_tables:
        return

    geocache_columns = [col['name'] for col in inspector.get_columns('geocache')]

    if 'original_coordinates_raw' in geocache_columns:
        op.drop_column('geocache', 'original_coordinates_raw')

    if 'original_longitude' in geocache_columns:
        op.drop_column('geocache', 'original_longitude')

    if 'original_latitude' in geocache_columns:
        op.drop_column('geocache', 'original_latitude')
