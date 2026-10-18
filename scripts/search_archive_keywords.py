#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any


KEYWORD_GROUP_PREFIX = "KEYWORD_GROUP_"
SEARCH_FIELDS = {
    "file": ["path", "filename", "stem", "parent_path", "top_folder", "inferred_sample", "acquisition_folder"],
    "session": ["session_key", "top_folder", "session_subfolder", "inferred_sample"],
    "signal": ["title", "signal_type", "data_shape", "category"],
    "metadata": ["key_path", "value_text"],
}


def normalize_group_name(value: str) -> str:
    group = re.sub(r"[^0-9A-Za-z]+", "_", value.strip()).strip("_").lower()
    return group or "default"


def split_keyword_values(value: str) -> list[str]:
    value = value.strip().strip('"').strip("'")
    return [part.strip() for part in re.split(r"[,;]", value) if part.strip()]


def parse_keyword_spec(value: str) -> tuple[str, str]:
    separator = "=" if "=" in value else ":"
    if separator not in value:
        raise ValueError(f"expected GROUP=KEYWORD or GROUP:KEYWORD, got {value!r}")
    group, keyword = value.split(separator, 1)
    group = normalize_group_name(group)
    keyword = keyword.strip()
    if not keyword:
        raise ValueError(f"empty keyword in {value!r}")
    return group, keyword


def read_keyword_env(path: Path) -> list[tuple[str, str]]:
    if not path.is_file():
        return []

    keywords: list[tuple[str, str]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: expected KEYWORD_GROUP_<NAME>=term1,term2")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.startswith(KEYWORD_GROUP_PREFIX):
            continue
        group = normalize_group_name(key[len(KEYWORD_GROUP_PREFIX) :])
        keywords.extend((group, keyword) for keyword in split_keyword_values(value))
    return keywords


def load_keywords(args: argparse.Namespace) -> list[tuple[str, str]]:
    keywords = read_keyword_env(Path(args.keywords_env))
    for spec in args.keyword:
        keywords.append(parse_keyword_spec(spec))

    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for group, keyword in keywords:
        key = (group.casefold(), keyword.casefold())
        if key in seen:
            continue
        seen.add(key)
        deduped.append((group, keyword))
    return deduped


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def clean_text(value: Any, limit: int = 500) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ")
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def match_expr(fields: list[str], qualifier: str = "") -> str:
    prefix = f"{qualifier}." if qualifier else ""
    return " OR ".join([f"lower(coalesce({prefix}{field}, '')) LIKE ?" for field in fields])


def match_params(keyword: str, fields: list[str]) -> list[str]:
    needle = f"%{keyword.lower()}%"
    return [needle for _ in fields]


def add_hit(
    rows: list[dict[str, Any]],
    keyword_group: str,
    keyword: str,
    hit_scope: str,
    field: str,
    row: sqlite3.Row,
    matched_text: Any,
) -> None:
    rows.append(
        {
            "keyword_group": keyword_group,
            "keyword": keyword,
            "hit_scope": hit_scope,
            "matched_field": field,
            "session_id": row["session_id"] if "session_id" in row.keys() else "",
            "session_key": clean_text(row["session_key"] if "session_key" in row.keys() else ""),
            "inferred_sample": clean_text(row["inferred_sample"] if "inferred_sample" in row.keys() else ""),
            "file_id": row["file_id"] if "file_id" in row.keys() else "",
            "path": clean_text(row["path"] if "path" in row.keys() else ""),
            "signal_id": row["signal_id"] if "signal_id" in row.keys() else "",
            "signal_title": clean_text(row["signal_title"] if "signal_title" in row.keys() else ""),
            "metadata_source": clean_text(row["metadata_source"] if "metadata_source" in row.keys() else ""),
            "metadata_key_path": clean_text(row["metadata_key_path"] if "metadata_key_path" in row.keys() else ""),
            "matched_text": clean_text(matched_text),
        }
    )


def search_fields(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    keyword_group: str,
    keyword: str,
    hit_scope: str,
    sql: str,
    fields: list[str],
) -> None:
    for row in conn.execute(sql, match_params(keyword, fields)):
        lowered_keyword = keyword.lower()
        for field in fields:
            value = row[field]
            if value is not None and lowered_keyword in str(value).lower():
                add_hit(rows, keyword_group, keyword, hit_scope, field, row, value)


def export(args: argparse.Namespace) -> int:
    metadata_db = Path(args.metadata_db)
    sessions_db = Path(args.sessions_db)
    output_dir = Path(args.output_dir)

    keywords = load_keywords(args)
    if not keywords:
        print(
            f"No keywords configured. Add {KEYWORD_GROUP_PREFIX}<NAME>=term1,term2 "
            f"to {args.keywords_env} or pass --keyword GROUP=TERM.",
        )
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)

    conn = connect(metadata_db)
    conn.execute("ATTACH DATABASE ? AS sess", (str(sessions_db),))

    hit_rows: list[dict[str, Any]] = []

    file_fields = SEARCH_FIELDS["file"]
    file_sql = f"""
        SELECT
            sf.session_id,
            se.session_key,
            f.inferred_sample,
            f.file_id,
            f.path,
            f.filename,
            f.stem,
            f.parent_path,
            f.top_folder,
            f.acquisition_folder,
            '' AS signal_id,
            '' AS signal_title,
            '' AS metadata_source,
            '' AS metadata_key_path
        FROM files f
        LEFT JOIN sess.session_files sf ON sf.source_file_id = f.file_id
        LEFT JOIN sess.sessions se ON se.session_id = sf.session_id
        WHERE {match_expr(file_fields, "f")}
    """

    session_fields = SEARCH_FIELDS["session"]
    session_sql = f"""
        SELECT
            s.session_id,
            s.session_key,
            s.inferred_sample,
            '' AS file_id,
            '' AS path,
            s.top_folder,
            s.session_subfolder,
            '' AS signal_id,
            '' AS signal_title,
            '' AS metadata_source,
            '' AS metadata_key_path
        FROM sess.sessions s
        WHERE {match_expr(session_fields, "s")}
    """

    signal_fields = SEARCH_FIELDS["signal"]
    signal_sql = f"""
        SELECT
            sf.session_id,
            se.session_key,
            f.inferred_sample,
            f.file_id,
            f.path,
            sig.signal_id,
            sig.title AS signal_title,
            sig.title,
            sig.signal_type,
            sig.data_shape,
            sig.category,
            '' AS metadata_source,
            '' AS metadata_key_path
        FROM signals sig
        JOIN files f ON f.file_id = sig.file_id
        LEFT JOIN sess.session_files sf ON sf.source_file_id = f.file_id
        LEFT JOIN sess.sessions se ON se.session_id = sf.session_id
        WHERE {match_expr(signal_fields, "sig")}
    """

    metadata_fields = SEARCH_FIELDS["metadata"]
    metadata_sql = f"""
        SELECT
            sf.session_id,
            se.session_key,
            f.inferred_sample,
            f.file_id,
            f.path,
            kv.signal_id,
            sig.title AS signal_title,
            kv.source AS metadata_source,
            kv.key_path AS metadata_key_path,
            kv.key_path,
            kv.value_text
        FROM metadata_kv kv
        JOIN files f ON f.file_id = kv.file_id
        LEFT JOIN signals sig ON sig.signal_id = kv.signal_id
        LEFT JOIN sess.session_files sf ON sf.source_file_id = f.file_id
        LEFT JOIN sess.sessions se ON se.session_id = sf.session_id
        WHERE {match_expr(metadata_fields, "kv")}
    """

    for keyword_group, keyword in keywords:
        search_fields(conn, hit_rows, keyword_group, keyword, "file", file_sql, file_fields)
        search_fields(conn, hit_rows, keyword_group, keyword, "session", session_sql, session_fields)
        search_fields(conn, hit_rows, keyword_group, keyword, "signal", signal_sql, signal_fields)
        search_fields(conn, hit_rows, keyword_group, keyword, "metadata", metadata_sql, metadata_fields)

    hit_rows.sort(
        key=lambda row: (
            row["keyword_group"],
            row["keyword"],
            str(row["session_key"]),
            str(row["path"]),
            str(row["signal_id"]),
            str(row["hit_scope"]),
            str(row["matched_field"]),
        )
    )

    session_map: dict[Any, dict[str, Any]] = {}
    for row in hit_rows:
        session_id = row["session_id"] or ""
        key = session_id if session_id != "" else f"unassigned:{row['path']}"
        summary = session_map.setdefault(
            key,
            {
                "session_id": row["session_id"],
                "session_key": row["session_key"],
                "inferred_sample": row["inferred_sample"],
                "keyword_groups": set(),
                "keywords": set(),
                "hit_scopes": set(),
                "matched_files": set(),
                "matched_signals": set(),
                "hit_count": 0,
            },
        )
        summary["hit_count"] += 1
        summary["hit_scopes"].add(row["hit_scope"])
        summary["keyword_groups"].add(row["keyword_group"])
        summary["keywords"].add(row["keyword"])
        if row["file_id"] != "":
            summary["matched_files"].add(row["file_id"])
        if row["signal_id"] != "":
            summary["matched_signals"].add(row["signal_id"])

    session_rows: list[dict[str, Any]] = []
    for summary in session_map.values():
        session_rows.append(
            {
                "session_id": summary["session_id"],
                "session_key": summary["session_key"],
                "inferred_sample": summary["inferred_sample"],
                "keyword_group_count": len(summary["keyword_groups"]),
                "keyword_count": len(summary["keywords"]),
                "keyword_groups": ";".join(sorted(summary["keyword_groups"])),
                "keywords": ";".join(sorted(summary["keywords"])),
                "hit_scopes": ";".join(sorted(summary["hit_scopes"])),
                "matched_file_count": len(summary["matched_files"]),
                "matched_signal_count": len(summary["matched_signals"]),
                "hit_count": summary["hit_count"],
            }
        )
    session_rows.sort(
        key=lambda row: (
            -row["hit_count"],
            -row["keyword_group_count"],
            -row["keyword_count"],
            str(row["session_key"]),
        )
    )

    keyword_rows: list[dict[str, Any]] = []
    by_keyword: dict[tuple[str, str], dict[str, set[Any] | int]] = defaultdict(
        lambda: {"hits": 0, "sessions": set(), "files": set(), "signals": set(), "scopes": set()}
    )
    for row in hit_rows:
        key = (row["keyword_group"], row["keyword"])
        by_keyword[key]["hits"] += 1
        if row["session_id"] != "":
            by_keyword[key]["sessions"].add(row["session_id"])
        if row["file_id"] != "":
            by_keyword[key]["files"].add(row["file_id"])
        if row["signal_id"] != "":
            by_keyword[key]["signals"].add(row["signal_id"])
        by_keyword[key]["scopes"].add(row["hit_scope"])

    for keyword_group, keyword in keywords:
        values = by_keyword[(keyword_group, keyword)]
        keyword_rows.append(
            {
                "keyword_group": keyword_group,
                "keyword": keyword,
                "hit_count": values["hits"],
                "session_count": len(values["sessions"]),
                "file_count": len(values["files"]),
                "signal_count": len(values["signals"]),
                "hit_scopes": ";".join(sorted(values["scopes"])),
            }
        )

    hit_fields = [
        "keyword_group",
        "keyword",
        "hit_scope",
        "matched_field",
        "session_id",
        "session_key",
        "inferred_sample",
        "file_id",
        "path",
        "signal_id",
        "signal_title",
        "metadata_source",
        "metadata_key_path",
        "matched_text",
    ]
    session_fields_out = [
        "session_id",
        "session_key",
        "inferred_sample",
        "keyword_group_count",
        "keyword_count",
        "keyword_groups",
        "keywords",
        "hit_scopes",
        "matched_file_count",
        "matched_signal_count",
        "hit_count",
    ]
    keyword_fields_out = [
        "keyword_group",
        "keyword",
        "hit_count",
        "session_count",
        "file_count",
        "signal_count",
        "hit_scopes",
    ]

    write_csv(output_dir / "keyword_hits.csv", hit_fields, hit_rows)
    write_csv(output_dir / "session_keyword_summary.csv", session_fields_out, session_rows)
    write_csv(output_dir / "keyword_summary.csv", keyword_fields_out, keyword_rows)

    manifest = {
        "created_utc": dt.datetime.now(dt.UTC).isoformat(),
        "description": "Keyword search across archive file, session, signal, and flattened metadata fields.",
        "keyword_source": {
            "env_file": str(Path(args.keywords_env)),
            "cli_keyword_count": len(args.keyword),
        },
        "source_databases": {
            "metadata_db": str(metadata_db.resolve()),
            "sessions_db": str(sessions_db.resolve()),
        },
        "keyword_groups": sorted({group for group, _keyword in keywords}),
        "keyword_count": len(keywords),
        "outputs": {
            "keyword_hits.csv": {"rows": len(hit_rows), "columns": hit_fields},
            "session_keyword_summary.csv": {"rows": len(session_rows), "columns": session_fields_out},
            "keyword_summary.csv": {"rows": len(keyword_rows), "columns": keyword_fields_out},
        },
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    print(f"Wrote {output_dir}")
    print(f"keyword_hits.csv: {len(hit_rows)} rows")
    print(f"session_keyword_summary.csv: {len(session_rows)} rows")
    print(f"keyword_summary.csv: {len(keyword_rows)} rows")
    for row in keyword_rows:
        print(
            f"{row['keyword_group']},{row['keyword']}: "
            f"{row['hit_count']} hits, {row['session_count']} sessions, "
            f"{row['file_count']} files, {row['signal_count']} signals"
        )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search STEM-Bank-26 metadata for configured keyword groups.")
    parser.add_argument(
        "--metadata-db",
        default="_archive_inventory/stem_bank_26_metadata.sqlite",
    )
    parser.add_argument(
        "--sessions-db",
        default="_archive_inventory/stem_bank_26_sessions_by_acq_date_sample.sqlite",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="_archive_inventory/keyword_search",
    )
    parser.add_argument(
        "--keywords-env",
        default="keyword_search.env",
        help=f"Env-style file containing {KEYWORD_GROUP_PREFIX}<NAME>=term1,term2 lines.",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Additional keyword as GROUP=TERM. Can be provided multiple times.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(export(parse_args()))
