import datetime as dt
import pytest
from ortools.sat.python import cp_model

from staffing_estimation import estimate_required_employees
from store_staffing_optimizer import (
    build_dates,
    build_model,
    solve_once,
    default_demand
)

# --- 1. Demand Estimation Tests ---

def test_estimation_ratio_rule():
    """Verify ratio rule: ceil(customers / ratio) with minimum."""
    # 100 cust / 30 ratio = 3.33 -> 4 employees
    flow = {"store": [100]} # period 0
    raw = estimate_required_employees(flow, rule="ratio", customers_per_employee=30.0, min_employees=1)
    assert raw[("store", 0)] == 4

    # 10 cust / 30 ratio = 0.33 -> 1 employee (min)
    flow_min = {"store": [10]}
    raw_min = estimate_required_employees(flow_min, rule="ratio", customers_per_employee=30.0, min_employees=1)
    assert raw_min[("store", 0)] == 1

def test_estimation_tiers_rule():
    """Verify tiers rule: selects correct employee count based on thresholds."""
    # Tiers: 50->1, 100->2, inf->3
    tiers = [(50, 1), (100, 2), (float("inf"), 3)]
    
    # 40 cust -> tier 1 (<=50) -> 1 emp
    raw_1 = estimate_required_employees({"s": [40]}, rule="tiers", tiers=tiers)
    assert raw_1[("s", 0)] == 1
    
    # 70 cust -> tier 2 (<=100) -> 2 emp
    raw_2 = estimate_required_employees({"s": [70]}, rule="tiers", tiers=tiers)
    assert raw_2[("s", 0)] == 2

    # 150 cust -> tier 3 (>100) -> 3 emp
    raw_3 = estimate_required_employees({"s": [150]}, rule="tiers", tiers=tiers)
    assert raw_3[("s", 0)] == 3


# --- 2. Constraint Verification Tests ---

def solve_with_model(num_employees, dates, shifts, demand, constraints, roster=None):
    """Helper to build and solve a model, returning the solver object and work variables."""
    model, work, _, _, _, _ = build_model(
        num_employees, dates, shifts, demand, constraints, roster
    )
    solver = cp_model.CpSolver()
    status = solver.Solve(model)
    return status, solver, work

def test_constraint_women_alternate_sundays_fix():
    """
    CRITICAL TEST: Verify the fix for 'Women Alternate Sundays'.
    Ensure a woman CANNOT work two consecutive Sundays.
    """
    # Setup: Feb 2025 (Starts Sat, so Sun is day 1). 
    # We schedule 2 weeks (enough to have 2 Sundays: Feb 2 and Feb 9).
    year, month = 2025, 2
    dates = build_dates(year, month)[:14] # First 14 days
    shifts = ["O", "M"] # Off, Morning
    
    # Roster: 1 Woman
    roster = [{"id": "alice", "gender": "F"}]
    num_employees = 1
    
    # Demand: Need 1 person on M every day
    demand = [{"M": 1} for _ in dates]
    
    # Constraint: Women must alternate Sundays (cannot work consecutive).
    # Also we force min_sunday_off_women=0 so the ONLY restriction is the "alternate" rule
    # if the rule is broken (e.g. inverted), she might be forced to work both if we don't forbid it.
    constraints = {
        "women_sunday_off_alternate": True,
        "min_sunday_off_women": 0, # Don't interfere with simple min count
        "excess_cover_penalties": {"M": 100} # Penalize overstaffing lightly
    }
    
    # We intentionally try to force her to work both Sundays by setting high demand? 
    # Actually, with 1 employee and demand=1, she MUST work every day to meet demand perfectly.
    # If the constraint works, the solver should report INFEASIBLE (or understaff/excess if relaxed).
    # But wait, hard cover is default. So she MUST work.
    
    # If she works Feb 2 (Sun) and Feb 9 (Sun), that's consecutive.
    # The constraint should FORBID this. 
    # So if she is the only employee and demand is 1, this should be INFEASIBLE.
    
    status, _, _ = solve_with_model(num_employees, dates, shifts, demand, constraints, roster)
    
    # Expect INFEASIBLE because she can't cover both Sundays if the rule is working.
    assert status == cp_model.INFEASIBLE, "Should be INFEASIBLE for 1 woman to cover 2 consecutive Sundays."

def test_constraint_min_days_off():
    """Verify min_days_off_per_week works."""
    year, month = 2025, 2
    dates = build_dates(year, month)[:7] # 1 week
    shifts = ["O", "M"]
    roster = [{"id": "bob", "gender": "M"}]
    
    # Demand 1 per day
    demand = [{"M": 1} for _ in dates]
    
    # Constraint: Min 2 days off per week.
    # With 7 days, if he needs 2 off, he can work max 5.
    # Demand requires 7 days of work.
    # So 1 employee cannot satisfy demand.
    constraints = {
        "min_days_off_per_week": 2
    }
    
    status, _, _ = solve_with_model(1, dates, shifts, demand, constraints, roster)
    assert status == cp_model.INFEASIBLE

def test_max_consecutive_work_days():
    """Verify max_consecutive_work_days."""
    year, month = 2025, 2
    dates = build_dates(year, month)[:10] # 10 days
    shifts = ["O", "M"]
    roster = [{"id": "charlie", "gender": "M"}]
    
    demand = [{"M": 1} for _ in dates] # Need work every day
    
    # Max consecutive = 5.
    # Employee cannot work 10 days straight.
    constraints = {
        "max_consecutive_work_days": 5
    }
    
    status, _, _ = solve_with_model(1, dates, shifts, demand, constraints, roster)
    assert status == cp_model.INFEASIBLE


# --- 3. End-to-End Test ---

def test_end_to_end_optimization():
    """Run a small but complete optimization that should succeed."""
    # 2 employees, need 1 per shift. 
    # Use 2 shifts (M, A)
    # They can swap or share.
    year, month = 2025, 2
    dates = build_dates(year, month)[:7] # 1 week
    shifts = ["O", "M", "A"]
    
    roster = [
        {"id": "e1", "gender": "M"},
        {"id": "e2", "gender": "F"}
    ]
    
    # Demand: 1 M, 1 A per day. Total 2 shifts/day.
    # 2 employees * 7 days = 14 shifts capacity.
    # Demand = 14 shifts.
    # Perfectly doable if no rest rules.
    demand = [{"M": 1, "A": 1} for _ in dates]
    
    # Relax rules to ensure feasibility
    constraints = {
        "min_days_off_per_week": 0,
        "max_consecutive_work_days": 10,
        "women_sunday_off_alternate": True 
    }
    
    status, _, _ = solve_with_model(2, dates, shifts, demand, constraints, roster)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)


def test_zero_demand_forbids_assignments():
    """
    Verify that when demand is 0 for a shift, no one can be assigned to it.
    This is critical for scenarios like store closed, shift doesn't exist on certain days.
    """
    year, month = 2025, 2
    dates = build_dates(year, month)[:7]  # 1 week: Sat Feb 1 to Fri Feb 7
    shifts = ["O", "S1", "S2"]
    
    # Roster: 3 employees
    roster = [
        {"id": "emp_0", "gender": "M"},
        {"id": "emp_1", "gender": "M"},
        {"id": "emp_2", "gender": "M"},
    ]
    num_employees = 3
    
    # Demand: S1=1, S2=1 on all days EXCEPT day 1 (Sunday Feb 2) where S1=0, S2=0
    # This simulates store closed on Sunday for those shifts
    demand = []
    for i, d in enumerate(dates):
        if d.weekday() == 6:  # Sunday
            demand.append({"S1": 0, "S2": 0})  # Store closed
        else:
            demand.append({"S1": 1, "S2": 1})
    
    # Use minimize_off_days_penalty to try to force assignments
    # If the fix works, no one should be assigned to S1 or S2 on Sunday
    constraints = {
        "minimize_off_days_penalty": 10,  # High penalty to try to force work
        "excess_cover_penalties": {"S1": 1, "S2": 1},
        "min_days_off_per_week": 0,
        "max_consecutive_work_days": 7,
    }
    
    status, solver, work = solve_with_model(num_employees, dates, shifts, demand, constraints, roster)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    
    # Find the Sunday (should be index 1 in Feb 2025: Sat=0, Sun=1)
    sunday_idx = None
    for i, d in enumerate(dates):
        if d.weekday() == 6:  # Sunday
            sunday_idx = i
            break
    
    assert sunday_idx is not None, "Expected a Sunday in the date range"
    
    # Verify no one is assigned to S1 or S2 on Sunday
    shift_index = {"O": 0, "S1": 1, "S2": 2}
    for e in range(num_employees):
        s1_assigned = solver.boolean_value(work[e, shift_index["S1"], sunday_idx])
        s2_assigned = solver.boolean_value(work[e, shift_index["S2"], sunday_idx])
        assert s1_assigned == 0, f"Employee {e} should NOT be assigned to S1 on Sunday (demand=0)"
        assert s2_assigned == 0, f"Employee {e} should NOT be assigned to S2 on Sunday (demand=0)"
        # They should all be Off on Sunday
        off_assigned = solver.boolean_value(work[e, shift_index["O"], sunday_idx])
        assert off_assigned == 1, f"Employee {e} should be Off on Sunday when all work shifts have demand=0"


def test_zero_demand_with_mixed_shifts():
    """
    Verify that when demand is 0 for some shifts but positive for others,
    only the zero-demand shifts are blocked.
    """
    year, month = 2025, 2
    dates = build_dates(year, month)[:7]  # 1 week
    shifts = ["O", "S1", "S2", "S3"]
    
    roster = [
        {"id": "emp_0", "gender": "M"},
        {"id": "emp_1", "gender": "M"},
        {"id": "emp_2", "gender": "M"},
    ]
    num_employees = 3
    
    # Demand: on Sunday, only S2 is open (S1=0, S2=2, S3=0)
    demand = []
    for i, d in enumerate(dates):
        if d.weekday() == 6:  # Sunday
            demand.append({"S1": 0, "S2": 2, "S3": 0})  # Only S2 open
        else:
            demand.append({"S1": 1, "S2": 1, "S3": 1})
    
    constraints = {
        "minimize_off_days_penalty": 10,
        "excess_cover_penalties": {"S1": 1, "S2": 1, "S3": 1},
        "min_days_off_per_week": 0,
        "max_consecutive_work_days": 7,
    }
    
    status, solver, work = solve_with_model(num_employees, dates, shifts, demand, constraints, roster)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    
    # Find Sunday
    sunday_idx = None
    for i, d in enumerate(dates):
        if d.weekday() == 6:
            sunday_idx = i
            break
    
    assert sunday_idx is not None
    
    shift_index = {"O": 0, "S1": 1, "S2": 2, "S3": 3}
    
    # Count assignments on Sunday
    s1_count = sum(solver.boolean_value(work[e, shift_index["S1"], sunday_idx]) for e in range(num_employees))
    s2_count = sum(solver.boolean_value(work[e, shift_index["S2"], sunday_idx]) for e in range(num_employees))
    s3_count = sum(solver.boolean_value(work[e, shift_index["S3"], sunday_idx]) for e in range(num_employees))
    
    assert s1_count == 0, f"S1 should have 0 assignments on Sunday (demand=0), got {s1_count}"
    assert s2_count >= 2, f"S2 should have at least 2 assignments on Sunday (demand=2), got {s2_count}"
    assert s3_count == 0, f"S3 should have 0 assignments on Sunday (demand=0), got {s3_count}"
