"""Add solved status field to geocache

Revision ID: add_solved_status
Revises: add_original_coords_raw
Create Date: 2025-11-01 16:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_solved_status'
down_revision = 'add_original_coords_raw'
branch_labels = None
depends_on = None


def upgrade():
    """
    Ajoute le champ solved à la table geocache.
    Ce champ indique le statut de résolution de l'énigme : not_solved, in_progress, solved
    """
    with op.batch_alter_table('geocache', schema=None) as batch_op:
        batch_op.add_column(sa.Column('solved', sa.String(length=20), nullable=True, server_default='not_solved'))


def downgrade():
    """
    Supprime le champ solved de la table geocache.
    """
    with op.batch_alter_table('geocache', schema=None) as batch_op:
        batch_op.drop_column('solved')
