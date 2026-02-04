"""CLI tests for store_optimizer_demo.py."""

import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_SCRIPT = os.path.join(PROJECT_ROOT, "store_optimizer_demo.py")
FAST_PARAMS = "max_time_in_seconds:0.5"
VENV_PYTHON = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")


def run_demo(*args: str) -> subprocess.CompletedProcess:
    """Run store_optimizer_demo.py with given args."""
    python = VENV_PYTHON if os.path.isfile(VENV_PYTHON) else sys.executable
    cmd = [python, DEMO_SCRIPT] + list(args)
    return subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


class TestDemoHelp(unittest.TestCase):
    def test_help(self):
        # store_optimizer_demo.py --help
        r = run_demo("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--year", r.stdout)
        self.assertIn("--demand", r.stdout)


class TestDemoDefaults(unittest.TestCase):
    def test_defaults(self):
        # store_optimizer_demo.py --params max_time_in_seconds:0.5
        r = run_demo("--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)
        self.assertTrue("Store:" in r.stdout or "store" in r.stdout.lower())
        self.assertTrue("OPTIMAL" in r.stdout or "FEASIBLE" in r.stdout)


class TestDemoTimeAndRoster(unittest.TestCase):
    def test_year_month(self):
        # store_optimizer_demo.py --year 2024 --month 6 --params max_time_in_seconds:0.5
        r = run_demo("--year", "2024", "--month", "6", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_store_ids(self):
        # store_optimizer_demo.py --store_ids store_A,store_B --params max_time_in_seconds:0.5
        r = run_demo("--store_ids", "store_A,store_B", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)
        self.assertIn("store_A", r.stdout)
        self.assertIn("store_B", r.stdout)

    def test_roster(self):
        # store_optimizer_demo.py --current_employees 12 --min_employees 10 --max_employees 15 --params max_time_in_seconds:0.5
        r = run_demo(
            "--current_employees", "12",
            "--min_employees", "10",
            "--max_employees", "15",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)

    def test_roster_men_women(self):
        # store_optimizer_demo.py --men 7 --women 8 with per-day demand and work/rest constraints
        r = run_demo(
            "--men", "7",
            "--women", "8",
            "--direct_base_M_weekday", "5,6,5,5,6",
            "--direct_base_A_weekday", "4,5,4,4,5",
            "--direct_base_M_weekend", "4,3",
            "--direct_base_A_weekend", "3,3",
            "--max_shifts_per_week", "6",
            "--min_days_off_per_week", "1",
            "--direct_deterministic",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("employee", r.stdout)

    def test_seed(self):
        # store_optimizer_demo.py --seed 42 --params max_time_in_seconds:0.5
        r = run_demo("--seed", "42", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)


class TestDemoDemandSource(unittest.TestCase):
    def test_demand_direct(self):
        # store_optimizer_demo.py --demand direct --params max_time_in_seconds:0.5
        r = run_demo("--demand", "direct", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_demand_estimation_ratio(self):
        # store_optimizer_demo.py --demand estimation --rule ratio --params max_time_in_seconds:0.5
        r = run_demo("--demand", "estimation", "--rule", "ratio", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_demand_estimation_tiers(self):
        # store_optimizer_demo.py --demand estimation --rule tiers --tiers 50,1,100,2,inf,4 --params max_time_in_seconds:0.5
        r = run_demo(
            "--demand", "estimation",
            "--rule", "tiers",
            "--tiers", "50,1,100,2,inf,4",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)

    def test_demand_estimation_formula(self):
        # store_optimizer_demo.py --demand estimation --rule formula --base_employees 2 --params max_time_in_seconds:0.5
        r = run_demo(
            "--demand", "estimation",
            "--rule", "formula",
            "--base_employees", "2",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)


class TestDemoEstimationParams(unittest.TestCase):
    def test_estimation_params(self):
        # store_optimizer_demo.py --demand estimation --customers_per_employee_M 25 --customers_per_employee_A 35 --min_employees_estimation 2 --params max_time_in_seconds:0.5
        r = run_demo(
            "--demand", "estimation",
            "--customers_per_employee_M", "25",
            "--customers_per_employee_A", "35",
            "--min_employees_estimation", "2",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)


class TestDemoDirectDemand(unittest.TestCase):
    def test_direct_bases(self):
        # store_optimizer_demo.py --direct_base_M_weekday 6 --direct_base_A_weekday 5 --direct_base_M_weekend 5 --direct_base_A_weekend 4 --params max_time_in_seconds:0.5
        r = run_demo(
            "--direct_base_M_weekday", "6",
            "--direct_base_A_weekday", "5",
            "--direct_base_M_weekend", "5",
            "--direct_base_A_weekend", "4",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)

    def test_direct_deterministic(self):
        # store_optimizer_demo.py --direct_deterministic --params max_time_in_seconds:0.5
        r = run_demo("--direct_deterministic", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_demand_profile_weekend_heavy(self):
        # store_optimizer_demo.py --demand_profile weekend_heavy --params max_time_in_seconds:0.5
        r = run_demo("--demand_profile", "weekend_heavy", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_demand_profile_weekday_heavy(self):
        # store_optimizer_demo.py --demand_profile weekday_heavy --params max_time_in_seconds:0.5
        r = run_demo("--demand_profile", "weekday_heavy", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_store_profiles(self):
        # store_optimizer_demo.py --store_ids A,B --store_profiles A:weekend_heavy,B:weekday_heavy --params max_time_in_seconds:0.5
        r = run_demo(
            "--store_ids", "A,B",
            "--store_profiles", "A:weekend_heavy,B:weekday_heavy",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)

    def test_direct_base_per_day(self):
        # Per-day demand: weekday 5,6,5,5,6 (Mon-Fri M), weekend 4,3 (Sat,Sun M)
        r = run_demo(
            "--direct_base_M_weekday", "5,6,5,5,6",
            "--direct_base_A_weekday", "4,4,4,4,4",
            "--direct_base_M_weekend", "4,3",
            "--direct_base_A_weekend", "3,3",
            "--direct_deterministic",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)


class TestDemoOptimizerConstraints(unittest.TestCase):
    def test_excess_penalties(self):
        # store_optimizer_demo.py --excess_penalty_M 2 --excess_penalty_A 2 --params max_time_in_seconds:0.5
        r = run_demo("--excess_penalty_M", "2", "--excess_penalty_A", "2", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_max_shifts_per_week(self):
        # store_optimizer_demo.py --max_shifts_per_week 5 --params max_time_in_seconds:0.5
        r = run_demo("--max_shifts_per_week", "5", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_min_days_off_per_week(self):
        # store_optimizer_demo.py --min_days_off_per_week 1 --params max_time_in_seconds:0.5
        r = run_demo("--min_days_off_per_week", "1", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_max_consecutive_work_days(self):
        # store_optimizer_demo.py --max_consecutive_work_days 5 --params max_time_in_seconds:0.5
        r = run_demo("--max_consecutive_work_days", "5", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_sequence_constraints(self):
        # store_optimizer_demo.py --sequence_constraints M:1,1,0,3,4,5 --params max_time_in_seconds:0.5
        r = run_demo(
            "--sequence_constraints", "M:1,1,0,3,4,5",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)

    def test_weekly_sum_constraints(self):
        # store_optimizer_demo.py --weekly_sum_constraints O:1,2,7,2,3,4 --params max_time_in_seconds:0.5
        r = run_demo(
            "--weekly_sum_constraints", "O:1,2,7,2,3,4",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)

    def test_max_weekend_work_shifts_women(self):
        # store_optimizer_demo.py --max_weekend_work_shifts_women 2 --params max_time_in_seconds:0.5
        r = run_demo("--max_weekend_work_shifts_women", "2", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_min_sunday_off(self):
        # store_optimizer_demo.py --min_sunday_off_per_month 1 --min_sunday_off_women 2 --params max_time_in_seconds:0.5
        r = run_demo(
            "--min_sunday_off_per_month", "1",
            "--min_sunday_off_women", "2",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)

    def test_spread_sunday_shifts(self):
        # store_optimizer_demo.py --spread_sunday_shifts_penalty 10 --params max_time_in_seconds:0.5
        r = run_demo("--spread_sunday_shifts_penalty", "10", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_target_women_ratio(self):
        # store_optimizer_demo.py --target_min_women_ratio 0.35 --target_max_women_ratio 0.65 --params max_time_in_seconds:0.5
        r = run_demo(
            "--target_min_women_ratio", "0.35",
            "--target_max_women_ratio", "0.65",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)


class TestDemoSolver(unittest.TestCase):
    def test_params(self):
        # store_optimizer_demo.py --params max_time_in_seconds:0.5
        r = run_demo("--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_output_proto(self):
        # store_optimizer_demo.py --output_proto <tempfile> --params max_time_in_seconds:0.5
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pbtxt") as f:
            proto_path = f.name
        try:
            r = run_demo("--output_proto", proto_path, "--params", FAST_PARAMS)
            self.assertEqual(r.returncode, 0)
            self.assertTrue(os.path.exists(proto_path))
            self.assertGreater(os.path.getsize(proto_path), 0)
        finally:
            if os.path.exists(proto_path):
                os.unlink(proto_path)


class TestDemoErrorCases(unittest.TestCase):
    def test_invalid_demand(self):
        # store_optimizer_demo.py --demand invalid (expects non-zero exit)
        r = run_demo("--demand", "invalid")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue("invalid" in r.stderr.lower() or "error" in r.stderr.lower() or "argument" in r.stderr.lower())

    def test_invalid_rule(self):
        # store_optimizer_demo.py --demand estimation --rule invalid (expects non-zero exit)
        r = run_demo("--demand", "estimation", "--rule", "invalid")
        self.assertNotEqual(r.returncode, 0)

    def test_invalid_tiers_odd_count(self):
        # store_optimizer_demo.py --demand estimation --rule tiers --tiers 50,1,100 (expects non-zero exit)
        r = run_demo("--demand", "estimation", "--rule", "tiers", "--tiers", "50,1,100")
        self.assertNotEqual(r.returncode, 0)
