from django.test import TestCase
from maproute.routing_service import haversine_distance, solve_refueling_greedy
from maproute.models import FuelStation
from decimal import Decimal

class FuelRouteTestCase(TestCase):
    def test_haversine_distance(self):
        # Distance between Nashville (36.162, -86.784) and Memphis (35.149, -90.048) is approx 200 miles
        dist = haversine_distance(36.162, -86.784, 35.149, -90.048)
        self.assertAlmostEqual(dist, 200.0, delta=15.0)

    def test_refueling_optimizer(self):
        # Mock stations
        station_a = FuelStation(opis_id=1, name="Station A", address="Road 1", city="City A", state="ST", rack_id=1, retail_price=Decimal("3.00"))
        station_b = FuelStation(opis_id=2, name="Station B", address="Road 2", city="City B", state="ST", rack_id=2, retail_price=Decimal("2.00"))
        station_c = FuelStation(opis_id=3, name="Station C", address="Road 3", city="City C", state="ST", rack_id=3, retail_price=Decimal("3.50"))
        
        candidates = [
            {'station': station_a, 'dist_along_route': 300.0, 'off_route_dist': 0.1},
            {'station': station_b, 'dist_along_route': 450.0, 'off_route_dist': 0.2},
            {'station': station_c, 'dist_along_route': 750.0, 'off_route_dist': 0.1},
        ]
        
        stops, total_cost = solve_refueling_greedy(
            candidates=candidates,
            total_distance=1000.0,
            tank_capacity=50.0,
            mpg=10.0,
            initial_fuel=50.0
        )
        
        # Verify stops and cost
        # Stop 1 should be at Station B (dist 450). Fuel bought: 45.0 gallons @ $2.00 = $90.00
        # Stop 2 should be at Station C (dist 750). Fuel bought: 5.0 gallons @ $3.50 = $17.50
        # Total cost: $107.50
        self.assertEqual(len(stops), 2)
        self.assertEqual(stops[0]['station'].name, "Station B")
        self.assertAlmostEqual(stops[0]['fuel_to_buy'], 45.0)
        self.assertAlmostEqual(stops[0]['cost'], 90.0)
        
        self.assertEqual(stops[1]['station'].name, "Station C")
        self.assertAlmostEqual(stops[1]['fuel_to_buy'], 5.0)
        self.assertAlmostEqual(stops[1]['cost'], 17.5)
        
        self.assertAlmostEqual(total_cost, 107.50)
