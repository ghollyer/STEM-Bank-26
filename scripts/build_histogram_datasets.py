#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

from inventory_paths import default_inventory_dir


ELEMENTS = {
    "H": "Hydrogen", "He": "Helium", "Li": "Lithium", "Be": "Beryllium",
    "B": "Boron", "C": "Carbon", "N": "Nitrogen", "O": "Oxygen",
    "F": "Fluorine", "Ne": "Neon", "Na": "Sodium", "Mg": "Magnesium",
    "Al": "Aluminium", "Si": "Silicon", "P": "Phosphorus", "S": "Sulfur",
    "Cl": "Chlorine", "Ar": "Argon", "K": "Potassium", "Ca": "Calcium",
    "Sc": "Scandium", "Ti": "Titanium", "V": "Vanadium", "Cr": "Chromium",
    "Mn": "Manganese", "Fe": "Iron", "Co": "Cobalt", "Ni": "Nickel",
    "Cu": "Copper", "Zn": "Zinc", "Ga": "Gallium", "Ge": "Germanium",
    "As": "Arsenic", "Se": "Selenium", "Br": "Bromine", "Kr": "Krypton",
    "Rb": "Rubidium", "Sr": "Strontium", "Y": "Yttrium", "Zr": "Zirconium",
    "Nb": "Niobium", "Mo": "Molybdenum", "Tc": "Technetium", "Ru": "Ruthenium",
    "Rh": "Rhodium", "Pd": "Palladium", "Ag": "Silver", "Cd": "Cadmium",
    "In": "Indium", "Sn": "Tin", "Sb": "Antimony", "Te": "Tellurium",
    "I": "Iodine", "Xe": "Xenon", "Cs": "Cesium", "Ba": "Barium",
    "La": "Lanthanum", "Ce": "Cerium", "Pr": "Praseodymium", "Nd": "Neodymium",
    "Pm": "Promethium", "Sm": "Samarium", "Eu": "Europium", "Gd": "Gadolinium",
    "Tb": "Terbium", "Dy": "Dysprosium", "Ho": "Holmium", "Er": "Erbium",
    "Tm": "Thulium", "Yb": "Ytterbium", "Lu": "Lutetium", "Hf": "Hafnium",
    "Ta": "Tantalum", "W": "Tungsten", "Re": "Rhenium", "Os": "Osmium",
    "Ir": "Iridium", "Pt": "Platinum", "Au": "Gold", "Hg": "Mercury",
    "Tl": "Thallium", "Pb": "Lead", "Bi": "Bismuth", "Po": "Polonium",
    "At": "Astatine", "Rn": "Radon", "Fr": "Francium", "Ra": "Radium",
    "Ac": "Actinium", "Th": "Thorium", "Pa": "Protactinium", "U": "Uranium",
    "Np": "Neptunium", "Pu": "Plutonium", "Am": "Americium", "Cm": "Curium",
    "Bk": "Berkelium", "Cf": "Californium", "Es": "Einsteinium", "Fm": "Fermium",
    "Md": "Mendelevium", "No": "Nobelium", "Lr": "Lawrencium", "Rf": "Rutherfordium",
    "Db": "Dubnium", "Sg": "Seaborgium", "Bh": "Bohrium", "Hs": "Hassium",
    "Mt": "Meitnerium", "Ds": "Darmstadtium", "Rg": "Roentgenium",
    "Cn": "Copernicium", "Nh": "Nihonium", "Fl": "Flerovium", "Mc": "Moscovium",
    "Lv": "Livermorium", "Ts": "Tennessine", "Og": "Oganesson",
}

COMMON_NON_SAMPLE_TOKENS = {
    "adf", "aged", "analytical", "archive", "area", "batch", "before", "box",
    "bisalt", "copy", "cryo", "data", "demo", "eds", "eels", "exfoliated", "fresh",
    "image", "imaging", "in", "insitu", "low", "map", "new", "old", "post",
    "pre", "recount", "reduced", "region", "sample", "session", "si", "situ",
    "stem", "survey", "test", "tested", "trial", "unknown",
    "nanoparticle", "nanoparticles", "np", "nps",
}

# These symbols are valid elements, but in this dataset they are almost always accidental
# token matches from sample names rather than chemically meaningful labels.
EXCLUDED_INFERRED_ELEMENTS = {
    # Noble gases.
    "He", "Ne", "Ar", "Kr", "Xe", "Rn", "Og",
    # Explicitly observed/requested false positives and neighboring radioactive elements.
    "Po", "At", "Fr", "Ra", "Ac", "Pa",
    # Synthetic or mostly synthetic elements.
    "Tc", "Pm", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md",
    "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn",
    "Nh", "Fl", "Mc", "Lv", "Ts",
}

FORMULA_SYMBOL_RE = re.compile(r"[A-Z][a-z]?")
NANOPARTICLE_SUFFIX_RE = re.compile(r"(?:NPs?|NPS)$")
LEADING_DIGITS_RE = re.compile(r"^\d+")
LEADING_CONCENTRATION_RE = re.compile(r"^\d+(?:[._]\d+)?M", re.IGNORECASE)
ELECTROLYTE_IMIDE_SALT_RE = re.compile(r"^(Li|Na|K|Rb|Cs)?(?:TFSI|FSI)$", re.IGNORECASE)
IODINE_NAME_RE = re.compile(r"\b(?:iodine|iodide|iodate|triiodide)\b", re.IGNORECASE)
UPPERCASE_PAIR_SYMBOLS = {"SI": "Si", "TI": "Ti"}
TEMPERATURE_TOKEN_RE = re.compile(r"^\d+(?:\.\d+)?C$", re.IGNORECASE)
ONE_LETTER_CODE_RE = re.compile(r"^[BCFKNPSVW]\d+$")
IGNORED_FORMULA_LIKE_CODES = {"F200", "K2", "KC1118", "KC1219", "KK01", "OWBS1"}
K_EDGE_RE = re.compile(r"\bK\s+edge\b", re.IGNORECASE)
VOLTAGE_CONTEXT_RE = re.compile(r"(?:^|[\s,(-])-?\d+(?:\.\d+)?\s*V(?:\s|$)", re.IGNORECASE)


def parse_formula_token(token: str, candidate_elements: dict[str, str]) -> set[str] | None:
    if TEMPERATURE_TOKEN_RE.fullmatch(token):
        return None
    if token.upper() in IGNORED_FORMULA_LIKE_CODES:
        return None
    if ONE_LETTER_CODE_RE.fullmatch(token):
        return None
    if token.upper().startswith("SFO"):
        return None
    token = LEADING_DIGITS_RE.sub("", token)
    if not token:
        return None
    if len(token) == 1:
        return None
    if token.isupper() and not any(char.isdigit() for char in token) and len(token) > 1:
        return None

    symbols = set()
    i = 0
    while i < len(token):
        if token[i].isdigit():
            i += 1
            continue
        if not token[i].isupper():
            return None
        if i + 1 < len(token) and token[i + 1].islower():
            symbol = token[i : i + 2]
            if symbol not in candidate_elements:
                return None
            symbols.add(symbol)
            i += 2
            continue
        if i + 1 < len(token) and token[i : i + 2] in UPPERCASE_PAIR_SYMBOLS:
            symbol = UPPERCASE_PAIR_SYMBOLS[token[i : i + 2]]
            if symbol not in candidate_elements:
                return None
            symbols.add(symbol)
            i += 2
            continue
        symbol = token[i]
        if symbol not in candidate_elements:
            return None
        symbols.add(symbol)
        i += 1
    return symbols


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def prepare_image_magnifications(conn: sqlite3.Connection) -> None:
    """Recover missing trajectory magnifications from the same signal's DM metadata."""
    conn.executescript("""
        CREATE TEMP VIEW metric_image_magnifications AS
        SELECT i.image_id,
               coalesce(i.magnification_number, raw.magnification) AS magnification
        FROM image_sequence i
        LEFT JOIN (
            SELECT signal_id, max(value_number) AS magnification
            FROM meta.metadata_kv
            WHERE source = 'hyperspy_original_metadata'
              AND key_path = 'ImageList.TagGroup0.ImageTags.Microscope Info.Indicated Magnification'
              AND value_number > 0
            GROUP BY signal_id
            HAVING count(DISTINCT value_number) = 1
        ) raw ON raw.signal_id = i.signal_id;
    """)


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params))


def values(conn: sqlite3.Connection, sql: str) -> list[float]:
    out = []
    for row in conn.execute(sql):
        value = row[0]
        if value is None:
            continue
        try:
            f = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return out


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


def percentile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def stats(values_: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(values_)
    if not ordered:
        return {
            "count": 0,
            "min": None,
            "p25": None,
            "median": None,
            "mean": None,
            "p75": None,
            "p90": None,
            "p95": None,
            "max": None,
        }
    return {
        "count": len(ordered),
        "min": ordered[0],
        "p25": percentile(ordered, 0.25),
        "median": percentile(ordered, 0.50),
        "mean": sum(ordered) / len(ordered),
        "p75": percentile(ordered, 0.75),
        "p90": percentile(ordered, 0.90),
        "p95": percentile(ordered, 0.95),
        "max": ordered[-1],
    }


def transform_value(value: float, transform: str) -> float | None:
    if transform == "linear":
        return value
    if transform == "log10":
        return math.log10(value) if value > 0 else None
    if transform == "log1p":
        return math.log1p(value) if value >= 0 else None
    raise ValueError(f"Unsupported transform: {transform}")


def inverse_transform(value: float, transform: str) -> float:
    if transform == "linear":
        return value
    if transform == "log10":
        return 10**value
    if transform == "log1p":
        return math.expm1(value)
    raise ValueError(f"Unsupported transform: {transform}")


def histogram(values_: list[float], bins: int, transform: str) -> list[dict[str, Any]]:
    pairs = []
    skipped = 0
    for value in values_:
        transformed = transform_value(value, transform)
        if transformed is None:
            skipped += 1
            continue
        pairs.append((value, transformed))
    if not pairs:
        return []

    lo = min(t for _, t in pairs)
    hi = max(t for _, t in pairs)
    if lo == hi:
        return [
            {
                "bin_index": 0,
                "bin_start": inverse_transform(lo, transform),
                "bin_end": inverse_transform(hi, transform),
                "bin_label": f"{inverse_transform(lo, transform):g}",
                "count": len(pairs),
                "fraction": 1.0,
                "skipped_by_transform": skipped,
            }
        ]

    width = (hi - lo) / bins
    counts = [0 for _ in range(bins)]
    for _, transformed in pairs:
        idx = min(int((transformed - lo) / width), bins - 1)
        counts[idx] += 1

    total = len(pairs)
    out = []
    for idx, count in enumerate(counts):
        start_t = lo + idx * width
        end_t = lo + (idx + 1) * width
        start = inverse_transform(start_t, transform)
        end = inverse_transform(end_t, transform)
        out.append(
            {
                "bin_index": idx,
                "bin_start": start,
                "bin_end": end,
                "bin_label": f"[{start:g}, {end:g}{')' if idx < bins - 1 else ']'}",
                "count": count,
                "fraction": count / total if total else 0,
                "skipped_by_transform": skipped if idx == 0 else 0,
            }
        )
    return out


def write_csv(path: Path, fieldnames: list[str], row_iter: Iterable[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in row_iter:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def normalize_json(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {key: normalize_json(child) for key, child in value.items()}
    if isinstance(value, list):
        return [normalize_json(child) for child in value]
    return value


def infer_elements(sample_name: str) -> set[str]:
    found = set()
    text = sample_name or ""
    lower_text = text.lower()
    candidate_elements = {symbol: name for symbol, name in ELEMENTS.items() if symbol not in EXCLUDED_INFERRED_ELEMENTS}
    for symbol, name in candidate_elements.items():
        if len(name) > 2 and re.search(rf"\b{re.escape(name.lower())}\b", lower_text):
            found.add(symbol)
    if IODINE_NAME_RE.search(text):
        found.add("I")

    for token in re.split(r"[^A-Za-z0-9]+", text):
        if not token:
            continue
        token_lower = token.lower()
        if token_lower in COMMON_NON_SAMPLE_TOKENS:
            continue
        token = NANOPARTICLE_SUFFIX_RE.sub("", token)
        if not token:
            continue
        if token.lower().startswith("ipa"):
            continue
        if token == "K" and K_EDGE_RE.search(text):
            continue
        if token == "V" and VOLTAGE_CONTEXT_RE.search(text):
            continue
        electrolyte_token = LEADING_CONCENTRATION_RE.sub("", token)
        electrolyte_match = ELECTROLYTE_IMIDE_SALT_RE.fullmatch(electrolyte_token)
        if electrolyte_match:
            cation = electrolyte_match.group(1)
            if cation:
                found.add(cation[0].upper() + cation[1:].lower())
            continue
        upper_count = sum(1 for char in token if char.isupper())
        formula_like = any(char.isdigit() for char in token) or upper_count >= 2
        if len(token) <= 2 and token in candidate_elements:
            found.add(token)
            continue
        if not formula_like:
            if len(token) > 2 and token[:2] in candidate_elements and token[:2] in {
                "Ag", "Al", "Au", "Bi", "Ce", "Co", "Cu", "Fe", "Hf", "Li",
                "Mg", "Mo", "Na", "Ni", "Pd", "Pt", "Re", "Rh", "Ru", "Se",
                "Si", "Ti", "Zr",
            }:
                found.add(token[:2])
            continue
        symbols = parse_formula_token(token, candidate_elements)
        if symbols:
            found.update(symbols)
    return found


def build_numeric_metric(
    metric: str,
    description: str,
    source: str,
    values_: list[float],
    total_possible: int,
    bins: int,
    transform: str,
) -> dict[str, Any]:
    bin_rows = histogram(values_, bins, transform)
    return {
        "metric": metric,
        "kind": "numeric",
        "description": description,
        "source": source,
        "binning": {"bins": bins, "transform": transform},
        "observations": len(values_),
        "missing": max(total_possible - len(values_), 0),
        "statistics": stats(values_),
        "bins": bin_rows,
    }


def build() -> int:
    args = parse_args()
    metadata_db = Path(args.metadata_db)
    sessions_db = Path(args.sessions_db)
    trajectories_db = Path(args.trajectories_db)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    meta = connect(metadata_db)
    sess = connect(sessions_db)
    traj = connect(trajectories_db)
    meta.execute("ATTACH DATABASE ? AS tr", (str(trajectories_db),))
    traj.execute("ATTACH DATABASE ? AS sess", (str(sessions_db),))
    traj.execute("ATTACH DATABASE ? AS meta", (str(metadata_db),))
    prepare_image_magnifications(traj)
    sample_source = prepare_metric_views(traj)

    session_count = traj.execute("SELECT count(*) FROM metric_sample_sessions").fetchone()[0]
    trajectory_count = traj.execute("SELECT count(*) FROM trajectory_summary").fetchone()[0]
    si_count = traj.execute("SELECT count(*) FROM si_events").fetchone()[0]

    images_per_session = values(
        traj,
        """
        SELECT coalesce(ic.image_count, 0)
        FROM metric_sample_sessions ms
        LEFT JOIN (
            SELECT
                m.refined_session_id,
                count(DISTINCT CASE WHEN NOT (lower(coalesce(i.title, '')) LIKE '%si survey%' OR lower(coalesce(i.path, '')) LIKE '%si survey%') THEN i.image_id END) AS image_count
            FROM image_sequence i
            JOIN metric_file_to_session m ON m.source_file_id = i.source_file_id
            GROUP BY m.refined_session_id
        ) ic ON ic.refined_session_id = ms.refined_session_id
        """,
    )
    si_per_session = values(
        traj,
        """
        SELECT coalesce(sc.si_count, 0)
        FROM metric_sample_sessions ms
        LEFT JOIN (
            SELECT
                m.refined_session_id,
                count(DISTINCT e.si_event_id) AS si_count
            FROM si_events e
            JOIN si_event_assets a
              ON a.si_event_id = e.si_event_id
             AND a.asset_role = 'PRIMARY_SI_SIGNAL'
            JOIN metric_file_to_session m ON m.source_file_id = a.source_file_id
            GROUP BY m.refined_session_id
        ) sc ON sc.refined_session_id = ms.refined_session_id
        """,
    )

    sessions_per_sample_rows = rows(
        traj,
        """
        SELECT
            refined_session_id,
            refined_session_key,
            acquisition_date,
            refined_sample,
            source_session_count AS session_count
        FROM metric_sample_sessions
        ORDER BY source_session_count DESC, refined_sample, acquisition_date
        """,
    )
    sessions_per_sample = [float(row["session_count"]) for row in sessions_per_sample_rows]

    eels_target_values = values(
        meta,
        """
        SELECT kv.value_number
        FROM tr.si_event_assets a
        JOIN metadata_kv kv
          ON kv.signal_id = a.signal_id
         AND kv.source = 'hyperspy_original_metadata'
         AND kv.key_path = 'ImageList.TagGroup0.ImageTags.EELS Spectrometer.Energy loss (eV)'
        WHERE a.asset_role = 'PRIMARY_SI_SIGNAL'
          AND a.modality = 'EELS'
          AND kv.value_number IS NOT NULL
        """,
    )

    metrics = [
        build_numeric_metric(
            "image_magnification",
            "Magnification for STEM image actions after excluding ADF SI-raster child images.",
            "image_sequence magnification_number, falling back to same-signal DM Indicated Magnification",
            values(traj, "SELECT magnification FROM metric_image_magnifications WHERE magnification IS NOT NULL"),
            traj.execute("SELECT count(*) FROM image_sequence").fetchone()[0],
            args.bins,
            "log10",
        ),
        build_numeric_metric(
            "trajectory_length_transitions",
            "Number of image-to-image transitions in each split trajectory segment.",
            "trajectories.trajectory_summary.transition_count",
            values(traj, "SELECT transition_count FROM trajectory_summary"),
            trajectory_count,
            args.bins,
            "log1p",
        ),
        build_numeric_metric(
            "trajectory_length_time_seconds",
            "Elapsed wall-clock time in seconds in each split trajectory segment.",
            "trajectories.trajectory_summary.total_elapsed_seconds",
            values(traj, "SELECT total_elapsed_seconds FROM trajectory_summary WHERE total_elapsed_seconds IS NOT NULL"),
            trajectory_count,
            args.bins,
            "log1p",
        ),
        build_numeric_metric(
            "images_per_session",
            "Number of standalone STEM images per refined date/sample session, excluding SI surveys and ADF SI-raster child images.",
            "refined sample sessions left joined to trajectories.image_sequence through source_file_id",
            images_per_session,
            session_count,
            args.bins,
            "log1p",
        ),
        build_numeric_metric(
            "si_per_session",
            "Number of SI acquisition events per refined date/sample session.",
            "refined sample sessions left joined to trajectories.si_events through primary SI source_file_id",
            si_per_session,
            session_count,
            args.bins,
            "log1p",
        ),
        build_numeric_metric(
            "sessions_per_sample",
            "Number of source sessions per date-scoped refined sample.",
            "refined sample sessions grouped by refined_session_key",
            sessions_per_sample,
            len(sessions_per_sample_rows),
            args.bins,
            "log1p",
        ),
        build_numeric_metric(
            "si_size_pixels",
            "SI raster size as width_px * height_px.",
            "trajectories.si_events where width and height are present",
            values(traj, "SELECT si_width_px * si_height_px FROM si_events WHERE si_width_px IS NOT NULL AND si_height_px IS NOT NULL"),
            si_count,
            args.bins,
            "log10",
        ),
        build_numeric_metric(
            "eels_target_energy_loss_ev",
            "EELS spectrometer target energy loss in eV from raw DM/GMS metadata.",
            "metadata_kv key 'ImageList.TagGroup0.ImageTags.EELS Spectrometer.Energy loss (eV)' joined through si_event_assets",
            eels_target_values,
            traj.execute("SELECT count(*) FROM si_event_assets WHERE asset_role='PRIMARY_SI_SIGNAL' AND modality='EELS'").fetchone()[0],
            args.bins,
            "linear",
        ),
        build_numeric_metric(
            "eels_dispersion_ev_per_channel",
            "EELS dispersion/channel width in eV per channel.",
            "trajectories.si_events.eels_dispersion_ev_ch",
            values(traj, "SELECT eels_dispersion_ev_ch FROM si_events WHERE eels_dispersion_ev_ch IS NOT NULL"),
            traj.execute("SELECT count(*) FROM si_events WHERE modality IN ('EELS', 'EDS+EELS')").fetchone()[0],
            args.bins,
            "log10",
        ),
    ]

    element_counts: dict[str, int] = {}
    session_element_rows = []
    for row in rows(
        traj,
        """
        SELECT
            refined_session_id,
            refined_session_key,
            acquisition_date,
            refined_sample
        FROM metric_sample_sessions
        ORDER BY refined_session_id
        """,
    ):
        elements = sorted(infer_elements(row["refined_sample"]))
        for symbol in elements:
            element_counts[symbol] = element_counts.get(symbol, 0) + 1
        session_element_rows.append(
            {
                "refined_session_id": row["refined_session_id"],
                "refined_session_key": row["refined_session_key"],
                "acquisition_date": row["acquisition_date"],
                "refined_sample": row["refined_sample"],
                "elements": ";".join(elements),
                "element_count": len(elements),
            }
        )

    element_metric = {
        "metric": "element_presence_by_session",
        "kind": "categorical",
        "description": "Presence of chemical elements inferred conservatively from refined sample names.",
        "source": "refined_sample_sessions.refined_sample parsed for element symbols/formulas/names",
        "observations": session_count,
        "categories": [
            {
                "symbol": symbol,
                "element": ELEMENTS[symbol],
                "session_count": count,
                "fraction_sessions": count / session_count if session_count else 0,
            }
            for symbol, count in sorted(element_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
    }

    samples_rows = [
        {
            "refined_session_id": row["refined_session_id"],
            "refined_session_key": row["refined_session_key"],
            "acquisition_date": row["acquisition_date"],
            "refined_sample": row["refined_sample"],
            "session_count": row["session_count"],
        }
        for row in sessions_per_sample_rows
    ]

    for metric in metrics:
        write_csv(
            output_dir / f"{metric['metric']}.csv",
            ["metric", "bin_index", "bin_start", "bin_end", "bin_label", "count", "fraction", "skipped_by_transform"],
            (dict(row, metric=metric["metric"]) for row in metric["bins"]),
        )
    write_csv(
        output_dir / "element_presence_by_session.csv",
        ["symbol", "element", "session_count", "fraction_sessions"],
        element_metric["categories"],
    )
    write_csv(
        output_dir / "session_elements.csv",
        ["refined_session_id", "refined_session_key", "acquisition_date", "refined_sample", "elements", "element_count"],
        session_element_rows,
    )
    write_csv(
        output_dir / "sessions_per_sample_counts.csv",
        ["refined_session_id", "refined_session_key", "acquisition_date", "refined_sample", "session_count"],
        samples_rows,
    )

    summary = {
        "created_utc": dt.datetime.now(dt.UTC).isoformat(),
        "source_databases": {
            "metadata_db": str(metadata_db.resolve()),
            "sessions_db": str(sessions_db.resolve()),
            "trajectories_db": str(trajectories_db.resolve()),
        },
        "sample_source": sample_source,
        "bin_count_default": args.bins,
        "metrics": metrics + [element_metric],
    }
    summary_path = output_dir / "histograms_summary.json"
    summary_path.write_text(json.dumps(normalize_json(summary), indent=2, sort_keys=True), encoding="utf-8")
    with summary_path.open("rb") as src, gzip.open(str(summary_path) + ".gz", "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)

    sqlite_path = output_dir / "stem_bank_26_histograms.sqlite"
    temp_sqlite_path = Path(tempfile.gettempdir()) / f"{sqlite_path.name}.{os.getpid()}.tmp"
    if temp_sqlite_path.exists():
        temp_sqlite_path.unlink()
    hconn = sqlite3.connect(temp_sqlite_path)
    hconn.executescript(
        """
        CREATE TABLE histogram_metadata (
            metric TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            description TEXT NOT NULL,
            source TEXT NOT NULL,
            observations INTEGER NOT NULL,
            missing INTEGER,
            binning_json TEXT NOT NULL,
            statistics_json TEXT NOT NULL
        );

        CREATE TABLE histogram_bins (
            metric TEXT NOT NULL,
            bin_index INTEGER NOT NULL,
            bin_start REAL,
            bin_end REAL,
            bin_label TEXT NOT NULL,
            count INTEGER NOT NULL,
            fraction REAL NOT NULL,
            PRIMARY KEY (metric, bin_index)
        );

        CREATE TABLE categorical_counts (
            metric TEXT NOT NULL,
            category TEXT NOT NULL,
            label TEXT NOT NULL,
            count INTEGER NOT NULL,
            fraction REAL NOT NULL,
            PRIMARY KEY (metric, category)
        );

        CREATE TABLE session_elements (
            refined_session_id INTEGER PRIMARY KEY,
            refined_session_key TEXT NOT NULL,
            acquisition_date TEXT NOT NULL,
            refined_sample TEXT NOT NULL,
            elements TEXT NOT NULL,
            element_count INTEGER NOT NULL
        );

        CREATE TABLE sessions_per_sample_counts (
            refined_session_id INTEGER PRIMARY KEY,
            refined_session_key TEXT NOT NULL,
            acquisition_date TEXT NOT NULL,
            refined_sample TEXT NOT NULL,
            session_count INTEGER NOT NULL
        );
        """
    )
    for metric in metrics:
        hconn.execute(
            """
            INSERT INTO histogram_metadata (
                metric, kind, description, source, observations, missing,
                binning_json, statistics_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metric["metric"],
                metric["kind"],
                metric["description"],
                metric["source"],
                metric["observations"],
                metric["missing"],
                json.dumps(metric["binning"], sort_keys=True),
                json.dumps(normalize_json(metric["statistics"]), sort_keys=True),
            ),
        )
        hconn.executemany(
            """
            INSERT INTO histogram_bins (
                metric, bin_index, bin_start, bin_end, bin_label, count, fraction
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    metric["metric"],
                    row["bin_index"],
                    row["bin_start"],
                    row["bin_end"],
                    row["bin_label"],
                    row["count"],
                    row["fraction"],
                )
                for row in metric["bins"]
            ],
        )
    hconn.execute(
        """
        INSERT INTO histogram_metadata (
            metric, kind, description, source, observations, missing,
            binning_json, statistics_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            element_metric["metric"],
            element_metric["kind"],
            element_metric["description"],
            element_metric["source"],
            element_metric["observations"],
            None,
            "{}",
            "{}",
        ),
    )
    hconn.executemany(
        """
        INSERT INTO categorical_counts (metric, category, label, count, fraction)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                element_metric["metric"],
                row["symbol"],
                row["element"],
                row["session_count"],
                row["fraction_sessions"],
            )
            for row in element_metric["categories"]
        ],
    )
    hconn.executemany(
        """
        INSERT INTO session_elements (
            refined_session_id, refined_session_key, acquisition_date, refined_sample, elements, element_count
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["refined_session_id"],
                row["refined_session_key"],
                row["acquisition_date"],
                row["refined_sample"],
                row["elements"],
                row["element_count"],
            )
            for row in session_element_rows
        ],
    )
    hconn.executemany(
        """
        INSERT INTO sessions_per_sample_counts (
            refined_session_id, refined_session_key, acquisition_date, refined_sample, session_count
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                row["refined_session_id"],
                row["refined_session_key"],
                row["acquisition_date"],
                row["refined_sample"],
                row["session_count"],
            )
            for row in samples_rows
        ],
    )
    hconn.executescript(
        """
        CREATE INDEX idx_histogram_bins_metric ON histogram_bins(metric, bin_index);
        CREATE INDEX idx_categorical_counts_count ON categorical_counts(metric, count DESC);
        """
    )
    hconn.commit()
    hconn.execute("VACUUM")
    hconn.close()
    shutil.copyfile(temp_sqlite_path, sqlite_path)
    temp_sqlite_path.unlink()
    with sqlite_path.open("rb") as src, gzip.open(str(sqlite_path) + ".gz", "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)

    print(f"Wrote {output_dir}")
    print(f"Wrote {summary_path}")
    print(f"Wrote {sqlite_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build histogram datasets from STEM-Bank-26 metadata/session/trajectory SQLite databases."
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
        default=str(inventory_dir / "histogram_datasets"),
    )
    parser.add_argument("--bins", type=int, default=30)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(build())
