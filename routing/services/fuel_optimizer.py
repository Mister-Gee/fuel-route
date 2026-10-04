"""Minimum cost purchases on a fixed ordered route and finite tank."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

CAPACITY = Decimal("50")
MPG = Decimal("10")
CENT = Decimal("0.01")


class FuelInfeasible(ValueError):
    pass


@dataclass(frozen=True)
class FuelStop:
    opis_id: str
    mile: Decimal
    price: Decimal
    gallons: Decimal
    arrival: Decimal
    departure: Decimal
    cost: Decimal


def _d(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise FuelInfeasible("Nonfinite route or price")
    return result


def optimize(distance_miles, stations, *, capacity=CAPACITY, mpg=MPG):
    """Stations are (mile, price, ID); every station remains a visited waypoint."""
    end = _d(distance_miles)
    capacity, mpg = _d(capacity), _d(mpg)
    if end < 0 or capacity <= 0 or mpg <= 0:
        raise FuelInfeasible("Invalid route or vehicle parameters")
    ordered = sorted(((_d(mile), _d(price), str(opis_id)) for mile, price, opis_id in stations), key=lambda row: (row[0], row[1], row[2]))
    if any(price <= 0 or mile <= 0 or mile >= end for mile, price, _ in ordered):
        raise FuelInfeasible("Invalid station mile or price")
    if any(a[0] == b[0] for a, b in zip(ordered, ordered[1:])):
        raise FuelInfeasible("Zero-mile station switching is unsupported")
    positions = [Decimal(0)] + [row[0] for row in ordered] + [end]
    if any((b - a) / mpg > capacity for a, b in zip(positions, positions[1:])):
        raise FuelInfeasible("A route interval exceeds the 500-mile tank range")
    fuel = capacity
    stops = []
    for i, (mile, price, opis_id) in enumerate(ordered):
        fuel -= (mile - positions[i]) / mpg
        if fuel < 0:
            raise FuelInfeasible("Fuel exhausted before a station")
        arrival = fuel
        # Defer purchases to a no-more-expensive reachable stop when possible.
        target = None
        for future_mile, future_price, _ in ordered[i + 1:]:
            if (future_mile - mile) / mpg > capacity:
                break
            if future_price <= price:
                target = (future_mile - mile) / mpg
                break
        if target is None:
            target = min(capacity, (end - mile) / mpg)
        gallons = max(Decimal(0), target - fuel)
        fuel += gallons
        stops.append(FuelStop(opis_id, mile, price, gallons, arrival, fuel, gallons * price))
    fuel -= (end - positions[-2]) / mpg
    if fuel < 0:
        raise FuelInfeasible("Fuel exhausted before destination")
    bought = sum((stop.gallons for stop in stops), Decimal(0))
    consumed = end / mpg
    if capacity + bought - consumed != fuel or fuel > capacity:
        raise FuelInfeasible("Fuel ledger does not conserve")
    displayed = [{"opis_id": s.opis_id, "route_mile": float(s.mile), "price": str(s.price), "gallons": str(s.gallons.quantize(Decimal("0.000001"), rounding=ROUND_CEILING)), "arrival_gallons": str(s.arrival.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)), "departure_gallons": str(s.departure.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)), "cost": str(s.cost.quantize(CENT, rounding=ROUND_HALF_UP))} for s in stops]
    # The public total is explicitly the sum of displayed money lines.
    display_cost = sum((Decimal(item["cost"]) for item in displayed), Decimal(0))
    return {"stops": stops, "display_stops": displayed, "purchased_gallons": bought, "consumed_gallons": consumed, "ending_gallons": fuel, "cost": sum((stop.cost for stop in stops), Decimal(0)), "display_cost": display_cost}
