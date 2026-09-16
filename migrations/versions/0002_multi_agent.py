"""Add immutable rule snapshots and durable per-agent execution traces."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "business_rules",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant", sa.String(80), nullable=False),
        sa.Column("owner_id", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_business_rules_tenant", "business_rules", ["tenant"])
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "task_id", sa.String(32), sa.ForeignKey("analysis_tasks.id"), nullable=False
        ),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("job_attempt", sa.Integer(), nullable=False),
        sa.Column("agent", sa.String(30), nullable=False),
        sa.Column("goal", sa.String(600), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(160), nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("job_id", "job_attempt", "agent"),
    )
    op.create_index("ix_agent_runs_task_id", "agent_runs", ["task_id"])
    op.create_index("ix_agent_runs_job_id", "agent_runs", ["job_id"])


def downgrade():
    raise RuntimeError(
        "Agent audit records must not be dropped; use a verified backup and controlled rollback."
    )
