"""Add editor_state_json field to geocache_image

Revision ID: add_geocache_image_editor_state
Revises: add_geocache_image_table
Create Date: 2025-12-20 00:00:00.000000

Ajoute un champ texte pour stocker l'état Fabric.js (JSON) afin de ré-ouvrir l'éditeur.
Migration idempotente pour éviter les erreurs si la colonne existe déjà.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_geocache_image_editor_state'
down_revision = 'add_geocache_image_table'
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect

    conn = op.get_bind()
    inspector = inspect(conn)

    if 'geocache_image' not in inspector.get_table_names():
        return

    columns = [col['name'] for col in inspector.get_columns('geocache_image')]

    if 'editor_state_json' not in columns:
        op.add_column('geocache_image', sa.Column('editor_state_json', sa.Text(), nullable=True))


def downgrade():
    from sqlalchemy import inspect

    conn = op.get_bind()
    inspector = inspect(conn)

    if 'geocache_image' not in inspector.get_table_names():
        return

    columns = [col['name'] for col in inspector.get_columns('geocache_image')]

    if 'editor_state_json' in columns:
        op.drop_column('geocache_image', 'editor_state_json')
