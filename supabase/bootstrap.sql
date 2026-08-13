-- One-time bootstrap for Zalo assistant tables (run in Supabase SQL editor if ensure_tables cannot DDL).
CREATE TABLE IF NOT EXISTS zalo_session (
    id text PRIMARY KEY,
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS message_logs (
    id bigserial PRIMARY KEY,
    group_id text NOT NULL,
    sender_id text NOT NULL DEFAULT '',
    sender_name text NOT NULL DEFAULT '',
    gender text NOT NULL DEFAULT 'unknown',
    text text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION ensure_zalo_tables()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    CREATE TABLE IF NOT EXISTS zalo_session (
        id text PRIMARY KEY,
        payload jsonb NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS message_logs (
        id bigserial PRIMARY KEY,
        group_id text NOT NULL,
        sender_id text NOT NULL DEFAULT '',
        sender_name text NOT NULL DEFAULT '',
        gender text NOT NULL DEFAULT 'unknown',
        text text NOT NULL DEFAULT '',
        created_at timestamptz NOT NULL DEFAULT now()
    );
END;
$$;
