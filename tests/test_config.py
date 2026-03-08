import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from student_rooms.models.config import AcademicYearConfig, Config, load_config


class TestConfigLoading(unittest.TestCase):
    def test_yaml_parse_error_returns_defaults(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".yaml", delete=False) as tmp:
            tmp.write("target:\n  country: [\n")
            tmp_path = tmp.name

        config, warnings = load_config(tmp_path)
        self.assertTrue(any("YAML parse error" in w for w in warnings))
        self.assertIsInstance(config, Config)


class TestAcademicYearDerivation(unittest.TestCase):
    def test_academic_year_jan_to_jul(self):
        cfg = AcademicYearConfig()
        with patch("student_rooms.models.config.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 27)
            self.assertEqual(cfg.academic_year_str(), "2025-26")

    def test_academic_year_aug_to_dec(self):
        cfg = AcademicYearConfig()
        with patch("student_rooms.models.config.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 9, 1)
            self.assertEqual(cfg.academic_year_str(), "2026-27")


if __name__ == "__main__":
    unittest.main()
