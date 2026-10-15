-- Weekly reporting warehouse. Applied idempotently on every connection.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    notes        TEXT
);

-- One row per entity/platform/run. Re-running the same run upserts.
CREATE TABLE IF NOT EXISTS follower_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    captured_at    TEXT NOT NULL,
    entity_key     TEXT NOT NULL,
    entity_name_ar TEXT NOT NULL,
    entity_name_en TEXT NOT NULL,
    owned          INTEGER NOT NULL DEFAULT 0,
    platform       TEXT NOT NULL,
    handle         TEXT NOT NULL,
    profile_url    TEXT NOT NULL,
    followers      INTEGER,
    error          TEXT,
    raw_json       TEXT,
    UNIQUE (run_id, entity_key, platform)
);

CREATE INDEX IF NOT EXISTS idx_followers_entity
    ON follower_snapshots (entity_key, platform, captured_at);

-- Likes on our own posts. One row per post, updated when it is seen again.
CREATE TABLE IF NOT EXISTS post_engagement (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    entity_key   TEXT NOT NULL,
    platform     TEXT NOT NULL,
    post_url     TEXT NOT NULL,
    published_at TEXT,
    likes        INTEGER NOT NULL DEFAULT 0,
    fetched_at   TEXT NOT NULL,
    UNIQUE (platform, post_url)
);

-- url_hash dedupes the same article arriving from several keywords/sources.
CREATE TABLE IF NOT EXISTS news_articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    url_hash     TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,
    published_at TEXT,
    source       TEXT,
    domain       TEXT,
    keyword      TEXT,
    language     TEXT,
    collector    TEXT,
    snippet      TEXT,
    fetched_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_news_published ON news_articles (published_at DESC);

CREATE TABLE IF NOT EXISTS comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    comment_hash TEXT NOT NULL UNIQUE,
    entity_key   TEXT NOT NULL,
    platform     TEXT NOT NULL,
    native_id    TEXT,
    author       TEXT,
    text         TEXT NOT NULL,
    post_url     TEXT,
    comment_url  TEXT,
    published_at TEXT,
    likes        INTEGER,
    fetched_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comments_published
    ON comments (published_at DESC);

-- Keyed by comment_hash rather than comment id so scores survive re-ingestion
-- and act as a cache that stops us paying the LLM twice for one comment.
CREATE TABLE IF NOT EXISTS sentiments (
    comment_hash TEXT PRIMARY KEY,
    label        TEXT NOT NULL,
    reason_ar    TEXT NOT NULL,
    confidence   REAL,
    model        TEXT,
    analyzed_at  TEXT NOT NULL
);

-- Convenience view used by the dashboard and the email summary.
CREATE VIEW IF NOT EXISTS v_comments_scored AS
SELECT
    c.id,
    c.run_id,
    c.comment_hash,
    c.entity_key,
    c.platform,
    c.author,
    c.text,
    c.post_url,
    c.comment_url,
    c.published_at,
    c.likes,
    c.fetched_at,
    s.label,
    s.reason_ar,
    s.confidence,
    s.model,
    s.analyzed_at
FROM comments c
LEFT JOIN sentiments s ON s.comment_hash = c.comment_hash;
