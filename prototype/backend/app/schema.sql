-- LeakWatch backend schema -- contracts/INTERFACES.md §9.
--
-- Applied idempotently at every startup (CREATE ... IF NOT EXISTS), so there is
-- no migration tool in the prototype. What this deliberately does NOT do:
--   * no ORM, no migration framework, no ALTER-based versioning. A prototype
--     that changes a column changes this file and recreates the DB file.
--   * no retention/rollup policy. 08-roadmap.md §3 P0 requires "raw retention
--     from day one", so uplinks.raw_b64 keeps the on-air bytes forever here.
--
-- The five tables are exactly the ones named in contract §9. Install-probe
-- results live as columns on `devices` rather than in a sixth table, precisely
-- so the table list does not deviate from the contract.

PRAGMA journal_mode = WAL;

-- ---------------------------------------------------------------- devices ---
CREATE TABLE IF NOT EXISTS devices (
    dev_eui                TEXT PRIMARY KEY,            -- canonical identity: the LoRaWAN DevEUI
    device_id              TEXT NOT NULL UNIQUE,        -- TTS end_device_ids.device_id, e.g. "leak-0001"

    -- Shadow mode. Default 0 (false) is standing rule 3 of 08-roadmap.md §0:
    -- no customer-visible alert until the nuisance rate is measured.
    alerting_enabled       INTEGER NOT NULL DEFAULT 0,
    alerting_changed_ts    INTEGER,
    alerting_change_reason TEXT,

    first_seen_ts          INTEGER,
    last_seen_ts           INTEGER,
    last_f_cnt             INTEGER,
    uplink_count           INTEGER NOT NULL DEFAULT 0,

    -- last decoded sample, denormalised for the fleet table (contract §8 GET /devices)
    last_pipe_c            REAL,
    last_ambient_c         REAL,
    last_delta_c           REAL,
    last_battery_mv        INTEGER,
    last_rssi              INTEGER,
    last_snr               REAL,
    install_verified       INTEGER NOT NULL DEFAULT 0,
    tamper                 INTEGER NOT NULL DEFAULT 0,
    cold_warning           INTEGER NOT NULL DEFAULT 0,
    last_verdict           TEXT,
    last_confidence        INTEGER,
    last_reason            TEXT,
    last_baseline_c        REAL,

    -- start of the current non-"quiet" run of verdicts; NULL while quiet.
    -- Used for detection latency (see app/insurer.py for why this is NOT
    -- called time-to-detection against physical event onset).
    anomaly_episode_start_ts INTEGER,

    -- Valve arming, from 05-open-source-stack.md §5.3 arm_or_refuse().
    -- NULL probe fields mean "never probed" => not armed => POST /valve is 409.
    install_probe_ts       INTEGER,
    breakaway_torque_mnm   REAL,
    breakaway_torque_ok    INTEGER,
    stem_leak              INTEGER,
    cycle_time_ms          INTEGER,
    probe_note             TEXT,

    valve_state            TEXT NOT NULL DEFAULT 'unknown',  -- unknown|open|closed|refused|fault
    created_ts             INTEGER NOT NULL,
    updated_ts             INTEGER NOT NULL
);

-- ---------------------------------------------------------------- uplinks ---
CREATE TABLE IF NOT EXISTS uplinks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dev_eui       TEXT NOT NULL,
    device_id     TEXT,
    f_cnt         INTEGER NOT NULL,
    f_port        INTEGER,
    received_at   TEXT,                 -- as supplied by the LNS, verbatim
    received_ts   INTEGER NOT NULL,     -- parsed unix seconds
    raw_b64       TEXT NOT NULL,        -- the on-air bytes, retained (P0 raw retention)

    decode_ok     INTEGER NOT NULL DEFAULT 0,
    decode_error  TEXT,
    pipe_c        REAL,
    ambient_c     REAL,
    delta_c       REAL,
    battery_mv    INTEGER,
    install_verified INTEGER,
    tamper        INTEGER,
    cold_warning  INTEGER,
    uplink_reason TEXT,

    rssi          INTEGER,
    snr           REAL,
    gateway_id    TEXT,
    spreading_factor INTEGER,

    verdict       TEXT,
    confidence    INTEGER,
    reason        TEXT,
    baseline_c    REAL,
    detector_error TEXT,

    created_ts    INTEGER NOT NULL,

    -- This is what makes ingest idempotent (contract §4, §9).
    UNIQUE (dev_eui, f_cnt)
);
CREATE INDEX IF NOT EXISTS idx_uplinks_dev_ts ON uplinks (dev_eui, received_ts);

-- ----------------------------------------------------------------- alerts ---
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dev_eui         TEXT NOT NULL,
    device_id       TEXT,
    verdict         TEXT NOT NULL,            -- leak_suspected | freeze_risk
    status          TEXT NOT NULL,            -- shadow | open | acked | nuisance
    -- 1 if the device was in shadow mode when this alert was raised. Kept
    -- separate from `status` so an acked shadow alert still counts towards the
    -- shadow-mode nuisance rate (contract §7, roadmap P1 gate).
    raised_in_shadow INTEGER NOT NULL,
    confidence      INTEGER,
    reason          TEXT,
    delta_c         REAL,
    baseline_c      REAL,

    raised_ts       INTEGER NOT NULL,
    raised_uplink_id INTEGER,
    first_anomalous_ts INTEGER,
    detection_latency_s INTEGER,
    last_uplink_ts  INTEGER,
    uplink_count    INTEGER NOT NULL DEFAULT 1,

    notified_ts     INTEGER,                  -- NULL for every shadow alert, always
    notify_channel  TEXT,

    acked_ts        INTEGER,
    acked_by        TEXT,
    was_nuisance    INTEGER,
    note            TEXT,

    created_ts      INTEGER NOT NULL,
    FOREIGN KEY (dev_eui) REFERENCES devices (dev_eui)
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts (status);
CREATE INDEX IF NOT EXISTS idx_alerts_dev ON alerts (dev_eui, verdict, status);

-- --------------------------------------------------------- valve_commands ---
CREATE TABLE IF NOT EXISTS valve_commands (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dev_eui       TEXT NOT NULL,
    device_id     TEXT,
    command       TEXT NOT NULL,              -- close | open
    -- Machine-readable refusal reason (no_install_probe,
    -- breakaway_torque_above_threshold, stem_leak_detected,
    -- cycle_time_above_timeout). NULL when the command was not refused.
    -- Separate from `reason` so 08-roadmap.md §8 risk 9's leading indicator can
    -- be grouped without string-matching an English sentence.
    reason_code   TEXT,
    -- refused: the arming check said no and nothing was sent.
    -- pending:  downlink queued, waiting for a confirming uplink.
    -- confirmed: a confirming uplink arrived inside the window.
    -- unconfirmed: the window closed with no confirming uplink. NEVER "success".
    status        TEXT NOT NULL,
    reason        TEXT NOT NULL,
    token         INTEGER,                    -- contract §2 uint16 echo token
    f_port        INTEGER,
    payload_b64   TEXT,                       -- the 4 downlink bytes we would send
    requested_by  TEXT,
    issued_ts     INTEGER NOT NULL,
    confirm_deadline_ts INTEGER,
    confirmed_ts  INTEGER,
    confirming_uplink_id INTEGER,
    FOREIGN KEY (dev_eui) REFERENCES devices (dev_eui)
);
CREATE INDEX IF NOT EXISTS idx_valve_dev_status ON valve_commands (dev_eui, status);

-- ------------------------------------------------------ nuisance_feedback ---
-- The one-click "this was nothing" of 08-roadmap.md §8 risk 2. Append-only:
-- a correction is a second row, so the operator's first answer is never erased.
CREATE TABLE IF NOT EXISTS nuisance_feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id     INTEGER NOT NULL,
    dev_eui      TEXT NOT NULL,
    device_id    TEXT,
    was_nuisance INTEGER NOT NULL,
    note         TEXT,
    actor        TEXT,
    created_ts   INTEGER NOT NULL,
    FOREIGN KEY (alert_id) REFERENCES alerts (id)
);
CREATE INDEX IF NOT EXISTS idx_nuisance_alert ON nuisance_feedback (alert_id);
