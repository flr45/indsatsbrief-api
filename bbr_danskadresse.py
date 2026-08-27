"""DanskAdresseAPI BBR adapter for IndsatsBrief.

Normalizes both the enriched text-field response and the native /v1 BBR
code-field response to the building contract already consumed by app.py.
"""

from copy import deepcopy
import os
import time

import requests


API_BASE = os.getenv("DANSKADRESSE_API_BASE", "https://api.danskadresseapi.dk/v1").rstrip("/")
_CACHE = {}

OUTER_WALL_CODES = {
    "1": "Mursten",
    "2": "Letbeton",
    "3": "Plader/eternit",
    "4": "Bindingsværk",
    "5": "Træ",
    "6": "Metal",
    "8": "Andet",
}
ROOF_CODES = {
    "1": "Tagpap",
    "2": "Cementtagsten",
    "3": "Eternit/cement",
    "4": "Tagsten/tegl",
    "5": "Metal",
    "6": "Stråtag",
    "7": "Glas",
    "80": "Andet",
}
HEATING_INSTALLATION_CODES = {
    "1": "Fjernvarme",
    "2": "Centralvarme fra eget anlæg",
    "3": "Ovne",
    "5": "Varmepumpe",
    "6": "Centralvarme med to fyringsenheder",
    "7": "Elvarme",
    "8": "Gasradiator",
    "9": "Ingen varmeinstallation",
    "99": "Ukendt",
}
HEATING_FUEL_CODES = {
    "1": "El",
    "2": "Gasværksgas",
    "3": "Flydende brændsel",
    "4": "Fast brændsel",
    "5": "Fast/flydende brændsel",
    "6": "Halm",
    "7": "Naturgas",
    "9": "Andet",
}
SUPPLEMENTARY_HEATING_CODES = {
    "0": "Ingen",
    "1": "Varmepumpe",
    "2": "Ovne",
    "3": "Elvarme",
    "4": "Gasradiator",
    "5": "Solenergi",
    "80": "Andet",
}
WATER_SUPPLY_CODES = {
    "1": "Alment vandforsyningsanlæg",
    "2": "Privat vandforsyningsanlæg",
    "3": "Enkeltindvindingsanlæg",
    "4": "Brønd",
    "6": "Ikke alment vandforsyningsanlæg",
    "7": "Blandet vandforsyning",
    "9": "Ingen vandforsyning",
}
ASBEST_CODES = {
    "0": "Nej",
    "1": "Ja",
    "9": "Ukendt",
}
PRESERVATION_CODES = {
    "0": "Ingen",
    "1": "Fredet",
    "2": "Bevaringsværdig",
}
BUILDING_USE_CODES = {
    "110": "Stuehus til landbrugsejendom",
    "120": "Fritliggende enfamiliehus",
    "121": "Sammenbygget enfamiliehus",
    "122": "Fritliggende enfamiliehus i tæt-lav bebyggelse",
    "131": "Række-, kæde- og klyngehus",
    "132": "Dobbelthus",
    "140": "Etagebolig-bygning/flerfamiliehus",
    "150": "Kollegium",
    "160": "Boligbygning til døgninstitution",
    "185": "Anneks i tilknytning til helårsbolig",
    "190": "Anden bygning til helårsbeboelse",
    "211": "Stald til svin",
    "212": "Stald til kvæg, får mv.",
    "213": "Stald til fjerkræ",
    "214": "Minkhal",
    "215": "Væksthus",
    "216": "Lade til foder, afgrøder mv.",
    "217": "Maskinhus/garage",
    "218": "Lade til halm/hø",
    "219": "Anden bygning til landbrug",
    "221": "Industribygning med integreret produktionsapparat",
    "222": "Industribygning uden integreret produktionsapparat",
    "223": "Værksted",
    "229": "Anden bygning til produktion",
    "231": "Bygning til energiproduktion",
    "232": "Bygning til energidistribution",
    "233": "Bygning til vandforsyning",
    "234": "Bygning til håndtering af affald og spildevand",
    "239": "Anden bygning til energiproduktion og forsyning",
    "910": "Garage",
    "920": "Carport",
    "930": "Udhus",
    "940": "Drivhus",
    "950": "Fritliggende overdækning",
    "960": "Fritliggende udestue",
    "970": "Anden bygning til landbrug/produktion",
    "990": "Anden mindre bygning",
    "999": "Ukendt bygningstype",
}


def _env_int(name, default, minimum=0, maximum=None):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def api_key():
    """Read the key at request time so container env changes are respected."""
    return (
        os.getenv("DANSKADRESSE_API_KEY")
        or os.getenv("INDSATSBRIEF_DANSKADRESSE_API_KEY")
        or ""
    ).strip()


def configured():
    return bool(api_key())


def _first(source, *keys):
    if not isinstance(source, dict):
        return None
    for key in keys:
        if source.get(key) is not None:
            return source.get(key)
    return None


def _number(value):
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return int(number) if number.is_integer() else number


def _code_key(value):
    if value in (None, ""):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _mapped_text(value, mapping):
    key = _code_key(value)
    return mapping.get(key) if key is not None else None


def _text_or_code(source, text_keys, code_keys, mapping):
    text = _first(source, *text_keys)
    if text not in (None, ""):
        return str(text).strip()
    return _mapped_text(_first(source, *code_keys), mapping)


def _bool_text(value):
    if value is True:
        return "Ja"
    if value is False:
        return "Nej"
    return _mapped_text(value, ASBEST_CODES)


def _unwrap_payload(payload):
    """Accept both direct v1 objects and a future envelope without breaking."""
    if not isinstance(payload, dict):
        return {}
    for key in ("data", "result"):
        nested = payload.get(key)
        if isinstance(nested, dict) and (nested.get("bbr") or nested.get("id")):
            return nested
    return payload


def fetch_access_address(access_address_id):
    key = api_key()
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

    ttl = _env_int("DANSKADRESSE_BBR_CACHE_TTL_SECONDS", 86400, minimum=0)
    cache_key = str(access_address_id)
    now = time.monotonic()
    cached = _CACHE.get(cache_key)

    if cached and ttl and now - cached["stored_at"] < ttl:
        response = deepcopy(cached["response"])
        response["cache"] = "hit"
        return response

    url = f"{API_BASE}/adgangsadresser/{access_address_id}"
    try:
        response = requests.get(
            url,
            params={"include": "bbr"},
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
                "User-Agent": "IndsatsBrief/1.0",
            },
            timeout=(3, 10),
        )
    except requests.RequestException as error:
        return {
            "ok": False,
            "status_code": None,
            "error": str(error),
            "payload": None,
            "cache": "miss",
        }

    try:
        payload = _unwrap_payload(response.json())
    except ValueError:
        payload = None

    result = {
        "ok": bool(response.ok and isinstance(payload, dict)),
        "status_code": response.status_code,
        "error": None if response.ok else f"DanskAdresseAPI HTTP {response.status_code}",
        "payload": payload,
        "cache": "miss",
    }

    if result["ok"] and ttl:
        if len(_CACHE) >= 512 and cache_key not in _CACHE:
            oldest_key = min(_CACHE, key=lambda item: _CACHE[item]["stored_at"])
            _CACHE.pop(oldest_key, None)
        _CACHE[cache_key] = {"stored_at": now, "response": deepcopy(result)}

    return result


def _normalize_floors(bbr):
    floors = bbr.get("etager") if isinstance(bbr, dict) else None
    floors = floors if isinstance(floors, list) else []

    basement_area = 0
    basement_present = None
    summaries = []

    for floor in floors:
        if not isinstance(floor, dict):
            continue
        label = str(_first(floor, "etagebetegnelse", "etage_betegnelse") or "").strip()
        floor_area = _number(
            _first(
                floor,
                "samlet_etage_areal",
                "samlet_areal_af_etage",
                "samlet_areal",
            )
        )
        explicit_basement = _number(
            _first(floor, "kaelder_areal", "kaelderareal", "kælder_areal", "kælderareal")
        )
        lowered_label = label.lower()
        is_basement = (
            lowered_label in {"kl", "kld", "k"}
            or "kælder" in lowered_label
            or "kaelder" in lowered_label
        )

        if explicit_basement:
            basement_area += explicit_basement
            basement_present = True
        elif is_basement and floor_area:
            basement_area += floor_area
            basement_present = True
        elif is_basement:
            basement_present = True

        summary = {
            "floor_label": label or None,
            "floor_area_m2": floor_area,
            "basement_area_m2": explicit_basement or (floor_area if is_basement else None),
        }
        summary = {key: value for key, value in summary.items() if value is not None}
        if summary:
            summaries.append(summary)

    return {
        "floors_raw": floors,
        "basement_area_m2": basement_area or None,
        "basement_present": basement_present,
        "basement_living_area_m2": None,
        "basement_commercial_area_m2": None,
        "basement_source": (
            "DanskAdresseAPI BBR etager"
            if basement_present is True
            else "DanskAdresseAPI BBR etager undersøgt, men kælderdata ikke fundet"
        ),
        "basement_raw": {"etager": floors},
        "attic_used_area_m2": None,
        "floors_summary": summaries,
    }


def normalize(result, address_data, app_module):
    """Map DanskAdresseAPI fields to the shape already consumed by IndsatsBrief."""
    if not result or not result.get("ok"):
        placeholder = app_module.get_building_placeholder(address_data)
        placeholder["source"] = "DanskAdresseAPI BBR kunne ikke hentes"
        placeholder["verification_status"] = "BBR/bygningsdata ikke fundet"
        placeholder["danskadresse_error"] = result.get("error") if result else None
        return placeholder

    payload = result.get("payload") or {}
    bbr = payload.get("bbr") or {}
    building = bbr.get("bygning") if isinstance(bbr, dict) else None
    unit = bbr.get("enhed") if isinstance(bbr, dict) else None
    building = building if isinstance(building, dict) else {}
    unit = unit if isinstance(unit, dict) else {}

    if not building:
        placeholder = app_module.get_building_placeholder(address_data)
        placeholder["source"] = "DanskAdresseAPI svarede, men fandt ingen BBR-bygning"
        placeholder["verification_status"] = "BBR/bygningsdata ikke fundet"
        return placeholder

    floor_data = _normalize_floors(bbr)
    technical_installations = bbr.get("tekniske_anlaeg")
    technical_installations = technical_installations if isinstance(technical_installations, list) else []
    grounds = bbr.get("grunde")
    grounds = grounds if isinstance(grounds, list) else []
    entrances = bbr.get("opgange")
    entrances = entrances if isinstance(entrances, list) else []

    usage_code = _first(building, "anvendelseskode", "anvendelse")
    usage_text = _first(building, "anvendelse_tekst") or _mapped_text(
        usage_code, BUILDING_USE_CODES
    )

    outer_wall_code = _first(
        building, "ydervaegs_materiale_kode", "ydervaeggens_materiale"
    )
    outer_wall_text = _text_or_code(
        building,
        ("ydervaegs_materiale_tekst",),
        ("ydervaegs_materiale_kode", "ydervaeggens_materiale"),
        OUTER_WALL_CODES,
    )
    roof_code = _first(
        building, "tagdaeknings_materiale_kode", "tagdaekningsmateriale"
    )
    roof_text = _text_or_code(
        building,
        ("tagdaeknings_materiale_tekst",),
        ("tagdaeknings_materiale_kode", "tagdaekningsmateriale"),
        ROOF_CODES,
    )
    heating_installation_code = _first(
        building, "varmeinstallation_kode", "varmeinstallation"
    )
    heating_installation_text = _text_or_code(
        building,
        ("varmeinstallation_tekst",),
        ("varmeinstallation_kode", "varmeinstallation"),
        HEATING_INSTALLATION_CODES,
    )
    heating_fuel_code = _first(
        building, "opvarmningsmiddel_kode", "opvarmningsmiddel"
    )
    heating_fuel_text = _text_or_code(
        building,
        ("opvarmningsmiddel_tekst",),
        ("opvarmningsmiddel_kode", "opvarmningsmiddel"),
        HEATING_FUEL_CODES,
    )
    supplementary_heating_code = _first(
        building, "supplerende_varme_kode", "supplerende_varme"
    )
    supplementary_heating_text = _text_or_code(
        building,
        ("supplerende_varme_tekst",),
        ("supplerende_varme_kode", "supplerende_varme"),
        SUPPLEMENTARY_HEATING_CODES,
    )
    water_supply_code = _first(building, "vandforsyning_kode", "vandforsyning")
    water_supply_text = _text_or_code(
        building,
        ("vandforsyning_tekst",),
        ("vandforsyning_kode", "vandforsyning"),
        WATER_SUPPLY_CODES,
    )

    asbestos_value = _first(building, "asbestholdigt_materiale")
    preservation_code = _first(building, "fredning")
    preservation_text = _first(building, "fredning_status") or _mapped_text(
        preservation_code, PRESERVATION_CODES
    )

    return {
        "source": "BBR via DanskAdresseAPI",
        "working_candidate": "danskadresse_v1_access_address",
        "bbr_id": _first(building, "bygningsnummer", "bygning_id", "id"),
        "id_lokalId": _first(building, "bygning_id", "id"),
        "id_namespace": None,
        "datafordelerRowId": None,
        "access_address_id": address_data.get("access_address_id") if address_data else payload.get("id"),
        "address_id": address_data.get("address_id") if address_data else None,
        "husnummer": payload.get("husnr") or (address_data.get("house_number") if address_data else None),
        "municipality_code": _first(building, "kommunekode") or (address_data.get("municipality_code") if address_data else None),
        "usage": usage_code,
        "usage_text": usage_text,
        "building_type": usage_code,
        "building_type_text": usage_text,
        "floors_count": _first(building, "antal_etager"),
        "apartments_with_kitchen": None,
        "apartments_without_kitchen": None,
        "construction_year": _first(building, "opfoerelsesaar"),
        "renovation_year": _first(building, "ombygningsaar"),
        "area_m2": _first(building, "samlet_bygningsareal"),
        "residential_area_m2": _first(unit, "samlet_boligareal", "areal_til_beboelse") or _first(building, "samlet_boligareal"),
        "commercial_area_m2": _first(unit, "samlet_erhvervsareal", "areal_til_erhverv") or _first(building, "samlet_erhvervsareal"),
        "built_area_m2": _first(building, "bebygget_areal"),
        "built_in_garage_area_m2": _first(building, "indbygget_garage_areal"),
        "built_in_carport_area_m2": _first(building, "indbygget_carport_areal"),
        "built_in_shed_area_m2": _first(building, "indbygget_udhus_areal"),
        "other_area_m2": _first(building, "andet_areal"),
        "access_area_m2": _first(building, "adgangsareal"),
        "basement": "Ikke verificeret",
        **floor_data,
        "outer_wall_material": outer_wall_code,
        "outer_wall_material_text": outer_wall_text,
        "roof_material": roof_code,
        "roof_material_text": roof_text,
        "water_supply": water_supply_code,
        "water_supply_text": water_supply_text,
        "asbestos_material": asbestos_value,
        "asbestos_material_text": _bool_text(asbestos_value),
        "heating_installation": heating_installation_code,
        "heating_installation_text": heating_installation_text,
        "heating_fuel": heating_fuel_code,
        "heating_fuel_text": heating_fuel_text,
        "supplementary_heating": supplementary_heating_code,
        "supplementary_heating_text": supplementary_heating_text,
        "preservation_status": preservation_code or preservation_text,
        "preservation_status_text": preservation_text,
        "preservation_reference": _first(building, "bevaringsvaerdig"),
        "bbr_notes_raw": None,
        "ground": grounds[0] if grounds else None,
        "cadastre_parcel": _first(building, "matrikelnr"),
        "status": _first(building, "status"),
        "status_text": None,
        "energy_label": _first(building, "energimaerke") or _first(unit, "energimaerke"),
        "energy_label_valid_until": _first(building, "energimaerke_gyldig_til"),
        "raw_bbr_building": building,
        "raw_bbr_unit": unit,
        "all_bbr_nodes_for_address": [building],
        "secondary_buildings": [],
        "technical_installations": technical_installations,
        "bbr_grounds": grounds,
        "bbr_entrances": entrances,
        "danskadresse_cache": result.get("cache"),
        "fire_relevant_notes": [
            "BBR-data er registerdata og skal vurderes kritisk ved indsats",
            "BBR-data er hentet via DanskAdresseAPI",
        ],
        "verification_status": "BBR/bygningsdata hentet via DanskAdresseAPI",
    }


def get_building(address_data, app_module):
    access_address_id = address_data.get("access_address_id") if address_data else None
    return normalize(fetch_access_address(access_address_id), address_data, app_module)
