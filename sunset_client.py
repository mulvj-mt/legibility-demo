import requests

def get_sun_times(lat, lng, date=None):
    """
    Fetches sunrise and sunset times for a given location.
    
    :param lat: Latitude as a float
    :param lng: Longitude as a float
    :param date: Optional date string in 'YYYY-MM-DD' format
    """
    base_url = "https://api.sunrise-sunset.org/json"
    
    # Prepare parameters
    params = {
        'lat': lat,
        'lng': lng,
        'date': date if date else 'today',
        'formatted': 0  # 0 returns time in ISO 8601 format
    }
    
    try:
        response = requests.get(base_url, params=params)
        response.raise_for_status()  # Raises an error for bad status codes
        data = response.json()
        
        if data['status'] == 'OK':
            results = data['results']
            print(f"Sunrise: {results['sunrise']}")
            print(f"Sunset: {results['sunset']}")
            return results
        else:
            print(f"API Error: {data['status']}")
            
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    get_sun_times(51.5074, -0.1278, '2026-06-20')
