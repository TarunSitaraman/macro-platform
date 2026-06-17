-- Agent runtime tables for audit and tracing

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
    user_id UUID REFERENCES users(user_id),
    session_id UUID REFERENCES chat_sessions(session_id),
    agent_name VARCHAR(100) NOT NULL,
    query TEXT NOT NULL,
    response TEXT,
    model_used VARCHAR(100),
    confidence VARCHAR(20),
    grounding_warnings JSONB DEFAULT '[]',
    context_record_ids TEXT[],
    status VARCHAR(50) DEFAULT 'running',
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_agent_runs_tenant ON agent_runs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_agent_runs_session ON agent_runs(session_id);
CREATE INDEX IF NOT EXISTS ix_agent_runs_created ON agent_runs(created_at);

CREATE TABLE IF NOT EXISTS agent_steps (
    step_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    step_type VARCHAR(50) NOT NULL,
    payload JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agent_steps_run ON agent_steps(run_id);
