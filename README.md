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

## Archive Layout

### Download the Full Dataset

The full raw dataset is hosted in the [STEM-Bank-26 Hugging Face bucket](https://huggingface.co/buckets/ghollyer/STEM-Bank-26). Install the Hugging Face CLI if `hf` is not already available:

```sh
curl -LsSf https://hf.co/cli/install.sh | bash
```

Check the bucket size and file count before downloading:

```sh
hf buckets info ghollyer/STEM-Bank-26
```

The bucket contains separate `_archive_inventory/`, `data/`, and `sample_data/` directories. Sync the entire bucket into the local `STEM-Bank-26/` root to preserve that layout. The full rebuild script detects and uses the `data/` subdirectory automatically:

```sh
STEM_BANK_26_ROOT="/path/to/STEM-Bank-26"
mkdir -p "$STEM_BANK_26_ROOT"
hf buckets sync hf://buckets/ghollyer/STEM-Bank-26 "$STEM_BANK_26_ROOT"
```

The bucket also includes prebuilt SQLite databases under `_archive_inventory/`, so you can query
the metadata, sessions, and trajectories without rebuilding them from the raw DigitalMicrograph
files. After syncing the bucket, set:

```sh
INVENTORY_DIR="$STEM_BANK_26_ROOT/_archive_inventory"
```

For example, count metadata signals by category:

```sh
python - "$INVENTORY_DIR/stem_bank_26_metadata.sqlite" <<'PY'
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
for category, count in db.execute(
    "SELECT category, COUNT(*) FROM signals GROUP BY category ORDER BY COUNT(*) DESC"
):
    print(category or "(uncategorized)", count)
db.close()
PY
```

Or list the ten longest trajectories by image count:

```sh
python - "$INVENTORY_DIR/stem_bank_26_trajectories_by_acq_date_sample.sqlite" <<'PY'
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
for row in db.execute("""
    SELECT session_key, image_count, transition_count, total_elapsed_seconds
    FROM session_trajectory_summary
    ORDER BY image_count DESC
    LIMIT 10
"""):
    print(*row, sep="\t")
db.close()
PY
```

To get the archive file paths for a session, use its `session_key` (the paths are relative to
the `data/` directory):

```sh
SESSION_KEY="2022-07-07/K2 low dose imaging"
python - "$INVENTORY_DIR/stem_bank_26_sessions_by_acq_date_sample.sqlite" "$SESSION_KEY" <<'PY'
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
for (path,) in db.execute("""
    SELECT sf.path
    FROM sessions AS s
    JOIN session_files AS sf USING (session_id)
    WHERE s.session_key = ?
    ORDER BY sf.path
""", (sys.argv[2],)):
    print(path)
db.close()
PY
```

To query a dated rebuild instead of the bucket’s prebuilt databases, set `INVENTORY_DIR` to
`"$STEM_BANK_26_ROOT/_archive_inventory/rebuild_YYYYMMDD"`.

From the code repository root, rebuild by passing the bucket root. The script automatically uses its `data/` subdirectory:

```sh
PYTHON=".venv/bin/python" \
STEM_BANK_26_ARCHIVE_ROOT="$STEM_BANK_26_ROOT" \
bash scripts/rebuild_full_dataset.sh
```

If Hugging Face reports that access is denied, sign in with an account that has access:

```sh
hf auth login
```

Then rerun the sync command. The sync command does not delete local files by default, so it can be rerun to fetch missing or changed files.

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
ARCHIVE_ROOT="/path/to/STEM-Bank-26/data"
INVENTORY_DIR="$(dirname "$ARCHIVE_ROOT")/_archive_inventory"
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

These three exporters default to the newest complete `rebuild_YYYYMMDD/` under the standalone
`_archive_inventory/` associated with `STEM_BANK_26_ARCHIVE_ROOT`. Their CSV, histogram, and JSON
outputs are written alongside those databases. Set `STEM_BANK_26_INVENTORY_DIR` to select a
particular inventory directory; explicit command-line paths still take precedence.

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

## Full Dataset Rebuild

To rebuild all SQLite databases and derived summaries from the full archive in one run:

```sh
PYTHON="/path/to/python-with-rosettasciio" \
STEM_BANK_26_ARCHIVE_ROOT="/path/to/STEM-Bank-26" \
bash scripts/rebuild_full_dataset.sh
```

Set `STEM_BANK_26_ARCHIVE_ROOT` to the local bucket root. The script automatically processes its
`data/` subdirectory when present; a path directly to the raw data folder also works. The script
writes to a standalone `_archive_inventory/rebuild_YYYYMMDD/` directory beside `data/`. It stops if that
output directory already exists; set `STEM_BANK_26_REBUILD_ID` to choose a different dated folder,
or set `STEM_BANK_26_OUTPUT_DIR` to use a specific location. Existing inventory files are left as
they are. The rebuild includes the metadata, session, trajectory, and histogram SQLite databases,
plus compressed copies and paper metric exports.
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
