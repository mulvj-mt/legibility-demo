import requests

def get_weather_forecast(lat: float, lng: float, date_str: str) -> dict | None:
    """
    Fetches daily weather forecast data from Open-Meteo.
    
    :param lat: Latitude (float)
    :param lng: Longitude (float)
    :param start_date: String in 'YYYY-MM-DD'
    :param end_date: String in 'YYYY-MM-DD'
    """
    url = "https://api.open-meteo.com/v1/forecast"
    
    # Define the daily metrics you want
    params = {
        'latitude': lat,
        'longitude': lng,
        'start_date': date_str,
        'end_date': date_str,
        'daily': 'temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code',
        'timezone': 'auto'
    }
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        # Displaying the results
        daily = data['daily']
        return {
            "max_temp": daily['temperature_2m_max'][0],
            "min_temp": daily['temperature_2m_min'][0],
            "precipitation": daily['precipitation_sum'][0]
        }
            
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")

if __name__ == "__main__":
    print(get_weather_forecast(51.5074, -0.1278, '2026-06-20'))