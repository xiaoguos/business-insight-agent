"""Initial immutable PostgreSQL schema. Generated 2026-09-16."""
from alembic import op
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None
STATEMENTS = [
  "CREATE EXTENSION IF NOT EXISTS vector",
  "CREATE TABLE analysis_tasks (\n\tid VARCHAR(32) NOT NULL, \n\ttenant VARCHAR(80) NOT NULL, \n\towner_id VARCHAR(32) NOT NULL, \n\trequest_key VARCHAR(100) NOT NULL, \n\tpayload JSON NOT NULL, \n\tjob_id VARCHAR(32) NOT NULL, \n\tstate VARCHAR(30) NOT NULL, \n\tresult JSON NOT NULL, \n\treport_hash VARCHAR(64) NOT NULL, \n\treviewer_id VARCHAR(32), \n\treview_note TEXT NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (tenant, owner_id, request_key)\n)",
  "CREATE INDEX ix_analysis_tasks_owner_id ON analysis_tasks (owner_id)",
  "CREATE INDEX ix_analysis_tasks_tenant ON analysis_tasks (tenant)",
  "CREATE TABLE audit_events (\n\tid VARCHAR(32) NOT NULL, \n\ttenant VARCHAR(80) NOT NULL, \n\tuser_id VARCHAR(32) NOT NULL, \n\taction VARCHAR(80) NOT NULL, \n\tresource_id VARCHAR(100) NOT NULL, \n\tdetail JSON NOT NULL, \n\tat TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id)\n)",
  "CREATE INDEX ix_audit_events_at ON audit_events (at)",
  "CREATE INDEX ix_audit_events_tenant ON audit_events (tenant)",
  "CREATE TABLE datasets (\n\tid VARCHAR(32) NOT NULL, \n\ttenant VARCHAR(80) NOT NULL, \n\towner_id VARCHAR(32) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tdigest VARCHAR(64) NOT NULL, \n\trow_count INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id)\n)",
  "CREATE INDEX ix_datasets_tenant ON datasets (tenant)",
  "CREATE TABLE jobs (\n\tid VARCHAR(32) NOT NULL, \n\ttenant VARCHAR(80) NOT NULL, \n\towner_id VARCHAR(32) NOT NULL, \n\tkind VARCHAR(40) NOT NULL, \n\tpayload JSON NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\tattempts INTEGER NOT NULL, \n\tlease_token VARCHAR(32), \n\tlease_until TIMESTAMP WITH TIME ZONE, \n\terror TEXT NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id)\n)",
  "CREATE INDEX ix_jobs_owner_id ON jobs (owner_id)",
  "CREATE INDEX ix_jobs_state ON jobs (state)",
  "CREATE INDEX ix_jobs_tenant ON jobs (tenant)",
  "CREATE TABLE login_attempts (\n\tid VARCHAR(32) NOT NULL, \n\tidentity VARCHAR(64) NOT NULL, \n\tat TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id)\n)",
  "CREATE INDEX ix_login_attempts_at ON login_attempts (at)",
  "CREATE INDEX ix_login_attempts_identity ON login_attempts (identity)",
  "CREATE TABLE login_sessions (\n\ttoken_hash VARCHAR(64) NOT NULL, \n\tuser_id VARCHAR(32) NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (token_hash)\n)",
  "CREATE INDEX ix_login_sessions_expires_at ON login_sessions (expires_at)",
  "CREATE INDEX ix_login_sessions_user_id ON login_sessions (user_id)",
  "CREATE TABLE users (\n\tid VARCHAR(32) NOT NULL, \n\ttenant VARCHAR(80) NOT NULL, \n\temail VARCHAR(254) NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tpassword_hash TEXT NOT NULL, \n\trole VARCHAR(20) NOT NULL, \n\tdepartments JSON NOT NULL, \n\tactive BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (email)\n)",
  "CREATE INDEX ix_users_tenant ON users (tenant)",
  "CREATE TABLE worker_heartbeats (\n\tid VARCHAR(32) NOT NULL, \n\tat TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id)\n)",
  "CREATE TABLE notifications (\n\tid VARCHAR(32) NOT NULL, \n\ttask_id VARCHAR(32) NOT NULL, \n\ttenant VARCHAR(80) NOT NULL, \n\trecipient_id VARCHAR(32) NOT NULL, \n\treport_hash VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (task_id), \n\tFOREIGN KEY(task_id) REFERENCES analysis_tasks (id)\n)",
  "CREATE INDEX ix_notifications_recipient_id ON notifications (recipient_id)",
  "CREATE INDEX ix_notifications_tenant ON notifications (tenant)",
  "CREATE TABLE orders (\n\tid VARCHAR(32) NOT NULL, \n\tdataset_id VARCHAR(32) NOT NULL, \n\torder_id VARCHAR(100) NOT NULL, \n\tordered_at DATE NOT NULL, \n\tchannel VARCHAR(80) NOT NULL, \n\tproduct VARCHAR(80) NOT NULL, \n\trefunded INTEGER NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (dataset_id, order_id), \n\tFOREIGN KEY(dataset_id) REFERENCES datasets (id)\n)",
  "CREATE INDEX ix_orders_dataset_id ON orders (dataset_id)",
  "CREATE INDEX ix_orders_ordered_at ON orders (ordered_at)"
]
TABLES = ["orders","notifications","worker_heartbeats","users","login_sessions","login_attempts","jobs","datasets","audit_events","analysis_tasks"]
def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)
def downgrade():
    raise RuntimeError("Destructive downgrade is disabled. Restore a verified backup into a new database.")
