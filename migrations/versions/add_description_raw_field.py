"""Add description_raw field to geocache table

Revision ID: add_description_raw
Revises: add_original_coordinates_raw
Create Date: 2025-11-17 00:00:00.000000

Ajoute le champ description_raw à la table geocache pour stocker
la description sans le HTML, facilitant les recherches de texte.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_description_raw'
down_revision = 'add_original_coordinates_raw'
branch_labels = None
depends_on = None


def upgrade():
    """
    Ajoute la colonne description_raw à la table geocache.
    """
    # Vérifier si la colonne existe déjà (pour éviter les erreurs)
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    geocache_columns = [col['name'] for col in inspector.get_columns('geocache')]

    if 'description_raw' not in geocache_columns:
        op.add_column('geocache', sa.Column('description_raw', sa.Text(), nullable=True))


def downgrade():
    """
    Supprime la colonne description_raw de la table geocache.
    """
    op.drop_column('geocache', 'description_raw')

