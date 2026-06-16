import requests
from datetime import datetime as dt


def get_sun_times(lat: float, lng: float, date_str: str | None=None) -> dict | None:
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
        'date': date_str if date_str else 'today',
        'formatted': 0  # 0 returns time in ISO 8601 format
    }
    
    try:
        response = requests.get(base_url, params=params)
        response.raise_for_status()  # Raises an error for bad status codes
        data = response.json()

        report = {}
        
        if data['status'] == 'OK':
            results = data['results']
            report = {
                "sunrise": results["sunrise"], 
                "sunset": results["sunset"]
            }
            return report
        else:
            print(f"API Error: {data['status']}")
            
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    print(get_sun_times(51.5074, -0.1278, '2026-06-20'))
