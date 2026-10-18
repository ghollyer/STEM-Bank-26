#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
archive_root="${STEM_BANK_26_ARCHIVE_ROOT:-/Volumes/Hollyer USB/STEM-Bank-26/Data}"
run_id="${STEM_BANK_26_REBUILD_ID:-$(date +%Y%m%d)}"
inventory_dir="${STEM_BANK_26_OUTPUT_DIR:-$archive_root/_archive_inventory/rebuild_$run_id}"
python_bin="${PYTHON:-python3}"

if [[ ! -d "$archive_root" ]]; then
  echo "Archive root does not exist: $archive_root" >&2
  exit 2
fi
if [[ -e "$inventory_dir" ]]; then
  echo "Output directory already exists; choose a new STEM_BANK_26_REBUILD_ID or STEM_BANK_26_OUTPUT_DIR: $inventory_dir" >&2
  exit 2
fi

mkdir -p "$inventory_dir"
metadata_db="$inventory_dir/stem_bank_26_metadata.sqlite"
sessions_db="$inventory_dir/stem_bank_26_sessions_by_acq_date_sample.sqlite"
trajectories_db="$inventory_dir/stem_bank_26_trajectories_by_acq_date_sample.sqlite"

echo "Archive: $archive_root"
echo "Output:  $inventory_dir"
echo "Python:  $python_bin"

"$python_bin" -u "$repo_root/scripts/extract_stem_bank_26_metadata.py" "$archive_root" \
  --progress-every 10000 --dm-timeout 300 --gzip-copy -o "$metadata_db"

"$python_bin" -u "$repo_root/scripts/aggregate_stem_bank_26_sessions.py" \
  -i "$metadata_db" -o "$sessions_db" \
  --session-rule acquisition_date_sample --gzip-copy

"$python_bin" -u "$repo_root/scripts/refine_session_samples.py" \
  --metadata-db "$metadata_db" --sessions-db "$sessions_db"

# Refinement adds tables to the sessions database, so refresh the compressed copy.
gzip -c -9 "$sessions_db" > "$sessions_db.gz.tmp"
mv "$sessions_db.gz.tmp" "$sessions_db.gz"

"$python_bin" -u "$repo_root/scripts/extract_session_trajectories.py" \
  --metadata-db "$metadata_db" --sessions-db "$sessions_db" \
  -o "$trajectories_db" --gzip-copy

"$python_bin" -u "$repo_root/scripts/export_metric_csvs.py" \
  --metadata-db "$metadata_db" --sessions-db "$sessions_db" \
  --trajectories-db "$trajectories_db" -o "$inventory_dir/metric_csvs"

"$python_bin" -u "$repo_root/scripts/build_histogram_datasets.py" \
  --metadata-db "$metadata_db" --sessions-db "$sessions_db" \
  --trajectories-db "$trajectories_db" -o "$inventory_dir/histogram_datasets"

"$python_bin" -u "$repo_root/scripts/export_paper_data_breakdown.py" \
  --metadata-db "$metadata_db" --sessions-db "$sessions_db" \
  --trajectories-db "$trajectories_db" -o "$inventory_dir/paper_data_breakdown.json"

echo "Full rebuild complete: $inventory_dir"
