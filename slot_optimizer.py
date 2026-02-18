#!/usr/bin/env python3
"""Slot-level workforce optimizer for a monthly horizon.

Assigns each employee a fixed start time for the month, handles shorter
weekend/special-day shifts with next-week hour compensation, and covers
a 20-minute-slot demand table as closely as possible.
"""

import argparse
import calendar
import csv
import datetime as dt
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ortools.sat.python import cp_model

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("slot_optimizer")

# ---------------------------------------------------------------------------
# Constants & Store-type presets
# ---------------------------------------------------------------------------

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

STORE_PRESETS = {
    "street": {"closed_days": [6], "special_days": [5]},       # closed Sun, short Sat
    "mall":   {"closed_days": [],  "special_days": [6]},       # open every day, short Sun only
}

# ---------------------------------------------------------------------------
# Default configuration  (Tier 1 = must provide, Tier 2 = sensible defaults,
#                          Tier 3 = disabled by default)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    # -- Tier 1 (essential – caller must supply) --
    # "demand_path": ...,
    # "roster_path": ...,
    "year": dt.date.today().year,
    "month": dt.date.today().month,

    # -- Tier 2 (active by default) --
    "slot_interval":              20,
    "normal_duration_hours":      8,
    "special_duration_hours":     6,
    "start_window":               ("10:00", "14:00"),
    "start_step":                 None,    # minutes between start positions; None = same as slot_interval
    "store_type":                 "street",
    "max_extra_per_day":          2,       # in slots
    "compensation_carry":         "allow_partial",
    "solver_time_limit":          10,      # seconds

    "relax_cover":                True,
    "understaff_penalty":         100,
    "excess_penalty":             1,
    "minimize_off_days_penalty":  10,

    "min_days_off_per_week":      1,
    "max_consecutive_work_days":  6,
    "max_consecutive_off_days":   3,

    "women_sunday_off_alternate": True,
    "min_sunday_off_per_month":   1,

    "spread_shifts_penalty":      5,
    "spread_sunday_shifts_penalty": 5,

    "min_work_days_per_month":    20,
    "min_work_days_penalty":      10,

    # -- Tier 3 (disabled by default) --
    "closed_days":                None,    # auto from store_type
    "special_days":               None,    # auto from store_type
    "demand_source":              "csv",
    "quadratic_excess_penalty":   0,
    "min_staff_floor":            0,
    "max_shifts_per_week":        None,
    "require_consecutive_off":    False,
    "sequence_constraints":       [],
    "weekly_sum_constraints":     [],
    "max_weekend_work_shifts_women": None,
    "min_sunday_off_women":       0,
    "previous_schedule":          None,
    "previous_dates":             None,
    "stability_penalty":          0,
    "preferred_start_penalty":    0,
    "min_employees":              None,
    "max_employees":              None,
    "demand_spread_penalty":      0,
    "special_day_demand_weight":  1.0,
}


def _merge_config(user: Dict[str, Any]) -> Dict[str, Any]:
    """Return DEFAULT_CONFIG updated with user overrides."""
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(user)
    return cfg


# ---------------------------------------------------------------------------
# Time / slot helpers
# ---------------------------------------------------------------------------

def time_str_to_minutes(t: str) -> int:
    """Parse 'HH:MM' or 'HH:MM:SS' or '09:00:00 AM' into minutes since midnight."""
    t = t.strip()
    # Handle AM/PM format
    upper = t.upper()
    is_pm = "PM" in upper
    is_am = "AM" in upper
    t_clean = upper.replace("AM", "").replace("PM", "").strip()
    parts = t_clean.split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    if is_am or is_pm:
        if is_pm and h != 12:
            h += 12
        if is_am and h == 12:
            h = 0
    return h * 60 + m


def minutes_to_time_str(m: int) -> str:
    """Convert minutes since midnight to 'HH:MM'."""
    return f"{m // 60:02d}:{m % 60:02d}"


def slot_index(minutes: int, slot_interval: int, first_slot_minutes: int) -> int:
    """Convert minutes-since-midnight to a 0-based slot index."""
    return (minutes - first_slot_minutes) // slot_interval


def slot_to_minutes(idx: int, slot_interval: int, first_slot_minutes: int) -> int:
    return first_slot_minutes + idx * slot_interval


# ---------------------------------------------------------------------------
# Demand parsing
# ---------------------------------------------------------------------------

def parse_demand_csv(path: str) -> Tuple[List[List[int]], List[int], int]:
    """Parse a demand CSV file.

    Expected format – first column is time, remaining 7 columns are
    monday..sunday.  Header row required.

    Returns
    -------
    demand : list[list[int]]
        demand[slot_idx][dow]  (dow 0=Mon .. 6=Sun)
    slot_minutes : list[int]
        minutes-since-midnight for each slot row
    slot_interval : int
        detected interval in minutes (from first two rows)
    """
    p = Path(path)
    rows: List[Tuple[int, List[int]]] = []
    with open(p, encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="\t" if "\t" in f.readline() else ",")
        f.seek(0)
        header = next(reader)
        # Normalise header – find day columns
        hdr_lower = [h.strip().lower() for h in header]
        day_cols: List[int] = []
        for dn in DAY_NAMES:
            for ci, h in enumerate(hdr_lower):
                if dn in h:
                    day_cols.append(ci)
                    break
            else:
                raise ValueError(f"Column for {dn} not found in header: {header}")
        for row in reader:
            if not row or not row[0].strip():
                continue
            t_min = time_str_to_minutes(row[0])
            vals = [int(row[c].strip()) for c in day_cols]
            rows.append((t_min, vals))

    rows.sort(key=lambda r: r[0])
    slot_minutes = [r[0] for r in rows]
    demand = [r[1] for r in rows]  # demand[slot][dow]
    detected_interval = (slot_minutes[1] - slot_minutes[0]) if len(slot_minutes) > 1 else 20
    return demand, slot_minutes, detected_interval


def parse_demand_dict(data: Dict[str, Dict[str, int]], slot_interval: int = 20) -> Tuple[List[List[int]], List[int], int]:
    """Parse a demand dict of the form {day_name: {time_str: count}}.

    Returns same tuple as parse_demand_csv.
    """
    all_times: set = set()
    for day, slots in data.items():
        for t in slots:
            all_times.add(time_str_to_minutes(t))
    sorted_times = sorted(all_times)
    time_idx = {m: i for i, m in enumerate(sorted_times)}
    demand: List[List[int]] = [[0] * 7 for _ in sorted_times]
    for day_name, slots in data.items():
        dow = DAY_NAMES.index(day_name.strip().lower())
        for t_str, count in slots.items():
            m = time_str_to_minutes(t_str)
            demand[time_idx[m]][dow] = count
    detected = (sorted_times[1] - sorted_times[0]) if len(sorted_times) > 1 else slot_interval
    return demand, sorted_times, detected


# ---------------------------------------------------------------------------
# Roster loading  (reuses format from store_optimizer_demo.py)
# ---------------------------------------------------------------------------

def load_roster(path: str) -> List[Dict[str, Any]]:
    """Load roster from JSON or CSV.  Returns list of employee dicts."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Roster file not found: {path}")
    suffix = p.suffix.lower()
    if suffix == ".json":
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("JSON roster must be a list of employee objects")
        roster: List[Dict[str, Any]] = []
        seen: set = set()
        for i, row in enumerate(data):
            if not isinstance(row, dict):
                raise ValueError(f"Row {i} must be a dict")
            eid = row.get("id") or f"emp_{i}"
            if eid in seen:
                raise ValueError(f"Duplicate employee id: {eid}")
            seen.add(eid)
            g = str(row.get("gender", "M")).upper()
            if g not in ("M", "F"):
                g = "M"
            entry = {k: v for k, v in row.items() if k not in ("id", "gender")}
            entry["id"] = eid
            entry["gender"] = g
            roster.append(entry)
        return roster
    # CSV
    with open(p, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        roster = []
        seen = set()
        for i, row in enumerate(reader):
            eid = (row.get("id") or "").strip() or f"emp_{i}"
            if eid in seen:
                raise ValueError(f"Duplicate employee id: {eid}")
            seen.add(eid)
            g = str(row.get("gender", "M")).strip().upper()
            if g not in ("M", "F"):
                g = "M"
            roster.append({"id": eid, "gender": g})
        return roster


# ---------------------------------------------------------------------------
# Soft-constraint helpers  (ported from store_staffing_optimizer.py)
# ---------------------------------------------------------------------------

def _negated_bounded_span(
    works: List[cp_model.BoolVarT], start: int, length: int
) -> List[cp_model.BoolVarT]:
    seq: List[cp_model.BoolVarT] = []
    if start > 0:
        seq.append(works[start - 1])
    for i in range(length):
        seq.append(~works[start + i])
    if start + length < len(works):
        seq.append(works[start + length])
    return seq


def add_symmetry_breaking(model: cp_model.CpModel, 
                          roster: List[Dict[str, Any]], 
                          start_val: Dict[int, cp_model.IntVar],
                          works: Dict[Tuple[int, int], cp_model.BoolVarT],
                          num_days: int):
    """
    Force an ordering on employees that have identical attributes.
    This prevents the solver from exploring permutations of the same schedule.
    """
    # Group employees by attributes that define their "type"
    groups = {}
    for e, emp in enumerate(roster):
        key = (emp.get("gender"), emp.get("preferred_start"))
        groups.setdefault(key, []).append(e)

    for key, indices in groups.items():
        indices.sort()
        
        # Apply constraints between adjacent identical employees
        for i in range(len(indices) - 1):
            e1 = indices[i]
            e2 = indices[i+1]
            
            # 1. Force Start Time Ordering: e1 starts earlier or same time as e2
            model.add(start_val[e1] <= start_val[e2])
            
            # 2. Tie-breaker: If start times are equal, force e1 to work 
            #    at least as many days as e2.
            
            # Create a boolean variable that is TRUE if start times are equal
            starts_equal = model.new_bool_var(f"seq_{e1}_{e2}")
            
            # Link the boolean to the math:
            # If starts_equal is TRUE, then values must be equal
            model.add(start_val[e1] == start_val[e2]).only_enforce_if(starts_equal)
            
            # If starts_equal is FALSE, then values must be NOT equal
            # (Since we already forced <=, NOT equal implies <)
            model.add(start_val[e1] < start_val[e2]).only_enforce_if(~starts_equal)

            # Calculate total work days
            work_sum_1 = sum(works[e1, d] for d in range(num_days))
            work_sum_2 = sum(works[e2, d] for d in range(num_days))
            
            # Enforce the work sum ordering ONLY if start times are equal
            model.add(work_sum_1 >= work_sum_2).only_enforce_if(starts_equal)
            
def add_soft_sequence_constraint(
    model: cp_model.CpModel,
    works: List[cp_model.BoolVarT],
    hard_min: int, soft_min: int, min_cost: int,
    soft_max: int, hard_max: int, max_cost: int,
    prefix: str,
) -> Tuple[List[cp_model.BoolVarT], List[int]]:
    cost_lits: List[cp_model.BoolVarT] = []
    cost_coeffs: List[int] = []
    for length in range(1, hard_min):
        for s in range(len(works) - length + 1):
            model.add_bool_or(_negated_bounded_span(works, s, length))
    if min_cost > 0:
        for length in range(hard_min, soft_min):
            for s in range(len(works) - length + 1):
                span = _negated_bounded_span(works, s, length)
                lit = model.new_bool_var(f"{prefix}:under({s},{length})")
                span.append(lit)
                model.add_bool_or(span)
                cost_lits.append(lit)
                cost_coeffs.append(min_cost * (soft_min - length))
    if max_cost > 0:
        for length in range(soft_max + 1, hard_max + 1):
            for s in range(len(works) - length + 1):
                span = _negated_bounded_span(works, s, length)
                lit = model.new_bool_var(f"{prefix}:over({s},{length})")
                span.append(lit)
                model.add_bool_or(span)
                cost_lits.append(lit)
                cost_coeffs.append(max_cost * (length - soft_max))
    for s in range(len(works) - hard_max):
        model.add_bool_or([~works[i] for i in range(s, s + hard_max + 1)])
    return cost_lits, cost_coeffs


def add_soft_sum_constraint(
    model: cp_model.CpModel,
    works: List[cp_model.BoolVarT],
    hard_min: int, soft_min: int, min_cost: int,
    soft_max: int, hard_max: int, max_cost: int,
    prefix: str,
) -> Tuple[List[cp_model.IntVar], List[int]]:
    cost_vars: List[cp_model.IntVar] = []
    cost_coeffs: List[int] = []
    sum_var = model.new_int_var(hard_min, hard_max, "")
    model.add(sum_var == sum(works))
    if soft_min > hard_min and min_cost > 0:
        delta = model.new_int_var(-len(works), len(works), "")
        model.add(delta == soft_min - sum_var)
        excess = model.new_int_var(0, len(works), f"{prefix}:under_sum")
        model.add_max_equality(excess, [delta, model.new_constant(0)])
        cost_vars.append(excess)
        cost_coeffs.append(min_cost)
    if soft_max < hard_max and max_cost > 0:
        delta = model.new_int_var(-len(works), len(works), "")
        model.add(delta == sum_var - soft_max)
        excess = model.new_int_var(0, len(works), f"{prefix}:over_sum")
        model.add_max_equality(excess, [delta, model.new_constant(0)])
        cost_vars.append(excess)
        cost_coeffs.append(max_cost)
    return cost_vars, cost_coeffs


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------

def build_dates(year: int, month: int) -> List[dt.date]:
    """Build dates for exactly the given month (no week completion)."""
    n = calendar.monthrange(year, month)[1]
    return [dt.date(year, month, d) for d in range(1, n + 1)]


def build_complete_week_dates(
    year: int, month: int,
) -> Tuple[List[dt.date], int, int]:
    """Build dates for complete Mon-Sun calendar weeks covering the month.

    Returns
    -------
    dates : list[dt.date]
        From Monday of the week containing the 1st to Sunday of the week
        containing the last day of the month.
    primary_start : int
        Index of the first day of the target month in *dates*.
    primary_end : int
        Index **after** the last day of the target month (exclusive).
    """
    first_day = dt.date(year, month, 1)
    _, num_days = calendar.monthrange(year, month)
    last_day = dt.date(year, month, num_days)

    # Monday of the week containing the first day
    start = first_day - dt.timedelta(days=first_day.weekday())
    # Sunday of the week containing the last day
    end = last_day + dt.timedelta(days=6 - last_day.weekday())

    dates: List[dt.date] = []
    current = start
    while current <= end:
        dates.append(current)
        current += dt.timedelta(days=1)

    primary_start = (first_day - start).days
    primary_end = primary_start + num_days
    return dates, primary_start, primary_end


def calendar_weeks(dates: List[dt.date]) -> List[List[int]]:
    """Group date indices into Mon-Sun calendar weeks."""
    weeks: Dict[int, List[int]] = {}
    for i, d in enumerate(dates):
        monday = d - dt.timedelta(days=d.weekday())
        key = monday.toordinal()
        weeks.setdefault(key, []).append(i)
    return [weeks[k] for k in sorted(weeks)]


# ---------------------------------------------------------------------------
# CORE: build the CP-SAT model
# ---------------------------------------------------------------------------

def build_slot_model(
    roster: List[Dict[str, Any]],
    demand: List[List[int]],       # demand[slot_idx][dow]
    slot_minutes: List[int],       # minutes-since-midnight per slot row
    dates: List[dt.date],
    cfg: Dict[str, Any],
) -> Tuple[cp_model.CpModel, Dict[str, Any]]:
    """Build and return (model, variables_dict)."""

    interval = cfg["slot_interval"]
    num_employees = len(roster)
    num_days = len(dates)
    num_slots = len(slot_minutes)
    first_slot_min = slot_minutes[0]

    logger.info("build_slot_model: %d employees, %d days, %d slots/day, interval=%d min",
                num_employees, num_days, num_slots, interval)

    # Primary month range (complete weeks may include padding days)
    primary_start = cfg.get("primary_start", 0)
    primary_end = cfg.get("primary_end", num_days)
    logger.debug("Primary month range: days [%d, %d) (%d month days)",
                 primary_start, primary_end, primary_end - primary_start)

    # Resolve store type -> closed / special days
    preset = STORE_PRESETS.get(cfg["store_type"], STORE_PRESETS["street"])
    closed_dows = cfg["closed_days"] if cfg["closed_days"] is not None else preset["closed_days"]
    special_dows = cfg["special_days"] if cfg["special_days"] is not None else preset["special_days"]
    logger.info("Store type=%s  closed_dows=%s  special_dows=%s",
                cfg["store_type"],
                [DAY_NAMES[d] for d in closed_dows],
                [DAY_NAMES[d] for d in special_dows])

    # Duration in slots
    normal_dur = int(cfg["normal_duration_hours"] * 60 / interval)
    special_dur = int(cfg["special_duration_hours"] * 60 / interval)
    deficit_per_special = normal_dur - special_dur
    max_extra = cfg["max_extra_per_day"]
    logger.info("Durations: normal=%d slots (%gh), special=%d slots (%gh), "
                "deficit_per_special=%d, max_extra=%d",
                normal_dur, cfg["normal_duration_hours"],
                special_dur, cfg["special_duration_hours"],
                deficit_per_special, max_extra)

    # Start window -> slot indices
    sw_start_min = time_str_to_minutes(cfg["start_window"][0])
    sw_end_min = time_str_to_minutes(cfg["start_window"][1])
    start_step = cfg.get("start_step") or interval
    possible_starts_min = list(range(sw_start_min, sw_end_min + 1, start_step))
    possible_start_slots = [
        slot_index(m, interval, first_slot_min)
        for m in possible_starts_min
    ]
    # Filter to valid range (start + max possible duration must not exceed last slot + 1)
    max_dur = normal_dur + max_extra
    possible_start_slots = [
        s for s in possible_start_slots
        if 0 <= s and s + special_dur <= num_slots  # at minimum, special dur must fit
    ]
    if not possible_start_slots:
        raise ValueError("No valid start positions found. Check start_window and slot range.")
    logger.info("Start window: %s-%s (step %dmin) → %d possible start slots %s",
                cfg["start_window"][0], cfg["start_window"][1], start_step,
                len(possible_start_slots), possible_start_slots)

    # Day classification
    def is_closed(d_idx: int) -> bool:
        return dates[d_idx].weekday() in closed_dows

    def is_special(d_idx: int) -> bool:
        return dates[d_idx].weekday() in special_dows

    def is_weekday_normal(d_idx: int) -> bool:
        return not is_closed(d_idx) and not is_special(d_idx)

    # Day classification summary
    closed_days_count = sum(1 for d in range(num_days) if is_closed(d))
    special_days_count = sum(1 for d in range(num_days) if is_special(d))
    normal_days_count = sum(1 for d in range(num_days) if is_weekday_normal(d))
    logger.debug("Day classification: %d closed, %d special, %d normal (of %d total)",
                 closed_days_count, special_days_count, normal_days_count, num_days)

    # For each special DOW, find the first slot with non-zero demand.
    # On special days every employee starts at this fixed slot.
    special_start_slot_by_dow: Dict[int, int] = {}
    for dow in special_dows:
        for t in range(num_slots):
            if t < len(demand) and dow < len(demand[t]) and demand[t][dow] > 0:
                special_start_slot_by_dow[dow] = t
                break
        else:
            # Fallback: use first possible start slot
            special_start_slot_by_dow[dow] = possible_start_slots[0]
    for dow, ss in special_start_slot_by_dow.items():
        logger.debug("Special day %s: fixed start slot=%d (%s)",
                     DAY_NAMES[dow], ss, minutes_to_time_str(slot_to_minutes(ss, interval, first_slot_min)))

    # -----------------------------------------------------------------------
    logger.info("Building CP-SAT model …")
    model = cp_model.CpModel()
    obj_int_vars: List[cp_model.IntVar] = []
    obj_int_coeffs: List[float] = []
    obj_bool_vars: List[cp_model.BoolVarT] = []
    obj_bool_coeffs: List[int] = []

    # -- Decision variables --

    # start_at[e, s] : boolean – employee e starts at slot position s
    starts_at: Dict[Tuple[int, int], cp_model.BoolVarT] = {}
    start_val: Dict[int, cp_model.IntVar] = {}  # integer version
    for e in range(num_employees):
        for s in possible_start_slots:
            starts_at[e, s] = model.new_bool_var(f"sa_{e}_{s}")
        model.add_exactly_one(starts_at[e, s] for s in possible_start_slots)
        # Integer start variable (derived)
        start_val[e] = model.new_int_var(
            min(possible_start_slots), max(possible_start_slots), f"start_{e}"
        )
        model.add(
            start_val[e] == sum(s * starts_at[e, s] for s in possible_start_slots)
        )

    logger.debug("Created %d start_at booleans + %d start_val ints",
                 len(starts_at), len(start_val))

    # works[e, d] : boolean – employee works on day d
    works: Dict[Tuple[int, int], cp_model.BoolVarT] = {}
    for e in range(num_employees):
        for d in range(num_days):
            works[e, d] = model.new_bool_var(f"w_{e}_{d}")
            # Force off on closed days
            if is_closed(d):
                model.add(works[e, d] == 0)

    logger.debug("Created %d works booleans", len(works))

    # extra[e, d] : int – extra compensation slots on normal weekdays
    extra: Dict[Tuple[int, int], cp_model.IntVar] = {}
    for e in range(num_employees):
        for d in range(num_days):
            if is_weekday_normal(d):
                extra[e, d] = model.new_int_var(0, max_extra, f"ex_{e}_{d}")
                # extra can only be > 0 if working
                model.add(extra[e, d] == 0).only_enforce_if(~works[e, d])
            else:
                # On special / closed days: no extra
                extra[e, d] = model.new_constant(0)

    logger.debug("Created %d extra vars (non-constant)",
                 sum(1 for v in extra.values() if not isinstance(v, int)))

    # -- Coverage model (efficient formulation) --
    # Instead of per-slot-per-employee booleans, we use ws[e, s, d]:
    # "employee e works on day d AND has start position s"
    # This is reusable across all slots – coverage at slot t is just a sum of ws.

    # ws[e, s, d] = works[e, d] AND starts_at[e, s]
    # On special days all employees share a fixed start slot (first non-zero
    # demand slot for that DOW), so ws is just works[e, d].
    ws: Dict[Tuple[int, int, int], cp_model.BoolVarT] = {}
    for e in range(num_employees):
        for d in range(num_days):
            if is_closed(d):
                continue
            if is_special(d):
                # Fixed start for all employees on special days
                dow = dates[d].weekday()
                s = special_start_slot_by_dow[dow]
                v = model.new_bool_var(f"ws_{e}_{s}_{d}")
                ws[e, s, d] = v
                model.add(v == works[e, d])
            else:
                for s in possible_start_slots:
                    v = model.new_bool_var(f"ws_{e}_{s}_{d}")
                    ws[e, s, d] = v
                    # Linearize AND: v = works[e,d] AND starts_at[e,s]
                    model.add(v <= works[e, d])
                    model.add(v <= starts_at[e, s])
                    model.add(v >= works[e, d] + starts_at[e, s] - 1)

    logger.debug("Created %d ws (work-start) booleans", len(ws))

    # extra_ge[e, d, k] : boolean – extra[e,d] >= k (for k=1..max_extra)
    extra_ge: Dict[Tuple[int, int, int], cp_model.BoolVarT] = {}
    for e in range(num_employees):
        for d in range(num_days):
            if is_weekday_normal(d):
                for k in range(1, max_extra + 1):
                    extra_ge[e, d, k] = model.new_bool_var(f"exge_{e}_{d}_{k}")
                    model.add(extra[e, d] >= k).only_enforce_if(extra_ge[e, d, k])
                    model.add(extra[e, d] < k).only_enforce_if(~extra_ge[e, d, k])

    # Precompute: for each (slot t, day_type), which start positions provide
    # base coverage vs. extra coverage
    def _valid_base_starts(t: int, base_dur: int) -> List[int]:
        """Return starts that cover slot t within base duration."""
        return [s for s in possible_start_slots if s <= t < s + base_dur]

    def _valid_extra_starts(t: int, base_dur: int) -> Dict[int, List[int]]:
        """Return {k: [starts]} where start s needs extra >= k to cover slot t."""
        result: Dict[int, List[int]] = {}
        for s in possible_start_slots:
            offset = t - s
            if base_dur <= offset < base_dur + max_extra:
                k = offset - base_dur + 1
                result.setdefault(k, []).append(s)
        return result

    # For extra coverage, create ws_extra[e, s, d, k] = ws[e,s,d] AND extra_ge[e,d,k]
    # Only needed for (s, t) where t is in extra range of start s
    ws_extra: Dict[Tuple[int, int, int, int], cp_model.BoolVarT] = {}
    # Track which (e, s, d, k) combos are needed
    needed_ws_extra: set = set()
    for t in range(num_slots):
        extra_s = _valid_extra_starts(t, normal_dur)
        for k, s_list in extra_s.items():
            for s in s_list:
                for e in range(num_employees):
                    for d in range(num_days):
                        if is_weekday_normal(d):
                            needed_ws_extra.add((e, s, d, k))

    logger.debug("Created %d extra_ge booleans, %d ws_extra needed",
                 len(extra_ge), len(needed_ws_extra))

    for (e, s, d, k) in needed_ws_extra:
        v = model.new_bool_var(f"wsx_{e}_{s}_{d}_{k}")
        ws_extra[e, s, d, k] = v
        model.add(v <= ws[e, s, d])
        model.add(v <= extra_ge[e, d, k])
        model.add(v >= ws[e, s, d] + extra_ge[e, d, k] - 1)

    # -- Coverage aggregation & demand constraints --
    coverage_var: Dict[Tuple[int, int], cp_model.IntVar] = {}
    for d in range(num_days):
        if is_closed(d):
            for t in range(num_slots):
                coverage_var[d, t] = model.new_constant(0)
            continue
        dow = dates[d].weekday()
        is_spec = is_special(d)
        base_dur = special_dur if is_spec else normal_dur

        for t in range(num_slots):
            # Base coverage: sum ws[e, s, d] for valid base starts
            base_starts = _valid_base_starts(t, base_dur)
            base_terms = [
                ws[e, s, d]
                for e in range(num_employees)
                for s in base_starts
                if (e, s, d) in ws
            ]

            # Extra coverage (only on normal weekdays)
            extra_terms = []
            if is_weekday_normal(d):
                extra_s = _valid_extra_starts(t, normal_dur)
                for k, s_list in extra_s.items():
                    for s in s_list:
                        for e in range(num_employees):
                            key = (e, s, d, k)
                            if key in ws_extra:
                                extra_terms.append(ws_extra[key])

            all_terms = base_terms + extra_terms
            if not all_terms:
                coverage_var[d, t] = model.new_constant(0)
            else:
                cov_sum = model.new_int_var(0, num_employees, f"cov_{d}_{t}")
                model.add(cov_sum == sum(all_terms))
                coverage_var[d, t] = cov_sum

            req = demand[t][dow] if t < len(demand) and dow < len(demand[t]) else 0
            floor = cfg.get("min_staff_floor", 0)
            effective_req = max(req, floor) if not is_closed(d) else 0

            cov_var = coverage_var[d, t]
            # Check if this is a constant 0 (no possible coverage at this slot)
            is_const_zero = not all_terms if not is_closed(d) else True
            if is_const_zero:
                if effective_req > 0 and cfg["relax_cover"]:
                    us = model.new_constant(effective_req)
                    obj_int_vars.append(us)
                    obj_int_coeffs.append(cfg["understaff_penalty"])
                continue

            if cfg["relax_cover"] and effective_req > 0:
                understaff = model.new_int_var(0, effective_req, f"us_{d}_{t}")
                model.add(understaff >= effective_req - cov_var)
                obj_int_vars.append(understaff)
                obj_int_coeffs.append(cfg["understaff_penalty"])
            elif effective_req > 0:
                model.add(cov_var >= effective_req)

            ep = cfg.get("excess_penalty", 0)
            qep = cfg.get("quadratic_excess_penalty", 0)
            if (ep > 0 or qep > 0) and not is_closed(d):
                excess = model.new_int_var(0, num_employees, f"ov_{d}_{t}")
                model.add(excess >= cov_var - max(req, 0))
                if ep > 0:
                    obj_int_vars.append(excess)
                    obj_int_coeffs.append(ep)
                if qep > 0:
                    excess_sq = model.new_int_var(0, num_employees ** 2, f"ovsq_{d}_{t}")
                    model.add_multiplication_equality(excess_sq, [excess, excess])
                    obj_int_vars.append(excess_sq)
                    obj_int_coeffs.append(qep)

    logger.info("Coverage model built: %d coverage vars, %d objective terms so far "
                "(int=%d, bool=%d)",
                len(coverage_var), len(obj_int_vars) + len(obj_bool_vars),
                len(obj_int_vars), len(obj_bool_vars))

    # -----------------------------------------------------------------------
    # Weekly hour-compensation constraint
    # -----------------------------------------------------------------------
    weeks = calendar_weeks(dates)
    logger.debug("Calendar has %d weeks", len(weeks))
    for e in range(num_employees):
        # Track which weeks' weekdays receive compensation (from previous week's deficit)
        compensated_weeks: set = set()  # week indices whose weekdays get compensation

        # First pass: set up deficit -> next-week compensation
        for wi, week_days in enumerate(weeks):
            special_worked: List[cp_model.BoolVarT] = [
                works[e, d] for d in week_days if is_special(d)
            ]
            if not special_worked:
                continue

            deficit = model.new_int_var(
                0, len(special_worked) * deficit_per_special,
                f"def_{e}_{wi}"
            )
            model.add(deficit == deficit_per_special * sum(special_worked))

            next_wi = wi + 1
            if next_wi < len(weeks):
                next_weekdays = [d for d in weeks[next_wi] if is_weekday_normal(d)]
                if next_weekdays:
                    compensated_weeks.add(next_wi)
                    comp_terms = [
                        extra[e, d] for d in next_weekdays
                        if not isinstance(extra[e, d], int)
                    ]
                    if comp_terms:
                        max_cap = len(comp_terms) * max_extra
                        if cfg["compensation_carry"] == "allow_partial":
                            capped = model.new_int_var(
                                0,
                                min(max_cap, len(special_worked) * deficit_per_special),
                                f"cap_{e}_{wi}",
                            )
                            model.add_min_equality(
                                capped,
                                [deficit, model.new_constant(max_cap)],
                            )
                            model.add(sum(comp_terms) == capped)
                        else:
                            model.add(sum(comp_terms) == deficit)
                    # else: no valid extra vars, deficit must be 0
                else:
                    if cfg["compensation_carry"] != "allow_partial":
                        for d in week_days:
                            if is_special(d):
                                model.add(works[e, d] == 0)
            else:
                # Last week of month
                if cfg["compensation_carry"] == "forbid_special_last_week":
                    for d in week_days:
                        if is_special(d):
                            model.add(works[e, d] == 0)

        # Second pass: zero out extras for weeks NOT receiving compensation
        for wi, week_days in enumerate(weeks):
            if wi in compensated_weeks:
                continue
            # Also skip if this is week 0 and we have previous_schedule compensation
            if wi == 0 and cfg.get("previous_schedule") is not None:
                continue
            for d in week_days:
                if is_weekday_normal(d) and not isinstance(extra[e, d], int):
                    model.add(extra[e, d] == 0)

    # -----------------------------------------------------------------------
    # Constraints carried from old optimizer
    # -----------------------------------------------------------------------

    # --- Min days off per week ---
    min_off = cfg.get("min_days_off_per_week")
    if min_off and min_off > 0:
        for week_days in weeks:
            non_closed_in_week = [d for d in week_days if not is_closed(d)]
            closed_in_week = len(week_days) - len(non_closed_in_week)
            # Skip partial weeks where enforcing min_off would prevent all work
            if len(non_closed_in_week) <= min_off:
                continue
            for e in range(num_employees):
                off_count = sum(1 - works[e, d] for d in non_closed_in_week)
                model.add(off_count + closed_in_week >= min_off)

    # --- Max shifts per week ---
    max_spw = cfg.get("max_shifts_per_week")
    if max_spw is not None and max_spw > 0:
        for week_days in weeks:
            for e in range(num_employees):
                model.add(sum(works[e, d] for d in week_days) <= max_spw)

    # --- Max consecutive work days ---
    max_consec = cfg.get("max_consecutive_work_days")
    if max_consec and max_consec > 0:
        for e in range(num_employees):
            working_seq = [works[e, d] for d in range(num_days)]
            for start_idx in range(num_days - max_consec):
                model.add_bool_or(
                    [~working_seq[i] for i in range(start_idx, start_idx + max_consec + 1)]
                )

    # --- Max consecutive off days ---
    max_consec_off = cfg.get("max_consecutive_off_days")
    if max_consec_off and max_consec_off > 0:
        for e in range(num_employees):
            working_seq = [works[e, d] for d in range(num_days)]
            for start_idx in range(num_days - max_consec_off):
                model.add_bool_or(
                    [working_seq[i] for i in range(start_idx, start_idx + max_consec_off + 1)]
                )

    # --- Require consecutive off ---
    require_consec_off = cfg.get("require_consecutive_off", False)
    min_consec_off = cfg.get("min_days_off_per_week", 0) or 0
    if require_consec_off and min_consec_off >= 2:
        num_states = min_consec_off + 1
        transition_triples = []
        for state in range(num_states):
            for symbol in [0, 1]:  # 0=work, 1=off
                if state == min_consec_off:
                    next_state = min_consec_off
                elif symbol == 0:
                    next_state = 0
                else:
                    next_state = state + 1
                transition_triples.append((state, symbol, next_state))
        for e in range(num_employees):
            off_seq = []
            for d in range(num_days):
                off_var = model.new_bool_var(f"off_{e}_{d}")
                model.add(off_var == 1).only_enforce_if(~works[e, d])
                model.add(off_var == 0).only_enforce_if(works[e, d])
                off_seq.append(off_var)
            for ws in range(0, num_days - 6):
                window = off_seq[ws:ws + 7]
                if len(window) == 7:
                    model.add_automaton(
                        window, 0, [min_consec_off], transition_triples
                    )

    # --- Sunday constraints ---
    # All Sundays (for alternation constraint that spans complete weeks)
    all_sunday_indices = [d for d in range(num_days) if dates[d].weekday() == 6]
    # Primary Sundays only (for monthly count constraints)
    sunday_indices = [d for d in range(primary_start, primary_end)
                      if dates[d].weekday() == 6]

    min_sun_off = cfg.get("min_sunday_off_per_month", 0)
    if min_sun_off > 0 and sunday_indices:
        for e in range(num_employees):
            # Count Sundays off within the primary month only
            sun_off = sum(1 - works[e, d] for d in sunday_indices)
            model.add(sun_off >= min_sun_off)

    # Min Sunday off for women (primary month only)
    min_sun_off_w = cfg.get("min_sunday_off_women", 0)
    if min_sun_off_w > 0 and sunday_indices:
        for e in range(num_employees):
            if roster[e].get("gender") == "F":
                sun_off = sum(1 - works[e, d] for d in sunday_indices)
                model.add(sun_off >= min_sun_off_w)

    # Women Sunday alternation (uses ALL Sundays for correct cross-week logic)
    if cfg.get("women_sunday_off_alternate", True) and len(all_sunday_indices) >= 2:
        for e in range(num_employees):
            if roster[e].get("gender") == "F":
                for i in range(len(all_sunday_indices) - 1):
                    s1, s2 = all_sunday_indices[i], all_sunday_indices[i + 1]
                    # At least one of the two Sundays must be off
                    model.add(works[e, s1] + works[e, s2] <= 1)

    # Max weekend work for women (primary month only)
    max_ww = cfg.get("max_weekend_work_shifts_women")
    if max_ww is not None:
        weekend_days = [d for d in range(primary_start, primary_end)
                        if dates[d].weekday() >= 5]
        for e in range(num_employees):
            if roster[e].get("gender") == "F":
                model.add(sum(works[e, d] for d in weekend_days) <= max_ww)

    # --- Sequence constraints ---
    for ct in cfg.get("sequence_constraints", []):
        hard_min_v, soft_min_v, min_cost_v, soft_max_v, hard_max_v, max_cost_v = ct
        for e in range(num_employees):
            working_seq = [works[e, d] for d in range(num_days)]
            lits, coeffs = add_soft_sequence_constraint(
                model, working_seq,
                hard_min_v, soft_min_v, min_cost_v,
                soft_max_v, hard_max_v, max_cost_v,
                f"seq(e{e})",
            )
            obj_bool_vars.extend(lits)
            obj_bool_coeffs.extend(coeffs)

    # --- Weekly sum constraints ---
    for ct in cfg.get("weekly_sum_constraints", []):
        hard_min_v, soft_min_v, min_cost_v, soft_max_v, hard_max_v, max_cost_v = ct
        for week_days in weeks:
            for e in range(num_employees):
                wk_works = [works[e, d] for d in week_days]
                if not wk_works:
                    continue
                ivars, icoeffs = add_soft_sum_constraint(
                    model, wk_works,
                    hard_min_v, soft_min_v, min_cost_v,
                    soft_max_v, hard_max_v, max_cost_v,
                    f"wsum(e{e})",
                )
                obj_int_vars.extend(ivars)
                obj_int_coeffs.extend(icoeffs)

    # --- Spread Sunday shifts (primary month Sundays only) ---
    spread_sun = cfg.get("spread_sunday_shifts_penalty", 0)
    if spread_sun > 0 and sunday_indices:
        sun_work: List[cp_model.IntVar] = []
        for e in range(num_employees):
            sw = model.new_int_var(0, len(sunday_indices), f"sunw_{e}")
            model.add(sw == sum(works[e, d] for d in sunday_indices))
            sun_work.append(sw)
        max_sw = model.new_int_var(0, len(sunday_indices), "max_sunw")
        min_sw = model.new_int_var(0, len(sunday_indices), "min_sunw")
        model.add_max_equality(max_sw, sun_work)
        model.add_min_equality(min_sw, sun_work)
        spread_var = model.new_int_var(0, len(sunday_indices), "spr_sun")
        model.add(spread_var == max_sw - min_sw)
        obj_int_vars.append(spread_var)
        obj_int_coeffs.append(spread_sun)

    # --- Spread total shifts (primary month days only) ---
    spread_tot = cfg.get("spread_shifts_penalty", 0)
    num_primary = primary_end - primary_start
    if spread_tot > 0:
        total_work: List[cp_model.IntVar] = []
        for e in range(num_employees):
            tw = model.new_int_var(0, num_primary, f"tw_{e}")
            model.add(tw == sum(works[e, d] for d in range(primary_start, primary_end)))
            total_work.append(tw)
        max_tw = model.new_int_var(0, num_primary, "max_tw")
        min_tw = model.new_int_var(0, num_primary, "min_tw")
        model.add_max_equality(max_tw, total_work)
        model.add_min_equality(min_tw, total_work)
        spr = model.new_int_var(0, num_primary, "spr_tot")
        model.add(spr == max_tw - min_tw)
        obj_int_vars.append(spr)
        obj_int_coeffs.append(spread_tot)

    # --- Demand-proportional daily staffing spread ---
    demand_spread_pen = cfg.get("demand_spread_penalty", 0)
    if demand_spread_pen > 0:
        special_demand_wt = cfg.get("special_day_demand_weight", 1.0)

        # 1. Compute per-DOW demand totals from the demand table
        num_dow = len(demand[0]) if demand else 7
        dow_demand = [0] * num_dow
        for s_idx in range(num_slots):
            for dow in range(num_dow):
                dow_demand[dow] += demand[s_idx][dow]

        # 2. Collect active (non-closed, demand > 0) days with raw weights
        active_days_raw: List[Tuple[int, float]] = []
        for d in range(primary_start, primary_end):
            if is_closed(d):
                continue
            dow = dates[d].weekday()
            w = float(dow_demand[dow])
            if w <= 0:
                continue
            if is_special(d):
                w *= special_demand_wt
            active_days_raw.append((d, w))

        if len(active_days_raw) >= 2:
            # 3. Normalize weights so they sum to ≈ 100
            #    This keeps the penalty scale comparable to other per-person penalties.
            total_raw = sum(w for _, w in active_days_raw)
            norm_days: List[Tuple[int, int]] = [
                (d, max(1, round(100.0 * w / total_raw)))
                for d, w in active_days_raw
            ]
            W = sum(w for _, w in norm_days)

            # 4. Build staff-count variables for each active day
            staff_dp: Dict[int, cp_model.IntVar] = {}
            for d, _w in norm_days:
                sv = model.new_int_var(0, num_employees, f"dstaff_dp_{d}")
                model.add(sv == sum(works[e, d] for e in range(num_employees)))
                staff_dp[d] = sv

            total_staff_dp = model.new_int_var(
                0, num_employees * len(norm_days), "total_staff_dp"
            )
            model.add(total_staff_dp == sum(staff_dp[d] for d, _ in norm_days))

            # 5. For each day d, penalize |W * staff_d - w_d * total_staff|.
            #    When staff is distributed proportionally to demand, this
            #    expression equals zero.  The penalty is in "demand-weighted
            #    person" units; with W ≈ 100 the effective cost per person
            #    of deviation is ≈ penalty × W.
            max_dev = W * num_employees
            for d, w in norm_days:
                dev = model.new_int_var(0, max_dev, f"dpdev_{d}")
                # expr = W * staff_d[d] - w * total_staff
                model.add(dev >= W * staff_dp[d] - w * total_staff_dp)
                model.add(dev >= w * total_staff_dp - W * staff_dp[d])
                obj_int_vars.append(dev)
                obj_int_coeffs.append(demand_spread_pen)

            logger.info(
                "Demand-proportional spread: %d active days, W=%d, "
                "special_demand_weight=%.2f, penalty=%d",
                len(norm_days), W, special_demand_wt, demand_spread_pen,
            )
            for d, w in norm_days:
                logger.debug(
                    "  Day %d (%s %s): norm_weight=%d (%.1f%%)",
                    d, dates[d].strftime("%a"), dates[d].isoformat(),
                    w, 100.0 * w / W,
                )
        else:
            logger.debug("Demand-proportional spread: skipped, <2 active days")

    # --- Min work days per month (primary month days only) ---
    mwd = cfg.get("min_work_days_per_month", 0)
    mwd_pen = cfg.get("min_work_days_penalty", 0)
    if mwd > 0 and mwd_pen > 0:
        for e in range(num_employees):
            tw = model.new_int_var(0, num_primary, f"tw_mwd_{e}")
            model.add(tw == sum(works[e, d] for d in range(primary_start, primary_end)))
            shortage = model.new_int_var(0, mwd, f"mwd_short_{e}")
            model.add(shortage >= mwd - tw)
            obj_int_vars.append(shortage)
            obj_int_coeffs.append(mwd_pen)

    # --- Minimize off days (primary month days only) ---
    off_pen = cfg.get("minimize_off_days_penalty", 0)
    if off_pen > 0:
        for e in range(num_employees):
            non_closed_primary = [d for d in range(primary_start, primary_end)
                                  if not is_closed(d)]
            off_days = model.new_int_var(0, num_primary, f"off_{e}")
            model.add(off_days == sum(1 - works[e, d] for d in non_closed_primary))
            obj_int_vars.append(off_days)
            obj_int_coeffs.append(off_pen)

    # --- Preferred start penalty ---
    psp = cfg.get("preferred_start_penalty", 0)
    if psp > 0:
        for e in range(num_employees):
            pref = roster[e].get("preferred_start")
            if pref is None:
                continue
            pref_min = time_str_to_minutes(pref)
            pref_slot = slot_index(pref_min, interval, first_slot_min)
            if pref_slot not in possible_start_slots:
                continue
            # Penalise choosing a different start
            diff = model.new_int_var(0, len(possible_start_slots), f"psd_{e}")
            pos = model.new_int_var(-num_slots, num_slots, f"psd_raw_{e}")
            model.add(pos == start_val[e] - pref_slot)
            model.add_abs_equality(diff, pos)
            obj_int_vars.append(diff)
            obj_int_coeffs.append(psp)

    # --- Stability penalty (previous schedule) ---
    stab = cfg.get("stability_penalty", 0)
    prev_sched = cfg.get("previous_schedule")
    if stab > 0 and prev_sched:
        # prev_sched: dict[emp_id -> list[day_assignment]]
        # For the slot optimizer, stability means keeping the same start time
        # and same work/off pattern as previous month (by weekday).
        # This is a soft penalty; implementation depends on previous schedule format.
        pass  # TODO: implement when previous schedule format is defined

    # -----------------------------------------------------------------------
    # Objective
    # -----------------------------------------------------------------------
    logger.info("All constraints added. Objective terms: %d int vars, %d bool vars",
                len(obj_int_vars), len(obj_bool_vars))
    add_symmetry_breaking(model, roster, start_val, works, num_days)
    # --- Scale float coefficients to integers (CP-SAT requires int coeffs) ---
    all_coeffs = obj_int_coeffs + obj_bool_coeffs
    if any(isinstance(c, float) and c != int(c) for c in all_coeffs):
        # Find the smallest power-of-10 multiplier that makes all coefficients
        # close to integers (handles up to 4 decimal places).
        scale = 1
        for c in all_coeffs:
            # Count needed decimal digits
            frac = abs(c) - int(abs(c))
            if frac > 0:
                s = 1
                while abs(round(c * s) - c * s) > 1e-9 and s < 10_000:
                    s *= 10
                scale = scale * s // math.gcd(scale, s)  # LCM
        obj_int_coeffs = [int(round(c * scale)) for c in obj_int_coeffs]
        obj_bool_coeffs = [int(round(c * scale)) for c in obj_bool_coeffs]
        logger.info("Scaled objective coefficients by %d to convert floats to ints", scale)
    else:
        obj_int_coeffs = [int(c) for c in obj_int_coeffs]

    if obj_bool_vars or obj_int_vars:
        model.minimize(
            sum(obj_bool_vars[i] * obj_bool_coeffs[i] for i in range(len(obj_bool_vars)))
            + sum(obj_int_vars[i] * obj_int_coeffs[i] for i in range(len(obj_int_vars)))
        )
    else:
        model.minimize(0)
    # =======================================================================
    # ITEM 2: INSERT SEARCH STRATEGIES HERE
    # =======================================================================
    
    # Strategy 1: Decide Start Times First (Macro Decision)
    # This is the most critical decision. If we fix this, the rest is just filling slots.
    # We choose MIN_VALUE to pack starts as early as possible (or just to be deterministic).
    model.add_decision_strategy(
        [start_val[e] for e in range(num_employees)],
        cp_model.CHOOSE_FIRST,
        cp_model.SELECT_MIN_VALUE
    )

    # Strategy 2: Decide Working Days (Micro Decision)
    # We organize variables day-by-day. This helps the solver fill Day 1 completely
    # before struggling with Day 2.
    # We choose MAX_VALUE to try setting works=1 (True) first to satisfy demand.
    all_works_vars = []
    for d in range(num_days):
        for e in range(num_employees):
            all_works_vars.append(works[e, d])

    model.add_decision_strategy(
        all_works_vars,
        cp_model.CHOOSE_FIRST,
        cp_model.SELECT_MAX_VALUE
    )
    
    logger.info("Model built successfully.")

    variables = {
        "works": works,
        "starts_at": starts_at,
        "start_val": start_val,
        "extra": extra,
        "ws": ws,
        "coverage_var": coverage_var,
        "possible_start_slots": possible_start_slots,
        "special_start_slot_by_dow": special_start_slot_by_dow,
    }
    return model, variables


# ---------------------------------------------------------------------------
# Solve
# ---------------------------------------------------------------------------

def solve_once(
    roster: List[Dict[str, Any]],
    demand: List[List[int]],
    slot_minutes: List[int],
    dates: List[dt.date],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Build & solve for a fixed roster. Returns solution dict."""
    logger.info("solve_once: building model for %d employees …", len(roster))
    model, variables = build_slot_model(roster, demand, slot_minutes, dates, cfg)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = cfg.get("solver_time_limit", 10)
    solver.parameters.linearization_level = 0   # Disable expensive linearization to speed up node exploration
    solver.parameters.num_workers = 8           # parallel search
    solver.parameters.symmetry_level = 2        # Aggressive symmetry detection
    solver.parameters.cp_model_probing_level = 2 # Enable probing to learn logical implications (e.g. if work Mon -> must work Tue)

    if cfg.get("solver_log", False):
        solver.parameters.log_search_progress = True
        logger.info("CP-SAT solver logging enabled (log_search_progress=True)")

    logger.info("Solving (time_limit=%.1fs, workers=%d) …",
                solver.parameters.max_time_in_seconds,
                solver.parameters.num_workers)
    status = solver.solve(model)
    status_name = solver.status_name(status)
    obj = solver.objective_value if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None
    logger.info("Solver finished: status=%s, objective=%s, wall_time=%.3fs",
                status_name, obj, solver.wall_time)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        logger.warning("No feasible solution found (status=%s)", status_name)

    return {
        "status": status,
        "solver": solver,
        "model": model,
        "variables": variables,
        "roster": roster,
        "dates": dates,
        "demand": demand,
        "slot_minutes": slot_minutes,
        "cfg": cfg,
        "objective": obj,
    }


def solve_with_roster_search(
    base_roster: List[Dict[str, Any]],
    demand: List[List[int]],
    slot_minutes: List[int],
    dates: List[dt.date],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Try roster sizes from min_employees..max_employees, return best solution."""
    min_emp = cfg.get("min_employees") or len(base_roster)
    max_emp = cfg.get("max_employees") or len(base_roster)
    current = len(base_roster)
    logger.info("Roster search: trying %d..%d employees (current=%d)",
                min_emp, max_emp, current)

    best = None
    for n in range(min_emp, max_emp + 1):
        # Build roster of size n
        roster_n = list(base_roster[:n])
        while len(roster_n) < n:
            roster_n.append({"id": f"hire_{len(roster_n)}", "gender": "M"})
        logger.info("--- Roster search: trying n=%d ---", n)
        result = solve_once(roster_n, demand, slot_minutes, dates, cfg)
        st = result["status"]
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            logger.info("  n=%d → infeasible", n)
            continue
        logger.info("  n=%d → objective=%.1f", n, result["objective"])
        if best is None:
            best = (n, result)
            continue
        _, prev = best
        if result["objective"] < prev["objective"]:
            best = (n, result)
        elif result["objective"] == prev["objective"]:
            best_n = best[0]
            if abs(n - current) < abs(best_n - current):
                best = (n, result)

    if best is None:
        logger.warning("No feasible schedule found between %d and %d employees.",
                       min_emp, max_emp)
        print(f"No feasible schedule found between {min_emp} and {max_emp} employees.")
        return {"status": cp_model.INFEASIBLE}
    best_n, best_result = best
    logger.info("Roster search complete: best n=%d, objective=%.1f",
                best_n, best_result["objective"])
    return best_result


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def extract_solution(result: Dict[str, Any]) -> Dict[str, Any]:
    """Extract human-readable solution from a solved result dict.

    Only primary month days (excluding week-padding days) are included in the
    output schedule and coverage tables.
    """
    solver = result["solver"]
    variables = result["variables"]
    roster = result["roster"]
    dates = result["dates"]
    demand = result["demand"]
    slot_minutes = result["slot_minutes"]
    cfg = result["cfg"]

    interval = cfg["slot_interval"]
    first_slot_min = slot_minutes[0]
    num_employees = len(roster)
    num_days = len(dates)
    num_slots = len(slot_minutes)
    possible_start_slots = variables["possible_start_slots"]

    # Primary month range
    primary_start = cfg.get("primary_start", 0)
    primary_end = cfg.get("primary_end", num_days)
    num_primary = primary_end - primary_start

    preset = STORE_PRESETS.get(cfg["store_type"], STORE_PRESETS["street"])
    closed_dows = cfg["closed_days"] if cfg["closed_days"] is not None else preset["closed_days"]
    special_dows = cfg["special_days"] if cfg["special_days"] is not None else preset["special_days"]
    normal_dur = int(cfg["normal_duration_hours"] * 60 / interval)
    special_dur = int(cfg["special_duration_hours"] * 60 / interval)
    special_start_slot_by_dow = variables["special_start_slot_by_dow"]

    logger.info("Extracting solution (primary days %d..%d) …", primary_start, primary_end)
    employees = []
    for e in range(num_employees):
        start_slot = solver.value(variables["start_val"][e])
        start_min = slot_to_minutes(start_slot, interval, first_slot_min)
        start_time = minutes_to_time_str(start_min)
        logger.debug("  Employee %s: start_slot=%d (%s)", roster[e]["id"], start_slot, start_time)

        schedule = []
        for d in range(primary_start, primary_end):
            if solver.boolean_value(variables["works"][e, d]):
                is_spec = dates[d].weekday() in special_dows
                base_dur = special_dur if is_spec else normal_dur
                ex = 0
                ex_var = variables["extra"].get((e, d))
                if ex_var is not None and not isinstance(ex_var, int):
                    ex = solver.value(ex_var)
                elif isinstance(ex_var, int):
                    ex = ex_var
                total_dur = base_dur + ex
                # On special days all employees share a fixed start
                if is_spec:
                    dow = dates[d].weekday()
                    day_start_min = slot_to_minutes(
                        special_start_slot_by_dow[dow], interval, first_slot_min
                    )
                else:
                    day_start_min = start_min
                day_start_time = minutes_to_time_str(day_start_min)
                end_min = day_start_min + total_dur * interval
                end_time = minutes_to_time_str(end_min)
                schedule.append({
                    "date": dates[d].isoformat(),
                    "day": DAY_NAMES[dates[d].weekday()],
                    "status": "work",
                    "start": day_start_time,
                    "end": end_time,
                    "duration_slots": total_dur,
                    "extra_slots": ex,
                })
            else:
                is_cls = dates[d].weekday() in closed_dows
                schedule.append({
                    "date": dates[d].isoformat(),
                    "day": DAY_NAMES[dates[d].weekday()],
                    "status": "closed" if is_cls else "off",
                })
        employees.append({
            "id": roster[e]["id"],
            "gender": roster[e]["gender"],
            "start_time": start_time,
            "schedule": schedule,
        })

    # Coverage vs demand (primary month days only)
    coverage_table = []
    total_understaff = 0
    total_overstaff = 0
    for d in range(primary_start, primary_end):
        dow = dates[d].weekday()
        day_row = {"date": dates[d].isoformat(), "day": DAY_NAMES[dow], "slots": []}
        for t in range(num_slots):
            cov = solver.value(variables["coverage_var"][d, t])
            req = demand[t][dow] if t < len(demand) and dow < len(demand[t]) else 0
            diff = cov - req
            if diff < 0:
                total_understaff += abs(diff)
            else:
                total_overstaff += diff
            day_row["slots"].append({
                "time": minutes_to_time_str(slot_minutes[t]),
                "demand": req,
                "coverage": cov,
                "diff": diff,
            })
        coverage_table.append(day_row)

    work_days = {e: sum(1 for d in range(primary_start, primary_end)
                        if solver.boolean_value(variables["works"][e, d]))
                 for e in range(num_employees)}

    logger.info("Solution extracted: understaff=%d, overstaff=%d, objective=%s",
                total_understaff, total_overstaff, result["objective"])
    for e in range(num_employees):
        logger.debug("  %s: %d work days", roster[e]["id"], work_days[e])

    return {
        "employees": employees,
        "coverage": coverage_table,
        "summary": {
            "num_employees": num_employees,
            "num_days": num_primary,
            "total_understaff": total_understaff,
            "total_overstaff": total_overstaff,
            "objective": result["objective"],
            "status": solver.status_name(result["status"]),
            "work_days_per_employee": {
                roster[e]["id"]: work_days[e] for e in range(num_employees)
            },
        },
    }


def print_schedule(solution: Dict[str, Any]) -> None:
    """Pretty-print the schedule to stdout."""
    summary = solution["summary"]
    print("=" * 80)
    print(f"SCHEDULE  |  {summary['num_employees']} employees  |  {summary['num_days']} days")
    print(f"Status: {summary['status']}  |  Objective: {summary['objective']}")
    print(f"Total understaff: {summary['total_understaff']}  |  Total overstaff: {summary['total_overstaff']}")
    print("=" * 80)

    for emp in solution["employees"]:
        work_count = sum(1 for s in emp["schedule"] if s["status"] == "work")
        print(f"\n{emp['id']} ({emp['gender']})  |  Start: {emp['start_time']}  |  {work_count} work days")
        print("-" * 60)
        for s in emp["schedule"]:
            date_str = s["date"]
            day_str = s["day"][:3].capitalize()
            if s["status"] == "work":
                extra_str = f" (+{s['extra_slots']} comp)" if s.get("extra_slots", 0) > 0 else ""
                print(f"  {date_str} {day_str}:  {s['start']} - {s['end']}{extra_str}")
            elif s["status"] == "closed":
                print(f"  {date_str} {day_str}:  CLOSED")
            else:
                print(f"  {date_str} {day_str}:  OFF")

    print("\n" + "=" * 80)
    print("WORK DAYS PER EMPLOYEE:")
    for eid, wdays in summary["work_days_per_employee"].items():
        print(f"  {eid}: {wdays}")


def print_compact_schedule(solution: Dict[str, Any]) -> None:
    """Print compact schedule table like the old optimizer.

    Each cell is a short code: start-hour (e.g. '10', '14'), 'O' for off,
    'X' for closed.  Compensation days get a '*' suffix.
    """
    _COL = 4  # characters per day column
    employees = solution["employees"]
    if not employees:
        return

    all_days = employees[0]["schedule"]
    num_days = len(all_days)

    # Label width
    label_w = max(len(emp["id"]) for emp in employees) + 7  # "(M): " + id

    prefix = " " * label_w
    hdr_days = prefix + "".join(f"{dt.date.fromisoformat(s['date']).day:>{_COL}}" for s in all_days)
    hdr_dow = prefix + "".join(
        f"{s['day'][:2].capitalize():>{_COL}}" for s in all_days
    )

    print()
    print(hdr_days)
    print(hdr_dow)
    for emp in employees:
        label = f"{emp['id']} ({emp['gender']}): "
        label = f"{label:<{label_w}}"
        cells = []
        for s in emp["schedule"]:
            if s["status"] == "work":
                sh = int(s["start"][:2])
                extra_mark = "*" if s.get("extra_slots", 0) > 0 else ""
                cell = f"{sh}{extra_mark}"
            elif s["status"] == "closed":
                cell = "X"
            else:
                cell = "O"
            cells.append(f"{cell:>{_COL}}")
        wc = sum(1 for s in emp["schedule"] if s["status"] == "work")
        print(label + "".join(cells) + f" ({wc})")
    print()

    # Totals row
    print(f"{'STAFF/DAY:':<{label_w}}" + "".join(
        f"{sum(1 for emp in employees if emp['schedule'][di]['status'] == 'work'):>{_COL}}"
        for di in range(num_days)
    ))


def print_employee_grid(solution: Dict[str, Any]) -> None:
    """Print Employee x Day grid table (detailed).

    Rows = employees, columns = calendar days.
    Cells show start-end (e.g. '10-18') or OFF / CLS.
    """
    employees = solution["employees"]
    if not employees:
        return

    all_days = employees[0]["schedule"]
    num_days = len(all_days)

    id_width = max(len(emp["id"]) for emp in employees) + 2
    col_w = 7  # enough for "10-18" or "10-19*"

    hdr1 = " " * id_width
    hdr2 = " " * id_width
    for s in all_days:
        d = dt.date.fromisoformat(s["date"])
        hdr1 += f"{d.day:>{col_w}}"
        hdr2 += f"{s['day'][:3].capitalize():>{col_w}}"

    print("\n" + "=" * (id_width + num_days * col_w))
    print("EMPLOYEE SCHEDULE GRID (detailed)")
    print("=" * (id_width + num_days * col_w))
    print(hdr1)
    print(hdr2)
    print("-" * (id_width + num_days * col_w))

    for emp in employees:
        row = f"{emp['id']:<{id_width}}"
        for s in emp["schedule"]:
            if s["status"] == "work":
                sh = int(s["start"][:2])
                sm = int(s["start"][3:5])
                eh = int(s["end"][:2])
                em_val = int(s["end"][3:5])
                s_tag = f"{sh}"
                e_tag = f"{eh}" if em_val == 0 else f"{eh}:{em_val:02d}"
                extra_mark = "*" if s.get("extra_slots", 0) > 0 else ""
                cell = f"{s_tag}-{e_tag}{extra_mark}"
                row += f"{cell:>{col_w}}"
            elif s["status"] == "closed":
                row += f"{'CLS':>{col_w}}"
            else:
                row += f"{'OFF':>{col_w}}"
        wc = sum(1 for s in emp["schedule"] if s["status"] == "work")
        row += f"  ({wc}d)"
        print(row)

    print("-" * (id_width + num_days * col_w))
    footer = f"{'TOTAL':<{id_width}}"
    for di in range(num_days):
        working = sum(
            1 for emp in employees
            if emp["schedule"][di]["status"] == "work"
        )
        footer += f"{working:>{col_w}}"
    print(footer)


def print_coverage_grid(solution: Dict[str, Any]) -> None:
    """Print coverage grid in same format as demand table.

    Rows = time slots, columns = days of the week (Mon-Sun).
    Each cell shows the actual coverage count.
    """
    coverage_data = solution["coverage"]
    if not coverage_data:
        return

    # Group coverage by day-of-week
    # We'll build a grid: rows = time slots, columns = unique dates
    # But to match demand table format, we'll show one typical week
    # Actually, let's show ALL days as columns (compact)

    num_days = len(coverage_data)
    slots = coverage_data[0]["slots"]
    num_slots = len(slots)

    col_w = 6
    time_w = 8

    # Header
    print("\n" + "=" * (time_w + num_days * col_w))
    print("COVERAGE GRID (actual employees present per slot)")
    print("=" * (time_w + num_days * col_w))

    hdr1 = " " * time_w
    hdr2 = " " * time_w
    for day_row in coverage_data:
        d = dt.date.fromisoformat(day_row["date"])
        hdr1 += f"{d.day:>{col_w}}"
        hdr2 += f"{day_row['day'][:3].capitalize():>{col_w}}"
    print(hdr1)
    print(hdr2)
    print("-" * (time_w + num_days * col_w))

    for ti in range(num_slots):
        time_str = slots[ti]["time"] if ti < len(slots) else ""
        row = f"{time_str:>{time_w}}"
        for di in range(num_days):
            day_slots = coverage_data[di]["slots"]
            if ti < len(day_slots):
                cov = day_slots[ti]["coverage"]
                req = day_slots[ti]["demand"]
                if cov == 0 and req == 0:
                    cell = "."
                elif cov < req:
                    cell = f"{cov}!"
                elif cov > req:
                    cell = f"{cov}+"
                else:
                    cell = str(cov)
            else:
                cell = "."
            row += f"{cell:>{col_w}}"
        print(row)

    print("-" * (time_w + num_days * col_w))
    print("Legend:  N = exact match  N+ = overstaffed  N! = UNDERSTAFFED  . = no demand/coverage")


def print_coverage(solution: Dict[str, Any]) -> None:
    """Print coverage vs demand comparison (per-day detail)."""
    print("\n" + "=" * 80)
    print("COVERAGE vs DEMAND (detail)")
    print("=" * 80)
    for day_row in solution["coverage"]:
        date_str = day_row["date"]
        day_str = day_row["day"][:3].capitalize()
        has_demand = any(s["demand"] > 0 for s in day_row["slots"])
        if not has_demand:
            continue
        print(f"\n{date_str} ({day_str}):")
        print(f"  {'Time':>8}  {'Demand':>6}  {'Cover':>5}  {'Diff':>5}")
        print(f"  {'----':>8}  {'------':>6}  {'-----':>5}  {'-----':>5}")
        for slot in day_row["slots"]:
            if slot["demand"] > 0 or slot["coverage"] > 0:
                diff_str = f"{slot['diff']:+d}" if slot["diff"] != 0 else "0"
                marker = " !!" if slot["diff"] < 0 else ""
                print(f"  {slot['time']:>8}  {slot['demand']:>6}  {slot['coverage']:>5}  {diff_str:>5}{marker}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_cli_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Slot-level workforce optimizer",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Tier 1
    p.add_argument("--demand", required=True, help="Path to demand CSV file")
    p.add_argument("--roster", required=True, help="Path to roster JSON/CSV file")
    p.add_argument("--year", type=int, default=DEFAULT_CONFIG["year"])
    p.add_argument("--month", type=int, default=DEFAULT_CONFIG["month"])

    # Tier 2
    p.add_argument("--slot-interval", type=int, default=20)
    p.add_argument("--normal-duration-hours", type=float, default=8)
    p.add_argument("--special-duration-hours", type=float, default=6)
    p.add_argument("--start-window", nargs=2, default=["10:00", "14:00"],
                    metavar=("START", "END"))
    p.add_argument("--start-step", type=int, default=None,
                    help="Minutes between start positions (default: same as slot-interval). "
                         "Use 60 to restrict starts to whole hours.")
    p.add_argument("--store-type", choices=["street", "mall"], default="street")
    p.add_argument("--max-extra-per-day", type=int, default=2)
    p.add_argument("--compensation-carry",
                    choices=["allow_partial", "forbid_special_last_week", "carry_forward"],
                    default="allow_partial")
    p.add_argument("--solver-time-limit", type=float, default=10)

    p.add_argument("--relax-cover", action="store_true", default=True)
    p.add_argument("--no-relax-cover", dest="relax_cover", action="store_false")
    p.add_argument("--understaff-penalty", type=int, default=100)
    p.add_argument("--excess-penalty", type=int, default=1)
    p.add_argument("--minimize-off-days-penalty", type=int, default=10)

    p.add_argument("--min-days-off-per-week", type=int, default=1)
    p.add_argument("--max-consecutive-work-days", type=int, default=6)
    p.add_argument("--max-consecutive-off-days", type=int, default=3)

    p.add_argument("--women-sunday-off-alternate", action="store_true", default=True)
    p.add_argument("--no-women-sunday-off-alternate",
                    dest="women_sunday_off_alternate", action="store_false")
    p.add_argument("--min-sunday-off-per-month", type=int, default=1)

    p.add_argument("--spread-shifts-penalty", type=int, default=5)
    p.add_argument("--spread-sunday-shifts-penalty", type=int, default=5)

    p.add_argument("--min-work-days-per-month", type=int, default=20)
    p.add_argument("--min-work-days-penalty", type=int, default=10)

    # Tier 3
    p.add_argument("--closed-days", type=int, nargs="*", default=None,
                    help="Day-of-week indices (0=Mon..6=Sun) when store is closed")
    p.add_argument("--special-days", type=int, nargs="*", default=None,
                    help="Day-of-week indices for shorter shifts")
    p.add_argument("--quadratic-excess-penalty", type=int, default=0)
    p.add_argument("--min-staff-floor", type=int, default=0)
    p.add_argument("--max-shifts-per-week", type=int, default=None)
    p.add_argument("--require-consecutive-off", action="store_true", default=False)
    p.add_argument("--max-weekend-work-shifts-women", type=int, default=None)
    p.add_argument("--min-sunday-off-women", type=int, default=0)
    p.add_argument("--stability-penalty", type=int, default=0)
    p.add_argument("--preferred-start-penalty", type=int, default=0)
    p.add_argument("--min-employees", type=int, default=None,
                    help="Min roster size for roster search")
    p.add_argument("--max-employees", type=int, default=None,
                    help="Max roster size for roster search")
    p.add_argument("--demand-spread-penalty", type=float, default=0,
                    help="Penalty for deviation from demand-proportional daily staffing. "
                         "Higher-demand days get proportionally more staff. "
                         "Accepts fractional values (e.g. 0.5).")
    p.add_argument("--special-day-demand-weight", type=float, default=1.0,
                    help="Multiplier on special-day demand weights for proportional "
                         "staffing (e.g. 1.5 = treat special days as 50%% more demanding)")

    # Logging
    p.add_argument("--verbose", "-v", action="count", default=0,
                    help="Increase logging verbosity (-v = INFO, -vv = DEBUG)")
    p.add_argument("--solver-log", action="store_true", default=False,
                    help="Enable CP-SAT solver's own search progress log")

    # Output
    p.add_argument("--output-json", default=None, help="Path to write solution JSON")
    p.add_argument("--show-compact", action="store_true", default=True,
                    help="Print compact schedule (like old optimizer)")
    p.add_argument("--no-show-compact", dest="show_compact", action="store_false")
    p.add_argument("--show-grid", action="store_true", default=False,
                    help="Print detailed employee schedule grid")
    p.add_argument("--show-coverage-grid", action="store_true", default=True,
                    help="Print coverage grid (time slots x days)")
    p.add_argument("--no-show-coverage-grid", dest="show_coverage_grid",
                    action="store_false")
    p.add_argument("--show-coverage-detail", action="store_true", default=False,
                    help="Print per-day coverage vs demand detail")

    return p


def _configure_logging(verbosity: int) -> None:
    """Set up logging based on the -v count."""
    if verbosity <= 0:
        level = logging.WARNING
    elif verbosity == 1:
        level = logging.INFO
    else:
        level = logging.DEBUG

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(name)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    ))
    root = logging.getLogger("slot_optimizer")
    root.setLevel(level)
    # Avoid duplicate handlers on repeated calls
    if not root.handlers:
        root.addHandler(handler)


def main() -> None:
    parser = build_cli_parser()
    args = parser.parse_args()

    _configure_logging(args.verbose)

    # Build config
    cfg = _merge_config({
        "year": args.year,
        "month": args.month,
        "slot_interval": args.slot_interval,
        "normal_duration_hours": args.normal_duration_hours,
        "special_duration_hours": args.special_duration_hours,
        "start_window": tuple(args.start_window),
        "start_step": args.start_step,
        "store_type": args.store_type,
        "max_extra_per_day": args.max_extra_per_day,
        "compensation_carry": args.compensation_carry,
        "solver_time_limit": args.solver_time_limit,
        "relax_cover": args.relax_cover,
        "understaff_penalty": args.understaff_penalty,
        "excess_penalty": args.excess_penalty,
        "minimize_off_days_penalty": args.minimize_off_days_penalty,
        "min_days_off_per_week": args.min_days_off_per_week,
        "max_consecutive_work_days": args.max_consecutive_work_days,
        "max_consecutive_off_days": args.max_consecutive_off_days,
        "women_sunday_off_alternate": args.women_sunday_off_alternate,
        "min_sunday_off_per_month": args.min_sunday_off_per_month,
        "spread_shifts_penalty": args.spread_shifts_penalty,
        "spread_sunday_shifts_penalty": args.spread_sunday_shifts_penalty,
        "min_work_days_per_month": args.min_work_days_per_month,
        "min_work_days_penalty": args.min_work_days_penalty,
        "closed_days": args.closed_days,
        "special_days": args.special_days,
        "quadratic_excess_penalty": args.quadratic_excess_penalty,
        "min_staff_floor": args.min_staff_floor,
        "max_shifts_per_week": args.max_shifts_per_week,
        "require_consecutive_off": args.require_consecutive_off,
        "max_weekend_work_shifts_women": args.max_weekend_work_shifts_women,
        "min_sunday_off_women": args.min_sunday_off_women,
        "stability_penalty": args.stability_penalty,
        "preferred_start_penalty": args.preferred_start_penalty,
        "min_employees": args.min_employees,
        "max_employees": args.max_employees,
        "solver_log": args.solver_log,
        "demand_spread_penalty": args.demand_spread_penalty,
        "special_day_demand_weight": args.special_day_demand_weight,
    })

    # Load inputs
    print(f"Loading demand from {args.demand} ...")
    demand, slot_minutes, detected_interval = parse_demand_csv(args.demand)
    if cfg["slot_interval"] != detected_interval:
        print(f"  Warning: detected interval {detected_interval} min differs from "
              f"configured {cfg['slot_interval']} min. Using detected.")
        cfg["slot_interval"] = detected_interval

    print(f"Loading roster from {args.roster} ...")
    roster = load_roster(args.roster)
    print(f"  {len(roster)} employees loaded.")

    dates, primary_start, primary_end = build_complete_week_dates(
        cfg["year"], cfg["month"],
    )
    cfg["primary_start"] = primary_start
    cfg["primary_end"] = primary_end
    month_days = primary_end - primary_start
    print(f"Scheduling {cfg['year']}-{cfg['month']:02d} "
          f"({month_days} month days, {len(dates)} total with week padding, "
          f"{len(slot_minutes)} slots/day, interval={cfg['slot_interval']}min)")
    print(f"  Store type: {cfg['store_type']}")
    preset = STORE_PRESETS.get(cfg["store_type"], STORE_PRESETS["street"])
    cd = cfg["closed_days"] if cfg["closed_days"] is not None else preset["closed_days"]
    sd = cfg["special_days"] if cfg["special_days"] is not None else preset["special_days"]
    print(f"  Closed days: {[DAY_NAMES[d] for d in cd]}")
    print(f"  Special days: {[DAY_NAMES[d] for d in sd]}")
    print(f"  Start window: {cfg['start_window'][0]} - {cfg['start_window'][1]}")
    print(f"  Normal shift: {cfg['normal_duration_hours']}h, "
          f"Special shift: {cfg['special_duration_hours']}h")

    # Solve
    do_search = cfg.get("min_employees") is not None and cfg.get("max_employees") is not None
    if do_search:
        print(f"\nSearching roster sizes {cfg['min_employees']}..{cfg['max_employees']} ...")
        result = solve_with_roster_search(roster, demand, slot_minutes, dates, cfg)
    else:
        print(f"\nSolving with {len(roster)} employees ...")
        result = solve_once(roster, demand, slot_minutes, dates, cfg)

    if result["status"] not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"\nNo feasible solution found. Status: {cp_model.CpSolver().status_name(result['status'])}")
        sys.exit(1)

    solution = extract_solution(result)
    print_schedule(solution)
    if args.show_compact:
        print_compact_schedule(solution)
    if args.show_grid:
        print_employee_grid(solution)
    if args.show_coverage_grid:
        print_coverage_grid(solution)
    if args.show_coverage_detail:
        print_coverage(solution)

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(solution, f, indent=2, ensure_ascii=False)
        print(f"\nSolution written to {args.output_json}")

    print(f"\nDone. Solver stats: {result['solver'].response_stats()}")


if __name__ == "__main__":
    main()
