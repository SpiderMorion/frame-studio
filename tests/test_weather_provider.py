from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from eink_frame.weather_provider import OpenMeteoProvider

MSK = timezone(timedelta(hours=3))


@pytest.mark.anyio
async def test_open_meteo_provider_normalizes_hour_and_solar_times() -> None:
    payload = {
        "timezone": "Europe/Moscow",
        "hourly": {
            "time": ["2026-08-14T13:00", "2026-08-14T14:00"],
            "temperature_2m": [24.0, 24.3],
            "apparent_temperature": [22.5, 22.7],
            "precipitation_probability": [0, 0],
            "precipitation": [0.0, 0.0],
            "weather_code": [1, 1],
            "cloud_cover": [40, 44],
            "visibility": [45000, 46340],
            "wind_speed_10m": [10.0, 11.1],
            "wind_direction_10m": [345, 347],
            "wind_gusts_10m": [25.0, 29.5],
        },
        "daily": {
            "time": ["2026-08-14"],
            "sunrise": ["2026-08-14T05:19"],
            "sunset": ["2026-08-14T19:38"],
        },
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["timezone"] == "Europe/Moscow"
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenMeteoProvider(client=client)
        snapshot = await provider.fetch_snapshot(
            latitude=47.2392184,
            longitude=38.8755031,
            timezone_name="Europe/Moscow",
            target_at=datetime(2026, 8, 14, 14, 0, tzinfo=MSK),
        )

    assert snapshot.temperature_c == 24.3
    assert snapshot.cloud_cover_pct == 44
    assert snapshot.sunrise == datetime(2026, 8, 14, 5, 19, tzinfo=MSK)
    assert snapshot.sunset == datetime(2026, 8, 14, 19, 38, tzinfo=MSK)
    assert len(snapshot.raw_payload_sha256) == 64
