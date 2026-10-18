#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import signal
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable


DM_EXTS = {".dm3", ".dm4"}
RASTER_EXTS = {".tif", ".tiff", ".jpg", ".jpeg", ".bmp", ".png"}
TEXT_EXTS = {".txt", ".csv", ".json", ".md", ".xml", ".dmw"}
GENERIC_IMAGE_TITLES = {
    "spatial drift scan",
    "spatial drift reference",
    "cross-correlation image",
    "correlation coefficient",
    "peak shift",
}
ORIGINAL_METADATA_KEY_PATHS = (
    "ImageList.TagGroup0.ImageTags.DataBar.Acquisition Date",
    "ImageList.TagGroup0.ImageTags.DataBar.Acquisition Time",
    "ImageList.TagGroup0.ImageTags.Acquisition.Device.Active Size (pixels)",
    "ImageList.TagGroup0.ImageTags.Acquisition.Device.Camera Number",
    "ImageList.TagGroup0.ImageTags.Acquisition.Device.CCD.Pixel Size (um)",
    "ImageList.TagGroup0.ImageTags.Acquisition.Device.Name",
    "ImageList.TagGroup0.ImageTags.Acquisition.Device.Source",
    "ImageList.TagGroup0.ImageTags.Acquisition.Device.Source ID",
    "ImageList.TagGroup0.ImageTags.Acquisition.Device.Source Model",
    "ImageList.TagGroup0.ImageTags.EDS.Acquisition.Channels",
    "ImageList.TagGroup0.ImageTags.EDS.Acquisition.Dispersion (eV/ch)",
    "ImageList.TagGroup0.ImageTags.EDS.Acquisition.Exposure (s)",
    "ImageList.TagGroup0.ImageTags.EDS.Acquisition.Process time index",
    "ImageList.TagGroup0.ImageTags.EDS.Acquisition.Survey cutoff energy (eV)",
    "ImageList.TagGroup0.ImageTags.EELS.Acquisition.Exposure (s)",
    "ImageList.TagGroup0.ImageTags.EELS.Acquisition.Number of frames",
    "ImageList.TagGroup0.ImageTags.EELS.Acquisition.Saturation fraction",
    "ImageList.TagGroup0.ImageTags.EELS.Experimental Conditions.Collection semi-angle (mrad)",
    "ImageList.TagGroup0.ImageTags.EELS.Experimental Conditions.Convergence semi-angle (mrad)",
    "ImageList.TagGroup0.ImageTags.EELS Spectrometer.Aperture index",
    "ImageList.TagGroup0.ImageTags.EELS Spectrometer.Aperture label",
    "ImageList.TagGroup0.ImageTags.EELS Spectrometer.Dispersion (eV/ch)",
    "ImageList.TagGroup0.ImageTags.EELS Spectrometer.Dispersion index",
    "ImageList.TagGroup0.ImageTags.EELS Spectrometer.Drift tube voltage (V)",
    "ImageList.TagGroup0.ImageTags.EELS Spectrometer.Energy loss (eV)",
    "ImageList.TagGroup0.ImageTags.EELS Spectrometer.Instrument name",
    "ImageList.TagGroup0.ImageTags.Elemental Quantification.State.Elemental Parameters.Active Edge Group",
    "ImageList.TagGroup0.ImageTags.Elemental Quantification.State.Elemental Parameters.Active Element",
    "ImageList.TagGroup0.ImageTags.Elemental Quantification.State.Elemental Parameters.Analytical Technique",
    "ImageList.TagGroup0.ImageTags.Elemental Quantification.State.Elemental Parameters.End Energy",
    "ImageList.TagGroup0.ImageTags.Elemental Quantification.State.Elemental Parameters.Start Energy",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Cs(mm)",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Emission Current (µA)",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Field of View (µm)",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Formatted Indicated Mag",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Formatted Voltage",
    "ImageList.TagGroup0.ImageTags.Microscope Info.HT Extrapolated",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Illumination Mode",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Imaging Mode",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Indicated Magnification",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Magnification Interpolated",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Name",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Operation Mode",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Operation Mode Type",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Probe Current (nA)",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Probe Size (nm)",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Stage Position.Stage Alpha",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Stage Position.Stage Beta",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Stage Position.Stage X",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Stage Position.Stage Y",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Stage Position.Stage Z",
    "ImageList.TagGroup0.ImageTags.Microscope Info.STEM Camera Length",
    "ImageList.TagGroup0.ImageTags.Microscope Info.Voltage",
    "ImageList.TagGroup0.ImageTags.SI.Acquisition.Date",
    "ImageList.TagGroup0.ImageTags.SI.Acquisition.End time",
    "ImageList.TagGroup0.ImageTags.SI.Acquisition.Last pixel",
    "ImageList.TagGroup0.ImageTags.SI.Acquisition.Number of cycles",
    "ImageList.TagGroup0.ImageTags.SI.Acquisition.Pixel iterator",
    "ImageList.TagGroup0.ImageTags.SI.Acquisition.Pixel time (s)",
    "ImageList.TagGroup0.ImageTags.SI.Acquisition.SI Application Mode.Name",
    "ImageList.TagGroup0.ImageTags.SI.Acquisition.Spatial Sampling.Height (pixels)",
    "ImageList.TagGroup0.ImageTags.SI.Acquisition.Spatial Sampling.Width (pixels)",
    "ImageList.TagGroup0.ImageTags.SI.Acquisition.Start time",
    "ImageList.TagGroup0.ImageTags.SI.Acquisition.Survey Image.Spectrum Image Rect",
    "ImageList.TagGroup0.ImageTags.SI.Acquisition.Survey Image.Unique Image ID",
)


def clean_text(value: str) -> str:
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"^\s*\d{2}\s+\d{2}\s+\d{2}\s+", " ", value)
    value = re.sub(r"\b\d{1,2}[._/-]\d{1,2}(?:[._/-]\d{2,4})?\b", " ", value)
    value = re.sub(r"\b(?:19|20)\d{2}[._/-]\d{1,2}[._/-]\d{1,2}\b", " ", value)
    value = re.sub(r"\b\d{2}_\d{2}_\d{2}\b", " ", value)
    value = re.sub(r"\b(?:19|20)\d{2}\b", " ", value)
    value = re.sub(r"\b\d{4,}\b", " ", value)
    value = re.sub(r"\b(?:st|nd|rd|th) session\b", " ", value, flags=re.I)
    value = re.sub(r"\bsession\b", " ", value, flags=re.I)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ._-")


def normalize_sample(value: str) -> str:
    value = clean_text(value)
    value = re.sub(r"\s*\([^)]*\)\s*$", "", value)
    value = re.sub(
        r"\b(?:fresh|tested|post catalysis|pre catal|as prepared|new sample)\b",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"\s+", " ", value).strip(" ._-")
    return value or "[unknown]"


def is_date_like(part: str) -> bool:
    p = part.strip()
    return bool(
        re.fullmatch(r"\d{1,2}[._-]\d{1,2}[._-]\d{2,4}", p)
        or re.fullmatch(r"\d{2}_\d{2}_\d{2}", p)
    )


def infer_sample(rel_path: Path) -> str:
    parts = rel_path.parts[:-1]
    if not parts:
        return "[root]"

    if is_date_like(parts[0]) and len(parts) > 1:
        candidate = parts[1]
    elif parts[0] == "Cu_Pt project" and len(parts) > 1:
        candidate = parts[2] if parts[1] == "Old" and len(parts) > 2 else parts[1]
    else:
        candidate = parts[0]
    return normalize_sample(candidate)


def simple_name(path_or_title: str) -> str:
    stem = Path(path_or_title).stem.lower()
    stem = re.sub(r"\s*\([^)]*\)", "", stem)
    return re.sub(r"\s+", " ", stem.replace("_", " ")).strip()


def utc_from_ns(ns: int | None) -> str:
    if ns is None:
        return ""
    return dt.datetime.fromtimestamp(ns / 1_000_000_000, tz=dt.UTC).isoformat()


def birthtime_ns(stat_result: os.stat_result) -> int | None:
    if hasattr(stat_result, "st_birthtime_ns"):
        return int(stat_result.st_birthtime_ns)
    if hasattr(stat_result, "st_birthtime"):
        return int(stat_result.st_birthtime * 1_000_000_000)
    return None


def file_kind(ext: str) -> str:
    if ext in DM_EXTS:
        return "digital_micrograph"
    if ext == ".dmw":
        return "digital_micrograph_workspace"
    if ext in RASTER_EXTS:
        return "raster_image"
    if ext in TEXT_EXTS:
        return "text_or_metadata"
    return "other"


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def import_dm_loader() -> Any | None:
    try:
        from rsciio.digitalmicrograph import file_reader

        return file_reader
    except Exception:
        pass

    try:
        import hyperspy.api as hs
    except Exception:
        return None
    return hs.load


class DMLoadTimeout(TimeoutError):
    pass


def load_dm_file(dm_loader: Any, path: Path, timeout_seconds: float) -> Any:
    if timeout_seconds <= 0:
        return dm_loader(str(path), lazy=True)

    previous_handler = signal.getsignal(signal.SIGALRM)

    def timeout_handler(signum: int, frame: Any) -> None:
        raise DMLoadTimeout(f"timed out after {timeout_seconds:g} seconds")

    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        return dm_loader(str(path), lazy=True)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return {"__bytes_hex__": value.hex()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if hasattr(value, "as_dictionary"):
        try:
            return to_jsonable(value.as_dictionary())
        except Exception:
            return repr(value)
    if hasattr(value, "tolist"):
        try:
            return to_jsonable(value.tolist())
        except Exception:
            return repr(value)
    return repr(value)


def json_dumps(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def flatten_json(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from flatten_json(child, child_prefix)
    elif isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            yield prefix, value
        else:
            for idx, child in enumerate(value):
                yield from flatten_json(child, f"{prefix}[{idx}]")
    else:
        yield prefix, value


def value_fields(value: Any) -> tuple[str, str, float | None]:
    if value is None:
        return "null", "", None
    if isinstance(value, bool):
        return "bool", "1" if value else "0", 1.0 if value else 0.0
    if isinstance(value, int):
        return "int", str(value), float(value)
    if isinstance(value, float):
        return "float", repr(value), value
    if isinstance(value, str):
        return "str", value, None
    return type(value).__name__, json_dumps(value), None


def classify_signal(
    path: str,
    title: str,
    signal_type: str,
    signal_dimension: int | None,
    navigation_dimension: int | None,
    ext: str,
) -> str:
    title_simple = simple_name(title or Path(path).stem)
    path_lower = path.lower()
    signal_lower = signal_type.lower()
    if "eds" in signal_lower or "eds spectrum image" in path_lower or re.search(r"\(eds\)", path_lower):
        return "eds"
    if "eels" in signal_lower or "eels spectrum image" in path_lower:
        return "eels"
    if signal_dimension == 2 and navigation_dimension == 0:
        if title_simple in GENERIC_IMAGE_TITLES:
            return "support_image"
        if "signal (eds)" in path_lower:
            return "eds"
        return "image"
    if ext in RASTER_EXTS:
        return "image"
    return "other"


def connect_database(path: Path) -> sqlite3.Connection:
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

        CREATE TABLE files (
            file_id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            absolute_path TEXT NOT NULL,
            parent_path TEXT NOT NULL,
            filename TEXT NOT NULL,
            stem TEXT NOT NULL,
            ext TEXT NOT NULL,
            kind TEXT NOT NULL,
            top_folder TEXT NOT NULL,
            inferred_sample TEXT NOT NULL,
            acquisition_folder TEXT NOT NULL,
            depth INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL,
            modified_ns INTEGER NOT NULL,
            modified_utc TEXT NOT NULL,
            changed_ns INTEGER NOT NULL,
            changed_utc TEXT NOT NULL,
            created_ns INTEGER,
            created_utc TEXT NOT NULL,
            inode TEXT NOT NULL,
            mode INTEGER NOT NULL,
            sha256 TEXT NOT NULL DEFAULT '',
            load_status TEXT NOT NULL DEFAULT 'not_loaded',
            load_error TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE signals (
            signal_id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
            signal_index INTEGER NOT NULL,
            title TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            signal_dimension INTEGER,
            navigation_dimension INTEGER,
            data_shape TEXT NOT NULL,
            data_dtype TEXT NOT NULL,
            data_ndim INTEGER,
            axes_count INTEGER,
            acquisition_date TEXT NOT NULL,
            acquisition_time TEXT NOT NULL,
            category TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            UNIQUE(file_id, signal_index)
        );

        CREATE TABLE axes (
            axis_id INTEGER PRIMARY KEY,
            signal_id INTEGER NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
            axis_index INTEGER NOT NULL,
            name TEXT NOT NULL,
            size INTEGER,
            scale REAL,
            offset REAL,
            units TEXT NOT NULL,
            navigate INTEGER,
            is_binned INTEGER,
            metadata_json TEXT NOT NULL,
            UNIQUE(signal_id, axis_index)
        );

        CREATE TABLE metadata_kv (
            kv_id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
            signal_id INTEGER REFERENCES signals(signal_id) ON DELETE CASCADE,
            source TEXT NOT NULL,
            key_path TEXT NOT NULL,
            value_type TEXT NOT NULL,
            value_text TEXT NOT NULL,
            value_number REAL
        );

        CREATE VIEW file_signals AS
        SELECT
            f.path,
            f.filename,
            f.ext,
            f.kind,
            f.inferred_sample,
            f.top_folder,
            f.acquisition_folder,
            f.size_bytes,
            f.modified_utc,
            f.sha256,
            f.load_status,
            f.load_error,
            s.signal_index,
            s.title,
            s.signal_type,
            s.signal_dimension,
            s.navigation_dimension,
            s.data_shape,
            s.data_dtype,
            s.acquisition_date,
            s.acquisition_time,
            s.category
        FROM files f
        LEFT JOIN signals s ON s.file_id = f.file_id;

        CREATE VIEW searchable_metadata AS
        SELECT
            f.path,
            f.inferred_sample,
            f.filename,
            s.title,
            s.category,
            kv.source,
            kv.key_path,
            kv.value_text,
            kv.value_number
        FROM metadata_kv kv
        JOIN files f ON f.file_id = kv.file_id
        LEFT JOIN signals s ON s.signal_id = kv.signal_id;
        """
    )


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX idx_files_ext ON files(ext);
        CREATE INDEX idx_files_kind ON files(kind);
        CREATE INDEX idx_files_sample ON files(inferred_sample);
        CREATE INDEX idx_files_top_folder ON files(top_folder);
        CREATE INDEX idx_files_modified ON files(modified_utc);
        CREATE INDEX idx_files_size ON files(size_bytes);
        CREATE INDEX idx_signals_file ON signals(file_id);
        CREATE INDEX idx_signals_category ON signals(category);
        CREATE INDEX idx_signals_type ON signals(signal_type);
        CREATE INDEX idx_signals_acq_date ON signals(acquisition_date);
        CREATE INDEX idx_axes_signal ON axes(signal_id);
        CREATE INDEX idx_metadata_key ON metadata_kv(key_path);
        CREATE INDEX idx_metadata_signal_source_key ON metadata_kv(signal_id, source, key_path);
        CREATE INDEX idx_metadata_file_source_key ON metadata_kv(file_id, source, key_path);
        CREATE INDEX idx_metadata_source_key ON metadata_kv(source, key_path);
        CREATE INDEX idx_metadata_value_text ON metadata_kv(value_text);
        CREATE INDEX idx_metadata_value_number ON metadata_kv(value_number);
        """
    )


def insert_kv(
    conn: sqlite3.Connection,
    file_id: int,
    signal_id: int | None,
    source: str,
    payload: Any,
) -> int:
    rows = []
    for key_path, value in flatten_json(to_jsonable(payload)):
        value_type, value_text, value_number = value_fields(value)
        rows.append((file_id, signal_id, source, key_path, value_type, value_text, value_number))
    conn.executemany(
        """
        INSERT INTO metadata_kv (
            file_id, signal_id, source, key_path, value_type, value_text, value_number
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def get_original_metadata_item(original_metadata: Any, key_path: str) -> Any:
    if isinstance(original_metadata, dict):
        current = original_metadata
        for part in key_path.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current
    if not hasattr(original_metadata, "get_item"):
        return None
    try:
        return original_metadata.get_item(key_path)
    except Exception:
        return None


def original_metadata_subset(original_metadata: Any) -> dict[str, Any]:
    subset = {}
    for key_path in ORIGINAL_METADATA_KEY_PATHS:
        value = get_original_metadata_item(original_metadata, key_path)
        if value is not None:
            subset[key_path] = value
    return subset


def insert_file(
    conn: sqlite3.Connection,
    archive_root: Path,
    path: Path,
    include_hash: bool,
) -> int:
    rel = path.relative_to(archive_root)
    stat_result = path.stat()
    created_ns = birthtime_ns(stat_result)
    ext = path.suffix.lower()
    parent_path = str(rel.parent) if str(rel.parent) != "." else ""
    acquisition_folder = parent_path
    row = {
        "path": str(rel),
        "absolute_path": str(path.resolve()),
        "parent_path": parent_path,
        "filename": rel.name,
        "stem": path.stem,
        "ext": ext,
        "kind": file_kind(ext),
        "top_folder": rel.parts[0] if rel.parts else "",
        "inferred_sample": infer_sample(rel),
        "acquisition_folder": acquisition_folder,
        "depth": len(rel.parts) - 1,
        "size_bytes": stat_result.st_size,
        "modified_ns": stat_result.st_mtime_ns,
        "modified_utc": utc_from_ns(stat_result.st_mtime_ns),
        "changed_ns": stat_result.st_ctime_ns,
        "changed_utc": utc_from_ns(stat_result.st_ctime_ns),
        "created_ns": created_ns,
        "created_utc": utc_from_ns(created_ns),
        "inode": str(stat_result.st_ino),
        "mode": stat_result.st_mode,
        "sha256": sha256_file(path) if include_hash else "",
    }
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    cursor = conn.execute(
        f"INSERT INTO files ({columns}) VALUES ({placeholders})",
        tuple(row.values()),
    )
    file_id = int(cursor.lastrowid)
    insert_kv(conn, file_id, None, "filesystem", row)
    return file_id


def axis_payload(axis: Any) -> dict[str, Any]:
    if isinstance(axis, dict):
        return {
            "name": axis.get("name") or "",
            "size": axis.get("size"),
            "scale": axis.get("scale"),
            "offset": axis.get("offset"),
            "units": axis.get("units") or "",
            "navigate": axis.get("navigate"),
            "is_binned": axis.get("is_binned"),
        }
    return {
        "name": getattr(axis, "name", "") or "",
        "size": getattr(axis, "size", None),
        "scale": getattr(axis, "scale", None),
        "offset": getattr(axis, "offset", None),
        "units": getattr(axis, "units", "") or "",
        "navigate": getattr(axis, "navigate", None),
        "is_binned": getattr(axis, "is_binned", None),
    }


def axes_for_metadata(axes_manager: Any) -> list[Any]:
    try:
        return list(axes_manager.navigation_axes) + list(axes_manager.signal_axes)
    except Exception:
        return list(getattr(axes_manager, "_axes", []))


def normalized_acquisition_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return text


def normalized_acquisition_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("\u2028", "").strip()
    for fmt in ("%H:%M:%S", "%H:%M:%S.%f", "%I:%M:%S %p", "%I:%M %p"):
        try:
            return dt.datetime.strptime(text, fmt).time().isoformat()
        except ValueError:
            pass
    return text


def first_original_metadata_value(original_metadata: Any, key_paths: Iterable[str]) -> Any:
    for key_path in key_paths:
        value = get_original_metadata_item(original_metadata, key_path)
        if value is not None:
            return value
    return None


def signal_payload_from_reader(signal: Any) -> dict[str, Any]:
    if isinstance(signal, dict):
        metadata = to_jsonable(signal.get("metadata", {}))
        original_metadata_source = signal.get("original_metadata", {})
        original_metadata = original_metadata_subset(original_metadata_source)
        general = metadata.get("General", {}) if isinstance(metadata, dict) else {}
        signal_md = metadata.get("Signal", {}) if isinstance(metadata, dict) else {}
        axes = list(signal.get("axes") or [])
        data = signal.get("data")
        data_shape_values = tuple(getattr(data, "shape", ()) or ())
        navigation_dimension = sum(1 for axis in axes if bool(axis_payload(axis).get("navigate")))
        signal_dimension = max(len(axes) - navigation_dimension, 0)
        acquisition_date = general.get("date") or first_original_metadata_value(
            original_metadata_source,
            (
                "ImageList.TagGroup0.ImageTags.DataBar.Acquisition Date",
                "ImageList.TagGroup0.ImageTags.SI.Acquisition.Date",
            ),
        )
        acquisition_time = general.get("time") or first_original_metadata_value(
            original_metadata_source,
            (
                "ImageList.TagGroup0.ImageTags.DataBar.Acquisition Time",
                "ImageList.TagGroup0.ImageTags.SI.Acquisition.Start time",
            ),
        )
        if isinstance(metadata, dict):
            general.setdefault("date", normalized_acquisition_date(acquisition_date))
            general.setdefault("time", normalized_acquisition_time(acquisition_time))
        return {
            "metadata": metadata,
            "original_metadata": original_metadata,
            "title": str(general.get("title") or ""),
            "signal_type": str(signal_md.get("signal_type") or ""),
            "signal_dimension": signal_dimension,
            "navigation_dimension": navigation_dimension,
            "data_shape": "x".join(str(v) for v in data_shape_values),
            "data_dtype": str(getattr(data, "dtype", "")),
            "data_ndim": int(getattr(data, "ndim", len(data_shape_values))),
            "axes": axes,
            "acquisition_date": str(general.get("date") or ""),
            "acquisition_time": str(general.get("time") or ""),
        }

    try:
        metadata = to_jsonable(signal.metadata.as_dictionary())
    except Exception:
        metadata = {}
    original_metadata = original_metadata_subset(signal.original_metadata)
    general = metadata.get("General", {}) if isinstance(metadata, dict) else {}
    signal_md = metadata.get("Signal", {}) if isinstance(metadata, dict) else {}
    axes_manager = signal.axes_manager
    axes = axes_for_metadata(axes_manager)
    return {
        "metadata": metadata,
        "original_metadata": original_metadata,
        "title": str(general.get("title") or ""),
        "signal_type": str(signal_md.get("signal_type") or ""),
        "signal_dimension": int(axes_manager.signal_dimension),
        "navigation_dimension": int(axes_manager.navigation_dimension),
        "data_shape": "x".join(str(v) for v in signal.data.shape),
        "data_dtype": str(getattr(signal.data, "dtype", "")),
        "data_ndim": int(getattr(signal.data, "ndim", len(signal.data.shape))),
        "axes": axes,
        "acquisition_date": str(general.get("date") or ""),
        "acquisition_time": str(general.get("time") or ""),
    }


def insert_dm_metadata(
    conn: sqlite3.Connection,
    dm_loader: Any,
    file_id: int,
    path: Path,
    rel_path: str,
    dm_timeout: float,
) -> int:
    try:
        loaded = load_dm_file(dm_loader, path, dm_timeout)
    except Exception as exc:
        conn.execute(
            "UPDATE files SET load_status = ?, load_error = ? WHERE file_id = ?",
            ("load_error", f"{type(exc).__name__}: {exc}", file_id),
        )
        return 0

    signals = loaded if isinstance(loaded, list) else [loaded]
    inserted = 0
    for signal_index, signal in enumerate(signals):
        payload = signal_payload_from_reader(signal)
        metadata = payload["metadata"]
        original_metadata = payload["original_metadata"]
        title = payload["title"]
        signal_type = payload["signal_type"]
        category = classify_signal(
            rel_path,
            title,
            signal_type,
            payload["signal_dimension"],
            payload["navigation_dimension"],
            path.suffix.lower(),
        )
        axes = payload["axes"]
        cursor = conn.execute(
            """
            INSERT INTO signals (
                file_id, signal_index, title, signal_type, signal_dimension,
                navigation_dimension, data_shape, data_dtype, data_ndim, axes_count,
                acquisition_date, acquisition_time, category, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                signal_index,
                title,
                signal_type,
                payload["signal_dimension"],
                payload["navigation_dimension"],
                payload["data_shape"],
                payload["data_dtype"],
                payload["data_ndim"],
                len(axes),
                payload["acquisition_date"],
                payload["acquisition_time"],
                category,
                json_dumps(metadata),
            ),
        )
        signal_id = int(cursor.lastrowid)
        insert_kv(conn, file_id, signal_id, "hyperspy_metadata", metadata)
        insert_kv(conn, file_id, signal_id, "hyperspy_original_metadata", original_metadata)

        for axis_index, axis in enumerate(axes):
            payload = axis_payload(axis)
            metadata_json = json_dumps(payload)
            conn.execute(
                """
                INSERT INTO axes (
                    signal_id, axis_index, name, size, scale, offset, units,
                    navigate, is_binned, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id,
                    axis_index,
                    str(payload["name"]),
                    payload["size"],
                    payload["scale"],
                    payload["offset"],
                    str(payload["units"]),
                    None if payload["navigate"] is None else int(bool(payload["navigate"])),
                    None if payload["is_binned"] is None else int(bool(payload["is_binned"])),
                    metadata_json,
                ),
            )
            insert_kv(conn, file_id, signal_id, f"axis_{axis_index}", payload)
        inserted += 1

    conn.execute(
        "UPDATE files SET load_status = ?, load_error = ? WHERE file_id = ?",
        ("loaded", "", file_id),
    )
    return inserted


def discover_files(archive_root: Path) -> list[Path]:
    return sorted(path for path in archive_root.rglob("*") if path.is_file())


def write_run_info(conn: sqlite3.Connection, args: argparse.Namespace, archive_root: Path, files: list[Path]) -> None:
    values = {
        "created_utc": dt.datetime.now(dt.UTC).isoformat(),
        "archive_root": str(archive_root.resolve()),
        "file_count_discovered": str(len(files)),
        "include_hash": str(bool(args.hash)),
        "extract_dm": str(not args.no_dm),
        "dm_timeout_seconds": str(args.dm_timeout),
        "original_metadata_json": "dropped",
        "original_metadata_kv_mode": "allowlist",
        "original_metadata_kv_key_count": str(len(ORIGINAL_METADATA_KEY_PATHS)),
        "python": sys.version.replace("\n", " "),
        "executable": sys.executable,
    }
    conn.executemany(
        "INSERT INTO run_info(key, value) VALUES (?, ?)",
        sorted(values.items()),
    )


def vacuum_database(conn: sqlite3.Connection) -> None:
    conn.commit()
    conn.execute("VACUUM")


def gzip_copy(path: Path) -> Path:
    import gzip

    gz_path = path.with_suffix(path.suffix + ".gz")
    with path.open("rb") as src, gzip.open(gz_path, "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)
    return gz_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract STEM-Bank-26 archive filesystem and DigitalMicrograph metadata into SQLite."
    )
    parser.add_argument(
        "archive_root",
        nargs="?",
        default="STEM-Bank-26 Archive Sampler",
        help="Archive directory to scan. Default: 'STEM-Bank-26 Archive Sampler'.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="_archive_inventory/stem_bank_26_metadata.sqlite",
        help="SQLite output path. Default: _archive_inventory/stem_bank_26_metadata.sqlite.",
    )
    parser.add_argument(
        "--hash",
        action="store_true",
        help="Compute SHA-256 for every file. This reads the full 67 GB archive and is slower.",
    )
    parser.add_argument(
        "--no-dm",
        action="store_true",
        help="Only record filesystem metadata; do not load .dm3/.dm4 files with HyperSpy.",
    )
    parser.add_argument(
        "--gzip-copy",
        action="store_true",
        help="Also write a compressed .sqlite.gz copy for transfer/storage. Query the .sqlite file directly.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only the first N files. Useful for testing.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print progress every N files. Default: 100.",
    )
    parser.add_argument(
        "--dm-timeout",
        type=float,
        default=300.0,
        help="Maximum seconds to allow a single DM3/DM4 HyperSpy load. Use 0 to disable. Default: 300.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    archive_root = Path(args.archive_root)
    if not archive_root.is_dir():
        print(f"Archive root not found: {archive_root}", file=sys.stderr)
        return 2

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(output.name + ".tmp")
    if temp_output.exists():
        temp_output.unlink()

    files = discover_files(archive_root)
    if args.limit:
        files = files[: args.limit]

    dm_loader = None if args.no_dm else import_dm_loader()
    if not args.no_dm and dm_loader is None:
        print("No DM metadata loader is available; continuing with filesystem metadata only.", file=sys.stderr)

    conn = connect_database(temp_output)
    try:
        create_schema(conn)
        write_run_info(conn, args, archive_root, files)

        signal_count = 0
        loaded_count = 0
        load_error_count = 0
        for index, path in enumerate(files, start=1):
            file_id = insert_file(conn, archive_root, path, args.hash)
            if dm_loader is not None and path.suffix.lower() in DM_EXTS:
                before = signal_count
                signal_count += insert_dm_metadata(
                    conn,
                    dm_loader,
                    file_id,
                    path,
                    str(path.relative_to(archive_root)),
                    args.dm_timeout,
                )
                if signal_count > before:
                    loaded_count += 1
                else:
                    status = conn.execute(
                        "SELECT load_status FROM files WHERE file_id = ?",
                        (file_id,),
                    ).fetchone()[0]
                    if status == "load_error":
                        load_error_count += 1

            if args.progress_every and index % args.progress_every == 0:
                conn.commit()
                print(f"Processed {index}/{len(files)} files; {signal_count} signals")

        create_indexes(conn)
        conn.execute("INSERT INTO run_info(key, value) VALUES (?, ?)", ("dm_files_loaded", str(loaded_count)))
        conn.execute("INSERT INTO run_info(key, value) VALUES (?, ?)", ("dm_load_errors", str(load_error_count)))
        conn.execute("INSERT INTO run_info(key, value) VALUES (?, ?)", ("signal_count", str(signal_count)))
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
