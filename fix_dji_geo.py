#!/usr/bin/env python3
"""Write an OpenDroneMap geo.txt from DJI mapping photos.

Height is the takeoff ground height you pass plus RelativeAltitude.
AbsoluteAltitude is not read. Each photo is shifted by FlightXSpeed
(north, m/s) and FlightYSpeed (east, m/s) times a GPS-to-shutter lag.
The default lag is 0.1 s, from one Mavic 3 Pro mapping flight.

Photos are not modified. ExifTool is required and is not bundled.

https://github.com/northidaholabs/fix-dji-geo
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

# WGS84 equatorial radius, meters, used as a sphere. This is the measured
# method, not a local projection and not a camera lever arm.
WGS84_A_M = 6378137.0
DEFAULT_LAG_S = 0.1
HEADER = "EPSG:4326"
REQUIRED_TAGS = (
    "GPSLatitude",
    "GPSLongitude",
    "RelativeAltitude",
    "FlightXSpeed",
    "FlightYSpeed",
)

DESCRIPTION = """\
Write an OpenDroneMap / WebODM geo.txt from DJI mapping photos.

Height is TAKEOFF_GROUND_M plus each photo's RelativeAltitude.
AbsoluteAltitude is ignored. The horizontal position is shifted by
FlightXSpeed (north, m/s) and FlightYSpeed (east, m/s) times --lag
(default 0.1 seconds). Photos are not modified.

The first output line is EPSG:4326. Each following line is:
filename longitude latitude altitude
"""

EPILOG = """\
examples:
  python3 fix_dji_geo.py photos/ 800.0 --output geo.txt
  python3 fix_dji_geo.py photos/ 800.0 --lag 0.1 > geo.txt

800.0 is a placeholder. Pass the height of the takeoff pad in meters.
The pad is not a ground elevation looked up under the first photo.

Not affiliated with or endorsed by DJI. Mavic is a trademark of its owner.
"""


def shift_lat_lon(
    lat: float,
    lon: float,
    north: float,
    east: float,
    lag: float,
) -> tuple[float, float]:
    """Shift lat/lon by north and east velocity (m/s) times lag (s).

    North is FlightXSpeed and east is FlightYSpeed, as on the measured
    Mavic 3 Pro flight. The longitude step uses the latitude after the
    north step.
    """
    lat = lat + math.degrees(north * lag / WGS84_A_M)
    lon = lon + math.degrees(east * lag / (WGS84_A_M * math.cos(math.radians(lat))))
    return lat, lon


def photo_altitude(takeoff_ground_m: float, relative_altitude_m: float) -> float:
    """Pad height plus the photo's height above takeoff."""
    return takeoff_ground_m + relative_altitude_m


def photo_name(source: str) -> str:
    """File name only, with no directory.

    ExifTool reports host paths. Replacing backslashes first keeps a
    Windows path to a single name when this runs on POSIX.
    """
    return os.path.basename(source.replace("\\", "/"))


def format_geo_line(filename: str, lon: float, lat: float, alt: float) -> str:
    """One geo.txt data line: name, longitude, latitude, altitude."""
    return f"{filename} {lon:.9f} {lat:.9f} {alt:.3f}"


def render_geo_txt(lines: list[str]) -> str:
    """EPSG header plus data lines, with a trailing newline."""
    body = "\n".join(lines)
    if body:
        body += "\n"
    return HEADER + "\n" + body


def parse_number(value: object) -> float:
    """Parse one finite ExifTool number. Reject bools, lists, and blanks."""
    if isinstance(value, bool) or value is None:
        raise ValueError("not a single number")
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except OverflowError:
            raise ValueError("not a finite number") from None
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError("not a single number")
        number = float(stripped)
    else:
        raise ValueError("not a single number")
    if not math.isfinite(number):
        raise ValueError("not a finite number")
    return number


def fix_photo(
    record: dict,
    takeoff_ground_m: float,
    lag_s: float,
) -> tuple[str | None, str | None]:
    """Return (geo.txt line, None) or (None, skip reason).

    A missing tag is a skip, not a KeyError. AbsoluteAltitude is ignored
    when it is present.
    """
    source = record.get("SourceFile", "")
    if not isinstance(source, str):
        source = str(source)
    name = photo_name(source)
    if not name:
        return None, "skipped (unknown file): the exiftool record has no SourceFile"

    if any(ch.isspace() for ch in name):
        return None, f"skipped {name}: filename contains whitespace"

    if not name.lower().endswith(".jpg"):
        return None, f"skipped {name}: not a .jpg or .JPG file"

    missing = [
        tag
        for tag in REQUIRED_TAGS
        if tag not in record or record[tag] is None or record[tag] == ""
    ]
    if missing:
        if len(missing) == len(REQUIRED_TAGS) and record.get("Error"):
            return None, f"skipped {name}: {record['Error']}"
        listed = ", ".join(missing)
        return None, f"skipped {name}: missing {listed}"

    parsed: dict[str, float] = {}
    for tag in REQUIRED_TAGS:
        try:
            parsed[tag] = parse_number(record[tag])
        except ValueError:
            return None, f"skipped {name}: {tag} is not a number ({record[tag]!r})"

    try:
        lat, lon = shift_lat_lon(
            parsed["GPSLatitude"],
            parsed["GPSLongitude"],
            parsed["FlightXSpeed"],
            parsed["FlightYSpeed"],
            lag_s,
        )
    except ZeroDivisionError:
        return None, f"skipped {name}: east correction is undefined at this latitude"

    alt = photo_altitude(takeoff_ground_m, parsed["RelativeAltitude"])
    return format_geo_line(name, lon, lat, alt), None


def record_sort_key(record: dict) -> str:
    source = record.get("SourceFile", "")
    if not isinstance(source, str):
        source = str(source)
    return photo_name(source)


def build_lines(
    records: list,
    takeoff_ground_m: float,
    lag_s: float,
) -> tuple[list[str], list[str]]:
    """Turn ExifTool records into geo.txt lines and skip messages.

    Records are ordered by file name. A repeated file name is skipped
    after the first one, because geo.txt would then carry two positions
    for one image.
    """
    warnings: list[str] = []
    usable: list[dict] = []
    for record in records:
        if not isinstance(record, dict):
            warnings.append("skipped (unknown file): exiftool record was not an object")
            continue
        usable.append(record)

    lines: list[str] = []
    seen: set[str] = set()
    for record in sorted(usable, key=record_sort_key):
        line, warning = fix_photo(record, takeoff_ground_m, lag_s)
        if warning or line is None:
            warnings.append(warning or "skipped (unknown file)")
            continue
        filename = line.split(" ", 1)[0]
        if filename in seen:
            warnings.append(f"skipped {filename}: duplicate filename")
            continue
        seen.add(filename)
        lines.append(line)
    return lines, warnings


def exiftool_command(exiftool: str, folder: Path) -> list[str]:
    """The read-only ExifTool invocation. AbsoluteAltitude is not requested."""
    return [
        exiftool,
        "-j",
        "-n",
        "-GPSLatitude",
        "-GPSLongitude",
        "-RelativeAltitude",
        "-FlightXSpeed",
        "-FlightYSpeed",
        "-ext",
        "JPG",
        "-ext",
        "jpg",
        str(folder),
    ]


def finite_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number: {text}") from exc
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"not a finite number: {text}")
    return value


EXIFTOOL_MISSING = (
    "exiftool was not found on PATH.\n"
    "Install ExifTool from https://exiftool.org/ and confirm that `exiftool -ver` works.\n"
    "This tool does not include ExifTool. ExifTool has its own license."
)


def die(message: str, code: int = 1) -> NoReturn:
    for line in message.splitlines() or [""]:
        print(f"fix_dji_geo: {line}", file=sys.stderr)
    raise SystemExit(code)


def looks_like_no_files(stderr: str) -> bool:
    low = stderr.lower()
    return "0 image files" in low or "no matching files" in low


def stderr_is_notable(stderr: str, returncode: int) -> bool:
    """True when ExifTool stderr is a warning or a failed run, not the scan count."""
    text = stderr.strip()
    if not text:
        return False
    if returncode != 0:
        return True
    low = text.lower()
    return "warning" in low or "error" in low


def load_records(folder: Path) -> list:
    """Run ExifTool on one directory and return its JSON records."""
    exe = shutil.which("exiftool")
    if exe is None:
        die(EXIFTOOL_MISSING)
    command = exiftool_command(exe, folder)
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        die(EXIFTOOL_MISSING)
    except OSError as exc:
        die(f"could not run exiftool: {exc.strerror or exc}")

    stdout = proc.stdout.lstrip("\ufeff").strip()
    stderr = proc.stderr.strip()
    if not stdout:
        if proc.returncode == 0 or looks_like_no_files(stderr):
            die(f"no .jpg or .JPG photos found in {folder}")
        detail = f"\n{stderr}" if stderr else ""
        die(f"exiftool exited with status {proc.returncode} and wrote no JSON.{detail}")

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        detail = f"\n{stderr}" if stderr else ""
        die(f"exiftool did not return JSON ({exc}).{detail}")

    # ExifTool always prints a scan count on stderr. That is not a failure.
    # Surface stderr when the process failed, or when it reported a warning.
    if stderr_is_notable(stderr, proc.returncode):
        print(f"fix_dji_geo: exiftool said: {stderr}", file=sys.stderr)

    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    die("exiftool JSON was not a list of files")


def emit(text: str, output: Path | None) -> None:
    if output is None:
        try:
            sys.stdout.write(text)
        except BrokenPipeError:
            try:
                sys.stdout.close()
            except OSError:
                pass
            raise SystemExit(0) from None
        return
    try:
        with output.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError as exc:
        die(f"could not write {output}: {exc.strerror or exc}")


def check_output_path(output: Path | None) -> None:
    if output is None:
        return
    if output.exists() and output.is_dir():
        die(f"output path is a directory: {output}")
    parent = output.parent
    if str(parent) not in ("", ".") and not parent.exists():
        die(f"output directory does not exist: {parent}")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fix_dji_geo.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=DESCRIPTION,
        epilog=EPILOG,
    )
    parser.add_argument(
        "folder",
        metavar="FOLDER",
        help="directory of DJI .JPG or .jpg photos (subfolders are not read)",
    )
    parser.add_argument(
        "takeoff_ground_m",
        metavar="TAKEOFF_GROUND_M",
        type=finite_float,
        help=(
            "height of the takeoff pad in meters, added to each RelativeAltitude. "
            "This is the pad, not a ground elevation under the first photo."
        ),
    )
    parser.add_argument(
        "--lag",
        type=finite_float,
        default=DEFAULT_LAG_S,
        metavar="SECONDS",
        help=(
            "GPS-to-shutter lag in seconds (default: %(default)s). "
            "FlightXSpeed (north, m/s) and FlightYSpeed (east, m/s) are multiplied by this."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help="write geo.txt to PATH instead of stdout. Use - for stdout. Overwrites PATH.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    folder = Path(args.folder)
    if not folder.exists():
        die(f"folder does not exist: {folder}")
    if not folder.is_dir():
        die(f"not a directory: {folder}")

    output: Path | None
    if args.output is None or args.output == "-":
        output = None
    else:
        output = Path(args.output)
    check_output_path(output)

    records = load_records(folder)
    if not records:
        die(f"no .jpg or .JPG photos found in {folder}")

    lines, warnings = build_lines(records, args.takeoff_ground_m, args.lag)
    for warning in warnings:
        print(f"fix_dji_geo: {warning}", file=sys.stderr)
    if not lines:
        die(f"no photos could be written from {folder}")

    emit(render_geo_txt(lines), output)
    dest = "stdout" if output is None else str(output)
    print(f"fix_dji_geo: wrote {len(lines)} photos to {dest}", file=sys.stderr)
    if warnings:
        print(f"fix_dji_geo: skipped {len(warnings)} photos", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except OSError:
            pass
        sys.exit(0)
