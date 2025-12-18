"""Add geocache_image table and backfill from geocache.images

Revision ID: add_geocache_image_table
Revises: add_description_raw
Create Date: 2025-12-18 00:00:00.000000

Crée la table geocache_image (v2) pour stocker les images + métadonnées.
Backfill idempotent depuis la colonne JSON legacy geocache.images.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


# revision identifiers, used by Alembic.
revision = 'add_geocache_image_table'
down_revision = 'add_description_raw'
branch_labels = None
depends_on = None


def _table_exists(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)

    if not _table_exists(inspector, 'geocache_image'):
        op.create_table(
            'geocache_image',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('geocache_id', sa.Integer(), nullable=False, index=True),
            sa.Column('source_url', sa.String(length=2000), nullable=False),
            sa.Column('stored', sa.Boolean(), nullable=True),
            sa.Column('stored_path', sa.String(length=1000), nullable=True),
            sa.Column('mime_type', sa.String(length=100), nullable=True),
            sa.Column('byte_size', sa.Integer(), nullable=True),
            sa.Column('sha256', sa.String(length=64), nullable=True),
            sa.Column('parent_image_id', sa.Integer(), nullable=True),
            sa.Column('derivation_type', sa.String(length=20), nullable=True),
            sa.Column('crop_rect', sa.JSON(), nullable=True),
            sa.Column('title', sa.String(length=255), nullable=True),
            sa.Column('note', sa.Text(), nullable=True),
            sa.Column('tags', sa.JSON(), nullable=True),
            sa.Column('detected_features', sa.JSON(), nullable=True),
            sa.Column('qr_payload', sa.Text(), nullable=True),
            sa.Column('ocr_text', sa.Text(), nullable=True),
            sa.Column('ocr_language', sa.String(length=20), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['geocache_id'], ['geocache.id']),
            sa.ForeignKeyConstraint(['parent_image_id'], ['geocache_image.id']),
            sa.UniqueConstraint(
                'geocache_id',
                'source_url',
                'parent_image_id',
                'derivation_type',
                name='unique_geocache_image_variant',
            ),
        )

        op.create_index(
            'ix_geocache_image_geocache_id',
            'geocache_image',
            ['geocache_id'],
            unique=False,
        )

    inspector = inspect(conn)
    if not _table_exists(inspector, 'geocache'):
        return

    geocache_columns = [col['name'] for col in inspector.get_columns('geocache')]
    if 'images' not in geocache_columns:
        return

    existing = set()
    for row in conn.execute(
        text(
            """
            SELECT geocache_id, source_url, parent_image_id, derivation_type
            FROM geocache_image
            """
        )
    ).fetchall():
        existing.add((row[0], row[1], row[2], row[3]))

    now = datetime.now(timezone.utc)

    geocache_image_table = sa.table(
        'geocache_image',
        sa.column('geocache_id', sa.Integer()),
        sa.column('source_url', sa.String()),
        sa.column('stored', sa.Boolean()),
        sa.column('derivation_type', sa.String()),
        sa.column('created_at', sa.DateTime()),
        sa.column('updated_at', sa.DateTime()),
    )

    to_insert = []
    for geocache_id, images_value in conn.execute(text('SELECT id, images FROM geocache')).fetchall():
        if not images_value:
            continue

        if isinstance(images_value, str):
            try:
                images = json.loads(images_value)
            except Exception:
                continue
        else:
            images = images_value

        if not isinstance(images, list):
            continue

        for item in images:
            if not isinstance(item, dict):
                continue

            url = item.get('url')
            if not url:
                continue

            key = (geocache_id, url, None, 'original')
            if key in existing:
                continue

            to_insert.append(
                {
                    'geocache_id': geocache_id,
                    'source_url': url,
                    'stored': False,
                    'derivation_type': 'original',
                    'created_at': now,
                    'updated_at': now,
                }
            )
            existing.add(key)

    if to_insert:
        op.bulk_insert(geocache_image_table, to_insert)


def downgrade():
    conn = op.get_bind()
    inspector = inspect(conn)

    if not _table_exists(inspector, 'geocache_image'):
        return

    op.drop_index('ix_geocache_image_geocache_id', table_name='geocache_image')
    op.drop_table('geocache_image')
