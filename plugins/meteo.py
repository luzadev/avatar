"""Meteo con Open-Meteo (gratuito, senza chiave): oggi, domani o i prossimi giorni per una città."""
from __future__ import annotations

from datetime import datetime

import requests

CODES = {0: "sereno", 1: "prevalentemente sereno", 2: "parzialmente nuvoloso", 3: "coperto", 45: "nebbia", 48: "nebbia con brina",
         51: "pioviggine leggera", 53: "pioviggine", 55: "pioviggine intensa", 61: "pioggia leggera", 63: "pioggia", 65: "pioggia forte",
         71: "neve leggera", 73: "neve", 75: "neve forte", 80: "rovesci leggeri", 81: "rovesci", 82: "rovesci violenti", 95: "temporale",
         96: "temporale con grandine", 99: "temporale con grandine forte"}
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


def _geocode(city: str) -> tuple[float, float, str]:
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 1, "language": "it"}, timeout=10)
    r.raise_for_status()
    res = (r.json().get("results") or [])
    if not res:
        raise ValueError(f"Città non trovata: {city}")
    g = res[0]
    return g["latitude"], g["longitude"], f"{g['name']}{', ' + g['admin1'] if g.get('admin1') else ''}"


def previsioni(params: dict, ctx: dict) -> str:
    city = str(params.get("citta", "")).strip() or "Roma"
    giorni = max(1, min(7, int(params.get("giorni") or 2)))
    lat, lon, label = _geocode(city)
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon, "timezone": "auto",
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum",
        "forecast_days": giorni}, timeout=10)
    r.raise_for_status()
    d = r.json()
    cur = d.get("current", {})
    out = [f"Meteo a {label}: ora {CODES.get(cur.get('weather_code'), '')}, {cur.get('temperature_2m')}° (percepiti {cur.get('apparent_temperature')}°), vento {cur.get('wind_speed_10m')} km/h, umidità {cur.get('relative_humidity_2m')}%."]
    daily = d.get("daily", {})
    for i, day in enumerate(daily.get("time", [])):
        dt = datetime.strptime(day, "%Y-%m-%d")
        nome = "oggi" if i == 0 else ("domani" if i == 1 else GIORNI[dt.weekday()])
        out.append(f"- {nome} {dt:%d/%m}: {CODES.get(daily['weather_code'][i], '')}, min {daily['temperature_2m_min'][i]}° max {daily['temperature_2m_max'][i]}°, pioggia {daily['precipitation_probability_max'][i]}%")
    return "\n".join(out)


TOOLS = [
    {"name": "meteo", "description": "Previsioni meteo per una città: ora, oggi e i prossimi giorni.",
     "parameters": {"type": "object", "properties": {"citta": {"type": "string"}, "giorni": {"type": "integer", "description": "Quanti giorni (1-7, default 2)."}}, "required": ["citta"]},
     "run": previsioni},
]
