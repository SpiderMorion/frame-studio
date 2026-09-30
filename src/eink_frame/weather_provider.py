from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .weather import WeatherSnapshot

HOURLY_FIELDS = (
    "temperature_2m,apparent_temperature,precipitation_probability,precipitation,"
    "weather_code,cloud_cover,visibility,wind_speed_10m,wind_direction_10m,wind_gusts_10m"
)


class OpenMeteoProvider:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str = "https://api.open-meteo.com/v1/forecast",
    ) -> None:
        self._client = client
        self._base_url = base_url

    async def fetch_snapshot(
        self,
        *,
        latitude: float,
        longitude: float,
        timezone_name: str,
        target_at: datetime,
    ) -> WeatherSnapshot:
        response = await self._client.get(
            self._base_url,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "timezone": timezone_name,
                "hourly": HOURLY_FIELDS,
                "daily": "sunrise,sunset",
                "forecast_days": 2,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        hour_key = target_at.strftime("%Y-%m-%dT%H:00")
        hourly: dict[str, list[Any]] = payload["hourly"]
        index = hourly["time"].index(hour_key)
        day_key = target_at.strftime("%Y-%m-%d")
        daily: dict[str, list[Any]] = payload["daily"]
        day_index = daily["time"].index(day_key)
        local_zone = ZoneInfo(timezone_name)

        def local_datetime(value: str) -> datetime:
            return datetime.fromisoformat(value).replace(tzinfo=local_zone)

        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return WeatherSnapshot(
            provider="open-meteo",
            latitude=latitude,
            longitude=longitude,
            timezone=timezone_name,
            target_at=target_at,
            fetched_at=datetime.now(UTC),
            sunrise=local_datetime(daily["sunrise"][day_index]),
            sunset=local_datetime(daily["sunset"][day_index]),
            temperature_c=hourly["temperature_2m"][index],
            apparent_temperature_c=hourly["apparent_temperature"][index],
            precipitation_probability_pct=hourly["precipitation_probability"][index],
            precipitation_mm=hourly["precipitation"][index],
            cloud_cover_pct=hourly["cloud_cover"][index],
            visibility_m=hourly["visibility"][index],
            wind_speed_kmh=hourly["wind_speed_10m"][index],
            wind_direction_deg=hourly["wind_direction_10m"][index],
            wind_gust_kmh=hourly["wind_gusts_10m"][index],
            weather_code=hourly["weather_code"][index],
            raw_payload_sha256=hashlib.sha256(canonical).hexdigest(),
        )
