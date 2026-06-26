import csv
import os
import sys
import requests
from decimal import Decimal
from django.core.management.base import BaseCommand
from maproute.models import FuelStation
from maproute.geocoding import geocode_online

class Command(BaseCommand):
    help = 'Import fuel prices from a CSV file into the database'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to the fuel prices CSV file')
        parser.add_argument(
            '--geocode-limit',
            type=int,
            default=100,
            help='Maximum number of new unique cities to geocode online'
        )

    def handle(self, *args, **options):
        csv_file_path = options['csv_file']
        geocode_limit = options['geocode_limit']

        if not os.path.exists(csv_file_path):
            self.stderr.write(self.style.ERROR(f"File not found: {csv_file_path}"))
            sys.exit(1)

        # Retrieve ORS API key from /home/harekrsna/ors if present
        api_key = None
        ors_key_path = '/home/harekrsna/ors'
        if os.path.exists(ors_key_path):
            try:
                with open(ors_key_path, 'r') as f:
                    api_key = f.read().strip()
                self.stdout.write(self.style.SUCCESS("Successfully loaded ORS API key."))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Failed to read ORS API key file: {e}"))

        self.stdout.write("Downloading US cities database for fast offline geocoding...")
        cities_coords = {}
        try:
            url = "https://raw.githubusercontent.com/kelvins/US-Cities-Database/main/csv/us_cities.csv"
            res = requests.get(url, timeout=15)
            if res.status_code == 200:
                lines = res.text.split("\n")
                reader = csv.DictReader(lines)
                for row in reader:
                    if not row:
                        continue
                    try:
                        state = row['STATE_CODE'].strip().lower()
                        city = row['CITY'].strip().lower()
                        lat = float(row['LATITUDE'])
                        lon = float(row['LONGITUDE'])
                        cities_coords[(city, state)] = (lat, lon)
                    except:
                        pass
                self.stdout.write(self.style.SUCCESS(f"Loaded {len(cities_coords)} city coordinates from cache database."))
            else:
                self.stdout.write(self.style.WARNING(f"Failed to download cities database (status {res.status_code}). Fallback to online only."))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Error downloading cities database: {e}. Fallback to online only."))

        self.stdout.write(f"Reading fuel prices from {csv_file_path}...")
        
        stations_data = []
        unique_cities = set()

        with open(csv_file_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            # Normalize field names just in case they have spaces
            fieldnames = [fn.strip() for fn in reader.fieldnames]
            reader.fieldnames = fieldnames
            
            for row in reader:
                try:
                    opis_id = int(row['OPIS Truckstop ID'].strip())
                    name = row['Truckstop Name'].strip()
                    address = row['Address'].strip()
                    city = row['City'].strip()
                    state = row['State'].strip()
                    rack_id = int(row['Rack ID'].strip())
                    retail_price = Decimal(row['Retail Price'].strip())
                    
                    stations_data.append({
                        'opis_id': opis_id,
                        'name': name,
                        'address': address,
                        'city': city,
                        'state': state,
                        'rack_id': rack_id,
                        'retail_price': retail_price
                    })
                    unique_cities.add((city, state))
                except Exception as e:
                    pass

        self.stdout.write(f"Parsed {len(stations_data)} station records.")
        self.stdout.write(f"Found {len(unique_cities)} unique city-state combinations.")

        # Geocode using our downloaded cache first
        geocoded_cache = {}
        for city, state in unique_cities:
            key = (city.lower(), state.lower())
            if key in cities_coords:
                geocoded_cache[key] = cities_coords[key]

        self.stdout.write(f"Matched {len(geocoded_cache)} cities offline.")

        new_cities_to_geocode = []
        for city, state in unique_cities:
            key = (city.lower(), state.lower())
            if key not in geocoded_cache:
                # Exclude Canadian provinces from online geocoding to save quota
                if state.upper() not in ['AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE', 'QC', 'SK', 'YT']:
                    new_cities_to_geocode.append((city, state))

        self.stdout.write(f"Need to geocode {len(new_cities_to_geocode)} new US cities online.")
        
        # Geocode remaining online, up to limit
        geocoded_count = 0
        for city, state in new_cities_to_geocode[:geocode_limit]:
            key = (city.lower(), state.lower())
            self.stdout.write(f"Geocoding {city}, {state} online...")
            lat, lon = geocode_online(city, state, api_key=api_key)
            if lat is not None and lon is not None:
                geocoded_cache[key] = (lat, lon)
                geocoded_count += 1
            else:
                self.stdout.write(self.style.WARNING(f"Could not geocode {city}, {state} online"))

        self.stdout.write(f"Successfully geocoded {geocoded_count} new cities online.")

        # Save to database
        self.stdout.write("Saving stations to database...")
        FuelStation.objects.all().delete()
        
        objects_to_create = []
        for s in stations_data:
            key = (s['city'].lower(), s['state'].lower())
            lat, lon = geocoded_cache.get(key, (None, None))
            
            objects_to_create.append(FuelStation(
                opis_id=s['opis_id'],
                name=s['name'],
                address=s['address'],
                city=s['city'],
                state=s['state'],
                rack_id=s['rack_id'],
                retail_price=s['retail_price'],
                latitude=lat,
                longitude=lon
            ))

        FuelStation.objects.bulk_create(objects_to_create, batch_size=999)
        self.stdout.write(self.style.SUCCESS(f"Successfully imported {len(objects_to_create)} fuel stations!"))
