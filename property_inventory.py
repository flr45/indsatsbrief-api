"""Property-wide BBR building inventory for IndsatsBrief.

Uses the same cached /v1/bbr/bygninger lookup as the asbestos guard and turns
all returned BBR building records into compact operational lines. The provider
currently returns at most 20 buildings per access address.
"""

import asbestos_guard
import bbr_danskadresse as bbr


MAX_BUILDINGS = 20


def _first(source, *keys):
    if not isinstance(source, dict):
        return None
    for key in keys:
        value = source.get(key)
        if value is not None and value != "":
            return value
    return None


def _number(value, allow_zero=False):
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if number < 0 or (number == 0 and not allow_zero):
        return None
    return int(number) if number.is_integer() else number


def _mapped_text(source, text_keys, code_keys, mapping):
    text = _first(source, *text_keys)
    if text not in (None, ""):
        return str(text).strip()
    return bbr._mapped_text(_first(source, *code_keys), mapping)


def _format_number(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).replace(".", ",")


def normalize_building(raw):
    """Normalize one standalone BBR building without hiding secondary structures."""
    if not isinstance(raw, dict):
        return {}

    use_code = _first(raw, "anvendelse", "anvendelseskode", "byg021BygningensAnvendelse")
    use_text = _first(raw, "anvendelse_tekst", "anvendelsestekst", "bygningstype_tekst")
    if not use_text:
        use_text = bbr._mapped_text(use_code, bbr.BUILDING_USE_CODES)
    if not use_text and use_code not in (None, ""):
        use_text = f"BBR-anvendelse {use_code}"

    asbestos = asbestos_guard.inspect_building(raw)

    return {
        "id": _first(raw, "id", "id_lokalId", "datafordelerRowId"),
        "building_number": _number(
            _first(raw, "bygningsnummer", "byg007Bygningsnummer"), allow_zero=True
        ),
        "status": _first(raw, "status"),
        "use_code": use_code,
        "use_text": use_text or "Ukendt bygningstype",
        "construction_year": _number(
            _first(raw, "opfoerelsesaar", "opførelsesår", "byg026Opfoerelsesaar")
        ),
        "alteration_year": _number(
            _first(
                raw,
                "ombygningsaar",
                "om_tilbygningsaar",
                "omtilbygningsaar",
                "byg027OmTilbygningsaar",
            )
        ),
        "floors": _number(_first(raw, "antal_etager", "byg054AntalEtager")),
        "total_area_m2": _number(
            _first(raw, "samlet_bygningsareal", "byg038SamletBygningsareal")
        ),
        "built_area_m2": _number(
            _first(raw, "bebygget_areal", "byg041BebyggetAreal")
        ),
        "residential_area_m2": _number(
            _first(raw, "samlet_boligareal", "byg039BygningensSamledeBoligAreal")
        ),
        "commercial_area_m2": _number(
            _first(raw, "samlet_erhvervsareal", "byg040BygningensSamledeErhvervsAreal")
        ),
        "built_in_garage_m2": _number(
            _first(raw, "indbygget_garage_areal", "byg042ArealIndbyggetGarage")
        ),
        "built_in_carport_m2": _number(
            _first(raw, "indbygget_carport_areal", "byg043ArealIndbyggetCarport")
        ),
        "built_in_outhouse_m2": _number(
            _first(raw, "indbygget_udhus_areal", "byg044ArealIndbyggetUdhus")
        ),
        "built_in_conservatory_m2": _number(
            _first(raw, "indbygget_udestue_areal")
        ),
        "other_area_m2": _number(_first(raw, "andet_areal", "byg048AndetAreal")),
        "outer_wall": _mapped_text(
            raw,
            ("ydervaeggens_materiale_tekst", "ydervaegs_materiale_tekst"),
            ("ydervaeggens_materiale", "ydervaegs_materiale", "byg032YdervaeggensMateriale"),
            bbr.OUTER_WALL_CODES,
        ),
        "roof": _mapped_text(
            raw,
            ("tagdaekningsmateriale_tekst", "tagdaeknings_materiale_tekst"),
            ("tagdaekningsmateriale", "tagdaeknings_materiale", "byg033Tagdaekningsmateriale"),
            bbr.ROOF_CODES,
        ),
        "heating": _mapped_text(
            raw,
            ("varmeinstallation_tekst",),
            ("varmeinstallation", "byg056Varmeinstallation"),
            bbr.HEATING_INSTALLATION_CODES,
        ),
        "heating_fuel": _mapped_text(
            raw,
            ("opvarmningsmiddel_tekst",),
            ("opvarmningsmiddel", "byg057Opvarmningsmiddel"),
            bbr.HEATING_FUEL_CODES,
        ),
        "asbestos_status": asbestos.get("status"),
        "asbestos_field_present": asbestos.get("field_present", False),
    }


def _sort_key(item):
    number = item.get("building_number")
    try:
        return (0, int(number))
    except (TypeError, ValueError):
        return (1, str(number or item.get("use_text") or ""))


def build_inventory(raw_buildings):
    items = [normalize_building(item) for item in raw_buildings or [] if isinstance(item, dict)]
    items = [item for item in items if item]
    items.sort(key=_sort_key)
    return items


def enrich_building(building, access_address_id=None):
    """Attach all registered BBR buildings using the asbestos lookup cache."""
    if not isinstance(building, dict):
        return building

    result = asbestos_guard.fetch_all_buildings(
        access_address_id or building.get("access_address_id")
    )
    if result.get("ok"):
        inventory = build_inventory(result.get("buildings") or [])
        building["registered_buildings"] = inventory
        building["registered_building_count"] = len(inventory)
        building["building_inventory_status"] = "ok"
        building["building_inventory_cache"] = result.get("cache")
        # The API endpoint has a hard max of 20. Exactly 20 can therefore mean
        # that additional records exist but cannot be proven from this response.
        building["building_inventory_may_be_truncated"] = len(inventory) >= MAX_BUILDINGS
    else:
        building["registered_buildings"] = []
        building["registered_building_count"] = 0
        building["building_inventory_status"] = (
            "disabled" if result.get("disabled") else "unavailable"
        )
        building["building_inventory_error"] = result.get("error")
        building["building_inventory_may_be_truncated"] = False
    return building


def building_line(item):
    number = item.get("building_number")
    prefix = f"Bygning {number}" if number is not None else "Bygning"
    parts = [item.get("use_text") or "Ukendt bygningstype"]

    area = item.get("total_area_m2") or item.get("built_area_m2")
    if area:
        parts.append(f"{_format_number(area)} m²")
    if item.get("construction_year"):
        parts.append(f"opført {item['construction_year']}")
    if item.get("alteration_year"):
        parts.append(f"om-/tilbygget {item['alteration_year']}")
    if item.get("floors"):
        parts.append(f"{_format_number(item['floors'])} etage(r)")

    embedded = []
    for label, key in (
        ("indbygget garage", "built_in_garage_m2"),
        ("indbygget carport", "built_in_carport_m2"),
        ("indbygget udhus", "built_in_outhouse_m2"),
        ("indbygget udestue", "built_in_conservatory_m2"),
    ):
        if item.get(key):
            embedded.append(f"{label} {_format_number(item[key])} m²")
    if embedded:
        parts.append("; ".join(embedded))

    materials = []
    if item.get("outer_wall"):
        materials.append(f"facade {item['outer_wall']}")
    if item.get("roof"):
        materials.append(f"tag {item['roof']}")
    if materials:
        parts.append(", ".join(materials))

    if item.get("asbestos_status") == "yes":
        parts.append("ASBEST registreret")
    elif item.get("asbestos_status") == "unknown":
        parts.append("asbeststatus ukendt")

    return f"{prefix}: " + " · ".join(str(part) for part in parts if part)


def report_lines(building):
    """Return a complete, compact building inventory for the incident brief."""
    if not isinstance(building, dict):
        return []

    inventory = building.get("registered_buildings") or []
    status = building.get("building_inventory_status")
    if status != "ok":
        return ["Bygningsoversigt: Alle BBR-bygninger kunne ikke hentes i dette opslag"]

    count = len(inventory)
    if not count:
        return ["Bygningsoversigt: Ingen separate BBR-bygningsposter fundet på adressen"]

    if count == 1:
        header = "Bygningsoversigt: 1 registreret bygning"
    else:
        header = f"Bygningsoversigt: {count} registrerede bygninger"
    if building.get("building_inventory_may_be_truncated"):
        header += " (API-grænse på 20 er nået; der kan findes flere)"

    lines = [header]
    lines.extend(building_line(item) for item in inventory)
    return lines
