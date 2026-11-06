"""CLI error paths and a fake ExifTool. No photos and no flight coordinates."""

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "fix_dji_geo.py"


def run(args, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def write_fake_exiftool(bin_dir: Path, payload, code=0, stderr=""):
    path = bin_dir / "exiftool"
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"sys.stderr.write({stderr!r})\n"
        f"json.dump({payload!r}, sys.stdout)\n"
        f"raise SystemExit({int(code)})\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def photos(self):
        folder = self.root / "photos"
        folder.mkdir()
        sentinel = folder / "KEEP.txt"
        sentinel.write_text("keep", encoding="utf-8")
        return folder

    def env_with(self, bin_dir: Path):
        env = os.environ.copy()
        env["PATH"] = str(bin_dir)
        return env

    def test_help_mentions_lag_and_field_order(self):
        proc = run(["--help"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("TAKEOFF_GROUND_M", proc.stdout)
        self.assertIn("0.1", proc.stdout)
        self.assertIn("RelativeAltitude", proc.stdout)
        self.assertIn("longitude latitude altitude", proc.stdout)
        self.assertIn("800.0", proc.stdout)

    def test_usage_error_exits_2(self):
        proc = run([])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", proc.stderr.lower())
        self.assertEqual(proc.stdout, "")

    def test_takeoff_height_must_be_a_number(self):
        folder = self.photos()
        proc = run([str(folder), "nope"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not a number", proc.stderr)
        self.assertEqual((folder / "KEEP.txt").read_text(encoding="utf-8"), "keep")

    def test_missing_folder_names_the_path(self):
        missing = self.root / "no-such-photos"
        proc = run([str(missing), "800.0"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("does not exist", proc.stderr)
        self.assertIn("no-such-photos", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_file_is_not_a_directory(self):
        folder = self.photos()
        note = folder / "KEEP.txt"
        proc = run([str(note), "800.0"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not a directory", proc.stderr)
        self.assertEqual(note.read_text(encoding="utf-8"), "keep")

    def test_missing_exiftool(self):
        folder = self.photos()
        empty_bin = self.root / "empty-bin"
        empty_bin.mkdir()
        proc = run([str(folder), "800.0"], env=self.env_with(empty_bin))
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("exiftool", proc.stderr.lower())
        self.assertIn("PATH", proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(list(folder.iterdir()), [folder / "KEEP.txt"])

    def test_fake_exiftool_writes_shifted_geo_txt(self):
        folder = self.photos()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        write_fake_exiftool(
            bin_dir,
            [
                {
                    "SourceFile": "C:\\flight\\FRAME_0001.JPG",
                    "GPSLatitude": 10.0,
                    "GPSLongitude": 20.0,
                    "RelativeAltitude": 15.5,
                    "FlightXSpeed": 10.0,
                    "FlightYSpeed": 0.0,
                }
            ],
            stderr="    1 directories scanned\n    1 image files read\n",
        )
        out = self.root / "out" / "geo.txt"
        out.parent.mkdir()
        proc = run(
            [str(folder), "800.0", "--output", str(out)],
            env=self.env_with(bin_dir),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(
            out.read_text(encoding="utf-8"),
            "EPSG:4326\nFRAME_0001.JPG 20.000000000 10.000008983 815.500\n",
        )
        self.assertIn("wrote 1 photos", proc.stderr)
        self.assertNotIn("directories scanned", proc.stderr)
        self.assertNotIn("exiftool said", proc.stderr)
        self.assertEqual((folder / "KEEP.txt").read_text(encoding="utf-8"), "keep")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["KEEP.txt"])

    def test_stdout_and_zero_lag(self):
        folder = self.photos()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        write_fake_exiftool(
            bin_dir,
            [
                {
                    "SourceFile": "sets/FRAME_0001.JPG",
                    "GPSLatitude": 10.0,
                    "GPSLongitude": 20.0,
                    "RelativeAltitude": 15.5,
                    "FlightXSpeed": 10.0,
                    "FlightYSpeed": 0.0,
                }
            ],
        )
        proc = run(
            [str(folder), "800.0", "--lag", "0", "--output", "-"],
            env=self.env_with(bin_dir),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "EPSG:4326\nFRAME_0001.JPG 20.000000000 10.000000000 815.500\n",
        )
        self.assertIn("stdout", proc.stderr)

    def test_missing_tag_is_a_warning_and_the_good_photo_is_kept(self):
        folder = self.photos()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        write_fake_exiftool(
            bin_dir,
            [
                {
                    "SourceFile": "FRAME_0002.JPG",
                    "GPSLatitude": 10.0,
                    "GPSLongitude": 20.0,
                },
                {
                    "SourceFile": "FRAME_0001.JPG",
                    "GPSLatitude": 10.0,
                    "GPSLongitude": 20.0,
                    "RelativeAltitude": 15.5,
                    "FlightXSpeed": 0.0,
                    "FlightYSpeed": 0.0,
                },
            ],
        )
        proc = run([str(folder), "800.0"], env=self.env_with(bin_dir))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("FRAME_0002.JPG", proc.stderr)
        self.assertIn("RelativeAltitude", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(
            proc.stdout,
            "EPSG:4326\nFRAME_0001.JPG 20.000000000 10.000000000 815.500\n",
        )
        self.assertIn("skipped 1", proc.stderr)

    def test_nothing_usable_exits_1_and_writes_no_file(self):
        folder = self.photos()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        write_fake_exiftool(
            bin_dir,
            [{"SourceFile": "FRAME_0002.JPG", "Error": "File is empty"}],
        )
        out = self.root / "geo.txt"
        proc = run(
            [str(folder), "800.0", "--output", str(out)],
            env=self.env_with(bin_dir),
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("FRAME_0002.JPG", proc.stderr)
        self.assertFalse(out.exists())
        self.assertEqual(proc.stdout, "")

    def test_bad_exiftool_json_exits_1(self):
        folder = self.photos()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        path = bin_dir / "exiftool"
        path.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            "sys.stderr.write('exiftool: boom\\n')\n"
            "sys.stdout.write('this is not json\\n')\n"
            "raise SystemExit(1)\n",
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        proc = run([str(folder), "800.0"], env=self.env_with(bin_dir))
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("JSON", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_exiftool_warning_with_valid_json_still_writes(self):
        folder = self.photos()
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        write_fake_exiftool(
            bin_dir,
            [
                {
                    "SourceFile": "FRAME_0001.JPG",
                    "GPSLatitude": 10.0,
                    "GPSLongitude": 20.0,
                    "RelativeAltitude": 1.0,
                    "FlightXSpeed": 0.0,
                    "FlightYSpeed": 0.0,
                }
            ],
            code=1,
            stderr="Warning: minor tag issue\n",
        )
        proc = run([str(folder), "800.0"], env=self.env_with(bin_dir))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.startswith("EPSG:4326\n"))
        self.assertIn("minor tag issue", proc.stderr)


if __name__ == "__main__":
    unittest.main()
