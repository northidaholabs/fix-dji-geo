# SPDX-FileCopyrightText: 2026 North Idaho Labs, LLC
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lat/lon shift tests with made-up numbers. No photos and no flight data."""

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fix_dji_geo


# degrees(1 / 6378137) = 8.983152841e-6, which is 0.000008983 at 9 decimals.
ONE_METRE_DEG_9 = "0.000008983"
RADIUS_M = 6378137.0


class ShiftTests(unittest.TestCase):
    def test_constants_match_the_measured_method(self):
        self.assertEqual(fix_dji_geo.WGS84_A_M, RADIUS_M)
        self.assertEqual(fix_dji_geo.DEFAULT_LAG_S, 0.1)

    def test_one_metre_north_at_a_fake_latitude(self):
        lat, lon = fix_dji_geo.shift_lat_lon(10.0, 20.0, 1.0, 0.0, 1.0)
        self.assertEqual(f"{lat:.9f}", "10.000008983")
        self.assertEqual(f"{lon:.9f}", "20.000000000")

    def test_one_metre_east_on_the_equator(self):
        # Fake position 0, 0. On the equator, 1 m east is the same angle as 1 m north.
        lat, lon = fix_dji_geo.shift_lat_lon(0.0, 0.0, 0.0, 1.0, 1.0)
        self.assertEqual(lat, 0.0)
        self.assertEqual(f"{lon:.9f}", ONE_METRE_DEG_9)

    def test_negative_velocity_shifts_south_and_west(self):
        lat, lon = fix_dji_geo.shift_lat_lon(10.0, 20.0, -1.0, -1.0, 1.0)
        self.assertEqual(f"{lat:.9f}", "9.999991017")
        self.assertLess(lon, 20.0)

    def test_zero_lag_leaves_the_position(self):
        lat, lon = fix_dji_geo.shift_lat_lon(10.0, 20.0, 50.0, -40.0, 0.0)
        self.assertEqual((lat, lon), (10.0, 20.0))

    def test_east_shift_uses_latitude_after_the_north_shift(self):
        lat0, lon0 = 10.0, 20.0
        north_m, east_m = 1000.0, 1000.0
        lat, lon = fix_dji_geo.shift_lat_lon(lat0, lon0, north_m, east_m, 1.0)
        expected_lat = lat0 + math.degrees(north_m / RADIUS_M)
        expected_lon = lon0 + math.degrees(
            east_m / (RADIUS_M * math.cos(math.radians(expected_lat)))
        )
        wrong_lon = lon0 + math.degrees(
            east_m / (RADIUS_M * math.cos(math.radians(lat0)))
        )
        self.assertAlmostEqual(lat, expected_lat, places=12)
        self.assertAlmostEqual(lon, expected_lon, places=12)
        # Using the original latitude for the east step is a different longitude.
        self.assertGreater(abs(lon - wrong_lon), 1e-7)

    def test_altitude_is_pad_plus_relative(self):
        self.assertEqual(fix_dji_geo.photo_altitude(800.0, 15.5), 815.5)

    def test_geo_line_is_name_lon_lat_alt(self):
        line, warning = fix_dji_geo.fix_photo(
            {
                "SourceFile": "C:\\flight\\FRAME_0001.JPG",
                "GPSLatitude": 10.0,
                "GPSLongitude": 20.0,
                "RelativeAltitude": 15.5,
                "FlightXSpeed": 10.0,
                "FlightYSpeed": 0.0,
            },
            800.0,
            0.1,
        )
        self.assertIsNone(warning)
        # 10 m/s north * 0.1 s = 1 m north. Longitude stays 20. Altitude is 815.5.
        self.assertEqual(line, "FRAME_0001.JPG 20.000000000 10.000008983 815.500")

    def test_absolute_altitude_is_ignored(self):
        base = {
            "SourceFile": "FRAME_0001.JPG",
            "GPSLatitude": 10.0,
            "GPSLongitude": 20.0,
            "RelativeAltitude": 15.5,
            "FlightXSpeed": 0.0,
            "FlightYSpeed": 0.0,
        }
        with_absolute = dict(base, AbsoluteAltitude=12345.0)
        line, warning = fix_dji_geo.fix_photo(base, 800.0, 0.1)
        other, other_warning = fix_dji_geo.fix_photo(with_absolute, 800.0, 0.1)
        self.assertIsNone(warning)
        self.assertIsNone(other_warning)
        self.assertEqual(line, other)
        self.assertTrue(line.endswith(" 815.500"))

    def test_missing_tags_name_the_file_and_do_not_raise(self):
        line, warning = fix_dji_geo.fix_photo(
            {"SourceFile": "FRAME_0002.JPG", "GPSLatitude": 10.0, "GPSLongitude": 20.0},
            800.0,
            0.1,
        )
        self.assertIsNone(line)
        self.assertIn("FRAME_0002.JPG", warning)
        self.assertIn("RelativeAltitude", warning)
        self.assertIn("FlightXSpeed", warning)
        self.assertNotIn("KeyError", warning)

    def test_header_and_trailing_newline(self):
        text = fix_dji_geo.render_geo_txt(
            ["FRAME_0001.JPG 20.000000000 10.000000000 800.000"]
        )
        self.assertEqual(
            text,
            "EPSG:4326\nFRAME_0001.JPG 20.000000000 10.000000000 800.000\n",
        )

    def test_exiftool_command_is_read_only_and_skips_absolute_altitude(self):
        command = fix_dji_geo.exiftool_command("exiftool", Path("photos"))
        self.assertEqual(command[0], "exiftool")
        self.assertIn("-j", command)
        self.assertIn("-n", command)
        for tag in (
            "-GPSLatitude",
            "-GPSLongitude",
            "-RelativeAltitude",
            "-FlightXSpeed",
            "-FlightYSpeed",
        ):
            self.assertIn(tag, command)
        self.assertNotIn("-AbsoluteAltitude", command)
        self.assertNotIn("-overwrite_original", command)
        self.assertEqual(command[-1], "photos")

    def test_duplicate_filename_is_skipped(self):
        def record(folder, relative):
            return {
                "SourceFile": f"{folder}/FRAME_0001.JPG",
                "GPSLatitude": 10.0,
                "GPSLongitude": 20.0,
                "RelativeAltitude": relative,
                "FlightXSpeed": 0.0,
                "FlightYSpeed": 0.0,
            }

        lines, warnings = fix_dji_geo.build_lines(
            [record("a", 1.0), record("b", 2.0)],
            800.0,
            0.1,
        )
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(warnings), 1)
        self.assertIn("duplicate", warnings[0])
        self.assertIn("FRAME_0001.JPG", warnings[0])


if __name__ == "__main__":
    unittest.main()
