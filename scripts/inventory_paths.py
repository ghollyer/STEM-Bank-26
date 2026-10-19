"""Shared defaults for statistics and histogram outputs."""

import os
from pathlib import Path

DATABASES = (
    "stem_bank_26_metadata.sqlite",
    "stem_bank_26_sessions_by_acq_date_sample.sqlite",
    "stem_bank_26_trajectories_by_acq_date_sample.sqlite",
)


def default_inventory_dir() -> Path:
    """Find the newest complete inventory for the configured archive root."""
    configured = os.environ.get("STEM_BANK_26_INVENTORY_DIR")
    if configured:
        return Path(configured).expanduser()

    archive_input = os.environ.get("STEM_BANK_26_ARCHIVE_ROOT")
    if archive_input:
        archive_path = Path(archive_input).expanduser()
        inventory_parent = archive_path if (archive_path / "data").is_dir() else archive_path.parent
    else:
        usb_root = Path("/Volumes/Hollyer USB/STEM-Bank-26")
        inventory_parent = usb_root if usb_root.is_dir() else Path(__file__).resolve().parents[1]

    inventory_root = inventory_parent / "_archive_inventory"

    def is_complete(path: Path) -> bool:
        return all((path / name).is_file() for name in DATABASES)

    rebuilds = sorted(
        (path for path in inventory_root.glob("rebuild_*") if is_complete(path)),
        key=lambda path: path.name,
    )
    if rebuilds:
        return rebuilds[-1]
    return inventory_root
