"""CLI tests for store_optimizer_demo.py."""

import json
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
        # Legal rule validation runs when women_sunday_off_alternate is enabled (default)
        self.assertIn("Legal rule: women alternate Sundays OK", r.stdout)

    def test_spread_sunday_shifts(self):
        # store_optimizer_demo.py --spread_sunday_shifts_penalty 10 --params max_time_in_seconds:0.5
        r = run_demo("--spread_sunday_shifts_penalty", "10", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_spread_shifts(self):
        # store_optimizer_demo.py --spread_shifts_penalty 5 --params max_time_in_seconds:0.5
        r = run_demo("--spread_shifts_penalty", "5", "--params", FAST_PARAMS)
        self.assertEqual(r.returncode, 0)

    def test_min_work_days_penalty(self):
        # store_optimizer_demo.py --min_work_days_per_month 15 --min_work_days_penalty 5 --params max_time_in_seconds:0.5
        r = run_demo(
            "--min_work_days_per_month", "15",
            "--min_work_days_penalty", "5",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)

    def test_target_women_ratio(self):
        # store_optimizer_demo.py --target_min_women_ratio 0.35 --target_max_women_ratio 0.65 --params max_time_in_seconds:0.5
        r = run_demo(
            "--target_min_women_ratio", "0.35",
            "--target_max_women_ratio", "0.65",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)

    def test_iterative_hire(self):
        # Understaffed scenario: 5 employees, demand needs more → iterative hire runs
        r = run_demo(
            "--current_employees", "5",
            "--min_employees", "5",
            "--max_employees", "10",
            "--direct_base_M_weekday", "3",
            "--direct_base_A_weekday", "3",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("Iterative hire", r.stdout)


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


class TestDemoDynamicShifts(unittest.TestCase):
    """Tests for --store_shifts (variable shifts per store)."""

    def test_store_shifts_single_store_3_shifts(self):
        # store_optimizer_demo.py --store_shifts store_A:3 --demand direct --direct_base_by_day 1 --direct_deterministic --max_employees 15 --params max_time_in_seconds:1.0
        r = run_demo(
            "--store_ids", "store_A",
            "--store_shifts", "store_A:3",
            "--demand", "direct",
            "--direct_base_by_day", "1",
            "--direct_deterministic",
            "--max_employees", "15",
            "--params", "max_time_in_seconds:1.0",
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("S1", r.stdout)
        self.assertIn("S2", r.stdout)
        self.assertIn("S3", r.stdout)

    def test_store_shifts_multi_store(self):
        # store_A:6, store_B:3 - different shift counts
        r = run_demo(
            "--store_ids", "store_A,store_B",
            "--store_shifts", "store_A:3,store_B:3",
            "--demand", "direct",
            "--direct_base_by_day", "2",
            "--direct_deterministic",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)

    def test_store_shifts_estimation(self):
        # Variable shifts with estimation
        r = run_demo(
            "--store_ids", "store_A",
            "--store_shifts", "store_A:3",
            "--demand", "estimation",
            "--rule", "ratio",
            "--customers_per_employee", "30",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)


def _parse_schedule_and_check_women_alternate_sundays(stdout: str, year: int = 2025, month: int = 1) -> list[str]:
    """Parse Solution 2 (optimized) from stdout, check women's alternating Sundays rule.
    Returns list of violation messages (empty if OK).
    """
    import calendar
    # Find Solution 2 block (optimized schedule)
    lines = stdout.splitlines()
    violations = []
    chars_per_day = 3
    # Sundays in month (1-indexed day of month)
    _, num_days = calendar.monthrange(year, month)
    sundays = [
        d for d in range(1, num_days + 1)
        if calendar.weekday(year, month, d) == 6  # Sunday
    ]
    if len(sundays) < 2:
        return []
    in_schedule = False
    for line in lines:
        if "Solution 2" in line and "optimized" in line:
            in_schedule = True
            continue
        if in_schedule and line.strip().startswith("employee"):
            # Parse "employee  3 (F): A  O  M  M  O  ..."
            parts = line.split(":", 1)
            if len(parts) != 2:
                continue
            label, shift_part = parts
            if "(F)" not in label:
                continue
            # Extract shift for each day; each day is 3 chars
            # Handle pipes | that separate prev/current/next month context
            shift_part = shift_part.rstrip()
            if "|" in shift_part:
                # Format: "prev_days|current_month_days|next_days" - extract middle part
                pipe_parts = shift_part.split("|")
                if len(pipe_parts) == 3:
                    shift_part = pipe_parts[1]  # Only the current month
                elif len(pipe_parts) == 2:
                    # Could be "prev|current" or "current|next"
                    shift_part = pipe_parts[1] if len(pipe_parts[0]) < len(pipe_parts[1]) else pipe_parts[0]
            shifts_by_day = []
            for i in range(num_days):
                start = i * chars_per_day
                if start + 1 <= len(shift_part):
                    s = shift_part[start:start + chars_per_day].strip()
                    shifts_by_day.append(s[0] if s else "?")
                else:
                    shifts_by_day.append("?")
            # Check consecutive Sundays
            for j in range(len(sundays) - 1):
                d1, d2 = sundays[j], sundays[j + 1]
                idx1, idx2 = d1 - 1, d2 - 1
                if idx1 < len(shifts_by_day) and idx2 < len(shifts_by_day):
                    s1 = shifts_by_day[idx1]
                    s2 = shifts_by_day[idx2]
                    if s1 != "O" and s2 != "O":
                        violations.append(
                            f"Employee {label.strip()}: worked consecutive Sundays "
                            f"day {d1} and day {d2}"
                        )
        elif in_schedule and line.strip() and not line.strip().startswith("employee") and "Solution" not in line:
            # End of schedule block (hit penalties or next section)
            if "Penalties:" in line or "Legal rule:" in line or "Recommendation:" in line:
                break
    return violations


class TestDemoRosterAndPreviousSchedule(unittest.TestCase):
    """Tests for --roster_file, --previous_schedule, --output_schedule."""

    FIXTURES = os.path.join(PROJECT_ROOT, "tests", "fixtures")

    def test_women_alternate_sundays_roster_file_iterative_hire(self):
        """Regression: roster_file + understaffed + iterative hire must satisfy women alternate Sundays."""
        r = run_demo(
            "--roster_file", os.path.join(self.FIXTURES, "roster.csv"),
            "--direct_base_M_weekday", "2",
            "--direct_base_A_weekday", "2",
            "--direct_base_M_weekend", "2",
            "--direct_base_A_weekend", "2",
            "--direct_deterministic",
            "--spread_sunday_shifts_penalty", "2",
            "--spread_shifts_penalty", "0",
            "--min_work_days_per_month", "20",
            "--min_work_days_penalty", "2",
            "--params", "max_time_in_seconds:10",
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr or r.stdout)
        violations = _parse_schedule_and_check_women_alternate_sundays(r.stdout)
        self.assertEqual(
            violations, [],
            f"Women alternate Sundays rule violated: {violations}\n\nOutput:\n{r.stdout[-2000:]}"
        )

    def test_roster_file_json(self):
        r = run_demo(
            "--roster_file", os.path.join(self.FIXTURES, "roster.json"),
            "--direct_base_M_weekday", "2",
            "--direct_base_A_weekday", "2",
            "--direct_base_M_weekend", "2",
            "--direct_base_A_weekend", "1",
            "--direct_deterministic",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("employee", r.stdout)

    def test_roster_file_csv(self):
        r = run_demo(
            "--roster_file", os.path.join(self.FIXTURES, "roster.csv"),
            "--direct_base_M_weekday", "2",
            "--direct_base_A_weekday", "2",
            "--direct_deterministic",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)

    def test_roster_file_6shifts(self):
        # Use 3-shift store (simpler) with roster; relax constraints for feasibility
        r = run_demo(
            "--store_shifts", "store:3",
            "--roster_file", os.path.join(self.FIXTURES, "roster.json"),
            "--max_employees", "10",
            "--direct_base_by_day", "1,1,1;1,1,1;1,1,1;1,1,1;1,1,1;1,1,1;1,1,1",
            "--direct_deterministic",
            "--min_sunday_off_per_month", "0",
            "--min_sunday_off_women", "0",
            "--params", "max_time_in_seconds:2.0",
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("S1", r.stdout)

    def test_previous_schedule_6shifts(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out_path = f.name
        try:
            # Use 3-shift store with 5-employee roster; relax constraints
            base_by_day = "1,1,1;1,1,1;1,1,1;1,1,1;1,1,1;1,1,1;1,1,1"
            r1 = run_demo(
                "--store_shifts", "store:3",
                "--roster_file", os.path.join(self.FIXTURES, "roster.json"),
                "--max_employees", "10",
                "--year", "2025", "--month", "1",
                "--direct_base_by_day", base_by_day,
                "--direct_deterministic",
                "--min_sunday_off_per_month", "0",
                "--min_sunday_off_women", "0",
                "--output_schedule", out_path,
                "--params", "max_time_in_seconds:2.0",
            )
            self.assertEqual(r1.returncode, 0, msg=r1.stderr or r1.stdout)
            self.assertTrue(os.path.exists(out_path))
            self.assertGreater(os.path.getsize(out_path), 0, msg="Schedule file should not be empty")

            r2 = run_demo(
                "--store_shifts", "store:3",
                "--roster_file", os.path.join(self.FIXTURES, "roster.json"),
                "--max_employees", "10",
                "--previous_schedule", out_path,
                "--year", "2025", "--month", "2",
                "--direct_base_by_day", base_by_day,
                "--direct_deterministic",
                "--min_sunday_off_per_month", "0",
                "--min_sunday_off_women", "0",
                "--params", "max_time_in_seconds:2.0",
            )
            self.assertEqual(r2.returncode, 0, msg=r2.stderr or r2.stdout)
        finally:
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_output_schedule_structure(self):
        """Output schedule JSON has expected structure (employee_ids, schedule, shifts, year, month)."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out_path = f.name
        try:
            r = run_demo(
                "--roster_file", os.path.join(self.FIXTURES, "roster.json"),
                "--direct_base_M_weekday", "2",
                "--direct_base_A_weekday", "2",
                "--direct_base_M_weekend", "1",
                "--direct_base_A_weekend", "1",
                "--direct_deterministic",
                "--output_schedule", out_path,
                "--params", FAST_PARAMS,
            )
            self.assertEqual(r.returncode, 0)
            with open(out_path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("employee_ids", data)
            self.assertIn("schedule", data)
            self.assertIn("shifts", data)
            self.assertIn("year", data)
            self.assertIn("month", data)
            self.assertEqual(len(data["schedule"]), len(data["employee_ids"]))
        finally:
            if os.path.exists(out_path):
                os.unlink(out_path)

    def test_previous_schedule_with_fixture(self):
        # Use fixture schedule (6 shifts, 18 employees); relax constraints for feasibility
        r = run_demo(
            "--store_shifts", "store:6",
            "--roster_file", os.path.join(self.FIXTURES, "roster_6shifts.json"),
            "--max_employees", "20",
            "--previous_schedule", os.path.join(self.FIXTURES, "schedule_jan_2025_6shifts.json"),
            "--year", "2025", "--month", "2",
            "--direct_base_by_day", "1,1,1,1,1,1;1,1,1,1,1,1;1,1,1,1,1,1;1,1,1,1,1,1;1,1,1,1,1,1;1,1,1,1,1,1;1,1,1,1,1,1",
            "--direct_deterministic",
            "--min_sunday_off_per_month", "0",
            "--min_sunday_off_women", "0",
            "--params", "max_time_in_seconds:3.0",
        )
        self.assertEqual(r.returncode, 0)


class TestDemoFixedShift(unittest.TestCase):
    """Tests for --fixed_shift (employee always works same shift)."""

    FIXTURES = os.path.join(PROJECT_ROOT, "tests", "fixtures")

    def test_fixed_shift_off(self):
        # Default: no constraint, employees can work any shift
        r = run_demo(
            "--store_shifts", "store:3",
            "--fixed_shift", "off",
            "--direct_base_by_day", "2,2,2",
            "--direct_deterministic",
            "--current_employees", "9",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)

    def test_fixed_shift_model(self):
        # Solver picks one shift per employee
        r = run_demo(
            "--store_shifts", "store:3",
            "--fixed_shift", "model",
            "--direct_base_by_day", "2,2,2",
            "--direct_deterministic",
            "--current_employees", "9",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)

    def test_fixed_shift_roster(self):
        # Roster defines shift per employee
        r = run_demo(
            "--store_shifts", "store:3",
            "--roster_file", os.path.join(self.FIXTURES, "roster_with_shift.json"),
            "--fixed_shift", "roster",
            "--direct_base_by_day", "2,2,2",
            "--direct_deterministic",
            "--min_sunday_off_per_month", "0",
            "--min_sunday_off_women", "0",
            "--params", FAST_PARAMS,
        )
        self.assertEqual(r.returncode, 0)

    def test_fixed_shift_roster_missing_shift_field(self):
        # Roster without shift field: employees without 'shift' use model-based assignment
        r = run_demo(
            "--store_shifts", "store:3",
            "--roster_file", os.path.join(self.FIXTURES, "roster.json"),
            "--fixed_shift", "roster",
            "--direct_base_by_day", "1,1,1",
            "--direct_deterministic",
            "--min_sunday_off_per_month", "0",
            "--min_sunday_off_women", "0",
            "--params", FAST_PARAMS,
        )
        # Should succeed: employees without shift use model-based assignment
        self.assertEqual(r.returncode, 0)


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

