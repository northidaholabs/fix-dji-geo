# fix-dji-geo

Write an OpenDroneMap / WebODM `geo.txt` from DJI mapping photos.

On one Mavic 3 Pro mapping flight the altitudes stored in the photos put
the map tens of meters low, and opposite passes flown at about 10 m/s sat
about 2 m apart. A GPS-to-shutter lag of about 0.1 s is the size of that
along-track gap. This tool writes a `geo.txt` that OpenDroneMap can use
in place of the coordinates embedded in the files.

For each photo:

1. `AbsoluteAltitude` is ignored.
2. Height is a takeoff ground height you supply, plus the photo's `RelativeAltitude`.
3. The horizontal position moves by `FlightXSpeed` (north, m/s) and `FlightYSpeed` (east, m/s) times a lag. The default lag is 0.1 s.

The photos are not modified.

Independent interoperability project. It contains no DJI software, and it
is **not affiliated with or endorsed by DJI**. Mavic is a trademark of its
owner.

## Install

Python 3.9 or later, and [ExifTool](https://exiftool.org/). ExifTool is a
separate program with its own license. This repository does not include it.
There is nothing to install from pip.

```bash
git clone https://github.com/northidaholabs/fix-dji-geo.git
cd fix-dji-geo
```

Debian or Ubuntu:

```bash
sudo apt-get install libimage-exiftool-perl
```

macOS, with Homebrew:

```bash
brew install exiftool
```

On Windows, install ExifTool from <https://exiftool.org/> so that a new
terminal can run it.

Check both:

```bash
python3 --version
exiftool -ver
```

## Usage

```bash
python3 fix_dji_geo.py photos/ 800.0 --output geo.txt
```

`800.0` is a placeholder. Pass the real height of the takeoff pad, in
meters. That number is added to each photo's `RelativeAltitude`. It is the
pad, not a ground elevation under the first photo.

```bash
python3 fix_dji_geo.py photos/ 800.0 --lag 0.1 > geo.txt
python3 fix_dji_geo.py --help
```

Stdout is only the `geo.txt` text, so the redirect is safe. Counts and
warnings go to stderr. `--output` overwrites an existing file. `-` means
stdout. Subfolders are not scanned.

| Argument | Required | Meaning |
|---|---|---|
| `FOLDER` | yes | Directory of `.JPG` and `.jpg` photos. |
| `TAKEOFF_GROUND_M` | yes | Takeoff pad height in meters, added to `RelativeAltitude`. |
| `--lag SECONDS` | no | GPS-to-shutter lag in seconds. Default `0.1`. |
| `-o`, `--output PATH` | no | Output file. Default is stdout. |

Exit status `0` means one or more photos were written. Skipped files are
named on stderr and the run still exits `0` when something was written.
Exit status `1` means nothing was written: the folder is missing, `exiftool`
is missing or failed, or no photo had the required tags. Exit status `2`
is a bad command line.

A photo missing `GPSLatitude`, `GPSLongitude`, `RelativeAltitude`,
`FlightXSpeed`, or `FlightYSpeed` is skipped with the file name and the
missing tags. The run does not stop on that photo. A file name with
whitespace is skipped, because `geo.txt` splits fields on whitespace. Two
records with the same file name keep the first and skip the rest.

If `exiftool` is not on `PATH`, the message says so and points at
<https://exiftool.org/>.

## Output

```text
EPSG:4326
FRAME_0001.JPG 20.000000000 10.000008983 815.500
```

The numbers above are made up, so the arithmetic can be checked:

| Input | Value |
|---|---|
| latitude, longitude | 10, 20 |
| `RelativeAltitude` | 15.5 m |
| `FlightXSpeed`, `FlightYSpeed` | 10 m/s north, 0 m/s east |
| lag | 0.1 s |
| takeoff ground height | 800 m |

One meter north on a sphere of radius 6378137 m is
`degrees(1 / 6378137)`, which prints as `0.000008983` degrees at 9 decimal
places. Longitude is unchanged. Altitude is 815.500 m.

Longitude is first, then latitude, then altitude. File names are the base
name only (`os.path.basename`), so a Windows path and a POSIX path both
become `FRAME_0001.JPG`.

## WebODM and OpenDroneMap

OpenDroneMap reads image positions from a file named `geo.txt` (lowercase)
in the project image folder. WebODM picks up the same file when you upload
it with the images. A different file name can be passed with `--geo`. The
format is documented at <https://docs.opendronemap.org/geo/>.

The first line is the coordinate reference system. For `EPSG:4326`, X is
longitude and Y is latitude. Z is the altitude from this tool, in meters,
in the same vertical datum as the takeoff height you passed. This tool does
not convert between ellipsoidal height and orthometric height.

The names have to match the files you upload, including `.JPG` versus
`.jpg`. If the log says `Updated 0 image positions`, the names did not
match. Upload the original photos plus this `geo.txt`. The photos are
unchanged. OpenDroneMap uses `geo.txt` instead of the GPS in the files.

## Method

```text
lat = lat + degrees(FlightXSpeed * lag / 6378137)
lon = lon + degrees(FlightYSpeed * lag / (6378137 * cos(lat)))
altitude = takeoff_ground_m + RelativeAltitude
```

`cos(lat)` uses the latitude after the north shift. 6378137 is the WGS84
equatorial radius in meters, used as a sphere. The stored coordinate moves
in the direction of flight by velocity times lag. That is the sign that
brought the passes together on the measured flight.

`FlightXSpeed` increased to the north and `FlightYSpeed` increased to the
east on that flight, in meters per second. A different camera that stores
those tags on other axes would be shifted the wrong way. No other camera
has been checked.

`AbsoluteAltitude` is not requested from ExifTool and is not written.

## What was measured

One Mavic 3 Pro mapping flight. 267 photos, about 9.8 m/s, electronic
shutter. No ground control points were set.

`AbsoluteAltitude` put the map tens of meters low, so it is not used.
Opposite passes sat about 2 m apart. At about 10 m/s, 0.1 s is about 1 m
along track on each pass, and the two directions disagree by about 2 m.
The lag fitted from those photos was 0.0995 s. The default of 0.1 s is
that fit, rounded.

After the shift, the passes lined up with each other. "Lined up" means the
photos agreed with each other. It does not mean the block was checked
against ground control. USGS 3DEP was a coarse check on the vertical. The
first processing pass can still be off.

Pass the real height of the takeoff pad. The first photo is not the pad.
On this flight, using a USGS elevation under the first photo instead of the
takeoff pad left the map about 10 m high.

## What was not measured

No other DJI model, and no other brand, was part of that fit. This
repository does not ship shutter lags or lever arms for other cameras.
A flight with ground control is the check for whether 0.1 s holds on
another camera. Reports from those flights are welcome as issues.

There is no ground-height lookup. The tool will not treat the first photo
as the takeoff.

## Tests

The tests use made-up latitudes, longitudes, speeds, and a fake `exiftool`
on `PATH`. No image files are in the repository.

```bash
python3 -m py_compile fix_dji_geo.py
python3 -m unittest discover -s tests -v
python3 fix_dji_geo.py --help
```

Coverage needs `coverage` 7.10 or later (`python3 -m pip install "coverage>=7.10"`).
`.coveragerc` also counts the copies of the script that `tests/test_cli.py`
starts in a subprocess. The report fails below 95%.

```bash
python3 -m coverage run -m unittest discover -s tests
python3 -m coverage combine
python3 -m coverage report
```

| Test | What it checks |
|---|---|
| `tests/test_shift.py` | 1 m north and 1 m east, the east step uses the updated latitude, sign, zero lag, pad plus `RelativeAltitude`, longitude-then-latitude order, missing tags, `AbsoluteAltitude` ignored |
| `tests/test_cli.py` | `--help`, usage errors, a missing folder, a missing `exiftool`, and `geo.txt` from fake ExifTool JSON |
| `tests/test_edges.py` | number parsing, skip reasons, duplicates, ExifTool failures with `subprocess.run` replaced, output path errors, a broken pipe |

A missing folder, or a `PATH` with no `exiftool`, exits `1` and names the problem on stderr. Stdout stays empty, and the photo folder is not written to.

## License

Licensed under Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

Earlier releases remain under MIT.
