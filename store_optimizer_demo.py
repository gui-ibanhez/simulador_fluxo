#!/usr/bin/env python3
"""Example usage for the store staffing optimizer with fake demand.

Supports two demand sources:
- --demand=direct: fake demand per (day, shift) (default).
- --demand=estimation: fake customer flow → staffing_estimation → demand (SPEC Part 1 → Part 2).
"""

import argparse
import csv
import json
import random
from pathlib import Path
from typing import Callable, Optional

from ortools.sat.python import cp_model

from store_staffing_optimizer import (
    build_complete_week_dates,
    build_model,
    build_roster_composition,
    compute_understaff_summary,
    extract_shift_assignments,
    load_previous_schedule,
    print_solution,
    recommend_hire_one_gender,
    schedule_to_dict,
    solve_once,
    status_name,
    validate_demand,
    validate_women_sunday_alternate,
)

try:
    from staffing_estimation import estimate_required_employees
except ImportError:
    estimate_required_employees = None


# Demand profiles: (base_M_weekday, base_A_weekday, base_M_weekend, base_A_weekend)
DEMAND_PROFILES = {
    "balanced": (5, 4, 4, 3),
    "weekend_heavy": (4, 3, 6, 5),
    "weekday_heavy": (6, 5, 3, 2),
}


def make_fake_roster(num_employees: int, seed: int) -> list[dict]:
    """Generate fake roster with gender for testing. Index i = employee i."""
    rng = random.Random(seed)
    return [
        {"id": f"emp_{e}", "gender": rng.choice(["M", "F"])}
        for e in range(num_employees)
    ]


def make_roster_from_composition(men: int, women: int) -> list[dict]:
    """Build roster with exactly N men and M women."""
    roster = []
    for e in range(men):
        roster.append({"id": f"emp_{e}", "gender": "M"})
    for e in range(men, men + women):
        roster.append({"id": f"emp_{e}", "gender": "F"})
    return roster


def scale_roster(
    base_roster: Optional[list[dict]],
    target_total: int,
    seed: int = 7,
    target_min_women_ratio: float = 0.4,
    target_max_women_ratio: float = 0.6,
) -> list[dict]:
    """Scale roster to target total while preserving existing members and their attributes."""
    if not base_roster:
        # No base roster: create from scratch with fake IDs
        n_m = round(target_total * 0.5)
        roster = []
        for i in range(n_m):
            roster.append({"id": f"emp_{i}", "gender": "M"})
        for i in range(n_m, target_total):
            roster.append({"id": f"emp_{i}", "gender": "F"})
        return roster

    if target_total <= len(base_roster):
        # Scale down: keep first N
        return base_roster[:target_total]

    # Scale up: keep all existing, add new ones
    new_roster = list(base_roster)
    num_to_hire = target_total - len(base_roster)
    for i in range(num_to_hire):
        g = recommend_hire_one_gender(
            new_roster, target_min_women_ratio, target_max_women_ratio
        )
        new_roster.append({"id": f"emp_{len(new_roster)}", "gender": g})
    return new_roster


def load_roster(path: str) -> list[dict]:
    """Load roster from JSON or CSV. Returns list of {"id": ..., "gender": ...}.

    JSON: [{"id": "E001", "gender": "M"}, ...]
    CSV: header row required, columns id,gender
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Roster file not found: {path}")
    suffix = p.suffix.lower()
    if suffix == ".json":
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("JSON roster must be a list of employee objects")
        roster = []
        seen_ids = set()
        for i, row in enumerate(data):
            if not isinstance(row, dict):
                raise ValueError(f"Roster row {i} must be a dict")
            eid = row.get("id")
            if eid is None or eid == "":
                eid = f"emp_{i}"
            if eid in seen_ids:
                raise ValueError(f"Duplicate employee id: {eid}")
            seen_ids.add(eid)
            g = str(row.get("gender", "M")).upper()
            if g not in ("M", "F"):
                g = "M" if g in ("MALE", "1", "TRUE") else "F"
            roster.append({"id": eid, "gender": g, **{k: v for k, v in row.items() if k not in ("id", "gender")}})
        return roster
    if suffix in (".csv", ".txt", ""):
        with open(p, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "id" not in reader.fieldnames or "gender" not in reader.fieldnames:
                raise ValueError("CSV must have header with id,gender columns")
            roster = []
            seen_ids = set()
            for i, row in enumerate(reader):
                eid = (row.get("id") or "").strip()
                if not eid:
                    eid = f"emp_{i}"
                if eid in seen_ids:
                    raise ValueError(f"Duplicate employee id: {eid}")
                seen_ids.add(eid)
                g = str(row.get("gender", "M")).strip().upper()
                if g not in ("M", "F"):
                    g = "M" if g in ("MALE", "1", "TRUE") else "F"
                roster.append({"id": eid, "gender": g})
            return roster
    raise ValueError(f"Roster file must be .json or .csv, got {suffix}")


def make_fake_demand(
    dates,
    seed,
    base_weekday=(5, 4),
    base_weekend=(4, 3),
    *,
    base_M_weekday=None,
    base_A_weekday=None,
    base_M_weekend=None,
    base_A_weekend=None,
    add_random: bool = True,
    profile: str | None = None,
    work_shifts: list[str] | None = None,
    base_by_day: list[list[int]] | None = None,
):
    """Fake demand per (day, shift): list of { shift: n } per day.

    M/A mode (work_shifts=None): use base_weekday, base_weekend, profile.
    Variable shifts mode (work_shifts + base_by_day): 7 groups (Mon-Sun), each len(work_shifts) values.
    If add_random=True (default), each day uses base ± {-1, 0, 1} (at least 1).
    If add_random=False, use bases exactly (deterministic).
    """
    rng = random.Random(seed)
    if work_shifts is not None and base_by_day is not None:
        # Variable shifts: base_by_day[wd] = [v1, v2, ...] for each shift
        demand = []
        for date in dates:
            wd = date.weekday()
            bases = base_by_day[wd][: len(work_shifts)]
            if len(bases) < len(work_shifts):
                bases = bases + [bases[-1] if bases else 1] * (len(work_shifts) - len(bases))
            row = {}
            for i, sh in enumerate(work_shifts):
                b = bases[i] if i < len(bases) else 1
                if add_random:
                    row[sh] = max(0, b + rng.choice([-1, 0, 1]))
                else:
                    row[sh] = b
            demand.append(row)
        return demand
    # M/A mode
    if profile and profile in DEMAND_PROFILES:
        base_weekday = DEMAND_PROFILES[profile][:2]
        base_weekend = DEMAND_PROFILES[profile][2:]
    if base_M_weekday is not None and base_A_weekday is not None:
        base_weekday = (base_M_weekday, base_A_weekday)
    if base_M_weekend is not None and base_A_weekend is not None:
        base_weekend = (base_M_weekend, base_A_weekend)
    demand = []
    for date in dates:
        wd = date.weekday()
        if wd < 5:
            bm, ba = base_weekday
            base_m = bm[wd] if isinstance(bm, list) else bm
            base_a = ba[wd] if isinstance(ba, list) else ba
        else:
            bm, ba = base_weekend
            idx = wd - 5
            base_m = bm[idx] if isinstance(bm, list) else bm
            base_a = ba[idx] if isinstance(ba, list) else ba
        if add_random:
            m = max(0, base_m + rng.choice([-1, 0, 1]))
            a = max(0, base_a + rng.choice([-1, 0, 1]))
        else:
            m = base_m
            a = base_a
        demand.append({"M": m, "A": a})
    return demand


def make_fake_customer_flow(
    dates,
    work_shifts: list[str],
    seed: int,
    store_ids: list[str] | None = None,
    work_shifts_by_store: dict[str, list[str]] | None = None,
):
    """
    Fake customer flow per store: one value per (day, shift) per store.
    Returns { "store_id": [cust_period_0, cust_period_1, ... ], ... }.
    Period order: day0_S1, day0_S2, ..., day1_S1, ...
    If work_shifts_by_store is provided, each store uses its own work_shifts.
    """
    if store_ids is None:
        store_ids = ["store"]
    out = {}
    for store_id in store_ids:
        shifts = work_shifts_by_store.get(store_id, work_shifts) if work_shifts_by_store else work_shifts
        rng = random.Random(seed + hash(store_id) % (2**32))
        num_days = len(dates)
        n_s = len(shifts)
        # Base per shift: (120, 80) for 2 shifts; pad for more
        base_wd = [120, 80] if n_s >= 2 else [120]
        base_we = [80, 60] if n_s >= 2 else [80]
        if n_s > 2:
            base_wd = base_wd + [base_wd[-1]] * (n_s - len(base_wd))
            base_we = base_we + [base_we[-1]] * (n_s - len(base_we))
        flow = []
        for d in range(num_days):
            weekday = dates[d].weekday()
            base = base_wd if weekday < 5 else base_we
            for s in range(n_s):
                b = base[s] if s < len(base) else base[-1]
                flow.append(max(0, b + rng.randint(-30, 40)))
        out[store_id] = flow
    return out


def _parse_store_shifts(s: str, store_ids: list[str]) -> dict[str, list[str]] | None:
    """
    Parse 'store_A:6,store_B:3,store_C:8' -> {store_A: [O,S1..S6], store_B: [O,S1,S2,S3], ...}.
    Returns None if s is empty (use default M/A).
    """
    if not s or not s.strip():
        return None
    result: dict[str, list[str]] = {}
    for part in s.split(","):
        part = part.strip()
        if ":" in part:
            store_id, num_str = part.split(":", 1)
            store_id = store_id.strip()
            n = int(num_str.strip())
            if n < 1:
                raise ValueError(f"store_shifts: {store_id} must have at least 1 work shift, got {n}")
            shifts = ["O"] + [f"S{i + 1}" for i in range(n)]
            result[store_id] = shifts
    # Fill in defaults for stores not listed
    for sid in store_ids:
        if sid not in result:
            result[sid] = ["O", "M", "A"]
    return result


def _parse_direct_base_by_day(s: str, num_shifts: int) -> list[list[int]]:
    """
    Parse --direct_base_by_day: 7 groups (Mon-Sun) sep by ;, each N comma-sep values.
    Single int '4' -> 7 groups of [4]*N.
    """
    s = str(s).strip()
    if ";" in s:
        parts = [p.strip() for p in s.split(";") if p.strip()]
        if len(parts) != 7:
            raise ValueError(
                f"--direct_base_by_day must have 7 groups (Mon-Sun), got {len(parts)}"
            )
        result = []
        for p in parts:
            vals = [int(x.strip()) for x in p.split(",")]
            # Pad or truncate to num_shifts
            if len(vals) < num_shifts:
                vals = vals + [vals[-1] if vals else 1] * (num_shifts - len(vals))
            else:
                vals = vals[:num_shifts]
            result.append(vals)
        return result
    # Single group or single value
    try:
        val = int(s)
        return [[val] * num_shifts for _ in range(7)]
    except ValueError:
        pass

    if "," in s:
        vals = [int(x.strip()) for x in s.split(",")]
        # Pad or truncate to num_shifts
        if len(vals) < num_shifts:
            vals = vals + [vals[-1] if vals else 1] * (num_shifts - len(vals))
        else:
            vals = vals[:num_shifts]
        return [vals for _ in range(7)]

    # Fallback
    return [[1] * num_shifts for _ in range(7)]


def _parse_per_shift_int(s: str, shift_names: list[str], default: int = 1) -> dict[str, int]:
    """Parse '1' or '1,1,2,1,1,2' -> {S1: 1, S2: 1, ...}."""
    if not s or not s.strip():
        return {sh: default for sh in shift_names}
    s = str(s).strip()
    if "," in s:
        parts = [int(x.strip()) for x in s.split(",")]
        return {
            sh: parts[i] if i < len(parts) else (parts[-1] if parts else default)
            for i, sh in enumerate(shift_names)
        }
    val = int(s)
    return {sh: val for sh in shift_names}


def _parse_per_shift_float(s: str, shift_names: list[str], default: float = 30.0) -> dict[str, float]:
    """Parse '30' or '30,25,30,...' -> {S1: 30, S2: 25, ...}."""
    if not s or not s.strip():
        return {sh: default for sh in shift_names}
    s = str(s).strip()
    if "," in s:
        parts = [float(x.strip()) for x in s.split(",")]
        return {
            sh: parts[i] if i < len(parts) else (parts[-1] if parts else default)
            for i, sh in enumerate(shift_names)
        }
    val = float(s)
    return {sh: val for sh in shift_names}


def _parse_store_profiles(s: str) -> dict[str, str]:
    """Parse 'store_A:weekend_heavy,store_B:weekday_heavy' -> {store_A: weekend_heavy, store_B: weekday_heavy}."""
    if not s or not s.strip():
        return {}
    result = {}
    for part in s.split(","):
        part = part.strip()
        if ":" in part:
            store_id, profile = part.split(":", 1)
            result[store_id.strip()] = profile.strip()
    return result


def _parse_base_arg(val: str | int, expected_len: int | None = None) -> int | list[int]:
    """Parse base arg: '5' -> 5, '5,6,5,5,6' -> [5,6,5,5,6]. If expected_len, validate length."""
    if isinstance(val, int):
        return val
    s = str(val).strip()
    if "," in s:
        parts = [int(x.strip()) for x in s.split(",")]
        if expected_len is not None and len(parts) != expected_len:
            raise ValueError(
                f"Expected {expected_len} comma-sep values, got {len(parts)}"
            )
        return parts
    return int(s)


def _parse_constraint_specs(s: str) -> list[tuple]:
    """Parse constraint specs: 'M:1,1,0,3,4,5 A:2,2,10,4,5,20' -> list of 7-tuples (shift, hard_min, soft_min, min_cost, soft_max, hard_max, max_cost)."""
    if not s or not s.strip():
        return []
    result = []
    for part in s.split():
        if ":" in part:
            shift, rest = part.split(":", 1)
            nums = [int(x.strip()) for x in rest.split(",")]
            if len(nums) != 6:
                raise ValueError(
                    f"Constraint spec needs 6 numbers (hard_min,soft_min,min_cost,soft_max,hard_max,max_cost), got {len(nums)}"
                )
            result.append(
                (shift.strip(), nums[0], nums[1], nums[2], nums[3], nums[4], nums[5])
            )
    return result


def _parse_tiers(s):
    """Parse --tiers string '50,1,100,2,inf,5' -> [(50,1), (100,2), (inf,5)]."""
    parts = [p.strip() for p in s.split(",")]
    if len(parts) % 2 != 0:
        raise ValueError("--tiers must be even number of values: max_cust,emp,...")
    tiers = []
    for i in range(0, len(parts), 2):
        max_c = parts[i].lower()
        max_c = float("inf") if max_c == "inf" else float(max_c)
        emp = int(parts[i + 1])
        tiers.append((max_c, emp))
    return tiers


def _demand_list_from_raw(raw, dates, work_shifts):
    """Convert raw (store_id, period_ix) -> required into demand list for one store."""
    num_days = len(dates)
    num_s = len(work_shifts)
    demand = [{} for _ in range(num_days)]
    for (_, period_ix), required in raw.items():
        day = period_ix // num_s
        shift_ix = period_ix % num_s
        if day < num_days and shift_ix < num_s:
            shift_name = work_shifts[shift_ix]
            demand[day][shift_name] = required
    for d in range(num_days):
        for s in work_shifts:
            if s not in demand[d]:
                demand[d][s] = 0
    return demand


def demand_from_estimation(
    dates, work_shifts, customer_flow, rule="ratio", period_to_shift=None, **kwargs
):
    """
    Convert customer flow → staffing_estimation → demand list for optimizer.
    customer_flow: { "store_id": [cust_period_0, ... ] }, period = day * num_shifts + shift_ix.
    period_to_shift: callable(period_ix) -> shift name (for per-shift customers_per_employee).
    Returns demand: list of { "M": n, "A": n } per day (single store).
    """
    if estimate_required_employees is None:
        raise ImportError("staffing_estimation not available")
    raw = estimate_required_employees(
        customer_flow, rule=rule, period_to_shift=period_to_shift, **kwargs
    )
    return _demand_list_from_raw(raw, dates, work_shifts)


def demand_from_estimation_by_store(
    dates,
    work_shifts,
    customer_flow,
    rule="ratio",
    period_to_shift=None,
    work_shifts_by_store: dict[str, list[str]] | None = None,
    period_to_shift_by_store: dict[str, Callable[[int], str]] | None = None,
    kwargs_by_store: dict[str, dict] | None = None,
    **kwargs,
):
    """
    Same as demand_from_estimation but returns demand per store: { store_id: demand_list }.
    If work_shifts_by_store is provided, each store uses its own work_shifts.
    If period_to_shift_by_store is provided, use per-store period_to_shift for ratio/formula.
    kwargs_by_store: per-store overrides for kwargs (e.g. customers_per_employee).
    """
    if estimate_required_employees is None:
        raise ImportError("staffing_estimation not available")
    demand_by_store = {}
    for store_id, flow in customer_flow.items():
        shifts = (
            work_shifts_by_store[store_id]
            if work_shifts_by_store and store_id in work_shifts_by_store
            else work_shifts
        )
        p2s = (
            period_to_shift_by_store[store_id]
            if period_to_shift_by_store and store_id in period_to_shift_by_store
            else period_to_shift
        )
        store_kwargs = {**kwargs}
        if kwargs_by_store and store_id in kwargs_by_store:
            store_kwargs.update(kwargs_by_store[store_id])
        raw = estimate_required_employees(
            {store_id: flow},
            rule=rule,
            period_to_shift=p2s,
            **store_kwargs,
        )
        demand_by_store[store_id] = _demand_list_from_raw(raw, dates, shifts)
    return demand_by_store


def main():
    parser = argparse.ArgumentParser(
        description="Demo: store staffing optimizer with fake demand. All parameters can be set via CLI."
    )
    # Time and roster
    parser.add_argument("--year", type=int, default=2025, help="Schedule year.")
    parser.add_argument("--month", type=int, default=1, help="Schedule month.")
    parser.add_argument(
        "--store_ids",
        type=str,
        default="store",
        help="Comma-separated store IDs; demand is computed per store (variable by store).",
    )
    parser.add_argument(
        "--store_shifts",
        type=str,
        default="",
        help="Per-store work shift counts: store_A:6,store_B:3,store_C:8. Omit for default M/A (2 shifts).",
    )
    parser.add_argument("--current_employees", type=int, default=10, help="Current roster size.")
    parser.add_argument(
        "--roster_file",
        type=str,
        default="",
        help="Load roster from JSON or CSV file. Overrides --men/--women, sets current_employees from file.",
    )
    parser.add_argument(
        "--men",
        type=int,
        default=None,
        help="Number of men in roster. Use with --women to specify exact composition (e.g. --men 7 --women 8).",
    )
    parser.add_argument(
        "--women",
        type=int,
        default=None,
        help="Number of women in roster. Use with --men to specify exact composition.",
    )
    parser.add_argument("--min_employees", type=int, default=8, help="Min employees to try (hire).")
    parser.add_argument("--max_employees", type=int, default=14, help="Max employees to try.")
    parser.add_argument("--seed", type=int, default=7, help="Random seed for fake data.")
    # Demand source
    parser.add_argument(
        "--demand",
        choices=("direct", "estimation"),
        default="direct",
        help="direct: fake demand per (day,shift). estimation: fake flow → staffing_estimation → demand.",
    )
    parser.add_argument(
        "--rule",
        choices=("ratio", "tiers", "formula"),
        default="ratio",
        help="Staffing rule when --demand=estimation.",
    )
    # Estimation: ratio and formula (per-shift customers_per_employee)
    parser.add_argument("--customers_per_employee_M", type=float, default=30.0, help="Ratio: customers per employee (Morning).")
    parser.add_argument("--customers_per_employee_A", type=float, default=30.0, help="Ratio: customers per employee (Afternoon).")
    parser.add_argument(
        "--customers_per_employee",
        type=str,
        default="",
        help="When --store_shifts: single float or comma-sep per shift (e.g. '30' or '30,25,30,25,30,25').",
    )
    parser.add_argument("--min_employees_estimation", type=int, default=1, help="Min employees per period (ratio/formula).")
    parser.add_argument("--base_employees", type=int, default=0, help="Formula: base employees per period.")
    parser.add_argument(
        "--tiers",
        type=str,
        default="50,1,100,2,200,3,inf,4",
        help="Tiers rule: max_cust,emp pairs e.g. '50,1,100,2,inf,4'.",
    )
    # Direct demand (fake) base counts per shift (weekday / weekend)
    # Single int = all days in group; comma-sep = per-day: M_weekday "5,6,5,5,6" = Mon-Fri, M_weekend "4,3" = Sat,Sun
    parser.add_argument("--direct_base_M_weekday", type=str, default="5", help="Base Morning weekdays. Int or Mon,Tue,Wed,Thu,Fri.")
    parser.add_argument("--direct_base_A_weekday", type=str, default="4", help="Base Afternoon weekdays. Int or Mon,Tue,Wed,Thu,Fri.")
    parser.add_argument("--direct_base_M_weekend", type=str, default="4", help="Base Morning weekend. Int or Sat,Sun.")
    parser.add_argument("--direct_base_A_weekend", type=str, default="3", help="Base Afternoon weekend. Int or Sat,Sun.")
    parser.add_argument(
        "--direct_base_by_day",
        type=str,
        default="",
        help="When --store_shifts: 7 groups (Mon-Sun) sep by ;, each N comma-sep values (S1..Sn). E.g. '5,4,3;6,5,4;...'",
    )
    parser.add_argument(
        "--direct_deterministic",
        action="store_true",
        help="Use bases exactly (no random ±1) when --demand=direct.",
    )
    parser.add_argument(
        "--demand_profile",
        type=str,
        default="balanced",
        choices=("balanced", "weekend_heavy", "weekday_heavy"),
        help="Default demand profile for all stores.",
    )
    parser.add_argument(
        "--store_profiles",
        type=str,
        default="",
        help="Per-store profiles: store_A:weekend_heavy,store_B:weekday_heavy. Overrides --demand_profile.",
    )
    # Optimizer constraints
    parser.add_argument("--excess_penalty_M", type=int, default=1, help="Penalty for excess cover (Morning).")
    parser.add_argument("--excess_penalty_A", type=int, default=1, help="Penalty for excess cover (Afternoon).")
    parser.add_argument(
        "--excess_penalty",
        type=str,
        default="",
        help="When --store_shifts: single int or comma-sep per shift (e.g. '1' or '1,1,2,1,1,2').",
    )
    parser.add_argument("--quadratic_excess_penalty", type=int, default=0, help="Square the excess penalty to discourage spikes (e.g. prefer 1,1,1 over 0,0,3).")
    parser.add_argument("--max_shifts_per_week", type=int, default=6, help="Max working shifts per employee per week. Default 6 so min_days_off(1)+max_shifts(6)=7. Use 0 to disable.")
    parser.add_argument("--min_days_off_per_week", type=int, default=None, help="Min rest days per employee per week (Sun–Sat). Default 7 - max_shifts_per_week. Use 0 to disable.")
    parser.add_argument("--max_consecutive_work_days", type=int, default=None, help="Max consecutive working days. Default max_shifts_per_week. Use 0 to disable.")
    parser.add_argument("--max_consecutive_off_days", type=int, default=None, help="Max consecutive off days per employee. None or 0 = disabled.")
    parser.add_argument(
        "--require_consecutive_off",
        action="store_true",
        help="When enabled, min_days_off_per_week must be consecutive (not scattered). Only applies when min_days_off >= 2.",
    )
    parser.add_argument(
        "--sequence_constraints",
        type=str,
        default="",
        help="Sequence constraints: shift:hard_min,soft_min,min_cost,soft_max,hard_max,max_cost. E.g. M:1,1,0,3,4,5. See schedule_test.py.",
    )
    parser.add_argument(
        "--weekly_sum_constraints",
        type=str,
        default="",
        help="Weekly sum constraints: same format. E.g. O:1,2,7,2,3,4 for rest days.",
    )
    parser.add_argument(
        "--max_weekend_work_shifts_women",
        type=int,
        default=None,
        help="Max weekend work shifts per woman per month. None = disabled (no constraint).",
    )
    parser.add_argument(
        "--min_sunday_off_per_month",
        type=int,
        default=1,
        help="Min Sundays off per employee per month. 0 = disabled.",
    )
    parser.add_argument(
        "--min_sunday_off_women",
        type=int,
        default=2,
        help="Min Sundays off per woman per month. 0 = disabled.",
    )
    parser.add_argument(
        "--women_sunday_off_alternate",
        action="store_true",
        default=True,
        help="Women's Sundays off must alternate (no two consecutive). Default True.",
    )
    parser.add_argument(
        "--no_women_sunday_off_alternate",
        action="store_false",
        dest="women_sunday_off_alternate",
        help="Disable alternating constraint for women's Sundays off.",
    )
    parser.add_argument(
        "--spread_sunday_shifts_penalty",
        type=int,
        default=10,
        help="Penalty for imbalance in Sunday shifts across employees. 0 = disabled.",
    )
    parser.add_argument(
        "--spread_shifts_penalty",
        type=int,
        default=0,
        help="Penalty for imbalance in total shifts across employees. 0 = disabled.",
    )
    parser.add_argument(
        "--min_work_days_per_month",
        type=int,
        default=0,
        help="Min work days per employee per month. Used with --min_work_days_penalty. 0 = disabled.",
    )
    parser.add_argument(
        "--min_work_days_penalty",
        type=int,
        default=0,
        help="Penalty per day below min_work_days_per_month. 0 = disabled.",
    )
    parser.add_argument(
        "--target_min_women_ratio",
        type=float,
        default=0.4,
        help="Target min women ratio for hiring recommendations.",
    )
    parser.add_argument(
        "--target_max_women_ratio",
        type=float,
        default=0.6,
        help="Target max women ratio for hiring recommendations.",
    )
    parser.add_argument(
        "--minimize_off_days_penalty",
        type=int,
        default=1,
        help="Penalty per off day for Solution 1 (actual roster). Minimizes days off to maximize utilization. "
        "Set to 0 to disable. Does not affect Solution 2 (optimized roster). Default 1.",
    )
    # Previous schedule (condition new schedule on last realized)
    parser.add_argument(
        "--previous_schedule",
        type=str,
        default="",
        help="JSON file with previous month schedule. Enables stability preference and sequence continuity.",
    )
    parser.add_argument(
        "--output_schedule",
        type=str,
        default="",
        help="Write solved schedule to JSON file (for use as --previous_schedule next month).",
    )
    parser.add_argument(
        "--stability_penalty",
        type=int,
        default=0,
        help="Penalty weight for deviating from previous schedule (when --previous_schedule used). 0 = disabled.",
    )
    # Solver
    parser.add_argument("--params", default="max_time_in_seconds:10.0", help="CP-SAT solver parameters.")
    parser.add_argument("--output_proto", default="", help="Write model proto to this file.")
    parser.add_argument(
        "--debug_sunday_constraints",
        action="store_true",
        help="Log debug info for women alternate Sundays and min Sunday off constraints.",
    )
    parser.add_argument(
        "--fixed_shift",
        choices=("off", "model", "roster"),
        default="off",
        help="Fixed shift constraint: 'off' (disabled), 'model' (solver picks one shift per employee), 'roster' (shift defined in roster 'shift' field).",
    )
    args = parser.parse_args()

    # Dynamic defaults based on max_shifts_per_week
    if args.min_days_off_per_week is None:
        args.min_days_off_per_week = max(0, 7 - args.max_shifts_per_week)
    if args.max_consecutive_work_days is None:
        args.max_consecutive_work_days = args.max_shifts_per_week
    store_ids = [s.strip() for s in args.store_ids.split(",") if s.strip()]
    if not store_ids:
        store_ids = ["store"]

    # Roster: --roster_file overrides --men/--women
    roster_from_file = None
    if args.roster_file:
        roster_from_file = load_roster(args.roster_file)
        args.current_employees = len(roster_from_file)
        if args.current_employees == 0:
            raise ValueError("Roster file must have at least one employee.")

    # Roster composition: --men N --women M sets current_employees and explicit roster (ignored if roster_file)
    has_explicit_roster = (args.men is not None or args.women is not None) and roster_from_file is None
    roster_men = (args.men if args.men is not None else 0) if has_explicit_roster else 0
    roster_women = (args.women if args.women is not None else 0) if has_explicit_roster else 0
    if has_explicit_roster:
        if roster_men < 0 or roster_women < 0:
            raise ValueError("--men and --women must be non-negative.")
        args.current_employees = roster_men + roster_women
        if args.current_employees == 0:
            raise ValueError("Roster must have at least one employee (--men + --women > 0).")

    skip_hire_search = roster_from_file is not None
    # When roster from file, derive men/women counts for hire search when understaffed
    file_roster_men = 0
    file_roster_women = 0
    if roster_from_file is not None:
        file_roster_men = sum(1 for e in roster_from_file if e.get("gender") == "M")
        file_roster_women = len(roster_from_file) - file_roster_men

    # Print all parameters (including defaults)
    print("Parameters:")
    for name in sorted(vars(args)):
        val = getattr(args, name)
        print(f"  --{name}: {val!r}")

    # Build dates: complete calendar weeks covering the month
    dates, primary_start, primary_end = build_complete_week_dates(args.year, args.month)

    # Parse store_shifts: None = use M/A for all; else {store_id: [O, S1, ..., Sn]}
    shifts_by_store = _parse_store_shifts(args.store_shifts, store_ids)
    use_variable_shifts = shifts_by_store is not None

    if use_variable_shifts:
        # Build work_shifts_by_store (without O)
        work_shifts_by_store = {
            sid: [s for s in shifts if s != "O"]
            for sid, shifts in shifts_by_store.items()
        }
    else:
        shifts_by_store = {sid: ["O", "M", "A"] for sid in store_ids}
        work_shifts_by_store = {sid: ["M", "A"] for sid in store_ids}

    if args.demand == "direct":
        if use_variable_shifts:
            base_str = args.direct_base_by_day or "4"
            # Use max work shifts for parsing; truncate per store
            max_shifts = max(len(ws) for ws in work_shifts_by_store.values())
            base_by_day = _parse_direct_base_by_day(base_str, max_shifts)
            demand_by_store = {}
            for store_id in store_ids:
                ws = work_shifts_by_store[store_id]
                # Truncate base_by_day to this store's shift count
                base_truncated = [[row[i] for i in range(len(ws))] for row in base_by_day]
                demand_one = make_fake_demand(
                    dates,
                    args.seed,
                    work_shifts=ws,
                    base_by_day=base_truncated,
                    add_random=not args.direct_deterministic,
                )
                demand_by_store[store_id] = demand_one
        else:
            bm_wd = _parse_base_arg(args.direct_base_M_weekday, 5)
            ba_wd = _parse_base_arg(args.direct_base_A_weekday, 5)
            bm_we = _parse_base_arg(args.direct_base_M_weekend, 2)
            ba_we = _parse_base_arg(args.direct_base_A_weekend, 2)
            store_profiles = _parse_store_profiles(args.store_profiles)
            demand_by_store = {}
            for store_id in store_ids:
                profile = store_profiles.get(store_id, args.demand_profile)
                demand_one = make_fake_demand(
                    dates,
                    args.seed,
                    base_weekday=(bm_wd, ba_wd),
                    base_weekend=(bm_we, ba_we),
                    base_M_weekday=bm_wd,
                    base_A_weekday=ba_wd,
                    base_M_weekend=bm_we,
                    base_A_weekend=ba_we,
                    add_random=not args.direct_deterministic,
                    profile=profile,
                )
                demand_by_store[store_id] = demand_one
    else:
        customer_flow = make_fake_customer_flow(
            dates,
            ["M", "A"] if not use_variable_shifts else list(work_shifts_by_store.values())[0],
            args.seed,
            store_ids=store_ids,
            work_shifts_by_store=work_shifts_by_store if use_variable_shifts else None,
        )
        if use_variable_shifts:
            period_to_shift_by_store = {
                sid: (lambda ws: lambda i: ws[i % len(ws)])(ws)
                for sid, ws in work_shifts_by_store.items()
            }
            kwargs_by_store = {
                sid: {
                    "customers_per_employee": _parse_per_shift_float(
                        args.customers_per_employee, ws, default=30.0
                    ),
                    "min_employees": args.min_employees_estimation,
                }
                for sid, ws in work_shifts_by_store.items()
            }
            if args.rule == "formula":
                for sid in kwargs_by_store:
                    kwargs_by_store[sid]["base_employees"] = args.base_employees
            if args.rule == "ratio":
                demand_by_store = demand_from_estimation_by_store(
                    dates,
                    list(work_shifts_by_store.values())[0],
                    customer_flow,
                    rule="ratio",
                    work_shifts_by_store=work_shifts_by_store,
                    period_to_shift_by_store=period_to_shift_by_store,
                    kwargs_by_store=kwargs_by_store,
                )
            elif args.rule == "tiers":
                demand_by_store = demand_from_estimation_by_store(
                    dates,
                    list(work_shifts_by_store.values())[0],
                    customer_flow,
                    rule="tiers",
                    work_shifts_by_store=work_shifts_by_store,
                    tiers=_parse_tiers(args.tiers),
                )
            else:
                demand_by_store = demand_from_estimation_by_store(
                    dates,
                    list(work_shifts_by_store.values())[0],
                    customer_flow,
                    rule="formula",
                    work_shifts_by_store=work_shifts_by_store,
                    period_to_shift_by_store=period_to_shift_by_store,
                    kwargs_by_store=kwargs_by_store,
                )
        else:
            period_to_shift = lambda i: ["M", "A"][i % 2]
            customers_per_employee = {
                "M": args.customers_per_employee_M,
                "A": args.customers_per_employee_A,
            }
            if args.rule == "ratio":
                demand_by_store = demand_from_estimation_by_store(
                    dates,
                    ["M", "A"],
                    customer_flow,
                    rule="ratio",
                    period_to_shift=period_to_shift,
                    customers_per_employee=customers_per_employee,
                    min_employees=args.min_employees_estimation,
                )
            elif args.rule == "tiers":
                demand_by_store = demand_from_estimation_by_store(
                    dates,
                    ["M", "A"],
                    customer_flow,
                    rule="tiers",
                    tiers=_parse_tiers(args.tiers),
                )
            else:
                demand_by_store = demand_from_estimation_by_store(
                    dates,
                    ["M", "A"],
                    customer_flow,
                    rule="formula",
                    period_to_shift=period_to_shift,
                    customers_per_employee=customers_per_employee,
                    base_employees=args.base_employees,
                    min_employees=args.min_employees_estimation,
                )
        for store_id in demand_by_store:
            ws = work_shifts_by_store.get(store_id, ["M", "A"])
            for d in demand_by_store[store_id]:
                for s in ws:
                    if d.get(s, 0) < 1:
                        d[s] = 1

    sequence_constraints = _parse_constraint_specs(args.sequence_constraints)
    weekly_sum_constraints = _parse_constraint_specs(args.weekly_sum_constraints)

    def _constraints_for_store(store_id: str) -> dict:
        shifts = shifts_by_store[store_id]
        work_shifts = [s for s in shifts if s != "O"]
        if use_variable_shifts:
            excess = _parse_per_shift_int(
                args.excess_penalty, work_shifts, default=1
            )
        else:
            excess = {"M": args.excess_penalty_M, "A": args.excess_penalty_A}
        c = {
            "excess_cover_penalties": excess,
            "quadratic_excess_penalty": args.quadratic_excess_penalty,
            "sequence_constraints": sequence_constraints,
            "weekly_sum_constraints": weekly_sum_constraints,
            "max_shifts_per_week": args.max_shifts_per_week if args.max_shifts_per_week else None,
            "min_days_off_per_week": args.min_days_off_per_week if args.min_days_off_per_week else None,
            "max_consecutive_work_days": args.max_consecutive_work_days if args.max_consecutive_work_days else None,
            "max_consecutive_off_days": args.max_consecutive_off_days if args.max_consecutive_off_days else None,
            "require_consecutive_off": args.require_consecutive_off,
            "max_weekend_work_shifts_women": args.max_weekend_work_shifts_women,
            "min_sunday_off_per_month": args.min_sunday_off_per_month,
            "min_sunday_off_women": args.min_sunday_off_women,
            "women_sunday_off_alternate": args.women_sunday_off_alternate,
            "spread_sunday_shifts_penalty": args.spread_sunday_shifts_penalty,
            "spread_shifts_penalty": args.spread_shifts_penalty,
            "min_work_days_per_month": args.min_work_days_per_month,
            "min_work_days_penalty": args.min_work_days_penalty,
            "debug_sunday_constraints": args.debug_sunday_constraints,
            "fixed_shift_mode": args.fixed_shift if args.fixed_shift != "off" else None,
        }
        if args.previous_schedule:
            try:
                ps, pd = load_previous_schedule(
                    args.previous_schedule, store_id, shifts
                )
                c["previous_schedule"] = ps
                c["previous_dates"] = pd
                c["stability_penalty"] = args.stability_penalty
            except (ValueError, FileNotFoundError) as ex:
                raise ValueError(f"Failed to load --previous_schedule for {store_id}: {ex}") from ex
        return c

    output_schedules = {}
    for store_id in store_ids:
        demand = demand_by_store[store_id]
        shifts = shifts_by_store[store_id]
        work_shifts = [s for s in shifts if s != "O"]
        constraints = _constraints_for_store(store_id)
        validate_demand(demand, dates, shifts)

        min_employees_needed = max(
            sum(demand[d][s] for s in work_shifts) for d in range(len(demand))
        )
        effective_min = max(args.min_employees, min_employees_needed)
        if skip_hire_search:
            if args.current_employees < min_employees_needed:
                print(
                    f"[{store_id}] Demand requires at least {min_employees_needed} employees; "
                    f"roster has {args.current_employees}. Producing understaffed schedule."
                )
        elif effective_min > args.max_employees:
            print(
                f"[{store_id}] Demand requires at least {min_employees_needed} employees; "
                f"--max_employees={args.max_employees} is too low. Raise --max_employees."
            )
            continue

        print(f"\n=== Store: {store_id} ===")
        print(f"Solving current roster: {args.current_employees} employees")
        roster = (
            roster_from_file
            if roster_from_file is not None
            else (
                make_roster_from_composition(roster_men, roster_women)
                if has_explicit_roster
                else make_fake_roster(args.current_employees, args.seed)
            )
        )
        if args.debug_sunday_constraints:
            src = (
                "roster_from_file"
                if roster_from_file is not None
                else (
                    "make_roster_from_composition"
                    if has_explicit_roster
                    else "make_fake_roster"
                )
            )
            women = sum(1 for e in roster if str(e.get("gender", "M")).upper() == "F")
            print(
                f"[DEBUG:sunday] demo: solve current roster, source={src}, "
                f"num_employees={args.current_employees}, women={women}"
            )
        understaffed = False
        relaxed_result = None
        # Solution 1 constraints: add minimize_off_days_penalty to maximize utilization
        constraints_sol1 = {
            **constraints,
            "minimize_off_days_penalty": args.minimize_off_days_penalty,
        }
        try:
            current = solve_once(
                args.current_employees,
                dates,
                shifts,
                demand,
                constraints_sol1,
                args.params,
                args.output_proto,
                write_proto=bool(args.output_proto) and (store_id == store_ids[0]),
                roster=roster,
            )
            if current["status"] not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                understaffed = True
                print(
                    f"[{store_id}] Current roster infeasible:",
                    status_name(current["status"]),
                )
        except ValueError as e:
            understaffed = True
            current = {"status": None}
            print(f"Current roster understaffed: {e}")

        if understaffed:
            relaxed_constraints = {
                **constraints_sol1,
                "relax_cover": True,
                "understaff_penalty": 100,
            }
            if args.debug_sunday_constraints:
                women = sum(1 for e in roster if str(e.get("gender", "M")).upper() == "F")
                print(
                    f"[DEBUG:sunday] demo: solve relaxed (current roster), "
                    f"num_employees={args.current_employees}, women={women}"
                )
            relaxed_result = solve_once(
                args.current_employees,
                dates,
                shifts,
                demand,
                relaxed_constraints,
                args.params,
                args.output_proto,
                write_proto=False,
                roster=roster,
            )
            if relaxed_result["status"] in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                understaff_list = compute_understaff_summary(
                    relaxed_result["solver"],
                    relaxed_result["work"],
                    demand,
                    dates,
                    shifts,
                    args.current_employees,
                )
                total_slots = sum(req - assigned for (_, _, req, assigned) in understaff_list)
                print(
                    f"[{store_id}] Current roster understaffed by {total_slots} in "
                    f"{len(understaff_list)} slot(s):"
                )
                for d, shift_name, required, assigned in understaff_list:
                    day_num = dates[d].day
                    weekday = dates[d].strftime("%a")
                    print(
                        f"  Day {day_num} ({weekday}) {shift_name}: need {required}, have {assigned} — using all {assigned} available"
                    )
        best = None
        best_roster = None

        if constraints.get("fixed_shift_mode") and current["status"] in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            shift_assignments = extract_shift_assignments(
                current["solver"],
                current["work"],
                args.current_employees,
                shifts,
                dates,
                roster,
            )
            constraints["fixed_shift_assignments"] = shift_assignments

        run_roster_search = (
            roster_from_file is not None
            or current["status"] in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        )
        run_iterative_hire = understaffed and roster_from_file is None

        if run_roster_search:
            search_msg = (
                f"[{store_id}] Searching for best roster size (hire more) in "
                if understaffed
                else f"[{store_id}] Searching for best roster size in "
            )
            print(f"{search_msg}[{effective_min}, {args.max_employees}]...")
            for num_employees in range(effective_min, args.max_employees + 1):
                # Solution 2 uses base constraints (no minimize_off_days) for consistent comparison
                if num_employees == args.current_employees:
                    try_roster = roster
                else:
                    try_roster = scale_roster(
                        roster,
                        num_employees,
                        args.seed,
                        args.target_min_women_ratio,
                        args.target_max_women_ratio,
                    )
                if args.debug_sunday_constraints:
                    src = "roster" if num_employees == args.current_employees else "scale_roster"
                    women = sum(
                        1 for e in try_roster if str(e.get("gender", "M")).upper() == "F"
                    )
                    print(
                        f"[DEBUG:sunday] demo: roster_search source={src}, "
                        f"num_employees={num_employees}, women={women}"
                    )
                result = solve_once(
                    num_employees,
                    dates,
                    shifts,
                    demand,
                    constraints,  # Solution 2: no minimize_off_days_penalty
                    args.params,
                    args.output_proto,
                    write_proto=False,
                    roster=try_roster,
                )
                if result["status"] not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                    continue
                if best is None:
                    best = (num_employees, result)
                    best_roster = try_roster
                    continue
                best_employees, best_result = best
                if result["objective"] < best_result["objective"]:
                    best = (num_employees, result)
                    best_roster = try_roster
                elif (
                    result["objective"] == best_result["objective"]
                    and num_employees < best_employees
                ):
                    best = (num_employees, result)
                    best_roster = try_roster
            if (
                best is None
                and understaffed
                and relaxed_result
                and relaxed_result["status"] in (cp_model.OPTIMAL, cp_model.FEASIBLE)
            ):
                best = (args.current_employees, relaxed_result)
                best_roster = roster
        elif run_iterative_hire:
            print(
                f"[{store_id}] Iterative hire: adding one at a time by ratio until feasible..."
            )
            if args.debug_sunday_constraints:
                print(
                    f"[DEBUG:sunday] demo: iterative_hire, starting roster len={len(roster)}"
                )
            working_roster = list(roster)
            while len(working_roster) <= args.max_employees:
                result = solve_once(
                    len(working_roster),
                    dates,
                    shifts,
                    demand,
                    constraints,
                    args.params,
                    args.output_proto,
                    write_proto=False,
                    roster=working_roster,
                )
                if result["status"] in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                    best = (len(working_roster), result)
                    best_roster = working_roster
                    break
                if len(working_roster) >= args.max_employees:
                    break
                gender = recommend_hire_one_gender(
                    working_roster,
                    args.target_min_women_ratio,
                    args.target_max_women_ratio,
                )
                working_roster = working_roster + [
                    {"id": f"emp_{len(working_roster)}", "gender": gender}
                ]
            if (
                best is None
                and relaxed_result
                and relaxed_result["status"] in (cp_model.OPTIMAL, cp_model.FEASIBLE)
            ):
                best = (args.current_employees, relaxed_result)
                best_roster = roster

        if best is None:
            print(
                f"[{store_id}] No feasible schedule between {effective_min} and {args.max_employees}."
            )
            continue

        best_employees, best_result = best
        if best_roster is None:
            best_roster = roster

        # Solution 1: best schedule with current roster (strict or relaxed)
        current_roster_result = None
        if current["status"] in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            current_roster_result = current
        elif relaxed_result and relaxed_result["status"] in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            current_roster_result = relaxed_result

        if current_roster_result is not None:
            sol1_label = (
                f"[{store_id}] Solution 1 (current roster): {args.current_employees} employees"
                + (" – understaffed" if understaffed else "")
            )
            print_solution(
                sol1_label,
                current_roster_result["solver"],
                current_roster_result["work"],
                args.current_employees,
                shifts,
                dates,
                current_roster_result["obj_bool_vars"],
                current_roster_result["obj_bool_coeffs"],
                current_roster_result["obj_int_vars"],
                current_roster_result["obj_int_coeffs"],
                roster=roster,
                primary_start=primary_start,
                primary_end=primary_end,
            )
            # Validate Solution 1
            if constraints.get("women_sunday_off_alternate", True):
                if args.debug_sunday_constraints:
                    print("[DEBUG:sunday] validate Solution 1 (current roster)")
                sol1_violations = validate_women_sunday_alternate(
                    current_roster_result["solver"],
                    current_roster_result["work"],
                    args.current_employees,
                    dates,
                    shifts,
                    roster=roster,
                    debug=args.debug_sunday_constraints,
                )
                if sol1_violations:
                    print(f"[{store_id}] LEGAL RULE VIOLATION in Solution 1 (women alternate Sundays):")
                    for v in sol1_violations:
                        print(f"  ! {v}")
                    print(f"[{store_id}] Refusing to output schedule. Fix the model.")
                    raise SystemExit(1)

        # Solution 2: optimized schedule (best roster from search)
        sol2_label = (
            f"[{store_id}] Solution 2 (optimized): {best_employees} employees"
            + (f" – hire {best_employees - args.current_employees}" if best_employees > args.current_employees else "")
            + (f" – reduce by {args.current_employees - best_employees}" if best_employees < args.current_employees else "")
        )
        print_solution(
            sol2_label,
            best_result["solver"],
            best_result["work"],
            best_employees,
            shifts,
            dates,
            best_result["obj_bool_vars"],
            best_result["obj_bool_coeffs"],
            best_result["obj_int_vars"],
            best_result["obj_int_coeffs"],
            roster=best_roster,
            primary_start=primary_start,
            primary_end=primary_end,
        )
        # Validate legal rule: women cannot be off on two consecutive Sundays (Solution 2)
        if constraints.get("women_sunday_off_alternate", True):
            if args.debug_sunday_constraints:
                print("[DEBUG:sunday] validate Solution 2 (optimized)")
            violations = validate_women_sunday_alternate(
                best_result["solver"],
                best_result["work"],
                best_employees,
                dates,
                shifts,
                roster=best_roster,
                debug=args.debug_sunday_constraints,
            )
            if violations:
                print(f"[{store_id}] LEGAL RULE VIOLATION in Solution 2 (women alternate Sundays):")
                for v in violations:
                    print(f"  ! {v}")
                print(f"[{store_id}] Refusing to output schedule. Fix the model.")
                raise SystemExit(1)
            print(f"[{store_id}] Legal rule: women alternate Sundays OK")
        if best_employees > args.current_employees:
            num_to_hire = best_employees - args.current_employees
            # Identify which employees are new and extract their shifts
            best_assignments = extract_shift_assignments(
                best_result["solver"],
                best_result["work"],
                best_employees,
                shifts,
                dates,
                best_roster,
            )
            new_hires_info = []
            original_ids = {e["id"] for e in roster}
            for e in best_roster:
                if e["id"] not in original_ids:
                    s = best_assignments.get(e["id"], "?")
                    new_hires_info.append(f"{e['gender']} ({s})")

            print(
                f"[{store_id}] Recommendation: hire {num_to_hire} more employee(s) to meet demand (current: {args.current_employees})."
            )
            print(f"[{store_id}] Hires needed: {', '.join(new_hires_info)}")
            n_m_after = sum(1 for e in best_roster if e.get("gender") == "M")
            n_f_after = best_employees - n_m_after
            print(
                f"[{store_id}] Current roster: {file_roster_men if roster_from_file else roster_men} men, "
                f"{file_roster_women if roster_from_file else roster_women} women. "
                f"After hire: {n_m_after} men, {n_f_after} women."
            )
        elif best_employees < args.current_employees:
            print(
                f"[{store_id}] Recommendation: roster can be reduced by "
                f"{args.current_employees - best_employees} (current: {args.current_employees})."
            )
        print(best_result["solver"].response_stats())

        if args.output_schedule:
            output_schedules[store_id] = schedule_to_dict(
                best_result["solver"],
                best_result["work"],
                best_employees,
                shifts,
                dates,
                best_roster,
                store_id=store_id if len(store_ids) > 1 else None,
                primary_start=primary_start,
                primary_end=primary_end,
            )

    if args.output_schedule and output_schedules:
        out_path = Path(args.output_schedule)
        if len(output_schedules) == 1:
            data = list(output_schedules.values())[0]
        else:
            data = {
                "stores": output_schedules,
                "year": dates[0].year,
                "month": dates[0].month,
            }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"\nWrote schedule to {out_path}")


if __name__ == "__main__":
    main()
