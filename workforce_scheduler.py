"""
Workforce scheduling with OR-Tools CP-SAT: assign employees to (store, shift)
so demand is covered and constraints are satisfied. See SPEC.md.
"""

from __future__ import annotations

from typing import Any

from ortools.sat.python import cp_model


def schedule_workforce(
    demand: dict[tuple[str, str], int],
    employees: list[str],
    stores: list[str],
    shifts: list[str],
    *,
    availability: dict[tuple[str, str, str], bool] | None = None,
    max_shifts_per_employee: int | None = None,
    cost: dict[tuple[str, str, str], float] | None = None,
    minimize_assignments: bool = True,
) -> tuple[list[tuple[str, str, str]], dict[str, Any]]:
    """
    Assign employees to (store, shift) to cover demand.

    demand: (store, shift) -> required number of employees
    employees: list of employee ids
    stores: list of store ids
    shifts: list of shift ids (must match keys in demand)
    availability: optional (employee, store, shift) -> True if allowed
    max_shifts_per_employee: optional cap per employee
    cost: optional (employee, store, shift) -> cost; used if not minimize_assignments
    minimize_assignments: if True, minimize total assignments; else minimize total cost

    Returns:
      assignments: list of (employee, store, shift)
      info: dict with status, objective_value, understaffing (if any), etc.
    """
    model = cp_model.CpModel()

    # Decision variables: x[e, s, sh] = 1 if employee e works at store s in shift sh
    x: dict[tuple[str, str, str], cp_model.IntVar] = {}
    for e in employees:
        for s in stores:
            for sh in shifts:
                key = (e, s, sh)
                if availability is not None and not availability.get(key, True):
                    continue
                x[key] = model.NewBoolVar(f"x_{e}_{s}_{sh}")

    # Coverage: for each (store, shift), assigned count >= demand
    for (s, sh), req in demand.items():
        assigned = [x[k] for k in x if k[1] == s and k[2] == sh]
        if assigned:
            model.Add(sum(assigned) >= req)

    # Each employee at most once per shift (across stores)
    for e in employees:
        for sh in shifts:
            in_shift = [x[k] for k in x if k[0] == e and k[2] == sh]
            if in_shift:
                model.Add(sum(in_shift) <= 1)

    # Optional: max shifts per employee
    if max_shifts_per_employee is not None:
        for e in employees:
            total = [x[k] for k in x if k[0] == e]
            if total:
                model.Add(sum(total) <= max_shifts_per_employee)

    # Objective
    if minimize_assignments:
        model.Minimize(sum(x.values()))
    else:
        if cost:
            model.Minimize(
                sum(
                    cost.get(k, 1.0) * x[k]
                    for k in x
                )
            )
        else:
            model.Minimize(sum(x.values()))

    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    assignments: list[tuple[str, str, str]] = []
    info: dict[str, Any] = {
        "status": solver.StatusName(status),
        "feasible": status in (cp_model.OPTIMAL, cp_model.FEASIBLE),
        "objective_value": solver.ObjectiveValue() if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
    }

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for k, var in x.items():
            if solver.Value(var) == 1:
                assignments.append(k)
        info["assignments_count"] = len(assignments)
        info["assignments_by_store_shift"] = {}
        for (e, s, sh) in assignments:
            key = (s, sh)
            info["assignments_by_store_shift"].setdefault(key, []).append(e)
        info["assignments_by_employee"] = {}
        for (e, s, sh) in assignments:
            info["assignments_by_employee"].setdefault(e, []).append((s, sh))

    return assignments, info


def demand_from_estimation(
    required_per_store_period: dict[tuple[str, int], int],
    period_to_shift: list[str],
) -> dict[tuple[str, str], int]:
    """
    Convert estimation output to scheduler demand format.

    required_per_store_period: (store_id, period_index) -> required employees
    period_to_shift: list of shift ids, index = period_index

    Returns: (store, shift_id) -> required employees
    """
    demand: dict[tuple[str, str], int] = {}
    for (store, period_ix), req in required_per_store_period.items():
        if period_ix < len(period_to_shift):
            shift_id = period_to_shift[period_ix]
            demand[(store, shift_id)] = req
    return demand
