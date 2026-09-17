"""Zip the hand-off package without staging a second copy of the media.

    docker compose run --rm backend python pack_delivery.py \
        --src /exports/delivery --media /media --out /exports/sberpodbor-выгрузка.zip

The media alone is 8 GB. Copying it into a staging directory and then compressing would
touch ~24 GB of disk for a 9 GB result, so files are added to the archive straight from
where they already live.

Photos and PDFs are already compressed — re-deflating them costs minutes of CPU for a
fraction of a percent, so they go in stored. CSV and XML are text and do compress, so those
get deflate.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

STORE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".zip", ".docx", ".rtf"}


def human(n: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.1f} {unit}" if unit != "Б" else f"{n} Б"
        n /= 1024
    return f"{n:.1f} ГБ"


def add(zf: zipfile.ZipFile, path: Path, arcname: str) -> int:
    method = (
        zipfile.ZIP_STORED
        if path.suffix.lower() in STORE_SUFFIXES
        else zipfile.ZIP_DEFLATED
    )
    zf.write(path, arcname, compress_type=method)
    return path.stat().st_size


def build(src: Path, media: Path | None, out: Path) -> int:
    if not src.exists():
        print(f"Нет каталога {src} — сначала запустите build_delivery.py", file=sys.stderr)
        return 1

    started = datetime.now(UTC)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")

    files = 0
    raw = 0
    # allowZip64: the archive is far past the 4 GB / 65535-entry limits of classic zip.
    with zipfile.ZipFile(tmp, "w", allowZip64=True) as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                raw += add(zf, path, str(path.relative_to(src)).replace("\\", "/"))
                files += 1
        print(f"  данные: {files} файлов", flush=True)

        if media and media.exists():
            for sub in ("photos", "resumes"):
                root = media / sub
                if not root.exists():
                    continue
                for path in root.rglob("*"):
                    if not path.is_file() or path.suffix == ".part":
                        continue
                    arc = f"media/{sub}/{path.relative_to(root)}".replace("\\", "/")
                    raw += add(zf, path, arc)
                    files += 1
                    if files % 20000 == 0:
                        print(f"  упаковано {files:,}...".replace(",", " "), flush=True)

    tmp.replace(out)  # a partial archive never appears under the final name
    size = out.stat().st_size
    mins = (datetime.now(UTC) - started).total_seconds() / 60
    print(f"\nГотово за {mins:.1f} мин")
    print(f"  файлов в архиве : {files:,}".replace(",", " "))
    print(f"  исходный объём  : {human(raw)}")
    print(f"  размер архива   : {human(size)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Zip the delivery package")
    ap.add_argument("--src", default="/exports/delivery")
    ap.add_argument("--media", default="/media")
    ap.add_argument("--out", default="/exports/sberpodbor-delivery.zip")
    ap.add_argument("--no-media", action="store_true")
    args = ap.parse_args()
    return build(Path(args.src), None if args.no_media else Path(args.media), Path(args.out))


if __name__ == "__main__":
    sys.exit(main())
