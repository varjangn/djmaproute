import math
import logging
import requests
import openrouteservice
from django.conf import settings
from maproute.models import FuelStation
from maproute.geocoding import geocode_online

logger = logging.getLogger(__name__)

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate the great circle distance between two points in miles."""
    R = 3958.8  # Radius of the Earth in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2) * math.sin(dlat/2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dlon/2) * math.sin(dlon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def geocode_query(query, api_key=None):
    """Geocode an address query using ORS (Pelias) or Nominatim."""
    if api_key:
        try:
            client = openrouteservice.Client(key=api_key)
            res = client.pelias_search(text=query, size=1)
            if res and 'features' in res and len(res['features']) > 0:
                coords = res['features'][0]['geometry']['coordinates']
                # Pelias returns [lon, lat]
                return float(coords[1]), float(coords[0])
        except Exception as e:
            logger.error(f"Error geocoding '{query}' via ORS: {e}")
            
    # Fallback to Nominatim
    try:
        headers = {"User-Agent": "djroute-be-assessment-agent/1.0"}
        url = f"https://nominatim.openstreetmap.org/search?q={requests.utils.quote(query)}&format=json&limit=1"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data and len(data) > 0:
                return float(data[0]['lat']), float(data[0]['lon'])
    except Exception as e:
        logger.error(f"Error geocoding '{query}' via Nominatim: {e}")
        
    return None

def find_stations_along_route(route_coords, max_distance_miles=10.0, api_key=None):
    """
    Find fuel stations within max_distance_miles of the route.
    route_coords: list of [longitude, latitude] points representing the route path.
    """
    if not route_coords:
        return [], 0.0
        
    # Get bounding box of the route
    lons = [p[0] for p in route_coords]
    lats = [p[1] for p in route_coords]
    
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    
    # Add buffer of 0.2 degrees (~14 miles) to the bounding box
    buffer = 0.2
    lat_range = (min_lat - buffer, max_lat + buffer)
    lon_range = (min_lon - buffer, max_lon + buffer)
    
    # Query candidate stations in the bounding box
    stations = FuelStation.objects.filter(
        latitude__range=lat_range,
        longitude__range=lon_range
    )
    
    # Identify crossed states to lazy-geocode any un-geocoded stations in those states
    crossed_states = set()
    num_points = len(route_coords)
    sample_indices = [0, num_points // 4, num_points // 2, (3 * num_points) // 4, num_points - 1]
    
    client = openrouteservice.Client(key=api_key) if api_key else None
    
    for idx in sample_indices:
        if idx < num_points:
            pt = route_coords[idx]  # [lon, lat]
            state_code = None
            if client:
                try:
                    res = client.pelias_reverse(point=pt, size=1)
                    if res and 'features' in res and len(res['features']) > 0:
                        state_code = res['features'][0]['properties'].get('region_a')
                except Exception:
                    pass
            if not state_code:
                try:
                    headers = {"User-Agent": "djroute-be-assessment-agent/1.0"}
                    url = f"https://nominatim.openstreetmap.org/reverse?lat={pt[1]}&lon={pt[0]}&format=json"
                    res = requests.get(url, headers=headers, timeout=3)
                    if res.status_code == 200:
                        addr = res.json().get('address', {})
                        state_code = addr.get('state_code') or addr.get('ISO3166-2-lvl4', '').split('-')[-1]
                except Exception:
                    pass
            if state_code:
                crossed_states.add(state_code.upper())
                
    # Geocode un-geocoded cities in crossed states (max 10 to keep response fast)
    if crossed_states:
        un_geocoded_cities = FuelStation.objects.filter(
            state__in=crossed_states,
            latitude__isnull=True
        ).values_list('city', 'state').distinct()
        
        geocoded_count = 0
        for city, state in list(un_geocoded_cities)[:10]:
            lat, lon = geocode_online(city, state, api_key=api_key)
            if lat is not None and lon is not None:
                FuelStation.objects.filter(city=city, state=state).update(latitude=lat, longitude=lon)
                geocoded_count += 1
                
        if geocoded_count > 0:
            stations = FuelStation.objects.filter(
                latitude__range=lat_range,
                longitude__range=lon_range
            )
            
    # Calculate cumulative distance along the route for all vertices
    dist_along = [0.0] * num_points
    for i in range(1, num_points):
        prev = route_coords[i-1]
        curr = route_coords[i]
        segment_dist = haversine_distance(prev[1], prev[0], curr[1], curr[0])
        dist_along[i] = dist_along[i-1] + segment_dist
        
    total_route_length = dist_along[-1]
    
    # Project each station onto the route
    candidate_stations = []
    for station in stations:
        if station.latitude is None or station.longitude is None:
            continue
            
        s_lat, s_lon = station.latitude, station.longitude
        min_dist_to_segment = float('inf')
        closest_dist_along = 0.0
        
        for i in range(num_points - 1):
            p1 = route_coords[i]
            p2 = route_coords[i+1]
            
            # Local flat-earth projection
            lat_mid = (p1[1] + p2[1]) / 2.0
            cos_lat = math.cos(math.radians(lat_mid))
            
            x1, y1 = p1[0] * cos_lat, p1[1]
            x2, y2 = p2[0] * cos_lat, p2[1]
            xs, ys = s_lon * cos_lat, s_lat
            
            dx = x2 - x1
            dy = y2 - y1
            
            denom = dx*dx + dy*dy
            if denom == 0:
                t = 0.0
            else:
                t = ((xs - x1) * dx + (ys - y1) * dy) / denom
                t = max(0.0, min(1.0, t))
                
            proj_lon = p1[0] + t * (p2[0] - p1[0])
            proj_lat = p1[1] + t * (p2[1] - p1[1])
            
            dist_to_proj = haversine_distance(s_lat, s_lon, proj_lat, proj_lon)
            if dist_to_proj < min_dist_to_segment:
                min_dist_to_segment = dist_to_proj
                segment_dist_p1_p2 = haversine_distance(p1[1], p1[0], p2[1], p2[0])
                closest_dist_along = dist_along[i] + t * segment_dist_p1_p2
                
        if min_dist_to_segment <= max_distance_miles:
            candidate_stations.append({
                'station': station,
                'dist_along_route': closest_dist_along,
                'off_route_dist': min_dist_to_segment
            })
            
    return candidate_stations, total_route_length

def solve_refueling_greedy(candidates, total_distance, tank_capacity=50.0, mpg=10.0, initial_fuel=50.0):
    """
    Greedy algorithm to find the optimal refueling plan.
    candidates: list of dicts with 'station', 'dist_along_route', 'off_route_dist'
    total_distance: float (total length of route in miles)
    """
    # Sort candidates by distance along the route
    candidates = sorted(candidates, key=lambda x: x['dist_along_route'])
    
    # Define Nodes:
    # Node 0: Start
    # Node 1..K: Fuel Stations
    # Node K+1: Destination
    nodes = []
    
    # Start node
    nodes.append({
        'type': 'start',
        'dist': 0.0,
        'price': float('inf'), # start fuel is free/full, we don't purchase here
        'station': None
    })
    
    for c in candidates:
        nodes.append({
            'type': 'station',
            'dist': c['dist_along_route'],
            'price': float(c['station'].retail_price),
            'station': c['station']
        })
        
    # Destination node
    nodes.append({
        'type': 'destination',
        'dist': total_distance,
        'price': 0.0, # no need to buy fuel at destination
        'station': None
    })
    
    max_range = tank_capacity * mpg
    
    current_idx = 0
    current_fuel = initial_fuel
    total_cost = 0.0
    
    stops = []
    
    while current_idx < len(nodes) - 1:
        current_node = nodes[current_idx]
        current_dist = current_node['dist']
        current_price = current_node['price']
        
        # Check if we can reach the destination directly with current fuel
        dest_node = nodes[-1]
        dist_to_dest = dest_node['dist'] - current_dist
        if dist_to_dest <= current_fuel * mpg:
            # We can reach the destination!
            current_fuel -= (dist_to_dest / mpg)
            current_idx = len(nodes) - 1
            break
            
        # Look at all nodes reachable with a FULL tank from current node
        # Wait, the range of reachable nodes is limited by max_range
        reachable_nodes = []
        for idx in range(current_idx + 1, len(nodes)):
            node = nodes[idx]
            dist_to_node = node['dist'] - current_dist
            if dist_to_node <= max_range:
                reachable_nodes.append((idx, node))
            else:
                break
                
        if not reachable_nodes:
            # Cannot reach any station or destination from here!
            raise ValueError(f"Trip is impossible. No fuel station reachable within {max_range} miles of {current_node['station'].name if current_node['station'] else 'Start'}.")
            
        # Find the first station in the reachable range that is strictly cheaper than current price
        cheaper_node = None
        for idx, node in reachable_nodes:
            if node['price'] < current_price:
                cheaper_node = (idx, node)
                break
                
        if cheaper_node:
            # Case A: There is a cheaper node in range
            idx, node = cheaper_node
            dist_to_node = node['dist'] - current_dist
            fuel_needed = dist_to_node / mpg
            
            # Buy just enough to reach the cheaper node
            fuel_to_buy = 0.0
            if current_fuel < fuel_needed:
                fuel_to_buy = fuel_needed - current_fuel
                current_fuel = 0.0
                total_cost += fuel_to_buy * current_price
            else:
                current_fuel -= fuel_needed
                
            if fuel_to_buy > 0:
                stops.append({
                    'station': current_node['station'],
                    'dist_along_route': current_dist,
                    'fuel_to_buy': fuel_to_buy,
                    'fuel_price': current_price,
                    'cost': fuel_to_buy * current_price
                })
                
            current_idx = idx
        else:
            # Case B: No node in range is cheaper than current node
            # This means current node is the cheapest in range. We should fill up to 100% capacity!
            fuel_to_buy = tank_capacity - current_fuel
            current_fuel = tank_capacity
            total_cost += fuel_to_buy * current_price
            
            if fuel_to_buy > 0:
                stops.append({
                    'station': current_node['station'],
                    'dist_along_route': current_dist,
                    'fuel_to_buy': fuel_to_buy,
                    'fuel_price': current_price,
                    'cost': fuel_to_buy * current_price
                })
                
            # Go to the cheapest node among reachable nodes
            # (which has to be the next stop we make)
            cheapest_in_range_idx = None
            cheapest_in_range_price = float('inf')
            
            for idx, node in reachable_nodes:
                if node['price'] < cheapest_in_range_price:
                    cheapest_in_range_price = node['price']
                    cheapest_in_range_idx = idx
                    
            if cheapest_in_range_idx is None:
                raise ValueError("No reachable stations found.")
                
            next_node = nodes[cheapest_in_range_idx]
            dist_to_next = next_node['dist'] - current_dist
            current_fuel -= (dist_to_next / mpg)
            current_idx = cheapest_in_range_idx
            
    return stops, total_cost

def calculate_route_and_fuel_plan(start_query, finish_query):
    """
    Combines geocoding, directions, station filtering, and optimization.
    """
    # 1. Load API Key
    api_key = settings.ORS_API_KEY

    # 2. Geocode start and finish
    start_coords = geocode_query(start_query, api_key)
    finish_coords = geocode_query(finish_query, api_key)
    
    if not start_coords:
        raise ValueError(f"Could not geocode start location: {start_query}")
    if not finish_coords:
        raise ValueError(f"Could not geocode finish location: {finish_query}")
        
    # 3. Get routing direction
    client = openrouteservice.Client(key=api_key)
    # Coordinates in ORS are [longitude, latitude]
    coords = [[start_coords[1], start_coords[0]], [finish_coords[1], finish_coords[0]]]
    
    try:
        routes_geojson = client.directions(coordinates=coords, profile='driving-car', format='geojson')
    except Exception as e:
        raise ValueError(f"OpenRouteService routing failed: {e}")
        
    if not routes_geojson or 'features' not in routes_geojson or len(routes_geojson['features']) == 0:
        raise ValueError("No route found between the locations.")
        
    feature = routes_geojson['features'][0]
    route_coords = feature['geometry']['coordinates'] # list of [lon, lat]
    
    # 4. Filter stations along route
    candidates, total_dist = find_stations_along_route(route_coords, max_distance_miles=10.0, api_key=api_key)
    
    # 5. Solve refueling problem
    stops, total_cost = solve_refueling_greedy(candidates, total_dist)
    
    return {
        'start_coords': start_coords,
        'finish_coords': finish_coords,
        'route_coords': route_coords,
        'total_distance_miles': total_dist,
        'stops': stops,
        'total_cost': total_cost
    }
