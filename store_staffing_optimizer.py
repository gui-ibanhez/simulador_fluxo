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


def build_dates(year: int, month: int) -> list[dt.date]:
    _, num_days = calendar.monthrange(year, month)
    return [dt.date(year, month, day) for day in range(1, num_days + 1)]


def _week_sunday_saturday(dates: list[dt.date]) -> Dict[int, list[int]]:
    """Group day indices by calendar week (Sunday–Saturday).
    Returns {week_key: [day_indices]}. week_key is days since epoch of the week's Sunday.
    """
    groups: Dict[int, list[int]] = {}
    for d, date in enumerate(dates):
        days_since_sunday = (date.weekday() + 1) % 7
        sunday = date - dt.timedelta(days=days_since_sunday)
        key = sunday.toordinal()
        groups.setdefault(key, []).append(d)
    return groups


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
    if roster is None:
        roster = [{"gender": "M"} for _ in range(num_employees)]
    else:
        roster = list(roster)
        while len(roster) < num_employees:
            roster.append({"gender": "M"})

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
            if not relax_cover and required > num_employees:
                raise ValueError(
                    f"Demand {required} exceeds employees {num_employees} on day {d}."
                )
            s_index = shift_index[shift_name]
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
                    excess = model.new_int_var(0, num_employees - required, "")
                    model.add(excess == worked - required)
                    obj_int_vars.append(excess)
                    obj_int_coeffs.append(penalty)

    # Optional sequence constraints per shift.
    for ct in constraints.get("sequence_constraints", []):
        shift_ref, hard_min, soft_min, min_cost, soft_max, hard_max, max_cost = ct
        s_index = shift_index[shift_ref] if isinstance(shift_ref, str) else shift_ref
        for e in range(num_employees):
            works = [work[e, s_index, d] for d in range(num_days)]
            variables, coeffs = add_soft_sequence_constraint(
                model,
                works,
                hard_min,
                soft_min,
                min_cost,
                soft_max,
                hard_max,
                max_cost,
                f"seq(employee {e}, shift {s_index})",
            )
            obj_bool_vars.extend(variables)
            obj_bool_coeffs.extend(coeffs)

    # Optional weekly sum constraints (7-day blocks from day 0).
    for ct in constraints.get("weekly_sum_constraints", []):
        shift_ref, hard_min, soft_min, min_cost, soft_max, hard_max, max_cost = ct
        s_index = shift_index[shift_ref] if isinstance(shift_ref, str) else shift_ref
        for e in range(num_employees):
            for w in range((num_days + 6) // 7):
                week_days = [d for d in range(w * 7, min((w + 1) * 7, num_days))]
                works = [work[e, s_index, d] for d in week_days]
                variables, coeffs = add_soft_sum_constraint(
                    model,
                    works,
                    hard_min,
                    soft_min,
                    min_cost,
                    soft_max,
                    hard_max,
                    max_cost,
                    f"weekly(employee {e}, shift {s_index}, week {w})",
                )
                obj_int_vars.extend(variables)
                obj_int_coeffs.extend(coeffs)

    # Optional max shifts per week (working shifts only). None or 0 = disabled.
    max_shifts_per_week = constraints.get("max_shifts_per_week")
    if max_shifts_per_week is not None and max_shifts_per_week > 0:
        for e in range(num_employees):
            for w in range((num_days + 6) // 7):
                week_days = [d for d in range(w * 7, min((w + 1) * 7, num_days))]
                model.add(
                    sum(
                        work[e, s, d]
                        for s in work_shift_indices
                        for d in week_days
                    )
                    <= max_shifts_per_week
                )

    # Optional min days off per week (Sunday–Saturday). Only applied when set.
    min_days_off = constraints.get("min_days_off_per_week")
    if off_index is not None and min_days_off is not None and min_days_off > 0:
        week_groups = _week_sunday_saturday(dates)
        for e in range(num_employees):
            for week_days in week_groups.values():
                if len(week_days) > 0:
                    model.add(
                        sum(work[e, off_index, d] for d in week_days)
                        >= min_days_off
                    )

    # Optional max consecutive work days. None or 0 = disabled.
    max_consecutive = constraints.get("max_consecutive_work_days")
    if max_consecutive is not None and max_consecutive > 0 and off_index is not None:
        for e in range(num_employees):
            working = []
            for d in range(num_days):
                w_var = model.new_bool_var(f"working{e}_{d}")
                model.add(w_var + work[e, off_index, d] == 1)
                working.append(w_var)
            for start in range(num_days - max_consecutive):
                model.add_bool_or(
                    [~working[i] for i in range(start, start + max_consecutive + 1)]
                )

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
    header_days = prefix + "".join(f"{d.day:>{_CHARS_PER_DAY}}" for d in dates)
    header_week = prefix + "".join(
        f"{d.strftime('%a')[0]:>{_CHARS_PER_DAY}}" for d in dates
    )
    print(header_days)
    print(header_week)
    for line in schedule_lines:
        print(line)
    print()
    if obj_bool_vars or obj_int_vars:
        print("Penalties:")
    for i, var in enumerate(obj_bool_vars):
        if solver.boolean_value(var):
            penalty = obj_bool_coeffs[i]
            if penalty > 0:
                print(f"  {var.name} violated, penalty={penalty}")
            else:
                print(f"  {var.name} fulfilled, gain={-penalty}")
    for i, var in enumerate(obj_int_vars):
        if solver.value(var) > 0:
            print(
                f"  {var.name} violated by {solver.value(var)}, linear"
                f" penalty={obj_int_coeffs[i]}"
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
