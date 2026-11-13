CREATE EXTENSION IF NOT EXISTS unaccent;

-- unaccent khong IMMUTABLE nen can ban boc de dung trong generated column
CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
  AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$;

CREATE TABLE IF NOT EXISTS tenants (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name           text NOT NULL,
  timezone       text NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
  retention_days int  NOT NULL DEFAULT 150 CHECK (retention_days BETWEEN 1 AND 3650),
  active         boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS devices (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  name          text NOT NULL,
  secret_enc    bytea NOT NULL,
  revoked_at    timestamptz,
  last_seen_at  timestamptz,
  agent_version text,
  state         jsonb NOT NULL DEFAULT '{}',
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
  id          bigserial PRIMARY KEY,
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  device_id   uuid NOT NULL REFERENCES devices(id),
  channel     text NOT NULL CHECK (channel IN ('whatsapp','zalo')),
  external_id text NOT NULL,
  chat_id     text NOT NULL,
  chat_name   text,
  is_group    boolean NOT NULL DEFAULT false,
  sender_id   text,
  sender_name text,
  direction   text NOT NULL DEFAULT 'in' CHECK (direction IN ('in','out')),
  body        text,
  sent_at     timestamptz NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  raw         jsonb,
  tsv         tsvector GENERATED ALWAYS AS (
                to_tsvector('simple', f_unaccent(
                  coalesce(body,'') || ' ' || coalesce(chat_name,'') || ' ' || coalesce(sender_name,'')))
              ) STORED,
  UNIQUE (tenant_id, channel, external_id)
);
CREATE INDEX IF NOT EXISTS messages_tenant_sent ON messages (tenant_id, sent_at DESC);
CREATE INDEX IF NOT EXISTS messages_tsv ON messages USING gin (tsv);

CREATE TABLE IF NOT EXISTS files (
  tenant_id  uuid NOT NULL REFERENCES tenants(id),
  sha256     char(64) NOT NULL,
  size       bigint NOT NULL,
  mime       text,
  path       text NOT NULL,           -- tuong doi so voi STORAGE_DIR
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, sha256)
);

CREATE TABLE IF NOT EXISTS attachments (
  id         bigserial PRIMARY KEY,
  tenant_id  uuid NOT NULL REFERENCES tenants(id),
  message_id bigint NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  file_name  text,
  mime       text,
  size       bigint,
  sha256     char(64),                -- NULL khi agent khong tai duoc file
  status     text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','stored','failed')),
  error      text
);
CREATE INDEX IF NOT EXISTS attachments_message ON attachments (message_id);
CREATE INDEX IF NOT EXISTS attachments_sha ON attachments (tenant_id, sha256);

CREATE TABLE IF NOT EXISTS audit_log (
  id        bigserial PRIMARY KEY,
  at        timestamptz NOT NULL DEFAULT now(),
  tenant_id uuid,
  actor     text NOT NULL,
  action    text NOT NULL,
  detail    jsonb
);

-- Giai doan 2: noi dung da doc tu file PDF/Excel/CSV (worker app/docs.py)
CREATE TABLE IF NOT EXISTS documents (
  tenant_id    uuid NOT NULL,
  sha256       char(64) NOT NULL,
  kind         text,                    -- pdf | xlsx | xls | csv, NULL khi khong nhan dang duoc
  status       text NOT NULL CHECK (status IN ('ok','empty','failed','unsupported')),
  error        text,
  pages        int,
  sheets       jsonb,                   -- [{name, rows}]
  text         text,
  truncated    boolean NOT NULL DEFAULT false,
  doc_type     text,                    -- quote | po | invoice | price_table | other (heuristic)
  hints        jsonb,
  attempts     int NOT NULL DEFAULT 1,
  extracted_at timestamptz NOT NULL DEFAULT now(),
  tsv          tsvector GENERATED ALWAYS AS (
                 to_tsvector('simple', f_unaccent(left(coalesce(text, ''), 200000)))
               ) STORED,
  PRIMARY KEY (tenant_id, sha256),
  FOREIGN KEY (tenant_id, sha256) REFERENCES files(tenant_id, sha256) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS documents_tsv ON documents USING gin (tsv);
CREATE INDEX IF NOT EXISTS documents_type ON documents (tenant_id, doc_type);
CREATE INDEX IF NOT EXISTS messages_chat ON messages (tenant_id, channel, chat_id, sent_at DESC);

-- Giai doan 2b: trang quan tri
ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_type_locked boolean NOT NULL DEFAULT false;

-- Trang thai dang nhap moi nhat tung kenh (agent gui ngay khi doi), kem ma QR
CREATE TABLE IF NOT EXISTS auth_state (
  device_id    uuid NOT NULL REFERENCES devices(id),
  connector    text NOT NULL,
  state        text NOT NULL,
  detail       text,
  account      text,
  qr_kind      text,                    -- text (WhatsApp) | png (Zalo, base64)
  qr_data      text,
  pairing_code text,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (device_id, connector)
);

-- Lenh tu trang quan tri xuong agent (agent poll)
CREATE TABLE IF NOT EXISTS agent_commands (
  id          bigserial PRIMARY KEY,
  device_id   uuid NOT NULL REFERENCES devices(id),
  connector   text NOT NULL,
  action      text NOT NULL,
  args        jsonb,
  status      text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','done','failed','expired')),
  result      text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_commands_pending ON agent_commands (device_id) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS tenant_settings (
  tenant_id  uuid PRIMARY KEY REFERENCES tenants(id),
  work_days  int[] NOT NULL DEFAULT '{1,2,3,4,5,6}',   -- ISO: 1 = thu Hai ... 7 = Chu nhat
  work_start time NOT NULL DEFAULT '08:00',
  work_end   time NOT NULL DEFAULT '17:30',
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Nhan xet bao cao do Claude (qua MCP) hoac nguoi viet
CREATE TABLE IF NOT EXISTS report_notes (
  tenant_id    uuid NOT NULL REFERENCES tenants(id),
  kind         text NOT NULL CHECK (kind IN ('week','month')),
  period_start date NOT NULL,
  text         text NOT NULL,
  author       text NOT NULL,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, kind, period_start)
);
