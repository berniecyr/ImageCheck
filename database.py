import os
import sqlite3


# Path normalisation is applied consistently everywhere so Windows
# case-insensitive paths compare correctly (os.path.normcase lowercases
# drive letters and path components on Windows; it is a no-op on Linux).
def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


class Database:

    def __init__(self, db_path):
        self.db_path = db_path

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def initialize(self):
        conn = self.get_connection()
        try:
            conn.execute("PRAGMA journal_mode=WAL;")

            # ── Detection results (existing table, unchanged) ──────────
            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS logs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,

                    source_file     TEXT,
                    fromwhere       TEXT,
                    frame_number    INTEGER,

                    person_name     TEXT,
                    gender          TEXT,
                    age             INTEGER,

                    is_explicit     INTEGER DEFAULT 0,
                    explicit_parts  TEXT,
                    confidence      REAL,
                    face_confidence REAL,

                    faces_path      TEXT,
                    nudity_path     TEXT
                )
                '''
            )

            # ── Scan deduplication (new table) ─────────────────────────
            #
            # Fingerprint = (file_path, file_size, file_mtime).
            #
            # Why not SHA-256?  Hashing 200,000 surveillance files at
            # startup means reading hundreds of GB before any AI work
            # starts.  Surveillance footage is write-once; size+mtime
            # catches every real-world change (re-export, correction)
            # in microseconds via a single os.stat() call.
            #
            # Why not path alone?  Guards against the rare case where a
            # file at the same path is replaced with different content
            # (e.g. a corrected export) – it will be rescanned.
            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS scan_history (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- Fingerprint ----------------------------------
                    file_path       TEXT    NOT NULL UNIQUE,
                    file_size       INTEGER NOT NULL DEFAULT 0,
                    file_mtime      REAL    NOT NULL DEFAULT 0,

                    -- Best available capture date ------------------
                    -- Priority: EXIF → ffprobe → filename → mtime
                    media_date      TEXT,

                    -- Audit columns --------------------------------
                    processed_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                    scan_source     TEXT     -- directory or list-file path
                )
                '''
            )

            # The UNIQUE constraint on file_path already creates an
            # implicit B-tree index, but an explicit covering index on
            # (file_path, file_size, file_mtime) lets SQLite satisfy
            # the single-row lookup in get_processed_fingerprints()
            # without touching the table data pages at all.
            conn.execute(
                '''
                CREATE INDEX IF NOT EXISTS idx_scan_history_fingerprint
                ON scan_history (file_path, file_size, file_mtime)
                '''
            )

            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # scan_history — write
    # ------------------------------------------------------------------

    def mark_file_processed(
        self,
        file_path: str,
        media_date: str = None,
        scan_source: str = "",
    ) -> None:
        """
        Upsert a scan_history row for *file_path*.

        Reads the current os.stat() so the stored fingerprint always
        reflects the file as it was when it was last processed.
        """
        norm_path = _norm(file_path)
        try:
            st         = os.stat(file_path)
            file_size  = st.st_size
            file_mtime = st.st_mtime
        except OSError:
            file_size  = 0
            file_mtime = 0.0

        conn = self.get_connection()
        try:
            conn.execute(
                '''
                INSERT INTO scan_history
                    (file_path, file_size, file_mtime, media_date, scan_source)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_size    = excluded.file_size,
                    file_mtime   = excluded.file_mtime,
                    media_date   = excluded.media_date,
                    processed_at = CURRENT_TIMESTAMP,
                    scan_source  = excluded.scan_source
                ''',
                (norm_path, file_size, file_mtime, media_date, scan_source),
            )
            conn.commit()
        finally:
            conn.close()

    def clear_scan_history(self) -> int:
        """
        Delete all scan_history rows.  Returns the number of rows removed.
        Call this to force a full rescan on the next launch.
        """
        conn = self.get_connection()
        try:
            cur = conn.execute("DELETE FROM scan_history")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # scan_history — read
    # ------------------------------------------------------------------

    def get_processed_fingerprints(self) -> dict:
        """
        Return a dict  { normalised_path : (file_size, file_mtime) }
        for every row in scan_history.

        Load this ONCE at the start of a scan session and do Python
        dict lookups per file — one DB query instead of 200,000.
        Memory cost: ~20 MB for 200,000 paths at 100 bytes each.
        """
        conn = self.get_connection()
        try:
            rows = conn.execute(
                "SELECT file_path, file_size, file_mtime FROM scan_history"
            ).fetchall()
            return {
                row["file_path"]: (row["file_size"], row["file_mtime"])
                for row in rows
            }
        finally:
            conn.close()

    def get_scan_history(self, limit: int = 500) -> list:
        """Return recent scan_history rows for inspection / debugging."""
        conn = self.get_connection()
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM scan_history
                ORDER BY processed_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # logs — read
    # ------------------------------------------------------------------

    def get_recent_logs(self, limit=100):
        conn = self.get_connection()
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM logs
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_all_logs(self):
        conn = self.get_connection()
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM logs
                ORDER BY timestamp DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # logs — write / delete
    # ------------------------------------------------------------------

    def insert_log(
        self,
        source_file,
        frame_number,
        person_name,
        gender,
        age,
        is_explicit,
        explicit_parts,
        confidence,
        face_confidence,
        faces_path,
        nudity_path,
    ):
        conn = self.get_connection()
        try:
            fromwhere = ""
            if source_file:
                fromwhere = os.path.basename(os.path.dirname(source_file))

            conn.execute(
                '''
                INSERT INTO logs (
                    source_file, fromwhere, frame_number,
                    person_name, gender, age,
                    is_explicit, explicit_parts, confidence, face_confidence,
                    faces_path, nudity_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    source_file, fromwhere, frame_number,
                    person_name, gender, age,
                    is_explicit, explicit_parts, confidence, face_confidence,
                    faces_path, nudity_path,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_log(self, log_id):
        conn = self.get_connection()
        try:
            conn.execute("DELETE FROM logs WHERE id=?", (log_id,))
            conn.commit()
        finally:
            conn.close()

    def delete_logs(self, ids):
        if not ids:
            return
        conn = self.get_connection()
        try:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"DELETE FROM logs WHERE id IN ({placeholders})",
                tuple(ids),
            )
            conn.commit()
        finally:
            conn.close()
