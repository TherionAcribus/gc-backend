"""Add resolution_diagnostics field to solved_geocache_archive

Revision ID: add_archive_resolution_diagnostics
Revises: add_geocache_image_editor_state
Create Date: 2026-03-22 00:00:00.000000

Ajoute un champ texte pour stocker un snapshot JSON des diagnostics
de resolution emis par les workflows assistant / plugin executor.
Migration idempotente pour eviter les erreurs si la colonne existe deja.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_archive_resolution_diagnostics'
down_revision = 'add_geocache_image_editor_state'
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect

    conn = op.get_bind()
    inspector = inspect(conn)

    if 'solved_geocache_archive' not in inspector.get_table_names():
        return

    archive_columns = [col['name'] for col in inspector.get_columns('solved_geocache_archive')]

    if 'resolution_diagnostics' not in archive_columns:
        op.add_column('solved_geocache_archive', sa.Column('resolution_diagnostics', sa.Text(), nullable=True))


def downgrade():
    from sqlalchemy import inspect

    conn = op.get_bind()
    inspector = inspect(conn)

    if 'solved_geocache_archive' not in inspector.get_table_names():
        return

    archive_columns = [col['name'] for col in inspector.get_columns('solved_geocache_archive')]

    if 'resolution_diagnostics' in archive_columns:
        op.drop_column('solved_geocache_archive', 'resolution_diagnostics')
