#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import os
import shutil
import sqlite3
import sys
from pathlib import Path


MAX_TRAJECTORY_GAP_SECONDS = 3600
MAX_TRAJECTORY_STAGE_MOVE_UM = 50.0
STAGE_POSITION_UNITS_TO_UM = 1.0
DM_MAGNIFICATION_KEY = "ImageList.TagGroup0.ImageTags.Microscope Info.Indicated Magnification"

DM_STAGE_KEYS = {
    "stage_x": "ImageList.TagGroup0.ImageTags.Microscope Info.Stage Position.Stage X",
    "stage_y": "ImageList.TagGroup0.ImageTags.Microscope Info.Stage Position.Stage Y",
    "stage_z": "ImageList.TagGroup0.ImageTags.Microscope Info.Stage Position.Stage Z",
    "stage_tilt_alpha": "ImageList.TagGroup0.ImageTags.Microscope Info.Stage Position.Stage Alpha",
    "stage_tilt_beta": "ImageList.TagGroup0.ImageTags.Microscope Info.Stage Position.Stage Beta",
}

ACTION_FIELDS = {
    **DM_STAGE_KEYS,
    "magnification": "Acquisition_instrument.TEM.magnification",
    "beam_energy": "Acquisition_instrument.TEM.beam_energy",
    "beam_current": "Acquisition_instrument.TEM.beam_current",
    "camera_length": "Acquisition_instrument.TEM.camera_length",
    "dwell_time": "Acquisition_instrument.TEM.dwell_time",
    "field_of_view_um": "ImageList.TagGroup0.ImageTags.Microscope Info.Field of View (µm)",
    "probe_current_na": "ImageList.TagGroup0.ImageTags.Microscope Info.Probe Current (nA)",
    "probe_size_nm": "ImageList.TagGroup0.ImageTags.Microscope Info.Probe Size (nm)",
    "formatted_magnification": "ImageList.TagGroup0.ImageTags.Microscope Info.Formatted Indicated Mag",
    "formatted_voltage": "ImageList.TagGroup0.ImageTags.Microscope Info.Formatted Voltage",
}

PLACEHOLDER_FIELDS = [
    "focus",
    "defocus",
    "objective_lens_current",
    "condenser_lens_current",
    "projector_lens_current",
    "stigmation_x",
    "stigmation_y",
]


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    value_columns = []
    delta_columns = []
    for field in ACTION_FIELDS:
        value_columns.append(f"prev_{field}_text TEXT NOT NULL DEFAULT ''")
        value_columns.append(f"curr_{field}_text TEXT NOT NULL DEFAULT ''")
        value_columns.append(f"prev_{field}_number REAL")
        value_columns.append(f"curr_{field}_number REAL")
        delta_columns.append(f"delta_{field} REAL")
    for field in PLACEHOLDER_FIELDS:
        value_columns.append(f"prev_{field}_text TEXT NOT NULL DEFAULT ''")
        value_columns.append(f"curr_{field}_text TEXT NOT NULL DEFAULT ''")
        value_columns.append(f"delta_{field} REAL")

    conn.executescript(
        f"""
        CREATE TABLE run_info (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE image_sequence (
            image_id INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL,
            session_key TEXT NOT NULL,
            trajectory_id INTEGER NOT NULL,
            trajectory_key TEXT NOT NULL,
            trajectory_sequence_index INTEGER NOT NULL,
            source_file_id INTEGER NOT NULL,
            signal_id INTEGER NOT NULL,
            path TEXT NOT NULL,
            title TEXT NOT NULL,
            acquisition_date TEXT NOT NULL,
            acquisition_time TEXT NOT NULL,
            acquisition_timestamp TEXT NOT NULL,
            sequence_index INTEGER NOT NULL,
            data_shape TEXT NOT NULL,
            data_dtype TEXT NOT NULL,
            {", ".join(f"{field}_text TEXT NOT NULL DEFAULT ''" for field in ACTION_FIELDS)},
            {", ".join(f"{field}_number REAL" for field in ACTION_FIELDS)},
            {", ".join(f"{field}_text TEXT NOT NULL DEFAULT ''" for field in PLACEHOLDER_FIELDS)}
        );

        CREATE TABLE trajectory_breaks (
            break_id INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL,
            session_key TEXT NOT NULL,
            prev_image_id INTEGER NOT NULL REFERENCES image_sequence(image_id) ON DELETE CASCADE,
            curr_image_id INTEGER NOT NULL REFERENCES image_sequence(image_id) ON DELETE CASCADE,
            prev_sequence_index INTEGER NOT NULL,
            curr_sequence_index INTEGER NOT NULL,
            prev_acquisition_timestamp TEXT NOT NULL,
            curr_acquisition_timestamp TEXT NOT NULL,
            delta_seconds REAL,
            delta_stage_x REAL,
            delta_stage_y REAL,
            stage_xy_distance_um REAL,
            split_due_to_time INTEGER NOT NULL,
            split_due_to_stage_move INTEGER NOT NULL,
            split_reason TEXT NOT NULL
        );

        CREATE TABLE image_transitions (
            transition_id INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL,
            session_key TEXT NOT NULL,
            trajectory_id INTEGER NOT NULL,
            trajectory_key TEXT NOT NULL,
            prev_image_id INTEGER NOT NULL REFERENCES image_sequence(image_id) ON DELETE CASCADE,
            curr_image_id INTEGER NOT NULL REFERENCES image_sequence(image_id) ON DELETE CASCADE,
            prev_source_file_id INTEGER NOT NULL,
            curr_source_file_id INTEGER NOT NULL,
            prev_signal_id INTEGER NOT NULL,
            curr_signal_id INTEGER NOT NULL,
            prev_path TEXT NOT NULL,
            curr_path TEXT NOT NULL,
            prev_title TEXT NOT NULL,
            curr_title TEXT NOT NULL,
            prev_acquisition_timestamp TEXT NOT NULL,
            curr_acquisition_timestamp TEXT NOT NULL,
            delta_seconds REAL,
            prev_sequence_index INTEGER NOT NULL,
            curr_sequence_index INTEGER NOT NULL,
            prev_trajectory_sequence_index INTEGER NOT NULL,
            curr_trajectory_sequence_index INTEGER NOT NULL,
            {", ".join(value_columns)},
            {", ".join(delta_columns)}
        );

        CREATE TABLE transition_changes (
            transition_id INTEGER NOT NULL REFERENCES image_transitions(transition_id) ON DELETE CASCADE,
            session_id INTEGER NOT NULL,
            session_key TEXT NOT NULL,
            trajectory_id INTEGER NOT NULL,
            trajectory_key TEXT NOT NULL,
            field TEXT NOT NULL,
            prev_value_text TEXT NOT NULL,
            curr_value_text TEXT NOT NULL,
            prev_value_number REAL,
            curr_value_number REAL,
            delta_number REAL,
            changed INTEGER NOT NULL,
            PRIMARY KEY (transition_id, field)
        );

        CREATE TABLE trajectory_summary (
            trajectory_id INTEGER PRIMARY KEY,
            trajectory_key TEXT NOT NULL,
            session_id INTEGER NOT NULL,
            session_key TEXT NOT NULL,
            trajectory_index INTEGER NOT NULL,
            image_count INTEGER NOT NULL,
            transition_count INTEGER NOT NULL,
            first_acquisition_timestamp TEXT NOT NULL,
            last_acquisition_timestamp TEXT NOT NULL,
            total_elapsed_seconds REAL,
            stage_move_count INTEGER NOT NULL,
            magnification_change_count INTEGER NOT NULL,
            beam_energy_change_count INTEGER NOT NULL,
            camera_length_change_count INTEGER NOT NULL,
            dwell_time_change_count INTEGER NOT NULL
        );

        CREATE TABLE session_trajectory_summary (
            session_id INTEGER PRIMARY KEY,
            session_key TEXT NOT NULL,
            trajectory_count INTEGER NOT NULL,
            split_break_count INTEGER NOT NULL,
            time_split_count INTEGER NOT NULL,
            stage_move_split_count INTEGER NOT NULL,
            image_count INTEGER NOT NULL,
            transition_count INTEGER NOT NULL,
            first_acquisition_timestamp TEXT NOT NULL,
            last_acquisition_timestamp TEXT NOT NULL,
            total_elapsed_seconds REAL,
            stage_move_count INTEGER NOT NULL,
            magnification_change_count INTEGER NOT NULL,
            beam_energy_change_count INTEGER NOT NULL,
            camera_length_change_count INTEGER NOT NULL,
            dwell_time_change_count INTEGER NOT NULL
        );

        CREATE TABLE si_events (
            si_event_id INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL,
            session_key TEXT NOT NULL,
            acquisition_folder TEXT NOT NULL,
            acquisition_date TEXT NOT NULL,
            acquisition_time TEXT NOT NULL,
            acquisition_timestamp TEXT NOT NULL,
            sequence_index INTEGER NOT NULL,
            modality TEXT NOT NULL,
            signal_count INTEGER NOT NULL,
            source_file_ids TEXT NOT NULL,
            signal_ids TEXT NOT NULL,
            titles TEXT NOT NULL,
            paths TEXT NOT NULL,
            stage_x REAL,
            stage_y REAL,
            stage_z REAL,
            stage_tilt_alpha REAL,
            stage_tilt_beta REAL,
            magnification REAL,
            field_of_view_um REAL,
            si_width_px INTEGER,
            si_height_px INTEGER,
            si_x_pixel_size_um REAL,
            si_y_pixel_size_um REAL,
            si_scan_width_um REAL,
            si_scan_height_um REAL,
            si_pixel_time_s REAL,
            eels_energy_start_ev REAL,
            eels_energy_end_ev REAL,
            eels_dispersion_ev_ch REAL,
            eels_exposure_s REAL,
            eels_frame_count REAL,
            eels_spectrometer TEXT NOT NULL DEFAULT '',
            eels_camera_number TEXT NOT NULL DEFAULT '',
            eels_camera_active_size_px TEXT NOT NULL DEFAULT '',
            eels_collection_angle_mrad REAL,
            eels_convergence_angle_mrad REAL,
            eds_energy_start_ev REAL,
            eds_energy_end_ev REAL,
            eds_dispersion_ev_ch REAL,
            eds_exposure_s REAL
        );

        CREATE TABLE adf_si_raster_images (
            adf_si_raster_image_id INTEGER PRIMARY KEY,
            si_event_id INTEGER NOT NULL REFERENCES si_events(si_event_id) ON DELETE CASCADE,
            session_id INTEGER NOT NULL,
            session_key TEXT NOT NULL,
            source_file_id INTEGER NOT NULL,
            signal_id INTEGER NOT NULL,
            acquisition_folder TEXT NOT NULL,
            acquisition_timestamp TEXT NOT NULL,
            sequence_index INTEGER NOT NULL,
            title TEXT NOT NULL,
            path TEXT NOT NULL,
            data_shape TEXT NOT NULL,
            data_dtype TEXT NOT NULL,
            si_width_px INTEGER,
            si_height_px INTEGER,
            modality TEXT NOT NULL DEFAULT '',
            stage_x REAL,
            stage_y REAL,
            stage_z REAL,
            stage_tilt_alpha REAL,
            stage_tilt_beta REAL,
            magnification REAL,
            field_of_view_um REAL,
            si_x_pixel_size_um REAL,
            si_y_pixel_size_um REAL,
            si_scan_width_um REAL,
            si_scan_height_um REAL,
            si_pixel_time_s REAL
        );

        CREATE TABLE si_event_assets (
            si_event_asset_id INTEGER PRIMARY KEY,
            si_event_id INTEGER NOT NULL REFERENCES si_events(si_event_id) ON DELETE CASCADE,
            session_id INTEGER NOT NULL,
            session_key TEXT NOT NULL,
            asset_role TEXT NOT NULL,
            is_secondary INTEGER NOT NULL,
            modality TEXT NOT NULL DEFAULT '',
            source_file_id INTEGER NOT NULL,
            signal_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            path TEXT NOT NULL,
            data_shape TEXT NOT NULL,
            data_dtype TEXT NOT NULL,
            acquisition_timestamp TEXT NOT NULL
        );

        CREATE TABLE action_sequence (
            action_id INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL,
            session_key TEXT NOT NULL,
            action_type TEXT NOT NULL,
            source_image_id INTEGER REFERENCES image_sequence(image_id) ON DELETE CASCADE,
            source_si_event_id INTEGER REFERENCES si_events(si_event_id) ON DELETE CASCADE,
            acquisition_timestamp TEXT NOT NULL,
            sequence_index INTEGER NOT NULL,
            title TEXT NOT NULL,
            path TEXT NOT NULL,
            modality TEXT NOT NULL DEFAULT '',
            stage_x REAL,
            stage_y REAL,
            stage_z REAL,
            stage_tilt_alpha REAL,
            stage_tilt_beta REAL,
            magnification REAL,
            field_of_view_um REAL,
            si_width_px INTEGER,
            si_height_px INTEGER,
            si_x_pixel_size_um REAL,
            si_y_pixel_size_um REAL,
            si_scan_width_um REAL,
            si_scan_height_um REAL,
            si_pixel_time_s REAL,
            eels_energy_start_ev REAL,
            eels_energy_end_ev REAL,
            eels_exposure_s REAL,
            eels_spectrometer TEXT NOT NULL DEFAULT '',
            eels_camera_number TEXT NOT NULL DEFAULT '',
            eds_energy_start_ev REAL,
            eds_energy_end_ev REAL,
            eds_exposure_s REAL
        );

        CREATE TABLE action_transitions (
            action_transition_id INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL,
            session_key TEXT NOT NULL,
            prev_action_id INTEGER NOT NULL REFERENCES action_sequence(action_id) ON DELETE CASCADE,
            curr_action_id INTEGER NOT NULL REFERENCES action_sequence(action_id) ON DELETE CASCADE,
            prev_action_type TEXT NOT NULL,
            curr_action_type TEXT NOT NULL,
            prev_sequence_index INTEGER NOT NULL,
            curr_sequence_index INTEGER NOT NULL,
            prev_timestamp TEXT NOT NULL,
            curr_timestamp TEXT NOT NULL,
            delta_seconds REAL,
            prev_title TEXT NOT NULL,
            curr_title TEXT NOT NULL,
            prev_path TEXT NOT NULL,
            curr_path TEXT NOT NULL,
            delta_stage_x REAL,
            delta_stage_y REAL,
            delta_stage_z REAL,
            delta_stage_tilt_alpha REAL,
            delta_stage_tilt_beta REAL,
            delta_magnification REAL,
            delta_field_of_view_um REAL,
            si_taken INTEGER NOT NULL DEFAULT 0,
            si_modality TEXT NOT NULL DEFAULT '',
            si_width_px INTEGER,
            si_height_px INTEGER,
            si_x_pixel_size_um REAL,
            si_y_pixel_size_um REAL,
            si_pixel_time_s REAL,
            eels_energy_start_ev REAL,
            eels_energy_end_ev REAL,
            eels_exposure_s REAL,
            eels_spectrometer TEXT NOT NULL DEFAULT '',
            eels_camera_number TEXT NOT NULL DEFAULT '',
            eds_energy_start_ev REAL,
            eds_energy_end_ev REAL,
            eds_exposure_s REAL
        );

        CREATE VIEW transition_overview AS
        SELECT
            session_key,
            trajectory_key,
            curr_sequence_index,
            curr_trajectory_sequence_index,
            prev_acquisition_timestamp,
            curr_acquisition_timestamp,
            delta_seconds,
            prev_title,
            curr_title,
            delta_stage_x,
            delta_stage_y,
            delta_stage_z,
            delta_stage_tilt_alpha,
            delta_stage_tilt_beta,
            delta_magnification,
            delta_field_of_view_um,
            delta_camera_length,
            delta_dwell_time
        FROM image_transitions;

        CREATE VIEW action_overview AS
        SELECT
            session_key,
            sequence_index,
            action_type,
            acquisition_timestamp,
            title,
            modality,
            stage_x,
            stage_y,
            stage_z,
            magnification,
            field_of_view_um,
            si_width_px,
            si_height_px,
            si_x_pixel_size_um,
            si_y_pixel_size_um,
            si_pixel_time_s,
            eels_energy_start_ev,
            eels_energy_end_ev,
            eels_exposure_s,
            eels_spectrometer,
            eels_camera_number,
            eds_energy_start_ev,
            eds_energy_end_ev,
            eds_exposure_s
        FROM action_sequence;
        """
    )


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX idx_image_sequence_session ON image_sequence(session_id, sequence_index);
        CREATE INDEX idx_image_sequence_trajectory ON image_sequence(trajectory_id, trajectory_sequence_index);
        CREATE INDEX idx_image_sequence_signal ON image_sequence(signal_id);
        CREATE INDEX idx_transitions_session ON image_transitions(session_id, curr_sequence_index);
        CREATE INDEX idx_transitions_trajectory ON image_transitions(trajectory_id, curr_trajectory_sequence_index);
        CREATE INDEX idx_transition_changes_field ON transition_changes(field, changed);
        CREATE INDEX idx_transition_changes_session ON transition_changes(session_id, field);
        CREATE INDEX idx_trajectory_breaks_session ON trajectory_breaks(session_id, curr_sequence_index);
        CREATE INDEX idx_trajectory_summary_session ON trajectory_summary(session_id, trajectory_index);
        CREATE INDEX idx_si_events_session ON si_events(session_id, sequence_index);
        CREATE INDEX idx_adf_si_raster_images_session ON adf_si_raster_images(session_id, sequence_index);
        CREATE INDEX idx_adf_si_raster_images_signal ON adf_si_raster_images(signal_id);
        CREATE INDEX idx_si_event_assets_event ON si_event_assets(si_event_id, asset_role);
        CREATE INDEX idx_si_event_assets_signal ON si_event_assets(signal_id);
        CREATE INDEX idx_action_sequence_session ON action_sequence(session_id, sequence_index);
        CREATE INDEX idx_action_transitions_session ON action_transitions(session_id, curr_sequence_index);
        """
    )


def write_run_info(conn: sqlite3.Connection, metadata_db: Path, sessions_db: Path) -> None:
    values = {
        "created_utc": dt.datetime.now(dt.UTC).isoformat(),
        "metadata_db": str(metadata_db.resolve()),
        "sessions_db": str(sessions_db.resolve()),
        "python": sys.version.replace("\n", " "),
        "executable": sys.executable,
        "image_filter": "signals.category = 'image' and acquisition_mode = STEM, excluding ADF SI-raster companion images",
        "trajectory_split_rules": (
            f"split when adjacent STEM images have elapsed time > {MAX_TRAJECTORY_GAP_SECONDS} seconds "
            f"or XY stage displacement > {MAX_TRAJECTORY_STAGE_MOVE_UM} microns"
        ),
        "stage_position_units_to_um": str(STAGE_POSITION_UNITS_TO_UM),
        "adf_si_raster_filter": "ADF image in an SI acquisition folder, not survey, timestamp equals SI timestamp, shape equals SI raster",
        "si_asset_model": "si_events are actions; EDS/EELS SI signals are primary assets; ADF SI-raster images are secondary child assets",
        "si_filter": "signals.category in ('eds','eels') and navigation_dimension >= 1",
        "ordering": "session_id, acquisition_date, acquisition_time, source path, signal_id",
        "blank_fields": ", ".join(PLACEHOLDER_FIELDS),
    }
    conn.executemany("INSERT INTO run_info(key, value) VALUES (?, ?)", sorted(values.items()))


def attach_sources(conn: sqlite3.Connection, metadata_db: Path, sessions_db: Path) -> None:
    conn.execute("ATTACH DATABASE ? AS meta", (str(metadata_db),))
    conn.execute("ATTACH DATABASE ? AS sess", (str(sessions_db),))


def populate_image_sequence(conn: sqlite3.Connection) -> None:
    key_values = []
    for field, key_path in ACTION_FIELDS.items():
        escaped_key_path = key_path.replace("'", "''")
        key_values.append(f"('{field}', '{escaped_key_path}')")
    key_values_sql = ", ".join(key_values)

    conn.executescript(
        f"""
        CREATE TEMP TABLE action_key_map (
            field TEXT PRIMARY KEY,
            key_path TEXT NOT NULL
        );
        INSERT INTO action_key_map(field, key_path)
        VALUES {key_values_sql};

        CREATE TEMP TABLE image_base AS
        SELECT
            sf.session_id,
            ss.session_key,
            f.file_id AS source_file_id,
            s.signal_id,
            f.path,
            s.title,
            s.acquisition_date,
            s.acquisition_time,
            CASE
                WHEN s.acquisition_date <> '' AND s.acquisition_time <> ''
                THEN s.acquisition_date || 'T' || s.acquisition_time
                ELSE ''
            END AS acquisition_timestamp,
            s.data_shape,
            s.data_dtype,
            row_number() OVER (
                PARTITION BY sf.session_id
                ORDER BY s.acquisition_date, s.acquisition_time, f.path, s.signal_id
            ) AS sequence_index
        FROM sess.session_files sf
        JOIN sess.sessions ss ON ss.session_id = sf.session_id
        JOIN meta.files f ON f.file_id = sf.source_file_id
        JOIN meta.signals s ON s.file_id = f.file_id
        LEFT JOIN meta.metadata_kv mode
            ON mode.signal_id = s.signal_id
           AND mode.source = 'hyperspy_metadata'
           AND mode.key_path = 'Acquisition_instrument.TEM.acquisition_mode'
        WHERE s.category = 'image'
          AND coalesce(mode.value_text, 'STEM') = 'STEM'
          AND NOT EXISTS (
              SELECT 1
              FROM adf_si_raster_images ar
              WHERE ar.signal_id = s.signal_id
          );

        CREATE TEMP TABLE image_values AS
        SELECT
            ib.signal_id,
            ak.field,
            kv.value_text,
            kv.value_number
        FROM image_base ib
        CROSS JOIN action_key_map ak
        LEFT JOIN meta.metadata_kv kv
            ON kv.signal_id = ib.signal_id
           AND kv.key_path = ak.key_path
           AND kv.source IN ('hyperspy_metadata', 'hyperspy_original_metadata')
        GROUP BY ib.signal_id, ak.field
        HAVING
            max(CASE WHEN kv.source = 'hyperspy_metadata' THEN 1 ELSE 0 END)
            OR max(CASE WHEN kv.source = 'hyperspy_original_metadata' THEN 1 ELSE 0 END)
            OR count(kv.kv_id) = 0;
        """
    )

    # Prefer normalized HyperSpy metadata where duplicate normalized/original keys exist.
    conn.execute("DROP TABLE image_values")
    conn.executescript(
        """
        CREATE TEMP TABLE image_values AS
        SELECT signal_id, field, value_text, value_number
        FROM (
            SELECT
                ib.signal_id,
                ak.field,
                coalesce(kv.value_text, '') AS value_text,
                kv.value_number AS value_number,
                row_number() OVER (
                    PARTITION BY ib.signal_id, ak.field
                    ORDER BY
                        CASE kv.source
                            WHEN 'hyperspy_metadata' THEN 0
                            WHEN 'hyperspy_original_metadata' THEN 1
                            ELSE 2
                        END
                ) AS rn
            FROM image_base ib
            CROSS JOIN action_key_map ak
            LEFT JOIN meta.metadata_kv kv
                ON kv.signal_id = ib.signal_id
               AND kv.key_path = ak.key_path
               AND kv.source IN ('hyperspy_metadata', 'hyperspy_original_metadata')
        )
        WHERE rn = 1;
        """
    )

    # Use the same numeric source for images and SI signals. Some DM imports
    # retain magnification only in original metadata, not the normalized key.
    magnification = magnification_number("image_values")
    conn.execute(
        f"UPDATE image_values SET value_number = {magnification}, "
        f"value_text = coalesce(CAST({magnification} AS TEXT), '') "
        "WHERE field = 'magnification'"
    )

    text_exprs = []
    number_exprs = []
    for field in ACTION_FIELDS:
        text_exprs.append(
            f"coalesce(max(case when iv.field='{field}' then iv.value_text end), '') as {field}_text"
        )
        number_exprs.append(
            f"max(case when iv.field='{field}' then iv.value_number end) as {field}_number"
        )
    placeholder_empty_values = ", ".join(f"'' as {field}_text" for field in PLACEHOLDER_FIELDS)

    conn.execute(
        f"""
        CREATE TEMP TABLE image_rows AS
        SELECT
            ib.session_id,
            ib.session_key,
            ib.source_file_id,
            ib.signal_id,
            ib.path,
            ib.title,
            ib.acquisition_date,
            ib.acquisition_time,
            ib.acquisition_timestamp,
            ib.sequence_index,
            ib.data_shape,
            ib.data_dtype,
            {", ".join(text_exprs)},
            {", ".join(number_exprs)},
            {placeholder_empty_values}
        FROM image_base ib
        LEFT JOIN image_values iv ON iv.signal_id = ib.signal_id
        GROUP BY ib.signal_id
        ORDER BY ib.session_id, ib.sequence_index
        """
    )

    conn.execute(
        f"""
        CREATE TEMP TABLE image_segmented AS
        WITH previous_values AS (
            SELECT
                image_rows.*,
                lag(acquisition_timestamp) OVER (
                    PARTITION BY session_id
                    ORDER BY sequence_index
                ) AS prev_acquisition_timestamp,
                lag(stage_x_number) OVER (
                    PARTITION BY session_id
                    ORDER BY sequence_index
                ) AS prev_stage_x_number,
                lag(stage_y_number) OVER (
                    PARTITION BY session_id
                    ORDER BY sequence_index
                ) AS prev_stage_y_number
            FROM image_rows
        ),
        break_flags AS (
            SELECT
                *,
                CASE
                    WHEN prev_acquisition_timestamp IS NOT NULL
                     AND prev_acquisition_timestamp <> ''
                     AND acquisition_timestamp <> ''
                    THEN unixepoch(acquisition_timestamp) - unixepoch(prev_acquisition_timestamp)
                END AS delta_seconds_from_prev,
                CASE
                    WHEN prev_stage_x_number IS NOT NULL
                     AND prev_stage_y_number IS NOT NULL
                     AND stage_x_number IS NOT NULL
                     AND stage_y_number IS NOT NULL
                    THEN stage_x_number - prev_stage_x_number
                END AS delta_stage_x_from_prev,
                CASE
                    WHEN prev_stage_x_number IS NOT NULL
                     AND prev_stage_y_number IS NOT NULL
                     AND stage_x_number IS NOT NULL
                     AND stage_y_number IS NOT NULL
                    THEN stage_y_number - prev_stage_y_number
                END AS delta_stage_y_from_prev
            FROM previous_values
        ),
        split_flags AS (
            SELECT
                *,
                CASE
                    WHEN delta_seconds_from_prev > {MAX_TRAJECTORY_GAP_SECONDS}
                    THEN 1 ELSE 0
                END AS split_due_to_time,
                CASE
                    WHEN delta_stage_x_from_prev IS NOT NULL
                     AND delta_stage_y_from_prev IS NOT NULL
                     AND (
                         delta_stage_x_from_prev * delta_stage_x_from_prev
                       + delta_stage_y_from_prev * delta_stage_y_from_prev
                     ) * ({STAGE_POSITION_UNITS_TO_UM} * {STAGE_POSITION_UNITS_TO_UM})
                       > ({MAX_TRAJECTORY_STAGE_MOVE_UM} * {MAX_TRAJECTORY_STAGE_MOVE_UM})
                    THEN 1 ELSE 0
                END AS split_due_to_stage_move
            FROM break_flags
        ),
        segmented AS (
            SELECT
                *,
                1 + sum(
                    CASE
                        WHEN split_due_to_time = 1 OR split_due_to_stage_move = 1
                        THEN 1 ELSE 0
                    END
                ) OVER (
                    PARTITION BY session_id
                    ORDER BY sequence_index
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS trajectory_index
            FROM split_flags
        )
        SELECT
            dense_rank() OVER (ORDER BY session_id, trajectory_index) AS trajectory_id,
            session_key || '/trajectory_' || printf('%04d', trajectory_index) AS trajectory_key,
            row_number() OVER (
                PARTITION BY session_id, trajectory_index
                ORDER BY sequence_index
            ) AS trajectory_sequence_index,
            *
        FROM segmented
        """
    )

    conn.execute(
        f"""
        INSERT INTO image_sequence (
            session_id, session_key, trajectory_id, trajectory_key,
            trajectory_sequence_index, source_file_id, signal_id, path, title,
            acquisition_date, acquisition_time, acquisition_timestamp,
            sequence_index, data_shape, data_dtype,
            {", ".join(f"{field}_text" for field in ACTION_FIELDS)},
            {", ".join(f"{field}_number" for field in ACTION_FIELDS)},
            {", ".join(f"{field}_text" for field in PLACEHOLDER_FIELDS)}
        )
        SELECT
            session_id,
            session_key,
            trajectory_id,
            trajectory_key,
            trajectory_sequence_index,
            source_file_id,
            signal_id,
            path,
            title,
            acquisition_date,
            acquisition_time,
            acquisition_timestamp,
            sequence_index,
            data_shape,
            data_dtype,
            {", ".join(f"{field}_text" for field in ACTION_FIELDS)},
            {", ".join(f"{field}_number" for field in ACTION_FIELDS)},
            {", ".join(f"{field}_text" for field in PLACEHOLDER_FIELDS)}
        FROM image_segmented
        ORDER BY session_id, sequence_index
        """
    )

    conn.execute(
        f"""
        INSERT INTO trajectory_breaks (
            session_id, session_key, prev_image_id, curr_image_id,
            prev_sequence_index, curr_sequence_index,
            prev_acquisition_timestamp, curr_acquisition_timestamp,
            delta_seconds, delta_stage_x, delta_stage_y, stage_xy_distance_um,
            split_due_to_time, split_due_to_stage_move, split_reason
        )
        SELECT
            curr.session_id,
            curr.session_key,
            prev.image_id,
            curr.image_id,
            prev.sequence_index,
            curr.sequence_index,
            prev.acquisition_timestamp,
            curr.acquisition_timestamp,
            seg.delta_seconds_from_prev,
            seg.delta_stage_x_from_prev,
            seg.delta_stage_y_from_prev,
            CASE
                WHEN seg.delta_stage_x_from_prev IS NOT NULL
                 AND seg.delta_stage_y_from_prev IS NOT NULL
                THEN sqrt(
                    seg.delta_stage_x_from_prev * seg.delta_stage_x_from_prev
                  + seg.delta_stage_y_from_prev * seg.delta_stage_y_from_prev
                ) * {STAGE_POSITION_UNITS_TO_UM}
            END,
            seg.split_due_to_time,
            seg.split_due_to_stage_move,
            CASE
                WHEN seg.split_due_to_time = 1 AND seg.split_due_to_stage_move = 1
                THEN 'time_gap_and_stage_move'
                WHEN seg.split_due_to_time = 1
                THEN 'time_gap'
                WHEN seg.split_due_to_stage_move = 1
                THEN 'stage_move'
                ELSE ''
            END
        FROM image_segmented seg
        JOIN image_sequence curr ON curr.signal_id = seg.signal_id
        JOIN image_sequence prev
          ON prev.session_id = curr.session_id
         AND prev.sequence_index = curr.sequence_index - 1
        WHERE seg.split_due_to_time = 1 OR seg.split_due_to_stage_move = 1
        ORDER BY curr.session_id, curr.sequence_index
        """
    )

    conn.execute("DROP TABLE image_base")
    conn.execute("DROP TABLE image_values")
    conn.execute("DROP TABLE image_rows")
    conn.execute("DROP TABLE image_segmented")
    conn.execute("DROP TABLE action_key_map")


def populate_transitions(conn: sqlite3.Connection) -> None:
    value_columns = []
    value_exprs = []
    delta_columns = []
    delta_exprs = []

    for field in ACTION_FIELDS:
        value_columns.extend(
            [
                f"prev_{field}_text",
                f"curr_{field}_text",
                f"prev_{field}_number",
                f"curr_{field}_number",
            ]
        )
        value_exprs.extend(
            [
                f"prev.{field}_text",
                f"curr.{field}_text",
                f"prev.{field}_number",
                f"curr.{field}_number",
            ]
        )
        delta_columns.append(f"delta_{field}")
        delta_exprs.append(
            f"case when prev.{field}_number is not null and curr.{field}_number is not null "
            f"then curr.{field}_number - prev.{field}_number end"
        )

    for field in PLACEHOLDER_FIELDS:
        value_columns.extend([f"prev_{field}_text", f"curr_{field}_text", f"delta_{field}"])
        value_exprs.extend([f"prev.{field}_text", f"curr.{field}_text", "NULL"])

    conn.execute(
        f"""
        INSERT INTO image_transitions (
            session_id, session_key, trajectory_id, trajectory_key,
            prev_image_id, curr_image_id,
            prev_source_file_id, curr_source_file_id, prev_signal_id, curr_signal_id,
            prev_path, curr_path, prev_title, curr_title,
            prev_acquisition_timestamp, curr_acquisition_timestamp, delta_seconds,
            prev_sequence_index, curr_sequence_index,
            prev_trajectory_sequence_index, curr_trajectory_sequence_index,
            {", ".join(value_columns)},
            {", ".join(delta_columns)}
        )
        SELECT
            curr.session_id,
            curr.session_key,
            curr.trajectory_id,
            curr.trajectory_key,
            prev.image_id,
            curr.image_id,
            prev.source_file_id,
            curr.source_file_id,
            prev.signal_id,
            curr.signal_id,
            prev.path,
            curr.path,
            prev.title,
            curr.title,
            prev.acquisition_timestamp,
            curr.acquisition_timestamp,
            CASE
                WHEN prev.acquisition_timestamp <> '' AND curr.acquisition_timestamp <> ''
                THEN unixepoch(curr.acquisition_timestamp) - unixepoch(prev.acquisition_timestamp)
            END,
            prev.sequence_index,
            curr.sequence_index,
            prev.trajectory_sequence_index,
            curr.trajectory_sequence_index,
            {", ".join(value_exprs)},
            {", ".join(delta_exprs)}
        FROM image_sequence curr
        JOIN image_sequence prev
          ON prev.session_id = curr.session_id
         AND prev.sequence_index = curr.sequence_index - 1
         AND prev.trajectory_id = curr.trajectory_id
        ORDER BY curr.session_id, curr.sequence_index
        """
    )


def populate_transition_changes(conn: sqlite3.Connection) -> None:
    rows = []
    for field in ACTION_FIELDS:
        rows.append(
            f"""
            SELECT
                transition_id,
                session_id,
                session_key,
                trajectory_id,
                trajectory_key,
                '{field}' AS field,
                prev_{field}_text,
                curr_{field}_text,
                prev_{field}_number,
                curr_{field}_number,
                delta_{field},
                CASE
                    WHEN coalesce(prev_{field}_text, '') <> coalesce(curr_{field}_text, '') THEN 1
                    WHEN delta_{field} IS NOT NULL AND abs(delta_{field}) > 0 THEN 1
                    ELSE 0
                END AS changed
            FROM image_transitions
            """
        )
    for field in PLACEHOLDER_FIELDS:
        rows.append(
            f"""
            SELECT
                transition_id,
                session_id,
                session_key,
                trajectory_id,
                trajectory_key,
                '{field}' AS field,
                prev_{field}_text,
                curr_{field}_text,
                NULL,
                NULL,
                delta_{field},
                0 AS changed
            FROM image_transitions
            """
        )

    conn.execute(
        f"""
        INSERT INTO transition_changes (
            transition_id, session_id, session_key, trajectory_id, trajectory_key, field, prev_value_text,
            curr_value_text, prev_value_number, curr_value_number, delta_number, changed
        )
        {" UNION ALL ".join(rows)}
        """
    )


def populate_summary(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TEMP TABLE trajectory_image_summary AS
        SELECT
            trajectory_id,
            trajectory_key,
            session_id,
            session_key,
            min(trajectory_sequence_index) AS first_trajectory_sequence_index,
            max(trajectory_sequence_index) AS last_trajectory_sequence_index,
            count(*) AS image_count,
            min(acquisition_timestamp) AS first_acquisition_timestamp,
            max(acquisition_timestamp) AS last_acquisition_timestamp,
            CASE
                WHEN min(acquisition_timestamp) <> '' AND max(acquisition_timestamp) <> ''
                THEN unixepoch(max(acquisition_timestamp)) - unixepoch(min(acquisition_timestamp))
            END AS total_elapsed_seconds
        FROM image_sequence
        GROUP BY trajectory_id, trajectory_key, session_id, session_key;

        CREATE TEMP TABLE trajectory_transition_summary AS
        SELECT
            trajectory_id,
            trajectory_key,
            session_id,
            session_key,
            count(*) AS transition_count,
            coalesce(sum(
                CASE
                    WHEN abs(coalesce(delta_stage_x, 0)) > 0
                      OR abs(coalesce(delta_stage_y, 0)) > 0
                      OR abs(coalesce(delta_stage_z, 0)) > 0
                      OR abs(coalesce(delta_stage_tilt_alpha, 0)) > 0
                      OR abs(coalesce(delta_stage_tilt_beta, 0)) > 0
                    THEN 1 ELSE 0
                END
            ), 0) AS stage_move_count,
            coalesce(sum(CASE WHEN abs(coalesce(delta_magnification, 0)) > 0 THEN 1 ELSE 0 END), 0)
                AS magnification_change_count,
            coalesce(sum(CASE WHEN abs(coalesce(delta_beam_energy, 0)) > 0 THEN 1 ELSE 0 END), 0)
                AS beam_energy_change_count,
            coalesce(sum(CASE WHEN abs(coalesce(delta_camera_length, 0)) > 0 THEN 1 ELSE 0 END), 0)
                AS camera_length_change_count,
            coalesce(sum(CASE WHEN abs(coalesce(delta_dwell_time, 0)) > 0 THEN 1 ELSE 0 END), 0)
                AS dwell_time_change_count
        FROM image_transitions
        GROUP BY trajectory_id, trajectory_key, session_id, session_key;

        INSERT INTO trajectory_summary (
            trajectory_id, trajectory_key, session_id, session_key, trajectory_index,
            image_count, transition_count, first_acquisition_timestamp,
            last_acquisition_timestamp, total_elapsed_seconds, stage_move_count,
            magnification_change_count, beam_energy_change_count,
            camera_length_change_count, dwell_time_change_count
        )
        SELECT
            i.trajectory_id,
            i.trajectory_key,
            i.session_id,
            i.session_key,
            cast(substr(i.trajectory_key, length(i.session_key) + 13) AS INTEGER),
            i.image_count,
            coalesce(t.transition_count, 0),
            i.first_acquisition_timestamp,
            i.last_acquisition_timestamp,
            i.total_elapsed_seconds,
            coalesce(t.stage_move_count, 0),
            coalesce(t.magnification_change_count, 0),
            coalesce(t.beam_energy_change_count, 0),
            coalesce(t.camera_length_change_count, 0),
            coalesce(t.dwell_time_change_count, 0)
        FROM trajectory_image_summary i
        LEFT JOIN trajectory_transition_summary t ON t.trajectory_id = i.trajectory_id;

        CREATE TEMP TABLE session_image_summary AS
        SELECT
            session_id,
            session_key,
            count(DISTINCT trajectory_id) AS trajectory_count,
            count(*) AS image_count,
            min(acquisition_timestamp) AS first_acquisition_timestamp,
            max(acquisition_timestamp) AS last_acquisition_timestamp,
            CASE
                WHEN min(acquisition_timestamp) <> '' AND max(acquisition_timestamp) <> ''
                THEN unixepoch(max(acquisition_timestamp)) - unixepoch(min(acquisition_timestamp))
            END AS total_elapsed_seconds
        FROM image_sequence
        GROUP BY session_id, session_key;

        CREATE TEMP TABLE session_transition_summary AS
        SELECT
            session_id,
            session_key,
            count(*) AS transition_count,
            coalesce(sum(
                CASE
                    WHEN abs(coalesce(delta_stage_x, 0)) > 0
                      OR abs(coalesce(delta_stage_y, 0)) > 0
                      OR abs(coalesce(delta_stage_z, 0)) > 0
                      OR abs(coalesce(delta_stage_tilt_alpha, 0)) > 0
                      OR abs(coalesce(delta_stage_tilt_beta, 0)) > 0
                    THEN 1 ELSE 0
                END
            ), 0) AS stage_move_count,
            coalesce(sum(CASE WHEN abs(coalesce(delta_magnification, 0)) > 0 THEN 1 ELSE 0 END), 0)
                AS magnification_change_count,
            coalesce(sum(CASE WHEN abs(coalesce(delta_beam_energy, 0)) > 0 THEN 1 ELSE 0 END), 0)
                AS beam_energy_change_count,
            coalesce(sum(CASE WHEN abs(coalesce(delta_camera_length, 0)) > 0 THEN 1 ELSE 0 END), 0)
                AS camera_length_change_count,
            coalesce(sum(CASE WHEN abs(coalesce(delta_dwell_time, 0)) > 0 THEN 1 ELSE 0 END), 0)
                AS dwell_time_change_count
        FROM image_transitions
        GROUP BY session_id, session_key;

        INSERT INTO session_trajectory_summary (
            session_id, session_key, trajectory_count, split_break_count,
            time_split_count, stage_move_split_count, image_count, transition_count,
            first_acquisition_timestamp, last_acquisition_timestamp,
            total_elapsed_seconds, stage_move_count, magnification_change_count,
            beam_energy_change_count, camera_length_change_count, dwell_time_change_count
        )
        SELECT
            i.session_id,
            i.session_key,
            i.trajectory_count,
            coalesce(b.split_break_count, 0),
            coalesce(b.time_split_count, 0),
            coalesce(b.stage_move_split_count, 0),
            i.image_count,
            coalesce(t.transition_count, 0),
            i.first_acquisition_timestamp,
            i.last_acquisition_timestamp,
            i.total_elapsed_seconds,
            coalesce(t.stage_move_count, 0),
            coalesce(t.magnification_change_count, 0),
            coalesce(t.beam_energy_change_count, 0),
            coalesce(t.camera_length_change_count, 0),
            coalesce(t.dwell_time_change_count, 0)
        FROM session_image_summary i
        LEFT JOIN session_transition_summary t ON t.session_id = i.session_id
        LEFT JOIN (
            SELECT
                session_id,
                count(*) AS split_break_count,
                sum(split_due_to_time) AS time_split_count,
                sum(split_due_to_stage_move) AS stage_move_split_count
            FROM trajectory_breaks
            GROUP BY session_id
        ) b ON b.session_id = i.session_id;

        DROP TABLE trajectory_image_summary;
        DROP TABLE trajectory_transition_summary;
        DROP TABLE session_image_summary;
        DROP TABLE session_transition_summary;
        """
    )


def kv_text(signal_alias: str, key_path: str) -> str:
    escaped = key_path.replace("'", "''")
    return (
        f"(SELECT value_text FROM meta.metadata_kv "
        f"WHERE signal_id = {signal_alias}.signal_id AND key_path = '{escaped}' "
        f"ORDER BY CASE source WHEN 'hyperspy_metadata' THEN 0 "
        f"WHEN 'hyperspy_original_metadata' THEN 1 ELSE 2 END LIMIT 1)"
    )


def kv_number(signal_alias: str, key_path: str) -> str:
    escaped = key_path.replace("'", "''")
    return (
        f"(SELECT value_number FROM meta.metadata_kv "
        f"WHERE signal_id = {signal_alias}.signal_id AND key_path = '{escaped}' "
        f"ORDER BY CASE source WHEN 'hyperspy_metadata' THEN 0 "
        f"WHEN 'hyperspy_original_metadata' THEN 1 ELSE 2 END LIMIT 1)"
    )


def axis_number(signal_alias: str, axis_name: str, column: str) -> str:
    escaped = axis_name.replace("'", "''")
    return (
        f"(SELECT {column} FROM meta.axes "
        f"WHERE signal_id = {signal_alias}.signal_id AND lower(name) = lower('{escaped}') "
        f"LIMIT 1)"
    )


def magnification_number(signal_alias: str) -> str:
    """Prefer valid normalized magnification, then unambiguous DM metadata."""
    candidates = []
    for source, key in (
        ("hyperspy_metadata", ACTION_FIELDS["magnification"]),
        ("hyperspy_original_metadata", DM_MAGNIFICATION_KEY),
    ):
        candidates.append(
            "(SELECT max(value_number) FROM meta.metadata_kv "
            f"WHERE signal_id = {signal_alias}.signal_id "
            f"AND source = '{source}' AND key_path = '{key}' "
            "AND value_number > 0 HAVING count(DISTINCT value_number) = 1)"
        )
    return f"coalesce({', '.join(candidates)})"


def populate_si_events(conn: sqlite3.Connection) -> None:
    stage_x = kv_number("s", DM_STAGE_KEYS["stage_x"])
    stage_y = kv_number("s", DM_STAGE_KEYS["stage_y"])
    stage_z = kv_number("s", DM_STAGE_KEYS["stage_z"])
    stage_alpha = kv_number("s", DM_STAGE_KEYS["stage_tilt_alpha"])
    stage_beta = kv_number("s", DM_STAGE_KEYS["stage_tilt_beta"])
    magnification = magnification_number("s")
    fov = kv_number("s", "ImageList.TagGroup0.ImageTags.Microscope Info.Field of View (µm)")
    pixel_time = kv_number("s", "ImageList.TagGroup0.ImageTags.SI.Acquisition.Pixel time (s)")
    si_width_key = kv_number("s", "ImageList.TagGroup0.ImageTags.SI.Acquisition.Spatial Sampling.Width (pixels)")
    si_height_key = kv_number("s", "ImageList.TagGroup0.ImageTags.SI.Acquisition.Spatial Sampling.Height (pixels)")
    x_size = axis_number("s", "x", "size")
    y_size = axis_number("s", "y", "size")
    x_scale = axis_number("s", "x", "scale")
    y_scale = axis_number("s", "y", "scale")
    energy_size = axis_number("s", "Energy loss", "size")
    energy_scale = axis_number("s", "Energy loss", "scale")
    energy_offset = axis_number("s", "Energy loss", "offset")
    eds_energy_size = axis_number("s", "z", "size")
    eds_energy_scale = axis_number("s", "z", "scale")
    eds_energy_offset = axis_number("s", "z", "offset")
    eels_dispersion = kv_number("s", "ImageList.TagGroup0.ImageTags.EELS Spectrometer.Dispersion (eV/ch)")
    eels_exposure = kv_number("s", "ImageList.TagGroup0.ImageTags.EELS.Acquisition.Exposure (s)")
    eels_frame_count = kv_number("s", "ImageList.TagGroup0.ImageTags.EELS.Acquisition.Number of frames")
    eels_spectrometer = kv_text("s", "ImageList.TagGroup0.ImageTags.EELS Spectrometer.Instrument name")
    eels_camera_number = kv_text("s", "ImageList.TagGroup0.ImageTags.Acquisition.Device.Camera Number")
    eels_camera_size = kv_text("s", "ImageList.TagGroup0.ImageTags.Acquisition.Device.Active Size (pixels)")
    eels_collection = kv_number("s", "ImageList.TagGroup0.ImageTags.EELS.Experimental Conditions.Collection semi-angle (mrad)")
    eels_convergence = kv_number("s", "ImageList.TagGroup0.ImageTags.EELS.Experimental Conditions.Convergence semi-angle (mrad)")
    eds_start = kv_number("s", "ImageList.TagGroup0.ImageTags.Elemental Quantification.State.Elemental Parameters.Start Energy")
    eds_end = kv_number("s", "ImageList.TagGroup0.ImageTags.Elemental Quantification.State.Elemental Parameters.End Energy")
    eds_dispersion = kv_number("s", "ImageList.TagGroup0.ImageTags.EDS.Acquisition.Dispersion (eV/ch)")
    eds_exposure = kv_number("s", "ImageList.TagGroup0.ImageTags.EDS.Acquisition.Exposure (s)")

    conn.executescript(
        f"""
        CREATE TEMP TABLE si_signal_base AS
        SELECT
            sf.session_id,
            ss.session_key,
            f.file_id AS source_file_id,
            s.signal_id,
            f.path,
            f.acquisition_folder,
            s.title,
            s.category,
            s.acquisition_date,
            s.acquisition_time,
            CASE
                WHEN s.acquisition_date <> '' AND s.acquisition_time <> ''
                THEN s.acquisition_date || 'T' || s.acquisition_time
                ELSE ''
            END AS acquisition_timestamp,
            s.data_shape,
            {stage_x} AS stage_x,
            {stage_y} AS stage_y,
            {stage_z} AS stage_z,
            {stage_alpha} AS stage_tilt_alpha,
            {stage_beta} AS stage_tilt_beta,
            {magnification} AS magnification,
            {fov} AS field_of_view_um,
            coalesce({si_width_key}, {x_size}) AS si_width_px,
            coalesce({si_height_key}, {y_size}) AS si_height_px,
            {x_scale} AS si_x_pixel_size_um,
            {y_scale} AS si_y_pixel_size_um,
            {pixel_time} AS si_pixel_time_s,
            CASE
                WHEN s.category = 'eels' THEN {energy_offset}
            END AS eels_energy_start_ev,
            CASE
                WHEN s.category = 'eels' AND {energy_offset} IS NOT NULL
                     AND {energy_scale} IS NOT NULL AND {energy_size} IS NOT NULL
                THEN {energy_offset} + {energy_scale} * ({energy_size} - 1)
            END AS eels_energy_end_ev,
            CASE WHEN s.category = 'eels' THEN {eels_dispersion} END AS eels_dispersion_ev_ch,
            CASE WHEN s.category = 'eels' THEN {eels_exposure} END AS eels_exposure_s,
            CASE WHEN s.category = 'eels' THEN {eels_frame_count} END AS eels_frame_count,
            CASE WHEN s.category = 'eels' THEN coalesce({eels_spectrometer}, '') ELSE '' END AS eels_spectrometer,
            CASE WHEN s.category = 'eels' THEN coalesce({eels_camera_number}, '') ELSE '' END AS eels_camera_number,
            CASE WHEN s.category = 'eels' THEN coalesce({eels_camera_size}, '') ELSE '' END AS eels_camera_active_size_px,
            CASE WHEN s.category = 'eels' THEN {eels_collection} END AS eels_collection_angle_mrad,
            CASE WHEN s.category = 'eels' THEN {eels_convergence} END AS eels_convergence_angle_mrad,
            CASE WHEN s.category = 'eds' THEN coalesce({eds_start}, {eds_energy_offset}) END AS eds_energy_start_ev,
            CASE
                WHEN s.category = 'eds' THEN coalesce(
                    {eds_end},
                    CASE
                        WHEN {eds_energy_offset} IS NOT NULL AND {eds_energy_scale} IS NOT NULL
                             AND {eds_energy_size} IS NOT NULL
                        THEN {eds_energy_offset} + {eds_energy_scale} * ({eds_energy_size} - 1) * 1000.0
                    END
                )
            END AS eds_energy_end_ev,
            CASE WHEN s.category = 'eds' THEN {eds_dispersion} END AS eds_dispersion_ev_ch,
            CASE WHEN s.category = 'eds' THEN {eds_exposure} END AS eds_exposure_s
        FROM sess.session_files sf
        JOIN sess.sessions ss ON ss.session_id = sf.session_id
        JOIN meta.files f ON f.file_id = sf.source_file_id
        JOIN meta.signals s ON s.file_id = f.file_id
        WHERE s.category IN ('eds', 'eels')
          AND coalesce(s.navigation_dimension, 0) >= 1;

        CREATE TEMP TABLE si_grouped AS
        SELECT
            session_id,
            session_key,
            acquisition_folder,
            acquisition_date,
            acquisition_time,
            acquisition_timestamp,
            CASE
                WHEN sum(category = 'eds') > 0 AND sum(category = 'eels') > 0 THEN 'EDS+EELS'
                WHEN sum(category = 'eds') > 0 THEN 'EDS'
                WHEN sum(category = 'eels') > 0 THEN 'EELS'
                ELSE 'SI'
            END AS modality,
            count(*) AS signal_count,
            group_concat(source_file_id, ';') AS source_file_ids,
            group_concat(signal_id, ';') AS signal_ids,
            group_concat(title, ' | ') AS titles,
            group_concat(path, ' | ') AS paths,
            avg(stage_x) AS stage_x,
            avg(stage_y) AS stage_y,
            avg(stage_z) AS stage_z,
            avg(stage_tilt_alpha) AS stage_tilt_alpha,
            avg(stage_tilt_beta) AS stage_tilt_beta,
            avg(magnification) AS magnification,
            avg(field_of_view_um) AS field_of_view_um,
            cast(max(si_width_px) AS INTEGER) AS si_width_px,
            cast(max(si_height_px) AS INTEGER) AS si_height_px,
            avg(si_x_pixel_size_um) AS si_x_pixel_size_um,
            avg(si_y_pixel_size_um) AS si_y_pixel_size_um,
            max(si_width_px * si_x_pixel_size_um) AS si_scan_width_um,
            max(si_height_px * si_y_pixel_size_um) AS si_scan_height_um,
            avg(si_pixel_time_s) AS si_pixel_time_s,
            min(eels_energy_start_ev) AS eels_energy_start_ev,
            max(eels_energy_end_ev) AS eels_energy_end_ev,
            avg(eels_dispersion_ev_ch) AS eels_dispersion_ev_ch,
            avg(eels_exposure_s) AS eels_exposure_s,
            avg(eels_frame_count) AS eels_frame_count,
            max(eels_spectrometer) AS eels_spectrometer,
            max(eels_camera_number) AS eels_camera_number,
            max(eels_camera_active_size_px) AS eels_camera_active_size_px,
            avg(eels_collection_angle_mrad) AS eels_collection_angle_mrad,
            avg(eels_convergence_angle_mrad) AS eels_convergence_angle_mrad,
            min(eds_energy_start_ev) AS eds_energy_start_ev,
            max(eds_energy_end_ev) AS eds_energy_end_ev,
            avg(eds_dispersion_ev_ch) AS eds_dispersion_ev_ch,
            avg(eds_exposure_s) AS eds_exposure_s
        FROM si_signal_base
        GROUP BY session_id, acquisition_folder, acquisition_timestamp;
        """
    )

    conn.execute(
        """
        INSERT INTO si_events (
            session_id, session_key, acquisition_folder, acquisition_date,
            acquisition_time, acquisition_timestamp, sequence_index, modality,
            signal_count, source_file_ids, signal_ids, titles, paths,
            stage_x, stage_y, stage_z, stage_tilt_alpha, stage_tilt_beta,
            magnification, field_of_view_um, si_width_px, si_height_px,
            si_x_pixel_size_um, si_y_pixel_size_um, si_scan_width_um,
            si_scan_height_um, si_pixel_time_s, eels_energy_start_ev,
            eels_energy_end_ev, eels_dispersion_ev_ch, eels_exposure_s,
            eels_frame_count, eels_spectrometer, eels_camera_number,
            eels_camera_active_size_px, eels_collection_angle_mrad,
            eels_convergence_angle_mrad, eds_energy_start_ev, eds_energy_end_ev,
            eds_dispersion_ev_ch, eds_exposure_s
        )
        SELECT
            session_id,
            session_key,
            acquisition_folder,
            acquisition_date,
            acquisition_time,
            acquisition_timestamp,
            row_number() OVER (
                PARTITION BY session_id
                ORDER BY acquisition_date, acquisition_time, acquisition_folder
            ) AS sequence_index,
            modality,
            signal_count,
            source_file_ids,
            signal_ids,
            titles,
            paths,
            stage_x,
            stage_y,
            stage_z,
            stage_tilt_alpha,
            stage_tilt_beta,
            magnification,
            field_of_view_um,
            si_width_px,
            si_height_px,
            si_x_pixel_size_um,
            si_y_pixel_size_um,
            si_scan_width_um,
            si_scan_height_um,
            si_pixel_time_s,
            eels_energy_start_ev,
            eels_energy_end_ev,
            eels_dispersion_ev_ch,
            eels_exposure_s,
            eels_frame_count,
            eels_spectrometer,
            eels_camera_number,
            eels_camera_active_size_px,
            eels_collection_angle_mrad,
            eels_convergence_angle_mrad,
            eds_energy_start_ev,
            eds_energy_end_ev,
            eds_dispersion_ev_ch,
            eds_exposure_s
        FROM si_grouped
        ORDER BY session_id, sequence_index
        """
    )

    conn.execute("DROP TABLE si_signal_base")
    conn.execute("DROP TABLE si_grouped")


def populate_adf_si_raster_images(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO adf_si_raster_images (
            si_event_id, session_id, session_key, source_file_id, signal_id,
            acquisition_folder, acquisition_timestamp, sequence_index, title,
            path, data_shape, data_dtype, si_width_px, si_height_px, modality,
            stage_x, stage_y, stage_z, stage_tilt_alpha, stage_tilt_beta,
            magnification, field_of_view_um, si_x_pixel_size_um, si_y_pixel_size_um,
            si_scan_width_um, si_scan_height_um, si_pixel_time_s
        )
        SELECT
            si_event_id,
            session_id,
            session_key,
            source_file_id,
            signal_id,
            acquisition_folder,
            acquisition_timestamp,
            row_number() OVER (
                PARTITION BY session_id
                ORDER BY acquisition_timestamp, path, signal_id
            ) AS sequence_index,
            title,
            path,
            data_shape,
            data_dtype,
            si_width_px,
            si_height_px,
            modality,
            stage_x,
            stage_y,
            stage_z,
            stage_tilt_alpha,
            stage_tilt_beta,
            magnification,
            field_of_view_um,
            si_x_pixel_size_um,
            si_y_pixel_size_um,
            si_scan_width_um,
            si_scan_height_um,
            si_pixel_time_s
        FROM (
            SELECT DISTINCT
                e.si_event_id,
                e.session_id,
                e.session_key,
                f.file_id AS source_file_id,
                s.signal_id,
                f.acquisition_folder,
                e.acquisition_timestamp,
                s.title,
                f.path,
                s.data_shape,
                s.data_dtype,
                e.si_width_px,
                e.si_height_px,
                e.modality,
                e.stage_x,
                e.stage_y,
                e.stage_z,
                e.stage_tilt_alpha,
                e.stage_tilt_beta,
                e.magnification,
                e.field_of_view_um,
                e.si_x_pixel_size_um,
                e.si_y_pixel_size_um,
                e.si_scan_width_um,
                e.si_scan_height_um,
                e.si_pixel_time_s
            FROM si_events e
            JOIN sess.session_files sf ON sf.session_id = e.session_id
            JOIN meta.files f
              ON f.file_id = sf.source_file_id
             AND f.acquisition_folder = e.acquisition_folder
            JOIN meta.signals s ON s.file_id = f.file_id
            WHERE s.category = 'image'
              AND lower(s.title) LIKE '%adf%'
              AND lower(s.title) NOT LIKE '%survey%'
              AND e.si_width_px IS NOT NULL
              AND e.si_height_px IS NOT NULL
              AND e.acquisition_timestamp <> ''
              AND s.acquisition_date || 'T' || s.acquisition_time = e.acquisition_timestamp
              AND s.data_shape IN (
                  e.si_width_px || 'x' || e.si_height_px,
                  e.si_height_px || 'x' || e.si_width_px
              )
        )
        ORDER BY session_id, sequence_index
        """
    )


def populate_si_event_assets(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        INSERT INTO si_event_assets (
            si_event_id, session_id, session_key, asset_role, is_secondary,
            modality, source_file_id, signal_id, title, path, data_shape,
            data_dtype, acquisition_timestamp
        )
        SELECT DISTINCT
            e.si_event_id,
            e.session_id,
            e.session_key,
            'PRIMARY_SI_SIGNAL',
            0,
            upper(s.category),
            f.file_id,
            s.signal_id,
            s.title,
            f.path,
            s.data_shape,
            s.data_dtype,
            e.acquisition_timestamp
        FROM si_events e
        JOIN sess.session_files sf ON sf.session_id = e.session_id
        JOIN meta.files f
          ON f.file_id = sf.source_file_id
         AND f.acquisition_folder = e.acquisition_folder
        JOIN meta.signals s ON s.file_id = f.file_id
        WHERE s.category IN ('eds', 'eels')
          AND coalesce(s.navigation_dimension, 0) >= 1
          AND e.acquisition_timestamp = CASE
              WHEN s.acquisition_date <> '' AND s.acquisition_time <> ''
              THEN s.acquisition_date || 'T' || s.acquisition_time
              ELSE ''
          END;

        INSERT INTO si_event_assets (
            si_event_id, session_id, session_key, asset_role, is_secondary,
            modality, source_file_id, signal_id, title, path, data_shape,
            data_dtype, acquisition_timestamp
        )
        SELECT
            si_event_id,
            session_id,
            session_key,
            'SECONDARY_ADF_SI_RASTER_IMAGE',
            1,
            modality,
            source_file_id,
            signal_id,
            title,
            path,
            data_shape,
            data_dtype,
            acquisition_timestamp
        FROM adf_si_raster_images;
        """
    )


def populate_action_sequence(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        INSERT INTO action_sequence (
            session_id, session_key, action_type, source_image_id, source_si_event_id,
            acquisition_timestamp, sequence_index, title, path, modality,
            stage_x, stage_y, stage_z, stage_tilt_alpha, stage_tilt_beta,
            magnification, field_of_view_um
        )
        SELECT
            session_id,
            session_key,
            'STEM_IMAGE',
            image_id,
            NULL,
            acquisition_timestamp,
            0,
            title,
            path,
            '',
            stage_x_number,
            stage_y_number,
            stage_z_number,
            stage_tilt_alpha_number,
            stage_tilt_beta_number,
            magnification_number,
            field_of_view_um_number
        FROM image_sequence;

        INSERT INTO action_sequence (
            session_id, session_key, action_type, source_image_id, source_si_event_id,
            acquisition_timestamp, sequence_index, title, path, modality,
            stage_x, stage_y, stage_z, stage_tilt_alpha, stage_tilt_beta,
            magnification, field_of_view_um, si_width_px, si_height_px,
            si_x_pixel_size_um, si_y_pixel_size_um, si_scan_width_um,
            si_scan_height_um, si_pixel_time_s, eels_energy_start_ev,
            eels_energy_end_ev, eels_exposure_s, eels_spectrometer,
            eels_camera_number, eds_energy_start_ev, eds_energy_end_ev, eds_exposure_s
        )
        SELECT
            session_id,
            session_key,
            'SI_ACQUISITION',
            NULL,
            si_event_id,
            acquisition_timestamp,
            0,
            titles,
            paths,
            modality,
            stage_x,
            stage_y,
            stage_z,
            stage_tilt_alpha,
            stage_tilt_beta,
            magnification,
            field_of_view_um,
            si_width_px,
            si_height_px,
            si_x_pixel_size_um,
            si_y_pixel_size_um,
            si_scan_width_um,
            si_scan_height_um,
            si_pixel_time_s,
            eels_energy_start_ev,
            eels_energy_end_ev,
            eels_exposure_s,
            eels_spectrometer,
            eels_camera_number,
            eds_energy_start_ev,
            eds_energy_end_ev,
            eds_exposure_s
        FROM si_events;

        CREATE TEMP TABLE action_order AS
        SELECT
            action_id,
            row_number() OVER (
                PARTITION BY session_id
                ORDER BY acquisition_timestamp,
                         CASE action_type
                             WHEN 'STEM_IMAGE' THEN 0
                             WHEN 'SI_ACQUISITION' THEN 1
                             ELSE 2
                         END,
                         title,
                         action_id
            ) AS new_sequence_index
        FROM action_sequence;

        UPDATE action_sequence
        SET sequence_index = (
            SELECT new_sequence_index
            FROM action_order
            WHERE action_order.action_id = action_sequence.action_id
        );

        DROP TABLE action_order;
        """
    )


def populate_action_transitions(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        INSERT INTO action_transitions (
            session_id, session_key, prev_action_id, curr_action_id,
            prev_action_type, curr_action_type, prev_sequence_index,
            curr_sequence_index, prev_timestamp, curr_timestamp, delta_seconds,
            prev_title, curr_title, prev_path, curr_path, delta_stage_x,
            delta_stage_y, delta_stage_z, delta_stage_tilt_alpha,
            delta_stage_tilt_beta, delta_magnification, delta_field_of_view_um,
            si_taken, si_modality, si_width_px, si_height_px,
            si_x_pixel_size_um, si_y_pixel_size_um, si_pixel_time_s,
            eels_energy_start_ev, eels_energy_end_ev, eels_exposure_s,
            eels_spectrometer, eels_camera_number, eds_energy_start_ev,
            eds_energy_end_ev, eds_exposure_s
        )
        SELECT
            curr.session_id,
            curr.session_key,
            prev.action_id,
            curr.action_id,
            prev.action_type,
            curr.action_type,
            prev.sequence_index,
            curr.sequence_index,
            prev.acquisition_timestamp,
            curr.acquisition_timestamp,
            CASE
                WHEN prev.acquisition_timestamp <> '' AND curr.acquisition_timestamp <> ''
                THEN unixepoch(curr.acquisition_timestamp) - unixepoch(prev.acquisition_timestamp)
            END,
            prev.title,
            curr.title,
            prev.path,
            curr.path,
            CASE WHEN prev.stage_x IS NOT NULL AND curr.stage_x IS NOT NULL THEN curr.stage_x - prev.stage_x END,
            CASE WHEN prev.stage_y IS NOT NULL AND curr.stage_y IS NOT NULL THEN curr.stage_y - prev.stage_y END,
            CASE WHEN prev.stage_z IS NOT NULL AND curr.stage_z IS NOT NULL THEN curr.stage_z - prev.stage_z END,
            CASE WHEN prev.stage_tilt_alpha IS NOT NULL AND curr.stage_tilt_alpha IS NOT NULL THEN curr.stage_tilt_alpha - prev.stage_tilt_alpha END,
            CASE WHEN prev.stage_tilt_beta IS NOT NULL AND curr.stage_tilt_beta IS NOT NULL THEN curr.stage_tilt_beta - prev.stage_tilt_beta END,
            CASE WHEN prev.magnification IS NOT NULL AND curr.magnification IS NOT NULL THEN curr.magnification - prev.magnification END,
            CASE WHEN prev.field_of_view_um IS NOT NULL AND curr.field_of_view_um IS NOT NULL THEN curr.field_of_view_um - prev.field_of_view_um END,
            CASE WHEN curr.action_type = 'SI_ACQUISITION' THEN 1 ELSE 0 END,
            CASE WHEN curr.action_type = 'SI_ACQUISITION' THEN curr.modality ELSE '' END,
            CASE WHEN curr.action_type = 'SI_ACQUISITION' THEN curr.si_width_px END,
            CASE WHEN curr.action_type = 'SI_ACQUISITION' THEN curr.si_height_px END,
            CASE WHEN curr.action_type = 'SI_ACQUISITION' THEN curr.si_x_pixel_size_um END,
            CASE WHEN curr.action_type = 'SI_ACQUISITION' THEN curr.si_y_pixel_size_um END,
            CASE WHEN curr.action_type = 'SI_ACQUISITION' THEN curr.si_pixel_time_s END,
            CASE WHEN curr.action_type = 'SI_ACQUISITION' THEN curr.eels_energy_start_ev END,
            CASE WHEN curr.action_type = 'SI_ACQUISITION' THEN curr.eels_energy_end_ev END,
            CASE WHEN curr.action_type = 'SI_ACQUISITION' THEN curr.eels_exposure_s END,
            CASE WHEN curr.action_type = 'SI_ACQUISITION' THEN curr.eels_spectrometer ELSE '' END,
            CASE WHEN curr.action_type = 'SI_ACQUISITION' THEN curr.eels_camera_number ELSE '' END,
            CASE WHEN curr.action_type = 'SI_ACQUISITION' THEN curr.eds_energy_start_ev END,
            CASE WHEN curr.action_type = 'SI_ACQUISITION' THEN curr.eds_energy_end_ev END,
            CASE WHEN curr.action_type = 'SI_ACQUISITION' THEN curr.eds_exposure_s END
        FROM action_sequence curr
        JOIN action_sequence prev
          ON prev.session_id = curr.session_id
         AND prev.sequence_index = curr.sequence_index - 1
        ORDER BY curr.session_id, curr.sequence_index;
        """
    )


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
        description="Extract serial STEM-image action trajectories from STEM-Bank-26 metadata."
    )
    parser.add_argument(
        "--metadata-db",
        default="_archive_inventory/stem_bank_26_metadata.sqlite",
        help="Raw file/signal metadata SQLite database.",
    )
    parser.add_argument(
        "--sessions-db",
        default="_archive_inventory/stem_bank_26_sessions_by_acq_date_sample.sqlite",
        help="Session aggregate SQLite database.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="_archive_inventory/stem_bank_26_trajectories_by_acq_date_sample.sqlite",
        help="Output trajectory SQLite database.",
    )
    parser.add_argument(
        "--gzip-copy",
        action="store_true",
        help="Also write a compressed .sqlite.gz copy.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    metadata_db = Path(args.metadata_db)
    sessions_db = Path(args.sessions_db)
    output = Path(args.output)
    if not metadata_db.is_file():
        print(f"Metadata database not found: {metadata_db}", file=sys.stderr)
        return 2
    if not sessions_db.is_file():
        print(f"Sessions database not found: {sessions_db}", file=sys.stderr)
        return 2

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(output.name + ".tmp")
    if temp_output.exists():
        temp_output.unlink()

    conn = connect(temp_output)
    try:
        attach_sources(conn, metadata_db, sessions_db)
        create_schema(conn)
        write_run_info(conn, metadata_db, sessions_db)
        populate_si_events(conn)
        populate_adf_si_raster_images(conn)
        populate_si_event_assets(conn)
        populate_image_sequence(conn)
        populate_transitions(conn)
        populate_transition_changes(conn)
        populate_action_sequence(conn)
        populate_action_transitions(conn)
        populate_summary(conn)
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
