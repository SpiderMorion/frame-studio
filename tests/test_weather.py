from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from eink_frame.weather import (
    SCENE_CATALOG,
    SCENE_CATALOG_VERSION,
    SCENE_WATER_SLOTS,
    WeatherSnapshot,
    build_prompt_snapshot,
)

MSK = timezone(timedelta(hours=3))


def weather_snapshot() -> WeatherSnapshot:
    return WeatherSnapshot(
        provider="open-meteo",
        latitude=47.2392184,
        longitude=38.8755031,
        timezone="Europe/Moscow",
        target_at=datetime(2026, 8, 14, 14, 0, tzinfo=MSK),
        fetched_at=datetime(2026, 8, 14, 13, 11, tzinfo=MSK),
        sunrise=datetime(2026, 8, 14, 5, 19, tzinfo=MSK),
        sunset=datetime(2026, 8, 14, 19, 38, tzinfo=MSK),
        temperature_c=24.3,
        apparent_temperature_c=22.7,
        precipitation_probability_pct=0,
        precipitation_mm=0,
        cloud_cover_pct=44,
        visibility_m=46_340,
        wind_speed_kmh=11.1,
        wind_direction_deg=347,
        wind_gust_kmh=29.5,
        weather_code=1,
        raw_payload_sha256="0" * 64,
    )


def test_prompt_snapshot_uses_versioned_taganrog_visual_profile() -> None:
    prompt = build_prompt_snapshot(weather_snapshot(), location_name="Taganrog, Russia")

    assert prompt.template_version == "photorealistic-weather-v4"
    assert prompt.visual_profile_version == "taganrog-scene-profiles-v2"
    assert prompt.scene_family in {"city", "landscape"}
    assert prompt.scene_id
    assert "horizontal 4:3 full-bleed photorealistic documentary photograph" in prompt.final_prompt
    assert "PRIMARY SUBJECT:" in prompt.final_prompt
    assert "SPATIAL COMPOSITION:" in prompt.final_prompt
    assert "LOCAL MATERIAL PROFILE:" in prompt.final_prompt
    assert "CURRENT CONDITIONS:" in prompt.final_prompt
    assert "maintained enough for active daily use" in prompt.final_prompt
    assert "Do not copy a specific landmark or reference photograph" in prompt.final_prompt
    assert "skyscrapers" in prompt.negative_prompt
    assert "alpine mountains" in prompt.negative_prompt
    assert "polished Mediterranean resort" in prompt.negative_prompt
    assert "American streets" in prompt.negative_prompt


def test_every_dry_scene_positive_prompt_contains_no_marine_vocabulary() -> None:
    marine_terms = (
        "sea",
        "azov",
        "coast",
        "waterfront",
        "harbor",
        "port",
        "quay",
        "pier",
        "ship",
        "boat",
        "beach",
    )
    dry_scene_ids = {scene.scene_id for scene in SCENE_CATALOG if scene.water_mode == "none"}
    seen: set[str] = set()
    recent: list[str] = []
    for index in range(18):
        prompt = build_prompt_snapshot(
            weather_snapshot(),
            location_name="Taganrog, Russia",
            recent_scene_ids=recent,
            rotation_index=index,
        )
        seen.add(prompt.scene_id)
        recent.insert(0, prompt.scene_id)
        lowered = prompt.final_prompt.lower()
        assert prompt.water_mode == "none"
        found = [term for term in marine_terms if term in lowered]
        assert not found, (prompt.scene_id, found)
        assert "taganrog" not in lowered
        assert "no distant horizon" in lowered
        assert "sea, ocean, bay, river, harbor, port" in prompt.negative_prompt

    assert seen == dry_scene_ids


def test_versioned_scene_catalog_preserves_exact_eighteen_one_one_water_mix() -> None:
    water_modes = [scene.water_mode for scene in SCENE_CATALOG]

    assert SCENE_CATALOG_VERSION == "taganrog-scene-catalog-v4"
    assert len(SCENE_CATALOG) == 20
    assert water_modes.count("none") == 18
    assert water_modes.count("bay") == 1
    assert water_modes.count("port") == 1
    assert len(SCENE_WATER_SLOTS) == 20
    assert SCENE_WATER_SLOTS.count("none") == 18
    assert SCENE_WATER_SLOTS.count("bay") == 1
    assert SCENE_WATER_SLOTS.count("port") == 1


def test_rejected_schoolyard_and_tram_interior_generation_methods_are_absent() -> None:
    scenes = {scene.scene_id: scene for scene in SCENE_CATALOG}

    assert "schoolyard" not in scenes
    assert "tram-interior" not in scenes
    tram_scenes = [scene for scene in SCENE_CATALOG if "tram" in scene.scene_id]
    assert len(tram_scenes) == 1
    tram = tram_scenes[0]
    combined = (f"{tram.description} {tram.spatial_guidance} {tram.local_profile}").lower()
    assert "secondary incidental detail" in combined
    assert "pedestrians" in combined
    assert "shops" in combined
    assert "trees" in combined
    assert "interior" not in combined
    assert "depot" not in combined
    assert "isolated" not in combined
    assert "abandoned" not in combined


def test_scene_selection_is_deterministic_and_excludes_recent_scene() -> None:
    snapshot = weather_snapshot()
    first = build_prompt_snapshot(snapshot, location_name="Taganrog, Russia")
    repeated = build_prompt_snapshot(snapshot, location_name="Taganrog, Russia")
    excluded = build_prompt_snapshot(
        snapshot,
        location_name="Taganrog, Russia",
        recent_scene_ids=(first.scene_id,),
    )
    excluded_again = build_prompt_snapshot(
        snapshot,
        location_name="Taganrog, Russia",
        recent_scene_ids=(first.scene_id,),
    )

    assert first == repeated
    assert excluded == excluded_again
    assert excluded.scene_id != first.scene_id
    assert excluded.content_sha256 != first.content_sha256


def test_first_five_sequential_slots_choose_five_distinct_dry_scenes() -> None:
    recent: list[str] = []
    prompts = []
    for index in range(5):
        prompt = build_prompt_snapshot(
            weather_snapshot(),
            location_name="Taganrog, Russia",
            recent_scene_ids=recent,
            rotation_index=index,
        )
        prompts.append(prompt)
        recent.insert(0, prompt.scene_id)

    assert len({prompt.scene_id for prompt in prompts}) == 5
    assert {prompt.water_mode for prompt in prompts} == {"none"}
    assert len({prompt.final_prompt for prompt in prompts}) == 5


def test_rotation_index_produces_exact_eighteen_dry_one_bay_one_port_cycle() -> None:
    water_modes = [
        build_prompt_snapshot(
            weather_snapshot(), location_name="Taganrog, Russia", rotation_index=index
        ).water_mode
        for index in range(20)
    ]

    assert water_modes == list(SCENE_WATER_SLOTS)
    assert water_modes[:18] == ["none"] * 18
    assert water_modes[18:] == ["bay", "port"]


def test_refetch_and_recent_exclusions_never_change_rotation_family() -> None:
    original = weather_snapshot()
    refetched = original.model_copy(
        update={
            "fetched_at": original.fetched_at + timedelta(minutes=30),
            "temperature_c": original.temperature_c + 8,
            "raw_payload_sha256": "f" * 64,
        }
    )
    family_scenes = tuple(scene.scene_id for scene in SCENE_CATALOG if scene.water_mode == "bay")

    first = build_prompt_snapshot(original, location_name="Taganrog, Russia", rotation_index=18)
    refreshed = build_prompt_snapshot(
        refetched, location_name="Taganrog, Russia", rotation_index=18
    )
    all_excluded = build_prompt_snapshot(
        refetched,
        location_name="Taganrog, Russia",
        recent_scene_ids=family_scenes,
        rotation_index=18,
    )

    assert first.water_mode == refreshed.water_mode == all_excluded.water_mode == "bay"
    assert first.scene_id == refreshed.scene_id == all_excluded.scene_id


def test_bay_scene_contains_marine_context_and_excludes_port_infrastructure() -> None:
    prompt = build_prompt_snapshot(
        weather_snapshot(),
        location_name="Taganrog, Russia",
        rotation_index=18,
    )

    assert prompt.scene_family == "landscape"
    assert prompt.water_mode == "bay"
    assert "broad shallow Taganrog Bay" in prompt.final_prompt
    assert "harbor cranes" in prompt.negative_prompt


def test_prompt_uses_immutable_weather_and_solar_offsets() -> None:
    snapshot = WeatherSnapshot(
        provider="open-meteo",
        latitude=47.2392184,
        longitude=38.8755031,
        timezone="Europe/Moscow",
        target_at=datetime(2026, 8, 14, 14, 0, tzinfo=MSK),
        fetched_at=datetime(2026, 8, 14, 13, 11, tzinfo=MSK),
        sunrise=datetime(2026, 8, 14, 5, 19, tzinfo=MSK),
        sunset=datetime(2026, 8, 14, 19, 38, tzinfo=MSK),
        temperature_c=24.3,
        apparent_temperature_c=22.7,
        precipitation_probability_pct=0,
        precipitation_mm=0,
        cloud_cover_pct=44,
        visibility_m=46_340,
        wind_speed_kmh=11.1,
        wind_direction_deg=347,
        wind_gust_kmh=29.5,
        weather_code=1,
        raw_payload_sha256="0" * 64,
    )

    first = build_prompt_snapshot(snapshot, location_name="Taganrog, Russia")
    second = build_prompt_snapshot(snapshot, location_name="Taganrog, Russia")
    prompt = first.final_prompt

    assert first == second
    assert len(first.content_sha256) == 64
    assert first.weather_payload_sha256 == snapshot.raw_payload_sha256
    assert first.template_version == "photorealistic-weather-v4"
    assert "8 hours 41 minutes after sunrise" in prompt
    assert "5 hours 38 minutes before sunset" in prompt
    assert "north-northwesterly" in prompt
    assert "24.3 degrees Celsius" in prompt
    assert "44 percent" in prompt
    assert "picture frame" not in prompt.lower()
    assert "product mockup" not in prompt.lower()


def test_prompt_describes_after_sunset_without_negative_duration() -> None:
    snapshot = WeatherSnapshot(
        provider="open-meteo",
        latitude=47.2392184,
        longitude=38.8755031,
        timezone="Europe/Moscow",
        target_at=datetime(2026, 8, 14, 20, 5, tzinfo=MSK),
        fetched_at=datetime(2026, 8, 14, 19, 50, tzinfo=MSK),
        sunrise=datetime(2026, 8, 14, 5, 19, tzinfo=MSK),
        sunset=datetime(2026, 8, 14, 19, 38, tzinfo=MSK),
        temperature_c=22,
        apparent_temperature_c=22,
        precipitation_probability_pct=0,
        precipitation_mm=0,
        cloud_cover_pct=10,
        visibility_m=20_000,
        wind_speed_kmh=5,
        wind_direction_deg=0,
        wind_gust_kmh=8,
        weather_code=0,
        raw_payload_sha256="1" * 64,
    )

    prompt = build_prompt_snapshot(snapshot, location_name="Taganrog, Russia").final_prompt

    assert "27 minutes after sunset" in prompt
    assert "-1 hours" not in prompt


def test_evening_twilight_requires_below_horizon_light_and_artificial_lights() -> None:
    snapshot = weather_snapshot().model_copy(
        update={
            "target_at": datetime(2026, 8, 28, 20, 0, tzinfo=MSK),
            "sunrise": datetime(2026, 8, 28, 5, 38, tzinfo=MSK),
            "sunset": datetime(2026, 8, 28, 19, 13, tzinfo=MSK),
            "cloud_cover_pct": 100,
            "weather_code": 3,
        }
    )

    result = build_prompt_snapshot(snapshot, location_name="Taganrog, Russia")
    prompt = result.final_prompt.lower()
    negative = result.negative_prompt.lower()

    assert result.template_version == "photorealistic-weather-v4"
    assert result.lighting_phase == "evening_twilight"
    assert "sun is below the horizon" in prompt
    assert "scene-appropriate existing artificial sources" in prompt
    assert "switched on where physically present" in prompt
    assert "no direct sunlight" in prompt
    assert "fully overcast" in prompt
    assert "no visible sun" in prompt
    assert "daylight" in negative
    assert "visible sun" in negative
    assert "hard solar shadows" in negative


def test_late_night_and_midnight_forbid_daylight_and_daytime_activity() -> None:
    for target in (
        datetime(2026, 8, 28, 22, 0, tzinfo=MSK),
        datetime(2026, 8, 29, 0, 0, tzinfo=MSK),
    ):
        snapshot = weather_snapshot().model_copy(
            update={
                "target_at": target,
                "sunrise": datetime(2026, 8, 29, 5, 39, tzinfo=MSK),
                "sunset": datetime(2026, 8, 28, 19, 13, tzinfo=MSK),
                "cloud_cover_pct": 100,
                "weather_code": 3,
            }
        )

        result = build_prompt_snapshot(snapshot, location_name="Taganrog, Russia")
        prompt = result.final_prompt.lower()
        negative = result.negative_prompt.lower()

        assert result.lighting_phase == "night"
        assert "full night" in prompt
        assert "there is no daylight" in prompt
        assert "provide the only illumination" in prompt
        assert "sparse and plausible for the late hour" in prompt
        assert "daylight" in negative
        assert "blue daytime sky" in negative
        assert "daytime crowds" in negative


def test_full_night_selects_only_late_hour_compatible_scenes() -> None:
    snapshot = weather_snapshot().model_copy(
        update={
            "target_at": datetime(2026, 8, 29, 0, 0, tzinfo=MSK),
            "sunrise": datetime(2026, 8, 29, 5, 39, tzinfo=MSK),
            "sunset": datetime(2026, 8, 28, 19, 13, tzinfo=MSK),
        }
    )
    allowed = {
        "quiet-residential-street",
        "old-new-corner",
        "soviet-block",
        "tree-boulevard",
        "tram-street-detail",
        "corner-cafe-street",
        "railway-edge",
    }
    recent: list[str] = []

    for index in range(18):
        result = build_prompt_snapshot(
            snapshot,
            location_name="Taganrog, Russia",
            recent_scene_ids=recent,
            rotation_index=index,
        )
        assert result.scene_id in allowed
        recent.insert(0, result.scene_id)
        recent = recent[:6]


def test_missing_solar_boundaries_never_classify_midnight_as_daylight() -> None:
    target = datetime(2026, 8, 29, 0, 0, tzinfo=MSK)
    cases = (
        {"sunrise": None, "sunset": datetime(2026, 8, 28, 19, 13, tzinfo=MSK)},
        {"sunrise": datetime(2026, 8, 29, 5, 39, tzinfo=MSK), "sunset": None},
        {"sunrise": None, "sunset": None},
    )

    for solar_update in cases:
        snapshot = weather_snapshot().model_copy(update={"target_at": target, **solar_update})
        result = build_prompt_snapshot(snapshot, location_name="Taganrog, Russia")

        assert result.lighting_phase == "night"
        assert "there is no daylight" in result.final_prompt.lower()


def test_known_sunrise_is_authoritative_when_sunset_is_missing() -> None:
    sunrise = datetime(2026, 8, 29, 5, 39, tzinfo=MSK)

    for target in (sunrise, sunrise + timedelta(minutes=30), sunrise + timedelta(minutes=80)):
        snapshot = weather_snapshot().model_copy(
            update={"target_at": target, "sunrise": sunrise, "sunset": None}
        )
        result = build_prompt_snapshot(snapshot, location_name="Taganrog, Russia")

        assert result.lighting_phase == "daylight"
        assert "sun is below the horizon" not in result.final_prompt.lower()


def test_early_sunrise_is_authoritative_when_sunset_is_missing() -> None:
    sunrise = datetime(2026, 6, 21, 4, 24, tzinfo=MSK)

    for target in (sunrise, sunrise + timedelta(minutes=30)):
        snapshot = weather_snapshot().model_copy(
            update={"target_at": target, "sunrise": sunrise, "sunset": None}
        )
        assert (
            build_prompt_snapshot(snapshot, location_name="Taganrog, Russia").lighting_phase
            == "daylight"
        )


def test_adjacent_day_solar_boundaries_use_the_applicable_nearest_transition() -> None:
    sunset = datetime(2026, 8, 28, 19, 13, tzinfo=MSK)
    sunrise = datetime(2026, 8, 29, 5, 39, tzinfo=MSK)
    cases = (
        (sunset, "late_afternoon"),
        (sunset + timedelta(seconds=1), "evening_twilight"),
        (sunset + timedelta(minutes=60), "evening_twilight"),
        (sunset + timedelta(minutes=60, seconds=1), "night"),
        (sunrise - timedelta(minutes=60, seconds=1), "night"),
        (sunrise - timedelta(minutes=60), "pre_dawn"),
        (sunrise - timedelta(seconds=1), "pre_dawn"),
        (sunrise, "daylight"),
        (sunrise + timedelta(seconds=1), "daylight"),
    )

    for target, expected in cases:
        snapshot = weather_snapshot().model_copy(
            update={"target_at": target, "sunrise": sunrise, "sunset": sunset}
        )
        assert (
            build_prompt_snapshot(snapshot, location_name="Taganrog, Russia").lighting_phase
            == expected
        )


def test_stale_or_out_of_window_solar_pairs_use_local_fallback() -> None:
    adjacent = weather_snapshot().model_copy(
        update={
            "sunset": datetime(2026, 8, 28, 19, 13, tzinfo=MSK),
            "sunrise": datetime(2026, 8, 29, 5, 39, tzinfo=MSK),
        }
    )
    same_day = weather_snapshot().model_copy(
        update={
            "sunrise": datetime(2026, 8, 29, 5, 39, tzinfo=MSK),
            "sunset": datetime(2026, 8, 29, 19, 13, tzinfo=MSK),
        }
    )
    cases = (
        (adjacent, datetime(2026, 8, 28, 0, 0, tzinfo=MSK), "night"),
        (adjacent, datetime(2026, 8, 29, 12, 0, tzinfo=MSK), "daylight"),
        (adjacent, datetime(2026, 8, 30, 0, 0, tzinfo=MSK), "night"),
        (same_day, datetime(2026, 8, 30, 12, 0, tzinfo=MSK), "daylight"),
    )

    for base, target, expected in cases:
        result = build_prompt_snapshot(
            base.model_copy(update={"target_at": target}),
            location_name="Taganrog, Russia",
        )
        assert result.lighting_phase == expected


def test_stale_partial_and_non_adjacent_solar_boundaries_use_local_fallback() -> None:
    cases = (
        (
            {"sunrise": datetime(2026, 8, 29, 5, 39, tzinfo=MSK), "sunset": None},
            datetime(2026, 8, 28, 12, 0, tzinfo=MSK),
            "daylight",
        ),
        (
            {"sunrise": None, "sunset": datetime(2026, 8, 28, 19, 13, tzinfo=MSK)},
            datetime(2026, 8, 29, 12, 0, tzinfo=MSK),
            "daylight",
        ),
        (
            {
                "sunset": datetime(2026, 8, 28, 19, 13, tzinfo=MSK),
                "sunrise": datetime(2026, 8, 30, 5, 39, tzinfo=MSK),
            },
            datetime(2026, 8, 29, 12, 0, tzinfo=MSK),
            "daylight",
        ),
    )

    for solar, target, expected in cases:
        result = build_prompt_snapshot(
            weather_snapshot().model_copy(update={"target_at": target, **solar}),
            location_name="Taganrog, Russia",
        )
        assert result.lighting_phase == expected


def test_invalid_solar_pair_exact_endpoints_use_local_fallback() -> None:
    sunset = datetime(2026, 8, 28, 19, 13, tzinfo=MSK)
    sunrise = datetime(2026, 8, 30, 5, 39, tzinfo=MSK)

    at_sunset = build_prompt_snapshot(
        weather_snapshot().model_copy(
            update={"target_at": sunset, "sunrise": sunrise, "sunset": sunset}
        ),
        location_name="Taganrog, Russia",
    )
    at_sunrise = build_prompt_snapshot(
        weather_snapshot().model_copy(
            update={"target_at": sunrise, "sunrise": sunrise, "sunset": sunset}
        ),
        location_name="Taganrog, Russia",
    )

    assert at_sunset.lighting_phase == "evening_twilight"
    assert at_sunrise.lighting_phase == "pre_dawn"


def test_weather_codes_produce_distinct_visible_phenomena() -> None:
    cases = (
        (45, ("fog",), ("crystal-clear distance",)),
        (61, ("rain", "wet surfaces"), ("falling snow", "dry pavement")),
        (71, ("snow", "snow accumulation"), ("rain-only appearance",)),
        (95, ("thunderstorm", "convective storm clouds"), ("calm fair weather",)),
    )

    for weather_code, required, forbidden in cases:
        snapshot = weather_snapshot().model_copy(
            update={"weather_code": weather_code, "precipitation_mm": 0}
        )
        result = build_prompt_snapshot(snapshot, location_name="Taganrog, Russia")
        prompt = result.final_prompt.lower()
        negative = result.negative_prompt.lower()

        assert all(text in prompt for text in required)
        assert all(text in negative for text in forbidden)


def test_weather_code_reconciles_conflicting_cloud_and_visibility_numbers() -> None:
    fog = build_prompt_snapshot(
        weather_snapshot().model_copy(
            update={"weather_code": 45, "visibility_m": 50_000, "cloud_cover_pct": 0}
        ),
        location_name="Taganrog, Russia",
    ).final_prompt.lower()
    rain = build_prompt_snapshot(
        weather_snapshot().model_copy(
            update={"weather_code": 61, "precipitation_mm": 0, "cloud_cover_pct": 0}
        ),
        location_name="Taganrog, Russia",
    ).final_prompt.lower()
    thunder = build_prompt_snapshot(
        weather_snapshot().model_copy(
            update={"weather_code": 95, "visibility_m": 50_000, "cloud_cover_pct": 0}
        ),
        location_name="Taganrog, Russia",
    ).final_prompt.lower()

    assert "localized patches of fog" in fog
    assert "distance obscured" not in fog
    assert "mostly clear sky" not in rain
    assert "precipitation-bearing cloud" in rain
    assert "locally reduced visibility" not in thunder


def test_timezone_equivalent_targets_have_identical_scene_prompt_and_hash() -> None:
    local_target = datetime(2026, 8, 29, 0, 0, tzinfo=MSK)
    utc_target = local_target.astimezone(UTC)
    base = weather_snapshot().model_copy(
        update={
            "sunrise": datetime(2026, 8, 29, 5, 39, tzinfo=MSK),
            "sunset": datetime(2026, 8, 28, 19, 13, tzinfo=MSK),
        }
    )

    local = build_prompt_snapshot(
        base.model_copy(update={"target_at": local_target}),
        location_name="Taganrog, Russia",
    )
    utc = build_prompt_snapshot(
        base.model_copy(update={"target_at": utc_target}),
        location_name="Taganrog, Russia",
    )

    assert utc.scene_id == local.scene_id
    assert utc.final_prompt == local.final_prompt
    assert utc.content_sha256 == local.content_sha256


def test_full_night_marine_slots_do_not_inject_city_light_sources() -> None:
    snapshot = weather_snapshot().model_copy(
        update={
            "target_at": datetime(2026, 8, 29, 0, 0, tzinfo=MSK),
            "sunrise": datetime(2026, 8, 29, 5, 39, tzinfo=MSK),
            "sunset": datetime(2026, 8, 28, 19, 13, tzinfo=MSK),
        }
    )

    for rotation_index in (18, 19):
        prompt = build_prompt_snapshot(
            snapshot,
            location_name="Taganrog, Russia",
            rotation_index=rotation_index,
        ).final_prompt.lower()
        assert "cafe terraces" not in prompt
        assert "shop lights" not in prompt
        assert "lit windows" not in prompt
        assert "street lamps" not in prompt


def test_lighting_phase_boundaries_are_exact_and_timezone_equivalent() -> None:
    sunrise = datetime(2026, 8, 29, 5, 39, tzinfo=MSK)
    sunset = datetime(2026, 8, 29, 19, 13, tzinfo=MSK)
    cases = (
        (sunrise - timedelta(minutes=60, seconds=1), "night"),
        (sunrise - timedelta(minutes=60), "pre_dawn"),
        (sunrise, "daylight"),
        (sunset - timedelta(minutes=90, seconds=1), "daylight"),
        (sunset - timedelta(minutes=90), "late_afternoon"),
        (sunset, "late_afternoon"),
        (sunset + timedelta(minutes=60), "evening_twilight"),
        (sunset + timedelta(minutes=60, seconds=1), "night"),
    )

    for target, expected in cases:
        snapshot = weather_snapshot().model_copy(
            update={"target_at": target, "sunrise": sunrise, "sunset": sunset}
        )
        assert (
            build_prompt_snapshot(snapshot, location_name="Taganrog, Russia").lighting_phase
            == expected
        )

    utc_nine_pm_in_taganrog = datetime(2026, 8, 28, 18, 0, tzinfo=UTC)
    no_solar = weather_snapshot().model_copy(
        update={
            "target_at": utc_nine_pm_in_taganrog,
            "sunrise": None,
            "sunset": None,
        }
    )
    timezone_result = build_prompt_snapshot(no_solar, location_name="Taganrog, Russia")
    assert timezone_result.lighting_phase == "night"
    assert "21:00 local time" in timezone_result.final_prompt
    assert "18:00 local time" not in timezone_result.final_prompt


def test_full_night_rewrites_every_eligible_primary_subject_for_sparse_activity() -> None:
    snapshot = weather_snapshot().model_copy(
        update={
            "target_at": datetime(2026, 8, 29, 0, 0, tzinfo=MSK),
            "sunrise": datetime(2026, 8, 29, 5, 39, tzinfo=MSK),
            "sunset": datetime(2026, 8, 28, 19, 13, tzinfo=MSK),
        }
    )
    eligible = {
        "quiet-residential-street",
        "old-new-corner",
        "soviet-block",
        "tree-boulevard",
        "tram-street-detail",
        "corner-cafe-street",
        "railway-edge",
    }
    forbidden = (
        "busy everyday corner",
        "ordinary lively neighborhood street",
        "several small cafe terraces, friends talking, servers moving",
        "dog walkers",
    )

    for wanted_scene in eligible:
        result = build_prompt_snapshot(
            snapshot,
            location_name="Taganrog, Russia",
            recent_scene_ids=sorted(eligible - {wanted_scene}),
        )
        prompt = result.final_prompt.lower()

        assert result.scene_id == wanted_scene
        assert "late-hour" in prompt
        assert all(text not in prompt for text in forbidden)


def test_scene_profile_prefers_maintained_buildings_and_lively_cafe_street() -> None:
    cafe = next(scene for scene in SCENE_CATALOG if scene.scene_id == "corner-cafe-street")
    cafe_text = f"{cafe.description} {cafe.local_profile}".lower()
    prompt = build_prompt_snapshot(weather_snapshot(), location_name="Taganrog, Russia")

    assert "pedestrian cafe street" in cafe_text
    assert "several small cafe terraces" in cafe_text
    assert "moscow" not in cafe_text
    assert "arbat" not in cafe_text
    assert "maintained enough for active daily use" in prompt.final_prompt
    assert "widespread peeling plaster" in prompt.negative_prompt
    assert "crumbling facades" in prompt.negative_prompt
    assert "poverty porn" in prompt.negative_prompt
