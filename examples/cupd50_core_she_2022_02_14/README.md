# Example Session: CuPd50 Core She, 2022-02-14

This example rebuilds the database workflow from a small subset of raw DigitalMicrograph files.
It was selected by locating the image titled `800 H2_0902`.

Located image:

```text
session_key: 2022-02-14/CuPd50 core she
sample: CuPd50 core she
path: CuPd50 core she/STEM (62)/800 H2_0902.dm4
title: 800 H2_0902
acquisition_timestamp: 2022-02-14T21:45:40
shape: 1024x1024
```

The request described this as a PtCu nanoparticle session, but the archive metadata and path label
for the matching image are `CuPd50 core she`.

## Example Layout

The rebuild uses 29 raw `.dm4` files from this session:

```text
sample data/
  CuPd50 core she/
    SI data (133)/
    SI data (134)/
    SI data (135)/
    STEM (62)/
    STEM (63)/

examples/cupd50_core_she_2022_02_14/_archive_inventory/
  generated databases and CSV/JSON outputs; ignored by Git
```

The raw sample files under `sample data/` are local and Git-ignored. Copy the 29-file subset there
before running the commands below. Generated database and plotting outputs are also ignored.

## Rebuild Commands

Run the full database workflow from scratch on the example raw subset:

```sh
EXAMPLE_DIR="examples/cupd50_core_she_2022_02_14"
SAMPLE_DATA_DIR="sample data"

mkdir -p "$EXAMPLE_DIR/_archive_inventory"

python -u scripts/extract_stem_bank_26_metadata.py "$SAMPLE_DATA_DIR" \
  --progress-every 10 \
  --dm-timeout 300 \
  --gzip-copy \
  -o "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_metadata.sqlite"

python -u scripts/aggregate_stem_bank_26_sessions.py \
  -i "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_metadata.sqlite" \
  -o "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_sessions_by_acq_date_sample.sqlite" \
  --session-rule acquisition_date_sample \
  --gzip-copy

python -u scripts/refine_session_samples.py \
  --metadata-db "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_metadata.sqlite" \
  --sessions-db "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_sessions_by_acq_date_sample.sqlite"

python -u scripts/extract_session_trajectories.py \
  --metadata-db "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_metadata.sqlite" \
  --sessions-db "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_sessions_by_acq_date_sample.sqlite" \
  -o "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_trajectories_by_acq_date_sample.sqlite" \
  --gzip-copy

python -u scripts/export_metric_csvs.py \
  --metadata-db "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_metadata.sqlite" \
  --sessions-db "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_sessions_by_acq_date_sample.sqlite" \
  --trajectories-db "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_trajectories_by_acq_date_sample.sqlite" \
  -o "$EXAMPLE_DIR/_archive_inventory/metric_csvs"

python -u scripts/build_histogram_datasets.py \
  --metadata-db "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_metadata.sqlite" \
  --sessions-db "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_sessions_by_acq_date_sample.sqlite" \
  --trajectories-db "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_trajectories_by_acq_date_sample.sqlite" \
  -o "$EXAMPLE_DIR/_archive_inventory/histogram_datasets"

python -u scripts/export_paper_data_breakdown.py \
  --metadata-db "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_metadata.sqlite" \
  --sessions-db "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_sessions_by_acq_date_sample.sqlite" \
  --trajectories-db "$EXAMPLE_DIR/_archive_inventory/stem_bank_26_trajectories_by_acq_date_sample.sqlite" \
  -o "$EXAMPLE_DIR/_archive_inventory/paper_data_breakdown.json"
```

## Rebuilt Example Counts

```text
raw files: 29
metadata signals: 29
sessions: 1
refined sample sessions: 1
image_sequence rows: 21
SI survey images in image_sequence: 3
paper-style standalone STEM images: 18
SI events: 3
EDS primary SI signals: 3
EELS primary SI signals: 2
trajectory segments: 1
image transitions: 20
ADF SI-raster child images: 3
```
