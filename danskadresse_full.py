"""Full DanskAdresseAPI enrichment for operational IndsatsBrief use.

This module keeps the existing BBR adapter as the compatibility layer, but asks
DanskAdresseAPI for BBR + DAGI + DAR + parcel data in one request and adds the
high-value fields that are useful in an incident brief.
"""

from copy import deepcopy
import os
import threading
import time

import requests

import bbr_danskadresse as base_bbr


_CACHE = {}
_CACHE_LOCK = threading.RLock()


def _env_int(name, default, minimum=0, maximum=None):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _first(source, *keys):
    if not isinstance(source, dict):
        return None
    for key in keys:
        value = source.get(key)
        if value is not None and value != "":
            return value
    return None


def _number(value):
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return int(number) if number.is_integer() else number


def _truthy(value):
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "ja", "y"}


def _unwrap_payload(payload):
    if not isinstance(payload, dict):
        return {}
    for key in ("data", "result"):
        nested = payload.get(key)
        if isinstance(nested, dict) and (nested.get("bbr") or nested.get("id")):
            return nested
    return payload


def _request(access_address_id, include_value):
    response = requests.get(
        f"{base_bbr.API_BASE}/adgangsadresser/{access_address_id}",
        params={"include": include_value},
        headers={
            "Authorization": f"Bearer {base_bbr.api_key()}",
            "Accept": "application/json",
            "User-Agent": "IndsatsBrief/1.1",
        },
        timeout=(3, 12),
    )
    try:
        payload = _unwrap_payload(response.json())
    except ValueError:
        payload = None
    return response, payload


def fetch_access_address(access_address_id):
    """Fetch all available enrichment in one call and cache it for 24h by default."""
    key = base_bbr.api_key()
    if not key:
        return {
            "ok": False,
            "status_code": None,
            "error": "DANSKADRESSE_API_KEY mangler",
            "payload": None,
            "cache": "disabled",
        }
    if not access_address_id:
        return {
            "ok": False,
            "status_code": None,
            "error": "Mangler access_address_id",
            "payload": None,
            "cache": "disabled",
        }

    include_value = (
        os.getenv("DANSKADRESSE_INCLUDE")
        or "bbr,dagi,dar,jordstykke"
    ).strip()
    ttl = _env_int("DANSKADRESSE_BBR_CACHE_TTL_SECONDS", 86400, maximum=604800)
    cache_key = (str(access_address_id), include_value)
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
        response, payload = _request(access_address_id, include_value)

        # Older API revisions may understand DAR and DAGI but not allow the
        # jordstykke token in a combined include. Keep the core enrichment alive
        # instead of failing the entire BBR lookup.
        if (
            response.status_code in {400, 404, 422}
            and "jordstykke" in include_value.split(",")
        ):
            reduced = ",".join(
                token for token in include_value.split(",")
                if token.strip() != "jordstykke"
            )
            response, payload = _request(access_address_id, reduced)
    except requests.RequestException as error:
        return {
            "ok": False,
            "status_code": None,
            "error": str(error),
            "payload": None,
            "cache": "miss",
        }

    result = {
        "ok": bool(response.ok and isinstance(payload, dict)),
        "status_code": response.status_code,
        "error": None if response.ok else f"DanskAdresseAPI HTTP {response.status_code}",
        "payload": payload,
        "cache": "miss",
        "include": include_value,
    }

    if result["ok"] and ttl:
        with _CACHE_LOCK:
            while len(_CACHE) >= 512:
                oldest = min(_CACHE, key=lambda item: _CACHE[item]["stored_at"])
                _CACHE.pop(oldest, None)
            _CACHE[cache_key] = {"stored_at": now, "result": deepcopy(result)}

    return result


def _named_area(dagi, key):
    value = dagi.get(key) if isinstance(dagi, dict) else None
    if isinstance(value, dict):
        return {
            "code": _first(value, "kode", "code"),
            "name": _first(value, "navn", "name"),
        }
    return None


def _humanize_classification(value):
    if value in (None, ""):
        return None
    text = str(value).strip().replace("_", " ").replace("-", " ")
    lower = text.lower().replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    if "solcelle" in lower or "solenergi" in lower:
        return "Solcelleanlæg"
    if "varmepumpe" in lower:
        return "Varmepumpe"
    if "vind" in lower and "moelle" in lower:
        return "Vindmølle"
    if "olie" in lower and "tank" in lower:
        return "Olietank"
    if "tank" in lower:
        return "Tank"
    return text[:1].upper() + text[1:]


def normalize_technical_installations(items):
    normalized = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        raw_type = _first(
            item,
            "anlaeg_type_tekst",
            "anlaegstype_tekst",
            "klassifikation_tekst",
            "klassifikation",
            "type_tekst",
            "type",
        )
        label = _humanize_classification(raw_type)
        established_year = _number(_first(item, "etableringsaar", "etableret_aar"))
        manufactured_year = _number(_first(item, "fabrikationsaar", "fremstillingsaar"))
        decommissioned_year = _number(
            _first(item, "sloejfningsaar", "sloejfet_aar", "sløjfningsår")
        )
        decommissioned = _truthy(_first(item, "sloejfet", "sløjfet")) or bool(
            decommissioned_year
        )
        output = _number(_first(item, "effekt", "ydeevne", "ydelse"))
        tank_size = _number(
            _first(
                item,
                "tank_storrelse_liter",
                "tankstoerrelse_liter",
                "tankstoerrelse",
                "tank_storrelse",
            )
        )
        placement = _first(item, "placering_tekst", "placering")
        material = _first(item, "materiale_tekst", "materiale")

        entry = {
            "type": label,
            "classification": raw_type,
            "established_year": established_year,
            "manufactured_year": manufactured_year,
            "decommissioned": decommissioned,
            "decommissioned_year": decommissioned_year,
            "output": output,
            "tank_size_l": tank_size,
            "placement": placement,
            "material": material,
        }
        entry = {key: value for key, value in entry.items() if value not in (None, "")}
        if entry:
            normalized.append(entry)
    return normalized[:20]


def _parcel_context(payload, bbr):
    dar = payload.get("dar") if isinstance(payload.get("dar"), dict) else {}
    parcel = payload.get("jordstykke")
    parcel = parcel if isinstance(parcel, dict) else {}
    grounds = bbr.get("grunde") if isinstance(bbr, dict) else []
    grounds = grounds if isinstance(grounds, list) else []
    ground = grounds[0] if grounds and isinstance(grounds[0], dict) else {}

    owner_area = dar.get("ejerlav") if isinstance(dar.get("ejerlav"), dict) else {}
    if not owner_area and isinstance(parcel.get("ejerlav"), dict):
        owner_area = parcel.get("ejerlav")

    matrikel_number = (
        _first(dar, "matrikelnr")
        or _first(parcel, "matrikelnr", "matrikelnummer")
        or _first(ground, "matrikelnr")
    )
    owner_area_code = (
        _first(owner_area, "kode", "code")
        or _first(parcel, "ejerlavskode")
        or _first(ground, "ejerlavskode")
    )
    owner_area_name = _first(owner_area, "navn", "name") or _first(
        parcel, "ejerlavsnavn"
    )
    ground_area = _number(
        _first(parcel, "areal", "grund_areal", "registreret_areal")
        or _first(ground, "grund_areal", "areal")
    )
    sfe_number = _first(
        parcel,
        "sfe_ejendomsnr",
        "sfe_ejendomsnummer",
    ) or _first(dar, "esrejendomsnr")

    ground_water_code = _first(ground, "vandforsyning")
    ground_water_text = _first(ground, "vandforsyning_tekst") or base_bbr._mapped_text(
        ground_water_code, base_bbr.WATER_SUPPLY_CODES
    )
    ground_drainage_text = _first(ground, "afloebsforhold_tekst", "afløbsforhold_tekst")

    return {
        "matrikel_number": matrikel_number,
        "owner_area_code": owner_area_code,
        "owner_area_name": owner_area_name,
        "ground_area_m2": ground_area,
        "sfe_property_number": sfe_number,
        "zone": _first(dar, "zone"),
        "ground_water_supply": ground_water_text,
        "ground_drainage": ground_drainage_text,
        "ground_drainage_code": _first(ground, "afloebsforhold"),
        "parcel_id": (
            _first(parcel, "id", "jordstykke_id")
            or (_first(dar, "jordstykke") if not isinstance(dar.get("jordstykke"), dict) else None)
        ),
    }


def normalize(result, address_data, app_module):
    """Normalize full API response while preserving the legacy building contract."""
    building = base_bbr.normalize(result, address_data, app_module)
    if not result or not result.get("ok") or not isinstance(building, dict):
        return building

    payload = result.get("payload") or {}
    bbr = payload.get("bbr") if isinstance(payload.get("bbr"), dict) else {}
    raw_building = bbr.get("bygning") if isinstance(bbr.get("bygning"), dict) else {}
    raw_unit = bbr.get("enhed") if isinstance(bbr.get("enhed"), dict) else {}
    entrances = bbr.get("opgange") if isinstance(bbr.get("opgange"), list) else []
    technical = bbr.get("tekniske_anlaeg") if isinstance(bbr.get("tekniske_anlaeg"), list) else []
    dagi = payload.get("dagi") if isinstance(payload.get("dagi"), dict) else {}
    dar = payload.get("dar") if isinstance(payload.get("dar"), dict) else {}

    parcel = _parcel_context(payload, bbr)
    administrative = {
        "municipality": _named_area(dagi, "kommune"),
        "region": _named_area(dagi, "region"),
        "parish": _named_area(dagi, "sogn"),
        "police_district": _named_area(dagi, "politikreds"),
        "court_district": _named_area(dagi, "retskreds"),
        "constituency": _named_area(dagi, "opstillingskreds"),
        "multi_member_constituency": _named_area(dagi, "storkreds"),
        "polling_district": _named_area(dagi, "afstemningsomraade"),
        "country_part": _named_area(dagi, "landsdel"),
    }
    administrative = {
        key: value for key, value in administrative.items()
        if value and (value.get("name") or value.get("code"))
    }

    elevator_registered = any(
        isinstance(entrance, dict)
        and _truthy(_first(entrance, "elevator", "elevator_status"))
        for entrance in entrances
    )

    settlements = []
    for item in dar.get("bebyggelser") or []:
        if isinstance(item, dict) and _first(item, "navn", "name"):
            settlements.append(_first(item, "navn", "name"))
        elif isinstance(item, str) and item.strip():
            settlements.append(item.strip())

    building.update(
        {
            "technical_installations": technical,
            "operational_installations": normalize_technical_installations(technical),
            "elevator_registered": elevator_registered,
            "shelter_spaces": _number(_first(raw_building, "sikringsrumpladser")),
            "floor_deviation_note": _first(raw_building, "afvigende_etager"),
            "revision_date": _first(raw_building, "revisionsdato"),
            "built_in_conservatory_area_m2": _number(
                _first(raw_building, "indbygget_udestue_areal")
            ),
            "waste_room_area_m2": _number(_first(raw_building, "affaldsrum_areal")),
            "covered_area_m2": _number(_first(raw_building, "overdaekket_areal")),
            "unit_rooms": _number(_first(raw_unit, "antal_vaerelser")),
            "unit_toilets": _number(_first(raw_unit, "antal_toiletter")),
            "unit_bathrooms": _number(_first(raw_unit, "antal_badevaerelser")),
            "unit_kitchen_text": _first(raw_unit, "koekken_tekst"),
            "unit_energy_supply": _first(raw_unit, "energiforsyning_tekst", "energiforsyning"),
            "administrative_context": administrative,
            "dar_context": {
                "kvh": _first(dar, "kvh"),
                "kvhx": _first(dar, "kvhx"),
                "history": dar.get("historik") if isinstance(dar.get("historik"), dict) else {},
                "address_point": dar.get("adgangspunkt") if isinstance(dar.get("adgangspunkt"), dict) else {},
                "road_point": dar.get("vejpunkt") if isinstance(dar.get("vejpunkt"), dict) else {},
                "zone": _first(dar, "zone"),
                "settlements": settlements,
                "ddkn": _first(dar, "ddkn"),
            },
            "cadastre": parcel,
            "cadastre_parcel": parcel.get("matrikel_number") or building.get("cadastre_parcel"),
            "danskadresse_include": result.get("include"),
        }
    )

    return building


def get_building(address_data, app_module):
    access_address_id = address_data.get("access_address_id") if address_data else None
    return normalize(fetch_access_address(access_address_id), address_data, app_module)


def _format_number(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).replace(".", ",")


def _installation_line(item):
    label = item.get("type") or "Teknisk anlæg"
    parts = [label]
    if item.get("tank_size_l"):
        parts.append(f"{_format_number(item['tank_size_l'])} L")
    if item.get("output"):
        parts.append(f"effekt/ydeevne {_format_number(item['output'])}")
    if item.get("placement"):
        parts.append(str(item["placement"]))
    if item.get("material"):
        parts.append(f"materiale {item['material']}")
    if item.get("established_year"):
        parts.append(f"etableret {item['established_year']}")
    if item.get("decommissioned"):
        if item.get("decommissioned_year"):
            parts.append(f"sløjfet {item['decommissioned_year']}")
        else:
            parts.append("registreret sløjfet")
    return ": ".join([parts[0], ", ".join(parts[1:])]) if len(parts) > 1 else f"{label} registreret"


def operational_report_additions(building):
    """Return compact, presentation-ready additions using existing report fields."""
    additions = {
        "building_lines": [],
        "building_details": [],
        "heating_lines": [],
        "risk_context_lines": [],
        "supplementary_lines": [],
    }
    if not isinstance(building, dict):
        return additions

    if building.get("energy_label"):
        additions["building_details"].append(f"Energimærke: {building['energy_label']}")
    if building.get("shelter_spaces"):
        additions["risk_context_lines"].append(
            f"Sikringsrumpladser registreret: {_format_number(building['shelter_spaces'])}"
        )
    if building.get("elevator_registered"):
        additions["risk_context_lines"].append("Elevator registreret")
    if str(building.get("asbestos_material_text") or "").strip().lower() == "ja":
        additions["risk_context_lines"].append("Asbestholdigt materiale registreret i BBR")

    for installation in building.get("operational_installations") or []:
        additions["risk_context_lines"].append(_installation_line(installation))

    cadastre = building.get("cadastre") or {}
    matrix = cadastre.get("matrikel_number")
    owner = cadastre.get("owner_area_name")
    if matrix and owner:
        additions["supplementary_lines"].append(f"Matrikel: {matrix}, {owner}")
    elif matrix:
        additions["supplementary_lines"].append(f"Matrikel: {matrix}")
    if cadastre.get("ground_area_m2"):
        additions["supplementary_lines"].append(
            f"Grundareal: {_format_number(cadastre['ground_area_m2'])} m²"
        )
    if cadastre.get("zone"):
        additions["supplementary_lines"].append(f"Zone: {cadastre['zone']}")
    if cadastre.get("ground_water_supply"):
        additions["supplementary_lines"].append(
            f"Vandforsyning på grund: {cadastre['ground_water_supply']}"
        )
    if cadastre.get("ground_drainage"):
        additions["supplementary_lines"].append(
            f"Afløb på grund: {cadastre['ground_drainage']}"
        )

    admin = building.get("administrative_context") or {}
    admin_parts = []
    for key in ("municipality", "region", "police_district"):
        value = admin.get(key) or {}
        if value.get("name"):
            admin_parts.append(value["name"])
    if admin_parts:
        additions["supplementary_lines"].append(
            "Administrativt område: " + " · ".join(dict.fromkeys(admin_parts))
        )

    return {
        key: list(dict.fromkeys(value))
        for key, value in additions.items()
    }


def augment_report(report, raw_incident_data, app_module):
    if not isinstance(report, dict):
        return report
    enriched = dict(report)
    building = (raw_incident_data or {}).get("building") or {}
    additions = operational_report_additions(building)
    for field, lines in additions.items():
        current = list(enriched.get(field) or [])
        for line in lines:
            if app_module.is_positive_report_value(line) and line not in current:
                current.append(line)
        enriched[field] = current
    return app_module.clean_report_sections(enriched)
