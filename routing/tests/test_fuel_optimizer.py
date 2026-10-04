from decimal import Decimal
from functools import lru_cache

from django.test import SimpleTestCase

from routing.services.fuel_optimizer import FuelInfeasible, optimize


def exhaustive(distance, stations, capacity):
    """Independent integer-unit search over every purchase quantity."""
    positions = [0] + [mile for mile, _ in stations] + [distance]
    prices = [price for _, price in stations]

    @lru_cache(None)
    def visit(index, fuel):
        if index == len(stations):
            return 0
        need = positions[index + 2] - positions[index + 1]
        best = None
        for bought in range(capacity - fuel + 1):
            if fuel + bought < need:
                continue
            future = visit(index + 1, fuel + bought - need)
            if future is not None:
                cost = bought * prices[index] + future
                best = cost if best is None else min(best, cost)
        return best

    first = positions[1] - positions[0]
    if first > capacity:
        return None
    return visit(0, capacity - first)


class FuelOptimizerTests(SimpleTestCase):
    def test_short_exact_range_and_epsilon(self):
        self.assertEqual(optimize(499, [])["purchased_gallons"], 0)
        self.assertEqual(optimize(500, [])["ending_gallons"], 0)
        with self.assertRaises(FuelInfeasible):
            optimize(500.00001, [])

    def test_partial_purchase_and_ledger(self):
        result = optimize(800, [(300, "4", "A"), (450, "3", "B")])
        self.assertEqual(result["stops"][0].gallons, Decimal("0"))
        self.assertEqual(result["stops"][1].gallons, Decimal("30"))
        self.assertEqual(result["purchased_gallons"], Decimal("30"))
        self.assertEqual(result["ending_gallons"], Decimal("0"))
        self.assertEqual(sum(Decimal(stop["cost"]) for stop in result["display_stops"]), result["display_cost"])

    def test_equal_price_and_same_mile_ties(self):
        result = optimize(900, [(300, "3", "A"), (600, "3", "B")])
        self.assertEqual([stop.gallons for stop in result["stops"]], [Decimal("10"), Decimal("30")])
        with self.assertRaisesRegex(FuelInfeasible, "Zero-mile"):
            optimize(900, [(300, "3", "A"), (300, "2", "B")])

    def test_fractional_ledger_and_money_display(self):
        result = optimize("500.000001", [("250.000001", "3.333333", "A")])
        self.assertEqual(result["ending_gallons"], Decimal("0"))
        self.assertEqual(Decimal("50") + result["purchased_gallons"] - result["consumed_gallons"], result["ending_gallons"])
        self.assertEqual(result["display_cost"], sum(Decimal(row["cost"]) for row in result["display_stops"]))
        self.assertGreaterEqual(Decimal(result["display_stops"][0]["gallons"]), result["stops"][0].gallons)

    def test_independent_grid_oracle(self):
        for capacity in (3, 4, 5):
            for distance in range(capacity + 1, capacity + 5):
                for first in range(1, distance - 1):
                    for second in range(first + 1, distance):
                        stations = [(first, 2), (second, 3)]
                        for p1 in (1, 2, 4):
                            for p2 in (1, 2, 4):
                                stations = [(first, p1), (second, p2)]
                                oracle = exhaustive(distance, stations, capacity)
                                try:
                                    actual = optimize(distance, [(mile, price, str(i)) for i, (mile, price) in enumerate(stations)], capacity=capacity, mpg=1)
                                except FuelInfeasible:
                                    self.assertIsNone(oracle)
                                else:
                                    self.assertEqual(actual["cost"], oracle)
