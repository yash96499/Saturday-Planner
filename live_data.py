"""Keyless live place discovery using OpenStreetMap public services."""

from functools import lru_cache

import requests


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "PerfectSaturdayPlanner/1.0 (portfolio assignment)"
SEARCH_RADIUS_METERS = 5000

INTEREST_TAGS = {
    "art": [("tourism", "gallery"), ("amenity", "arts_centre")],
    "books": [("shop", "books"), ("amenity", "library")],
    "coffee": [("amenity", "cafe")],
    "culture": [("tourism", "museum"), ("historic", "*")],
    "fitness": [("leisure", "fitness_centre"), ("leisure", "sports_centre")],
    "food": [("amenity", "marketplace")],
    "games": [("leisure", "amusement_arcade"), ("leisure", "bowling_alley")],
    "music": [("amenity", "music_venue"), ("amenity", "theatre")],
    "nature": [("leisure", "park"), ("leisure", "garden")],
    "nightlife": [("amenity", "music_venue"), ("amenity", "pub")],
    "photography": [("tourism", "viewpoint"), ("tourism", "attraction")],
    "shopping": [("shop", "mall"), ("amenity", "marketplace")],
    "walks": [("leisure", "park"), ("leisure", "garden")],
}

TYPE_DETAILS = {
    "park": ("walk", 0, 1.5, "nature"),
    "garden": ("walk", 0, 1.5, "nature"),
    "museum": ("culture", 200, 2.0, "culture"),
    "gallery": ("art", 150, 1.5, "art"),
    "arts_centre": ("art", 300, 2.0, "art"),
    "library": ("indoor", 0, 1.5, "books"),
    "books": ("shopping", 300, 1.0, "books"),
    "theatre": ("culture", 500, 2.5, "culture"),
    "music_venue": ("music", 500, 2.0, "music"),
    "viewpoint": ("outdoor", 0, 1.0, "photography"),
    "attraction": ("culture", 200, 1.5, "culture"),
    "fitness_centre": ("fitness", 400, 1.5, "fitness"),
    "sports_centre": ("fitness", 300, 1.5, "fitness"),
    "amusement_arcade": ("games", 500, 2.0, "games"),
    "bowling_alley": ("games", 600, 2.0, "games"),
    "mall": ("shopping", 300, 2.0, "shopping"),
    "marketplace": ("shopping", 300, 1.5, "shopping"),
    "pub": ("nightlife", 700, 2.0, "nightlife"),
    "cafe": ("cafe", 350, 1.0, "coffee"),
}


def fetch_activities(city: str, interests: list) -> list:
    """Return normalized activity candidates from OpenStreetMap."""
    coordinates = geocode_city(city)
    requested_tags = _tags_for_interests(interests)
    elements = _overpass_elements(coordinates, requested_tags)

    activities = []
    for element in elements:
        tags = element.get("tags", {})
        name = tags.get("name")
        place_kind = _place_kind(tags)
        if not name or not place_kind or place_kind not in TYPE_DETAILS:
            continue

        activity_type, cost, duration, primary_interest = TYPE_DETAILS[place_kind]
        activities.append({
            "name": name,
            "type": activity_type,
            "cost": cost,
            "duration_hours": duration,
            "mood_tags": _moods_for_type(activity_type),
            "interest_tags": list(dict.fromkeys([primary_interest, activity_type])),
            "crowd_level": _crowd_level(tags, place_kind),
            "time_of_day": "morning" if activity_type in ("walk", "fitness") else "afternoon",
            "description": _activity_description(name, place_kind, tags),
            "latitude": element.get("lat") or element.get("center", {}).get("lat"),
            "longitude": element.get("lon") or element.get("center", {}).get("lon"),
            "_data_source": "OpenStreetMap",
        })

    return _deduplicate(activities)[:20]


def fetch_food_spots(city: str) -> list:
    """Return normalized restaurant and cafe candidates from OpenStreetMap."""
    coordinates = geocode_city(city)
    food_tags = [("amenity", "restaurant"), ("amenity", "cafe"), ("amenity", "fast_food")]
    elements = _overpass_elements(coordinates, food_tags)

    spots = []
    for element in elements:
        tags = element.get("tags", {})
        name = tags.get("name")
        amenity = tags.get("amenity")
        if not name or amenity not in {"restaurant", "cafe", "fast_food"}:
            continue

        cuisine = tags.get("cuisine", "local").replace(";", ", ").replace("_", " ")
        vegetarian = tags.get("diet:vegetarian") in {"yes", "only"}
        dietary = ["vegetarian"] if vegetarian else []
        if tags.get("diet:vegan") in {"yes", "only"}:
            dietary.extend(["vegan", "vegetarian"])

        spots.append({
            "name": name,
            "cuisine": cuisine.title(),
            "cost_per_person": _food_cost(amenity, tags),
            "dietary": list(dict.fromkeys(dietary)),
            "crowd_level": "medium",
            "rating": 3.5,
            "description": _food_description(name, amenity, cuisine, tags),
            "latitude": element.get("lat") or element.get("center", {}).get("lat"),
            "longitude": element.get("lon") or element.get("center", {}).get("lon"),
            "_data_source": "OpenStreetMap",
        })

    return _deduplicate(spots)[:30]


@lru_cache(maxsize=64)
def geocode_city(city: str) -> tuple:
    """Resolve a city name to coordinates through Nominatim."""
    response = requests.get(
        NOMINATIM_URL,
        params={"q": city, "format": "jsonv2", "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=8,
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        raise ValueError(f"Could not locate {city}")
    return float(results[0]["lat"]), float(results[0]["lon"])


@lru_cache(maxsize=128)
def _cached_overpass(lat: float, lon: float, tag_tuple: tuple) -> tuple:
    filters = []
    for key, value in tag_tuple:
        condition = f'["{key}"]' if value == "*" else f'["{key}"="{value}"]'
        for osm_type in ("node", "way", "relation"):
            filters.append(f"{osm_type}{condition}(around:{SEARCH_RADIUS_METERS},{lat},{lon});")

    query = "[out:json][timeout:25];(" + "".join(filters) + ");out center tags;"
    response = requests.post(
        OVERPASS_URL,
        data={"data": query},
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    response.raise_for_status()
    return tuple(response.json().get("elements", []))


def _overpass_elements(coordinates: tuple, tags: list) -> list:
    lat, lon = coordinates
    return list(_cached_overpass(round(lat, 5), round(lon, 5), tuple(tags)))


def _tags_for_interests(interests: list) -> list:
    selected = []
    for interest in interests:
        selected.extend(INTEREST_TAGS.get(interest.lower(), []))
    if not selected:
        selected = INTEREST_TAGS["walks"] + INTEREST_TAGS["culture"] + INTEREST_TAGS["coffee"]
    return list(dict.fromkeys(selected))[:8]


def _place_kind(tags: dict):
    for key in ("leisure", "tourism", "amenity", "shop", "historic"):
        value = tags.get(key)
        if value:
            return "attraction" if key == "historic" else value
    return None


def _moods_for_type(activity_type: str) -> list:
    if activity_type in {"walk", "cafe", "indoor", "art", "culture"}:
        return ["relaxed", "tired", "peaceful", "curious"]
    if activity_type in {"music", "nightlife", "games"}:
        return ["social", "energetic", "fun"]
    return ["energetic", "adventurous", "curious"]


def _crowd_level(tags: dict, place_kind: str) -> str:
    if tags.get("access") == "private":
        return "high"
    if place_kind in {"park", "garden", "library", "viewpoint", "gallery"}:
        return "low"
    if place_kind in {"mall", "marketplace", "pub", "music_venue"}:
        return "high"
    return "medium"


def _activity_description(name: str, place_kind: str, tags: dict) -> str:
    opening = tags.get("opening_hours")
    detail = f"A real {place_kind.replace('_', ' ')} listed on OpenStreetMap."
    if opening:
        detail += f" Listed hours: {opening}."
    return f"{name}: {detail} Verify current timings before leaving."


def _food_cost(amenity: str, tags: dict) -> int:
    if tags.get("charge"):
        return 500
    return {"cafe": 350, "fast_food": 300, "restaurant": 600}.get(amenity, 500)


def _food_description(name: str, amenity: str, cuisine: str, tags: dict) -> str:
    detail = f"A real {amenity.replace('_', ' ')} listed on OpenStreetMap serving {cuisine}."
    if tags.get("opening_hours"):
        detail += f" Listed hours: {tags['opening_hours']}."
    return f"{name}: {detail} Prices and availability are estimates; verify before visiting."


def _deduplicate(items: list) -> list:
    seen = set()
    unique = []
    for item in items:
        key = item["name"].strip().lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique
