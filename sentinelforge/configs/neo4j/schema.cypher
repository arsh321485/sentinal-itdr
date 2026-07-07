CREATE CONSTRAINT user_email IF NOT EXISTS FOR (u:User) REQUIRE u.email IS UNIQUE;
CREATE CONSTRAINT device_id IF NOT EXISTS FOR (d:Device) REQUIRE d.device_id IS UNIQUE;
CREATE CONSTRAINT session_id IF NOT EXISTS FOR (s:Session) REQUIRE s.session_id IS UNIQUE;
CREATE CONSTRAINT oauth_app_id IF NOT EXISTS FOR (a:OAuthApp) REQUIRE a.app_id IS UNIQUE;
CREATE INDEX user_tenant IF NOT EXISTS FOR (u:User) ON (u.tenant_id);
CREATE INDEX session_timestamp IF NOT EXISTS FOR (s:Session) ON (s.timestamp);
