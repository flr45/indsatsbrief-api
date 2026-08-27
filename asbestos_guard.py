"""Property-wide BBR asbestos check for IndsatsBrief.

The normal address enrichment exposes one primary BBR building. For operational
use we also query the lightweight BBR building list for the access address so
asbestos registered on another building at the same address is not silently
missed. Results are cached per worker for 24 hours by default.
"""

from copy import deepcopy
import os
import threading
import time

import requests


API_BASE = os.getenv("DANSKADRESSE_API_BASE", "https://api.danskadresseapi.dk/v1").rstrip("/")
_CACHE = {}
_CACHE_LOCK = threading.RLock()

_ASBEST_KEYS = (
    "asbestholdigt_materiale",
    "asbestholdigt_materiale_kode",
    "asbestos_material",
    "asbestos_material_code",
    "byg036AsbestholdigtMateriale",
)
_ASBEST_TEXT_KEYS = (
    "asbestholdigt_materiale_tekst",
    "asbestos_material_text",
)
_MATERIAL_SOURCE_KEYS = (
    "kilde_til_bygningens_materialer",
    "materialer_kilde_tekst",
    "material_source_text",
)


def _env_int(name, default, minimum=0, maximum=None):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_bool(name, default=True):
    value = os.getenv(name)
    if value is None:
        return bool(default)
    return value.strip().lower() in {"1", "true", "yes", "ja", "on"}


def _api_key():
    return (
        os.getenv("DANSKADRESSE_API_KEY")
        or os.getenv("INDSATSBRIEF_DANSKADRESSE_API_KEY")
        or ""
    ).strip()


def _first_present(source, keys):
    if not isinstance(source, dict):
        return False, None, None
    for key in keys:
        if key in source:
            return True, source.get(key), key
    return False, None, None


def _status_from_value(value, text_value=None):
    if text_value not in (None, ""):
        text = str(text_value).strip().lower()
        if text in {"ja", "yes", "true"}:
            return "yes"
        if text in {"nej", "no", "false"}:
            return "no"
        if text in {"ukendt", "unknown"}:
            return "unknown"

    if value is True:
        return "yes"
    if value is False:
        return "no"
    if value in (None, ""):
        return "not_returned"

    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "ja", "y"}:
        return "yes"
    if text in {"0", "false", "no", "nej", "n"}:
        return "no"
    if text in {"9", "unknown", "ukendt"}:
        return "unknown"
    if "ikke verificeret" in text or "ikke oplyst" in text:
        return "not_returned"
    return "unknown"


def _material_indicators(building):
    if not isinstance(building, dict):
        return []
    indicators = []
    fields = (
        ("Facade", building.get("outer_wall_material_text")),
        ("Tag", building.get("roof_material_text")),
        ("Facade", building.get("ydervaegs_materiale_tekst")),
        ("Tag", building.get("tagdaeknings_materiale_tekst")),
    )
    for label, value in fields:
        if value in (None, ""):
            continue
        lowered = str(value).strip().lower()
        if "eternit" in lowered or "asbest" in lowered:
            entry = f"{label}: {str(value).strip()}"
            if entry not in indicators:
                indicators.append(entry)
    return indicators


def inspect_building(building):
    """Inspect one normalized or raw BBR building without inferring asbestos."""
    if not isinstance(building, dict):
        return {
            "status": "not_returned",
            "field_present": False,
            "material_indicators": [],
        }

    raw = building.get("raw_bbr_building")
    raw = raw if isinstance(raw, dict) else building

    present, value, field = _first_present(raw, _ASBEST_KEYS)
    text_present, text_value, text_field = _first_present(raw, _ASBEST_TEXT_KEYS)

    if not present:
        present, value, field = _first_present(building, _ASBEST_KEYS)
    if not text_present:
        text_present, text_value, text_field = _first_present(building, _ASBEST_TEXT_KEYS)

    material_source = None
    for source in (raw, building):
        found, candidate, _ = _first_present(source, _MATERIAL_SOURCE_KEYS)
        if found and candidate not in (None, ""):
            material_source = str(candidate).strip()
            break

    status = _status_from_value(value, text_value)
    field_present = bool(present or text_present)
    if not field_present:
        status = "not_returned"

    return {
        "status": status,
        "field_present": field_present,
        "raw_value": value,
        "field": field or text_field,
        "material_source": material_source,
        "building_number": building.get("bygningsnummer") or building.get("bbr_id"),
        "building_type": (
            building.get("anvendelse_tekst")
            or building.get("building_type_text")
            or building.get("usage_text")
        ),
        "material_indicators": _material_indicators(building),
    }


def _unwrap_building_list(payload):
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "results", "items", "bygninger"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def fetch_all_buildings(access_address_id):
    """Fetch all BBR buildings registered on an access address."""
    if not _env_bool("DANSKADRESSE_ASBEST_ALL_BUILDINGS", True):
        return {"ok": False, "disabled": True, "buildings": [], "error": "disabled"}

    key = _api_key()
    if not key or not access_address_id:
        return {
            "ok": False,
            "disabled": False,
            "buildings": [],
            "error": "missing_key_or_access_address_id",
        }

    ttl = _env_int("DANSKADRESSE_ASBEST_CACHE_TTL_SECONDS", 86400, maximum=604800)
    cache_key = str(access_address_id)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and ttl and now - cached["stored_at"] < ttl:
            result = deepcopy(cached["result"])
            result["cache"] = "hit"
            return result
        if cached:
            _CACHE.pop(cache_key, None)

    try:
        response = requests.get(
            f"{API_BASE}/bbr/bygninger",
            params={"adgangsadresseId": access_address_id, "limit": 20},
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
                "User-Agent": "IndsatsBrief/1.2 asbestos-check",
            },
            timeout=(3, 10),
        )
        payload = response.json() if response.content else None
    except (requests.RequestException, ValueError) as error:
        return {
            "ok": False,
            "disabled": False,
            "buildings": [],
            "error": str(error),
            "cache": "miss",
        }

    buildings = _unwrap_building_list(payload)
    result = {
        "ok": bool(response.ok),
        "disabled": False,
        "status_code": response.status_code,
        "buildings": buildings,
        "error": None if response.ok else f"DanskAdresseAPI HTTP {response.status_code}",
        "cache": "miss",
    }
    if result["ok"] and ttl:
        with _CACHE_LOCK:
            while len(_CACHE) >= 512:
                oldest = min(_CACHE, key=lambda item: _CACHE[item]["stored_at"])
                _CACHE.pop(oldest, None)
            _CACHE[cache_key] = {"stored_at": now, "result": deepcopy(result)}
    return result


def combine_checks(checks, coverage="main_building"):
    checks = [check for check in checks if isinstance(check, dict)]
    statuses = [check.get("status") for check in checks]
    if "yes" in statuses:
        status = "yes"
    elif "unknown" in statuses:
        status = "unknown"
    elif statuses and all(item == "no" for item in statuses):
        status = "no"
    elif "no" in statuses and all(item in {"no", "not_returned"} for item in statuses):
        status = "partial_no"
    else:
        status = "not_returned"

    indicators = []
    positive_buildings = []
    for check in checks:
        for indicator in check.get("material_indicators") or []:
            if indicator not in indicators:
                indicators.append(indicator)
        if check.get("status") == "yes":
            positive_buildings.append(
                {
                    "building_number": check.get("building_number"),
                    "building_type": check.get("building_type"),
                }
            )

    return {
        "status": status,
        "coverage": coverage,
        "buildings_checked": len(checks),
        "positive_buildings": positive_buildings,
        "material_indicators": indicators,
        "checks": checks,
    }


def enrich_building(building, access_address_id=None):
    """Attach a property-wide asbestos_check object to normalized building data."""
    if not isinstance(building, dict):
        return building

    main_check = inspect_building(building)
    all_result = fetch_all_buildings(access_address_id or building.get("access_address_id"))

    if all_result.get("ok") and all_result.get("buildings"):
        checks = [inspect_building(item) for item in all_result["buildings"]]
        combined = combine_checks(checks, coverage="all_registered_buildings")
        combined["source_status"] = "ok"
        combined["cache"] = all_result.get("cache")
    else:
        combined = combine_checks([main_check], coverage="main_building_only")
        combined["source_status"] = "disabled" if all_result.get("disabled") else "unavailable"
        combined["source_error"] = all_result.get("error")

    # Preserve material indicators from the normalized primary building, which
    # has translated facade/roof text even when the list endpoint returns codes.
    for indicator in main_check.get("material_indicators") or []:
        if indicator not in combined["material_indicators"]:
            combined["material_indicators"].append(indicator)

    building["asbestos_check"] = combined
    return building


def report_lines(building):
    """Always surface the result of the asbestos check in the incident brief."""
    if not isinstance(building, dict):
        return []
    check = building.get("asbestos_check")
    if not isinstance(check, dict):
        check = combine_checks([inspect_building(building)], coverage="main_building_only")

    status = check.get("status")
    count = check.get("buildings_checked") or 0
    full_coverage = check.get("coverage") == "all_registered_buildings"

    if status == "yes":
        positives = check.get("positive_buildings") or []
        suffix = ""
        if full_coverage and count:
            suffix = f" ({len(positives)} af {count} registrerede bygninger)"
        primary = f"ASBEST: Registreret i BBR{suffix}"
    elif status == "no":
        if full_coverage and count > 1:
            primary = f"Asbestkontrol: BBR angiver nej på alle {count} registrerede bygninger"
        else:
            primary = "Asbestkontrol: BBR angiver nej"
    elif status == "partial_no":
        primary = "Asbestkontrol: BBR angiver nej på den kontrollerede bygning, men ikke alle bygninger har en entydig status"
    elif status == "unknown":
        primary = "Asbestkontrol: BBR indeholder ukendt asbeststatus"
    else:
        primary = "Asbestkontrol: BBR-felt for asbest blev ikke returneret"

    if check.get("coverage") == "main_building_only" and check.get("source_status") == "unavailable":
        primary += "; øvrige bygninger kunne ikke kontrolleres i dette opslag"

    lines = [primary]
    indicators = check.get("material_indicators") or []
    if indicators:
        lines.append(
            "Materialeindikator: "
            + "; ".join(indicators[:3])
            + " – eternit/cement er ikke i sig selv dokumentation for asbest"
        )
    return lines
