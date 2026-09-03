"""Real public weather tool implementation.

Fetches live real-time weather metrics using public weather APIs (Open-Meteo & OpenWeatherMap),
supporting backend API key configuration, strict input validation, timeout protection, and
comprehensive error handling.
"""

import logging
import os
from typing import Any, Dict, Optional
import httpx

logger = logging.getLogger(__name__)

SUPPORTED_UNITS = ("celsius", "fahrenheit")

# Standard WMO Weather Interpretation Codes for Open-Meteo
WMO_WEATHER_CODES: Dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}


def _get_api_key() -> str:
    """Retrieve weather API key from backend environment."""
    return os.getenv("WEATHER_API_KEY", "").strip()


def _get_api_provider() -> str:
    """Retrieve weather API provider: 'open-meteo' or 'openweathermap'."""
    return os.getenv("WEATHER_API_PROVIDER", "open-meteo").lower().strip()


def get_weather(
    city: Optional[str] = None,
    location: Optional[str] = None,
    unit: Optional[str] = None,
    timeout: float = 6.0,
) -> Dict[str, Any]:
    """Retrieve current live weather report for a given city from a public weather API.

    Args:
        city: Target city name (e.g. "Pune", "London", "Tokyo", "New York").
        location: Alias for city for backward compatibility.
        unit: Temperature scale: "celsius" or "fahrenheit". Defaults to "celsius" when
              omitted (None). An explicitly supplied empty string is rejected as invalid.
        timeout: HTTP request timeout in seconds (default: 6.0).

    Returns:
        Structured dictionary containing live weather metrics or error details.
    """
    # 1. Check API key configuration
    api_key = _get_api_key()
    if not api_key:
        logger.warning("get_weather invoked without WEATHER_API_KEY configured.")
        return {
            "status": "error",
            "error": "Weather API key is not configured. Please set WEATHER_API_KEY in your .env file or environment.",
        }

    # 2. Validate city parameter
    raw_city = city or location
    if not raw_city or not isinstance(raw_city, str) or not raw_city.strip():
        return {
            "status": "error",
            "error": "Parameter 'city' is required and must be a non-empty string.",
        }

    clean_city = raw_city.strip()

    # 3. Validate unit parameter
    # unit=None means the caller omitted the argument; default to "celsius".
    # An explicitly supplied value (including an empty string) must be a valid unit.
    if unit is None:
        clean_unit = "celsius"
    else:
        clean_unit = unit.lower().strip() if isinstance(unit, str) else ""
        if clean_unit not in SUPPORTED_UNITS:
            return {
                "status": "error",
                "error": f"Invalid unit '{unit}'. Supported units: {', '.join(SUPPORTED_UNITS)}.",
            }

    provider = _get_api_provider()

    # 4. Fetch live weather according to configured provider
    try:
        if provider == "openweathermap" and api_key not in ("open-meteo", "free_public_access", "public"):
            return _fetch_openweathermap(clean_city, clean_unit, api_key, timeout)
        else:
            return _fetch_open_meteo(clean_city, clean_unit, timeout)

    except httpx.TimeoutException:
        logger.error("Weather API request timed out after %s seconds for city '%s'.", timeout, clean_city)
        return {
            "status": "error",
            "error": f"Weather API request timed out after {timeout} seconds.",
        }
    except httpx.HTTPStatusError as http_err:
        logger.error("Weather API HTTP error %s for city '%s': %s", http_err.response.status_code, clean_city, http_err)
        return {
            "status": "error",
            "error": f"Weather API returned HTTP {http_err.response.status_code}.",
        }
    except httpx.RequestError as req_err:
        logger.error("Weather API network connection failure for city '%s': %s", clean_city, req_err)
        return {
            "status": "error",
            "error": f"Weather API connection failed: {req_err}",
        }
    except Exception as exc:
        logger.exception("Unexpected error fetching weather for city '%s': %s", clean_city, exc)
        return {
            "status": "error",
            "error": f"Failed to retrieve weather: {exc}",
        }


def _fetch_open_meteo(city: str, unit: str, timeout: float) -> Dict[str, Any]:
    """Fetch live real-time weather from Open-Meteo public API."""
    headers = {"User-Agent": "RealTimeVoiceAssistant/1.0"}

    with httpx.Client(timeout=timeout, headers=headers) as client:
        # Step A: Geocode city name to coordinates
        geo_url = "https://geocoding-api.open-meteo.com/v1/search"
        geo_resp = client.get(geo_url, params={"name": city, "count": 1, "language": "en", "format": "json"})
        geo_resp.raise_for_status()

        geo_data = geo_resp.json()
        results = geo_data.get("results")
        if not results:
            return {
                "status": "error",
                "error": f"City '{city}' not found.",
            }

        place = results[0]
        lat = place.get("latitude")
        lon = place.get("longitude")
        resolved_name = place.get("name", city)
        country = place.get("country", "")

        # Step B: Fetch current weather metrics
        forecast_url = "https://api.open-meteo.com/v1/forecast"
        params: Dict[str, Any] = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
        }
        if unit == "fahrenheit":
            params["temperature_unit"] = "fahrenheit"

        weather_resp = client.get(forecast_url, params=params)
        weather_resp.raise_for_status()

        weather_data = weather_resp.json()
        current = weather_data.get("current", {})

        temp = round(current.get("temperature_2m", 0.0), 1)
        humidity = current.get("relative_humidity_2m", 0)
        wind = round(current.get("wind_speed_10m", 0.0), 1)
        weather_code = current.get("weather_code", 0)
        condition = WMO_WEATHER_CODES.get(weather_code, "Fair")

        location_str = f"{resolved_name}, {country}" if country else resolved_name

        return {
            "status": "success",
            "tool": "get_weather",
            "city": resolved_name,
            "country": country,
            "location": location_str,
            "temperature": temp,
            "unit": unit,
            "condition": condition,
            "humidity_percent": humidity,
            "wind_kmh": wind,
            "description": f"The weather in {location_str} is currently {temp}° {unit.capitalize()} and {condition}.",
        }


def _fetch_openweathermap(city: str, unit: str, api_key: str, timeout: float) -> Dict[str, Any]:
    """Fetch live weather from OpenWeatherMap API."""
    url = "https://api.openweathermap.org/data/2.5/weather"
    units_param = "metric" if unit == "celsius" else "imperial"

    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, params={"q": city, "appid": api_key, "units": units_param})

        if resp.status_code == 404:
            return {"status": "error", "error": f"City '{city}' not found."}
        if resp.status_code == 401:
            logger.warning(
                "OpenWeatherMap returned 401 (API key may take up to 1 hour to activate on their servers). "
                "Falling back to Open-Meteo live API."
            )
            return _fetch_open_meteo(city, unit, timeout)

        resp.raise_for_status()
        data = resp.json()

        main = data.get("main", {})
        weather = data.get("weather", [{}])[0]
        wind = data.get("wind", {})
        sys = data.get("sys", {})

        temp = round(main.get("temp", 0.0), 1)
        humidity = main.get("humidity", 0)
        condition = weather.get("main", "Clear")
        wind_speed = round(wind.get("speed", 0.0) * (3.6 if unit == "celsius" else 1.0), 1)
        resolved_name = data.get("name", city)
        country = sys.get("country", "")
        location_str = f"{resolved_name}, {country}" if country else resolved_name

        return {
            "status": "success",
            "tool": "get_weather",
            "city": resolved_name,
            "country": country,
            "location": location_str,
            "temperature": temp,
            "unit": unit,
            "condition": condition,
            "humidity_percent": humidity,
            "wind_kmh": wind_speed,
            "description": f"The weather in {location_str} is currently {temp}° {unit.capitalize()} and {condition}.",
        }
