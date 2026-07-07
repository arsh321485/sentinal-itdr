-- SentinelForge ITDR PostgreSQL schema

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    m365_enabled BOOLEAN DEFAULT false,
    google_enabled BOOLEAN DEFAULT false,
    notification_webhook TEXT,
    company_domain VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS detection_thresholds (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    rule_name VARCHAR(64) NOT NULL,
    threshold_value DOUBLE PRECISION NOT NULL,
    UNIQUE(tenant_id, rule_name)
);

CREATE TABLE IF NOT EXISTS oauth_whitelist (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    app_id VARCHAR(255) NOT NULL,
    app_name VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, app_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64) NOT NULL,
    alert_id VARCHAR(64) UNIQUE NOT NULL,
    rule_name VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    affected_user VARCHAR(255),
    source VARCHAR(32),
    event_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_tenant_created ON alerts(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);

CREATE TABLE IF NOT EXISTS connector_status (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64) NOT NULL,
    connector_name VARCHAR(64) NOT NULL,
    last_success_at TIMESTAMPTZ,
    last_error TEXT,
    events_processed BIGINT DEFAULT 0,
    status VARCHAR(16) DEFAULT 'unknown',
    UNIQUE(tenant_id, connector_name)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64),
    action VARCHAR(128) NOT NULL,
    actor VARCHAR(255),
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO tenants (tenant_id, name, m365_enabled, google_enabled, company_domain)
VALUES ('default', 'Default Tenant', false, false, 'example.com')
ON CONFLICT (tenant_id) DO NOTHING;

INSERT INTO detection_thresholds (tenant_id, rule_name, threshold_value) VALUES
    ('default', 'impossible_travel_speed_kmh', 900),
    ('default', 'mfa_fatigue_push_count', 10),
    ('default', 'token_theft_window_minutes', 30)
ON CONFLICT (tenant_id, rule_name) DO NOTHING;
