# Project Notes

## Purpose

This repository contains tooling for building a compact, queryable metadata archive from the
STEM-Bank-26 DigitalMicrograph dataset. The intended public artifact is source code plus documentation;
raw microscope files and generated databases remain outside Git.

## Environment

Local development uses the TEM Python environment:

Use a Python environment with the dependencies from `requirements.txt`.

The extractor depends on RosettaSciIO for DigitalMicrograph metadata parsing. It avoids full
HyperSpy signal construction during extraction because processed workspace files can trigger very
large metadata deep copies.

## Current Workflow

Full archive rebuild (creates a new dated output directory and keeps existing inventory products):

```bash
PYTHON="/path/to/python-with-rsciio" \
STEM_BANK_26_ARCHIVE_ROOT="/path/to/STEM-Bank-26/Data" \
bash scripts/rebuild_full_dataset.sh
```

The legacy folder-rule session and trajectory databases can still be regenerated with
`--session-rule folder` when path-based grouping is needed for comparison.

## Public Release Surface

Tracked scripts:

- `scripts/extract_stem_bank_26_metadata.py`: builds the compact raw metadata SQLite database.
- `scripts/aggregate_stem_bank_26_sessions.py`: groups files/signals into sessions.
- `scripts/refine_session_samples.py`: creates date-scoped refined sample assignments.
- `scripts/extract_session_trajectories.py`: creates serial image/action trajectories and SI event tables.
- `scripts/export_metric_csvs.py`: exports row-level metrics for plotting.
- `scripts/build_histogram_datasets.py`: exports binned histogram datasets and a histogram SQLite file.
- `scripts/export_paper_data_breakdown.py`: exports paper-ready dataset summary statistics as JSON.
- `scripts/search_archive_keywords.py`: generic keyword search utility driven by a local env file.

Removed from the public release surface:

- ad hoc archive exploration script
- focused element-audit script
- one-off ADF size analysis script
- one-off spectral coverage analysis script

Those analyses are now covered by the database, trajectory, metric CSV, and histogram outputs.

## Keyword Search

Search terms are local configuration and should not be committed. Copy
`keyword_search.env.example` to `keyword_search.env` and add env-style lines:

```bash
KEYWORD_GROUP_GROUP_A="term one,term two"
KEYWORD_GROUP_GROUP_B="another term"
```

Then run:

```bash
python scripts/search_archive_keywords.py --keywords-env keyword_search.env
```

You can also add terms at runtime:

```bash
python scripts/search_archive_keywords.py --keyword group_a="term one"
```

The script writes:

- `keyword_hits.csv`
- `session_keyword_summary.csv`
- `keyword_summary.csv`
- `manifest.json`

## Latest Rebuilt Counts

Full metadata database:

```text
files        95,247
signals      79,932
axes         160,265
metadata_kv  5,917,046
load_errors  109
```

Extractor run metadata:

```text
dm_files_loaded                   76,832
dm_load_errors                    109
dm_timeout_seconds                300.0
file_count_discovered             95,247
original_metadata_json            dropped
original_metadata_kv_mode         allowlist
original_metadata_kv_key_count    65
```

Refined sessions and trajectories:

```text
sessions                  1,549
session_files             65,065
derived_products          11,767
refined_sample_sessions   1,499
refined_sample_files      65,065
image_sequence            45,066
image_transitions         41,875
trajectory_summary        3,191
si_events                 7,152
si_event_assets           18,688
adf_si_raster_images      5,994
```

SI event modality breakdown:

```text
EDS        2,183
EDS+EELS   2,729
EELS       2,240
```

Metric CSV row counts:

```text
image_magnification.csv                  45,066
trajectory_length_transitions.csv         3,191
trajectory_length_time.csv                3,191
images_per_session.csv                    1,499
si_per_session.csv                        1,499
sessions_per_sample.csv                   1,499
element_presence_by_session.csv           2,484
si_size_pixels.csv                        7,152
eels_target_energy_loss.csv               7,698
eels_dispersion_ev_per_channel.csv        7,698
```

## Data Handling

Generated inventory products are ignored by Git:

- SQLite databases and compressed copies
- CSV/TSV/JSON outputs
- local keyword env files
- raw microscope archive directories

The main metadata database intentionally stores compact normalized metadata plus an allowlist of
original DigitalMicrograph tags. Full original metadata JSON is not stored because it is large and
not needed for the current ML/discovery workflows.
