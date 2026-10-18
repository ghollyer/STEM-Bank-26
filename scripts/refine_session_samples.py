#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path


GENERIC_SAMPLE_NAMES = {
    "",
    "[unknown]",
    "[root]",
    "unknown",
    "root",
    "stem",
    "si data",
    "analytical",
    "eds map",
    "eels map",
}

ACQUISITION_DIR_RE = re.compile(
    r"^(?:stem|si data|analytical|eds map|eels map)(?:\s*\([^)]*\).*)?$",
    re.IGNORECASE,
)
DATE_PART_RE = re.compile(
    r"^(?:\d{1,2}[._-]\d{1,2}[._-]\d{2,4}|\d{2}_\d{2}_\d{2}|\d{4,8})$"
)
TRAILING_ACQ_NUMBER_RE = re.compile(r"(?:[_\s-]?\d{3,5}|[_\s-]?\d+[_\s-]?\d*)$")

CONDITION_WORDS = {
    "after",
    "again",
    "aliqod",
    "aliqods",
    "aliquot",
    "aliquots",
    "annealed",
    "before",
    "beginning",
    "copy",
    "cycle",
    "cycled",
    "day",
    "end",
    "ex",
    "exsitu",
    "final",
    "for",
    "heated",
    "heating",
    "hour",
    "hours",
    "hr",
    "in",
    "initial",
    "min",
    "minute",
    "minutes",
    "more",
    "new",
    "old",
    "ox",
    "oxidized",
    "post",
    "pre",
    "recovered",
    "red",
    "reduced",
    "reox",
    "repeat",
    "revisiting",
    "saml",
    "sample",
    "second",
    "seconfd",
    "situ",
    "start",
    "step",
    "thin",
    "trial",
    "used",
}

FILENAME_NOISE_WORDS = {
    "abf",
    "adf",
    "aftermeas",
    "aligned",
    "average",
    "bf",
    "chip",
    "df",
    "fft",
    "haadf",
    "image",
    "jeol",
    "live",
    "of",
    "rigid",
    "spectrum",
    "stack",
    "survey",
    "tema",
}
FILENAME_NOISE_RE = re.compile(
    r"^(?:"
    r"\d+(?:\.\d+)?(?:mx|kx|kv|nm|um|c)?|"
    r"(?:chip|cihp)[a-z0-9]*|"
    r"\d{6,8}|"
    r"\d+"
    r")$",
    re.IGNORECASE,
)


def clean_text(value: str) -> str:
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"\b\d{1,2}[._/-]\d{1,2}(?:[._/-]\d{2,4})?\b", " ", value)
    value = re.sub(r"\b(?:19|20)\d{2}[._/-]\d{1,2}[._/-]\d{1,2}\b", " ", value)
    value = re.sub(r"\b\d{2}_\d{2}_\d{2}\b", " ", value)
    value = re.sub(r"\b(?:19|20)\d{2}\b", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ._-")


def normalized_name(value: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def display_name(value: str) -> str:
    value = clean_text(value)
    value = re.sub(r"\s*\([^)]*\)\s*$", "", value)
    value = re.sub(r"\s+", " ", value).strip(" ._-")
    return value or "[unknown]"


def is_generic(value: str) -> bool:
    norm = normalized_name(value)
    tokens = norm.split()
    alpha_len = sum(len(re.sub(r"[^a-z]", "", token)) for token in tokens)
    return (
        norm in GENERIC_SAMPLE_NAMES
        or value.strip().lower() in GENERIC_SAMPLE_NAMES
        or bool(tokens) and all(re.fullmatch(r"\d+", token) for token in tokens)
        or alpha_len <= 2
    )


def is_dateish(value: str) -> bool:
    return bool(DATE_PART_RE.fullmatch(value.strip()))


def is_acquisition_dir(value: str) -> bool:
    return bool(ACQUISITION_DIR_RE.fullmatch(clean_text(value)))


def filename_hint(path: str) -> str:
    stem = Path(path).stem
    stem = TRAILING_ACQ_NUMBER_RE.sub("", stem)
    candidate = display_name(stem)
    tokens = []
    for token in normalized_name(candidate).split():
        if token in FILENAME_NOISE_WORDS or token in CONDITION_WORDS:
            continue
        if FILENAME_NOISE_RE.fullmatch(token):
            continue
        tokens.append(token)
    if len(tokens) < 2:
        return ""
    if sum(len(re.sub(r"[^a-z]", "", token)) for token in tokens) < 4:
        return ""
    return display_name(" ".join(tokens))


def path_sample_hint(path: str) -> str:
    parts = Path(path).parts
    for part in parts[:-1]:
        if is_dateish(part) or is_acquisition_dir(part):
            continue
        candidate = display_name(part)
        if not is_generic(candidate):
            return candidate
    return filename_hint(path)


def condition_base(value: str) -> str:
    tokens = normalized_name(value).split()
    has_condition = bool(set(tokens) & CONDITION_WORDS)
    out: list[str] = []
    for token in tokens:
        if token in CONDITION_WORDS:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?\s*(?:h|hr|hrs|hour|hours|min|mins|minute|minutes)", token):
            continue
        if has_condition and re.fullmatch(r"\d{1,2}", token):
            continue
        out.append(token)
    return " ".join(out).strip()


def has_condition_word(value: str) -> bool:
    return bool(set(normalized_name(value).split()) & CONDITION_WORDS)


def strip_condition_display(value: str) -> str:
    raw_tokens = re.sub(r"[^A-Za-z0-9]+", " ", clean_text(value)).split()
    norm_tokens = normalized_name(value).split()
    has_condition = bool(set(norm_tokens) & CONDITION_WORDS)
    kept = []
    for raw, norm in zip(raw_tokens, norm_tokens):
        if norm in CONDITION_WORDS:
            continue
        if has_condition and re.fullmatch(r"\d{1,2}", norm):
            continue
        kept.append(raw)
    return display_name(" ".join(kept))


def canonical_display_for_group(names: list[str], base: str) -> str:
    no_condition = [name for name in names if not has_condition_word(name)]
    if no_condition:
        return sorted(no_condition, key=lambda s: (len(s), s.lower()))[0]
    stripped = [strip_condition_display(name) for name in names]
    stripped = [name for name in stripped if name and not is_generic(name)]
    if stripped:
        return Counter(stripped).most_common(1)[0][0]
    return " ".join(token.capitalize() for token in base.split()) or sorted(names, key=len)[0]


def parse_timestamp(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def midpoint(first_ts: str, last_ts: str) -> dt.datetime | None:
    first = parse_timestamp(first_ts)
    last = parse_timestamp(last_ts) or first
    if first is None:
        return last
    if last is None:
        return first
    return first + (last - first) / 2


def time_gap_seconds(
    point: dt.datetime | None, first_ts: str, last_ts: str
) -> float | None:
    if point is None:
        return None
    start = parse_timestamp(first_ts)
    end = parse_timestamp(last_ts) or start
    if start is None:
        return None
    if point < start:
        return (start - point).total_seconds()
    if end is not None and point > end:
        return (point - end).total_seconds()
    return 0.0


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def fetch_file_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            f.file_id,
            f.path,
            f.inferred_sample AS raw_inferred_sample,
            coalesce(min(nullif(s.acquisition_date, '')), '') AS acquisition_date,
            min(
                CASE
                    WHEN s.acquisition_date <> '' AND s.acquisition_time <> ''
                    THEN s.acquisition_date || 'T' || s.acquisition_time
                END
            ) AS first_timestamp,
            max(
                CASE
                    WHEN s.acquisition_date <> '' AND s.acquisition_time <> ''
                    THEN s.acquisition_date || 'T' || s.acquisition_time
                END
            ) AS last_timestamp,
            count(s.signal_id) AS signal_count,
            f.size_bytes
        FROM meta.files f
        LEFT JOIN meta.signals s ON s.file_id = f.file_id
        GROUP BY f.file_id
        HAVING acquisition_date <> ''
        ORDER BY acquisition_date, f.path
        """
    ).fetchall()


def initial_assignments(rows: list[sqlite3.Row]) -> dict[int, dict[str, object]]:
    assignments: dict[int, dict[str, object]] = {}
    for row in rows:
        raw = row["raw_inferred_sample"] or "[unknown]"
        if is_generic(raw):
            hint = path_sample_hint(row["path"])
            if hint:
                sample = hint
                method = "generic_path_or_filename_hint"
                confidence = 0.75
                evidence = hint
            else:
                sample = "[unassigned_generic]"
                method = "generic_unassigned"
                confidence = 0.0
                evidence = ""
        else:
            sample = display_name(raw)
            method = "raw_inferred_sample"
            confidence = 1.0
            evidence = raw
        assignments[row["file_id"]] = {
            "source_file_id": row["file_id"],
            "acquisition_date": row["acquisition_date"],
            "path": row["path"],
            "raw_inferred_sample": raw,
            "initial_sample": sample,
            "refined_sample": sample,
            "assignment_method": method,
            "confidence": confidence,
            "evidence": evidence,
            "first_timestamp": row["first_timestamp"] or "",
            "last_timestamp": row["last_timestamp"] or "",
            "signal_count": row["signal_count"],
            "size_bytes": row["size_bytes"],
        }
    return assignments


def apply_modifier_merges(assignments: dict[int, dict[str, object]]) -> None:
    names_by_date: dict[str, Counter[str]] = defaultdict(Counter)
    for item in assignments.values():
        sample = str(item["initial_sample"])
        if sample == "[unassigned_generic]" or is_generic(sample):
            continue
        names_by_date[str(item["acquisition_date"])][sample] += 1

    canonical_by_date_name: dict[tuple[str, str], str] = {}
    for date, name_counts in names_by_date.items():
        by_base: dict[str, list[str]] = defaultdict(list)
        for name in name_counts:
            base = condition_base(name)
            if base and not is_generic(base):
                by_base[base].append(name)

        for base, names in by_base.items():
            if len(names) < 2 or not any(has_condition_word(name) for name in names):
                continue
            canonical = canonical_display_for_group(names, base)
            for name in names:
                canonical_by_date_name[(date, name)] = canonical

    for item in assignments.values():
        key = (str(item["acquisition_date"]), str(item["initial_sample"]))
        canonical = canonical_by_date_name.get(key)
        if canonical:
            item["refined_sample"] = canonical
            if item["assignment_method"] == "raw_inferred_sample":
                item["assignment_method"] = "same_day_modifier_merge"
            else:
                item["assignment_method"] = f"{item['assignment_method']}+same_day_modifier_merge"
            item["confidence"] = max(float(item["confidence"]), 0.9)
            item["evidence"] = f"{item['evidence']} -> {canonical}".strip()


def group_stats(assignments: dict[int, dict[str, object]]) -> dict[tuple[str, str], dict[str, object]]:
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for item in assignments.values():
        sample = str(item["refined_sample"])
        if sample == "[unassigned_generic]":
            continue
        key = (str(item["acquisition_date"]), sample)
        group = grouped.setdefault(
            key,
            {
                "file_count": 0,
                "signal_count": 0,
                "total_size_bytes": 0,
                "first_timestamp": "",
                "last_timestamp": "",
            },
        )
        group["file_count"] = int(group["file_count"]) + 1
        group["signal_count"] = int(group["signal_count"]) + int(item["signal_count"])
        group["total_size_bytes"] = int(group["total_size_bytes"]) + int(item["size_bytes"])
        first = str(item["first_timestamp"])
        last = str(item["last_timestamp"])
        if first and (not group["first_timestamp"] or first < group["first_timestamp"]):
            group["first_timestamp"] = first
        if last and (not group["last_timestamp"] or last > group["last_timestamp"]):
            group["last_timestamp"] = last
    return grouped


def assign_remaining_generics(
    assignments: dict[int, dict[str, object]], max_gap_seconds: float
) -> None:
    known = group_stats(assignments)
    by_date: dict[str, list[tuple[str, dict[str, object]]]] = defaultdict(list)
    for (date, sample), stats in known.items():
        by_date[date].append((sample, stats))

    for item in assignments.values():
        if item["refined_sample"] != "[unassigned_generic]":
            continue
        candidates = by_date.get(str(item["acquisition_date"]), [])
        if not candidates:
            continue
        point = midpoint(str(item["first_timestamp"]), str(item["last_timestamp"]))
        scored = []
        for sample, stats in candidates:
            gap = time_gap_seconds(
                point, str(stats["first_timestamp"]), str(stats["last_timestamp"])
            )
            if gap is None:
                continue
            scored.append((gap, sample))
        if not scored and len(candidates) == 1:
            item["refined_sample"] = candidates[0][0]
            item["assignment_method"] = "generic_unique_sample_same_date"
            item["confidence"] = 0.55
            item["evidence"] = "only non-generic sample on acquisition date"
            continue
        if not scored:
            continue
        scored.sort()
        best_gap, best_sample = scored[0]
        ambiguous = len(scored) > 1 and abs(scored[1][0] - best_gap) <= 300
        if best_gap <= max_gap_seconds and not ambiguous:
            item["refined_sample"] = best_sample
            item["assignment_method"] = "generic_time_nearest_same_date"
            item["confidence"] = 0.65 if best_gap > 0 else 0.8
            item["evidence"] = f"nearest same-date sample by timestamp gap: {best_gap:.0f}s"


def create_output_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS refined_sample_files;
        DROP TABLE IF EXISTS refined_sample_sessions;
        DROP TABLE IF EXISTS refined_sample_session_members;
        DROP TABLE IF EXISTS refined_sample_aliases;
        DROP TABLE IF EXISTS refined_sample_run_info;

        CREATE TABLE refined_sample_run_info (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE refined_sample_files (
            source_file_id INTEGER PRIMARY KEY,
            acquisition_date TEXT NOT NULL,
            raw_inferred_sample TEXT NOT NULL,
            initial_sample TEXT NOT NULL,
            refined_sample TEXT NOT NULL,
            refined_session_key TEXT NOT NULL,
            assignment_method TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence TEXT NOT NULL,
            first_timestamp TEXT NOT NULL,
            last_timestamp TEXT NOT NULL,
            signal_count INTEGER NOT NULL,
            total_size_bytes INTEGER NOT NULL,
            path TEXT NOT NULL
        );

        CREATE TABLE refined_sample_sessions (
            refined_session_id INTEGER PRIMARY KEY,
            refined_session_key TEXT NOT NULL UNIQUE,
            acquisition_date TEXT NOT NULL,
            refined_sample TEXT NOT NULL,
            file_count INTEGER NOT NULL,
            signal_count INTEGER NOT NULL,
            total_size_bytes INTEGER NOT NULL,
            first_timestamp TEXT NOT NULL,
            last_timestamp TEXT NOT NULL
        );

        CREATE TABLE refined_sample_aliases (
            acquisition_date TEXT NOT NULL,
            raw_inferred_sample TEXT NOT NULL,
            initial_sample TEXT NOT NULL,
            refined_sample TEXT NOT NULL,
            assignment_method TEXT NOT NULL,
            file_count INTEGER NOT NULL,
            PRIMARY KEY (
                acquisition_date, raw_inferred_sample, initial_sample,
                refined_sample, assignment_method
            )
        );

        CREATE TABLE refined_sample_session_members (
            session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            refined_session_id INTEGER NOT NULL REFERENCES refined_sample_sessions(refined_session_id) ON DELETE CASCADE,
            refined_session_key TEXT NOT NULL,
            file_count INTEGER NOT NULL,
            signal_count INTEGER NOT NULL,
            PRIMARY KEY (session_id, refined_session_id)
        );

        CREATE INDEX idx_refined_sample_files_date_sample
            ON refined_sample_files(acquisition_date, refined_sample);
        CREATE INDEX idx_refined_sample_files_raw
            ON refined_sample_files(raw_inferred_sample);
        CREATE INDEX idx_refined_sample_sessions_date
            ON refined_sample_sessions(acquisition_date);
        CREATE INDEX idx_refined_sample_sessions_sample
            ON refined_sample_sessions(refined_sample);
        CREATE INDEX idx_refined_sample_members_refined
            ON refined_sample_session_members(refined_session_id);
        """
    )


def write_outputs(
    conn: sqlite3.Connection,
    metadata_db: Path,
    assignments: dict[int, dict[str, object]],
    max_gap_seconds: float,
) -> None:
    rows = []
    for item in assignments.values():
        refined_sample = str(item["refined_sample"])
        refined_key = (
            ""
            if refined_sample == "[unassigned_generic]"
            else f"{item['acquisition_date']}/{refined_sample}"
        )
        rows.append(
            (
                item["source_file_id"],
                item["acquisition_date"],
                item["raw_inferred_sample"],
                item["initial_sample"],
                refined_sample,
                refined_key,
                item["assignment_method"],
                item["confidence"],
                item["evidence"],
                item["first_timestamp"],
                item["last_timestamp"],
                item["signal_count"],
                item["size_bytes"],
                item["path"],
            )
        )
    conn.executemany(
        """
        INSERT INTO refined_sample_files (
            source_file_id, acquisition_date, raw_inferred_sample, initial_sample,
            refined_sample, refined_session_key, assignment_method, confidence,
            evidence, first_timestamp, last_timestamp, signal_count,
            total_size_bytes, path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )

    conn.execute(
        """
        INSERT INTO refined_sample_sessions (
            refined_session_id, refined_session_key, acquisition_date, refined_sample,
            file_count, signal_count, total_size_bytes, first_timestamp, last_timestamp
        )
        SELECT
            row_number() OVER (ORDER BY acquisition_date, lower(refined_sample)),
            refined_session_key,
            acquisition_date,
            refined_sample,
            count(*),
            sum(signal_count),
            sum(total_size_bytes),
            min(nullif(first_timestamp, '')),
            max(nullif(last_timestamp, ''))
        FROM refined_sample_files
        WHERE refined_session_key <> ''
        GROUP BY refined_session_key, acquisition_date, refined_sample
        """
    )

    conn.execute(
        """
        INSERT INTO refined_sample_aliases (
            acquisition_date, raw_inferred_sample, initial_sample, refined_sample,
            assignment_method, file_count
        )
        SELECT
            acquisition_date,
            raw_inferred_sample,
            initial_sample,
            refined_sample,
            assignment_method,
            count(*)
        FROM refined_sample_files
        GROUP BY
            acquisition_date, raw_inferred_sample, initial_sample,
            refined_sample, assignment_method
        """
    )

    conn.execute(
        """
        INSERT INTO refined_sample_session_members (
            session_id, refined_session_id, refined_session_key, file_count, signal_count
        )
        SELECT
            sf.session_id,
            rss.refined_session_id,
            rsf.refined_session_key,
            count(*),
            sum(rsf.signal_count)
        FROM session_files sf
        JOIN refined_sample_files rsf ON rsf.source_file_id = sf.source_file_id
        JOIN refined_sample_sessions rss
          ON rss.refined_session_key = rsf.refined_session_key
        WHERE rsf.refined_session_key <> ''
        GROUP BY sf.session_id, rss.refined_session_id, rsf.refined_session_key
        """
    )

    summary = dict(
        conn.execute(
            """
            SELECT
                count(*) AS file_rows,
                count(DISTINCT refined_session_key) FILTER (WHERE refined_session_key <> '') AS refined_sessions,
                sum(refined_sample = '[unassigned_generic]') AS unassigned_generic_files,
                sum(assignment_method LIKE '%same_day_modifier_merge%') AS modifier_merged_files,
                sum(assignment_method LIKE 'generic_%') AS generic_reassigned_or_unassigned_files
            FROM refined_sample_files
            """
        ).fetchone()
    )
    values = {
        "created_utc": dt.datetime.now(dt.UTC).isoformat(),
        "metadata_db": str(metadata_db.resolve()),
        "python": sys.version.replace("\n", " "),
        "max_generic_assignment_gap_seconds": str(max_gap_seconds),
        "different_day_policy": "different acquisition dates are always different refined sample sessions",
        "modifier_policy": "same-day sample names that differ by condition/modifier words are merged",
        "generic_policy": "generic names are reassigned using path/filename hints, then nearest same-date sample by timestamp",
        **{key: str(value) for key, value in summary.items()},
    }
    conn.executemany(
        "INSERT INTO refined_sample_run_info(key, value) VALUES (?, ?)",
        sorted(values.items()),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create date-scoped refined sample assignments for STEM-Bank-26 sessions."
    )
    parser.add_argument(
        "--metadata-db",
        default="_archive_inventory/stem_bank_26_metadata.sqlite",
        help="Source raw metadata SQLite database.",
    )
    parser.add_argument(
        "--sessions-db",
        default="_archive_inventory/stem_bank_26_sessions_by_acq_date_sample.sqlite",
        help="Session database to receive refined sample tables.",
    )
    parser.add_argument(
        "--max-generic-gap-seconds",
        type=float,
        default=3600.0,
        help="Maximum timestamp gap for assigning remaining generic files to a same-date sample.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    metadata_db = Path(args.metadata_db)
    sessions_db = Path(args.sessions_db)
    if not metadata_db.is_file():
        print(f"Metadata DB not found: {metadata_db}", file=sys.stderr)
        return 2
    if not sessions_db.is_file():
        print(f"Sessions DB not found: {sessions_db}", file=sys.stderr)
        return 2

    conn = connect(sessions_db)
    try:
        conn.execute("ATTACH DATABASE ? AS meta", (str(metadata_db),))
        file_rows = fetch_file_rows(conn)
        assignments = initial_assignments(file_rows)
        apply_modifier_merges(assignments)
        assign_remaining_generics(assignments, args.max_generic_gap_seconds)
        create_output_tables(conn)
        write_outputs(conn, metadata_db, assignments, args.max_generic_gap_seconds)
        conn.commit()
    finally:
        conn.close()

    print(f"Wrote refined sample tables to {sessions_db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
