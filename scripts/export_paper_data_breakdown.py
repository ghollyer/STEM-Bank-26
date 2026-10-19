#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sqlite3
import statistics
from pathlib import Path
from typing import Any

from inventory_paths import default_inventory_dir


SI_SURVEY_EXPR = "(lower(coalesce(title, '')) LIKE '%si survey%' OR lower(coalesce(path, '')) LIKE '%si survey%')"


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    return (
        conn.execute(
            f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def prepare_sample_views(conn: sqlite3.Connection) -> str:
    conn.executescript(
        """
        DROP VIEW IF EXISTS sample_sessions;
        DROP VIEW IF EXISTS file_to_sample_session;
        """
    )
    has_refined = all(
        table_exists(conn, "sess", table)
        for table in ("refined_sample_sessions", "refined_sample_files")
    )
    if has_refined:
        conn.executescript(
            """
            CREATE TEMP VIEW sample_sessions AS
            SELECT
                rss.refined_session_id AS sample_session_id,
                rss.refined_session_key AS sample_session_key,
                rss.acquisition_date,
                rss.refined_sample AS sample_name,
                coalesce(count(DISTINCT rsm.session_id), 0) AS source_session_count,
                rss.file_count,
                rss.signal_count
            FROM sess.refined_sample_sessions rss
            LEFT JOIN sess.refined_sample_session_members rsm
              ON rsm.refined_session_id = rss.refined_session_id
            GROUP BY rss.refined_session_id;

            CREATE TEMP VIEW file_to_sample_session AS
            SELECT
                rsf.source_file_id,
                rss.refined_session_id AS sample_session_id,
                rss.refined_session_key AS sample_session_key,
                rss.acquisition_date,
                rss.refined_sample AS sample_name
            FROM sess.refined_sample_files rsf
            JOIN sess.refined_sample_sessions rss
              ON rss.refined_session_key = rsf.refined_session_key;
            """
        )
        return "refined_date_scoped_sample_sessions"

    conn.executescript(
        """
        CREATE TEMP VIEW sample_sessions AS
        SELECT
            s.session_id AS sample_session_id,
            s.session_key AS sample_session_key,
            coalesce(nullif(s.first_acquisition_date, ''), nullif(s.last_acquisition_date, ''), '') AS acquisition_date,
            s.inferred_sample AS sample_name,
            1 AS source_session_count,
            s.file_count,
            s.signal_count
        FROM sess.sessions s;

        CREATE TEMP VIEW file_to_sample_session AS
        SELECT
            sf.source_file_id,
            s.session_id AS sample_session_id,
            s.session_key AS sample_session_key,
            coalesce(nullif(s.first_acquisition_date, ''), nullif(s.last_acquisition_date, ''), '') AS acquisition_date,
            s.inferred_sample AS sample_name
        FROM sess.session_files sf
        JOIN sess.sessions s ON s.session_id = sf.session_id;
        """
    )
    return "sessions"


def count_by(conn: sqlite3.Connection, sql: str) -> dict[str, int]:
    return {str(row[0]): int(row[1]) for row in conn.execute(sql)}


def percent(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return 100.0 * numerator / denominator


def count_percent(count: int, total: int) -> dict[str, Any]:
    return {"count": count, "percent": percent(count, total)}


def numeric_summary(values: list[float]) -> dict[str, Any]:
    clean = sorted(v for v in values if math.isfinite(v))
    if not clean:
        return {"n": 0, "min": None, "mean": None, "median": None, "max": None}
    return {
        "n": len(clean),
        "min": clean[0],
        "mean": sum(clean) / len(clean),
        "median": statistics.median(clean),
        "max": clean[-1],
    }


def top_counts(counts: dict[str, int], total: int, limit: int) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count, "percent": percent(count, total)}
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def session_image_counts(conn: sqlite3.Connection, standalone_only: bool) -> list[int]:
    image_count_expr = (
        f"count(DISTINCT CASE WHEN NOT {SI_SURVEY_EXPR} THEN i.image_id END)"
        if standalone_only
        else "count(DISTINCT i.image_id)"
    )
    rows = conn.execute(
        f"""
        SELECT
            ss.sample_session_id,
            coalesce({image_count_expr}, 0) AS image_count
        FROM sample_sessions ss
        LEFT JOIN file_to_sample_session m ON m.sample_session_id = ss.sample_session_id
        LEFT JOIN image_sequence i ON i.source_file_id = m.source_file_id
        GROUP BY ss.sample_session_id
        ORDER BY ss.sample_session_id
        """
    )
    return [int(row["image_count"] or 0) for row in rows]


def session_si_counts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT
                ss.sample_session_id,
                coalesce(count(DISTINCT e.si_event_id), 0) AS si_count,
                coalesce(count(DISTINCT CASE WHEN e.modality = 'EDS' THEN e.si_event_id END), 0) AS eds_only_si_count,
                coalesce(count(DISTINCT CASE WHEN e.modality = 'EELS' THEN e.si_event_id END), 0) AS eels_only_si_count,
                coalesce(count(DISTINCT CASE WHEN e.modality = 'EDS+EELS' THEN e.si_event_id END), 0) AS eds_plus_eels_si_count,
                coalesce(count(DISTINCT CASE WHEN e.modality IN ('EDS', 'EDS+EELS') THEN e.si_event_id END), 0) AS si_with_eds_count,
                coalesce(count(DISTINCT CASE WHEN e.modality IN ('EELS', 'EDS+EELS') THEN e.si_event_id END), 0) AS si_with_eels_count
            FROM sample_sessions ss
            LEFT JOIN file_to_sample_session m ON m.sample_session_id = ss.sample_session_id
            LEFT JOIN si_event_assets a
              ON a.source_file_id = m.source_file_id
             AND a.asset_role = 'PRIMARY_SI_SIGNAL'
            LEFT JOIN si_events e ON e.si_event_id = a.si_event_id
            GROUP BY ss.sample_session_id
            ORDER BY ss.sample_session_id
            """
        )
    )


def binned_session_counts(values: list[int]) -> dict[str, Any]:
    total = len(values)
    lt_10 = sum(value < 10 for value in values)
    between = sum(10 <= value <= 100 for value in values)
    gt_100 = sum(value > 100 for value in values)
    return {
        "total_sessions": total,
        "lt_10": count_percent(lt_10, total),
        "between_10_and_100": count_percent(between, total),
        "gt_100": count_percent(gt_100, total),
    }


def spectral_sample_coverage(si_rows: list[sqlite3.Row]) -> dict[str, Any]:
    total = len(si_rows)
    with_eds = 0
    with_eels = 0
    eds_only = 0
    eels_only = 0
    both = 0
    none = 0
    for row in si_rows:
        has_eds = int(row["si_with_eds_count"] or 0) > 0
        has_eels = int(row["si_with_eels_count"] or 0) > 0
        if has_eds:
            with_eds += 1
        if has_eels:
            with_eels += 1
        if has_eds and has_eels:
            both += 1
        elif has_eds:
            eds_only += 1
        elif has_eels:
            eels_only += 1
        else:
            none += 1
    any_spectral = eds_only + eels_only + both
    return {
        "samples": total,
        "any_spectral": count_percent(any_spectral, total),
        "with_eds": count_percent(with_eds, total),
        "with_eels": count_percent(with_eels, total),
        "both": count_percent(both, total),
        "none": count_percent(none, total),
        "mutually_exclusive": {
            "eds_only": count_percent(eds_only, total),
            "eels_only": count_percent(eels_only, total),
            "both": count_percent(both, total),
            "none": count_percent(none, total),
        },
    }


def build_breakdown(args: argparse.Namespace) -> dict[str, Any]:
    metadata_db = Path(args.metadata_db)
    sessions_db = Path(args.sessions_db)
    trajectories_db = Path(args.trajectories_db)

    conn = connect(trajectories_db)
    conn.execute("ATTACH DATABASE ? AS meta", (str(metadata_db),))
    conn.execute("ATTACH DATABASE ? AS sess", (str(sessions_db),))
    sample_source = prepare_sample_views(conn)

    image_sequence_count = int(scalar(conn, "SELECT count(*) FROM image_sequence"))
    si_survey_count = int(scalar(conn, f"SELECT count(*) FROM image_sequence WHERE {SI_SURVEY_EXPR}"))
    standalone_stem_count = image_sequence_count - si_survey_count
    adf_si_raster_count = int(scalar(conn, "SELECT count(*) FROM adf_si_raster_images"))

    standalone_shape_counts = count_by(
        conn,
        f"""
        SELECT data_shape, count(*)
        FROM image_sequence
        WHERE NOT {SI_SURVEY_EXPR}
        GROUP BY data_shape
        """,
    )
    image_sequence_shape_counts = count_by(
        conn,
        "SELECT data_shape, count(*) FROM image_sequence GROUP BY data_shape",
    )

    si_rows = session_si_counts(conn)
    si_counts = [int(row["si_count"] or 0) for row in si_rows]
    standalone_image_counts = session_image_counts(conn, standalone_only=True)
    image_sequence_counts = session_image_counts(conn, standalone_only=False)

    si_event_pixels = [
        int(row["si_width_px"]) * int(row["si_height_px"])
        for row in conn.execute(
            "SELECT si_width_px, si_height_px FROM si_events WHERE si_width_px IS NOT NULL AND si_height_px IS NOT NULL"
        )
    ]
    si_assets_with_xy = [
        int(row["x_size"]) * int(row["y_size"])
        for row in conn.execute(
            """
            SELECT x.size AS x_size, y.size AS y_size
            FROM si_event_assets a
            JOIN meta.axes x ON x.signal_id = a.signal_id AND lower(x.name) = 'x'
            JOIN meta.axes y ON y.signal_id = a.signal_id AND lower(y.name) = 'y'
            WHERE a.asset_role = 'PRIMARY_SI_SIGNAL'
            """
        )
    ]

    transition_counts = [
        float(row[0])
        for row in conn.execute("SELECT transition_count FROM trajectory_summary")
        if row[0] is not None
    ]
    trajectory_hours = [
        float(row[0]) / 3600.0
        for row in conn.execute("SELECT total_elapsed_seconds FROM trajectory_summary WHERE total_elapsed_seconds IS NOT NULL")
        if row[0] is not None
    ]

    return {
        "created_utc": dt.datetime.now(dt.UTC).isoformat(),
        "source_databases": {
            "metadata_db": str(metadata_db),
            "sessions_db": str(sessions_db),
            "trajectories_db": str(trajectories_db),
        },
        "conventions": {
            "sample_unit": sample_source,
            "topline_stem_images": "standalone_stem_images",
            "standalone_stem_images": (
                "image_sequence records excluding SI survey images; ADF SI-raster child images "
                "are already separated into adf_si_raster_images"
            ),
            "spectrum_images": "session-assigned PRIMARY_SI_SIGNAL assets in SI events",
        },
        "metadata_database": {
            "files": int(scalar(conn, "SELECT count(*) FROM meta.files")),
            "signals": int(scalar(conn, "SELECT count(*) FROM meta.signals")),
            "axes": int(scalar(conn, "SELECT count(*) FROM meta.axes")),
            "metadata_kv": int(scalar(conn, "SELECT count(*) FROM meta.metadata_kv")),
            "load_errors": int(scalar(conn, "SELECT count(*) FROM meta.files WHERE load_status = 'load_error'")),
        },
        "topline": {
            "standalone_stem_images": standalone_stem_count,
            "eds_spectrum_images": int(
                scalar(
                    conn,
                    "SELECT count(*) FROM si_event_assets WHERE asset_role = 'PRIMARY_SI_SIGNAL' AND modality = 'EDS'",
                )
            ),
            "eels_spectrum_images": int(
                scalar(
                    conn,
                    "SELECT count(*) FROM si_event_assets WHERE asset_role = 'PRIMARY_SI_SIGNAL' AND modality = 'EELS'",
                )
            ),
            "si_events": int(scalar(conn, "SELECT count(*) FROM si_events")),
            "sample_sessions": int(scalar(conn, "SELECT count(*) FROM sample_sessions")),
            "distinct_sample_labels": int(scalar(conn, "SELECT count(DISTINCT sample_name) FROM sample_sessions")),
            "trajectories": int(scalar(conn, "SELECT count(*) FROM trajectory_summary")),
        },
        "images": {
            "standalone_stem_images": standalone_stem_count,
            "image_sequence_including_si_surveys": image_sequence_count,
            "si_survey_images": si_survey_count,
            "adf_si_raster_child_images": adf_si_raster_count,
            "raw_metadata_image_signals": int(scalar(conn, "SELECT count(*) FROM meta.signals WHERE category = 'image'")),
            "standalone_shape_top": top_counts(standalone_shape_counts, standalone_stem_count, args.top_shapes),
            "standalone_shape_counts": standalone_shape_counts,
            "image_sequence_shape_top": top_counts(image_sequence_shape_counts, image_sequence_count, args.top_shapes),
        },
        "spectrum_images": {
            "session_assigned_primary_assets": {
                "eds": int(
                    scalar(
                        conn,
                        "SELECT count(*) FROM si_event_assets WHERE asset_role = 'PRIMARY_SI_SIGNAL' AND modality = 'EDS'",
                    )
                ),
                "eels": int(
                    scalar(
                        conn,
                        "SELECT count(*) FROM si_event_assets WHERE asset_role = 'PRIMARY_SI_SIGNAL' AND modality = 'EELS'",
                    )
                ),
                "total": int(scalar(conn, "SELECT count(*) FROM si_event_assets WHERE asset_role = 'PRIMARY_SI_SIGNAL'")),
            },
            "all_metadata_navigation_spectrum_signals": {
                "eds": int(
                    scalar(conn, "SELECT count(*) FROM meta.signals WHERE category = 'eds' AND coalesce(navigation_dimension, 0) >= 1")
                ),
                "eels": int(
                    scalar(conn, "SELECT count(*) FROM meta.signals WHERE category = 'eels' AND coalesce(navigation_dimension, 0) >= 1")
                ),
            },
        },
        "si_events": {
            "total": int(scalar(conn, "SELECT count(*) FROM si_events")),
            "by_modality": count_by(conn, "SELECT modality, count(*) FROM si_events GROUP BY modality"),
            "with_1000_to_10000_pixels": count_percent(
                sum(1000 <= value <= 10000 for value in si_event_pixels),
                len(si_event_pixels),
            )
            | {"denominator": len(si_event_pixels)},
            "primary_spectrum_assets_with_1000_to_10000_spatial_pixels": count_percent(
                sum(1000 <= value <= 10000 for value in si_assets_with_xy),
                len(si_assets_with_xy),
            )
            | {"denominator": len(si_assets_with_xy)},
        },
        "sessions": {
            "total": len(standalone_image_counts),
            "with_si": count_percent(sum(value > 0 for value in si_counts), len(si_counts)),
            "standalone_images_per_session_bins": binned_session_counts(standalone_image_counts),
            "image_sequence_per_session_bins_including_si_surveys": binned_session_counts(image_sequence_counts),
            "standalone_images_per_session_summary": numeric_summary([float(value) for value in standalone_image_counts]),
            "si_per_session_summary": numeric_summary([float(value) for value in si_counts]),
        },
        "samples": {
            "sample_sessions": int(scalar(conn, "SELECT count(*) FROM sample_sessions")),
            "distinct_sample_labels": int(scalar(conn, "SELECT count(DISTINCT sample_name) FROM sample_sessions")),
            "spectral_coverage": spectral_sample_coverage(si_rows),
        },
        "trajectories": {
            "total": int(scalar(conn, "SELECT count(*) FROM trajectory_summary")),
            "transitions": int(scalar(conn, "SELECT count(*) FROM image_transitions")),
            "transitions_per_trajectory_summary": numeric_summary(transition_counts),
            "duration_hours_summary": numeric_summary(trajectory_hours),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export paper-ready STEM-Bank-26 data breakdown statistics as JSON.")
    inventory_dir = default_inventory_dir()
    parser.add_argument(
        "--metadata-db",
        default=str(inventory_dir / "stem_bank_26_metadata.sqlite"),
    )
    parser.add_argument(
        "--sessions-db",
        default=str(inventory_dir / "stem_bank_26_sessions_by_acq_date_sample.sqlite"),
    )
    parser.add_argument(
        "--trajectories-db",
        default=str(inventory_dir / "stem_bank_26_trajectories_by_acq_date_sample.sqlite"),
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(inventory_dir / "paper_data_breakdown.json"),
    )
    parser.add_argument("--top-shapes", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    breakdown = build_breakdown(args)
    output.write_text(json.dumps(breakdown, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    print(json.dumps(breakdown["topline"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
