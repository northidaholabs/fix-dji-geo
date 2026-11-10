"""Edge cases run in-process: number parsing, skips, ExifTool failures, output.

ExifTool is never run here. subprocess.run and shutil.which are replaced, so
these tests need no PATH setup. Made-up numbers only. No photos.
"""

import contextlib
import io
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "fix_dji_geo.py"
sys.path.insert(0, str(ROOT))

import fix_dji_geo


def good_record(name="FRAME_0001.JPG", **overrides):
    record = {
        "SourceFile": name,
        "GPSLatitude": 10.0,
        "GPSLongitude": 20.0,
        "RelativeAltitude": 15.5,
        "FlightXSpeed": 0.0,
        "FlightYSpeed": 0.0,
    }
    record.update(overrides)
    return record


def completed(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(["exiftool"], code, stdout, stderr)


class Captured:
    """Run a callable with stdout and stderr captured. Record SystemExit codes."""

    def __init__(self, func, *args):
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.code = None
        self.result = None
        with contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr):
            try:
                self.result = func(*args)
            except SystemExit as exc:
                self.code = exc.code


class RaisingStream:
    """A stream whose write and close fail, like a closed pipe."""

    def __init__(self, write_exc, close_exc=None):
        self.write_exc = write_exc
        self.close_exc = close_exc
        self.closed_called = False

    def write(self, text):
        raise self.write_exc

    def flush(self):
        pass

    def close(self):
        self.closed_called = True
        if self.close_exc is not None:
            raise self.close_exc


class ParseNumberTests(unittest.TestCase):
    def test_accepts_int_float_and_padded_string(self):
        self.assertEqual(fix_dji_geo.parse_number(3), 3.0)
        self.assertEqual(fix_dji_geo.parse_number(2.5), 2.5)
        self.assertEqual(fix_dji_geo.parse_number("  -1.25 "), -1.25)

    def test_rejects_bool_none_blank_list_and_text(self):
        for value in (True, False, None, "", "   ", [1.0], {"a": 1}, "abc"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                fix_dji_geo.parse_number(value)

    def test_rejects_non_finite(self):
        for value in (float("inf"), float("nan"), "nan", "-inf", "1e400"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                fix_dji_geo.parse_number(value)

    def test_huge_int_is_a_value_error(self):
        with self.assertRaises(ValueError):
            fix_dji_geo.parse_number(10**400)


class FixPhotoTests(unittest.TestCase):
    def test_string_numbers_are_read(self):
        line, warning = fix_dji_geo.fix_photo(
            good_record(RelativeAltitude="15.5", GPSLatitude="10"), 800.0, 0.1
        )
        self.assertIsNone(warning)
        self.assertEqual(line, "FRAME_0001.JPG 20.000000000 10.000000000 815.500")

    def test_lowercase_extension_is_accepted(self):
        line, warning = fix_dji_geo.fix_photo(good_record("frame_0001.jpg"), 0.0, 0.1)
        self.assertIsNone(warning)
        self.assertTrue(line.startswith("frame_0001.jpg "))

    def test_whitespace_in_name_is_skipped(self):
        line, warning = fix_dji_geo.fix_photo(good_record("dir/FRAME 1.JPG"), 0.0, 0.1)
        self.assertIsNone(line)
        self.assertIn("FRAME 1.JPG", warning)
        self.assertIn("whitespace", warning)

    def test_non_jpg_is_skipped(self):
        line, warning = fix_dji_geo.fix_photo(good_record("FRAME_0001.DNG"), 0.0, 0.1)
        self.assertIsNone(line)
        self.assertIn("not a .jpg", warning)

    def test_non_string_source_file_is_named_as_text(self):
        line, warning = fix_dji_geo.fix_photo(good_record(1234), 0.0, 0.1)
        self.assertIsNone(line)
        self.assertIn("skipped 1234", warning)

    def test_missing_source_file_is_still_skipped(self):
        record = good_record()
        del record["SourceFile"]
        line, warning = fix_dji_geo.fix_photo(record, 0.0, 0.1)
        self.assertIsNone(line)
        self.assertIn("(unknown file)", warning)
        self.assertIn("no SourceFile", warning)
        self.assertNotIn("whitespace", warning)

    def test_blank_and_null_tags_count_as_missing(self):
        line, warning = fix_dji_geo.fix_photo(
            good_record(FlightXSpeed=None, FlightYSpeed=""), 0.0, 0.1
        )
        self.assertIsNone(line)
        self.assertIn("missing FlightXSpeed, FlightYSpeed", warning)

    def test_all_tags_missing_with_error_reports_the_error(self):
        line, warning = fix_dji_geo.fix_photo(
            {"SourceFile": "FRAME_0001.JPG", "Error": "File format error"}, 0.0, 0.1
        )
        self.assertIsNone(line)
        self.assertEqual(warning, "skipped FRAME_0001.JPG: File format error")

    def test_tag_that_is_not_a_number_is_named(self):
        line, warning = fix_dji_geo.fix_photo(good_record(GPSLatitude="north"), 0.0, 0.1)
        self.assertIsNone(line)
        self.assertIn("GPSLatitude is not a number", warning)
        self.assertIn("'north'", warning)

    def test_bool_tag_is_not_a_number(self):
        line, warning = fix_dji_geo.fix_photo(good_record(FlightYSpeed=True), 0.0, 0.1)
        self.assertIsNone(line)
        self.assertIn("FlightYSpeed is not a number", warning)

    def test_undefined_east_correction_is_a_skip(self):
        with mock.patch.object(fix_dji_geo, "shift_lat_lon", side_effect=ZeroDivisionError):
            line, warning = fix_dji_geo.fix_photo(good_record(), 0.0, 0.1)
        self.assertIsNone(line)
        self.assertIn("east correction is undefined", warning)


class BuildLinesTests(unittest.TestCase):
    def test_non_object_records_are_skipped(self):
        lines, warnings = fix_dji_geo.build_lines(["oops", 7, good_record()], 0.0, 0.0)
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(warnings), 2)
        for warning in warnings:
            self.assertIn("not an object", warning)

    def test_sorted_by_name_and_duplicates_keep_the_first(self):
        records = [
            good_record("b/FRAME_0002.JPG"),
            good_record("a/FRAME_0001.JPG", RelativeAltitude=1.0),
            good_record("c/FRAME_0001.JPG", RelativeAltitude=2.0),
        ]
        lines, warnings = fix_dji_geo.build_lines(records, 0.0, 0.0)
        self.assertEqual([line.split()[0] for line in lines], ["FRAME_0001.JPG", "FRAME_0002.JPG"])
        self.assertTrue(lines[0].endswith(" 1.000"))
        self.assertEqual(warnings, ["skipped FRAME_0001.JPG: duplicate filename"])

    def test_sort_key_handles_non_string_source(self):
        self.assertEqual(fix_dji_geo.record_sort_key({"SourceFile": 5}), "5")
        self.assertEqual(fix_dji_geo.record_sort_key({}), "")

    def test_render_with_no_lines_is_just_the_header(self):
        self.assertEqual(fix_dji_geo.render_geo_txt([]), "EPSG:4326\n")


class SmallHelperTests(unittest.TestCase):
    def test_finite_float_rejects_infinity(self):
        import argparse

        with self.assertRaises(argparse.ArgumentTypeError) as ctx:
            fix_dji_geo.finite_float("inf")
        self.assertIn("not a finite number", str(ctx.exception))

    def test_looks_like_no_files(self):
        self.assertTrue(fix_dji_geo.looks_like_no_files("    0 image files read"))
        self.assertTrue(fix_dji_geo.looks_like_no_files("Error: No matching files"))
        self.assertFalse(fix_dji_geo.looks_like_no_files("    1 image files read"))

    def test_stderr_is_notable(self):
        self.assertFalse(fix_dji_geo.stderr_is_notable("   ", 1))
        self.assertTrue(fix_dji_geo.stderr_is_notable("1 image files read", 1))
        self.assertFalse(fix_dji_geo.stderr_is_notable("1 image files read", 0))
        self.assertTrue(fix_dji_geo.stderr_is_notable("Warning: odd", 0))
        self.assertTrue(fix_dji_geo.stderr_is_notable("Error: odd", 0))

    def test_exiftool_command_does_not_ask_for_absolute_altitude(self):
        command = fix_dji_geo.exiftool_command("exiftool", Path("photos"))
        self.assertNotIn("-AbsoluteAltitude", command)
        self.assertEqual(command[-1], "photos")

    def test_die_with_empty_message_still_prints_a_prefix(self):
        cap = Captured(fix_dji_geo.die, "", 3)
        self.assertEqual(cap.code, 3)
        self.assertEqual(cap.stderr.getvalue(), "fix_dji_geo: \n")


class LoadRecordsTests(unittest.TestCase):
    folder = Path("photos")

    def load(self, proc=None, side_effect=None):
        with mock.patch.object(fix_dji_geo.shutil, "which", return_value="/fake/exiftool"), \
                mock.patch.object(fix_dji_geo.subprocess, "run", return_value=proc,
                                  side_effect=side_effect) as run:
            cap = Captured(fix_dji_geo.load_records, self.folder)
        if side_effect is None:
            self.assertEqual(run.call_args.args[0][0], "/fake/exiftool")
        return cap

    def test_single_object_becomes_a_list(self):
        cap = self.load(completed(json.dumps(good_record())))
        self.assertIsNone(cap.code)
        self.assertEqual(cap.result, [good_record()])

    def test_byte_order_mark_is_ignored(self):
        cap = self.load(completed("﻿" + json.dumps([good_record()])))
        self.assertEqual(cap.result, [good_record()])

    def test_json_that_is_not_a_list(self):
        cap = self.load(completed("42"))
        self.assertEqual(cap.code, 1)
        self.assertIn("not a list of files", cap.stderr.getvalue())

    def test_no_output_and_success_means_no_photos(self):
        cap = self.load(completed("", "", 0))
        self.assertEqual(cap.code, 1)
        self.assertIn("no .jpg or .JPG photos found in photos", cap.stderr.getvalue())

    def test_no_output_and_zero_files_means_no_photos(self):
        cap = self.load(completed("", "    0 image files read\n", 1))
        self.assertEqual(cap.code, 1)
        self.assertIn("no .jpg or .JPG photos found", cap.stderr.getvalue())

    def test_no_output_and_failure_shows_status_and_stderr(self):
        cap = self.load(completed("", "something broke\n", 2))
        self.assertEqual(cap.code, 1)
        err = cap.stderr.getvalue()
        self.assertIn("exited with status 2 and wrote no JSON", err)
        self.assertIn("fix_dji_geo: something broke", err)

    def test_no_output_and_failure_without_stderr(self):
        cap = self.load(completed("", "", 2))
        self.assertEqual(cap.code, 1)
        self.assertEqual(
            cap.stderr.getvalue(),
            "fix_dji_geo: exiftool exited with status 2 and wrote no JSON.\n",
        )

    def test_bad_json_without_stderr(self):
        cap = self.load(completed("not json", "", 0))
        self.assertEqual(cap.code, 1)
        self.assertIn("did not return JSON", cap.stderr.getvalue())

    def test_exiftool_vanishes_between_which_and_run(self):
        cap = self.load(side_effect=FileNotFoundError(2, "No such file"))
        self.assertEqual(cap.code, 1)
        self.assertIn("exiftool was not found on PATH", cap.stderr.getvalue())

    def test_exiftool_cannot_be_started(self):
        cap = self.load(side_effect=PermissionError(13, "Permission denied"))
        self.assertEqual(cap.code, 1)
        self.assertIn("could not run exiftool: Permission denied", cap.stderr.getvalue())


class OutputTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_output_path_that_is_a_directory(self):
        cap = Captured(fix_dji_geo.check_output_path, self.root)
        self.assertEqual(cap.code, 1)
        self.assertIn("output path is a directory", cap.stderr.getvalue())

    def test_output_parent_that_does_not_exist(self):
        cap = Captured(fix_dji_geo.check_output_path, self.root / "nope" / "geo.txt")
        self.assertEqual(cap.code, 1)
        self.assertIn("output directory does not exist", cap.stderr.getvalue())

    def test_bare_file_name_is_accepted(self):
        cap = Captured(fix_dji_geo.check_output_path, Path("geo.txt"))
        self.assertIsNone(cap.code)

    def test_write_failure_is_reported(self):
        blocker = self.root / "file"
        blocker.write_text("x", encoding="utf-8")
        cap = Captured(fix_dji_geo.emit, "EPSG:4326\n", blocker / "geo.txt")
        self.assertEqual(cap.code, 1)
        self.assertIn("could not write", cap.stderr.getvalue())

    def test_write_uses_unix_newlines(self):
        out = self.root / "geo.txt"
        fix_dji_geo.emit("EPSG:4326\nA.JPG 1 2 3\n", out)
        self.assertEqual(out.read_bytes(), b"EPSG:4326\nA.JPG 1 2 3\n")

    def test_broken_pipe_on_stdout_exits_0(self):
        for close_exc in (None, OSError("already closed")):
            with self.subTest(close_exc=close_exc):
                stream = RaisingStream(BrokenPipeError(), close_exc)
                with mock.patch.object(sys, "stdout", stream), \
                        self.assertRaises(SystemExit) as ctx:
                    fix_dji_geo.emit("EPSG:4326\n", None)
                self.assertEqual(ctx.exception.code, 0)
                self.assertTrue(stream.closed_called)


class MainInProcessTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def main_with(self, records, *extra):
        with mock.patch.object(fix_dji_geo, "load_records", return_value=records):
            return Captured(fix_dji_geo.main, [str(self.folder), "800.0", *extra])

    def test_empty_record_list_exits_1(self):
        cap = self.main_with([])
        self.assertEqual(cap.code, 1)
        self.assertIn("no .jpg or .JPG photos found", cap.stderr.getvalue())
        self.assertEqual(cap.stdout.getvalue(), "")

    def test_writes_to_stdout_by_default(self):
        cap = self.main_with([good_record()])
        self.assertIsNone(cap.code)
        self.assertEqual(cap.result, 0)
        self.assertEqual(
            cap.stdout.getvalue(),
            "EPSG:4326\nFRAME_0001.JPG 20.000000000 10.000000000 815.500\n",
        )
        self.assertIn("wrote 1 photos to stdout", cap.stderr.getvalue())
        self.assertNotIn("skipped", cap.stderr.getvalue())

    def test_lag_must_be_finite(self):
        cap = self.main_with([good_record()], "--lag", "nan")
        self.assertEqual(cap.code, 2)
        self.assertIn("not a finite number", cap.stderr.getvalue())


class ScriptEntryPointTests(unittest.TestCase):
    """The __main__ block turns a broken pipe into exit status 0."""

    def run_script(self, close_exc):
        stdout = RaisingStream(BrokenPipeError(), close_exc)
        stderr = RaisingStream(BrokenPipeError())
        argv = ["fix_dji_geo.py", str(ROOT / "no-such-folder-for-tests"), "800.0"]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(sys, "stdout", stdout), \
                mock.patch.object(sys, "stderr", stderr), \
                self.assertRaises(SystemExit) as ctx:
            runpy.run_path(str(SCRIPT), run_name="__main__")
        return ctx.exception.code, stdout

    def test_broken_pipe_exits_0(self):
        for close_exc in (None, OSError("already closed")):
            with self.subTest(close_exc=close_exc):
                code, stdout = self.run_script(close_exc)
                self.assertEqual(code, 0)
                self.assertTrue(stdout.closed_called)


if __name__ == "__main__":
    unittest.main()
