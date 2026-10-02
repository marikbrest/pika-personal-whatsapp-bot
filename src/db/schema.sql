-- See PRD.md section 4 for a full explanation of every table

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    whatsapp_number TEXT UNIQUE NOT NULL,
    display_name TEXT,
    timezone TEXT DEFAULT 'Asia/Jerusalem',
    preferred_tone TEXT DEFAULT 'neutral',
    is_active BOOLEAN DEFAULT 1,
    is_admin BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL,
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT NOT NULL,
    scope TEXT NOT NULL,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    direction TEXT NOT NULL,
    message_type TEXT NOT NULL,
    raw_content TEXT,
    parsed_intent TEXT,
    whatsapp_message_id TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    whatsapp_number TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_user_id, name)
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    content TEXT NOT NULL,
    schedule_type TEXT NOT NULL,
    schedule_time TEXT NOT NULL,
    schedule_days TEXT,
    next_trigger_at TIMESTAMP NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    recipient_contact_id INTEGER REFERENCES contacts(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    list_name TEXT DEFAULT 'default',
    content TEXT NOT NULL,
    is_done BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS saved_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    original_url TEXT NOT NULL,
    title TEXT,
    content_snapshot TEXT,
    content_type TEXT,
    fetch_status TEXT DEFAULT 'pending',
    embedding BLOB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS email_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    to_address TEXT NOT NULL,
    subject TEXT,
    body TEXT NOT NULL,
    status TEXT DEFAULT 'pending_approval',
    original_thread_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_profile_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    fact_key TEXT NOT NULL,
    fact_value TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- One value per key per user, so restating a fact updates it instead of
-- accumulating duplicates. A unique index rather than a table constraint,
-- because SQLite cannot add a constraint to an existing table via ALTER.
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_profile_facts_user_key
    ON user_profile_facts(user_id, fact_key);
