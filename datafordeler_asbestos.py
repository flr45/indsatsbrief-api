"""Targeted asbestos fallback against the modern Datafordeler BBR GraphQL API.

DanskAdresseAPI remains the primary provider for IndsatsBrief. This module is
only used when the normal enrichment did not return an unambiguous asbestos
registration. It queries the BBR Bygning entity by DAR husnummer id and reads
the official ``byg036AsbestholdigtMateriale`` field.

BBR's asbestos field is not a boolean:
  1 = asbestos-containing exterior wall material
  2 = asbestos-containing roof material
  3 = asbestos-containing exterior wall and roof material
Blank/not returned means the register did not return a positive registration.
Legacy adapters may also expose 0/9, which are treated conservatively as
negative/unknown only when explicitly returned.
"""

from copy import deepcopy
from datetime import datetime, timezone
import os
import threading
import time

import requests


DEFAULT_GRAPHQL_URL = "https://graphql.datafordeler.dk/BBR/v1"
_CACHE = {}
_CACHE_LOCK = threading.RLock()

_POSITIVE_CODES = {
    "1": "Asbestholdigt ydervægsmateriale",
    "2": "Asbestholdigt tagdækningsmateriale",
    "3": "Asbestholdigt ydervægs- og tagdækningsmateriale",
}
_EXISTING_KEYS = (
    "byg036AsbestholdigtMateriale",
    "asbestholdigt_materiale",
    "asbestholdigt_materiale_kode",
    "asbestos_material",
    "asbestos_material_code",
)


def _api_key():
    return (os.getenv("DATAFORDELER_API_KEY") or "").strip()


def configured():
    return bool(_api_key())


def _graphql_url():
    return (os.getenv("DATAFORDELER_BBR_GRAPHQL_URL") or DEFAULT_GRAPHQL_URL).strip()


def _ttl_seconds():
    try:
        return max(0, min(604800, int(os.getenv("DATAFORDELER_ASBEST_CACHE_TTL_SECONDS", "86400"))))
    except (TypeError, ValueError):
        return 86400


def _code(value):
    if value in (None, ""):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def asbestos_status(value):
    """Return ``yes``/``no``/``unknown``/``not_returned`` for a BBR value."""
    code = _code(value)
    if code is None:
        return "not_returned"
    if code in _POSITIVE_CODES:
        return "yes"
    if code in {"0", "false", "nej", "no"}:
        return "no"
    if code in {"9", "unknown", "ukendt"}:
        return "unknown"
    return "unknown"


def asbestos_text(value):
    code = _code(value)
    return _POSITIVE_CODES.get(code)


def _first_existing_code(building):
    if not isinstance(building, dict):
        return None
    raw = building.get("raw_bbr_building")
    sources = [raw, building] if isinstance(raw, dict) else [building]
    for source in sources:
        for key in _EXISTING_KEYS:
            if key in source and source.get(key) not in (None, ""):
                return source.get(key)
    return None


def _query_candidates(access_address_id):
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    fields = """
          id_lokalId
          status
          husnummer
          byg007Bygningsnummer
          byg021BygningensAnvendelse
          byg032YdervaeggensMateriale
          byg033Tagdaekningsmateriale
          byg036AsbestholdigtMateriale
          byg037KildeTilBygningensMaterialer
    """
    return [
        {
            "name": "point_in_time",
            "query": f"""
              query($tid: DafDateTime!, $husnummerId: String!) {{
                BBR_Bygning(
                  first: 20,
                  virkningstid: $tid,
                  registreringstid: $tid,
                  where: {{ husnummer: {{ eq: $husnummerId }} }}
                ) {{
                  nodes {{ {fields} }}
                }}
              }}
            """,
            "variables": {"tid": now, "husnummerId": access_address_id},
        },
        {
            "name": "current_without_time",
            "query": f"""
              query($husnummerId: String!) {{
                BBR_Bygning(
                  first: 20,
                  where: {{ husnummer: {{ eq: $husnummerId }} }}
                ) {{
                  nodes {{ {fields} }}
                }}
              }}
            """,
            "variables": {"husnummerId": access_address_id},
        },
    ]


def _post_query(candidate):
    response = requests.post(
        _graphql_url(),
        params={"apiKey": _api_key()},
        json={"query": candidate["query"], "variables": candidate["variables"]},
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "IndsatsBrief/1.3 asbestos-fallback",
        },
        timeout=(3, 10),
    )
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return response, payload


def _nodes_from_payload(payload):
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    entity = data.get("BBR_Bygning")
    if not isinstance(entity, dict):
        return []
    nodes = entity.get("nodes")
    return [item for item in (nodes or []) if isinstance(item, dict)]


def fetch(access_address_id):
    """Fetch current BBR buildings for one DAR husnummer id."""
    if not configured():
        return {"ok": False, "error": "DATAFORDELER_API_KEY mangler", "nodes": [], "source": "disabled"}
    if not access_address_id:
        return {"ok": False, "error": "Mangler access_address_id", "nodes": [], "source": "disabled"}

    cache_key = str(access_address_id)
    ttl = _ttl_seconds()
    now_monotonic = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and ttl and now_monotonic - cached["stored_at"] < ttl:
            result = deepcopy(cached["result"])
            result["cache"] = "hit"
            return result
        if cached:
            _CACHE.pop(cache_key, None)

    attempts = []
    result = None
    for candidate in _query_candidates(cache_key):
        try:
            response, payload = _post_query(candidate)
        except requests.RequestException as error:
            attempts.append({"name": candidate["name"], "error": str(error)})
            continue

        nodes = _nodes_from_payload(payload)
        errors = payload.get("errors") if isinstance(payload, dict) else None
        attempts.append(
            {
                "name": candidate["name"],
                "status_code": response.status_code,
                "errors": errors,
                "nodes": len(nodes),
            }
        )
        if response.ok and nodes:
            current_nodes = [item for item in nodes if str(item.get("status")) == "6"]
            result = {
                "ok": True,
                "nodes": current_nodes or nodes,
                "error": None,
                "source": "Datafordeler BBR GraphQL v1",
                "candidate": candidate["name"],
                "cache": "miss",
                "attempts": attempts,
            }
            break
        if response.ok and isinstance(payload, dict) and not errors:
            result = {
                "ok": True,
                "nodes": [],
                "error": None,
                "source": "Datafordeler BBR GraphQL v1",
                "candidate": candidate["name"],
                "cache": "miss",
                "attempts": attempts,
            }
            break

    if result is None:
        result = {
            "ok": False,
            "nodes": [],
            "error": "Datafordeler GraphQL kunne ikke hente BBR-bygninger",
            "source": "Datafordeler BBR GraphQL v1",
            "cache": "miss",
            "attempts": attempts,
        }

    if result.get("ok") and ttl:
        with _CACHE_LOCK:
            while len(_CACHE) >= 512:
                oldest = min(_CACHE, key=lambda key: _CACHE[key]["stored_at"])
                _CACHE.pop(oldest, None)
            _CACHE[cache_key] = {"stored_at": now_monotonic, "result": deepcopy(result)}
    return result


def _positive_entries(nodes):
    positives = []
    for node in nodes or []:
        code = node.get("byg036AsbestholdigtMateriale")
        if asbestos_status(code) != "yes":
            continue
        positives.append(
            {
                "building_number": node.get("byg007Bygningsnummer"),
                "building_type": node.get("byg021BygningensAnvendelse"),
                "code": _code(code),
                "location_text": asbestos_text(code),
                "bbr_id": node.get("id_lokalId"),
            }
        )
    return positives


def _apply_positive(building, positives, source, cache=None):
    if not positives:
        return building
    first = positives[0]
    building["asbestos_material"] = first.get("code")
    building["asbestos_material_text"] = first.get("location_text")
    building["asbestos_fallback"] = {
        "status": "yes",
        "source": source,
        "code": first.get("code"),
        "location_text": first.get("location_text"),
        "positive_buildings": positives,
        "cache": cache,
    }
    building["asbestos_check"] = {
        "status": "yes",
        "coverage": "datafordeler_graphql",
        "buildings_checked": max(1, len(positives)),
        "positive_buildings": [
            {
                "building_number": item.get("building_number"),
                "building_type": item.get("building_type"),
            }
            for item in positives
        ],
        "material_indicators": [],
        "checks": [],
        "source_status": "ok",
        "source": source,
        "cache": cache,
    }
    return building


def enrich_if_needed(building, access_address_id=None):
    """Preserve positive data and use Datafordeler only when asbestos is unclear."""
    if not isinstance(building, dict):
        return building

    existing_code = _first_existing_code(building)
    if asbestos_status(existing_code) == "yes":
        positive = {
            "building_number": building.get("bbr_id"),
            "building_type": building.get("building_type_text") or building.get("usage_text"),
            "code": _code(existing_code),
            "location_text": asbestos_text(existing_code),
            "bbr_id": building.get("id_lokalId"),
        }
        return _apply_positive(building, [positive], "Primært BBR-svar")

    check = building.get("asbestos_check")
    if isinstance(check, dict) and check.get("status") == "yes":
        return building

    address_id = access_address_id or building.get("access_address_id")
    result = fetch(address_id)
    building["asbestos_fallback"] = {
        "status": "checked" if result.get("ok") else "unavailable",
        "source": result.get("source"),
        "error": result.get("error"),
        "cache": result.get("cache"),
        "attempts": result.get("attempts"),
    }
    positives = _positive_entries(result.get("nodes") or [])
    if positives:
        return _apply_positive(
            building,
            positives,
            result.get("source") or "Datafordeler BBR GraphQL v1",
            result.get("cache"),
        )
    return building
