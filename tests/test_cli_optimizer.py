"""CLI tests for store_staffing_optimizer.py."""

import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPTIMIZER_SCRIPT = os.path.join(PROJECT_ROOT, "store_staffing_optimizer.py")
FAST_PARAMS = "max_time_in_seconds:0.5"


def run_optimizer(*args: str) -> subprocess.CompletedProcess:
    """Run store_staffing_optimizer.py with given args."""
    cmd = [sys.executable, OPTIMIZER_SCRIPT] + list(args)
    return subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


class TestOptimizerHelp(unittest.TestCase):
    def test_help(self):
        # store_staffing_optimizer.py --help
        r = run_optimizer("--help")
        # absl exits 1 after printing help (does not run main)
        self.assertIn(r.returncode, (0, 1))
        self.assertIn("--year", r.stdout)
        self.assertIn("--current_employees", r.stdout)


class TestOptimizerDefaults(unittest.TestCase):
    def test_defaults(self):
        # store_staffing_optimizer.py --params max_time_in_seconds:0.5
        r = run_optimizer("--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)
        self.assertTrue("OPTIMAL" in r.stdout or "FEASIBLE" in r.stdout)


class TestOptimizerTimeAndRoster(unittest.TestCase):
    def test_year_month(self):
        # store_staffing_optimizer.py --year=2024 --month=3 --params max_time_in_seconds:0.5
        r = run_optimizer("--year=2024", "--month=3", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_roster(self):
        # store_staffing_optimizer.py --current_employees=12 --min_employees=10 --max_employees=14 --params max_time_in_seconds:0.5
        r = run_optimizer(
            "--current_employees=12",
            "--min_employees=10",
            "--max_employees=14",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)


class TestOptimizerDemandBases(unittest.TestCase):
    def test_demand_bases(self):
        # store_staffing_optimizer.py --demand_base_M_weekday=6 --demand_base_A_weekday=5 --demand_base_M_weekend=5 --demand_base_A_weekend=4 --params max_time_in_seconds:0.5
        r = run_optimizer(
            "--demand_base_M_weekday=6",
            "--demand_base_A_weekday=5",
            "--demand_base_M_weekend=5",
            "--demand_base_A_weekend=4",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)


class TestOptimizerSolver(unittest.TestCase):
    def test_params(self):
        # store_staffing_optimizer.py --params max_time_in_seconds:0.5
        r = run_optimizer("--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_output_proto(self):
        # store_staffing_optimizer.py --output_proto <tempfile> --params max_time_in_seconds:0.5
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pbtxt") as f:
            proto_path = f.name
        try:
            r = run_optimizer("--output_proto", proto_path, "--params", FAST_PARAMS)
            self.assertEqual(r.returncode, 0)
            self.assertTrue(os.path.exists(proto_path))
            self.assertGreater(os.path.getsize(proto_path), 0)
        finally:
            if os.path.exists(proto_path):
                os.unlink(proto_path)
