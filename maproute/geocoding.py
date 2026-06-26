import time
import requests
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

# To prevent hitting rate limits too fast, we keep a tiny delay for online API calls
# Nominatim requires at least 1 second between requests
LAST_REQUEST_TIME = 0

def geocode_online(city, state, api_key=None):
    global LAST_REQUEST_TIME
    query = f"{city.strip()}, {state.strip()}, USA"
    
    # 1. Try OpenRouteService pelias_search if API key is available
    if api_key:
        try:
            import openrouteservice
            client = openrouteservice.Client(key=api_key)
            # pelias_search is ORS geocoding search
            res = client.pelias_search(text=query, size=1)
            if res and 'features' in res and len(res['features']) > 0:
                coords = res['features'][0]['geometry']['coordinates']
                # ORS returns [lon, lat]
                logger.info(f"Geocoded {query} via ORS: {coords}")
                return float(coords[1]), float(coords[0])
        except Exception as e:
            logger.error(f"Error geocoding {query} with ORS: {e}")

    # 2. Try Nominatim (OpenStreetMap)
    # Rate limit: 1 request per second
    now = time.time()
    elapsed = now - LAST_REQUEST_TIME
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    
    try:
        headers = {"User-Agent": "djroute-be-assessment-agent/1.0"}
        url = f"https://nominatim.openstreetmap.org/search?q={requests.utils.quote(query)}&format=json&limit=1"
        LAST_REQUEST_TIME = time.time()
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data and len(data) > 0:
                lat = float(data[0]['lat'])
                lon = float(data[0]['lon'])
                logger.info(f"Geocoded {query} via Nominatim: {lat}, {lon}")
                return lat, lon
        logger.warning(f"Nominatim returned status {res.status_code} or empty data for {query}")
    except Exception as e:
        logger.error(f"Error geocoding {query} with Nominatim: {e}")
        
    return None, None
