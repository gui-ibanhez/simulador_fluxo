"""Unit tests for staffing_estimation module."""

import unittest

from staffing_estimation import (
    employees_by_formula,
    employees_by_ratio,
    employees_by_tiers,
    estimate_required_employees,
)


class TestEmployeesByRatio(unittest.TestCase):
    def test_ceil_customers_per_employee(self):
        self.assertEqual(employees_by_ratio(60, 30), 2)
        self.assertEqual(employees_by_ratio(61, 30), 3)
        self.assertEqual(employees_by_ratio(30, 30), 1)

    def test_min_employees_floor(self):
        self.assertEqual(employees_by_ratio(5, 30, min_employees=2), 2)
        self.assertEqual(employees_by_ratio(100, 30, min_employees=5), 5)

    def test_zero_customers(self):
        self.assertEqual(employees_by_ratio(0, 30), 0)
        self.assertEqual(employees_by_ratio(-1, 30), 0)

    def test_customers_per_employee_clamped_to_one(self):
        # Function clamps customers_per_employee to min 1.0
        self.assertEqual(employees_by_ratio(10, 0.5), 10)  # 10/1.0
        self.assertEqual(employees_by_ratio(10, 0.1), 10)   # 10/1.0


class TestEmployeesByTiers(unittest.TestCase):
    def test_tier_boundaries(self):
        tiers = [(50, 1), (100, 2), (200, 3), (float("inf"), 4)]
        self.assertEqual(employees_by_tiers(0, tiers), 1)
        self.assertEqual(employees_by_tiers(50, tiers), 1)
        self.assertEqual(employees_by_tiers(51, tiers), 2)
        self.assertEqual(employees_by_tiers(100, tiers), 2)
        self.assertEqual(employees_by_tiers(101, tiers), 3)
        self.assertEqual(employees_by_tiers(200, tiers), 3)
        self.assertEqual(employees_by_tiers(201, tiers), 4)
        self.assertEqual(employees_by_tiers(1000, tiers), 4)

    def test_inf_tier(self):
        tiers = [(float("inf"), 1)]
        self.assertEqual(employees_by_tiers(999999, tiers), 1)

    def test_empty_tiers(self):
        self.assertEqual(employees_by_tiers(100, []), 0)


class TestEmployeesByFormula(unittest.TestCase):
    def test_base_plus_ceil(self):
        self.assertEqual(employees_by_formula(60, 30, base_employees=2), 4)
        self.assertEqual(employees_by_formula(0, 30, base_employees=2), 2)

    def test_min_employees_floor(self):
        self.assertEqual(employees_by_formula(10, 30, base_employees=0, min_employees=3), 3)
        self.assertEqual(employees_by_formula(0, 30, base_employees=1, min_employees=2), 2)

    def test_zero_customers(self):
        self.assertEqual(employees_by_formula(0, 30, base_employees=0, min_employees=0), 0)
        self.assertEqual(employees_by_formula(0, 30, base_employees=2, min_employees=0), 2)


class TestEstimateRequiredEmployees(unittest.TestCase):
    def test_rule_ratio(self):
        flow = {"store_A": [60, 90, 30]}
        result = estimate_required_employees(
            flow, rule="ratio", customers_per_employee=30.0, min_employees=1
        )
        self.assertEqual(result[("store_A", 0)], 2)
        self.assertEqual(result[("store_A", 1)], 3)
        self.assertEqual(result[("store_A", 2)], 1)

    def test_rule_tiers(self):
        flow = {"store_A": [25, 75, 150, 250]}
        tiers = [(50, 1), (100, 2), (200, 3), (float("inf"), 4)]
        result = estimate_required_employees(flow, rule="tiers", tiers=tiers)
        self.assertEqual(result[("store_A", 0)], 1)
        self.assertEqual(result[("store_A", 1)], 2)
        self.assertEqual(result[("store_A", 2)], 3)
        self.assertEqual(result[("store_A", 3)], 4)

    def test_rule_formula(self):
        flow = {"store_A": [60]}
        result = estimate_required_employees(
            flow, rule="formula", customers_per_employee=30.0, base_employees=1, min_employees=0
        )
        self.assertEqual(result[("store_A", 0)], 3)  # 1 + ceil(60/30)

    def test_per_shift_customers_per_employee_dict(self):
        flow = {"store_A": [60, 60]}  # period 0 -> M, period 1 -> A
        period_to_shift = lambda i: "M" if i == 0 else "A"
        cpe = {"M": 30.0, "A": 60.0}
        result = estimate_required_employees(
            flow, rule="ratio", period_to_shift=period_to_shift, customers_per_employee=cpe
        )
        self.assertEqual(result[("store_A", 0)], 2)  # 60/30
        self.assertEqual(result[("store_A", 1)], 1)  # 60/60

    def test_unknown_rule_raises_value_error(self):
        flow = {"store_A": [60]}
        with self.assertRaises(ValueError) as ctx:
            estimate_required_employees(flow, rule="invalid")
        self.assertIn("Unknown rule", str(ctx.exception))
