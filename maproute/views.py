from django.shortcuts import render
from django.http import JsonResponse
from maproute.routing_service import calculate_route_and_fuel_plan

def index(request):
    return render(request, 'maproute/index.html')

def route_api(request):
    start = request.GET.get('start')
    finish = request.GET.get('finish')
    
    if not start or not finish:
        return JsonResponse({'error': 'Please provide both start and finish parameters.'}, status=400)
        
    try:
        plan = calculate_route_and_fuel_plan(start, finish)
        
        # Serialize fuel stops
        stops_serialized = []
        for stop in plan['stops']:
            st = stop['station']
            stops_serialized.append({
                'station': {
                    'opis_id': st.opis_id,
                    'name': st.name,
                    'address': st.address,
                    'city': st.city,
                    'state': st.state,
                    'rack_id': st.rack_id,
                    'retail_price': float(st.retail_price),
                    'latitude': st.latitude,
                    'longitude': st.longitude
                },
                'dist_along_route': stop['dist_along_route'],
                'fuel_to_buy': stop['fuel_to_buy'],
                'fuel_price': stop['fuel_price'],
                'cost': stop['cost']
            })
            
        return JsonResponse({
            'start_coords': plan['start_coords'],
            'finish_coords': plan['finish_coords'],
            'route_coords': plan['route_coords'],
            'total_distance_miles': plan['total_distance_miles'],
            'stops': stops_serialized,
            'total_cost': plan['total_cost']
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=400)
