"""Add original_coordinates_raw field to geocache

Revision ID: add_original_coords_raw
Revises: 8b5f1422b82d
Create Date: 2025-11-01 15:46:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_original_coords_raw'
down_revision = '8b5f1422b82d'
branch_labels = None
depends_on = None


def upgrade():
    """
    Ajoute le champ original_coordinates_raw à la table geocache.
    Ce champ stocke les coordonnées originales au format Geocaching (ex: "N 48° 51.400 E 002° 21.050")
    qui est le format utilisé par les joueurs et pour les énigmes.
    """
    with op.batch_alter_table('geocache', schema=None) as batch_op:
        batch_op.add_column(sa.Column('original_coordinates_raw', sa.String(length=100), nullable=True))


def downgrade():
    """
    Supprime le champ original_coordinates_raw de la table geocache.
    """
    with op.batch_alter_table('geocache', schema=None) as batch_op:
        batch_op.drop_column('original_coordinates_raw')
