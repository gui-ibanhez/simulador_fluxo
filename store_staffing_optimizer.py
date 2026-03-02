#!/usr/bin/env python3
# Copyright 2010-2025 Google LLC
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Single-store staffing optimizer for a monthly horizon."""

import calendar
import datetime as dt
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from absl import app
from absl import flags
from ortools.sat.python import cp_model

_OUTPUT_PROTO = flags.DEFINE_string(
    "output_proto", "", "Output file to write the cp_model proto to."
)
_PARAMS = flags.DEFINE_string(
    "params", "max_time_in_seconds:10.0", "Sat solver parameters."
)
_YEAR = flags.DEFINE_integer("year", 2025, "Schedule year.")
_MONTH = flags.DEFINE_integer("month", 1, "Schedule month.")
_CURRENT_EMPLOYEES = flags.DEFINE_integer(
    "current_employees", 10, "Current roster size."
)
_MIN_EMPLOYEES = flags.DEFINE_integer("min_employees", 8, "Minimum employees.")
_MAX_EMPLOYEES = flags.DEFINE_integer("max_employees", 14, "Maximum employees.")
# Demand (default_demand): required employees per shift; weekday vs weekend bases.
_DEMAND_BASE_M_WEEKDAY = flags.DEFINE_integer(
    "demand_base_M_weekday", 5, "Required Morning shift (weekday)."
)
_DEMAND_BASE_A_WEEKDAY = flags.DEFINE_integer(
    "demand_base_A_weekday", 4, "Required Afternoon shift (weekday)."
)
_DEMAND_BASE_M_WEEKEND = flags.DEFINE_integer(
    "demand_base_M_weekend", 4, "Required Morning shift (weekend)."
)
_DEMAND_BASE_A_WEEKEND = flags.DEFINE_integer(
    "demand_base_A_weekend", 3, "Required Afternoon shift (weekend)."
)


def negated_bounded_span(
    works: list[cp_model.BoolVarT], start: int, length: int
) -> list[cp_model.BoolVarT]:
    """Filters an isolated sub-sequence of variables assigned to True.

    Extract the span of Boolean variables [start, start + length), negate them,
    and if there is variables to the left/right of this span, surround the span by
    them in non negated form.

    Args:
      works: a list of variables to extract the span from.
      start: the start to the span.
      length: the length of the span.

    Returns:
      a list of variables which conjunction will be false if the sub-list is
      assigned to True, and correctly bounded by variables assigned to False,
      or by the start or end of works.
    """
    sequence = []
    # left border (start of works, or works[start - 1])
    if start > 0:
        sequence.append(works[start - 1])
    for i in range(length):
        sequence.append(~works[start + i])
    # right border (end of works or works[start + length])
    if start + length < len(works):
        sequence.append(works[start + length])
    return sequence


def add_soft_sequence_constraint(
    model: cp_model.CpModel,
    works: list[cp_model.BoolVarT],
    hard_min: int,
    soft_min: int,
    min_cost: int,
    soft_max: int,
    hard_max: int,
    max_cost: int,
    prefix: str,
) -> tuple[list[cp_model.BoolVarT], list[int]]:
    """Sequence constraint on true variables with soft and hard bounds.

    This constraint look at every maximal contiguous sequence of variables
    assigned to true. If forbids sequence of length < hard_min or > hard_max.
    Then it creates penalty terms if the length is < soft_min or > soft_max.
    """
    cost_literals = []
    cost_coefficients = []

    # Forbid sequences that are too short.
    for length in range(1, hard_min):
        for start in range(len(works) - length + 1):
            model.add_bool_or(negated_bounded_span(works, start, length))

    # Penalize sequences that are below the soft limit.
    if min_cost > 0:
        for length in range(hard_min, soft_min):
            for start in range(len(works) - length + 1):
                span = negated_bounded_span(works, start, length)
                name = f": under_span(start={start}, length={length})"
                lit = model.new_bool_var(prefix + name)
                span.append(lit)
                model.add_bool_or(span)
                cost_literals.append(lit)
                # We filter exactly the sequence with a short length.
                # The penalty is proportional to the delta with soft_min.
                cost_coefficients.append(min_cost * (soft_min - length))

    # Penalize sequences that are above the soft limit.
    if max_cost > 0:
        for length in range(soft_max + 1, hard_max + 1):
            for start in range(len(works) - length + 1):
                span = negated_bounded_span(works, start, length)
                name = f": over_span(start={start}, length={length})"
                lit = model.new_bool_var(prefix + name)
                span.append(lit)
                model.add_bool_or(span)
                cost_literals.append(lit)
                # Cost paid is max_cost * excess length.
                cost_coefficients.append(max_cost * (length - soft_max))

    # Just forbid any sequence of true variables with length hard_max + 1
    for start in range(len(works) - hard_max):
        model.add_bool_or([~works[i] for i in range(start, start + hard_max + 1)])
    return cost_literals, cost_coefficients


def add_soft_sum_constraint(
    model: cp_model.CpModel,
    works: list[cp_model.BoolVarT],
    hard_min: int,
    soft_min: int,
    min_cost: int,
    soft_max: int,
    hard_max: int,
    max_cost: int,
    prefix: str,
) -> tuple[list[cp_model.IntVar], list[int]]:
    """Sum constraint with soft and hard bounds."""
    cost_variables = []
    cost_coefficients = []
    sum_var = model.new_int_var(hard_min, hard_max, "")
    # This adds the hard constraints on the sum.
    model.add(sum_var == sum(works))

    # Penalize sums below the soft_min target.
    if soft_min > hard_min and min_cost > 0:
        delta = model.new_int_var(-len(works), len(works), "")
        model.add(delta == soft_min - sum_var)
        excess = model.new_int_var(0, len(works), prefix + ": under_sum")
        model.add_max_equality(excess, [delta, 0])
        cost_variables.append(excess)
        cost_coefficients.append(min_cost)

    # Penalize sums above the soft_max target.
    if soft_max < hard_max and max_cost > 0:
        delta = model.new_int_var(-len(works), len(works), "")
        model.add(delta == sum_var - soft_max)
        excess = model.new_int_var(0, len(works), prefix + ": over_sum")
        model.add_max_equality(excess, [delta, 0])
        cost_variables.append(excess)
        cost_coefficients.append(max_cost)

    return cost_variables, cost_coefficients


def build_roster_composition(men: int, women: int) -> list[dict[str, Any]]:
    roster = []
    for i in range(men):
        roster.append({"id": f"man_{i}", "gender": "M"})
    for i in range(women):
        roster.append({"id": f"woman_{i}", "gender": "F"})
    return roster


def status_name(status: int) -> str:
    """Return status name from cp_model status int."""
    return cp_model.CpSolver().status_name(status)


def build_dates(year: int, month: int) -> list[dt.date]:
    _, num_days = calendar.monthrange(year, month)
    return [dt.date(year, month, day) for day in range(1, num_days + 1)]


def build_complete_week_dates(year: int, month: int) -> tuple[list[dt.date], int, int]:
    """Build dates for complete calendar weeks covering the target month.

    Returns:
        - dates: list of dates from Monday of first week to Sunday of last week
        - primary_start: index of first day of target month in dates
        - primary_end: index after last day of target month (exclusive)
    """
    # First day of month
    first_day = dt.date(year, month, 1)
    # Last day of month
    _, num_days = calendar.monthrange(year, month)
    last_day = dt.date(year, month, num_days)

    # Monday of week containing first day
    start = first_day - dt.timedelta(days=first_day.weekday())
    # Sunday of week containing last day
    end = last_day + dt.timedelta(days=6 - last_day.weekday())

    # Build date list
    dates = []
    current = start
    while current <= end:
        dates.append(current)
        current += dt.timedelta(days=1)

    # Calculate primary range indices
    primary_start = (first_day - start).days
    primary_end = primary_start + num_days

    return dates, primary_start, primary_end


def _weeks_with_previous(
    dates: list[dt.date],
    previous_dates: Optional[list[dt.date]] = None,
) -> List[Tuple[list[int], list[int]]]:
    """Calendar weeks (Mon–Sun) with optional previous-month days.
    Returns list of (current_day_indices, prev_day_indices) per week.
    Only includes weeks that have at least one current-month day.
    prev_day_indices are indices into previous_dates for days in same week.
    """
    week_to_current: Dict[int, list[int]] = {}
    week_to_prev: Dict[int, list[int]] = {}
    for d, date in enumerate(dates):
        monday = date - dt.timedelta(days=date.weekday())
        key = monday.toordinal()
        week_to_current.setdefault(key, []).append(d)
    if previous_dates:
        for d, date in enumerate(previous_dates):
            monday = date - dt.timedelta(days=date.weekday())
            key = monday.toordinal()
            if key in week_to_current:
                week_to_prev.setdefault(key, []).append(d)
    result = []
    for key in sorted(week_to_current.keys()):
        current = sorted(week_to_current[key])
        prev = sorted(week_to_prev.get(key, []))
        result.append((current, prev))
    return result


def default_demand(
    dates: list[dt.date],
    base_m_weekday: int = 5,
    base_a_weekday: int = 4,
    base_m_weekend: int = 4,
    base_a_weekend: int = 3,
) -> list[Dict[str, int]]:
    """Demand table: required employees per (day, shift). Weekday vs weekend bases."""
    demand = []
    for date in dates:
        if date.weekday() < 5:  # Mon=0 .. Fri=4
            demand.append({"M": base_m_weekday, "A": base_a_weekday})
        else:
            demand.append({"M": base_m_weekend, "A": base_a_weekend})
    return demand


def validate_demand(
    demand: list[Dict[str, int]], dates: list[dt.date], shifts: list[str]
) -> None:
    if len(demand) != len(dates):
        raise ValueError("Demand length must match number of days in month.")
    work_shifts = [s for s in shifts if s != "O"]
    for day_index, day_demand in enumerate(demand):
        missing = [s for s in work_shifts if s not in day_demand]
        if missing:
            raise ValueError(
                f"Demand missing shifts {missing} for day index {day_index}."
            )


def schedule_to_dict(
    solver: cp_model.CpSolver,
    work: Dict[Tuple[int, int, int], cp_model.BoolVarT],
    num_employees: int,
    shifts: list[str],
    dates: list[dt.date],
    roster: Optional[list[dict[str, Any]]] = None,
    store_id: Optional[str] = None,
    primary_start: Optional[int] = None,
    primary_end: Optional[int] = None,
) -> dict:
    """Export schedule to JSON-serializable dict."""
    employee_ids = [
        (roster[e].get("id", f"emp_{e}") if roster and e < len(roster) else f"emp_{e}")
        for e in range(num_employees)
    ]
    schedule = []
    for e in range(num_employees):
        row = []
        for d in range(len(dates)):
            for s in range(len(shifts)):
                if solver.boolean_value(work[e, s, d]):
                    row.append(shifts[s])
                    break
        schedule.append(row)
    out = {
        "employee_ids": employee_ids,
        "schedule": schedule,
        "shifts": shifts,
        "year": dates[0].year if primary_start is None else dates[primary_start].year,
        "month": dates[0].month if primary_start is None else dates[primary_start].month,
    }
    if primary_start is not None:
        out["extended_dates"] = {
            "start": dates[0].isoformat(),
            "end": dates[-1].isoformat(),
            "primary_start_index": primary_start,
            "primary_end_index": primary_end,
        }
    if store_id:
        out["store_id"] = store_id
    return out


def load_previous_schedule(
    path: str,
    store_id: str,
    current_shifts: list[str],
) -> tuple[Dict[str, list[str]], list[dt.date]]:
    """Load previous schedule from JSON. Returns (dict[employee_id, shifts_per_day], dates).

    Validates that previous shifts match current_shifts.
    For multi-store files, uses data["stores"][store_id]. For single-store, uses top-level.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    store_data = data.get("stores", {}).get(store_id, data)
    if "shifts" not in store_data:
        raise ValueError("Previous schedule JSON must contain 'shifts' field")
    prev_shifts = store_data["shifts"]
    if prev_shifts != current_shifts:
        raise ValueError(
            f"Previous schedule shifts {prev_shifts} do not match current {current_shifts}"
        )
    prev = {
        eid: store_data["schedule"][i]
        for i, eid in enumerate(store_data["employee_ids"])
    }
    dates = build_dates(store_data["year"], store_data["month"])
    return prev, dates


def build_model(
    num_employees: int,
    dates: list[dt.date],
    shifts: list[str],
    demand: list[Dict[str, int]],
    constraints: Dict[str, object],
    roster: Optional[list[dict[str, Any]]] = None,
) -> tuple[
    cp_model.CpModel,
    Dict[Tuple[int, int, int], cp_model.BoolVarT],
    list[cp_model.BoolVarT],
    list[int],
    list[cp_model.IntVar],
    list[int],
]:
    num_days = len(dates)
    num_shifts = len(shifts)
    shift_index = {name: idx for idx, name in enumerate(shifts)}
    off_index = shift_index.get("O")
    work_shift_indices = [i for i, name in enumerate(shifts) if name != "O"]

    # Normalize roster: pad with default if None or shorter than num_employees.
    debug_sunday = bool(constraints.get("debug_sunday_constraints", False))
    if roster is None:
        roster = [{"id": f"emp_{e}", "gender": "M"} for e in range(num_employees)]
        if debug_sunday:
            print("[DEBUG:sunday] build_model: roster was None, all default to M (no women)")
    else:
        roster = list(roster)
        padded = 0
        for e in range(len(roster)):
            if roster[e].get("id") is None or roster[e].get("id") == "":
                roster[e] = {**roster[e], "id": f"emp_{e}"}
        while len(roster) < num_employees:
            roster.append({"id": f"emp_{len(roster)}", "gender": "M"})
            padded += 1
        if debug_sunday and padded:
            print(f"[DEBUG:sunday] build_model: roster padded with {padded} default M")

    model = cp_model.CpModel()
    work = {}
    for e in range(num_employees):
        for s in range(num_shifts):
            for d in range(num_days):
                work[e, s, d] = model.new_bool_var(f"work{e}_{s}_{d}")

    obj_int_vars: list[cp_model.IntVar] = []
    obj_int_coeffs: list[int] = []
    obj_bool_vars: list[cp_model.BoolVarT] = []
    obj_bool_coeffs: list[int] = []

    # At most one shift per day per employee (includes Off).
    for e in range(num_employees):
        for d in range(num_days):
            model.add_exactly_one(work[e, s, d] for s in range(num_shifts))

    # Cover constraints per day/shift.
    relax_cover = constraints.get("relax_cover", False)
    understaff_penalty = int(constraints.get("understaff_penalty", 100))
    excess_penalties = constraints.get("excess_cover_penalties", {})
    for d in range(num_days):
        for shift_name in shifts:
            if shift_name == "O":
                continue
            required = demand[d][shift_name]
            s_index = shift_index[shift_name]
            # When demand is 0, forbid all assignments (shift closed, store closed, etc.)
            if required == 0:
                for e in range(num_employees):
                    model.add(work[e, s_index, d] == 0)
                continue
            if not relax_cover and required > num_employees:
                raise ValueError(
                    f"Demand {required} exceeds employees {num_employees} on day {d}."
                )
            if relax_cover:
                worked = model.new_int_var(0, num_employees, "")
                model.add(
                    worked == sum(work[e, s_index, d] for e in range(num_employees))
                )
                understaff = model.new_int_var(
                    0, required, f"understaff_{d}_{shift_name}"
                )
                model.add(understaff >= required - worked)
                obj_int_vars.append(understaff)
                obj_int_coeffs.append(understaff_penalty)
            else:
                worked = model.new_int_var(required, num_employees, "")
                model.add(
                    worked == sum(work[e, s_index, d] for e in range(num_employees))
                )
                penalty = int(excess_penalties.get(shift_name, 0))
                if penalty > 0 and num_employees > required:
                    excess = model.new_int_var(
                        0,
                        num_employees - required,
                        f"excess_cover_day{dates[d].day}_{shift_name}",
                    )
                    model.add(excess == worked - required)
                    obj_int_vars.append(excess)
                    obj_int_coeffs.append(penalty)

                quadratic_penalty = int(constraints.get("quadratic_excess_penalty", 0))
                if quadratic_penalty > 0 and num_employees > required:
                     # Reuse excess var if created, otherwise create it
                    if not (penalty > 0 and num_employees > required):
                         excess = model.new_int_var(
                            0,
                            num_employees - required,
                            f"excess_cover_day{dates[d].day}_{shift_name}_sq_base",
                        )
                         model.add(excess == worked - required)
                    
                    excess_sq = model.new_int_var(
                        0,
                        (num_employees - required) ** 2,
                        f"excess_cover_day{dates[d].day}_{shift_name}_sq",
                    )
                    model.add_multiplication_equality(excess_sq, [excess, excess])
                    obj_int_vars.append(excess_sq)
                    obj_int_coeffs.append(quadratic_penalty)

    # Optional sequence constraints per shift.
    for ct in constraints.get("sequence_constraints", []):
        shift_ref, hard_min, soft_min, min_cost, soft_max, hard_max, max_cost = ct
        s_index = shift_index[shift_ref] if isinstance(shift_ref, str) else shift_ref
        for e in range(num_employees):
            works = [work[e, s_index, d] for d in range(num_days)]
            shift_name = shifts[s_index] if s_index < len(shifts) else str(s_index)
            variables, coeffs = add_soft_sequence_constraint(
                model,
                works,
                hard_min,
                soft_min,
                min_cost,
                soft_max,
                hard_max,
                max_cost,
                f"seq(employee {e}, {shift_name})",
            )
            obj_bool_vars.extend(variables)
            obj_bool_coeffs.extend(coeffs)

    # Optional weekly sum constraints. Uses calendar weeks (Mon–Sun); completes partial
    # weeks with previous month when previous_schedule is provided.
    prev_sched = constraints.get("previous_schedule")
    prev_dates_list = constraints.get("previous_dates")
    for ct in constraints.get("weekly_sum_constraints", []):
        shift_ref, hard_min, soft_min, min_cost, soft_max, hard_max, max_cost = ct
        s_index = shift_index[shift_ref] if isinstance(shift_ref, str) else shift_ref
        shift_name = shifts[s_index] if s_index < len(shifts) else str(shift_ref)
        for current_days, prev_days in _weeks_with_previous(dates, prev_dates_list):
            for e in range(num_employees):
                prev_count = 0
                if prev_sched and prev_dates_list and prev_days:
                    emp_id = roster[e].get("id", f"emp_{e}")
                    if emp_id in prev_sched:
                        prev_shifts = prev_sched[emp_id]
                        for pd in prev_days:
                            if pd < len(prev_shifts) and prev_shifts[pd] == shift_name:
                                prev_count += 1
                h_min = max(0, hard_min - prev_count)
                h_max = min(len(current_days), hard_max - prev_count)
                if h_min > h_max:
                    continue
                s_min = max(h_min, soft_min - prev_count)
                s_max = min(h_max, soft_max - prev_count)
                works = [work[e, s_index, d] for d in current_days]
                if not works:
                    continue
                variables, coeffs = add_soft_sum_constraint(
                    model,
                    works,
                    h_min,
                    s_min,
                    min_cost,
                    s_max,
                    h_max,
                    max_cost,
                    f"weekly(employee {e}, {shift_name})",
                )
                obj_int_vars.extend(variables)
                obj_int_coeffs.extend(coeffs)

    # Optional max shifts per week (working shifts only). Uses calendar weeks (Mon–Sun);
    # completes partial weeks with previous month when previous_schedule is provided.
    max_shifts_per_week = constraints.get("max_shifts_per_week")
    if max_shifts_per_week is not None and max_shifts_per_week > 0:
        prev_sched = constraints.get("previous_schedule")
        prev_dates_list = constraints.get("previous_dates")
        for current_days, prev_days in _weeks_with_previous(dates, prev_dates_list):
            for e in range(num_employees):
                prev_work = 0
                if prev_sched and prev_dates_list and prev_days:
                    emp_id = roster[e].get("id", f"emp_{e}")
                    if emp_id in prev_sched:
                        prev_shifts = prev_sched[emp_id]
                        for pd in prev_days:
                            if pd < len(prev_shifts) and prev_shifts[pd] != "O":
                                prev_work += 1
                limit = max_shifts_per_week - prev_work
                if limit < 0:
                    continue
                if current_days:
                    model.add(
                        sum(
                            work[e, s, d]
                            for s in work_shift_indices
                            for d in current_days
                        )
                        <= limit
                    )

    # Optional min days off per week (Monday–Sunday). Uses calendar weeks; completes
    # partial weeks with previous month when previous_schedule is provided.
    min_days_off = constraints.get("min_days_off_per_week")
    previous_schedule = constraints.get("previous_schedule")
    previous_dates = constraints.get("previous_dates")
    if off_index is not None and min_days_off is not None and min_days_off > 0:
        for current_days, prev_days in _weeks_with_previous(dates, previous_dates):
            for e in range(num_employees):
                prev_off = 0
                if previous_schedule and previous_dates and prev_days:
                    emp_id = roster[e].get("id", f"emp_{e}")
                    if emp_id in previous_schedule:
                        prev_shifts = previous_schedule[emp_id]
                        for pd in prev_days:
                            if pd < len(prev_shifts) and prev_shifts[pd] == "O":
                                prev_off += 1
                required = min_days_off - prev_off
                if required <= 0:
                    continue
                if current_days:
                    model.add(
                        sum(work[e, off_index, d] for d in current_days)
                        >= required
                    )

    # Optional max days off per week (Monday–Sunday). Uses calendar weeks; completes
    # partial weeks with previous month when previous_schedule is provided.
    max_days_off = constraints.get("max_days_off_per_week")
    if off_index is not None and max_days_off is not None and max_days_off > 0:
        for current_days, prev_days in _weeks_with_previous(dates, previous_dates):
            for e in range(num_employees):
                prev_off = 0
                if previous_schedule and previous_dates and prev_days:
                    emp_id = roster[e].get("id", f"emp_{e}")
                    if emp_id in previous_schedule:
                        prev_shifts = previous_schedule[emp_id]
                        for pd in prev_days:
                            if pd < len(prev_shifts) and prev_shifts[pd] == "O":
                                prev_off += 1
                allowed = max_days_off - prev_off
                if current_days:
                    model.add(
                        sum(work[e, off_index, d] for d in current_days)
                        <= allowed
                    )

    # Optional: require min_days_off to be consecutive within rolling 7-day windows.
    # Uses an automaton to ensure at least min_days_off consecutive off days exist
    # in every 7-day window. Only applies when min_days_off >= 2.
    require_consecutive_off = constraints.get("require_consecutive_off", False)
    min_consecutive = constraints.get("min_days_off_per_week", 0) or 0
    if require_consecutive_off and off_index is not None and min_consecutive >= 2:
        # Build automaton: states 0..min_consecutive where state min_consecutive = "satisfied"
        # Symbols: 0 = work, 1 = off
        num_states = min_consecutive + 1
        initial_state = 0
        final_states = [min_consecutive]
        transition_triples = []
        for state in range(num_states):
            for symbol in [0, 1]:  # 0=work, 1=off
                if state == min_consecutive:
                    # Already satisfied, stay satisfied
                    next_state = min_consecutive
                elif symbol == 0:
                    # Work: reset consecutive count to 0
                    next_state = 0
                else:
                    # Off: increment consecutive count
                    next_state = state + 1
                transition_triples.append((state, symbol, next_state))

        window_size = 7
        previous_schedule = constraints.get("previous_schedule")
        previous_dates = constraints.get("previous_dates")

        for e in range(num_employees):
            emp_id = roster[e].get("id", f"emp_{e}")

            # Get previous month's last (window_size - 1) off/work status
            prev_off_status = []
            if previous_schedule and previous_dates and emp_id in previous_schedule:
                prev_shifts = previous_schedule[emp_id]
                K = min(window_size - 1, len(prev_shifts))
                for i in range(len(prev_shifts) - K, len(prev_shifts)):
                    prev_off_status.append(1 if prev_shifts[i] == "O" else 0)

            # Build sequence of "is off" indicators for this employee
            # Previous month's days as int constants + current month as BoolVars
            off_seq = []
            for val in prev_off_status:
                off_seq.append(val)  # Constant
            for d in range(num_days):
                off_seq.append(work[e, off_index, d])  # BoolVar (1=off)

            # Apply automaton to each 7-day window that includes at least one current-month day
            num_prev = len(prev_off_status)
            for start in range(len(off_seq) - window_size + 1):
                # Skip windows entirely in previous month
                if start + window_size <= num_prev:
                    continue
                window_vars = off_seq[start : start + window_size]
                model.add_automaton(
                    window_vars,
                    initial_state,
                    final_states,
                    transition_triples,
                )

    # Optional max consecutive work days. None or 0 = disabled.
    # When previous_schedule is provided, extend to consider last K days of previous month.
    max_consecutive = constraints.get("max_consecutive_work_days")
    previous_schedule = constraints.get("previous_schedule")
    previous_dates = constraints.get("previous_dates")
    if max_consecutive is not None and max_consecutive > 0 and off_index is not None:
        for e in range(num_employees):
            emp_id = roster[e].get("id", f"emp_{e}")
            prev_working = []
            if (
                previous_schedule
                and previous_dates
                and emp_id in previous_schedule
            ):
                prev_shifts = previous_schedule[emp_id]
                K = min(max_consecutive, len(prev_shifts))
                for i in range(len(prev_shifts) - K, len(prev_shifts)):
                    shift_name = prev_shifts[i]
                    prev_working.append(1 if shift_name != "O" else 0)
            working = []
            for d in range(num_days):
                w_var = model.new_bool_var(f"working{e}_{d}")
                model.add(w_var + work[e, off_index, d] == 1)
                working.append(w_var)
            for start in range(num_days - max_consecutive):
                model.add_bool_or(
                    [~working[i] for i in range(start, start + max_consecutive + 1)]
                )
            # Sequence continuity: if last max_consecutive days of prev were all working, day 0 must be off
            if prev_working and len(prev_working) >= max_consecutive:
                if all(prev_working[-max_consecutive:]):
                    model.add(work[e, off_index, 0] == 1)

    # Optional max consecutive off days. None or 0 = disabled.
    # When previous_schedule is provided, extend to consider last K days of previous month.
    max_consecutive_off = constraints.get("max_consecutive_off_days")
    if max_consecutive_off is not None and max_consecutive_off > 0 and off_index is not None:
        for e in range(num_employees):
            emp_id = roster[e].get("id", f"emp_{e}")
            prev_off = []
            if (
                previous_schedule
                and previous_dates
                and emp_id in previous_schedule
            ):
                prev_shifts = previous_schedule[emp_id]
                K = min(max_consecutive_off, len(prev_shifts))
                for i in range(len(prev_shifts) - K, len(prev_shifts)):
                    shift_name = prev_shifts[i]
                    prev_off.append(1 if shift_name == "O" else 0)
            # Build working vars (1 = working, 0 = off)
            working = []
            for d in range(num_days):
                w_var = model.new_bool_var(f"working_off{e}_{d}")
                model.add(w_var + work[e, off_index, d] == 1)
                working.append(w_var)
            # Constraint: in any (max+1) consecutive days, at least one must be work
            for start in range(num_days - max_consecutive_off):
                model.add_bool_or(
                    [working[i] for i in range(start, start + max_consecutive_off + 1)]
                )
            # Sequence continuity: if last max_consecutive_off days of prev were all off, day 0 must be work
            if prev_off and len(prev_off) >= max_consecutive_off:
                if all(prev_off[-max_consecutive_off:]):
                    # Day 0 must NOT be off (must work)
                    model.add(work[e, off_index, 0] == 0)

    # Optional max weekend work shifts for women.
    max_weekend_work_shifts_women = constraints.get("max_weekend_work_shifts_women")
    if max_weekend_work_shifts_women is not None:
        weekend_days = [d for d in range(num_days) if dates[d].weekday() >= 5]
        for e in range(num_employees):
            if roster[e].get("gender") == "F":
                model.add(
                    sum(
                        work[e, s, d]
                        for s in work_shift_indices
                        for d in weekend_days
                    )
                    <= max_weekend_work_shifts_women
                )

    # Sunday-specific constraints.
    sunday_indices = [d for d in range(num_days) if dates[d].weekday() == 6]
    min_sunday_off = int(constraints.get("min_sunday_off_per_month", 0))
    min_sunday_off_women = int(constraints.get("min_sunday_off_women", 0))
    women_alternate = constraints.get("women_sunday_off_alternate", True)
    if debug_sunday and sunday_indices:
        sun_dates = [str(dates[d]) for d in sunday_indices]
        print(
            f"[DEBUG:sunday] build_model: off_index={off_index}, sunday_indices={sunday_indices} "
            f"-> {sun_dates}"
        )
        print(
            f"[DEBUG:sunday] build_model: min_sunday_off={min_sunday_off}, "
            f"min_sunday_off_women={min_sunday_off_women}, women_alternate={women_alternate}"
        )
    if off_index is not None and min_sunday_off > 0 and sunday_indices:
        for e in range(num_employees):
            model.add(
                sum(work[e, off_index, d] for d in sunday_indices) >= min_sunday_off
            )

    def _is_woman(e: int) -> bool:
        return str(roster[e].get("gender", "M")).upper() == "F"

    # Min Sundays off for women (when enabled)
    if off_index is not None and min_sunday_off_women > 0 and sunday_indices:
        for e in range(num_employees):
            if _is_woman(e):
                model.add(
                    sum(work[e, off_index, d] for d in sunday_indices)
                    >= min_sunday_off_women
                )
    # Legal rule: women cannot be off on two consecutive Sundays (always enforced when enabled)
    women_added = []
    if (
        off_index is not None
        and women_alternate
        and sunday_indices
        and len(sunday_indices) >= 2
    ):
        for e in range(num_employees):
            if _is_woman(e):
                women_added.append((e, roster[e].get("id", f"emp_{e}")))
                for i in range(len(sunday_indices) - 1):
                    model.add(
                        work[e, off_index, sunday_indices[i]]
                        + work[e, off_index, sunday_indices[i + 1]]
                        >= 1

                    )
    if debug_sunday:
        if women_added:
            emp_list = ", ".join(f"{emp_id} (idx {e})" for e, emp_id in women_added[:5])
            if len(women_added) > 5:
                emp_list += f", ... ({len(women_added)} women total)"
            print(f"[DEBUG:sunday] women_alternate: added constraint for {emp_list}")
        else:
            reasons = []
            if off_index is None:
                reasons.append("off_index is None")
            if not women_alternate:
                reasons.append("women_alternate is False")
            if not sunday_indices or len(sunday_indices) < 2:
                reasons.append(f"len(sunday_indices)={len(sunday_indices)} < 2")
            if not any(_is_woman(e) for e in range(num_employees)):
                reasons.append("no women in roster")
            print(f"[DEBUG:sunday] women_alternate: SKIPPED - {', '.join(reasons)}")

    # Spread Sunday shifts evenly (soft).
    spread_penalty = int(constraints.get("spread_sunday_shifts_penalty", 0))
    if spread_penalty > 0 and sunday_indices:
        sunday_work = []
        for e in range(num_employees):
            sw = model.new_int_var(
                0, len(sunday_indices) * len(work_shift_indices), f"sunday_work_{e}"
            )
            model.add(
                sw
                == sum(
                    work[e, s, d]
                    for s in work_shift_indices
                    for d in sunday_indices
                )
            )
            sunday_work.append(sw)
        max_sun = model.new_int_var(
            0, len(sunday_indices) * len(work_shift_indices), "max_sunday_work"
        )
        min_sun = model.new_int_var(
            0, len(sunday_indices) * len(work_shift_indices), "min_sunday_work"
        )
        model.add_max_equality(max_sun, sunday_work)
        model.add_min_equality(min_sun, sunday_work)
        spread_var = model.new_int_var(
            0,
            len(sunday_indices) * len(work_shift_indices),
            "spread_sunday_shifts",
        )
        model.add(spread_var == max_sun - min_sun)
        obj_int_vars.append(spread_var)
        obj_int_coeffs.append(spread_penalty)

    # Spread total shifts evenly across roster (soft).
    spread_shifts_penalty = int(constraints.get("spread_shifts_penalty", 0))
    if spread_shifts_penalty > 0:
        total_work = []
        max_work_per_emp = num_days  # at most one work shift per day
        for e in range(num_employees):
            tw = model.new_int_var(0, max_work_per_emp, f"total_work_{e}")
            model.add(
                tw
                == sum(
                    work[e, s, d]
                    for s in work_shift_indices
                    for d in range(num_days)
                )
            )
            total_work.append(tw)
        max_total = model.new_int_var(0, max_work_per_emp, "max_total_work")
        min_total = model.new_int_var(0, max_work_per_emp, "min_total_work")
        model.add_max_equality(max_total, total_work)
        model.add_min_equality(min_total, total_work)
        spread_total_var = model.new_int_var(
            0, max_work_per_emp, "spread_total_shifts"
        )
        model.add(spread_total_var == max_total - min_total)
        obj_int_vars.append(spread_total_var)
        obj_int_coeffs.append(spread_shifts_penalty)

    # Penalty when employee works less than n days per month (soft).
    min_work_days = int(constraints.get("min_work_days_per_month", 0))
    min_work_days_penalty = int(constraints.get("min_work_days_penalty", 0))
    if min_work_days > 0 and min_work_days_penalty > 0:
        for e in range(num_employees):
            total_work_e = model.new_int_var(0, num_days, f"total_work_for_min_{e}")
            model.add(
                total_work_e
                == sum(
                    work[e, s, d]
                    for s in work_shift_indices
                    for d in range(num_days)
                )
            )
            shortage = model.new_int_var(
                0, min(min_work_days, num_days), f"min_work_shortage_{e}"
            )
            model.add(shortage >= min_work_days - total_work_e)
            obj_int_vars.append(shortage)
            obj_int_coeffs.append(min_work_days_penalty)

    # Minimize off days (maximize utilization). When enabled, adds a penalty for each
    # off day, causing the solver to prefer assigning more work shifts even beyond
    # the minimum demand. Useful for Solution 1 (actual roster) where we want everyone
    # working as much as possible given constraints.
    minimize_off_penalty = int(constraints.get("minimize_off_days_penalty", 0))
    if minimize_off_penalty > 0 and off_index is not None:
        for e in range(num_employees):
            off_days_e = model.new_int_var(0, num_days, f"off_days_{e}")
            model.add(
                off_days_e == sum(work[e, off_index, d] for d in range(num_days))
            )
            obj_int_vars.append(off_days_e)
            obj_int_coeffs.append(minimize_off_penalty)

    # Soft stability: prefer same shift as previous month (by weekday)
    stability_penalty = int(constraints.get("stability_penalty", 0))
    if (
        stability_penalty > 0
        and previous_schedule
        and previous_dates
    ):
        # Build weekday -> last day index in previous month for each weekday
        last_weekday_in_prev = {}
        for d, date in enumerate(previous_dates):
            w = date.weekday()
            last_weekday_in_prev[w] = d
        for e in range(num_employees):
            emp_id = roster[e].get("id", f"emp_{e}")
            if emp_id not in previous_schedule:
                continue
            prev_shifts = previous_schedule[emp_id]
            for d in range(num_days):
                w = dates[d].weekday()
                if w not in last_weekday_in_prev:
                    continue
                prev_d = last_weekday_in_prev[w]
                if prev_d >= len(prev_shifts):
                    continue
                preferred_shift = prev_shifts[prev_d]
                if preferred_shift not in shift_index:
                    continue
                s_preferred = shift_index[preferred_shift]
                obj_bool_vars.append(work[e, s_preferred, d])
                obj_bool_coeffs.append(-stability_penalty)

    # Fixed shift constraint: each employee always works the same shift.
    # Mode "model": solver picks which shift each employee is assigned to.
    # Mode "roster": shift is pre-defined in roster[e]["shift"].
    fixed_shift_mode = constraints.get("fixed_shift_mode")
    # Pre-assigned shifts from previous solution (for consistency between Solution 1 & 2)
    fixed_shift_assignments = constraints.get("fixed_shift_assignments", {})
    if fixed_shift_mode == "model":
        # Create decision vars: assigned_shift[e, s] = 1 if employee e is assigned to shift s
        assigned_shift = {}
        for e in range(num_employees):
            emp_id = roster[e].get("id", f"emp_{e}") if roster else f"emp_{e}"
            pre_assigned = fixed_shift_assignments.get(emp_id)
            if pre_assigned is not None and pre_assigned in shift_index:
                # Employee has pre-assigned shift from previous solution: fix it
                assigned_s = shift_index[pre_assigned]
                for s in work_shift_indices:
                    if s == assigned_s:
                        assigned_shift[e, s] = model.new_constant(1)
                    else:
                        assigned_shift[e, s] = model.new_constant(0)
                        for d in range(num_days):
                            model.add(work[e, s, d] == 0)
            else:
                # New employee or no prior assignment: solver picks
                for s in work_shift_indices:
                    assigned_shift[e, s] = model.new_bool_var(f"assigned_shift_{e}_{s}")
                model.add_exactly_one(assigned_shift[e, s] for s in work_shift_indices)
                for s in work_shift_indices:
                    for d in range(num_days):
                        model.add(work[e, s, d] <= assigned_shift[e, s])
    elif fixed_shift_mode == "roster":
        # Shift is pre-defined in roster; forbid working any other shift.
        # Employees without a 'shift' field (e.g., padded during hire search) use model behavior.
        employees_without_shift = []
        for e in range(num_employees):
            emp_shift = roster[e].get("shift")
            if emp_shift is None:
                employees_without_shift.append(e)
                continue
            if emp_shift not in shift_index:
                raise ValueError(
                    f"Employee {roster[e].get('id', e)} has shift '{emp_shift}' "
                    f"which is not in shifts: {shifts}"
                )
            assigned_s = shift_index[emp_shift]
            # Forbid all other work shifts
            for s in work_shift_indices:
                if s != assigned_s:
                    for d in range(num_days):
                        model.add(work[e, s, d] == 0)
        # For employees without shift: use model-based assignment (solver picks)
        if employees_without_shift:
            for e in employees_without_shift:
                assigned_shift_e = {}
                for s in work_shift_indices:
                    assigned_shift_e[s] = model.new_bool_var(f"assigned_shift_{e}_{s}")
                model.add_exactly_one(assigned_shift_e[s] for s in work_shift_indices)
                for s in work_shift_indices:
                    for d in range(num_days):
                        model.add(work[e, s, d] <= assigned_shift_e[s])

    # Objective
    if obj_bool_vars or obj_int_vars:
        model.minimize(
            sum(
                obj_bool_vars[i] * obj_bool_coeffs[i]
                for i in range(len(obj_bool_vars))
            )
            + sum(
                obj_int_vars[i] * obj_int_coeffs[i]
                for i in range(len(obj_int_vars))
            )
        )
    else:
        model.minimize(0)

    return (
        model,
        work,
        obj_bool_vars,
        obj_bool_coeffs,
        obj_int_vars,
        obj_int_coeffs,
    )


def validate_women_sunday_alternate(
    solver: cp_model.CpSolver,
    work: Dict[Tuple[int, int, int], cp_model.BoolVarT],
    num_employees: int,
    dates: list[dt.date],
    shifts: list[str],
    roster: Optional[list[dict[str, Any]]] = None,
    debug: bool = False,
) -> list[str]:
    """Check that no woman is off on two consecutive Sundays.

    Uses the same logic as format_schedule: derive shift per (e,d) by finding
    which s has work[e,s,d]=1. Returns list of violation messages, empty if OK.
    """
    num_days = len(dates)
    num_shifts = len(shifts)
    sunday_indices = [d for d in range(num_days) if dates[d].weekday() == 6]
    if len(sunday_indices) < 2:
        return []
    roster_len = len(roster) if roster else 0
    women_count = (
        sum(
            1
            for e in range(min(num_employees, roster_len))
            if roster and str(roster[e].get("gender", "M")).upper() == "F"
        )
        if roster
        else 0
    )
    if debug:
        print(
            f"[DEBUG:sunday] validate: roster_len={roster_len}, num_employees={num_employees}, "
            f"women={women_count}"
        )
    violations = []
    for e in range(num_employees):
        # Only check women; need roster to identify gender
        if roster is None or e >= len(roster):
            continue
        gender = str(roster[e].get("gender", "M")).upper()
        if gender != "F":
            continue
        # Derive shift per day (same logic as format_schedule)
        shift_per_day = []
        for d in range(num_days):
            assigned = None
            for s in range(num_shifts):
                key = (e, s, d)
                if key not in work:
                    continue
                if solver.boolean_value(work[key]):
                    assigned = shifts[s] if s < len(shifts) else "?"
                    break
            shift_per_day.append(assigned)
        sunday_shifts = [
            shift_per_day[d] if d < len(shift_per_day) else "?"
            for d in sunday_indices
        ]
        if debug:
            emp_id = roster[e].get("id", f"emp_{e}")
            print(
                f"[DEBUG:sunday] validate: emp {emp_id} (idx {e}): Sundays {sunday_indices} -> {sunday_shifts}"
            )
        # Check consecutive Sundays
        for i in range(len(sunday_indices) - 1):
            d1, d2 = sunday_indices[i], sunday_indices[i + 1]
            s1 = shift_per_day[d1] if d1 < len(shift_per_day) else None
            s2 = shift_per_day[d2] if d2 < len(shift_per_day) else None
            if s1 != "O" and s2 != "O":
                emp_id = roster[e].get("id", f"emp_{e}")
                violations.append(
                    f"Employee {emp_id} (F): worked consecutive Sundays "
                    f"{dates[d1]} and {dates[d2]}"
                )
                if debug:
                    print(
                        f"[DEBUG:sunday] VIOLATION: emp {emp_id} (idx {e}): off on "
                        f"{dates[d1]} and {dates[d2]}"
                    )
    return violations


def compute_understaff_summary(
    solver: cp_model.CpSolver,
    work: Dict[Tuple[int, int, int], cp_model.BoolVarT],
    demand: list[Dict[str, int]],
    dates: list[dt.date],
    shifts: list[str],
    num_employees: int,
) -> list[tuple[int, str, int, int]]:
    """Return list of (day_index, shift_name, required, assigned) for understaffed slots."""
    shift_index = {name: idx for idx, name in enumerate(shifts)}
    result = []
    for d in range(len(dates)):
        for shift_name in shifts:
            if shift_name == "O":
                continue
            required = demand[d][shift_name]
            s_idx = shift_index[shift_name]
            assigned = sum(
                1 for e in range(num_employees) if solver.boolean_value(work[e, s_idx, d])
            )
            if required > assigned:
                result.append((d, shift_name, required, assigned))
    return result


def recommend_hiring_gender_split(
    roster: list[dict[str, Any]],
    num_to_hire: int,
    target_min_women_ratio: float = 0.4,
    target_max_women_ratio: float = 0.6,
) -> tuple[int, int]:
    """Recommend how many men and women to hire to maintain gender balance.

    Returns (n_men_to_hire, n_women_to_hire) with n_men_to_hire + n_women_to_hire == num_to_hire.
    """
    n_m = sum(1 for e in roster if e.get("gender") == "M")
    n_f = sum(1 for e in roster if e.get("gender") == "F")
    total_after = n_m + n_f + num_to_hire
    target = (target_min_women_ratio + target_max_women_ratio) / 2
    desired_f_after = target * total_after
    h_f = round(desired_f_after - n_f)
    h_f = max(0, min(num_to_hire, h_f))
    h_m = num_to_hire - h_f
    return (h_m, h_f)


def extract_shift_assignments(
    solver: cp_model.CpSolver,
    work: Dict[Tuple[int, int, int], cp_model.BoolVarT],
    num_employees: int,
    shifts: list[str],
    dates: list[dt.date],
    roster: Optional[list[dict[str, Any]]] = None,
) -> Dict[str, str]:
    """Extract the shift each employee was assigned to from a solved model.

    Returns a dict mapping employee ID to their assigned shift name.
    """
    work_shifts = [s for s in shifts if s != "O"]
    shift_index = {s: i for i, s in enumerate(shifts)}
    assignments = {}
    num_days = len(dates)

    for e in range(num_employees):
        emp_id = roster[e].get("id", f"emp_{e}") if roster else f"emp_{e}"
        # Find which shift this employee worked most days on
        shift_days = {s: 0 for s in work_shifts}
        for s in work_shifts:
            s_idx = shift_index[s]
            for d in range(num_days):
                try:
                    if solver.value(work[e, s_idx, d]):
                        shift_days[s] += 1
                except (ValueError, KeyError):
                    continue
        # Assign to the shift they worked most (should be the only one with fixed_shift)
        if shift_days:
            assigned = max(shift_days, key=lambda x: shift_days[x])
            if shift_days[assigned] > 0:
                assignments[emp_id] = assigned

    return assignments


def recommend_hire_one_gender(
    roster: list[dict[str, Any]],
    target_min_women_ratio: float = 0.4,
    target_max_women_ratio: float = 0.6,
) -> str:
    """Recommend gender for the next single hire to balance ratio.

    Returns 'M' or 'F'. Hire a woman if too few women, a man if too few men.
    """
    n_m = sum(1 for e in roster if e.get("gender") == "M")
    n_f = sum(1 for e in roster if e.get("gender") == "F")
    total = n_m + n_f
    if total == 0:
        return "M"
    women_ratio = n_f / total
    if women_ratio < target_min_women_ratio:
        return "F"  # too few women
    if women_ratio > target_max_women_ratio:
        return "M"  # too few men
    target = (target_min_women_ratio + target_max_women_ratio) / 2
    return "F" if women_ratio < target else "M"


# Width of the "employee N: " prefix so day columns align across rows.
_EMPLOYEE_LABEL_WIDTH = 13
# Characters per day column (shift letter + space).
_CHARS_PER_DAY = 3


def format_schedule(
    solver: cp_model.CpSolver,
    work: Dict[Tuple[int, int, int], cp_model.BoolVarT],
    num_employees: int,
    shifts: list[str],
    dates: list[dt.date],
    roster: Optional[list[dict[str, Any]]] = None,
) -> list[str]:
    num_days = len(dates)
    num_shifts = len(shifts)
    lines = []
    for e in range(num_employees):
        schedule = []
        for d in range(num_days):
            for s in range(num_shifts):
                if solver.boolean_value(work[e, s, d]):
                    schedule.append(shifts[s])
                    break
        # Fixed-width label + exactly _CHARS_PER_DAY per day for alignment.
        if roster and e < len(roster) and roster[e].get("gender"):
            label = f"employee {e:>2} ({roster[e]['gender']}): "
        else:
            label = f"employee {e:>2}: "
        day_part = "".join(f"{s:<{_CHARS_PER_DAY}}" for s in schedule)
        lines.append(label + day_part)
    return lines


def print_solution(
    label: str,
    solver: cp_model.CpSolver,
    work: Dict[Tuple[int, int, int], cp_model.BoolVarT],
    num_employees: int,
    shifts: list[str],
    dates: list[dt.date],
    obj_bool_vars: list[cp_model.BoolVarT],
    obj_bool_coeffs: list[int],
    obj_int_vars: list[cp_model.IntVar],
    obj_int_coeffs: list[int],
    roster: Optional[list[dict[str, Any]]] = None,
    primary_start: Optional[int] = None,
    primary_end: Optional[int] = None,
) -> None:
    print()
    print(f"{label}")
    schedule_lines = format_schedule(
        solver, work, num_employees, shifts, dates, roster
    )
    prefix_width = _EMPLOYEE_LABEL_WIDTH
    if schedule_lines and dates:
        first_label_len = len(schedule_lines[0]) - len(dates) * _CHARS_PER_DAY
        prefix_width = max(prefix_width, first_label_len)
    prefix = " " * (prefix_width - 2)

    def format_header(items: list[str]) -> str:
        if primary_start is not None and primary_end is not None:
            parts = []
            if primary_start > 0:
                parts.append("".join(items[:primary_start]))
                parts.append("|")
            parts.append("".join(items[primary_start:primary_end]))
            if primary_end < len(items):
                parts.append("|")
                parts.append("".join(items[primary_end:]))
            return prefix + "".join(parts)
        return prefix + "".join(items)

    header_days = format_header([f"{d.day:>{_CHARS_PER_DAY}}" for d in dates])
    header_week = format_header(
        [f"{d.strftime('%a')[0]:>{_CHARS_PER_DAY}}" for d in dates]
    )
    print(header_days)
    print(header_week)
    for i, line in enumerate(schedule_lines):
        if primary_start is not None and primary_end is not None:
            # Re-format line to add separators
            label = line[:prefix_width]
            content = line[prefix_width:]
            parts = []
            # chars per day = _CHARS_PER_DAY
            day_strs = [
                content[j * _CHARS_PER_DAY : (j + 1) * _CHARS_PER_DAY]
                for j in range(len(dates))
            ]
            if primary_start > 0:
                parts.append("".join(day_strs[:primary_start]))
                parts.append("|")
            parts.append("".join(day_strs[primary_start:primary_end]))
            if primary_end < len(day_strs):
                parts.append("|")
                parts.append("".join(day_strs[primary_end:]))
            print(label + "".join(parts))
        else:
            print(line)
    print()
    if obj_bool_vars or obj_int_vars:
        print("Penalties:")
    for i, var in enumerate(obj_bool_vars):
        if solver.boolean_value(var):
            rule = var.name or f"constraint_{i}"
            penalty = obj_bool_coeffs[i]
            if penalty > 0:
                print(f"  {rule}: violated, penalty={penalty}")
    for i, var in enumerate(obj_int_vars):
        if solver.value(var) > 0:
            rule = var.name or f"penalty_{i}"
            print(
                f"  {rule}: violated by {solver.value(var)}, "
                f"penalty weight={obj_int_coeffs[i]}"
            )


def solve_once(
    num_employees: int,
    dates: list[dt.date],
    shifts: list[str],
    demand: list[Dict[str, int]],
    constraints: Dict[str, object],
    params: str,
    output_proto: str,
    write_proto: bool,
    roster: Optional[list[dict[str, Any]]] = None,
) -> Dict[str, object]:
    if constraints.get("debug_sunday_constraints", False):
        roster_len = len(roster) if roster is not None else 0
        women = (
            sum(1 for e in roster if str(e.get("gender", "M")).upper() == "F")
            if roster
            else 0
        )
        print(
            f"[DEBUG:sunday] solve_once: num_employees={num_employees}, roster_len={roster_len}, "
            f"women={women}, roster_is_None={roster is None}"
        )
    (
        model,
        work,
        obj_bool_vars,
        obj_bool_coeffs,
        obj_int_vars,
        obj_int_coeffs,
    ) = build_model(num_employees, dates, shifts, demand, constraints, roster)

    if output_proto and write_proto:
        print(f"Writing proto to {output_proto}")
        with open(output_proto, "w") as text_file:
            text_file.write(str(model))

    solver = cp_model.CpSolver()
    if params:
        solver.parameters.parse_text_format(params)
    status = solver.solve(model)
    return {
        "status": status,
        "solver": solver,
        "work": work,
        "obj_bool_vars": obj_bool_vars,
        "obj_bool_coeffs": obj_bool_coeffs,
        "obj_int_vars": obj_int_vars,
        "obj_int_coeffs": obj_int_coeffs,
        "objective": solver.objective_value,
    }


def solve_store(
    params: str,
    output_proto: str,
    year: int,
    month: int,
    current_employees: int,
    min_employees: int,
    max_employees: int,
    base_m_weekday: int = 5,
    base_a_weekday: int = 4,
    base_m_weekend: int = 4,
    base_a_weekend: int = 3,
) -> None:
    dates = build_dates(year, month)
    shifts = ["O", "M", "A"]
    demand = default_demand(
        dates,
        base_m_weekday=base_m_weekday,
        base_a_weekday=base_a_weekday,
        base_m_weekend=base_m_weekend,
        base_a_weekend=base_a_weekend,
    )
    validate_demand(demand, dates, shifts)

    constraints: Dict[str, object] = {
        "excess_cover_penalties": {"M": 1, "A": 1},
        "sequence_constraints": [],
        "weekly_sum_constraints": [],
        "max_shifts_per_week": None,
        "min_days_off_per_week": None,
        "max_days_off_per_week": None,
        "max_consecutive_work_days": None,
    }

    print(f"Solving for {year}-{month:02d} with current roster...")
    current = solve_once(
        current_employees,
        dates,
        shifts,
        demand,
        constraints,
        params,
        output_proto,
        write_proto=True,
    )
    if current["status"] in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print_solution(
            f"Current roster: {current_employees} employees",
            current["solver"],
            current["work"],
            current_employees,
            shifts,
            dates,
            current["obj_bool_vars"],
            current["obj_bool_coeffs"],
            current["obj_int_vars"],
            current["obj_int_coeffs"],
        )
    else:
        print(
            "Current roster infeasible:",
            cp_model.CpSolver().status_name(current["status"]),
        )

    best = None
    for num_employees in range(min_employees, max_employees + 1):
        result = solve_once(
            num_employees,
            dates,
            shifts,
            demand,
            constraints,
            params,
            output_proto,
            write_proto=False,
        )
        status = result["status"]
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            continue
        if best is None:
            best = (num_employees, result)
            continue
        best_employees, best_result = best
        if result["objective"] < best_result["objective"]:
            best = (num_employees, result)
            continue
        if result["objective"] == best_result["objective"]:
            if abs(num_employees - current_employees) < abs(
                best_employees - current_employees
            ):
                best = (num_employees, result)

    if best is None:
        print()
        print(
            f"No feasible schedule found between {min_employees} and {max_employees}."
        )
        return

    best_employees, best_result = best
    print_solution(
        f"Best roster within bounds: {best_employees} employees",
        best_result["solver"],
        best_result["work"],
        best_employees,
        shifts,
        dates,
        best_result["obj_bool_vars"],
        best_result["obj_bool_coeffs"],
        best_result["obj_int_vars"],
        best_result["obj_int_coeffs"],
    )
    print()
    print(best_result["solver"].response_stats())


def main(_):
    solve_store(
        _PARAMS.value,
        _OUTPUT_PROTO.value,
        _YEAR.value,
        _MONTH.value,
        _CURRENT_EMPLOYEES.value,
        _MIN_EMPLOYEES.value,
        _MAX_EMPLOYEES.value,
        base_m_weekday=_DEMAND_BASE_M_WEEKDAY.value,
        base_a_weekday=_DEMAND_BASE_A_WEEKDAY.value,
        base_m_weekend=_DEMAND_BASE_M_WEEKEND.value,
        base_a_weekend=_DEMAND_BASE_A_WEEKEND.value,
    )


if __name__ == "__main__":
    app.run(main)
