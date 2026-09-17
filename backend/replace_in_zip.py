"""Replace one file inside an existing archive without rebuilding it from source.

    docker compose run --rm backend python replace_in_zip.py \
        --zip /exports/x.zip --entry csv/media.csv --from /exports/delivery/csv/media.csv

Rebuilding the 8.4 GB package from scratch takes 43 minutes to fix a 13 MB index. Copying
entry-by-entry is bound by disk rather than CPU: the 176k photos and PDFs are stored
uncompressed, so they pass straight through, and only the text entries are re-deflated.

The new archive is written beside the old one and swapped in at the end, so an interrupted
run cannot leave a half-written package under the real name.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path


def replace(zip_path: Path, entry: str, source: Path) -> int:
    if not zip_path.exists():
        print(f"Нет архива {zip_path}", file=sys.stderr)
        return 1
    if not source.exists():
        print(f"Нет файла {source}", file=sys.stderr)
        return 1

    started = datetime.now(UTC)
    tmp = zip_path.with_suffix(".rebuild.zip")
    replaced = copied = 0

    with zipfile.ZipFile(zip_path) as src, zipfile.ZipFile(tmp, "w", allowZip64=True) as dst:
        if entry not in src.namelist():
            print(f"В архиве нет записи {entry}", file=sys.stderr)
            return 1

        for info in src.infolist():
            if info.filename == entry:
                dst.write(source, entry, compress_type=zipfile.ZIP_DEFLATED)
                replaced += 1
                continue
            # Preserve each entry's original compression so stored media stays a plain copy.
            # force_zip64: streaming a member gives zipfile no size up front, so it refuses
            # anything that might cross the 4 GB ZIP64 threshold unless told to allow it.
            with src.open(info) as fh_in, dst.open(_clone(info), "w", force_zip64=True) as fh_out:
                shutil.copyfileobj(fh_in, fh_out, 1024 * 1024)
            copied += 1
            if copied % 25000 == 0:
                print(f"  перенесено {copied:,}...".replace(",", " "), flush=True)

    backup = zip_path.with_suffix(".old.zip")
    zip_path.replace(backup)
    tmp.replace(zip_path)
    backup.unlink()

    mins = (datetime.now(UTC) - started).total_seconds() / 60
    print(f"\nГотово за {mins:.1f} мин: заменено {replaced}, перенесено {copied:,}".replace(",", " "))
    print(f"  размер: {zip_path.stat().st_size / 1073741824:.2f} ГБ")
    return 0


def _clone(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    out = zipfile.ZipInfo(info.filename, info.date_time)
    out.compress_type = info.compress_type
    out.external_attr = info.external_attr
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Replace a single entry inside a zip")
    ap.add_argument("--zip", required=True)
    ap.add_argument("--entry", required=True)
    ap.add_argument("--from", dest="source", required=True)
    args = ap.parse_args()
    return replace(Path(args.zip), args.entry, Path(args.source))


if __name__ == "__main__":
    sys.exit(main())
