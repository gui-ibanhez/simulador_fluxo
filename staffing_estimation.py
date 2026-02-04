"""
Staffing estimation: customer flow → required employees per store per period.

See SPEC.md for inputs/outputs and rules.
"""

from __future__ import annotations

import math
from typing import Any, Callable


# --- Staffing rules -----------------------------------------------------------

def employees_by_ratio(
    customers: float,
    customers_per_employee: float,
    min_employees: int = 1,
) -> int:
    """Required employees = ceil(customers / N), at least min_employees."""
    if customers <= 0:
        return 0
    n = max(1.0, customers_per_employee)
    return max(min_employees, math.ceil(customers / n))


def employees_by_tiers(customers: float, tiers: list[tuple[float, int]]) -> int:
    """
    tiers: list of (max_customers, employees), e.g. [(50,1), (100,2), (200,3), (float('inf'),4)].
    Returns employees for the first tier where customers <= max_customers.
    """
    for max_cust, emp in tiers:
        if customers <= max_cust:
            return emp
    return tiers[-1][1] if tiers else 0


def employees_by_formula(
    customers: float,
    customers_per_employee: float,
    base_employees: int = 0,
    min_employees: int = 0,
) -> int:
    """Required employees = base + ceil(customers / N), at least min_employees."""
    if customers <= 0:
        return max(min_employees, base_employees)
    n = max(1.0, customers_per_employee)
    return max(min_employees, base_employees + math.ceil(customers / n))


# --- Main estimator -----------------------------------------------------------

def estimate_required_employees(
    customer_flow: dict[str, list[float]],
    rule: str = "ratio",
    period_to_shift: Callable[[int], str] | None = None,
    **kwargs: Any,
) -> dict[tuple[str, int], int]:
    """
    From customer flow per store per period, compute required employees per (store, period_index).

    customer_flow: { "store_id": [cust_period_0, cust_period_1, ... ], ... }
    rule: "ratio" | "tiers" | "formula"
    period_to_shift: optional callable(period_ix) -> shift_name; required when
        customers_per_employee (or formula params) is a dict per shift.
    kwargs: passed to the chosen rule (e.g. customers_per_employee, min_employees, tiers).
        customers_per_employee can be float (same for all) or dict[str, float] (per shift).

    Returns: { (store_id, period_index): required_employees }
    """
    demand: dict[tuple[str, int], int] = {}
    for store_id, flow in customer_flow.items():
        for period_ix, customers in enumerate(flow):
            if rule == "ratio":
                cpe = kwargs.get("customers_per_employee", 30.0)
                if isinstance(cpe, dict) and period_to_shift is not None:
                    shift = period_to_shift(period_ix)
                    cpe = cpe.get(shift, 30.0)
                n = employees_by_ratio(
                    customers,
                    cpe,
                    kwargs.get("min_employees", 1),
                )
            elif rule == "tiers":
                n = employees_by_tiers(
                    customers,
                    kwargs.get("tiers", [(50, 1), (100, 2), (200, 3), (float("inf"), 4)]),
                )
            elif rule == "formula":
                cpe = kwargs.get("customers_per_employee", 30.0)
                if isinstance(cpe, dict) and period_to_shift is not None:
                    shift = period_to_shift(period_ix)
                    cpe = cpe.get(shift, 30.0)
                n = employees_by_formula(
                    customers,
                    cpe,
                    kwargs.get("base_employees", 0),
                    kwargs.get("min_employees", 0),
                )
            else:
                raise ValueError(f"Unknown rule: {rule}")
            demand[(store_id, period_ix)] = n
    return demand
