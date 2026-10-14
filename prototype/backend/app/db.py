"""SQLite connection handling and idempotent schema application (contract §9).

stdlib `sqlite3` only: there is no sqlite3 CLI and no Docker on the build box
(contract header), so the database is a single file and the schema is applied by
`executescript` at startup. One connection is opened per unit of work and closed
again, because FastAPI runs sync endpoints on a worker thread pool and a sqlite3
connection must not be shared across threads.

What this module deliberately does NOT do:
  * no connection pool -- at pilot scale (50-100 devices on a 15-minute
    heartbeat, 08-roadmap.md §3 P1) the write rate is a few per minute;
  * no migrations. See the note at the top of schema.sql;
  * no ORM.
"""

import datetime
import os
import pathlib
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

_APP_DIR = pathlib.Path(__file__).resolve().parent
_SCHEMA_PATH = _APP_DIR / "schema.sql"
# backend/leakwatch.db, ignored by backend/.gitignore. Kept inside the backend
# component rather than at the prototype root so running the app never writes
# into another component's directory.
_DEFAULT_DB_PATH = _APP_DIR.parent / "leakwatch.db"

# Reentrant: `ensure_db()` holds it while calling `db_path()`, which takes it too.
_lock = threading.RLock()
_db_path: Optional[pathlib.Path] = None
_initialised_paths = set()


# Both names are honoured. `LEAK_DB_PATH` is what scripts/e2e.sh, the Dockerfile
# and docker-compose.yml set; the contract names no environment variable, so
# neither is a deviation and supporting both is cheaper than a mismatch that
# silently writes to the wrong database.
DB_PATH_ENV_VARS = ("LEAKWATCH_DB", "LEAK_DB_PATH")


def db_path() -> pathlib.Path:
    """Resolved database path: environment, then an explicit set, then default."""
    global _db_path
    with _lock:
        if _db_path is None:
            env = None
            for name in DB_PATH_ENV_VARS:
                env = os.environ.get(name)
                if env:
                    break
            _db_path = pathlib.Path(env).expanduser().resolve() if env else _DEFAULT_DB_PATH
        return _db_path


def set_db_path(path: Any) -> pathlib.Path:
    """Point the process at a different database file. Used by the tests."""
    global _db_path
    with _lock:
        _db_path = pathlib.Path(path).expanduser().resolve()
        _initialised_paths.discard(str(_db_path))
        return _db_path


def connect() -> sqlite3.Connection:
    """A fresh connection with row access by name and foreign keys enforced."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on exception, always close."""
    conn = connect()
    try:
        with conn:  # commits or rolls back the implicit transaction
            yield conn
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection) -> Dict[str, set]:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {t: {r[1] for r in conn.execute("PRAGMA table_info('%s')" % t)} for t in tables}


def _check_schema_drift(conn: sqlite3.Connection, sql: str) -> None:
    """Fail loudly if an existing database file predates the current schema.sql.

    There is no migration tool here by choice (see schema.sql), and
    `CREATE TABLE IF NOT EXISTS` silently leaves an old table's columns alone.
    Without this check a stale file turns into an "no such column" error deep in
    a request. schema.sql is the source of truth: it is applied to a scratch
    in-memory database and the two shapes compared.
    """
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(sql)
        want = _columns(reference)
    finally:
        reference.close()
    have = _columns(conn)
    problems = []
    for table, columns in sorted(want.items()):
        if table not in have:
            continue        # a table this file has never had: CREATE will make it
        missing = columns - have[table]
        if missing:
            problems.append("%s is missing %s" % (table, ", ".join(sorted(missing))))
    if problems:
        raise RuntimeError(
            "database %s predates backend/app/schema.sql (%s). This prototype has no "
            "migration tool on purpose: delete the file and let it be recreated, or point "
            "LEAKWATCH_DB somewhere else." % (db_path(), "; ".join(problems)))


def init_db() -> pathlib.Path:
    """Apply schema.sql. Safe to call on every startup and from every test."""
    path = db_path()
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn = connect()
    try:
        # Checked first: applying the script to a stale file fails with an
        # obscure "no such column" from an index, not a useful message.
        _check_schema_drift(conn, sql)
        with conn:
            conn.executescript(sql)
    finally:
        conn.close()
    with _lock:
        _initialised_paths.add(str(path))
    return path


def ensure_db() -> None:
    """Apply the schema once per process per database file."""
    path = str(db_path())
    with _lock:
        already = path in _initialised_paths
    if not already:
        init_db()


def now_s() -> int:
    """Unix seconds. Single choke point so tests can pass an explicit `now_s=`."""
    return int(time.time())


def iso(ts: Optional[int]) -> Optional[str]:
    """Unix seconds -> RFC3339 UTC, the format the LNS and the dashboard use."""
    if ts is None:
        return None
    return (
        datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_ts(received_at: Any, default: Optional[int] = None) -> int:
    """Parse the LNS `received_at` string into unix seconds.

    The Things Stack emits RFC3339 with a trailing "Z" and often 9 fractional
    digits ("...T03:00:00.123456789Z"); Python 3.9's `fromisoformat` accepts
    neither, so both are normalised here rather than in the request handler.
    An unparseable timestamp falls back to server time instead of rejecting the
    uplink -- losing a frame over a clock-format quirk is worse than a slightly
    wrong timestamp, and the raw string is retained verbatim either way.
    """
    if default is None:
        default = now_s()
    if not isinstance(received_at, str) or not received_at.strip():
        return default
    text = received_at.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, _, tail = text.partition(".")
        digits = ""
        rest = ""
        for i, ch in enumerate(tail):
            if ch.isdigit():
                digits += ch
            else:
                rest = tail[i:]
                break
        text = head + "." + (digits[:6] or "0") + rest
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        return default
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    """sqlite3.Row -> plain dict (Row is not JSON-serialisable)."""
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}
