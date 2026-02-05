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
