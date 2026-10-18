# STEM-Bank-26 Metadata Tools

Utilities for extracting compact metadata from a STEM-Bank-26 DigitalMicrograph archive, grouping files
into microscopy sessions, deriving serial STEM/SI trajectories, and exporting paper-ready summary
statistics and plotting datasets.

Full raw microscope archives, the local `sample data/` subset, and generated inventory products are
intentionally excluded from Git. The example workflow refers to that local subset; copy it into place
separately before running the example.

## Requirements

- Python 3.10 or newer
- RosettaSciIO, listed in `requirements.txt`
- SQLite, available through Python's standard library
- Optional: Jupyter and Matplotlib for the example image-grid notebook
- Optional: a TeX distribution or Tectonic for the TikZ appendix diagram

Install the Python dependency into an environment of your choice:

```sh
python -m pip install -r requirements.txt
```

## Full Dataset Rebuild

To rebuild all SQLite databases and derived summaries from the full archive in one run:

```sh
PYTHON="/path/to/python-with-rsciio" \
STEM_BANK_26_ARCHIVE_ROOT="/path/to/STEM-Bank-26/Data" \
bash scripts/rebuild_full_dataset.sh
```

The script writes to `_archive_inventory/rebuild_YYYYMMDD/` under the archive. It stops if that
output directory already exists; set `STEM_BANK_26_REBUILD_ID` to choose a different dated folder,
or set `STEM_BANK_26_OUTPUT_DIR` to use a specific location. Existing inventory files are left as
they are. The rebuild includes the metadata, session, trajectory, and histogram SQLite databases,
plus compressed copies and paper metric exports.

## Archive Layout

The scripts expect an archive root containing DigitalMicrograph files.

```text
STEM-Bank-26 Archive Sampler/
  sample_or_date_folder/
    *.dm3
    *.dm4
```

Any path can be used. The commands below use `ARCHIVE_ROOT` and `INVENTORY_DIR` shell variables so
machine-specific paths are not embedded in the scripts.

```sh
ARCHIVE_ROOT="/path/to/STEM-Bank-26/Data"
INVENTORY_DIR="$ARCHIVE_ROOT/_archive_inventory"
mkdir -p "$INVENTORY_DIR"
```

## Core Workflow

Build the raw metadata database:

```sh
python -u scripts/extract_stem_bank_26_metadata.py "$ARCHIVE_ROOT" \
  --progress-every 10000 \
  --dm-timeout 300 \
  --gzip-copy \
  -o "$INVENTORY_DIR/stem_bank_26_metadata.sqlite"
```

Build date/sample sessions and refined sample assignments:

```sh
python -u scripts/aggregate_stem_bank_26_sessions.py \
  -i "$INVENTORY_DIR/stem_bank_26_metadata.sqlite" \
  -o "$INVENTORY_DIR/stem_bank_26_sessions_by_acq_date_sample.sqlite" \
  --session-rule acquisition_date_sample \
  --gzip-copy

python -u scripts/refine_session_samples.py \
  --metadata-db "$INVENTORY_DIR/stem_bank_26_metadata.sqlite" \
  --sessions-db "$INVENTORY_DIR/stem_bank_26_sessions_by_acq_date_sample.sqlite"
```

Build trajectories:

Magnification uses normalized HyperSpy metadata when available, falling back to
the same signal's original DM `Microscope Info.Indicated Magnification` value.
This populates image and SI magnifications, action records, and magnification
changes. Rebuild existing trajectory databases to populate previously missing values.

```sh
python -u scripts/extract_session_trajectories.py \
  --metadata-db "$INVENTORY_DIR/stem_bank_26_metadata.sqlite" \
  --sessions-db "$INVENTORY_DIR/stem_bank_26_sessions_by_acq_date_sample.sqlite" \
  -o "$INVENTORY_DIR/stem_bank_26_trajectories_by_acq_date_sample.sqlite" \
  --gzip-copy
```

Export plotting and paper-summary artifacts:

```sh
python -u scripts/export_metric_csvs.py \
  --metadata-db "$INVENTORY_DIR/stem_bank_26_metadata.sqlite" \
  --sessions-db "$INVENTORY_DIR/stem_bank_26_sessions_by_acq_date_sample.sqlite" \
  --trajectories-db "$INVENTORY_DIR/stem_bank_26_trajectories_by_acq_date_sample.sqlite" \
  -o "$INVENTORY_DIR/metric_csvs"

python -u scripts/build_histogram_datasets.py \
  --metadata-db "$INVENTORY_DIR/stem_bank_26_metadata.sqlite" \
  --sessions-db "$INVENTORY_DIR/stem_bank_26_sessions_by_acq_date_sample.sqlite" \
  --trajectories-db "$INVENTORY_DIR/stem_bank_26_trajectories_by_acq_date_sample.sqlite" \
  -o "$INVENTORY_DIR/histogram_datasets"

python -u scripts/export_paper_data_breakdown.py \
  --metadata-db "$INVENTORY_DIR/stem_bank_26_metadata.sqlite" \
  --sessions-db "$INVENTORY_DIR/stem_bank_26_sessions_by_acq_date_sample.sqlite" \
  --trajectories-db "$INVENTORY_DIR/stem_bank_26_trajectories_by_acq_date_sample.sqlite" \
  -o "$INVENTORY_DIR/paper_data_breakdown.json"
```

## Outputs

Primary SQLite outputs:

- `stem_bank_26_metadata.sqlite`: file, signal, axis, and flattened metadata tables.
- `stem_bank_26_sessions_by_acq_date_sample.sqlite`: date/sample session aggregates and refined sample tables.
- `stem_bank_26_trajectories_by_acq_date_sample.sqlite`: image sequences, transitions, SI events, and mixed action sequences.

Derived outputs:

- `metric_csvs/`: row-level metric CSVs for flexible plotting.
- `histogram_datasets/`: pre-binned histogram CSVs, JSON summary, and histogram SQLite database.
- `paper_data_breakdown.json`: summary counts for manuscript text and tables.

## Session And Sample Rules

The default public workflow defines a session as the pair:

```text
earliest non-empty signal acquisition date / inferred sample name
```

Loaded DigitalMicrograph files with signals but no acquisition date are retained in
`derived_products` but are not used to create sessions.

Refined sample assignment is date-scoped. Different acquisition dates are always distinct sample
sessions. On the same date, sample names that differ only by condition/modifier words can be merged,
and generic names can be reassigned from path/filename hints or nearest same-date sample timing when
the evidence is unambiguous.

## Notebook Figure

`notebooks/random_adf_stem_image_grid.ipynb` creates an 8 row by 5 column random ADF/HAADF STEM image grid.
Set `STEM_BANK_26_ARCHIVE_ROOT` before running it if the archive is not located at `STEM-Bank-26 Archive Sampler`
relative to the notebook:

```sh
export STEM_BANK_26_ARCHIVE_ROOT="/path/to/STEM-Bank-26/Data"
```

The generated `figures/` directory is ignored because figures can be regenerated.

## Example Session

`examples/cupd50_core_she_2022_02_14/` documents a small one-session rebuild selected by the image
title `800 H2_0902`. The raw files are expected under the Git-ignored `sample data/` directory; generated
`_archive_inventory/` outputs are ignored, and the example README records the rebuild commands and
expected counts.

## Optional Keyword Search

`scripts/search_archive_keywords.py` searches archive paths, session fields, signal fields, and flattened
metadata values for configured keyword groups.

Search terms are local configuration and should not be committed:

```sh
cp keyword_search.env.example keyword_search.env
python scripts/search_archive_keywords.py --keywords-env keyword_search.env
```

Additional terms can be supplied without editing the env file:

```sh
python scripts/search_archive_keywords.py --keyword group_name="search term"
```

## Appendix Files

- `docs/APPENDIX_IMAGE_METADATA_FIELDS.txt`: current `image_sequence` field list with one example image record.
- `docs/sample_session_determination_block_diagram.tex`: TikZ block diagram of the sample/session determination process.

## Notes

The extractor stores compact normalized metadata and a small allowlist of original DigitalMicrograph
tags. It does not store full original metadata JSON, which is too large for the intended
ML/discovery workflow.
