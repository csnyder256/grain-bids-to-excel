"""File lifecycle: atomic writes, collision-safe names, archive, open/reveal.
Never overwrites an existing workbook."""
from __future__ import annotations

import logging
import os
import shutil
import string
from pathlib import Path

from openpyxl import Workbook

from .naming import safe_filename

log = logging.getLogger("bidboard.report.files")


def render_filename(pattern: str, tokens: dict[str, str]) -> str:
    """Fill a filename pattern; unknown tokens already rejected at settings
    save time, but be defensive."""
    class _Safe(dict):
        def __missing__(self, key):  # pragma: no cover
            return ""
    out = string.Formatter().vformat(pattern, (), _Safe(tokens))
    return safe_filename(out)


def unique_path(folder: Path, filename: str) -> Path:
    """Never overwrite: append ' (2)', ' (3)' … if the name is taken."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    n = 2
    while True:
        candidate = folder / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def safe_write(wb: Workbook, target: Path) -> Path:
    """Atomic save via temp + replace. On a locked destination (open in
    Excel), fall back to a ' (2)' name rather than failing."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    wb.save(tmp)
    try:
        os.replace(tmp, target)
        return target
    except PermissionError:
        alt = unique_path(target.parent, f"{target.stem} (2){target.suffix}")
        os.replace(tmp, alt)
        log.info("target locked, wrote %s instead", alt.name)
        return alt


def archive_old(folder: Path, keep_latest: int, now_month: str) -> list[str]:
    """Move workbooks beyond the newest keep_latest into Archive/YYYY-MM/.
    Files open in Excel are skipped with a note. Returns warnings."""
    warnings: list[str] = []
    xlsx = sorted(
        [p for p in folder.glob("*.xlsx") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in xlsx[keep_latest:]:
        dest_dir = folder / "Archive" / now_month
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(old), str(dest_dir / old.name))
        except (PermissionError, OSError):
            warnings.append(
                f"We left {old.name} where it is because it's open in Excel - "
                "it'll be tidied next time."
            )
    return warnings
