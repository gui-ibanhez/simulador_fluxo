"""CLI and behavior tests for slot_optimizer.py."""

import os
import subprocess
import sys
import tempfile
import unittest

from ortools.sat.python import cp_model

from slot_optimizer import (
    DAY_NAMES,
    STORE_PRESETS,
    _merge_config,
    build_slot_model,
    build_complete_week_dates,
    build_demand_by_day_from_daily_dates,
    detect_demand_mode_from_csv,
    parse_demand_csv_daily,
    parse_demand_dict,
    parse_demand_csv,
    solve_once,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLOT_SCRIPT = os.path.join(PROJECT_ROOT, "slot_optimizer.py")
FAST_TIME_LIMIT = "0.5"
VENV_PYTHON = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")


def run_slot(*args: str) -> subprocess.CompletedProcess:
    """Run slot_optimizer.py with given args."""
    python = VENV_PYTHON if os.path.isfile(VENV_PYTHON) else sys.executable
    cmd = [python, SLOT_SCRIPT] + list(args)
    return subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )


def base_required_args() -> list[str]:
    """Minimal stable args for CLI smoke tests."""
    return [
        "--demand",
        os.path.join(PROJECT_ROOT, "test_data", "demand_sample_hour_barra.csv"),
        "--roster",
        os.path.join(PROJECT_ROOT, "test_data", "roster_barra_mixed.json"),
        "--year",
        "2025",
        "--month",
        "1",
        "--solver-time-limit",
        FAST_TIME_LIMIT,
        "--min-days-off-per-week",
        "0",
        "--max-consecutive-work-days",
        "0",
        "--max-consecutive-off-days",
        "0",
    ]


def _is_closed(cfg, date_obj):
    preset = STORE_PRESETS.get(cfg["store_type"], STORE_PRESETS["street"])
    closed_dows = (
        cfg["closed_days"] if cfg.get("closed_days") is not None else preset["closed_days"]
    )
    return date_obj.weekday() in closed_dows


def _off_gap_by_gender(result, scope: str):
    solver = result["solver"]
    works = result["variables"]["works"]
    roster = result["roster"]
    dates = result["dates"]
    cfg = result["cfg"]

    primary_start = cfg.get("primary_start", 0)
    primary_end = cfg.get("primary_end", len(dates))
    day_indices = (
        list(range(len(dates))) if scope == "full" else list(range(primary_start, primary_end))
    )
    non_closed_days = [d for d in day_indices if not _is_closed(cfg, dates[d])]

    groups = {"M": [], "F": []}
    for e, emp in enumerate(roster):
        g = str(emp.get("gender", "")).upper()
        if g in groups:
            groups[g].append(e)

    gaps = {}
    for g, emps in groups.items():
        if len(emps) < 2:
            continue
        off_counts = []
        for e in emps:
            worked = sum(1 for d in non_closed_days if solver.boolean_value(works[e, d]))
            off_counts.append(len(non_closed_days) - worked)
        gaps[g] = max(off_counts) - min(off_counts)
    return gaps


def _sunday_off_gap_by_gender(result, scope: str):
    solver = result["solver"]
    works = result["variables"]["works"]
    roster = result["roster"]
    dates = result["dates"]
    cfg = result["cfg"]

    primary_start = cfg.get("primary_start", 0)
    primary_end = cfg.get("primary_end", len(dates))
    if scope == "full":
        sunday_indices = [d for d in range(len(dates)) if dates[d].weekday() == 6]
    else:
        sunday_indices = [d for d in range(primary_start, primary_end) if dates[d].weekday() == 6]

    groups = {"M": [], "F": []}
    for e, emp in enumerate(roster):
        g = str(emp.get("gender", "")).upper()
        if g in groups:
            groups[g].append(e)

    gaps = {}
    for g, emps in groups.items():
        if len(emps) < 2 or not sunday_indices:
            continue
        off_counts = []
        for e in emps:
            worked = sum(1 for d in sunday_indices if solver.boolean_value(works[e, d]))
            off_counts.append(len(sunday_indices) - worked)
        gaps[g] = max(off_counts) - min(off_counts)
    return gaps


class TestSlotHelp(unittest.TestCase):
    def test_help_includes_new_flags(self):
        r = run_slot("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--min-sunday-off-per-month", r.stdout)
        self.assertIn("--quadratic-understaff-penalty", r.stdout)
        self.assertIn("--use-greedy-start-hint", r.stdout)
        self.assertIn("--min-workers-per-day", r.stdout)
        self.assertIn("--max-off-gap-same-gender", r.stdout)
        self.assertIn("--max-off-gap-same-gender-scope", r.stdout)
        self.assertIn("--max-sunday-off-gap-same-gender", r.stdout)
        self.assertIn("--max-sunday-off-gap-same-gender-scope", r.stdout)
        self.assertIn("--demand-mode", r.stdout)


class TestSlotOffGapCLI(unittest.TestCase):
    def test_quadratic_understaff_penalty_smoke(self):
        r = run_slot(
            *base_required_args(),
            "--quadratic-understaff-penalty",
            "1",
        )
        self.assertIn(r.returncode, (0, 1))

    def test_use_greedy_start_hint_smoke(self):
        r = run_slot(
            *base_required_args(),
            "--use-greedy-start-hint",
        )
        self.assertIn(r.returncode, (0, 1))
        self.assertIn("Greedy start hint: enabled", r.stdout)

    def test_min_workers_per_day_smoke(self):
        r = run_slot(
            *base_required_args(),
            "--min-workers-per-day",
            "5",
        )
        self.assertIn(r.returncode, (0, 1))
        self.assertIn("Min workers per day: 5 (primary, non-closed)", r.stdout)

    def test_off_gap_primary_scope_smoke(self):
        r = run_slot(
            *base_required_args(),
            "--max-off-gap-same-gender",
            "1",
            "--max-off-gap-same-gender-scope",
            "primary",
        )
        self.assertIn(r.returncode, (0, 1))
        self.assertIn("Max off-day gap same gender: 1 (primary)", r.stdout)

    def test_off_gap_full_scope_smoke(self):
        r = run_slot(
            *base_required_args(),
            "--max-off-gap-same-gender",
            "1",
            "--max-off-gap-same-gender-scope",
            "full",
        )
        self.assertIn(r.returncode, (0, 1))
        self.assertIn("Max off-day gap same gender: 1 (full)", r.stdout)

    def test_off_gap_negative_rejected(self):
        r = run_slot(
            *base_required_args(),
            "--max-off-gap-same-gender",
            "-1",
        )
        self.assertNotEqual(r.returncode, 0)
        combined = f"{r.stdout}\n{r.stderr}"
        self.assertIn("max_off_gap_same_gender must be >= 0", combined)

    def test_min_workers_per_day_negative_rejected(self):
        r = run_slot(
            *base_required_args(),
            "--min-workers-per-day",
            "-1",
        )
        self.assertNotEqual(r.returncode, 0)
        combined = f"{r.stdout}\n{r.stderr}"
        self.assertIn("min_workers_per_day must be >= 0", combined)

    def test_min_sunday_off_per_month_negative_rejected(self):
        r = run_slot(
            *base_required_args(),
            "--min-sunday-off-per-month",
            "-1",
        )
        self.assertNotEqual(r.returncode, 0)
        combined = f"{r.stdout}\n{r.stderr}"
        self.assertIn("min_sunday_off_per_month must be >= 0", combined)


class TestSlotOffGapBehavior(unittest.TestCase):
    def test_gap_is_enforced_primary(self):
        demand_path = os.path.join(PROJECT_ROOT, "test_data", "demand_sample_hour_barra.csv")
        demand, slot_minutes, detected_interval = parse_demand_csv(demand_path)
        dates, primary_start, primary_end = build_complete_week_dates(2025, 1)

        roster = [
            {"id": "m1", "gender": "M"},
            {"id": "m2", "gender": "M"},
            {"id": "f1", "gender": "F"},
            {"id": "f2", "gender": "F"},
        ]

        cfg = _merge_config(
            {
                "year": 2025,
                "month": 1,
                "slot_interval": detected_interval,
                "primary_start": primary_start,
                "primary_end": primary_end,
                "solver_time_limit": 1.0,
                "min_days_off_per_week": 0,
                "max_consecutive_work_days": 0,
                "max_consecutive_off_days": 0,
                "closed_days": [],
                "special_days": [],
                "max_off_gap_same_gender": 1,
                "max_off_gap_same_gender_scope": "primary",
            }
        )

        result = solve_once(roster, demand, slot_minutes, dates, cfg)
        self.assertIn(result["status"], (cp_model.OPTIMAL, cp_model.FEASIBLE))

        gaps = _off_gap_by_gender(result, scope="primary")
        for g, gap in gaps.items():
            self.assertLessEqual(gap, 1, f"{g} off-gap exceeded: {gap}")


class TestSlotSundayOffGapBehavior(unittest.TestCase):
    def test_sunday_gap_is_enforced_full(self):
        demand_path = os.path.join(PROJECT_ROOT, "test_data", "demand_sample_hour_barra.csv")
        demand, slot_minutes, detected_interval = parse_demand_csv(demand_path)
        dates, primary_start, primary_end = build_complete_week_dates(2025, 3)

        roster = [
            {"id": "m1", "gender": "M"},
            {"id": "m2", "gender": "M"},
            {"id": "m3", "gender": "M"},
            {"id": "m4", "gender": "M"},
        ]

        cfg = _merge_config(
            {
                "year": 2025,
                "month": 3,
                "slot_interval": detected_interval,
                "primary_start": primary_start,
                "primary_end": primary_end,
                "solver_time_limit": 2.0,
                "min_days_off_per_week": 0,
                "max_consecutive_work_days": 0,
                "max_consecutive_off_days": 0,
                "closed_days": [],
                "special_days": [],
                "max_sunday_off_gap_same_gender": 1,
                "max_sunday_off_gap_same_gender_scope": "full",
                # Keep objective neutral for this behavior check.
                "minimize_off_days_penalty": 0,
                "spread_sunday_shifts_penalty": 0,
                "spread_shifts_penalty": 0,
            }
        )

        result = solve_once(roster, demand, slot_minutes, dates, cfg)
        self.assertIn(result["status"], (cp_model.OPTIMAL, cp_model.FEASIBLE))

        gaps = _sunday_off_gap_by_gender(result, scope="full")
        for g, gap in gaps.items():
            self.assertLessEqual(gap, 1, f"{g} Sunday off-gap exceeded: {gap}")


class TestSlotMinSundayOffBehavior(unittest.TestCase):
    def test_min_sunday_off_per_month_is_enforced_primary(self):
        # Keep one active slot/day and enough slack so the Sunday-off rule can bind.
        demand_dict = {dn: {"10:00": 1} for dn in DAY_NAMES}
        demand, slot_minutes, detected_interval = parse_demand_dict(demand_dict, slot_interval=60)
        dates, primary_start, primary_end = build_complete_week_dates(2025, 1)
        sunday_indices = [d for d in range(primary_start, primary_end) if dates[d].weekday() == 6]

        roster = [{"id": f"e{i}", "gender": "M"} for i in range(4)]

        cfg = _merge_config(
            {
                "year": 2025,
                "month": 1,
                "slot_interval": detected_interval,
                "primary_start": primary_start,
                "primary_end": primary_end,
                "solver_time_limit": 1.0,
                "normal_duration_hours": 1,
                "special_duration_hours": 1,
                "close_time": "11:00",
                "closed_days": [],
                "special_days": [],
                "min_days_off_per_week": 0,
                "max_consecutive_work_days": 0,
                "max_consecutive_off_days": 0,
                "min_sunday_off_per_month": 2,
            }
        )

        result = solve_once(roster, demand, slot_minutes, dates, cfg)
        self.assertIn(result["status"], (cp_model.OPTIMAL, cp_model.FEASIBLE))

        solver = result["solver"]
        works = result["variables"]["works"]
        for e in range(len(roster)):
            sunday_worked = sum(1 for d in sunday_indices if solver.boolean_value(works[e, d]))
            sunday_off = len(sunday_indices) - sunday_worked
            self.assertGreaterEqual(
                sunday_off,
                2,
                f"Employee {e} has only {sunday_off} Sundays off (<2)",
            )


class TestSlotMinWorkersPerDayBehavior(unittest.TestCase):
    def test_min_workers_per_day_is_enforced(self):
        # Very low demand baseline so the daily floor drives the assignment.
        demand_dict = {dn: {"10:00": 0} for dn in DAY_NAMES}
        demand, slot_minutes, detected_interval = parse_demand_dict(demand_dict, slot_interval=60)
        dates, primary_start, primary_end = build_complete_week_dates(2025, 1)

        roster = [{"id": f"m{i}", "gender": "M"} for i in range(7)]

        cfg = _merge_config(
            {
                "year": 2025,
                "month": 1,
                "slot_interval": detected_interval,
                "primary_start": primary_start,
                "primary_end": primary_end,
                "solver_time_limit": 1.0,
                "normal_duration_hours": 1,
                "special_duration_hours": 1,
                "close_time": "11:00",
                "closed_days": [],  # keep all primary days active for this test
                "special_days": [],
                "min_days_off_per_week": 0,
                "max_consecutive_work_days": 0,
                "max_consecutive_off_days": 0,
                "minimize_off_days_penalty": 0,
                "min_workers_per_day": 5,
            }
        )

        result = solve_once(roster, demand, slot_minutes, dates, cfg)
        self.assertIn(result["status"], (cp_model.OPTIMAL, cp_model.FEASIBLE))

        solver = result["solver"]
        works = result["variables"]["works"]
        for d in range(primary_start, primary_end):
            worked = sum(
                1 for e in range(len(roster)) if solver.boolean_value(works[e, d])
            )
            self.assertGreaterEqual(
                worked, 5, f"Day {dates[d]} has only {worked} workers (<5)"
            )


class TestQuadraticUnderstaffBehavior(unittest.TestCase):
    def test_enabling_quadratic_understaff_adds_square_terms(self):
        # Keep one active slot with demand > capacity to force understaff variables.
        demand_dict = {dn: {"10:00": 2} for dn in DAY_NAMES}
        demand, slot_minutes, detected_interval = parse_demand_dict(demand_dict, slot_interval=60)
        dates, primary_start, primary_end = build_complete_week_dates(2025, 1)
        roster = [{"id": "m1", "gender": "M"}]

        base_cfg = {
            "year": 2025,
            "month": 1,
            "slot_interval": detected_interval,
            "primary_start": primary_start,
            "primary_end": primary_end,
            "solver_time_limit": 0.2,
            "normal_duration_hours": 1,
            "special_duration_hours": 1,
            "close_time": "11:00",
            "closed_days": [],
            "special_days": [],
            "start_window": ("10:00", "10:00"),
            "min_days_off_per_week": 0,
            "max_consecutive_work_days": 0,
            "max_consecutive_off_days": 0,
            "understaff_penalty": 1,
            "excess_penalty": 0,
            "quadratic_excess_penalty": 0,
        }

        cfg_linear = _merge_config({**base_cfg, "quadratic_understaff_penalty": 0})
        model_linear, _ = build_slot_model(roster, demand, slot_minutes, dates, cfg_linear)
        int_prod_linear = sum(
            1
            for ct in model_linear.Proto().constraints
            if len(ct.int_prod.exprs) > 0 or len(ct.int_prod.target.vars) > 0
        )

        cfg_quadratic = _merge_config({**base_cfg, "quadratic_understaff_penalty": 2})
        model_quadratic, _ = build_slot_model(roster, demand, slot_minutes, dates, cfg_quadratic)
        int_prod_quadratic = sum(
            1
            for ct in model_quadratic.Proto().constraints
            if len(ct.int_prod.exprs) > 0 or len(ct.int_prod.target.vars) > 0
        )

        self.assertEqual(int_prod_linear, 0)
        self.assertGreater(int_prod_quadratic, int_prod_linear)


class TestGreedyStartHintBehavior(unittest.TestCase):
    def test_greedy_hint_populates_solution_hint(self):
        demand_dict = {dn: {"10:00": 2, "11:00": 3, "12:00": 3, "13:00": 2} for dn in DAY_NAMES}
        demand, slot_minutes, detected_interval = parse_demand_dict(demand_dict, slot_interval=60)
        dates, primary_start, primary_end = build_complete_week_dates(2025, 1)
        roster = [{"id": f"e{i}", "gender": "M"} for i in range(8)]

        base_cfg = {
            "year": 2025,
            "month": 1,
            "slot_interval": detected_interval,
            "primary_start": primary_start,
            "primary_end": primary_end,
            "normal_duration_hours": 2,
            "special_duration_hours": 2,
            "close_time": "14:00",
            "start_window": ("10:00", "12:00"),
            "closed_days": [],
            "special_days": [],
            "min_days_off_per_week": 0,
            "max_consecutive_work_days": 0,
            "max_consecutive_off_days": 0,
        }

        cfg_off = _merge_config({**base_cfg, "use_greedy_start_hint": False})
        model_off, _ = build_slot_model(roster, demand, slot_minutes, dates, cfg_off)
        self.assertEqual(len(model_off.Proto().solution_hint.vars), 0)

        cfg_on = _merge_config({**base_cfg, "use_greedy_start_hint": True})
        model_on, _ = build_slot_model(roster, demand, slot_minutes, dates, cfg_on)
        self.assertGreater(len(model_on.Proto().solution_hint.vars), 0)


class TestDailyDemandMode(unittest.TestCase):
    def _write_temp_csv(self, rows):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        try:
            tmp.write("\n".join(rows) + "\n")
            tmp.flush()
            return tmp.name
        finally:
            tmp.close()

    def test_detect_mode_from_header(self):
        weekly_path = self._write_temp_csv(
            [
                "time,monday,tuesday,wednesday,thursday,friday,saturday,sunday",
                "10:00,1,1,1,1,1,1,1",
            ]
        )
        daily_path = self._write_temp_csv(
            [
                "date,time,demand",
                "2025-01-01,10:00,2",
            ]
        )
        try:
            self.assertEqual(detect_demand_mode_from_csv(weekly_path), "weekly")
            self.assertEqual(detect_demand_mode_from_csv(daily_path), "daily")
        finally:
            os.remove(weekly_path)
            os.remove(daily_path)

    def test_primary_only_daily_is_padded_with_dow_mean_ceil(self):
        dates, primary_start, primary_end = build_complete_week_dates(2025, 1)
        rows = ["date,time,demand"]
        for d in dates[primary_start:primary_end]:
            rows.append(f"{d.isoformat()},10:00,{d.weekday() + 1}")
        path = self._write_temp_csv(rows)
        try:
            daily_by_date, slot_minutes, _ = parse_demand_csv_daily(path)
            demand_by_day = build_demand_by_day_from_daily_dates(
                daily_by_date=daily_by_date,
                dates=dates,
                primary_start=primary_start,
                primary_end=primary_end,
                slot_minutes=slot_minutes,
            )
            for i, d in enumerate(dates):
                self.assertEqual(demand_by_day[i][0], d.weekday() + 1)
        finally:
            os.remove(path)

    def test_full_padded_daily_is_used_as_is(self):
        dates, primary_start, primary_end = build_complete_week_dates(2025, 1)
        rows = ["date,time,demand"]
        expected = {}
        for i, d in enumerate(dates):
            val = (i % 5) + 2
            rows.append(f"{d.isoformat()},10:00,{val}")
            expected[d] = val
        path = self._write_temp_csv(rows)
        try:
            daily_by_date, slot_minutes, _ = parse_demand_csv_daily(path)
            demand_by_day = build_demand_by_day_from_daily_dates(
                daily_by_date=daily_by_date,
                dates=dates,
                primary_start=primary_start,
                primary_end=primary_end,
                slot_minutes=slot_minutes,
            )
            for i, d in enumerate(dates):
                self.assertEqual(demand_by_day[i][0], expected[d])
        finally:
            os.remove(path)

    def test_partial_daily_date_coverage_is_rejected(self):
        dates, primary_start, primary_end = build_complete_week_dates(2025, 1)
        # Provide only part of the primary month -> invalid.
        rows = ["date,time,demand"]
        for d in dates[primary_start:primary_start + 5]:
            rows.append(f"{d.isoformat()},10:00,3")
        path = self._write_temp_csv(rows)
        try:
            daily_by_date, slot_minutes, _ = parse_demand_csv_daily(path)
            with self.assertRaises(ValueError):
                build_demand_by_day_from_daily_dates(
                    daily_by_date=daily_by_date,
                    dates=dates,
                    primary_start=primary_start,
                    primary_end=primary_end,
                    slot_minutes=slot_minutes,
                )
        finally:
            os.remove(path)

    def test_weekly_and_equivalent_daily_have_same_objective(self):
        weekly = {dn: {"10:00": i + 1} for i, dn in enumerate(DAY_NAMES)}
        demand_weekly, slot_minutes, detected_interval = parse_demand_dict(weekly, slot_interval=60)
        dates, primary_start, primary_end = build_complete_week_dates(2025, 1)

        rows = ["date,time,demand"]
        for d in dates[primary_start:primary_end]:
            rows.append(f"{d.isoformat()},10:00,{d.weekday() + 1}")
        daily_path = self._write_temp_csv(rows)
        try:
            daily_by_date, slot_minutes_daily, _ = parse_demand_csv_daily(daily_path)
            demand_daily = build_demand_by_day_from_daily_dates(
                daily_by_date=daily_by_date,
                dates=dates,
                primary_start=primary_start,
                primary_end=primary_end,
                slot_minutes=slot_minutes_daily,
            )
        finally:
            os.remove(daily_path)

        roster = [{"id": f"m{i}", "gender": "M"} for i in range(8)]
        base_cfg = {
            "year": 2025,
            "month": 1,
            "slot_interval": detected_interval,
            "primary_start": primary_start,
            "primary_end": primary_end,
            "solver_time_limit": 1.0,
            "normal_duration_hours": 1,
            "special_duration_hours": 1,
            "close_time": "11:00",
            "closed_days": [],
            "special_days": [],
            "min_days_off_per_week": 0,
            "max_consecutive_work_days": 0,
            "max_consecutive_off_days": 0,
            "minimize_off_days_penalty": 0,
        }

        weekly_cfg = _merge_config({**base_cfg, "demand_matrix_kind": "weekly"})
        daily_cfg = _merge_config({**base_cfg, "demand_matrix_kind": "daily_by_day"})
        res_weekly = solve_once(roster, demand_weekly, slot_minutes, dates, weekly_cfg)
        res_daily = solve_once(roster, demand_daily, slot_minutes, dates, daily_cfg)
        self.assertIn(res_weekly["status"], (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertIn(res_daily["status"], (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertEqual(res_weekly["objective"], res_daily["objective"])
