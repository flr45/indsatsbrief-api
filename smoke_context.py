"""Wind-sector context screening for IndsatsBrief.

This module intentionally does not claim that an OSM object is inside a toxic
plume. It finds selected vulnerable/institutional places around the incident,
then filters them by a user-selected downwind sector. The result is decision
support only and inherits OpenStreetMap completeness limitations.
"""

from copy import deepcopy
import math
import threading
import time

import requests
from flask import jsonify, request


OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
CACHE_TTL_SECONDS = 900
_CACHE = {}
_CACHE_LOCK = threading.RLock()


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_bearing(value):
    return (float(value) % 360.0 + 360.0) % 360.0


def angular_difference(a, b):
    diff = abs(normalize_bearing(a) - normalize_bearing(b))
    return min(diff, 360.0 - diff)


def haversine_m(lat1, lon1, lat2, lon2):
    radius = 6371008.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def initial_bearing(lat1, lon1, lat2, lon2):
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = (
        math.cos(phi1) * math.sin(phi2)
        - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    )
    return normalize_bearing(math.degrees(math.atan2(y, x)))


def classify_place(tags):
    tags = tags if isinstance(tags, dict) else {}
    amenity = str(tags.get("amenity") or "").lower()
    healthcare = str(tags.get("healthcare") or "").lower()
    social = str(tags.get("social_facility") or "").lower()

    if amenity == "hospital" or healthcare == "hospital":
        return "hospital", "Hospital"
    if amenity == "clinic" or healthcare in {"clinic", "doctor"}:
        return "healthcare", "Klinik/sundhed"
    if amenity in {"kindergarten", "childcare"}:
        return "childcare", "Daginstitution"
    if amenity == "school":
        return "school", "Skole"
    if amenity in {"college", "university"}:
        return "education", "Uddannelsesinstitution"
    if amenity == "nursing_home" or social in {
        "nursing_home",
        "assisted_living",
        "group_home",
    }:
        return "care", "Pleje-/botilbud"
    if amenity == "social_facility" or social:
        return "social", "Social institution"
    if healthcare in {"hospice", "rehabilitation"}:
        return "care", "Pleje-/behandlingstilbud"
    if amenity == "prison":
        return "institution", "Institution"
    return "other", "Andet sårbart sted"


def _element_coordinates(element):
    if not isinstance(element, dict):
        return None
    lat = _number(element.get("lat"))
    lon = _number(element.get("lon"))
    if lat is not None and lon is not None:
        return lat, lon
    center = element.get("center")
    if isinstance(center, dict):
        lat = _number(center.get("lat"))
        lon = _number(center.get("lon"))
        if lat is not None and lon is not None:
            return lat, lon
    return None


def _overpass_query(lat, lon, radius_m):
    radius = int(radius_m)
    return f"""
[out:json][timeout:8];
(
  nwr(around:{radius},{lat:.7f},{lon:.7f})[\"amenity\"~\"^(school|kindergarten|childcare|hospital|clinic|college|university|nursing_home|social_facility|prison)$\"];
  nwr(around:{radius},{lat:.7f},{lon:.7f})[\"healthcare\"~\"^(hospital|clinic|doctor|hospice|rehabilitation)$\"];
  nwr(around:{radius},{lat:.7f},{lon:.7f})[\"social_facility\"~\"^(nursing_home|assisted_living|group_home|day_care|shelter)$\"];
);
out center tags;
""".strip()


def _normalize_elements(payload, origin_lat, origin_lon):
    elements = payload.get("elements") if isinstance(payload, dict) else None
    elements = elements if isinstance(elements, list) else []
    places = []
    seen = set()

    for element in elements:
        if not isinstance(element, dict):
            continue
        key = (element.get("type"), element.get("id"))
        if key in seen:
            continue
        seen.add(key)
        coordinates = _element_coordinates(element)
        if not coordinates:
            continue
        lat, lon = coordinates
        tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}
        category, category_label = classify_place(tags)
        name = str(tags.get("name") or tags.get("official_name") or category_label).strip()
        distance = haversine_m(origin_lat, origin_lon, lat, lon)
        bearing = initial_bearing(origin_lat, origin_lon, lat, lon)
        places.append(
            {
                "osm_type": element.get("type"),
                "osm_id": element.get("id"),
                "name": name,
                "category": category,
                "category_label": category_label,
                "latitude": round(lat, 7),
                "longitude": round(lon, 7),
                "distance_m": round(distance),
                "bearing_deg": round(bearing, 1),
            }
        )

    places.sort(key=lambda item: item["distance_m"])
    return places


def fetch_nearby_places(lat, lon, radius_m):
    """Fetch and cache nearby selected OSM places independently of wind direction."""
    radius_m = int(_clamp(int(radius_m), 1000, 15000))
    cache_key = (round(float(lat), 4), round(float(lon), 4), radius_m)
    now = time.monotonic()

    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and now - cached["stored_at"] < CACHE_TTL_SECONDS:
            result = deepcopy(cached["result"])
            result["cache"] = "hit"
            return result
        if cached:
            _CACHE.pop(cache_key, None)

    query = _overpass_query(float(lat), float(lon), radius_m)
    attempts = []
    started = time.monotonic()
    for url in OVERPASS_URLS:
        if time.monotonic() - started > 9:
            break
        try:
            response = requests.get(
                url,
                params={"data": query},
                headers={
                    "User-Agent": "IndsatsBrief/1.4 smoke-context",
                    "Accept": "application/json",
                },
                timeout=(2, 6),
            )
            attempts.append({"url": url, "status_code": response.status_code})
            response.raise_for_status()
            payload = response.json()
            places = _normalize_elements(payload, float(lat), float(lon))
            result = {
                "ok": True,
                "source": "OpenStreetMap via Overpass API",
                "working_overpass_url": url,
                "radius_m": radius_m,
                "places": places,
                "nearby_count": len(places),
                "cache": "miss",
            }
            with _CACHE_LOCK:
                while len(_CACHE) >= 128:
                    oldest = min(_CACHE, key=lambda item: _CACHE[item]["stored_at"])
                    _CACHE.pop(oldest, None)
                _CACHE[cache_key] = {"stored_at": now, "result": deepcopy(result)}
            return result
        except (requests.RequestException, ValueError) as error:
            if attempts:
                attempts[-1]["error"] = str(error)

    return {
        "ok": False,
        "source": "OpenStreetMap via Overpass API",
        "radius_m": radius_m,
        "places": [],
        "nearby_count": 0,
        "attempts": attempts,
        "error": "OSM-kontekst kunne ikke hentes inden for timeout.",
        "cache": "miss",
    }


def sector_places(places, direction_to, half_angle):
    center = normalize_bearing(direction_to)
    half_angle = float(_clamp(float(half_angle), 10, 90))
    selected = []
    for place in places or []:
        bearing = _number(place.get("bearing_deg")) if isinstance(place, dict) else None
        if bearing is None:
            continue
        offset = angular_difference(bearing, center)
        if offset <= half_angle:
            item = deepcopy(place)
            item["sector_offset_deg"] = round(offset, 1)
            selected.append(item)
    selected.sort(key=lambda item: (item["distance_m"], item["sector_offset_deg"]))
    return selected


def build_context(lat, lon, direction_to, radius_m=5000, half_angle=45):
    nearby = fetch_nearby_places(lat, lon, radius_m)
    if not nearby.get("ok"):
        return nearby

    selected = sector_places(nearby.get("places"), direction_to, half_angle)
    categories = {}
    for place in selected:
        key = place.get("category_label") or "Andet"
        categories[key] = categories.get(key, 0) + 1

    return {
        "ok": True,
        "source": nearby.get("source"),
        "cache": nearby.get("cache"),
        "radius_m": nearby.get("radius_m"),
        "direction_to_deg": round(normalize_bearing(direction_to), 1),
        "half_angle_deg": round(float(half_angle), 1),
        "nearby_count": nearby.get("nearby_count", 0),
        "sector_count": len(selected),
        "categories": categories,
        "places": selected[:25],
        "note": (
            "Vindsektor-screening baseret på OpenStreetMap. Et fund i sektoren er ikke "
            "dokumentation for røgpåvirkning; OSM kan være ufuldstændigt."
        ),
    }


def register_routes(app, legacy):
    endpoint = "ib_smoke_context_api"
    if endpoint in app.view_functions:
        return

    @app.get("/api/smoke-context", endpoint=endpoint)
    @legacy.login_required(api=True)
    def smoke_context_api():
        lat = _number(request.args.get("lat"))
        lon = _number(request.args.get("lon"))
        direction = _number(request.args.get("direction"))
        radius = _number(request.args.get("radius_m")) or 5000
        half_angle = _number(request.args.get("half_angle")) or 45

        if lat is None or lon is None or direction is None:
            return jsonify(
                {
                    "ok": False,
                    "error": "lat, lon og direction er påkrævet.",
                }
            ), 400
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return jsonify({"ok": False, "error": "Ugyldige koordinater."}), 400

        result = build_context(
            lat,
            lon,
            direction,
            radius_m=int(_clamp(radius, 1000, 15000)),
            half_angle=_clamp(half_angle, 10, 90),
        )
        return jsonify(result), (200 if result.get("ok") else 503)
