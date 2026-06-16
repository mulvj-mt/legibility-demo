import requests

def get_weather_forecast(lat, lng, start_date, end_date):
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
        'start_date': start_date,
        'end_date': end_date,
        'daily': 'temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code',
        'timezone': 'auto'
    }
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        # Displaying the results
        daily = data['daily']
        print(f"Weather for ({lat}, {lng}) from {start_date} to {end_date}:")
        for i in range(len(daily['time'])):
            print(f"Date: {daily['time'][i]}")
            print(f"  Max Temp: {daily['temperature_2m_max'][i]}°C")
            print(f"  Min Temp: {daily['temperature_2m_min'][i]}°C")
            print(f"  Precipitation: {daily['precipitation_sum'][i]}mm")
            print("-" * 20)
            
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")

if __name__ == "__main__":
    get_weather_forecast(51.5074, -0.1278, '2026-06-20', '2026-06-20')