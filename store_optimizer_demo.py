#!/usr/bin/env python3
"""Example usage for the store staffing optimizer with fake demand.

Supports two demand sources:
- --demand=direct: fake demand per (day, shift) (default).
- --demand=estimation: fake customer flow → staffing_estimation → demand (SPEC Part 1 → Part 2).
"""

import argparse
import random
from ortools.sat.python import cp_model

from store_staffing_optimizer import (
    build_dates,
    compute_understaff_summary,
    print_solution,
    recommend_hiring_gender_split,
    solve_once,
    validate_demand,
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
    return [{"gender": rng.choice(["M", "F"])} for _ in range(num_employees)]


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
):
    """Fake demand per (day, shift): list of { "M": n, "A": n } per day.

    Bases can be passed as:
      - profile: "balanced" | "weekend_heavy" | "weekday_heavy" (default when no explicit bases),
      - base_weekday=(base_M, base_A), base_weekend=(base_M, base_A), or
      - base_M_weekday, base_A_weekday, base_M_weekend, base_A_weekend (override profile).
    Explicit base_* values override the profile. If add_random=True (default), each day uses base ± {-1, 0, 1} (at least 1).
    If add_random=False, use bases exactly (deterministic).
    """
    if profile and profile in DEMAND_PROFILES:
        base_weekday = DEMAND_PROFILES[profile][:2]
        base_weekend = DEMAND_PROFILES[profile][2:]
    if base_M_weekday is not None and base_A_weekday is not None:
        base_weekday = (base_M_weekday, base_A_weekday)
    if base_M_weekend is not None and base_A_weekend is not None:
        base_weekend = (base_M_weekend, base_A_weekend)
    rng = random.Random(seed)
    demand = []
    for date in dates:
        if date.weekday() < 5:
            base_m, base_a = base_weekday
        else:
            base_m, base_a = base_weekend
        if add_random:
            m = max(1, base_m + rng.choice([-1, 0, 1]))
            a = max(1, base_a + rng.choice([-1, 0, 1]))
        else:
            m = max(1, base_m)
            a = max(1, base_a)
        demand.append({"M": m, "A": a})
    return demand


def make_fake_customer_flow(dates, work_shifts, seed, store_ids=None):
    """
    Fake customer flow per store: one value per (day, shift) per store.
    Returns { "store_id": [cust_period_0, cust_period_1, ... ], ... }.
    Period order: day0_M, day0_A, day1_M, day1_A, ...
    Each store gets different flow (seed + store_id hash) so demand varies by store.
    """
    if store_ids is None:
        store_ids = ["store"]
    out = {}
    for store_id in store_ids:
        rng = random.Random(seed + hash(store_id) % (2**32))
        num_days = len(dates)
        flow = []
        for d in range(num_days):
            weekday = dates[d].weekday()
            base = (120, 80) if weekday < 5 else (80, 60)  # M, A
            for s in range(len(work_shifts)):
                flow.append(max(0, base[s] + rng.randint(-30, 40)))
        out[store_id] = flow
    return out


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
    dates, work_shifts, customer_flow, rule="ratio", period_to_shift=None, **kwargs
):
    """
    Same as demand_from_estimation but returns demand per store: { store_id: demand_list }.
    Demand is variable by store (each store has its own flow → own required employees per shift).
    """
    if estimate_required_employees is None:
        raise ImportError("staffing_estimation not available")
    demand_by_store = {}
    for store_id, flow in customer_flow.items():
        raw = estimate_required_employees(
            {store_id: flow},
            rule=rule,
            period_to_shift=period_to_shift,
            **kwargs,
        )
        demand_by_store[store_id] = _demand_list_from_raw(raw, dates, work_shifts)
    return demand_by_store


def status_name(status):
    return cp_model.CpSolver().status_name(status)


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
    parser.add_argument("--current_employees", type=int, default=10, help="Current roster size.")
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
    parser.add_argument("--min_employees_estimation", type=int, default=1, help="Min employees per period (ratio/formula).")
    parser.add_argument("--base_employees", type=int, default=0, help="Formula: base employees per period.")
    parser.add_argument(
        "--tiers",
        type=str,
        default="50,1,100,2,200,3,inf,4",
        help="Tiers rule: max_cust,emp pairs e.g. '50,1,100,2,inf,4'.",
    )
    # Direct demand (fake) base counts per shift (weekday / weekend)
    parser.add_argument("--direct_base_M_weekday", type=int, default=5, help="Direct fake: base Morning (weekday).")
    parser.add_argument("--direct_base_A_weekday", type=int, default=4, help="Direct fake: base Afternoon (weekday).")
    parser.add_argument("--direct_base_M_weekend", type=int, default=4, help="Direct fake: base Morning (weekend).")
    parser.add_argument("--direct_base_A_weekend", type=int, default=3, help="Direct fake: base Afternoon (weekend).")
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
    parser.add_argument("--max_shifts_per_week", type=int, default=6, help="Max working shifts per employee per week. Default 6 so min_days_off(1)+max_shifts(6)=7. Use 0 to disable.")
    parser.add_argument("--min_days_off_per_week", type=int, default=1, help="Min rest days per employee per week (Sun–Sat). Use 0 to disable.")
    parser.add_argument("--max_consecutive_work_days", type=int, default=5, help="Max consecutive working days. Use 0 to disable.")
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
    # Solver
    parser.add_argument("--params", default="max_time_in_seconds:10.0", help="CP-SAT solver parameters.")
    parser.add_argument("--output_proto", default="", help="Write model proto to this file.")
    args = parser.parse_args()
    store_ids = [s.strip() for s in args.store_ids.split(",") if s.strip()]
    if not store_ids:
        store_ids = ["store"]

    # Print all parameters (including defaults)
    print("Parameters:")
    for name in sorted(vars(args)):
        val = getattr(args, name)
        print(f"  --{name}: {val!r}")

    dates = build_dates(args.year, args.month)
    shifts = ["O", "M", "A"]
    work_shifts = ["M", "A"]

    if args.demand == "direct":
        store_profiles = _parse_store_profiles(args.store_profiles)
        demand_by_store = {}
        for store_id in store_ids:
            profile = store_profiles.get(store_id, args.demand_profile)
            demand_one = make_fake_demand(
                dates,
                args.seed,
                base_weekday=(args.direct_base_M_weekday, args.direct_base_A_weekday),
                base_weekend=(args.direct_base_M_weekend, args.direct_base_A_weekend),
                base_M_weekday=args.direct_base_M_weekday,
                base_A_weekday=args.direct_base_A_weekday,
                base_M_weekend=args.direct_base_M_weekend,
                base_A_weekend=args.direct_base_A_weekend,
                add_random=not args.direct_deterministic,
                profile=profile,
            )
            demand_by_store[store_id] = demand_one
    else:
        customer_flow = make_fake_customer_flow(
            dates,
            work_shifts,
            args.seed,
            store_ids=store_ids,
        )
        period_to_shift = lambda i: work_shifts[i % len(work_shifts)]
        customers_per_employee = {
            "M": args.customers_per_employee_M,
            "A": args.customers_per_employee_A,
        }
        if args.rule == "ratio":
            demand_by_store = demand_from_estimation_by_store(
                dates,
                work_shifts,
                customer_flow,
                rule="ratio",
                period_to_shift=period_to_shift,
                customers_per_employee=customers_per_employee,
                min_employees=args.min_employees_estimation,
            )
        elif args.rule == "tiers":
            demand_by_store = demand_from_estimation_by_store(
                dates,
                work_shifts,
                customer_flow,
                rule="tiers",
                tiers=_parse_tiers(args.tiers),
            )
        else:
            demand_by_store = demand_from_estimation_by_store(
                dates,
                work_shifts,
                customer_flow,
                rule="formula",
                period_to_shift=period_to_shift,
                customers_per_employee=customers_per_employee,
                base_employees=args.base_employees,
                min_employees=args.min_employees_estimation,
            )
        for store_id in demand_by_store:
            for d in demand_by_store[store_id]:
                for s in work_shifts:
                    if d.get(s, 0) < 1:
                        d[s] = 1

    sequence_constraints = _parse_constraint_specs(args.sequence_constraints)
    weekly_sum_constraints = _parse_constraint_specs(args.weekly_sum_constraints)
    constraints = {
        "excess_cover_penalties": {"M": args.excess_penalty_M, "A": args.excess_penalty_A},
        "sequence_constraints": sequence_constraints,
        "weekly_sum_constraints": weekly_sum_constraints,
        "max_shifts_per_week": args.max_shifts_per_week if args.max_shifts_per_week else None,
        "min_days_off_per_week": args.min_days_off_per_week if args.min_days_off_per_week else None,
        "max_consecutive_work_days": args.max_consecutive_work_days if args.max_consecutive_work_days else None,
        "max_weekend_work_shifts_women": args.max_weekend_work_shifts_women,
    }

    for store_id in store_ids:
        demand = demand_by_store[store_id]
        validate_demand(demand, dates, shifts)

        min_employees_needed = max(
            sum(demand[d][s] for s in work_shifts) for d in range(len(demand))
        )
        effective_min = max(args.min_employees, min_employees_needed)
        if effective_min > args.max_employees:
            print(
                f"[{store_id}] Demand requires at least {min_employees_needed} employees; "
                f"--max_employees={args.max_employees} is too low. Raise --max_employees."
            )
            continue

        print(f"\n=== Store: {store_id} ===")
        print(f"Solving current roster: {args.current_employees} employees")
        roster = make_fake_roster(args.current_employees, args.seed)
        understaffed = False
        relaxed_result = None
        try:
            current = solve_once(
                args.current_employees,
                dates,
                shifts,
                demand,
                constraints,
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
            print(f"Current roster understaffed: {e}")

        if understaffed:
            print(
                f"Searching for best roster size (hire more) in "
                f"[{effective_min}, {args.max_employees}]..."
            )
            relaxed_constraints = {
                **constraints,
                "relax_cover": True,
                "understaff_penalty": 100,
            }
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
                    f"[{store_id}] Schedule B (no hire): Use current roster; "
                    f"understaffed by {total_slots} in {len(understaff_list)} slot(s):"
                )
                for d, shift_name, required, assigned in understaff_list:
                    day_num = dates[d].day
                    weekday = dates[d].strftime("%a")
                    print(
                        f"  Day {day_num} ({weekday}) {shift_name}: need {required}, have {assigned} — using all {assigned} available"
                    )
                print_solution(
                    f"[{store_id}] Schedule B (no hire) – current roster",
                    relaxed_result["solver"],
                    relaxed_result["work"],
                    args.current_employees,
                    shifts,
                    dates,
                    relaxed_result["obj_bool_vars"],
                    relaxed_result["obj_bool_coeffs"],
                    relaxed_result["obj_int_vars"],
                    relaxed_result["obj_int_coeffs"],
                    roster=roster,
                )
        elif current["status"] in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            print_solution(
                f"[{store_id}] Current roster result",
                current["solver"],
                current["work"],
                args.current_employees,
                shifts,
                dates,
                current["obj_bool_vars"],
                current["obj_bool_coeffs"],
                current["obj_int_vars"],
                current["obj_int_coeffs"],
                roster=roster,
            )

        best = None
        for num_employees in range(effective_min, args.max_employees + 1):
            result = solve_once(
                num_employees,
                dates,
                shifts,
                demand,
                constraints,
                args.params,
                args.output_proto,
                write_proto=False,
                roster=make_fake_roster(num_employees, args.seed),
            )
            if result["status"] not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                continue
            if best is None:
                best = (num_employees, result)
                continue
            best_employees, best_result = best
            if result["objective"] < best_result["objective"]:
                best = (num_employees, result)
                continue
            if result["objective"] == best_result["objective"]:
                # Prefer smaller roster (minimize headcount) when objectives tie.
                if num_employees < best_employees:
                    best = (num_employees, result)

        if best is None:
            print(
                f"[{store_id}] No feasible schedule between {effective_min} and {args.max_employees}."
            )
            continue

        best_employees, best_result = best
        best_roster = make_fake_roster(best_employees, args.seed)
        schedule_a_label = (
            f"[{store_id}] Schedule A (recommended): Hire {best_employees - args.current_employees}"
            if understaffed and best_employees > args.current_employees
            else f"[{store_id}] Best roster within bounds: {best_employees} employees"
        )
        print_solution(
            schedule_a_label,
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
        )
        if best_employees > args.current_employees:
            num_to_hire = best_employees - args.current_employees
            h_m, h_f = recommend_hiring_gender_split(
                roster,
                num_to_hire,
                args.target_min_women_ratio,
                args.target_max_women_ratio,
            )
            n_m = sum(1 for e in roster if e.get("gender") == "M")
            n_f = sum(1 for e in roster if e.get("gender") == "F")
            n_m_after = n_m + h_m
            n_f_after = n_f + h_f
            if h_m and h_f:
                gender_rec = f" To maintain balance: {h_m} man, {h_f} woman."
            elif h_m:
                gender_rec = f" To maintain balance: {h_m} men."
            else:
                gender_rec = f" To maintain balance: {h_f} women."
            print(
                f"[{store_id}] Recommendation: hire {num_to_hire} more "
                f"employee(s) to meet demand (current: {args.current_employees}).{gender_rec}"
            )
            print(
                f"[{store_id}] Current roster: {n_m} men, {n_f} women. "
                f"After hire: {n_m_after} men, {n_f_after} women."
            )
        elif best_employees < args.current_employees:
            print(
                f"[{store_id}] Recommendation: roster can be reduced by "
                f"{args.current_employees - best_employees} (current: {args.current_employees})."
            )
        print(best_result["solver"].response_stats())


if __name__ == "__main__":
    main()
