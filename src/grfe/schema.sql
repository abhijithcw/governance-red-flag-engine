-- governance-red-flag-engine — SQLite schema (v1)
-- Canonical company key: ISIN. All timestamps ISO-8601, IST (Asia/Kolkata).
-- Other projects on the Pi read this DB: table/column names are STABLE — add columns, never rename.

PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 30000;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- 0. Housekeeping
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job          TEXT NOT NULL,            -- 'daily' | 'backfill' | 'company_master' | 'score' | 'site'
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    status       TEXT NOT NULL DEFAULT 'running',  -- running | ok | failed | partial
    rows_added   INTEGER NOT NULL DEFAULT 0,
    rows_updated INTEGER NOT NULL DEFAULT 0,
    errors       INTEGER NOT NULL DEFAULT 0,
    notes        TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_job_status ON runs(job, status, ended_at);

-- Resume checkpoints: one row per (job, unit). Re-runs skip units marked done.
CREATE TABLE IF NOT EXISTS checkpoints (
    job         TEXT NOT NULL,
    unit        TEXT NOT NULL,             -- e.g. 'shp:INE009A01021:2025Q2', 'ann:nse:2026-11-09'
    status      TEXT NOT NULL,             -- done | failed | skipped
    updated_at  TEXT NOT NULL,
    detail      TEXT,
    PRIMARY KEY (job, unit)
);

-- Raw HTTP cache index (bodies live on disk under /mnt/hdd/.../cache/<sha256[:2]>/<sha256>)
CREATE TABLE IF NOT EXISTS http_cache (
    url_hash     TEXT PRIMARY KEY,         -- sha256(method + url + sorted params)
    url          TEXT NOT NULL,
    fetched_at   TEXT NOT NULL,
    status_code  INTEGER NOT NULL,
    content_hash TEXT,                     -- sha256 of body; file path derived from it
    bytes        INTEGER
);

-- ---------------------------------------------------------------------------
-- 1. Company master (ISIN canonical)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS company_master (
    isin          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    nse_symbol    TEXT,
    nse_series    TEXT,                    -- EQ | BE | ...
    bse_scrip     TEXT,
    bse_group     TEXT,
    cin           TEXT,
    sector        TEXT,
    listing_date  TEXT,
    face_value    REAL,
    status        TEXT NOT NULL DEFAULT 'active',   -- active | suspended | delisted | cirp
    cirp_since    TEXT,                    -- set when CIRP/IBC admission announced
    source_nse_at TEXT,
    source_bse_at TEXT,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cm_symbol ON company_master(nse_symbol);
CREATE INDEX IF NOT EXISTS ix_cm_bse    ON company_master(bse_scrip);
CREATE INDEX IF NOT EXISTS ix_cm_cin    ON company_master(cin);

-- ISIN changes (corporate actions). Resolve old_isin -> new_isin when joining history.
CREATE TABLE IF NOT EXISTS isin_history (
    old_isin      TEXT NOT NULL,
    new_isin      TEXT NOT NULL,
    effective_date TEXT,
    reason        TEXT,
    source_url    TEXT,
    PRIMARY KEY (old_isin, new_isin)
);

-- ---------------------------------------------------------------------------
-- 2. Announcements (LODR Reg 30) — NSE + BSE
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS announcements (
    exchange        TEXT NOT NULL,         -- NSE | BSE
    exch_ann_id     TEXT NOT NULL,         -- exchange's own id (NSE seq_id / BSE NEWSID)
    isin            TEXT,
    symbol          TEXT,                  -- as given by exchange
    category        TEXT,                  -- exchange category / "desc"
    subcategory     TEXT,
    subject         TEXT,
    body_excerpt    TEXT,                  -- first ~2k chars of exchange-provided text
    attachment_url  TEXT,
    attachment_hash TEXT,                  -- sha256 of downloaded PDF (null if not fetched)
    announced_at    TEXT NOT NULL,         -- exchange timestamp (IST)
    ingested_at     TEXT NOT NULL,
    PRIMARY KEY (exchange, exch_ann_id)
);
CREATE INDEX IF NOT EXISTS ix_ann_isin_date ON announcements(isin, announced_at DESC);
CREATE INDEX IF NOT EXISTS ix_ann_date      ON announcements(announced_at DESC);
CREATE INDEX IF NOT EXISTS ix_ann_cat       ON announcements(category, announced_at DESC);

-- Classified announcement events (auditor changes, results, CIRP, etc.) — one row per detected event.
CREATE TABLE IF NOT EXISTS announcement_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange        TEXT NOT NULL,
    exch_ann_id     TEXT NOT NULL,
    isin            TEXT NOT NULL,
    event_type      TEXT NOT NULL,         -- auditor_resignation | auditor_appointment | auditor_rotation
                                           -- | results_filed | cirp_admission | other
    event_date      TEXT NOT NULL,
    auditor_kind    TEXT,                  -- statutory | secretarial | internal | cost (only statutory scored)
    mid_term        INTEGER,               -- 1/0/NULL
    reason_text     TEXT,                  -- item 7A reasons (resignation)
    period_end      TEXT,                  -- for results_filed: quarter/year end date
    period_kind     TEXT,                  -- quarterly | annual
    confidence      REAL,                  -- classifier confidence 0-1
    detail_json     TEXT,
    UNIQUE (exchange, exch_ann_id, event_type),
    FOREIGN KEY (exchange, exch_ann_id) REFERENCES announcements(exchange, exch_ann_id)
);
CREATE INDEX IF NOT EXISTS ix_ae_isin_type ON announcement_events(isin, event_type, event_date DESC);

-- ---------------------------------------------------------------------------
-- 3. Shareholding pattern (LODR Reg 31, quarterly, from XBRL)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shp_summary (
    isin               TEXT NOT NULL,
    period_end         TEXT NOT NULL,      -- quarter end, YYYY-MM-DD
    exchange           TEXT NOT NULL,      -- source exchange of the XBRL
    total_shares       INTEGER,
    promoter_shares    INTEGER,
    promoter_pct       REAL,
    promoter_pledged_shares INTEGER,
    promoter_pledged_pct_of_holding REAL,  -- pledged / promoter_shares * 100
    promoter_pledged_pct_of_capital REAL,  -- pledged / total_shares * 100
    fpi_pct            REAL,
    dii_pct            REAL,               -- MF + insurance + banks/FI + AIF + other institutions (domestic)
    mf_pct             REAL,
    public_pct         REAL,
    filed_at           TEXT,
    source_url         TEXT,
    source_hash        TEXT,               -- sha256 of XBRL file
    ingested_at        TEXT NOT NULL,
    PRIMARY KEY (isin, period_end)
);
CREATE INDEX IF NOT EXISTS ix_shp_period ON shp_summary(period_end DESC);

-- Named holders (Table II promoter group + Table III holders ≥1%)
CREATE TABLE IF NOT EXISTS shp_holders (
    isin          TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    table_no      TEXT NOT NULL,           -- II | III
    category      TEXT NOT NULL,           -- promoter | promoter_group | fpi | mf | insurance | bank_fi | aif | other_dii | public_individual | body_corporate | other
    holder_name   TEXT NOT NULL,
    holder_key    TEXT NOT NULL,           -- normalised name (upper, stripped punctuation/suffixes) for cross-quarter matching
    shares        INTEGER,
    pct           REAL,
    pledged_shares INTEGER,
    pledged_pct   REAL,
    PRIMARY KEY (isin, period_end, table_no, holder_key)
);
CREATE INDEX IF NOT EXISTS ix_shph_key ON shp_holders(isin, holder_key, period_end);

-- ---------------------------------------------------------------------------
-- 4. Pledge / encumbrance disclosures (SAST Reg 31(1)/(2))
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pledge_disclosures (
    exchange         TEXT NOT NULL,
    disc_id          TEXT NOT NULL,        -- exchange row id, or sha256 of (isin,promoter,date,event,shares) if none
    isin             TEXT NOT NULL,
    promoter_name    TEXT,
    event_type       TEXT,                 -- creation | release | invocation
    shares           INTEGER,
    pct_of_capital   REAL,
    event_date       TEXT,
    disclosed_at     TEXT,
    reason_text      TEXT,                 -- Annexure-II reasons when present
    source_url       TEXT,
    ingested_at      TEXT NOT NULL,
    PRIMARY KEY (exchange, disc_id)
);
CREATE INDEX IF NOT EXISTS ix_pledge_isin ON pledge_disclosures(isin, event_date DESC);

-- ---------------------------------------------------------------------------
-- 5. Insider trades (PIT Reg 7(2))
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS insider_trades (
    exchange        TEXT NOT NULL,
    disc_id         TEXT NOT NULL,         -- exchange row id, else sha256 of natural fields
    isin            TEXT NOT NULL,
    person_name     TEXT NOT NULL,
    person_key      TEXT NOT NULL,         -- normalised name
    person_category TEXT,                  -- Promoter | Promoter Group | Director | KMP | Designated Person | Immediate Relative | ...
    security_type   TEXT,
    txn_type        TEXT NOT NULL,         -- Buy | Sell | Pledge | Revoke | Invoke | ...
    mode            TEXT,                  -- Market Purchase | Market Sale | Off Market | ESOP | Gift | Inter-se | ...
    quantity        INTEGER,
    value_inr       REAL,
    shares_before   INTEGER,
    pct_before      REAL,
    shares_after    INTEGER,
    pct_after       REAL,
    trade_from      TEXT,
    trade_to        TEXT,
    intimated_at    TEXT,
    source_url      TEXT,
    ingested_at     TEXT NOT NULL,
    PRIMARY KEY (exchange, disc_id)
);
CREATE INDEX IF NOT EXISTS ix_it_isin_date ON insider_trades(isin, trade_to DESC);
CREATE INDEX IF NOT EXISTS ix_it_mode      ON insider_trades(isin, mode, trade_to);

-- ---------------------------------------------------------------------------
-- 6. Related party transactions (LODR Reg 23(9), half-yearly, XBRL) — line-item level
--    STABLE TABLE: reused by other builds.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpt_filings (
    isin          TEXT NOT NULL,
    period_end    TEXT NOT NULL,           -- half-year end (Sep 30 / Mar 31)
    exchange      TEXT NOT NULL,
    basis         TEXT NOT NULL DEFAULT 'consolidated',  -- consolidated | standalone
    filed_at      TEXT,
    source_url    TEXT,
    source_hash   TEXT,
    total_rpt_value_inr REAL,              -- sum of line items (as filed, INR)
    ingested_at   TEXT NOT NULL,
    PRIMARY KEY (isin, period_end, basis)
);

CREATE TABLE IF NOT EXISTS rpt_line_items (
    line_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    isin                 TEXT NOT NULL,
    period_end           TEXT NOT NULL,
    basis                TEXT NOT NULL DEFAULT 'consolidated',
    sr_no                INTEGER,
    counterparty_name    TEXT NOT NULL,
    counterparty_key     TEXT NOT NULL,    -- normalised
    counterparty_pan_masked TEXT,
    relationship         TEXT,             -- as filed (e.g. "Wholly owned subsidiary", "Promoter", "KMP")
    is_wos               INTEGER NOT NULL DEFAULT 0,  -- wholly-owned subsidiary (Reg 23(5) exempt) → excluded from spike ratio
    transaction_type     TEXT,             -- Sale of goods / Loans given / Remuneration / ...
    approved_value_inr   REAL,
    value_inr            REAL,             -- value during the period
    period_start         TEXT,
    detail_json          TEXT,             -- any extra XBRL fields (loan tenure, rate, secured, purpose...)
    UNIQUE (isin, period_end, basis, sr_no, counterparty_key, transaction_type),
    FOREIGN KEY (isin, period_end, basis) REFERENCES rpt_filings(isin, period_end, basis)
);
CREATE INDEX IF NOT EXISTS ix_rpt_isin_period ON rpt_line_items(isin, period_end DESC);
CREATE INDEX IF NOT EXISTS ix_rpt_cp          ON rpt_line_items(counterparty_key);

-- ---------------------------------------------------------------------------
-- 7. Financial results — filing dates + revenue (for RPT ratio and delay flag)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS results_filings (
    isin          TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    period_kind   TEXT NOT NULL,           -- quarterly | annual
    exchange      TEXT NOT NULL,
    filed_at      TEXT NOT NULL,
    due_date      TEXT NOT NULL,           -- computed from Reg 33(3) + any relaxation in config
    days_late     INTEGER NOT NULL DEFAULT 0,
    revenue_inr   REAL,                    -- revenue from operations (consolidated preferred), from XBRL when available
    basis         TEXT,                    -- consolidated | standalone
    source_url    TEXT,
    ingested_at   TEXT NOT NULL,
    PRIMARY KEY (isin, period_end, period_kind)
);
CREATE INDEX IF NOT EXISTS ix_rf_late ON results_filings(days_late DESC, period_end DESC);

-- ---------------------------------------------------------------------------
-- 8. Scoring output (regenerated each run; history kept for trend)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS flags (
    flag_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL,
    isin          TEXT NOT NULL,
    flag_code     TEXT NOT NULL,           -- see config/scoring.yaml
    severity      TEXT NOT NULL,           -- info | low | medium | high
    weight        REAL NOT NULL,
    as_of         TEXT NOT NULL,           -- date of the underlying data point
    period_end    TEXT,
    metric_value  REAL,
    threshold     REAL,
    summary       TEXT NOT NULL,           -- one-line human text for dashboard
    evidence_json TEXT NOT NULL,           -- [{label, value, source_url, as_of}, ...]
    first_seen_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS ix_flags_run_isin ON flags(run_id, isin);
CREATE INDEX IF NOT EXISTS ix_flags_isin     ON flags(isin, as_of DESC);

CREATE TABLE IF NOT EXISTS scores (
    run_id        INTEGER NOT NULL,
    isin          TEXT NOT NULL,
    score         REAL NOT NULL,
    flag_count    INTEGER NOT NULL,
    high_count    INTEGER NOT NULL,
    label         TEXT,                    -- e.g. 'CIRP' overrides ranking display
    computed_at   TEXT NOT NULL,
    PRIMARY KEY (run_id, isin),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS ix_scores_run_score ON scores(run_id, score DESC);

-- Latest successful score run (dashboard reads this view)
CREATE VIEW IF NOT EXISTS v_latest_scores AS
SELECT s.* FROM scores s
WHERE s.run_id = (SELECT MAX(run_id) FROM runs WHERE job='score' AND status='ok');

INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (1, strftime('%Y-%m-%dT%H:%M:%S','now'));
