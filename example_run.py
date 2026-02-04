"""
Example: estimate required employees from customer flow, then schedule workforce.

See SPEC.md for the overall flow.
"""

from staffing_estimation import estimate_required_employees
from workforce_scheduler import demand_from_estimation, schedule_workforce


def main() -> None:
    # --- 1) Customer flow: customers per store per period (e.g. 4 periods = 4 shifts) ---
    customer_flow = {
        "store_A": [120, 80, 200, 90],   # 4 periods
        "store_B": [50, 60, 70, 55],
        "store_C": [200, 180, 220, 150],
    }

    # --- 2) Staffing estimation: flow -> required employees per store per period ---
    required = estimate_required_employees(
        customer_flow,
        rule="ratio",
        customers_per_employee=40.0,
        min_employees=1,
    )
    print("Required employees per (store, period):")
    for (store, period_ix), n in sorted(required.items()):
        print(f"  {store} period {period_ix}: {n}")

    # Map period index to shift id for the scheduler
    shifts = ["Mon-AM", "Mon-PM", "Tue-AM", "Tue-PM"]
    demand = demand_from_estimation(required, shifts)
    stores = list(customer_flow.keys())

    # --- 3) Workforce: employees and scheduling ---
    employees = [f"Emp_{i}" for i in range(1, 15)]  # 14 employees

    assignments, info = schedule_workforce(
        demand=demand,
        employees=employees,
        stores=stores,
        shifts=shifts,
        max_shifts_per_employee=4,
        minimize_assignments=True,
    )

    print("\nSolver status:", info["status"])
    print("Total assignments:", info.get("assignments_count", 0))
    if info.get("objective_value") is not None:
        print("Objective (total assignments):", info["objective_value"])

    print("\nAssignments (employee -> store, shift):")
    for e, s, sh in sorted(assignments):
        print(f"  {e} -> {s}, {sh}")

    print("\nCoverage per store per shift:")
    for (s, sh), emp_list in sorted(info.get("assignments_by_store_shift", {}).items()):
        req = demand.get((s, sh), 0)
        print(f"  {s} {sh}: {len(emp_list)} assigned (required {req}) -> {emp_list}")


if __name__ == "__main__":
    main()
