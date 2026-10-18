#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path


DATE_LIKE_RE = re.compile(
    r"^(?:\d{1,2}[._-]\d{1,2}[._-]\d{2,4}|\d{2}_\d{2}_\d{2})$"
)
SESSION_RULES = ("folder", "acquisition_date_sample")


def is_date_like(value: str) -> bool:
    return bool(DATE_LIKE_RE.fullmatch(value.strip()))


def session_key_for_path(path: str) -> tuple[str, str, str]:
    parts = Path(path).parts
    if not parts:
        return "[root]", "[root]", ""

    top_folder = parts[0]
    if is_date_like(top_folder) and len(parts) > 1:
        return f"{top_folder}/{parts[1]}", top_folder, parts[1]

    if top_folder == "Cu_Pt project" and len(parts) > 1:
        if parts[1] == "Old" and len(parts) > 2:
            return f"{top_folder}/{parts[1]}/{parts[2]}", top_folder, f"{parts[1]}/{parts[2]}"
        return f"{top_folder}/{parts[1]}", top_folder, parts[1]

    return top_folder, top_folder, ""


def session_key_for_acquisition_date_sample(
    acquisition_date: str, inferred_sample: str
) -> tuple[str, str, str]:
    date_key = acquisition_date if acquisition_date else "[no_acquisition_date]"
    sample_key = inferred_sample if inferred_sample else "[unknown]"
    return f"{date_key}/{sample_key}", date_key, sample_key


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE run_info (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE sessions (
            session_id INTEGER PRIMARY KEY,
            session_key TEXT NOT NULL UNIQUE,
            top_folder TEXT NOT NULL,
            session_subfolder TEXT NOT NULL,
            inferred_sample TEXT NOT NULL,
            file_count INTEGER NOT NULL DEFAULT 0,
            signal_count INTEGER NOT NULL DEFAULT 0,
            total_size_bytes INTEGER NOT NULL DEFAULT 0,
            first_modified_utc TEXT NOT NULL DEFAULT '',
            last_modified_utc TEXT NOT NULL DEFAULT '',
            first_acquisition_date TEXT NOT NULL DEFAULT '',
            last_acquisition_date TEXT NOT NULL DEFAULT '',
            dm_files_loaded INTEGER NOT NULL DEFAULT 0,
            dm_load_errors INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE session_files (
            session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            source_file_id INTEGER NOT NULL,
            path TEXT NOT NULL,
            PRIMARY KEY (session_id, source_file_id)
        );

        CREATE TABLE session_file_ext_counts (
            session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            ext TEXT NOT NULL,
            file_count INTEGER NOT NULL,
            total_size_bytes INTEGER NOT NULL,
            PRIMARY KEY (session_id, ext)
        );

        CREATE TABLE session_file_kind_counts (
            session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            file_count INTEGER NOT NULL,
            total_size_bytes INTEGER NOT NULL,
            PRIMARY KEY (session_id, kind)
        );

        CREATE TABLE session_load_status_counts (
            session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            load_status TEXT NOT NULL,
            file_count INTEGER NOT NULL,
            PRIMARY KEY (session_id, load_status)
        );

        CREATE TABLE session_signal_category_counts (
            session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            signal_count INTEGER NOT NULL,
            PRIMARY KEY (session_id, category)
        );

        CREATE TABLE session_signal_type_counts (
            session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            signal_type TEXT NOT NULL,
            signal_count INTEGER NOT NULL,
            PRIMARY KEY (session_id, signal_type)
        );

        CREATE TABLE session_shape_counts (
            session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            data_shape TEXT NOT NULL,
            data_dtype TEXT NOT NULL,
            signal_count INTEGER NOT NULL,
            PRIMARY KEY (session_id, data_shape, data_dtype)
        );

        CREATE TABLE session_axis_summary (
            session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            units TEXT NOT NULL,
            axis_count INTEGER NOT NULL,
            min_size INTEGER,
            max_size INTEGER,
            avg_size REAL,
            min_scale REAL,
            max_scale REAL,
            avg_scale REAL,
            PRIMARY KEY (session_id, name, units)
        );

        CREATE TABLE session_metadata_profile (
            session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            source TEXT NOT NULL,
            key_path TEXT NOT NULL,
            value_count INTEGER NOT NULL,
            distinct_value_count INTEGER NOT NULL,
            numeric_value_count INTEGER NOT NULL,
            min_number REAL,
            max_number REAL,
            mean_number REAL,
            PRIMARY KEY (session_id, source, key_path)
        );

        CREATE TABLE session_metadata_top_values (
            session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            source TEXT NOT NULL,
            key_path TEXT NOT NULL,
            value_text TEXT NOT NULL,
            value_count INTEGER NOT NULL,
            rank INTEGER NOT NULL,
            PRIMARY KEY (session_id, source, key_path, rank)
        );

        CREATE TABLE derived_products (
            source_file_id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            top_folder TEXT NOT NULL,
            inferred_sample TEXT NOT NULL,
            acquisition_folder TEXT NOT NULL,
            ext TEXT NOT NULL,
            kind TEXT NOT NULL,
            load_status TEXT NOT NULL,
            signal_count INTEGER NOT NULL,
            total_size_bytes INTEGER NOT NULL,
            modified_utc TEXT NOT NULL,
            reason TEXT NOT NULL
        );

        CREATE TABLE derived_product_signal_category_counts (
            source_file_id INTEGER NOT NULL REFERENCES derived_products(source_file_id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            signal_count INTEGER NOT NULL,
            PRIMARY KEY (source_file_id, category)
        );

        CREATE TABLE derived_product_signal_type_counts (
            source_file_id INTEGER NOT NULL REFERENCES derived_products(source_file_id) ON DELETE CASCADE,
            signal_type TEXT NOT NULL,
            signal_count INTEGER NOT NULL,
            PRIMARY KEY (source_file_id, signal_type)
        );

        CREATE VIEW session_overview AS
        SELECT
            session_key,
            inferred_sample,
            file_count,
            signal_count,
            total_size_bytes,
            first_acquisition_date,
            last_acquisition_date,
            dm_files_loaded,
            dm_load_errors
        FROM sessions;
        """
    )


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX idx_session_files_file ON session_files(source_file_id);
        CREATE INDEX idx_sessions_sample ON sessions(inferred_sample);
        CREATE INDEX idx_sessions_top_folder ON sessions(top_folder);
        CREATE INDEX idx_metadata_profile_key ON session_metadata_profile(key_path);
        CREATE INDEX idx_metadata_top_key ON session_metadata_top_values(key_path);
        CREATE INDEX idx_metadata_top_value ON session_metadata_top_values(value_text);
        CREATE INDEX idx_derived_products_sample ON derived_products(inferred_sample);
        CREATE INDEX idx_derived_products_top_folder ON derived_products(top_folder);
        CREATE INDEX idx_derived_products_path ON derived_products(path);
        """
    )


def session_boundary_description(session_rule: str) -> str:
    if session_rule == "folder":
        return (
            "top_folder, except date-like folders use top_folder/second_path_part "
            "and Cu_Pt project uses sample subfolder"
        )
    if session_rule == "acquisition_date_sample":
        return (
            "unique pair of per-file earliest non-empty signal acquisition_date and "
            "files.inferred_sample; files without signal acquisition_date do not "
            "create or join sessions"
        )
    raise ValueError(f"Unknown session rule: {session_rule}")


def write_run_info(
    conn: sqlite3.Connection, input_db: Path, top_values: int, session_rule: str
) -> None:
    values = {
        "created_utc": dt.datetime.now(dt.UTC).isoformat(),
        "input_db": str(input_db.resolve()),
        "python": sys.version.replace("\n", " "),
        "executable": sys.executable,
        "session_rule": session_rule,
        "session_boundary": session_boundary_description(session_rule),
        "derived_product_rule": (
            "loaded .dm3/.dm4 files with signals but no signal acquisition_date are "
            "stored in derived_products and excluded from acquisition_date_sample sessions"
        ),
        "top_values_per_key": str(top_values),
    }
    conn.executemany("INSERT INTO run_info(key, value) VALUES (?, ?)", sorted(values.items()))


def attach_source(conn: sqlite3.Connection, input_db: Path) -> None:
    conn.execute("ATTACH DATABASE ? AS src", (str(input_db),))


def build_session_map(conn: sqlite3.Connection, session_rule: str) -> None:
    if session_rule == "folder":
        select_sql = """
        SELECT file_id, path, top_folder, inferred_sample, '' AS acquisition_date
        FROM src.files
        ORDER BY path
        """
    elif session_rule == "acquisition_date_sample":
        select_sql = """
        SELECT
            f.file_id,
            f.path,
            f.top_folder,
            f.inferred_sample,
            coalesce(min(nullif(s.acquisition_date, '')), '') AS acquisition_date
        FROM src.files f
        LEFT JOIN src.signals s ON s.file_id = f.file_id
        GROUP BY f.file_id
        HAVING acquisition_date <> ''
        ORDER BY acquisition_date, f.inferred_sample, f.path
        """
    else:
        raise ValueError(f"Unknown session rule: {session_rule}")

    rows = conn.execute(
        select_sql
    ).fetchall()

    session_data: dict[str, dict[str, str | set[str]]] = {}
    file_rows: list[tuple[int, int, str]] = []

    for file_id, path, _top_folder, inferred_sample, acquisition_date in rows:
        if session_rule == "folder":
            session_key, top_folder, subfolder = session_key_for_path(path)
        else:
            session_key, top_folder, subfolder = session_key_for_acquisition_date_sample(
                acquisition_date, inferred_sample
            )
        if session_key not in session_data:
            session_data[session_key] = {
                "top_folder": top_folder,
                "session_subfolder": subfolder,
                "samples": set(),
            }
        session_data[session_key]["samples"].add(inferred_sample)

    ordered_keys = sorted(session_data, key=str.lower)
    session_ids = {key: idx for idx, key in enumerate(ordered_keys, start=1)}

    session_rows = []
    for key in ordered_keys:
        data = session_data[key]
        samples = sorted(data["samples"], key=str.lower)
        inferred_sample = samples[0] if len(samples) == 1 else "; ".join(samples)
        session_rows.append(
            (
                session_ids[key],
                key,
                data["top_folder"],
                data["session_subfolder"],
                inferred_sample,
            )
        )

    for file_id, path, _top_folder, inferred_sample, acquisition_date in rows:
        if session_rule == "folder":
            session_key, _top_folder, _subfolder = session_key_for_path(path)
        else:
            (
                session_key,
                _top_folder,
                _subfolder,
            ) = session_key_for_acquisition_date_sample(acquisition_date, inferred_sample)
        file_rows.append((session_ids[session_key], file_id, path))

    conn.executemany(
        """
        INSERT INTO sessions (
            session_id, session_key, top_folder, session_subfolder, inferred_sample
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        session_rows,
    )
    conn.executemany(
        """
        INSERT INTO session_files (session_id, source_file_id, path)
        VALUES (?, ?, ?)
        """,
        file_rows,
    )


def populate_derived_products(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TEMP TABLE no_date_dm_products AS
        SELECT
            f.file_id,
            f.path,
            f.top_folder,
            f.inferred_sample,
            f.acquisition_folder,
            f.ext,
            f.kind,
            f.load_status,
            count(s.signal_id) AS signal_count,
            f.size_bytes,
            f.modified_utc
        FROM src.files f
        JOIN src.signals s ON s.file_id = f.file_id
        WHERE f.ext IN ('.dm3', '.dm4')
          AND f.load_status = 'loaded'
        GROUP BY f.file_id
        HAVING coalesce(min(nullif(s.acquisition_date, '')), '') = ''
           AND count(s.signal_id) > 0;

        INSERT INTO derived_products (
            source_file_id, path, top_folder, inferred_sample, acquisition_folder,
            ext, kind, load_status, signal_count, total_size_bytes, modified_utc,
            reason
        )
        SELECT
            file_id,
            path,
            top_folder,
            inferred_sample,
            acquisition_folder,
            ext,
            kind,
            load_status,
            signal_count,
            size_bytes,
            modified_utc,
            'loaded_digitalmicrograph_without_signal_acquisition_date'
        FROM no_date_dm_products;

        INSERT INTO derived_product_signal_category_counts (
            source_file_id, category, signal_count
        )
        SELECT p.file_id, s.category, count(*)
        FROM no_date_dm_products p
        JOIN src.signals s ON s.file_id = p.file_id
        GROUP BY p.file_id, s.category;

        INSERT INTO derived_product_signal_type_counts (
            source_file_id, signal_type, signal_count
        )
        SELECT p.file_id, s.signal_type, count(*)
        FROM no_date_dm_products p
        JOIN src.signals s ON s.file_id = p.file_id
        GROUP BY p.file_id, s.signal_type;

        DROP TABLE no_date_dm_products;
        """
    )


def populate_aggregates(conn: sqlite3.Connection, top_values: int) -> None:
    conn.executescript(
        """
        UPDATE sessions
        SET
            file_count = (
                SELECT count(*)
                FROM session_files sf
                WHERE sf.session_id = sessions.session_id
            ),
            total_size_bytes = (
                SELECT coalesce(sum(f.size_bytes), 0)
                FROM session_files sf
                JOIN src.files f ON f.file_id = sf.source_file_id
                WHERE sf.session_id = sessions.session_id
            ),
            first_modified_utc = (
                SELECT coalesce(min(f.modified_utc), '')
                FROM session_files sf
                JOIN src.files f ON f.file_id = sf.source_file_id
                WHERE sf.session_id = sessions.session_id
            ),
            last_modified_utc = (
                SELECT coalesce(max(f.modified_utc), '')
                FROM session_files sf
                JOIN src.files f ON f.file_id = sf.source_file_id
                WHERE sf.session_id = sessions.session_id
            ),
            signal_count = (
                SELECT count(*)
                FROM session_files sf
                JOIN src.signals s ON s.file_id = sf.source_file_id
                WHERE sf.session_id = sessions.session_id
            ),
            first_acquisition_date = (
                SELECT coalesce(min(nullif(s.acquisition_date, '')), '')
                FROM session_files sf
                JOIN src.signals s ON s.file_id = sf.source_file_id
                WHERE sf.session_id = sessions.session_id
            ),
            last_acquisition_date = (
                SELECT coalesce(max(nullif(s.acquisition_date, '')), '')
                FROM session_files sf
                JOIN src.signals s ON s.file_id = sf.source_file_id
                WHERE sf.session_id = sessions.session_id
            ),
            dm_files_loaded = (
                SELECT count(*)
                FROM session_files sf
                JOIN src.files f ON f.file_id = sf.source_file_id
                WHERE sf.session_id = sessions.session_id
                  AND f.load_status = 'loaded'
            ),
            dm_load_errors = (
                SELECT count(*)
                FROM session_files sf
                JOIN src.files f ON f.file_id = sf.source_file_id
                WHERE sf.session_id = sessions.session_id
                  AND f.load_status = 'load_error'
            );

        INSERT INTO session_file_ext_counts (
            session_id, ext, file_count, total_size_bytes
        )
        SELECT sf.session_id, f.ext, count(*), coalesce(sum(f.size_bytes), 0)
        FROM session_files sf
        JOIN src.files f ON f.file_id = sf.source_file_id
        GROUP BY sf.session_id, f.ext;

        INSERT INTO session_file_kind_counts (
            session_id, kind, file_count, total_size_bytes
        )
        SELECT sf.session_id, f.kind, count(*), coalesce(sum(f.size_bytes), 0)
        FROM session_files sf
        JOIN src.files f ON f.file_id = sf.source_file_id
        GROUP BY sf.session_id, f.kind;

        INSERT INTO session_load_status_counts (
            session_id, load_status, file_count
        )
        SELECT sf.session_id, f.load_status, count(*)
        FROM session_files sf
        JOIN src.files f ON f.file_id = sf.source_file_id
        GROUP BY sf.session_id, f.load_status;

        INSERT INTO session_signal_category_counts (
            session_id, category, signal_count
        )
        SELECT sf.session_id, s.category, count(*)
        FROM session_files sf
        JOIN src.signals s ON s.file_id = sf.source_file_id
        GROUP BY sf.session_id, s.category;

        INSERT INTO session_signal_type_counts (
            session_id, signal_type, signal_count
        )
        SELECT sf.session_id, s.signal_type, count(*)
        FROM session_files sf
        JOIN src.signals s ON s.file_id = sf.source_file_id
        GROUP BY sf.session_id, s.signal_type;

        INSERT INTO session_shape_counts (
            session_id, data_shape, data_dtype, signal_count
        )
        SELECT sf.session_id, s.data_shape, s.data_dtype, count(*)
        FROM session_files sf
        JOIN src.signals s ON s.file_id = sf.source_file_id
        GROUP BY sf.session_id, s.data_shape, s.data_dtype;

        INSERT INTO session_axis_summary (
            session_id, name, units, axis_count, min_size, max_size, avg_size,
            min_scale, max_scale, avg_scale
        )
        SELECT
            sf.session_id,
            a.name,
            a.units,
            count(*),
            min(a.size),
            max(a.size),
            avg(a.size),
            min(a.scale),
            max(a.scale),
            avg(a.scale)
        FROM session_files sf
        JOIN src.signals s ON s.file_id = sf.source_file_id
        JOIN src.axes a ON a.signal_id = s.signal_id
        GROUP BY sf.session_id, a.name, a.units;

        INSERT INTO session_metadata_profile (
            session_id, source, key_path, value_count, distinct_value_count,
            numeric_value_count, min_number, max_number, mean_number
        )
        SELECT
            sf.session_id,
            kv.source,
            kv.key_path,
            count(*),
            count(distinct kv.value_text),
            count(kv.value_number),
            min(kv.value_number),
            max(kv.value_number),
            avg(kv.value_number)
        FROM session_files sf
        JOIN src.metadata_kv kv ON kv.file_id = sf.source_file_id
        GROUP BY sf.session_id, kv.source, kv.key_path;
        """
    )

    conn.execute("DROP TABLE IF EXISTS temp.metadata_value_counts")
    conn.execute(
        """
        CREATE TEMP TABLE metadata_value_counts AS
        SELECT
            sf.session_id AS session_id,
            kv.source AS source,
            kv.key_path AS key_path,
            kv.value_text AS value_text,
            count(*) AS value_count
        FROM session_files sf
        JOIN src.metadata_kv kv ON kv.file_id = sf.source_file_id
        GROUP BY sf.session_id, kv.source, kv.key_path, kv.value_text
        """
    )
    conn.execute(
        """
        INSERT INTO session_metadata_top_values (
            session_id, source, key_path, value_text, value_count, rank
        )
        SELECT session_id, source, key_path, value_text, value_count, rank
        FROM (
            SELECT
                session_id,
                source,
                key_path,
                value_text,
                value_count,
                row_number() OVER (
                    PARTITION BY session_id, source, key_path
                    ORDER BY value_count DESC, value_text ASC
                ) AS rank
            FROM metadata_value_counts
        )
        WHERE rank <= ?
        """,
        (top_values,),
    )
    conn.execute("DROP TABLE temp.metadata_value_counts")


def vacuum_database(conn: sqlite3.Connection) -> None:
    conn.commit()
    conn.execute("VACUUM")


def gzip_copy(path: Path) -> Path:
    gz_path = path.with_suffix(path.suffix + ".gz")
    with path.open("rb") as src, gzip.open(gz_path, "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)
    return gz_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate STEM-Bank-26 file/signal metadata into session-level SQLite tables."
    )
    parser.add_argument(
        "-i",
        "--input",
        default="_archive_inventory/stem_bank_26_metadata.sqlite",
        help="Source metadata SQLite database.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="_archive_inventory/stem_bank_26_sessions_by_acq_date_sample.sqlite",
        help="Output session aggregate SQLite database.",
    )
    parser.add_argument(
        "--top-values",
        type=int,
        default=10,
        help="Top value count to keep per session/source/key_path. Default: 10.",
    )
    parser.add_argument(
        "--session-rule",
        choices=SESSION_RULES,
        default="acquisition_date_sample",
        help=(
            "Session grouping rule. 'folder' preserves the original path-based rule; "
            "'acquisition_date_sample' groups by acquisition date plus inferred sample."
        ),
    )
    parser.add_argument(
        "--gzip-copy",
        action="store_true",
        help="Also write a compressed .sqlite.gz copy.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_db = Path(args.input)
    output = Path(args.output)
    if not input_db.is_file():
        print(f"Input database not found: {input_db}", file=sys.stderr)
        return 2
    if args.top_values < 1:
        print("--top-values must be at least 1", file=sys.stderr)
        return 2

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(output.name + ".tmp")
    if temp_output.exists():
        temp_output.unlink()

    conn = connect(temp_output)
    try:
        attach_source(conn, input_db)
        create_schema(conn)
        write_run_info(conn, input_db, args.top_values, args.session_rule)
        build_session_map(conn, args.session_rule)
        populate_derived_products(conn)
        populate_aggregates(conn, args.top_values)
        create_indexes(conn)
        vacuum_database(conn)
    finally:
        conn.close()

    os.replace(temp_output, output)
    print(f"Wrote {output}")
    if args.gzip_copy:
        gz_path = gzip_copy(output)
        print(f"Wrote {gz_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
