from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

SceneFamily = Literal["city", "landscape"]
WaterMode = Literal["none", "bay", "port"]
LightingPhase = Literal["pre_dawn", "daylight", "late_afternoon", "evening_twilight", "night"]
SCENE_CATALOG_VERSION = "taganrog-scene-catalog-v4"
SCENE_WATER_SLOTS: tuple[WaterMode, ...] = (
    "none",
    "none",
    "none",
    "none",
    "none",
    "none",
    "none",
    "none",
    "none",
    "none",
    "none",
    "none",
    "none",
    "none",
    "none",
    "none",
    "none",
    "none",
    "bay",
    "port",
)


@dataclass(frozen=True)
class Scene:
    scene_id: str
    family: SceneFamily
    water_mode: WaterMode
    description: str
    spatial_guidance: str
    local_profile: str


SCENE_CATALOG = (
    Scene(
        "quiet-residential-street",
        "city",
        "none",
        "an ordinary residential street with a maintained one-story stucco house, a modest red-brick house, repaired asphalt and mature trees; a resident walks beside a bicycle while another returns through a gate",
        "eye-level 50 mm view across the street; opposite facades and dense tree canopy fill the background; foreground and middle distance only; no distant horizon",
        "heterogeneous, practical and inhabited: patched stucco, exposed brick, sheet-metal roofs, concrete curbs, utility poles and repairs from different decades",
    ),
    Scene(
        "narrow-private-lane",
        "city",
        "none",
        "a narrow private-sector lane where a resident repairs a bicycle beside mismatched wooden and metal gates, fruit trees and small detached houses",
        "camera at pedestrian eye level inside the lane; fences, houses and foliage close every line of sight; intimate middle-distance composition",
        "modest domestic construction, improvised additions, old fences, drainpipes, utility wires, garden plants and worn surfaces",
    ),
    Scene(
        "old-new-corner",
        "city",
        "none",
        "a busy everyday corner where a worn pre-revolutionary house stands beside a plain Soviet low-rise building and pedestrians wait to cross",
        "35 mm street-level diagonal view across the junction; buildings occupy the complete far edge of the frame; no panoramic opening",
        "mixed-age city fabric with ordinary asphalt, simple signs without readable lettering, utility poles, mature trees and restrained provincial scale",
    ),
    Scene(
        "facade-fragment",
        "city",
        "none",
        "an intimate architectural fragment: maintained pale plaster, tall windows, drainpipes, a restored old gate and shifting tree shadows while a caretaker sweeps the pavement",
        "tight 85 mm crop with facade details filling the frame; shallow depth and no vista",
        "well-kept provincial craft rather than a polished monument: restrained age, sound masonry, maintained timber and everyday care",
    ),
    Scene(
        "brick-street",
        "city",
        "none",
        "a red-brick neighborhood street with simple cornices, workshops behind old gates and two workers unloading boxes from a small van",
        "eye-level 50 mm side view along a short block; a bend and dense buildings terminate the background",
        "exposed red brick, modest metal roofs, utilitarian doors, patched paving and mixed plastered houses under deciduous trees",
    ),
    Scene(
        "soviet-block",
        "city",
        "none",
        "an ordinary late-Soviet residential block where a few neighbors cross the courtyard or enter through a practical well-lit doorway",
        "human-height documentary view between three- to five-story buildings; the courtyard is visually enclosed by buildings and tree crowns",
        "unpretentious Soviet housing, older trees, concrete curbs, practical balconies, parked everyday cars and one surviving small older house",
    ),
    Scene(
        "inner-courtyard",
        "city",
        "none",
        "a deep lived-in courtyard where two residents repair a chair near a one-story outbuilding, grape vines, buckets and a bicycle",
        "50 mm view through a partly open gate; walls, the opposite outbuilding and dense foliage fill the entire background; foreground and middle distance only; no distant horizon",
        "patched plaster, exposed brick, simple timber additions, drainpipes, uneven paving and quiet domestic clutter",
    ),
    Scene(
        "shared-courtyard",
        "city",
        "none",
        "a shared courtyard between several old low-rise homes with laundry, sheds, garden plants and residents drinking tea at a small table",
        "camera inside the courtyard at table height; surrounding homes and trees form a complete visual enclosure",
        "layered repairs from several generations, mismatched chairs, pipes, vines, worn steps and practical household objects",
    ),
    Scene(
        "market-street",
        "city",
        "none",
        "a modest neighborhood market street with produce tables, small kiosks and local residents choosing vegetables",
        "35 mm candid view from within the crowd; stalls and mixed low-rise buildings fill the middle and far background",
        "functional everyday commerce, old trees, awnings, patched sidewalks and mixed brick, stucco and Soviet-era construction",
    ),
    Scene(
        "shaded-park",
        "city",
        "none",
        "a deeply shaded city park path where a child runs past pensioners playing chess on a simple bench",
        "long 70 mm view under a dense deciduous canopy; foliage and low buildings close the end of the path; no open horizon",
        "ordinary civic landscaping, worn paths, old lamp posts, modest flower beds and mature trees rather than formal palace gardens",
    ),
    Scene(
        "tree-boulevard",
        "city",
        "none",
        "a leafy pedestrian boulevard with dog walkers, a newspaper kiosk without readable text and people resting on simple benches",
        "eye-level 50 mm view across the boulevard; dense foliage and low mixed-age buildings occupy the whole background",
        "restrained public space, simple lamps, repaired paving, thick deciduous canopy and everyday provincial activity",
    ),
    Scene(
        "tram-street-detail",
        "city",
        "none",
        "an ordinary lively neighborhood street where pedestrians pass small shops and courtyard gates beneath mature trees, with a city tram present only as a secondary incidental detail in the middle distance",
        "35 mm eye-level documentary view led by the sidewalk activity and shopfronts; the tram occupies a small peripheral part of the frame and the next block closes the view",
        "everyday street life, heterogeneous low-rise buildings, practical storefronts without readable text, mature trees, rails embedded in asphalt and restrained overhead contact wires",
    ),
    Scene(
        "private-house-street",
        "city",
        "none",
        "an ordinary residential street of modest detached houses where neighbors talk by a gate, a cyclist passes and someone tends plants along the sidewalk",
        "50 mm eye-level view across the inhabited near side of a short block; houses, fences and dense tree crowns close the background",
        "mixed brick and plaster homes, practical gates, repaired sidewalks, garden plants, utility lines and everyday domestic upkeep without picturesque staging",
    ),
    Scene(
        "corner-cafe-street",
        "city",
        "none",
        "a compact pedestrian cafe street in a small southern city with several small cafe terraces, friends talking, servers moving between tables and local residents strolling under mature trees",
        "candid 50 mm view along the pedestrian street; warm terraces lead through the middle distance while maintained low-rise facades and tree crowns close the background",
        "lively but locally scaled evening commerce, well-kept plaster and brick facades, varied restrained outdoor furniture, simple awnings without readable text and no metropolitan grandeur",
    ),
    Scene(
        "garage-lane",
        "city",
        "none",
        "a narrow service lane of garages and backyard workshops where a mechanic works beside an old compact car",
        "low 35 mm view inside the lane; garage doors, walls and utility structures block the background",
        "corrugated metal, brick repairs, concrete patches, pipes, cables and functional objects without picturesque polish",
    ),
    Scene(
        "railway-edge",
        "city",
        "none",
        "a modest neighborhood beside railway sidings where a local commuter walks past warehouses and small houses",
        "long-lens lateral composition across tracks; warehouses and tree rows form the complete distant boundary",
        "working urban edge with rails, utilitarian brick sheds, fences, weeds, modest houses and no monumental industry",
    ),
    Scene(
        "apartment-courtyard",
        "city",
        "none",
        "a present-day apartment courtyard where residents carry shopping, a delivery courier parks a bicycle and children draw on the pavement",
        "eye-level 35 mm view enclosed by apartment wings and mature tree canopy",
        "mixed late-Soviet and contemporary repairs, practical entrances, balconies, parked everyday cars and ordinary landscaping",
    ),
    Scene(
        "small-shop-delivery",
        "city",
        "none",
        "a delivery to a tiny corner shop in a mixed residential block, with crates, a shopkeeper and passing pedestrians",
        "candid 50 mm street-level composition focused on the doorway and sidewalk; nearby facades fill the background",
        "small-scale commerce, patched stucco, brick, simple awning without readable text, drainpipes and mature street trees",
    ),
    Scene(
        "azov-bay",
        "landscape",
        "bay",
        "the broad shallow Taganrog Bay with reeds, a low flat horizon and a few people walking along an unpolished natural edge",
        "wide horizontal view dominated by low muted water and sky, with no dramatic elevation",
        "restrained gray-green shallows, native vegetation and ordinary local access rather than a resort beach",
    ),
    Scene(
        "working-port",
        "city",
        "port",
        "a modest working port with a few workboats, low warehouses, utilitarian piers and workers handling practical cargo",
        "documentary view across an industrial apron that clearly separates warehouses and city fabric from the quay",
        "functional restrained infrastructure, one or two modest cranes, service roads and working space without picturesque residential facades",
    ),
)

NIGHT_COMPATIBLE_DRY_SCENE_IDS = frozenset(
    {
        "quiet-residential-street",
        "old-new-corner",
        "soviet-block",
        "tree-boulevard",
        "tram-street-detail",
        "corner-cafe-street",
        "railway-edge",
    }
)

NIGHT_SCENE_DESCRIPTIONS = {
    "quiet-residential-street": "a quiet late-hour residential street where one resident walks beside a bicycle toward a lit gate and only a few windows remain illuminated",
    "old-new-corner": "a calm late-hour corner where one or two pedestrians cross between maintained low-rise buildings under switched-on street lamps",
    "soviet-block": "a quiet late-hour residential courtyard where one resident approaches a well-lit entrance and a few apartment windows remain illuminated",
    "tree-boulevard": "a calm late-hour pedestrian boulevard with one dog walker, a closed kiosk and pools of light from simple switched-on lamps",
    "tram-street-detail": "a quiet late-hour neighborhood street with a few pedestrians near lit shopfronts and a city tram present only as a secondary incidental detail in the middle distance",
    "corner-cafe-street": "a calm late-hour pedestrian cafe street with one or two warm terraces winding down, a few remaining patrons and a server clearing tables",
    "railway-edge": "a quiet late-hour neighborhood beside railway sidings where one late commuter walks under sparse practical lighting past low warehouses and small houses",
}


class WeatherSnapshot(BaseModel):
    """Immutable normalized weather input used by one generation attempt."""

    model_config = ConfigDict(frozen=True)

    provider: str
    latitude: float
    longitude: float
    timezone: str
    target_at: datetime
    fetched_at: datetime
    sunrise: datetime | None
    sunset: datetime | None
    temperature_c: float
    apparent_temperature_c: float
    precipitation_probability_pct: int = Field(ge=0, le=100)
    precipitation_mm: float = Field(ge=0)
    cloud_cover_pct: int = Field(ge=0, le=100)
    visibility_m: int = Field(ge=0)
    wind_speed_kmh: float = Field(ge=0)
    wind_direction_deg: int = Field(ge=0, lt=360)
    wind_gust_kmh: float = Field(ge=0)
    weather_code: int
    raw_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_aware_timestamps(self) -> WeatherSnapshot:
        timestamps = (self.target_at, self.fetched_at, self.sunrise, self.sunset)
        if any(value is not None and value.utcoffset() is None for value in timestamps):
            raise ValueError("weather timestamps must be timezone-aware")
        return self


class PromptSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    template_version: str
    visual_profile_version: str
    scene_id: str
    scene_family: Literal["city", "landscape"]
    water_mode: WaterMode
    lighting_phase: LightingPhase
    location_name: str
    weather_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    final_prompt: str
    negative_prompt: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _duration_words(seconds: int) -> str:
    minutes = seconds // 60
    hours, minutes = divmod(minutes, 60)
    return f"{hours} hours {minutes} minutes"


def _wind_direction(degrees: int) -> str:
    names = (
        "northerly",
        "north-northeasterly",
        "northeasterly",
        "east-northeasterly",
        "easterly",
        "east-southeasterly",
        "southeasterly",
        "south-southeasterly",
        "southerly",
        "south-southwesterly",
        "southwesterly",
        "west-southwesterly",
        "westerly",
        "west-northwesterly",
        "northwesterly",
        "north-northwesterly",
    )
    return names[int((degrees + 11.25) // 22.5) % 16]


def _fallback_lighting_phase(snapshot: WeatherSnapshot) -> LightingPhase:
    local_hour = snapshot.target_at.astimezone(ZoneInfo(snapshot.timezone)).hour
    if local_hour < 5 or local_hour >= 21:
        return "night"
    if local_hour < 7:
        return "pre_dawn"
    if local_hour >= 19:
        return "evening_twilight"
    if local_hour >= 17:
        return "late_afternoon"
    return "daylight"


def _classify_lighting_phase(snapshot: WeatherSnapshot) -> LightingPhase:
    target = snapshot.target_at
    sunrise = snapshot.sunrise
    sunset = snapshot.sunset
    twilight = timedelta(minutes=60)
    late_afternoon = timedelta(minutes=90)

    if sunrise is not None and sunset is not None:
        zone = ZoneInfo(snapshot.timezone)
        target_date = target.astimezone(zone).date()
        sunrise_date = sunrise.astimezone(zone).date()
        sunset_date = sunset.astimezone(zone).date()

        if sunrise < sunset and target_date == sunrise_date == sunset_date:
            if target == sunrise:
                return "daylight"
            if target == sunset:
                return "late_afternoon"
            if target < sunrise:
                return "pre_dawn" if sunrise - target <= twilight else "night"
            if target < sunset:
                return "late_afternoon" if sunset - target <= late_afternoon else "daylight"
            return "evening_twilight" if target - sunset <= twilight else "night"

        if sunset < sunrise and (sunrise_date - sunset_date).days == 1:
            if target == sunset:
                return "late_afternoon"
            if target == sunrise:
                return "daylight"
            if sunset < target < sunrise:
                since_sunset = target - sunset
                until_sunrise = sunrise - target
                if since_sunset <= twilight and since_sunset <= until_sunrise:
                    return "evening_twilight"
                if until_sunrise <= twilight:
                    return "pre_dawn"
                return "night"
            if target_date == sunset_date and target < sunset:
                if sunset - target <= late_afternoon:
                    return "late_afternoon"
                return _fallback_lighting_phase(snapshot)
            if target_date == sunrise_date and target > sunrise:
                fallback = _fallback_lighting_phase(snapshot)
                if target - sunrise < timedelta(hours=12) and fallback in {
                    "night",
                    "pre_dawn",
                }:
                    return "daylight"
                return fallback

        return _fallback_lighting_phase(snapshot)

    if sunrise is not None:
        zone = ZoneInfo(snapshot.timezone)
        if target.astimezone(zone).date() != sunrise.astimezone(zone).date():
            return _fallback_lighting_phase(snapshot)
        if target == sunrise:
            return "daylight"
        if target < sunrise:
            return "pre_dawn" if sunrise - target <= twilight else "night"
        fallback = _fallback_lighting_phase(snapshot)
        if target - sunrise < timedelta(hours=12) and fallback in {"night", "pre_dawn"}:
            return "daylight"
        return fallback

    if sunset is not None:
        if target > sunset:
            return (
                "evening_twilight"
                if target - sunset <= twilight
                else _fallback_lighting_phase(snapshot)
            )
        zone = ZoneInfo(snapshot.timezone)
        if target.astimezone(zone).date() != sunset.astimezone(zone).date():
            return _fallback_lighting_phase(snapshot)
        if sunset - target <= late_afternoon:
            return "late_afternoon"

    return _fallback_lighting_phase(snapshot)


def _lighting_contract(snapshot: WeatherSnapshot) -> tuple[LightingPhase, str, str]:
    phase = _classify_lighting_phase(snapshot)

    contracts: dict[LightingPhase, tuple[str, str]] = {
        "pre_dawn": (
            (
                "LIGHTING PHASE: pre-dawn twilight. The sun is below the horizon; the sky has only "
                "faint cool residual light. Scene-appropriate existing artificial sources remain "
                "visible where physically present. No direct sunlight or solar shadows. Keep shadow detail readable "
                "for a six-color display rather than making the frame nearly black."
            ),
            "daylight, daytime, visible sun, sunrise disk, blue daytime sky, direct sunlight, hard solar shadows",
        ),
        "daylight": (
            (
                "LIGHTING PHASE: daytime. Render illumination consistent with the stated cloud "
                "cover and weather, without substituting golden-hour or sunset light."
            ),
            "sunset lighting, implausible night lighting",
        ),
        "late_afternoon": (
            (
                "LIGHTING PHASE: late afternoon before sunset. The sun remains above the horizon, "
                "but its visibility and shadow strength must follow the stated cloud cover exactly."
            ),
            "nighttime, midnight sky, street lamps as the primary light source",
        ),
        "evening_twilight": (
            (
                "LIGHTING PHASE: evening twilight. The sun is below the horizon. Only fading diffuse "
                "twilight remains; scene-appropriate existing artificial sources are switched on where "
                "physically present. No direct sunlight or solar shadows. Keep "
                "buildings and people readable for a six-color display without turning the scene into "
                "daytime."
            ),
            "daylight, daytime, visible sun, blue daytime sky, direct sunlight, hard solar shadows, unlit street lamps",
        ),
        "night": (
            (
                "LIGHTING PHASE: full night. The sun is far below the horizon and there is no "
                "daylight. A dark sky, diffuse ambient night light and scene-appropriate existing "
                "artificial sources where physically present provide the only illumination. No direct sunlight or solar shadows. Preserve "
                "readable local detail and color separation for a six-color display without brightening "
                "the scene into daytime. Human activity must be sparse and plausible for the late hour; "
                "no daytime routines."
            ),
            "daylight, daytime, visible sun, sunrise, sunset glow, blue daytime sky, direct sunlight, hard solar shadows, unlit street lamps, daytime crowds",
        ),
    }
    positive, negative = contracts[phase]
    return phase, positive, negative


def _weather_visual_contract(snapshot: WeatherSnapshot) -> tuple[str, str]:
    fog_codes = {45, 48}
    rain_codes = {51, 53, 55, 61, 63, 65, 80, 81, 82}
    freezing_rain_codes = {56, 57, 66, 67}
    snow_codes = {71, 73, 75, 77, 85, 86}
    thunderstorm_codes = {95, 96, 99}
    code = snapshot.weather_code

    if code in fog_codes:
        if snapshot.visibility_m <= 10_000:
            cloud_positive = (
                "WEATHER APPEARANCE: visible fog with reduced local contrast, softened edges and "
                "distance obscured consistently with the reported visibility."
            )
        else:
            cloud_positive = (
                "WEATHER APPEARANCE: localized patches of fog with softened edges and reduced "
                "contrast inside the patches, while long-range visibility remains consistent with "
                "the reported value."
            )
        cloud_negative = "crystal-clear distance, crisp distant detail, harsh unfiltered sunlight"
    elif code in thunderstorm_codes:
        cloud_positive = (
            "WEATHER APPEARANCE: an active thunderstorm with convective storm clouds, darkened "
            "diffuse illumination and visibility consistent with the reported value."
        )
        cloud_negative = "calm fair weather, clear cloudless sky, visible sun"
    elif code in rain_codes | freezing_rain_codes | snow_codes:
        cloud_positive = (
            "WEATHER APPEARANCE: precipitation-bearing cloud cover appropriate to the categorical "
            "weather code; the categorical precipitation state takes visual precedence over any "
            "conflicting cloud percentage."
        )
        cloud_negative = "mostly clear sky, clear cloudless sky, visible sun"
    elif snapshot.cloud_cover_pct >= 90:
        cloud_positive = (
            "WEATHER APPEARANCE: fully overcast with an unbroken cloud layer, diffuse low-contrast "
            "illumination, no visible sun, no sunbeams and no hard-edged shadows."
        )
        cloud_negative = (
            "visible sun, sun disk, sunbeams, blue clear sky, hard-edged shadows, golden-hour rays"
        )
    elif snapshot.cloud_cover_pct >= 70:
        cloud_positive = (
            "WEATHER APPEARANCE: mostly cloudy, with broad cloud cover dominating the sky and only "
            "soft subdued illumination."
        )
        cloud_negative = "clear cloudless sky, harsh unfiltered sunlight"
    elif snapshot.cloud_cover_pct <= 20:
        cloud_positive = (
            "WEATHER APPEARANCE: mostly clear sky consistent with the stated solar phase."
        )
        cloud_negative = "fully overcast sky"
    else:
        cloud_positive = "WEATHER APPEARANCE: broken cloud cover with illumination matching the stated solar phase."
        cloud_negative = "weather inconsistent with the stated cloud cover"

    effects: list[str] = []
    negatives = [cloud_negative]
    if code in thunderstorm_codes:
        effects.append(
            "Convective rain and restrained wet-surface reflections make the thunderstorm visibly present."
        )
        negatives.extend(("calm fair weather", "dry pavement"))
    elif code in freezing_rain_codes:
        effects.append(
            "Freezing rain is visibly present with a restrained glaze on exposed surfaces and wet reflections."
        )
        negatives.extend(("falling snow", "dry pavement", "dry roofs"))
    elif code in snow_codes:
        effects.append(
            "Snow is visibly falling or drifting with plausible snow accumulation on suitable surfaces."
        )
        negatives.extend(("rain-only appearance", "dry bare surfaces"))
    elif code in rain_codes:
        effects.append(
            "Rain or drizzle is visibly present with naturally wet surfaces and restrained reflections."
        )
        negatives.extend(("falling snow", "dry pavement", "dry roofs"))
    elif snapshot.precipitation_mm > 0:
        effects.append(
            "Active precipitation is visibly present, with naturally wet surfaces and restrained reflections."
        )
        negatives.append("dry pavement, dry roofs")
    else:
        effects.append(
            "No active precipitation is visible and surfaces are not made wet without cause."
        )
        negatives.append("falling rain, falling snow, wet pavement without precipitation")
    if snapshot.visibility_m < 2_000 and code not in fog_codes:
        effects.append(
            "The reported low visibility visibly shortens atmospheric depth and softens distant contrast."
        )
        negatives.append("crystal-clear distant detail")
    if snapshot.wind_speed_kmh >= 20 or snapshot.wind_gust_kmh >= 35:
        effects.append(
            "The stated wind is visible through coherent movement of foliage, loose fabric and small branches."
        )
        negatives.append("perfectly still foliage")
    return " ".join((cloud_positive, *effects)), ", ".join(negatives)


def build_photorealistic_prompt(
    snapshot: WeatherSnapshot,
    *,
    location_name: str,
    scene: Scene,
) -> str:
    lighting_phase, lighting_positive, _ = _lighting_contract(snapshot)
    weather_positive, _ = _weather_visual_contract(snapshot)
    local_target = snapshot.target_at.astimezone(ZoneInfo(snapshot.timezone))
    solar_parts: list[str] = []
    if snapshot.sunrise is not None:
        sunrise_delta = int(abs((snapshot.target_at - snapshot.sunrise).total_seconds()))
        sunrise_relation = "after" if snapshot.target_at >= snapshot.sunrise else "before"
        solar_parts.append(f"{_duration_words(sunrise_delta)} {sunrise_relation} sunrise")
    if snapshot.sunset is not None:
        sunset_delta = int(abs((snapshot.target_at - snapshot.sunset).total_seconds()))
        sunset_relation = "after" if snapshot.target_at >= snapshot.sunset else "before"
        solar_parts.append(f"{_duration_words(sunset_delta)} {sunset_relation} sunset")
    solar = (
        " and ".join(solar_parts)
        if solar_parts
        else f"in the required {lighting_phase.replace('_', ' ')} lighting phase"
    )
    identity = (
        "A believable small southern Russian city based on an everyday local urban profile. "
        "The city fabric is heterogeneous, practical, maintained enough for active daily use and "
        "inhabited rather than a uniform preserved old town. Signs of age are restrained and local, "
        "not widespread decay or dereliction. "
        if scene.water_mode == "none"
        else f"A geographically restrained scene in {location_name}. "
    )
    dry_boundary = (
        "The selected enclosure leaves no distant horizon. " if scene.water_mode == "none" else ""
    )
    subject_description = (
        NIGHT_SCENE_DESCRIPTIONS.get(scene.scene_id, scene.description)
        if lighting_phase == "night"
        else scene.description
    )
    return (
        "One continuous horizontal 4:3 full-bleed photorealistic documentary photograph. "
        f"{lighting_positive} {weather_positive} "
        f"PRIMARY SUBJECT: {subject_description}. "
        f"SPATIAL COMPOSITION: {scene.spatial_guidance}. {dry_boundary}"
        f"LOCAL MATERIAL PROFILE: {scene.local_profile}. "
        f"REGIONAL IDENTITY: {identity}"
        f"CURRENT CONDITIONS: {local_target:%H:%M} local time, {solar}. "
        f"Temperature {snapshot.temperature_c:.1f} degrees Celsius, precipitation "
        f"probability {snapshot.precipitation_probability_pct} percent, "
        f"{snapshot.precipitation_mm:g} millimetres of precipitation, "
        f"{snapshot.cloud_cover_pct} percent cloud cover and visibility "
        f"{snapshot.visibility_m / 1000:.1f} kilometres. A {_wind_direction(snapshot.wind_direction_deg)} "
        f"wind at {snapshot.wind_speed_kmh:.1f} kilometres per hour with gusts to "
        f"{snapshot.wind_gust_kmh:.1f}. Physically accurate light, realistic materials, "
        "natural atmospheric depth and documentary detail. Do not copy a specific landmark or "
        "reference photograph. Fill every pixel edge to edge with the scene itself. No graphic "
        "layout or typography."
    )


def _select_scene(
    snapshot: WeatherSnapshot,
    *,
    location_name: str,
    recent_scene_ids: Sequence[str],
    rotation_index: int,
) -> Scene:
    selection_input = json.dumps(
        {
            "catalog_version": SCENE_CATALOG_VERSION,
            "location_name": location_name,
            "rotation_index": rotation_index,
            "target_at": snapshot.target_at.astimezone(ZoneInfo(snapshot.timezone)).isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(selection_input).digest()
    water_mode = SCENE_WATER_SLOTS[rotation_index % len(SCENE_WATER_SLOTS)]
    excluded = set(recent_scene_ids)
    lighting_phase, _, _ = _lighting_contract(snapshot)
    phase_candidates = [scene for scene in SCENE_CATALOG if scene.water_mode == water_mode]
    if water_mode == "none" and lighting_phase == "night":
        phase_candidates = [
            scene for scene in phase_candidates if scene.scene_id in NIGHT_COMPATIBLE_DRY_SCENE_IDS
        ]
    available = [scene for scene in phase_candidates if scene.scene_id not in excluded]
    if not available:
        available = phase_candidates
    return available[int.from_bytes(digest[:8], "big") % len(available)]


def build_prompt_snapshot(
    snapshot: WeatherSnapshot,
    *,
    location_name: str,
    recent_scene_ids: Sequence[str] = (),
    rotation_index: int = 0,
) -> PromptSnapshot:
    template_version = "photorealistic-weather-v4"
    visual_profile_version = "taganrog-scene-profiles-v2"
    scene = _select_scene(
        snapshot,
        location_name=location_name,
        recent_scene_ids=recent_scene_ids,
        rotation_index=rotation_index,
    )
    scene_id = scene.scene_id
    scene_family = scene.family
    lighting_phase, _, lighting_negative = _lighting_contract(snapshot)
    _, weather_negative = _weather_visual_contract(snapshot)
    final_prompt = build_photorealistic_prompt(
        snapshot,
        location_name=location_name,
        scene=scene,
    )
    base_negative = (
        "text, lettering, logo, watermark, border, product mockup, metropolis, skyscrapers, "
        "alpine mountains, polished Mediterranean resort, sterile tourist postcard, American streets"
    )
    dry_negative = (
        "sea, ocean, bay, river, harbor, port, marina, quay, pier, jetty, breakwater, beach, "
        "coastline, water horizon, ship, cargo ship, fishing boat, yacht, crane, dock, lighthouse, "
        "panoramic vista, elevated coastal viewpoint"
    )
    bay_negative = (
        "harbor cranes, cargo terminal, container port, marina, yachts, industrial pier, "
        "Mediterranean beach, cliffs, rocks, giant waves"
    )
    port_negative = (
        "Italian harbor, Mediterranean marina, yacht club, fishing village postcard, colorful "
        "residential facades directly on quay, luxury waterfront, tropical beach, container megaterminal"
    )
    mode_negative = {
        "none": dry_negative,
        "bay": bay_negative,
        "port": port_negative,
    }[scene.water_mode]
    maintenance_negative = (
        "derelict buildings, widespread peeling plaster, crumbling facades, abandoned street, "
        "ruin, poverty porn"
    )
    negative_prompt = (
        f"{mode_negative}, {lighting_negative}, {weather_negative}, {maintenance_negative}, "
        f"{base_negative}"
    )
    canonical = json.dumps(
        {
            "template_version": template_version,
            "visual_profile_version": visual_profile_version,
            "scene_id": scene_id,
            "scene_family": scene_family,
            "water_mode": scene.water_mode,
            "lighting_phase": lighting_phase,
            "location_name": location_name,
            "weather_payload_sha256": snapshot.raw_payload_sha256,
            "final_prompt": final_prompt,
            "negative_prompt": negative_prompt,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return PromptSnapshot(
        template_version=template_version,
        visual_profile_version=visual_profile_version,
        scene_id=scene_id,
        scene_family=scene_family,
        water_mode=scene.water_mode,
        lighting_phase=lighting_phase,
        location_name=location_name,
        weather_payload_sha256=snapshot.raw_payload_sha256,
        final_prompt=final_prompt,
        negative_prompt=negative_prompt,
        content_sha256=hashlib.sha256(canonical).hexdigest(),
    )
