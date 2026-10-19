#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from inventory_paths import default_inventory_dir

from build_histogram_datasets import ELEMENTS, infer_elements, prepare_image_magnifications


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def write_rows_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
            count += 1
    return count


def query_dicts(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql)]


def attach(conn: sqlite3.Connection, schema: str, path: Path) -> None:
    conn.execute(f"ATTACH DATABASE ? AS {schema}", (str(path),))


def attached_table_exists(conn: sqlite3.Connection, schema: str, table: str) -> bool:
    return (
        conn.execute(
            f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def prepare_metric_views(conn: sqlite3.Connection) -> str:
    has_refined_samples = all(
        attached_table_exists(conn, "sess", table)
        for table in ("refined_sample_sessions", "refined_sample_files")
    )
    conn.executescript(
        """
        DROP VIEW IF EXISTS metric_sample_sessions;
        DROP VIEW IF EXISTS metric_file_to_session;
        """
    )
    if has_refined_samples:
        conn.executescript(
            """
            CREATE TEMP VIEW metric_sample_sessions AS
            SELECT
                rss.refined_session_id,
                rss.refined_session_key,
                rss.acquisition_date,
                rss.refined_sample,
                coalesce(count(DISTINCT rsm.session_id), 0) AS source_session_count,
                rss.file_count,
                rss.signal_count,
                rss.total_size_bytes,
                rss.first_timestamp,
                rss.last_timestamp
            FROM sess.refined_sample_sessions rss
            LEFT JOIN sess.refined_sample_session_members rsm
              ON rsm.refined_session_id = rss.refined_session_id
            GROUP BY rss.refined_session_id;

            CREATE TEMP VIEW metric_file_to_session AS
            SELECT
                rsf.source_file_id,
                rss.refined_session_id,
                rss.refined_session_key,
                rss.acquisition_date,
                rss.refined_sample
            FROM sess.refined_sample_files rsf
            JOIN sess.refined_sample_sessions rss
              ON rss.refined_session_key = rsf.refined_session_key;
            """
        )
        return "refined_sample_sessions"

    conn.executescript(
        """
        CREATE TEMP VIEW metric_sample_sessions AS
        SELECT
            s.session_id AS refined_session_id,
            s.session_key AS refined_session_key,
            coalesce(nullif(s.first_acquisition_date, ''), nullif(s.last_acquisition_date, ''), '') AS acquisition_date,
            s.inferred_sample AS refined_sample,
            1 AS source_session_count,
            s.file_count,
            s.signal_count,
            s.total_size_bytes,
            '' AS first_timestamp,
            '' AS last_timestamp
        FROM sess.sessions s;

        CREATE TEMP VIEW metric_file_to_session AS
        SELECT
            sf.source_file_id,
            s.session_id AS refined_session_id,
            s.session_key AS refined_session_key,
            coalesce(nullif(s.first_acquisition_date, ''), nullif(s.last_acquisition_date, ''), '') AS acquisition_date,
            s.inferred_sample AS refined_sample
        FROM sess.session_files sf
        JOIN sess.sessions s ON s.session_id = sf.session_id;
        """
    )
    return "sessions"


def export(args: argparse.Namespace) -> int:
    metadata_db = Path(args.metadata_db)
    sessions_db = Path(args.sessions_db)
    trajectories_db = Path(args.trajectories_db)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = connect(trajectories_db)
    attach(conn, "meta", metadata_db)
    attach(conn, "sess", sessions_db)
    sample_source = prepare_metric_views(conn)
    prepare_image_magnifications(conn)

    outputs: list[dict[str, Any]] = []

    def save(name: str, description: str, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
        csv_path = output_dir / f"{name}.csv"
        row_count = write_rows_csv(csv_path, fieldnames, rows)
        outputs.append(
            {
                "metric": name,
                "description": description,
                "file": csv_path.name,
                "rows": row_count,
                "columns": fieldnames,
            }
        )

    save(
        "image_magnification",
        "One row per STEM image in image_sequence; ADF SI-raster child images are excluded.",
        [
            "image_id",
            "session_id",
            "session_key",
            "refined_session_id",
            "refined_session_key",
            "acquisition_date",
            "refined_sample",
            "trajectory_id",
            "trajectory_key",
            "sequence_index",
            "trajectory_sequence_index",
            "source_file_id",
            "signal_id",
            "acquisition_timestamp",
            "magnification",
            "magnification_text",
            "title",
            "path",
        ],
        query_dicts(
            conn,
            """
            SELECT
                i.image_id,
                i.session_id,
                i.session_key,
                m.refined_session_id,
                m.refined_session_key,
                m.acquisition_date,
                m.refined_sample,
                i.trajectory_id,
                i.trajectory_key,
                i.sequence_index,
                i.trajectory_sequence_index,
                i.source_file_id,
                i.signal_id,
                i.acquisition_timestamp,
                mag.magnification AS magnification,
                i.magnification_text,
                i.title,
                i.path
            FROM image_sequence i
            LEFT JOIN metric_image_magnifications mag ON mag.image_id = i.image_id
            LEFT JOIN metric_file_to_session m ON m.source_file_id = i.source_file_id
            ORDER BY m.refined_session_id, i.session_id, i.sequence_index, i.image_id
            """,
        ),
    )

    trajectory_rows = query_dicts(
        conn,
        """
        SELECT
            trajectory_id,
            trajectory_key,
            session_id,
            session_key,
            trajectory_index,
            image_count,
            transition_count,
            first_acquisition_timestamp,
            last_acquisition_timestamp,
            total_elapsed_seconds
        FROM trajectory_summary
        ORDER BY session_id, trajectory_index
        """,
    )
    save(
        "trajectory_length_transitions",
        "One row per split trajectory segment, with transition_count as the metric value.",
        [
            "trajectory_id",
            "trajectory_key",
            "session_id",
            "session_key",
            "trajectory_index",
            "image_count",
            "transition_count",
            "first_acquisition_timestamp",
            "last_acquisition_timestamp",
            "total_elapsed_seconds",
        ],
        trajectory_rows,
    )
    save(
        "trajectory_length_time",
        "One row per split trajectory segment, with total_elapsed_seconds as the metric value.",
        [
            "trajectory_id",
            "trajectory_key",
            "session_id",
            "session_key",
            "trajectory_index",
            "image_count",
            "transition_count",
            "first_acquisition_timestamp",
            "last_acquisition_timestamp",
            "total_elapsed_seconds",
        ],
        trajectory_rows,
    )

    save(
        "images_per_session",
        "One row per refined date/sample session; image_count excludes SI surveys, including sessions with zero standalone images.",
        [
            "refined_session_id",
            "refined_session_key",
            "acquisition_date",
            "refined_sample",
            "source_session_count",
            "file_count",
            "signal_count",
            "trajectory_count",
            "image_count",
            "transition_count",
        ],
        query_dicts(
            conn,
            """
            SELECT
                ms.refined_session_id,
                ms.refined_session_key,
                ms.acquisition_date,
                ms.refined_sample,
                ms.source_session_count,
                ms.file_count,
                ms.signal_count,
                coalesce(ic.trajectory_count, 0) AS trajectory_count,
                coalesce(ic.image_count, 0) AS image_count,
                coalesce(tc.transition_count, 0) AS transition_count
            FROM metric_sample_sessions ms
            LEFT JOIN (
                SELECT
                    m.refined_session_id,
                    count(DISTINCT i.trajectory_id) AS trajectory_count,
                    count(DISTINCT CASE WHEN NOT (lower(coalesce(i.title, '')) LIKE '%si survey%' OR lower(coalesce(i.path, '')) LIKE '%si survey%') THEN i.image_id END) AS image_count
                FROM image_sequence i
                JOIN metric_file_to_session m ON m.source_file_id = i.source_file_id
                GROUP BY m.refined_session_id
            ) ic ON ic.refined_session_id = ms.refined_session_id
            LEFT JOIN (
                SELECT
                    p.refined_session_id,
                    count(DISTINCT t.transition_id) AS transition_count
                FROM image_transitions t
                JOIN metric_file_to_session p ON p.source_file_id = t.prev_source_file_id
                JOIN metric_file_to_session c
                  ON c.source_file_id = t.curr_source_file_id
                 AND c.refined_session_id = p.refined_session_id
                GROUP BY p.refined_session_id
            ) tc ON tc.refined_session_id = ms.refined_session_id
            ORDER BY ms.refined_session_id
            """,
        ),
    )

    save(
        "si_per_session",
        "One row per refined date/sample session, including sessions with zero SI events.",
        [
            "refined_session_id",
            "refined_session_key",
            "acquisition_date",
            "refined_sample",
            "source_session_count",
            "si_count",
            "eds_only_si_count",
            "eels_only_si_count",
            "eds_plus_eels_si_count",
            "si_with_eds_count",
            "si_with_eels_count",
            "adf_si_raster_child_image_count",
        ],
        query_dicts(
            conn,
            """
            SELECT
                ms.refined_session_id,
                ms.refined_session_key,
                ms.acquisition_date,
                ms.refined_sample,
                ms.source_session_count,
                coalesce(sc.si_count, 0) AS si_count,
                coalesce(sc.eds_only_si_count, 0) AS eds_only_si_count,
                coalesce(sc.eels_only_si_count, 0) AS eels_only_si_count,
                coalesce(sc.eds_plus_eels_si_count, 0) AS eds_plus_eels_si_count,
                coalesce(sc.si_with_eds_count, 0) AS si_with_eds_count,
                coalesce(sc.si_with_eels_count, 0) AS si_with_eels_count,
                coalesce(sc.adf_si_raster_child_image_count, 0) AS adf_si_raster_child_image_count
            FROM metric_sample_sessions ms
            LEFT JOIN (
                SELECT
                    m.refined_session_id,
                    count(DISTINCT e.si_event_id) AS si_count,
                    count(DISTINCT CASE WHEN e.modality = 'EDS' THEN e.si_event_id END) AS eds_only_si_count,
                    count(DISTINCT CASE WHEN e.modality = 'EELS' THEN e.si_event_id END) AS eels_only_si_count,
                    count(DISTINCT CASE WHEN e.modality = 'EDS+EELS' THEN e.si_event_id END) AS eds_plus_eels_si_count,
                    count(DISTINCT CASE WHEN e.modality IN ('EDS', 'EDS+EELS') THEN e.si_event_id END) AS si_with_eds_count,
                    count(DISTINCT CASE WHEN e.modality IN ('EELS', 'EDS+EELS') THEN e.si_event_id END) AS si_with_eels_count,
                    count(DISTINCT ar.adf_si_raster_image_id) AS adf_si_raster_child_image_count
                FROM si_events e
                JOIN si_event_assets a
                  ON a.si_event_id = e.si_event_id
                 AND a.asset_role = 'PRIMARY_SI_SIGNAL'
                JOIN metric_file_to_session m ON m.source_file_id = a.source_file_id
                LEFT JOIN adf_si_raster_images ar ON ar.si_event_id = e.si_event_id
                GROUP BY m.refined_session_id
            ) sc ON sc.refined_session_id = ms.refined_session_id
            ORDER BY ms.refined_session_id
            """,
        ),
    )

    save(
        "sessions_per_sample",
        "One row per date-scoped refined sample, with number of source sessions assigned to that sample.",
        [
            "refined_session_id",
            "refined_session_key",
            "acquisition_date",
            "refined_sample",
            "source_session_count",
            "file_count",
            "signal_count",
        ],
        query_dicts(
            conn,
            """
            SELECT
                refined_session_id,
                refined_session_key,
                acquisition_date,
                refined_sample,
                source_session_count,
                file_count,
                signal_count
            FROM metric_sample_sessions
            ORDER BY source_session_count DESC, refined_sample, acquisition_date
            """,
        ),
    )

    element_rows = []
    for row in conn.execute(
        """
        SELECT
            refined_session_id,
            refined_session_key,
            acquisition_date,
            refined_sample,
            source_session_count,
            file_count,
            signal_count
        FROM metric_sample_sessions
        ORDER BY refined_session_id
        """
    ):
        elements = sorted(infer_elements(row["refined_sample"]))
        if not elements:
            element_rows.append(
                {
                    "refined_session_id": row["refined_session_id"],
                    "refined_session_key": row["refined_session_key"],
                    "acquisition_date": row["acquisition_date"],
                    "refined_sample": row["refined_sample"],
                    "source_session_count": row["source_session_count"],
                    "file_count": row["file_count"],
                    "signal_count": row["signal_count"],
                    "element_symbol": "",
                    "element_name": "",
                    "present": 0,
                    "elements_inferred": "",
                    "element_count": 0,
                }
            )
            continue
        for symbol in elements:
            element_rows.append(
                {
                    "refined_session_id": row["refined_session_id"],
                    "refined_session_key": row["refined_session_key"],
                    "acquisition_date": row["acquisition_date"],
                    "refined_sample": row["refined_sample"],
                    "source_session_count": row["source_session_count"],
                    "file_count": row["file_count"],
                    "signal_count": row["signal_count"],
                    "element_symbol": symbol,
                    "element_name": ELEMENTS[symbol],
                    "present": 1,
                    "elements_inferred": ";".join(elements),
                    "element_count": len(elements),
                }
            )
    save(
        "element_presence_by_session",
        "Long-form element presence inferred from the refined sample string. Refined sessions with no inferred element have present=0 and blank element fields.",
        [
            "refined_session_id",
            "refined_session_key",
            "acquisition_date",
            "refined_sample",
            "source_session_count",
            "file_count",
            "signal_count",
            "element_symbol",
            "element_name",
            "present",
            "elements_inferred",
            "element_count",
        ],
        element_rows,
    )

    save(
        "si_size_pixels",
        "One row per SI event, with size_pixels = si_width_px * si_height_px.",
        [
            "si_event_id",
            "session_id",
            "session_key",
            "refined_session_id",
            "refined_session_key",
            "acquisition_date",
            "refined_sample",
            "sequence_index",
            "acquisition_timestamp",
            "modality",
            "signal_count",
            "si_width_px",
            "si_height_px",
            "size_pixels",
            "si_x_pixel_size_um",
            "si_y_pixel_size_um",
            "si_scan_width_um",
            "si_scan_height_um",
            "si_pixel_time_s",
            "titles",
            "paths",
        ],
        query_dicts(
            conn,
            """
            SELECT
                e.si_event_id,
                e.session_id,
                e.session_key,
                m.refined_session_id,
                m.refined_session_key,
                m.acquisition_date,
                m.refined_sample,
                e.sequence_index,
                e.acquisition_timestamp,
                e.modality,
                e.signal_count,
                e.si_width_px,
                e.si_height_px,
                CASE
                    WHEN e.si_width_px IS NOT NULL AND e.si_height_px IS NOT NULL
                    THEN e.si_width_px * e.si_height_px
                END AS size_pixels,
                e.si_x_pixel_size_um,
                e.si_y_pixel_size_um,
                e.si_scan_width_um,
                e.si_scan_height_um,
                e.si_pixel_time_s,
                e.titles,
                e.paths
            FROM si_events e
            LEFT JOIN (
                SELECT
                    a.si_event_id,
                    min(m.refined_session_id) AS refined_session_id,
                    min(m.refined_session_key) AS refined_session_key,
                    min(m.acquisition_date) AS acquisition_date,
                    min(m.refined_sample) AS refined_sample
                FROM si_event_assets a
                JOIN metric_file_to_session m ON m.source_file_id = a.source_file_id
                WHERE a.asset_role = 'PRIMARY_SI_SIGNAL'
                GROUP BY a.si_event_id
            ) m ON m.si_event_id = e.si_event_id
            ORDER BY m.refined_session_id, e.sequence_index, e.si_event_id
            """,
        ),
    )

    save(
        "eels_target_energy_loss",
        "One row per primary EELS SI signal asset with raw GMS target energy loss when present.",
        [
            "si_event_asset_id",
            "si_event_id",
            "session_id",
            "session_key",
            "refined_session_id",
            "refined_session_key",
            "acquisition_date",
            "refined_sample",
            "source_file_id",
            "signal_id",
            "acquisition_timestamp",
            "target_energy_loss_ev",
            "title",
            "path",
            "data_shape",
            "data_dtype",
        ],
        query_dicts(
            conn,
            """
            SELECT
                a.si_event_asset_id,
                a.si_event_id,
                a.session_id,
                a.session_key,
                m.refined_session_id,
                m.refined_session_key,
                m.acquisition_date,
                m.refined_sample,
                a.source_file_id,
                a.signal_id,
                a.acquisition_timestamp,
                kv.value_number AS target_energy_loss_ev,
                a.title,
                a.path,
                a.data_shape,
                a.data_dtype
            FROM si_event_assets a
            LEFT JOIN metric_file_to_session m ON m.source_file_id = a.source_file_id
            LEFT JOIN meta.metadata_kv kv
              ON kv.signal_id = a.signal_id
             AND kv.source = 'hyperspy_original_metadata'
             AND kv.key_path = 'ImageList.TagGroup0.ImageTags.EELS Spectrometer.Energy loss (eV)'
            WHERE a.asset_role = 'PRIMARY_SI_SIGNAL'
              AND a.modality = 'EELS'
            ORDER BY m.refined_session_id, a.si_event_id, a.si_event_asset_id
            """,
        ),
    )

    save(
        "eels_dispersion_ev_per_channel",
        "One row per primary EELS SI signal asset with raw GMS dispersion/channel width when present.",
        [
            "si_event_asset_id",
            "si_event_id",
            "session_id",
            "session_key",
            "refined_session_id",
            "refined_session_key",
            "acquisition_date",
            "refined_sample",
            "source_file_id",
            "signal_id",
            "acquisition_timestamp",
            "eels_dispersion_ev_per_channel",
            "title",
            "path",
            "data_shape",
            "data_dtype",
        ],
        query_dicts(
            conn,
            """
            SELECT
                a.si_event_asset_id,
                a.si_event_id,
                a.session_id,
                a.session_key,
                m.refined_session_id,
                m.refined_session_key,
                m.acquisition_date,
                m.refined_sample,
                a.source_file_id,
                a.signal_id,
                a.acquisition_timestamp,
                kv.value_number AS eels_dispersion_ev_per_channel,
                a.title,
                a.path,
                a.data_shape,
                a.data_dtype
            FROM si_event_assets a
            LEFT JOIN metric_file_to_session m ON m.source_file_id = a.source_file_id
            LEFT JOIN meta.metadata_kv kv
              ON kv.signal_id = a.signal_id
             AND kv.source = 'hyperspy_original_metadata'
             AND kv.key_path = 'ImageList.TagGroup0.ImageTags.EELS Spectrometer.Dispersion (eV/ch)'
            WHERE a.asset_role = 'PRIMARY_SI_SIGNAL'
              AND a.modality = 'EELS'
            ORDER BY m.refined_session_id, a.si_event_id, a.si_event_asset_id
            """,
        ),
    )

    manifest = {
        "created_utc": dt.datetime.now(dt.UTC).isoformat(),
        "description": "Raw row-level CSV metric datasets for flexible plotting; no histogram bins are precomputed.",
        "source_databases": {
            "metadata_db": str(metadata_db.resolve()),
            "sessions_db": str(sessions_db.resolve()),
            "trajectories_db": str(trajectories_db.resolve()),
        },
        "sample_source": sample_source,
        "outputs": outputs,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    print(f"Wrote {output_dir}")
    print(f"Wrote {manifest_path}")
    for output in outputs:
        print(f"{output['file']}: {output['rows']} rows")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export row-level metric CSV datasets from STEM-Bank-26 metadata/session/trajectory SQLite databases."
    )
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
        "--output-dir",
        default=str(inventory_dir / "metric_csvs"),
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(export(parse_args()))
