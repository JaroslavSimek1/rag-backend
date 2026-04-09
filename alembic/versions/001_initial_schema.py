"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sources',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('name', sa.String(), index=True),
        sa.Column('base_url', sa.String()),
        sa.Column('permission_type', sa.String(), server_default='public'),
        sa.Column('crawl_rules', sa.Text(), nullable=True),
        sa.Column('retention_rules', sa.String(), server_default='30_days'),
        sa.Column('schedule_interval', sa.Enum('hourly', 'daily', 'weekly', 'monthly', name='scheduleenum'), nullable=True),
        sa.Column('last_scheduled_ts', sa.DateTime(), nullable=True),
    )

    op.create_table(
        'ingest_jobs',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('source_id', sa.Integer(), sa.ForeignKey('sources.id')),
        sa.Column('url', sa.String(), index=True),
        sa.Column('strategy', sa.Enum('HTML', 'Rendered DOM', 'Screenshot', name='strategyenum'), nullable=True),
        sa.Column('status', sa.Enum('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CAPTCHA_DETECTED', name='statusenum'), server_default='PENDING'),
        sa.Column('started_ts', sa.DateTime()),
        sa.Column('completed_ts', sa.DateTime(), nullable=True),
        sa.Column('max_depth', sa.Integer(), server_default='1'),
        sa.Column('error_code', sa.String(), nullable=True),
        sa.Column('captured_html_path', sa.String(), nullable=True),
    )

    op.create_table(
        'evidences',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('ingest_jobs.id')),
        sa.Column('evidence_type', sa.String()),
        sa.Column('storage_uri', sa.String()),
        sa.Column('file_hash', sa.String()),
        sa.Column('created_ts', sa.DateTime()),
    )


def downgrade() -> None:
    op.drop_table('evidences')
    op.drop_table('ingest_jobs')
    op.drop_table('sources')
