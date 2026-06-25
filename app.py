from flask import Flask, request, jsonify, Response
from datetime import datetime, timezone
from urllib.parse import quote
import requests
import os
import math
import base64
import re

app = Flask(__name__)
app.json.ensure_ascii = False


# -------------------------------------------------------
# BBR kodelister
# -------------------------------------------------------

BBR_BUILDING_USAGE = {
    "110": "Stuehus til landbrugsejendom",
    "120": "Fritliggende enfamiliehus",
    "121": "Sammenbygget enfamiliehus",
    "122": "Fritliggende enfamiliehus i tæt-lav bebyggelse",
    "130": "(UDFASES) Række-, kæde-, eller dobbelthus",
    "131": "Række-, kæde- og klyngehus",
    "132": "Dobbelthus",
    "140": "Etagebolig-bygning, flerfamiliehus eller to-familiehus",
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
    "217": "Maskinhus, garage mv.",
    "218": "Lade til halm, hø mv.",
    "219": "Anden bygning til landbrug mv.",

    "221": "Bygning til industri med integreret produktionsapparat",
    "222": "Bygning til industri uden integreret produktionsapparat",
    "223": "Værksted",
    "229": "Anden bygning til produktion",

    "231": "Bygning til energiproduktion",
    "232": "Bygning til energidistribution",
    "233": "Bygning til vandforsyning",
    "234": "Bygning til håndtering af affald og spildevand",
    "239": "Anden bygning til energiproduktion og forsyning",

    "311": "Bygning til jernbane- og busdrift",
    "312": "Bygning til luftfart",
    "313": "Bygning til parkering- og transportanlæg",
    "314": "Bygning til parkering af flere end to køretøjer i tilknytning til boliger",
    "315": "Havneanlæg",
    "319": "Andet transportanlæg",

    "321": "Bygning til kontor",
    "322": "Bygning til detailhandel",
    "323": "Bygning til lager",
    "324": "Butikscenter",
    "325": "Tankstation",
    "329": "Anden bygning til kontor, handel og lager",

    "331": "Hotel, kro eller konferencecenter med overnatning",
    "332": "Bed & breakfast mv.",
    "333": "Restaurant, café og konferencecenter uden overnatning",
    "334": "Privat servicevirksomhed som frisør, vaskeri, netcafé mv.",
    "339": "Anden bygning til serviceerhverv",

    "411": "Biograf, teater, koncertsted mv.",
    "412": "Museum",
    "413": "Bibliotek",
    "414": "Kirke eller anden bygning til trosudøvelse",
    "415": "Forsamlingshus",
    "416": "Forlystelsespark",
    "419": "Anden bygning til kulturelle formål",

    "421": "Grundskole",
    "422": "Universitet",
    "429": "Anden bygning til undervisning og forskning",

    "431": "Hospital og sygehus",
    "432": "Hospice, behandlingshjem mv.",
    "433": "Sundhedscenter, lægehus, fødeklinik mv.",
    "439": "Anden bygning til sundhedsformål",

    "441": "Daginstitution",
    "442": "Servicefunktion på døgninstitution",
    "443": "Kaserne",
    "444": "Fængsel, arresthus mv.",
    "449": "Anden bygning til institutionsformål",
    "451": "Beskyttelsesrum",

    "510": "Sommerhus",
    "521": "Feriecenter, center til campingplads mv.",
    "522": "Bygning med ferielejligheder til erhvervsmæssig udlejning",
    "523": "Bygning med ferielejligheder til eget brug",
    "529": "Anden bygning til ferieformål",

    "531": "Klubhus i forbindelse med fritid og idræt",
    "532": "Svømmehal",
    "533": "Idrætshal",
    "534": "Tribune i forbindelse med stadion",
    "535": "Bygning til træning og opstaldning af heste",
    "539": "Anden bygning til idrætsformål",

    "540": "Kolonihavehus",
    "585": "Anneks i tilknytning til fritids- og sommerhus",
    "590": "Anden bygning til fritidsformål",

    "910": "Garage",
    "920": "Carport",
    "930": "Udhus",
    "940": "Drivhus",
    "950": "Fritliggende overdækning",
    "960": "Fritliggende udestue",
    "970": "Tiloversbleven landbrugsbygning",
    "990": "Faldefærdig bygning",
    "999": "Ukendt bygning",
}

BBR_OUTER_WALL_MATERIAL = {
    "1": "Mursten",
    "2": "Letbetonsten",
    "3": "Fibercement herunder asbest",
    "4": "Bindingsværk",
    "5": "Træ",
    "6": "Betonelementer",
    "8": "Metal",
    "10": "Fibercement uden asbest",
    "11": "Plastmaterialer",
    "12": "Glas",
    "80": "Ingen",
    "90": "Andet materiale",
}

BBR_ROOF_MATERIAL = {
    "1": "Tagpap med lille hældning",
    "2": "Tagpap med stor hældning",
    "3": "Fibercement herunder asbest",
    "4": "Betontagsten",
    "5": "Tegl",
    "6": "Metal",
    "7": "Stråtag",
    "10": "Fibercement uden asbest",
    "11": "Plastmaterialer",
    "12": "Glas",
    "20": "Levende tage",
    "90": "Andet materiale",
}

BBR_HEATING_FUEL = {
    "1": "Elektricitet",
    "2": "Gasværksgas",
    "3": "Flydende brændsel",
    "4": "Fast brændsel",
    "6": "Halm",
    "7": "Naturgas",
    "9": "Andet",
}

BBR_HEATING_INSTALLATION = {
    "1": "Fjernvarme/blokvarme",
    "2": "Centralvarme fra eget anlæg",
    "3": "Ovne, herunder kakkelovne, kamin og brændeovne",
    "5": "Varmepumpe",
    "6": "Centralvarme med to fyringsenheder",
    "7": "Elovne/elpaneler",
    "8": "Gasradiatorer",
    "9": "Ingen varmeinstallation",
    "99": "Blandet varmeinstallation",
}

BBR_SUPPLEMENTARY_HEATING = {
    "1": "Varmepumpe",
    "2": "Ovne til fast brændsel",
    "3": "Ovne til flydende brændsel",
    "4": "Solpaneler",
    "5": "Pejs",
    "6": "Gasradiator",
    "7": "Elovne",
    "10": "Biogasanlæg",
    "80": "Andet",
    "90": "Ingen supplerende varme",
}

BBR_STATUS = {
    "1": "Bygning under opførelse",
    "2": "Bygning færdigmeldt",
    "3": "Bygning nedrevet",
    "4": "Bygning nedbrændt",
    "5": "Bygning under sletning",
    "6": "Opført",
}

BBR_WATER_SUPPLY = {}
BBR_ASBEST_MATERIAL = {}


def translate_bbr_code(code, mapping):
    if code is None:
        return "Ikke oplyst i BBR-svar"

    code_str = str(code)

    if code_str in mapping:
        return mapping[code_str]

    return f"Ukendt/ikke oversat BBR-kode: {code_str}"


# -------------------------------------------------------
# Retning, afstand og små helpers
# -------------------------------------------------------

def parse_radius(value, default=250):
    try:
        return int(value)
    except Exception:
        return default


def direction_from_degrees(deg):
    if deg is None:
        return "Ikke verificeret"

    directions = [
        "nord", "nordøst", "øst", "sydøst",
        "syd", "sydvest", "vest", "nordvest"
    ]

    index = round(deg / 45) % 8
    return directions[index]


def opposite_direction_text(deg):
    if deg is None:
        return "Ikke verificeret"

    opposite = (deg + 180) % 360
    return direction_from_degrees(opposite)


def distance_meters(lat1, lon1, lat2, lon2):
    if None in [lat1, lon1, lat2, lon2]:
        return None

    radius_earth_m = 6371000

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return round(radius_earth_m * c)


def is_positive_text(value):
    if value is None:
        return False

    if not isinstance(value, str):
        return True

    bad_parts = [
        "Ikke verificeret",
        "Ikke oplyst",
        "Ukendt",
        "ikke fundet",
        "ikke koblet",
    ]

    return not any(part.lower() in value.lower() for part in bad_parts)


# -------------------------------------------------------
# Adresseopslag via DAWA
# -------------------------------------------------------

def lookup_address(address):
    url = "https://api.dataforsyningen.dk/adresser"

    params = {
        "q": address,
        "per_side": 1
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        results = response.json()

        if not results:
            return None

        item = results[0]

        adgangsadresse = item.get("adgangsadresse", {})
        adgangs_point = adgangsadresse.get("adgangspunkt", {})
        coords = adgangs_point.get("koordinater", None)

        longitude = coords[0] if coords else None
        latitude = coords[1] if coords else None

        kommune = adgangsadresse.get("kommune", {})
        postnummer = adgangsadresse.get("postnummer", {})
        vejstykke = adgangsadresse.get("vejstykke", {})
        matrikel = adgangsadresse.get("matrikel", {})
        ejerlav = matrikel.get("ejerlav", {}) if matrikel else {}

        return {
            "normalized_address": item.get("adressebetegnelse", address),
            "address_id": item.get("id"),
            "access_address_id": adgangsadresse.get("id"),

            "street_name": vejstykke.get("navn", "Ikke verificeret"),
            "street_code": vejstykke.get("kode"),
            "house_number": adgangsadresse.get("husnr", "Ikke verificeret"),
            "floor": item.get("etage", None),
            "door": item.get("dør", None),

            "municipality": kommune.get("navn", "Ikke verificeret"),
            "municipality_code": kommune.get("kode"),
            "postal_code": postnummer.get("nr", "Ikke verificeret"),
            "city": postnummer.get("navn", "Ikke verificeret"),

            "latitude": latitude,
            "longitude": longitude,

            "cadastre": {
                "matrikelnummer": matrikel.get("matrikelnummer") if matrikel else None,
                "ejerlav_navn": ejerlav.get("navn") if ejerlav else None,
                "ejerlav_kode": ejerlav.get("kode") if ejerlav else None,
                "status": "Ikke verificeret som indsatsdata"
            },

            "source": "Dataforsyningen/DAWA adresseopslag",
            "verification_status": "Adresse og koordinater forsøgt verificeret via Dataforsyningen/DAWA"
        }

    except Exception as e:
        return {
            "error": str(e)
        }


# -------------------------------------------------------
# Vejrdata via Open-Meteo
# -------------------------------------------------------

def get_weather(latitude, longitude):
    if latitude is None or longitude is None:
        return {
            "source": "Open-Meteo ikke forsøgt - mangler koordinater",
            "timestamp": datetime.now().isoformat(),
            "temperature_c": None,
            "wind_direction_degrees": None,
            "wind_direction_text": "Ikke verificeret",
            "wind_speed_ms": None,
            "wind_gust_ms": None,
            "precipitation": None,
            "smoke_direction_text": "Ikke verificeret",
            "tactical_note": "Vejr/vind kan ikke hentes uden koordinater",
            "error": "missing_coordinates",
            "debug": {
                "latitude": latitude,
                "longitude": longitude
            }
        }

    url = "https://api.open-meteo.com/v1/forecast"

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,precipitation,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
        "wind_speed_unit": "ms",
        "timezone": "Europe/Copenhagen"
    }

    try:
        response = requests.get(url, params=params, timeout=10)

        debug = {
            "url": url,
            "params": params,
            "status_code": response.status_code,
            "response_text_preview": response.text[:1000]
        }

        response.raise_for_status()

        try:
            result = response.json()
        except Exception as e:
            return {
                "source": "Open-Meteo",
                "timestamp": datetime.now().isoformat(),
                "temperature_c": None,
                "wind_direction_degrees": None,
                "wind_direction_text": "Ikke verificeret",
                "wind_speed_ms": None,
                "wind_gust_ms": None,
                "precipitation": None,
                "smoke_direction_text": "Ikke verificeret",
                "tactical_note": "Open-Meteo svarede, men JSON kunne ikke læses",
                "error": str(e),
                "debug": debug
            }

        current = result.get("current", {})
        debug["current_keys"] = list(current.keys()) if isinstance(current, dict) else []

        wind_deg = current.get("wind_direction_10m")
        wind_from_text = direction_from_degrees(wind_deg)
        smoke_to_text = opposite_direction_text(wind_deg)

        return {
            "source": "Open-Meteo testdata - bør senere erstattes eller suppleres med DMI",
            "timestamp": current.get("time", datetime.now().isoformat()),
            "temperature_c": current.get("temperature_2m"),
            "wind_direction_degrees": wind_deg,
            "wind_direction_text": f"Vind fra {wind_from_text}" if wind_from_text != "Ikke verificeret" else "Ikke verificeret",
            "wind_speed_ms": current.get("wind_speed_10m"),
            "wind_gust_ms": current.get("wind_gusts_10m"),
            "precipitation": current.get("precipitation"),
            "smoke_direction_text": f"Røg forventes at drive mod {smoke_to_text}" if smoke_to_text != "Ikke verificeret" else "Ikke verificeret",
            "tactical_note": f"Overvej opstilling på vindsiden. Røg kan påvirke området mod {smoke_to_text}." if smoke_to_text != "Ikke verificeret" else "Ikke verificeret",
            "error": None,
            "debug": debug
        }

    except Exception as e:
        return {
            "source": "Open-Meteo kunne ikke hentes",
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
            "temperature_c": None,
            "wind_direction_degrees": None,
            "wind_direction_text": "Ikke verificeret",
            "wind_speed_ms": None,
            "wind_gust_ms": None,
            "precipitation": None,
            "smoke_direction_text": "Ikke verificeret",
            "tactical_note": "Open-Meteo-data kunne ikke hentes",
            "debug": {
                "url": url,
                "params": params
            }
        }


# -------------------------------------------------------
# Brandhaner via OSM / Overpass
# -------------------------------------------------------

def get_possible_hydrants_from_osm(latitude, longitude, radius_m=250):
    if latitude is None or longitude is None:
        return {
            "source": "OpenStreetMap/Overpass ikke forsøgt - mangler koordinater",
            "hydrants": [],
            "hydrant_count": 0,
            "alternative_water": [],
            "verification_status": "Brandhaner/vandforsyning ikke verificeret"
        }

    overpass_urls = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.openstreetmap.ru/api/interpreter"
    ]

    query = f"""
    [out:json][timeout:25];
    (
      node["emergency"="fire_hydrant"](around:{int(radius_m)},{latitude},{longitude});
      way["emergency"="fire_hydrant"](around:{int(radius_m)},{latitude},{longitude});
      relation["emergency"="fire_hydrant"](around:{int(radius_m)},{latitude},{longitude});
    );
    out center tags 50;
    """

    attempts = []

    for overpass_url in overpass_urls:
        try:
            response = requests.get(
                overpass_url,
                params={"data": query},
                headers={
                    "User-Agent": "IndsatsBrief-Brand/1.0",
                    "Accept": "application/json"
                },
                timeout=30
            )

            attempts.append({
                "url": overpass_url,
                "status_code": response.status_code,
                "preview": response.text[:300]
            })

            response.raise_for_status()
            result = response.json()

            hydrants = []

            for element in result.get("elements", []):
                tags = element.get("tags", {}) or {}

                if element.get("type") == "node":
                    hydrant_lat = element.get("lat")
                    hydrant_lon = element.get("lon")
                else:
                    center = element.get("center", {}) or {}
                    hydrant_lat = center.get("lat")
                    hydrant_lon = center.get("lon")

                distance = distance_meters(latitude, longitude, hydrant_lat, hydrant_lon)

                hydrants.append({
                    "id": element.get("id"),
                    "osm_type": element.get("type"),
                    "latitude": hydrant_lat,
                    "longitude": hydrant_lon,
                    "distance_m": distance,
                    "map_url": f"https://www.openstreetmap.org/?mlat={hydrant_lat}&mlon={hydrant_lon}#map=19/{hydrant_lat}/{hydrant_lon}" if hydrant_lat and hydrant_lon else None,
                    "hydrant_type": tags.get("fire_hydrant:type"),
                    "position": tags.get("fire_hydrant:position"),
                    "diameter": tags.get("fire_hydrant:diameter"),
                    "pressure": tags.get("fire_hydrant:pressure"),
                    "ref": tags.get("ref"),
                    "operator": tags.get("operator"),
                    "raw_tags": tags,
                    "verification_status": "Mulig brandhane fra OpenStreetMap - ikke verificeret"
                })

            hydrants = sorted(
                hydrants,
                key=lambda h: h["distance_m"] if h["distance_m"] is not None else 999999
            )

            return {
                "source": "OpenStreetMap via Overpass API",
                "working_overpass_url": overpass_url,
                "query_radius_m": int(radius_m),
                "hydrants": hydrants,
                "hydrant_count": len(hydrants),
                "alternative_water": [],
                "note": "Brandhaner fra OpenStreetMap kan være ufuldstændige eller forkerte og må ikke betragtes som verificeret vandforsyning.",
                "verification_status": "Mulige brandhaner fundet via åben datakilde - ikke verificeret"
            }

        except Exception as e:
            attempts.append({
                "url": overpass_url,
                "error": str(e)
            })

    return {
        "source": "OpenStreetMap/Overpass API",
        "query_radius_m": int(radius_m),
        "hydrants": [],
        "hydrant_count": 0,
        "alternative_water": [],
        "attempts": attempts,
        "note": "Overpass/OSM kunne ikke hentes fra de testede servere. Brandhaner/vandforsyning er ikke verificeret.",
        "verification_status": "Brandhaner/vandforsyning ikke verificeret"
    }


# -------------------------------------------------------
# OSM risikotjek
# -------------------------------------------------------

def categorize_osm_risk(tags):
    categories = []

    if tags.get("generator:source") == "solar" or tags.get("plant:source") == "solar":
        categories.append({
            "category": "Muligt solcelleanlæg",
            "risk_level": "OBS",
            "note": "Registreret i OpenStreetMap som solenergi/solcelleanlæg - ikke verificeret"
        })

    if tags.get("power") in ["generator", "plant"] and (
        tags.get("generator:source") == "solar" or tags.get("plant:source") == "solar"
    ):
        categories.append({
            "category": "Muligt solcelle-/energianlæg",
            "risk_level": "OBS",
            "note": "Power-tag i OpenStreetMap indikerer mulig energiproduktion - ikke verificeret"
        })

    if tags.get("man_made") == "storage_tank":
        categories.append({
            "category": "Mulig tank/beholder",
            "risk_level": "OBS",
            "note": "Registreret i OpenStreetMap som tank/beholder - indhold ikke verificeret"
        })

    if tags.get("amenity") == "fuel":
        categories.append({
            "category": "Tankstation/brændstof",
            "risk_level": "Vigtig",
            "note": "Registreret i OpenStreetMap som tankstation/brændstofanlæg - ikke verificeret"
        })

    if tags.get("landuse") == "industrial":
        categories.append({
            "category": "Industriområde",
            "risk_level": "OBS",
            "note": "Registreret i OpenStreetMap som industriområde - konkret risiko ikke verificeret"
        })

    if tags.get("building") in ["industrial", "warehouse", "commercial", "retail"]:
        categories.append({
            "category": "Mulig erhvervs-/lagerbygning",
            "risk_level": "OBS",
            "note": "Bygningstype registreret i OpenStreetMap - ikke verificeret"
        })

    if tags.get("barrier") in ["gate", "bollard", "lift_gate"]:
        categories.append({
            "category": "Mulig adgangsbegrænsning",
            "risk_level": "OBS",
            "note": "Port/bom/adgangsbarriere registreret i OpenStreetMap - ikke verificeret"
        })

    if tags.get("access") in ["private", "no", "permissive"]:
        categories.append({
            "category": "Mulig adgangsbegrænsning",
            "risk_level": "OBS",
            "note": f"Access-tag i OpenStreetMap: {tags.get('access')} - skal verificeres"
        })

    if tags.get("railway"):
        categories.append({
            "category": "Jernbane/spor",
            "risk_level": "OBS",
            "note": "Jernbane/spor registreret i OpenStreetMap - afstand og adgang skal verificeres"
        })

    if tags.get("natural") == "water" or tags.get("waterway") or tags.get("landuse") == "reservoir":
        categories.append({
            "category": "Vand/sø/vandløb",
            "risk_level": "Mulig ressource/risiko",
            "note": "Vand registreret i OpenStreetMap - anvendelighed som vandforsyning ikke verificeret"
        })

    return categories


def build_osm_risk_summary(findings):
    summary = {}

    for finding in findings:
        distance = finding.get("distance_m")

        for category in finding.get("categories", []):
            name = category.get("category")
            note = category.get("note")
            risk_level = category.get("risk_level")

            if not name:
                continue

            if name not in summary:
                summary[name] = {
                    "category": name,
                    "count": 0,
                    "nearest_distance_m": None,
                    "risk_level": risk_level,
                    "note": note,
                    "verification_status": "OSM-data - ikke verificeret"
                }

            summary[name]["count"] += 1

            if distance is not None:
                current = summary[name]["nearest_distance_m"]
                if current is None or distance < current:
                    summary[name]["nearest_distance_m"] = distance

    return sorted(
        summary.values(),
        key=lambda item: item["nearest_distance_m"] if item["nearest_distance_m"] is not None else 999999
    )


def get_osm_risk_check(latitude, longitude, radius_m=250):
    if latitude is None or longitude is None:
        return {
            "source": "OpenStreetMap/Overpass ikke forsøgt - mangler koordinater",
            "query_radius_m": int(radius_m),
            "findings": [],
            "finding_count": 0,
            "grouped_summary": {},
            "osm_risk_summary": [],
            "verification_status": "OSM-risikotjek ikke verificeret"
        }

    overpass_urls = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.openstreetmap.ru/api/interpreter"
    ]

    query = f"""
    [out:json][timeout:25];
    (
      node(around:{int(radius_m)},{latitude},{longitude})["generator:source"="solar"];
      way(around:{int(radius_m)},{latitude},{longitude})["generator:source"="solar"];
      relation(around:{int(radius_m)},{latitude},{longitude})["generator:source"="solar"];

      node(around:{int(radius_m)},{latitude},{longitude})["plant:source"="solar"];
      way(around:{int(radius_m)},{latitude},{longitude})["plant:source"="solar"];
      relation(around:{int(radius_m)},{latitude},{longitude})["plant:source"="solar"];

      node(around:{int(radius_m)},{latitude},{longitude})["power"="generator"];
      way(around:{int(radius_m)},{latitude},{longitude})["power"="generator"];
      relation(around:{int(radius_m)},{latitude},{longitude})["power"="generator"];

      node(around:{int(radius_m)},{latitude},{longitude})["power"="plant"];
      way(around:{int(radius_m)},{latitude},{longitude})["power"="plant"];
      relation(around:{int(radius_m)},{latitude},{longitude})["power"="plant"];

      node(around:{int(radius_m)},{latitude},{longitude})["man_made"="storage_tank"];
      way(around:{int(radius_m)},{latitude},{longitude})["man_made"="storage_tank"];
      relation(around:{int(radius_m)},{latitude},{longitude})["man_made"="storage_tank"];

      node(around:{int(radius_m)},{latitude},{longitude})["amenity"="fuel"];
      way(around:{int(radius_m)},{latitude},{longitude})["amenity"="fuel"];
      relation(around:{int(radius_m)},{latitude},{longitude})["amenity"="fuel"];

      node(around:{int(radius_m)},{latitude},{longitude})["landuse"="industrial"];
      way(around:{int(radius_m)},{latitude},{longitude})["landuse"="industrial"];
      relation(around:{int(radius_m)},{latitude},{longitude})["landuse"="industrial"];

      node(around:{int(radius_m)},{latitude},{longitude})["building"="industrial"];
      way(around:{int(radius_m)},{latitude},{longitude})["building"="industrial"];
      relation(around:{int(radius_m)},{latitude},{longitude})["building"="industrial"];

      node(around:{int(radius_m)},{latitude},{longitude})["building"="warehouse"];
      way(around:{int(radius_m)},{latitude},{longitude})["building"="warehouse"];
      relation(around:{int(radius_m)},{latitude},{longitude})["building"="warehouse"];

      node(around:{int(radius_m)},{latitude},{longitude})["barrier"="gate"];
      way(around:{int(radius_m)},{latitude},{longitude})["barrier"="gate"];
      relation(around:{int(radius_m)},{latitude},{longitude})["barrier"="gate"];

      node(around:{int(radius_m)},{latitude},{longitude})["barrier"="lift_gate"];
      way(around:{int(radius_m)},{latitude},{longitude})["barrier"="lift_gate"];
      relation(around:{int(radius_m)},{latitude},{longitude})["barrier"="lift_gate"];

      node(around:{int(radius_m)},{latitude},{longitude})["access"="private"];
      way(around:{int(radius_m)},{latitude},{longitude})["access"="private"];
      relation(around:{int(radius_m)},{latitude},{longitude})["access"="private"];

      node(around:{int(radius_m)},{latitude},{longitude})["railway"];
      way(around:{int(radius_m)},{latitude},{longitude})["railway"];
      relation(around:{int(radius_m)},{latitude},{longitude})["railway"];

      node(around:{int(radius_m)},{latitude},{longitude})["natural"="water"];
      way(around:{int(radius_m)},{latitude},{longitude})["natural"="water"];
      relation(around:{int(radius_m)},{latitude},{longitude})["natural"="water"];

      node(around:{int(radius_m)},{latitude},{longitude})["waterway"];
      way(around:{int(radius_m)},{latitude},{longitude})["waterway"];
      relation(around:{int(radius_m)},{latitude},{longitude})["waterway"];
    );
    out center tags 100;
    """

    attempts = []

    for overpass_url in overpass_urls:
        try:
            response = requests.get(
                overpass_url,
                params={"data": query},
                headers={
                    "User-Agent": "IndsatsBrief-Brand/1.0",
                    "Accept": "application/json"
                },
                timeout=30
            )

            attempts.append({
                "url": overpass_url,
                "status_code": response.status_code,
                "preview": response.text[:300]
            })

            response.raise_for_status()
            result = response.json()

            findings = []

            for element in result.get("elements", []):
                tags = element.get("tags", {}) or {}
                categories = categorize_osm_risk(tags)

                if not categories:
                    continue

                if element.get("type") == "node":
                    item_lat = element.get("lat")
                    item_lon = element.get("lon")
                else:
                    center = element.get("center", {}) or {}
                    item_lat = center.get("lat")
                    item_lon = center.get("lon")

                distance = distance_meters(latitude, longitude, item_lat, item_lon)

                findings.append({
                    "id": element.get("id"),
                    "osm_type": element.get("type"),
                    "name": tags.get("name"),
                    "latitude": item_lat,
                    "longitude": item_lon,
                    "distance_m": distance,
                    "map_url": f"https://www.openstreetmap.org/?mlat={item_lat}&mlon={item_lon}#map=19/{item_lat}/{item_lon}" if item_lat and item_lon else None,
                    "categories": categories,
                    "raw_tags": tags,
                    "verification_status": "Fundet i OpenStreetMap - ikke verificeret"
                })

            findings = sorted(
                findings,
                key=lambda x: x["distance_m"] if x["distance_m"] is not None else 999999
            )

            grouped_summary = {}

            for finding in findings:
                for category in finding.get("categories", []):
                    name = category.get("category")
                    grouped_summary[name] = grouped_summary.get(name, 0) + 1

            return {
                "source": "OpenStreetMap via Overpass API",
                "working_overpass_url": overpass_url,
                "query_radius_m": int(radius_m),
                "finding_count": len(findings),
                "grouped_summary": grouped_summary,
                "osm_risk_summary": build_osm_risk_summary(findings),
                "findings": findings,
                "note": "OSM-data kan være ufuldstændige, forældede eller forkerte. Alle fund skal verificeres før operativ brug.",
                "verification_status": "Mulige risikoelementer fundet via åben datakilde - ikke verificeret"
            }

        except Exception as e:
            attempts.append({
                "url": overpass_url,
                "error": str(e)
            })

    return {
        "source": "OpenStreetMap/Overpass API",
        "query_radius_m": int(radius_m),
        "finding_count": 0,
        "grouped_summary": {},
        "osm_risk_summary": [],
        "findings": [],
        "attempts": attempts,
        "note": "OSM-risikotjek kunne ikke gennemføres via de testede Overpass-servere.",
        "verification_status": "OSM-risikotjek ikke verificeret"
    }


# -------------------------------------------------------
# Luftfoto / satellit / visuel risikotjekliste
# -------------------------------------------------------

def get_aerial_check(address_data, radius_m=250):
    if not address_data:
        return {
            "source": "Luftfoto/satellit ikke tilgængeligt",
            "status": "Adresse ikke fundet",
            "verification_status": "Ikke verificeret",
            "links": {},
            "possible_visual_risks_to_check": []
        }

    latitude = address_data.get("latitude")
    longitude = address_data.get("longitude")
    normalized_address = address_data.get("normalized_address", "Ikke verificeret")

    if latitude is None or longitude is None:
        return {
            "source": "Luftfoto/satellit ikke tilgængeligt",
            "status": "Mangler koordinater",
            "verification_status": "Ikke verificeret",
            "links": {},
            "possible_visual_risks_to_check": []
        }

    google_satellite_url = (
        f"https://www.google.com/maps/@{latitude},{longitude},19z/data=!3m1!1e3"
    )

    openstreetmap_url = (
        f"https://www.openstreetmap.org/?mlat={latitude}&mlon={longitude}"
        f"#map=19/{latitude}/{longitude}"
    )

    public_image_url = (
        f"https://indsatsbrief-api.onrender.com/aerial-image.jpg"
        f"?address={quote(normalized_address)}"
    )

    return {
        "source": "Visuel luftfoto-/satellitvurdering via eksterne kortlinks og forsøgt ortofoto",
        "status": "Klar til manuel visuel vurdering",
        "address": normalized_address,
        "latitude": latitude,
        "longitude": longitude,
        "radius_m": int(radius_m),

        "links": {
            "google_maps_satellite": google_satellite_url,
            "openstreetmap": openstreetmap_url,
            "attempted_ortofoto_image": public_image_url
        },

        "possible_visual_risks_to_check": [
            {
                "risk": "Solceller på tag",
                "look_for": "Mørke rektangulære paneler i rækker på tagflader",
                "operational_note": "Hvis muligt observeret: behandles som ikke verificeret indtil bekræftet på stedet eller via officiel kilde"
            },
            {
                "risk": "Oplag på grund",
                "look_for": "Paller, containere, affald, gasflasker, materialestakke eller udendørs lager",
                "operational_note": "Kan påvirke brandspredning, adgang og slukningsindsats"
            },
            {
                "risk": "Tanke eller beholdere",
                "look_for": "Cylindriske tanke, beholdere, tankgårde eller tekniske installationer",
                "operational_note": "Indhold er ikke verificeret og må ikke gættes"
            },
            {
                "risk": "Smal eller vanskelig adgang",
                "look_for": "Smal indkørsel, baggård, porte, bomme, ensrettede veje eller begrænset vendeplads",
                "operational_note": "Tilkørsel skal verificeres lokalt"
            },
            {
                "risk": "Indelukkede gårdrum",
                "look_for": "Bygninger omkring lukket gård eller svært tilgængelige bagsider",
                "operational_note": "Kan give lange slangeveje og vanskelig evakuering"
            },
            {
                "risk": "Spredningsrisiko til nabobygninger",
                "look_for": "Kort afstand mellem bygninger, sammenbyggede tage eller tæt gårdbebyggelse",
                "operational_note": "Visuel vurdering er ikke nok til endelig risikovurdering"
            },
            {
                "risk": "Tagtype og tagadgang",
                "look_for": "Fladt tag, skråt tag, store ovenlys, tagterrasser eller tekniske anlæg",
                "operational_note": "Tagets bæreevne og adgangsforhold er ikke verificeret"
            }
        ],

        "gpt_instruction": (
            "Brug links til manuel visuel vurdering. Skriv aldrig at solceller, tanke, oplag "
            "eller andre farer er verificeret ud fra luftfoto alene. Brug formuleringer som "
            "'muligt visuelt tegn på...' eller 'bør kontrolleres på stedet'."
        ),

        "verification_status": "Visuel vurdering mulig - ikke verificeret"
    }


# -------------------------------------------------------
# Datafordeler Ortofoto WMS, forsøgsmodul
# -------------------------------------------------------

def get_datafordeler_api_key():
    return os.getenv("DATAFORDELER_API_KEY")


def build_ortofoto_wms_url(latitude, longitude, width=900, height=900, bbox_degrees=0.0012):
    api_key = get_datafordeler_api_key()

    if not api_key:
        return {
            "status": "error",
            "message": "DATAFORDELER_API_KEY mangler som environment variable",
            "url": None
        }

    min_lon = longitude - bbox_degrees
    max_lon = longitude + bbox_degrees
    min_lat = latitude - bbox_degrees
    max_lat = latitude + bbox_degrees

    base_url = "https://services.datafordeler.dk/GeoDanmarkOrto/orto_foraar/1.0.0/WMS"

    params = {
        "apikey": api_key,
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetMap",
        "LAYERS": "orto_foraar",
        "STYLES": "",
        "CRS": "EPSG:4326",
        "BBOX": f"{min_lat},{min_lon},{max_lat},{max_lon}",
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "FORMAT": "image/jpeg",
        "TRANSPARENT": "FALSE"
    }

    prepared = requests.Request("GET", base_url, params=params).prepare()

    return {
        "status": "ready",
        "url": prepared.url,
        "bbox": {
            "min_lat": min_lat,
            "min_lon": min_lon,
            "max_lat": max_lat,
            "max_lon": max_lon
        },
        "crs": "EPSG:4326",
        "width": width,
        "height": height,
        "source": "GeoDanmark Ortofoto Forår WMS / Datafordeleren"
    }


def fetch_ortofoto_image(latitude, longitude):
    if latitude is None or longitude is None:
        return {
            "status": "error",
            "message": "Mangler koordinater",
            "verification_status": "Ortofoto ikke hentet"
        }

    wms = build_ortofoto_wms_url(latitude, longitude)

    if wms.get("status") != "ready":
        return wms

    try:
        response = requests.get(wms["url"], timeout=30)
        content_type = response.headers.get("Content-Type", "")

        result = {
            "status_code": response.status_code,
            "content_type": content_type,
            "source": wms.get("source"),
            "crs": wms.get("crs"),
            "bbox": wms.get("bbox"),
            "width": wms.get("width"),
            "height": wms.get("height"),
            "verification_status": "Ortofoto forsøgt hentet - ikke automatisk analyseret"
        }

        if response.status_code != 200:
            result["status"] = "error"
            result["message"] = "WMS returnerede ikke HTTP 200"
            result["response_preview"] = response.text[:1000]
            return result

        if "image" not in content_type.lower():
            result["status"] = "error"
            result["message"] = "WMS returnerede ikke et billede"
            result["response_preview"] = response.text[:1000]
            return result

        image_base64 = base64.b64encode(response.content).decode("utf-8")

        result["status"] = "success"
        result["image_base64_preview"] = image_base64[:500]
        result["image_size_bytes"] = len(response.content)
        result["note"] = (
            "Ortofoto blev hentet. API’en laver endnu ikke automatisk visuel analyse "
            "af solceller, tanke, oplag eller adgangsforhold."
        )

        return result

    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "source": wms.get("source"),
            "verification_status": "Ortofoto kunne ikke hentes"
        }


def get_ortofoto_image_bytes(latitude, longitude):
    wms = build_ortofoto_wms_url(latitude, longitude)

    if wms.get("status") != "ready":
        return None, "text/plain", wms.get("message", "WMS kunne ikke bygges")

    try:
        response = requests.get(wms["url"], timeout=30)
        content_type = response.headers.get("Content-Type", "text/plain")

        if response.status_code != 200:
            return None, "text/plain", response.text[:1000]

        if "image" not in content_type.lower():
            return None, "text/plain", response.text[:1000]

        return response.content, content_type, None

    except Exception as e:
        return None, "text/plain", str(e)


# -------------------------------------------------------
# BBR GraphQL
# -------------------------------------------------------

def current_graphql_time():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def call_bbr_graphql(query, variables=None):
    api_key = get_datafordeler_api_key()

    if not api_key:
        return {
            "status": "error",
            "message": "DATAFORDELER_API_KEY mangler som environment variable"
        }

    url = f"https://graphql.datafordeler.dk/BBR/v3?apikey={api_key}"

    try:
        response = requests.post(
            url,
            json={
                "query": query,
                "variables": variables or {}
            },
            timeout=25
        )

        try:
            response_json = response.json()
        except Exception:
            response_json = None

        return {
            "status": "ok",
            "status_code": response.status_code,
            "response_json": response_json,
            "response_text": response.text[:12000]
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }


def test_bbr_graphql_connection():
    query = """
    query {
      __typename
    }
    """

    return call_bbr_graphql(query)


def bbr_address_candidate_queries(access_address_id):
    now = current_graphql_time()

    return [
        {
            "name": "husnummer_string_eq",
            "query": """
            query($tid: DafDateTime!, $husnummerId: String!) {
              BBR_Bygning(
                first: 10,
                virkningstid: $tid,
                registreringstid: $tid,
                where: {
                  husnummer: { eq: $husnummerId }
                }
              ) {
                pageInfo {
                  endCursor
                  hasNextPage
                }
                nodes {
                  id_lokalId
                  id_namespace
                  husnummer
                  datafordelerRowId
                  kommunekode
                  byg007Bygningsnummer
                  byg021BygningensAnvendelse
                  byg026Opfoerelsesaar
                  byg027OmTilbygningsaar
                  byg030Vandforsyning
                  byg032YdervaeggensMateriale
                  byg033Tagdaekningsmateriale
                  byg036AsbestholdigtMateriale
                  byg038SamletBygningsareal
                  byg056Varmeinstallation
                  byg057Opvarmningsmiddel
                  byg058SupplerendeVarme
                  grund
                  jordstykke
                  status
                }
              }
            }
            """,
            "variables": {
                "tid": now,
                "husnummerId": access_address_id
            }
        }
    ]


def test_bbr_graphql_address(access_address_id):
    attempts = []

    if not access_address_id:
        return {
            "status": "error",
            "message": "Mangler access_address_id",
            "nodes": [],
            "attempts": attempts
        }

    for candidate in bbr_address_candidate_queries(access_address_id):
        result = call_bbr_graphql(candidate["query"], candidate["variables"])
        response_json = result.get("response_json") or {}
        errors = response_json.get("errors")
        data = response_json.get("data")

        nodes = []
        if data and data.get("BBR_Bygning"):
            nodes = data.get("BBR_Bygning", {}).get("nodes", []) or []

        attempt = {
            "name": candidate["name"],
            "status_code": result.get("status_code"),
            "errors": errors,
            "nodes_count": len(nodes),
            "data_preview": data,
            "response_text_preview": result.get("response_text", "")[:4000]
        }

        attempts.append(attempt)

        if result.get("status_code") == 200 and not errors and len(nodes) > 0:
            return {
                "status": "success",
                "working_candidate": candidate["name"],
                "nodes": nodes,
                "attempts": attempts
            }

        if result.get("status_code") == 200 and not errors:
            return {
                "status": "query_worked_but_no_nodes",
                "working_candidate": candidate["name"],
                "nodes": nodes,
                "attempts": attempts
            }

    return {
        "status": "no_candidate_worked",
        "nodes": [],
        "attempts": attempts
    }


def select_best_bbr_building(nodes):
    if not nodes:
        return None

    def score(node):
        points = 0

        if str(node.get("status")) == "6":
            points += 100

        if node.get("byg026Opfoerelsesaar") is not None:
            points += 10

        if node.get("byg038SamletBygningsareal") is not None:
            points += 10

        if node.get("byg021BygningensAnvendelse") is not None:
            points += 5

        return points

    return sorted(nodes, key=score, reverse=True)[0]


def normalize_secondary_bbr_building(node):
    usage_code = node.get("byg021BygningensAnvendelse")
    outer_wall_code = node.get("byg032YdervaeggensMateriale")
    roof_code = node.get("byg033Tagdaekningsmateriale")
    status_code = node.get("status")

    return {
        "usage": usage_code,
        "usage_text": translate_bbr_code(usage_code, BBR_BUILDING_USAGE),

        "building_type": usage_code,
        "building_type_text": translate_bbr_code(usage_code, BBR_BUILDING_USAGE),

        "construction_year": node.get("byg026Opfoerelsesaar"),
        "renovation_year": node.get("byg027OmTilbygningsaar"),
        "area_m2": node.get("byg038SamletBygningsareal"),

        "roof_material": roof_code,
        "roof_material_text": translate_bbr_code(roof_code, BBR_ROOF_MATERIAL),

        "outer_wall_material": outer_wall_code,
        "outer_wall_material_text": translate_bbr_code(outer_wall_code, BBR_OUTER_WALL_MATERIAL),

        "bbr_id": node.get("byg007Bygningsnummer"),
        "status": status_code,
        "status_text": translate_bbr_code(status_code, BBR_STATUS)
    }


def build_secondary_bbr_buildings(nodes, main_building):
    secondary_buildings = []
    main_identifier = (
        main_building.get("id_lokalId"),
        main_building.get("datafordelerRowId"),
        main_building.get("byg007Bygningsnummer")
    ) if main_building else None

    for node in nodes or []:
        node_identifier = (
            node.get("id_lokalId"),
            node.get("datafordelerRowId"),
            node.get("byg007Bygningsnummer")
        )

        if main_identifier and node_identifier == main_identifier:
            continue

        secondary_buildings.append(normalize_secondary_bbr_building(node))

    return secondary_buildings


def normalize_bbr_building_from_graphql(address_result, address_data):
    if not address_result or address_result.get("status") not in ["success", "query_worked_but_no_nodes"]:
        placeholder = get_building_placeholder(address_data)
        placeholder["source"] = "BBR GraphQL forsøgt, men ingen query-variant virkede"
        placeholder["bbr_graphql_status"] = address_result.get("status") if address_result else None
        return placeholder

    nodes = address_result.get("nodes") or []

    if not nodes:
        placeholder = get_building_placeholder(address_data)
        placeholder["source"] = "BBR GraphQL query virkede, men fandt ingen bygning på adressen"
        placeholder["verification_status"] = "BBR/bygningsdata ikke fundet"
        return placeholder

    building = select_best_bbr_building(nodes)
    secondary_buildings = build_secondary_bbr_buildings(nodes, building)

    usage_code = building.get("byg021BygningensAnvendelse")
    outer_wall_code = building.get("byg032YdervaeggensMateriale")
    roof_code = building.get("byg033Tagdaekningsmateriale")
    water_code = building.get("byg030Vandforsyning")
    asbestos_code = building.get("byg036AsbestholdigtMateriale")
    heating_installation_code = building.get("byg056Varmeinstallation")
    heating_fuel_code = building.get("byg057Opvarmningsmiddel")
    supplementary_heating_code = building.get("byg058SupplerendeVarme")
    status_code = building.get("status")

    return {
        "source": "BBR GraphQL via Datafordeleren",
        "working_candidate": address_result.get("working_candidate"),

        "bbr_id": building.get("byg007Bygningsnummer"),
        "id_lokalId": building.get("id_lokalId"),
        "id_namespace": building.get("id_namespace"),
        "datafordelerRowId": building.get("datafordelerRowId"),

        "access_address_id": address_data.get("access_address_id") if address_data else None,
        "address_id": address_data.get("address_id") if address_data else None,
        "husnummer": building.get("husnummer"),

        "municipality_code": building.get("kommunekode"),

        "usage": usage_code,
        "usage_text": translate_bbr_code(usage_code, BBR_BUILDING_USAGE),

        "building_type": usage_code,
        "building_type_text": translate_bbr_code(usage_code, BBR_BUILDING_USAGE),

        "construction_year": building.get("byg026Opfoerelsesaar"),
        "renovation_year": building.get("byg027OmTilbygningsaar"),
        "area_m2": building.get("byg038SamletBygningsareal"),

        "basement": "Ikke verificeret",

        "outer_wall_material": outer_wall_code,
        "outer_wall_material_text": translate_bbr_code(outer_wall_code, BBR_OUTER_WALL_MATERIAL),

        "roof_material": roof_code,
        "roof_material_text": translate_bbr_code(roof_code, BBR_ROOF_MATERIAL),

        "water_supply": water_code,
        "water_supply_text": translate_bbr_code(water_code, BBR_WATER_SUPPLY),

        "asbestos_material": asbestos_code,
        "asbestos_material_text": translate_bbr_code(asbestos_code, BBR_ASBEST_MATERIAL),

        "heating_installation": heating_installation_code,
        "heating_installation_text": translate_bbr_code(heating_installation_code, BBR_HEATING_INSTALLATION),

        "heating_fuel": heating_fuel_code,
        "heating_fuel_text": translate_bbr_code(heating_fuel_code, BBR_HEATING_FUEL),

        "supplementary_heating": supplementary_heating_code,
        "supplementary_heating_text": translate_bbr_code(supplementary_heating_code, BBR_SUPPLEMENTARY_HEATING),

        "ground": building.get("grund"),
        "cadastre_parcel": building.get("jordstykke"),

        "status": status_code,
        "status_text": translate_bbr_code(status_code, BBR_STATUS),

        "raw_bbr_building": building,
        "all_bbr_nodes_for_address": nodes,
        "secondary_buildings": secondary_buildings,

        "fire_relevant_notes": [
            "BBR-data er registerdata og skal vurderes kritisk ved indsats",
            "BBR-koder er oversat programmatisk, men bør verificeres ved kritisk indsats",
            "Kælder, ABA, nøgleboks, stigrør, solceller, gas/el og aktuelle adgangsforhold er ikke verificeret af denne query"
        ],

        "verification_status": "BBR/bygningsdata forsøgt hentet via Datafordeleren"
    }


# -------------------------------------------------------
# BBR fallback
# -------------------------------------------------------

def bbr_building_has_real_data(building_data):
    if not building_data:
        return False

    useful_fields = [
        "usage_text",
        "building_type_text",
        "construction_year",
        "renovation_year",
        "area_m2",
        "outer_wall_material_text",
        "roof_material_text",
        "heating_installation_text",
        "heating_fuel_text",
        "supplementary_heating_text",
        "asbestos_material_text",
    ]

    for field in useful_fields:
        value = building_data.get(field)

        if not is_positive_text(value):
            continue

        return True

    return False


def is_positive_report_value(value):
    if value is None:
        return False

    if isinstance(value, str):
        stripped = value.strip()

        if not stripped:
            return False

        bad_parts = [
            "Ikke verificeret",
            "Ikke oplyst",
            "Ukendt",
            "ikke fundet",
            "ikke tilgængeligt",
        ]

        return not any(part.lower() in stripped.lower() for part in bad_parts)

    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0

    return True


def add_positive_field(target, source, source_key, target_key=None):
    value = source.get(source_key) if source else None

    if is_positive_report_value(value):
        target[target_key or source_key] = value


def prune_positive_report_data(value):
    if isinstance(value, dict):
        cleaned = {}

        for key, item in value.items():
            pruned = prune_positive_report_data(item)

            if is_positive_report_value(pruned):
                cleaned[key] = pruned

        return cleaned

    if isinstance(value, list):
        cleaned = []

        for item in value:
            pruned = prune_positive_report_data(item)

            if is_positive_report_value(pruned):
                cleaned.append(pruned)

        return cleaned

    return value if is_positive_report_value(value) else None


def build_short_report_address(address, normalized_address, municipality, postal_code, city):
    address_section = {
        "requested_address": address,
        "matched_address": normalized_address,
        "municipality": municipality,
        "postal_code": postal_code,
        "city": city
    }

    if (
        is_positive_report_value(address)
        and is_positive_report_value(normalized_address)
        and normalize_match_text(address) != normalize_match_text(normalized_address)
    ):
        address_section["note"] = "Adresseopslag matchede nærmeste registrerede adresse"

    return prune_positive_report_data(address_section)


def build_short_report_building(building_data):
    building = {}

    text_linked_fields = [
        ("usage", "usage_text"),
        ("building_type", "building_type_text"),
        ("outer_wall_material", "outer_wall_material_text"),
        ("roof_material", "roof_material_text"),
        ("water_supply", "water_supply_text"),
        ("asbestos_material", "asbestos_material_text"),
        ("heating_installation", "heating_installation_text"),
        ("heating_fuel", "heating_fuel_text"),
        ("supplementary_heating", "supplementary_heating_text"),
        ("status", "status_text"),
    ]

    for code_key, text_key in text_linked_fields:
        text_value = building_data.get(text_key) if building_data else None

        if not is_positive_report_value(text_value):
            continue

        add_positive_field(building, building_data, code_key)
        building[text_key] = text_value

    for field in [
        "bbr_id",
        "construction_year",
        "renovation_year",
        "area_m2",
        "municipality_code",
        "husnummer",
    ]:
        add_positive_field(building, building_data, field)

    secondary_buildings = []

    for secondary in building_data.get("secondary_buildings", []) if building_data else []:
        normalized_secondary = {}

        for code_key, text_key in [
            ("usage", "usage_text"),
            ("building_type", "building_type_text"),
            ("outer_wall_material", "outer_wall_material_text"),
            ("roof_material", "roof_material_text"),
            ("status", "status_text"),
        ]:
            text_value = secondary.get(text_key)

            if not is_positive_report_value(text_value):
                continue

            add_positive_field(normalized_secondary, secondary, code_key)
            normalized_secondary[text_key] = text_value

        for field in [
            "bbr_id",
            "construction_year",
            "renovation_year",
            "area_m2",
        ]:
            add_positive_field(normalized_secondary, secondary, field)

        normalized_secondary = prune_positive_report_data(normalized_secondary)

        if normalized_secondary:
            secondary_buildings.append(normalized_secondary)

    if secondary_buildings:
        building["secondary_buildings"] = secondary_buildings

    return prune_positive_report_data(building)


def build_short_report_osm_summary(osm_risk_check_data):
    summary = []

    for item in osm_risk_check_data.get("osm_risk_summary", []) if osm_risk_check_data else []:
        cleaned_item = {}

        for field in [
            "category",
            "count",
            "nearest_distance_m",
            "risk_level",
        ]:
            add_positive_field(cleaned_item, item, field)

        cleaned_item = prune_positive_report_data(cleaned_item)

        if cleaned_item:
            summary.append(cleaned_item)

    return summary


def build_short_report_weather(weather_data):
    if not weather_data or weather_data.get("error"):
        return {}

    weather = {}

    for field in [
        "temperature_c",
        "wind_direction_degrees",
        "wind_direction_text",
        "wind_speed_ms",
        "wind_gust_ms",
        "precipitation",
        "smoke_direction_text",
    ]:
        add_positive_field(weather, weather_data, field)

    return prune_positive_report_data(weather)


def build_short_report_data(
    address,
    normalized_address,
    municipality,
    postal_code,
    city,
    latitude,
    longitude,
    map_url,
    aerial_check_data,
    building_data,
    osm_risk_check_data,
    weather_data
):
    short_report_data = {
        "address": build_short_report_address(
            address,
            normalized_address,
            municipality,
            postal_code,
            city
        )
    }

    coordinates = prune_positive_report_data({
        "latitude": latitude,
        "longitude": longitude
    })

    if coordinates:
        short_report_data["coordinates"] = coordinates

    if is_positive_report_value(map_url):
        short_report_data["map_url"] = map_url

    google_maps_satellite = (
        aerial_check_data.get("links", {}).get("google_maps_satellite")
        if aerial_check_data else None
    )

    if is_positive_report_value(google_maps_satellite):
        short_report_data["google_maps_satellite"] = google_maps_satellite

    building = build_short_report_building(building_data)

    if building:
        short_report_data["building"] = building

    osm_risk_summary = build_short_report_osm_summary(osm_risk_check_data)

    if osm_risk_summary:
        short_report_data["osm_risk_summary"] = osm_risk_summary

    weather = build_short_report_weather(weather_data)

    if weather:
        short_report_data["weather"] = weather

    return prune_positive_report_data(short_report_data)


def get_base_house_number(house_number):
    if not house_number:
        return None

    match = re.match(r"^(\d+)", str(house_number).strip())

    if not match:
        return None

    return match.group(1)


def normalize_match_text(value):
    if value is None:
        return None

    return re.sub(r"\s+", " ", str(value).strip().lower())


def normalize_dawa_access_address(item, source_address_data=None):
    if not item:
        return None

    adgangsadresse = item.get("adgangsadresse") or item
    adgangs_point = adgangsadresse.get("adgangspunkt", {}) or {}
    coords = adgangs_point.get("koordinater", None)

    longitude = coords[0] if coords else None
    latitude = coords[1] if coords else None

    kommune = adgangsadresse.get("kommune", {}) or {}
    postnummer = adgangsadresse.get("postnummer", {}) or {}
    vejstykke = adgangsadresse.get("vejstykke", {}) or {}
    matrikel = adgangsadresse.get("matrikel", {}) or {}
    ejerlav = matrikel.get("ejerlav", {}) if matrikel else {}

    return {
        "normalized_address": (
            item.get("adressebetegnelse")
            or adgangsadresse.get("adressebetegnelse")
            or f"{vejstykke.get('navn', '')} {adgangsadresse.get('husnr', '')}".strip()
        ),
        "address_id": item.get("id") if item.get("adgangsadresse") else None,
        "access_address_id": adgangsadresse.get("id"),

        "street_name": vejstykke.get("navn", "Ikke verificeret"),
        "street_code": vejstykke.get("kode"),
        "house_number": adgangsadresse.get("husnr", "Ikke verificeret"),
        "floor": None,
        "door": None,

        "municipality": kommune.get("navn", "Ikke verificeret"),
        "municipality_code": kommune.get("kode"),
        "postal_code": postnummer.get("nr", "Ikke verificeret"),
        "city": postnummer.get("navn", "Ikke verificeret"),

        "latitude": latitude,
        "longitude": longitude,
        "distance_m": distance_meters(
            source_address_data.get("latitude") if source_address_data else None,
            source_address_data.get("longitude") if source_address_data else None,
            latitude,
            longitude
        ),

        "cadastre": {
            "matrikelnummer": matrikel.get("matrikelnummer") if matrikel else None,
            "ejerlav_navn": ejerlav.get("navn") if ejerlav else None,
            "ejerlav_kode": ejerlav.get("kode") if ejerlav else None,
            "status": "Ikke verificeret som indsatsdata"
        },

        "source": "Dataforsyningen/DAWA adgangsadresseopslag",
        "verification_status": "Nærliggende adgangsadresse forsøgt verificeret via Dataforsyningen/DAWA"
    }


def get_nearby_dawa_access_addresses(address_data, radius_m=75, per_side=50):
    if not address_data:
        return {
            "status": "skipped",
            "message": "Mangler adressegrundlag",
            "addresses": []
        }

    latitude = address_data.get("latitude")
    longitude = address_data.get("longitude")

    if latitude is None or longitude is None:
        return {
            "status": "skipped",
            "message": "Mangler koordinater til nærliggende DAWA-adgangsadresser",
            "addresses": []
        }

    params = {
        "cirkel": f"{longitude},{latitude},{int(radius_m)}",
        "per_side": int(per_side)
    }

    try:
        response = requests.get(
            "https://api.dataforsyningen.dk/adgangsadresser",
            params=params,
            timeout=10
        )
        response.raise_for_status()
        results = response.json()

        addresses = []
        for item in results:
            candidate = normalize_dawa_access_address(item, address_data)
            if candidate:
                addresses.append(candidate)

        addresses = sorted(
            addresses,
            key=lambda candidate: (
                candidate.get("distance_m") is None,
                candidate.get("distance_m") if candidate.get("distance_m") is not None else 999999
            )
        )

        return {
            "status": "success",
            "radius_m": int(radius_m),
            "per_side": int(per_side),
            "count": len(addresses),
            "addresses": addresses
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "radius_m": int(radius_m),
            "per_side": int(per_side),
            "addresses": []
        }


def build_nearby_bbr_fallback_candidates(address_data, radius_m=75, per_side=50):
    nearby_result = get_nearby_dawa_access_addresses(address_data, radius_m, per_side)
    original_street_name = normalize_match_text(address_data.get("street_name"))
    original_base_house_number = get_base_house_number(address_data.get("house_number"))

    candidates = []
    skipped = []

    for candidate in nearby_result.get("addresses") or []:
        candidate_street_name = normalize_match_text(candidate.get("street_name"))
        candidate_base_house_number = get_base_house_number(candidate.get("house_number"))

        skip_reason = None

        if original_street_name and candidate_street_name != original_street_name:
            skip_reason = "different_street_name"
        elif original_base_house_number and candidate_base_house_number != original_base_house_number:
            skip_reason = "different_base_house_number"

        candidate_debug = {
            "matched_address": candidate.get("normalized_address"),
            "access_address_id": candidate.get("access_address_id"),
            "street_name": candidate.get("street_name"),
            "house_number": candidate.get("house_number"),
            "base_house_number": candidate_base_house_number,
            "distance_m": candidate.get("distance_m")
        }

        if skip_reason:
            candidate_debug["skip_reason"] = skip_reason
            skipped.append(candidate_debug)
            continue

        candidates.append({
            "label": "nearby_dawa_access_address",
            "address_data": candidate,
            "debug": candidate_debug
        })

    return {
        "nearby_lookup": {
            "status": nearby_result.get("status"),
            "message": nearby_result.get("message"),
            "radius_m": nearby_result.get("radius_m"),
            "per_side": nearby_result.get("per_side"),
            "count": nearby_result.get("count", 0)
        },
        "filter": {
            "street_name": address_data.get("street_name"),
            "base_house_number": original_base_house_number
        },
        "candidates": candidates,
        "skipped": skipped
    }


def get_bbr_with_fallback(address_data):
    if not address_data:
        return get_building_placeholder(address_data)

    attempts = []

    original_access_address_id = address_data.get("access_address_id")

    if original_access_address_id:
        original_result = test_bbr_graphql_address(original_access_address_id)
        original_building = normalize_bbr_building_from_graphql(original_result, address_data)

        attempts.append({
            "label": "original_access_address_id",
            "query": address_data.get("normalized_address"),
            "access_address_id": original_access_address_id,
            "bbr_status": original_result.get("status"),
            "nodes_count": len(original_result.get("nodes") or []),
            "bbr_attempts": original_result.get("attempts", [])
        })

        if bbr_building_has_real_data(original_building):
            original_building["bbr_fallback"] = {
                "used": False,
                "matched_on": "original_access_address_id",
                "attempts": attempts
            }
            return original_building

    seen_access_ids = set()
    if original_access_address_id:
        seen_access_ids.add(original_access_address_id)

    fallback_candidates = build_nearby_bbr_fallback_candidates(address_data)

    attempts.append({
        "label": "nearby_dawa_access_addresses",
        "address_lookup_status": fallback_candidates["nearby_lookup"].get("status"),
        "nearby_lookup": fallback_candidates["nearby_lookup"],
        "filter": fallback_candidates["filter"],
        "skipped_candidates": fallback_candidates["skipped"],
        "candidate_count": len(fallback_candidates["candidates"])
    })

    for candidate in fallback_candidates["candidates"]:
        candidate_address_data = candidate["address_data"]
        candidate_access_address_id = candidate_address_data.get("access_address_id")
        candidate_debug = candidate["debug"]

        if not candidate_access_address_id:
            attempts.append({
                "label": candidate["label"],
                "address_lookup_status": "no_access_address_id",
                "candidate": candidate_debug
            })
            continue

        if candidate_access_address_id in seen_access_ids:
            attempts.append({
                "label": candidate["label"],
                "address_lookup_status": "duplicate_access_address_id",
                "access_address_id": candidate_access_address_id,
                "candidate": candidate_debug
            })
            continue

        seen_access_ids.add(candidate_access_address_id)

        bbr_result = test_bbr_graphql_address(candidate_access_address_id)
        building = normalize_bbr_building_from_graphql(bbr_result, candidate_address_data)

        attempts.append({
            "label": candidate["label"],
            "matched_address": candidate_address_data.get("normalized_address"),
            "access_address_id": candidate_access_address_id,
            "distance_m": candidate_address_data.get("distance_m"),
            "bbr_status": bbr_result.get("status"),
            "nodes_count": len(bbr_result.get("nodes") or []),
            "has_real_bbr_data": bbr_building_has_real_data(building),
            "bbr_attempts": bbr_result.get("attempts", [])
        })

        if bbr_building_has_real_data(building):
            building["bbr_fallback"] = {
                "used": True,
                "matched": True,
                "matched_on": candidate["label"],
                "original_address": address_data.get("normalized_address"),
                "matched_address": candidate_address_data.get("normalized_address"),
                "matched_access_address_id": candidate_access_address_id,
                "matched_distance_m": candidate_address_data.get("distance_m"),
                "attempts": attempts,
                "note": "BBR-data er fundet via fallback-adresse. Skal verificeres ved operativ brug."
            }

            building["verification_status"] = (
                "BBR/bygningsdata fundet via fallback-adresse - skal verificeres"
            )

            return building

    placeholder = get_building_placeholder(address_data)
    placeholder["source"] = "BBR GraphQL fallback prøvet, men ingen bygning fundet"
    placeholder["bbr_fallback"] = {
        "used": True,
        "matched": False,
        "attempts": attempts
    }
    placeholder["verification_status"] = "BBR/bygningsdata ikke fundet"

    return placeholder


# -------------------------------------------------------
# Placeholders
# -------------------------------------------------------

def get_building_placeholder(address_data):
    return {
        "source": "BBR ikke fundet",
        "bbr_id": None,
        "access_address_id": address_data.get("access_address_id") if address_data else None,
        "address_id": address_data.get("address_id") if address_data else None,

        "usage": "Ikke verificeret",
        "usage_text": "Ikke verificeret",

        "building_type": "Ikke verificeret",
        "building_type_text": "Ikke verificeret",

        "construction_year": None,
        "renovation_year": None,
        "area_m2": None,
        "basement": "Ikke verificeret",

        "roof_material": "Ikke verificeret",
        "roof_material_text": "Ikke verificeret",

        "outer_wall_material": "Ikke verificeret",
        "outer_wall_material_text": "Ikke verificeret",

        "water_supply": "Ikke verificeret",
        "water_supply_text": "Ikke verificeret",

        "asbestos_material": "Ikke verificeret",
        "asbestos_material_text": "Ikke verificeret",

        "heating_installation": "Ikke verificeret",
        "heating_installation_text": "Ikke verificeret",

        "heating_fuel": "Ikke verificeret",
        "heating_fuel_text": "Ikke verificeret",

        "supplementary_heating": "Ikke verificeret",
        "supplementary_heating_text": "Ikke verificeret",

        "secondary_buildings": [],

        "technical_installations": [],

        "fire_relevant_notes": [
            "BBR/bygningsdata blev ikke fundet",
            "Bygningstype, areal, etager, kælder, tag og tekniske anlæg skal verificeres i BBR/beredskabets egne systemer"
        ],

        "verification_status": "BBR/bygningsdata ikke verificeret"
    }


def get_road_placeholder(address_data):
    street_name = address_data.get("street_name", "Ikke verificeret") if address_data else "Ikke verificeret"
    house_number = address_data.get("house_number", "Ikke verificeret") if address_data else "Ikke verificeret"

    return {
        "source": "Vejdata/trafikdata ikke koblet på endnu",

        "street_name": street_name,
        "house_number": house_number,
        "roadworks": [],
        "traffic_events": [],
        "closures": [],
        "access_notes": [],

        "preliminary_access_assessment": [
            f"Adresse ligger på/ved {street_name} {house_number}" if street_name != "Ikke verificeret" else "Vejnavn ikke verificeret",
            "Tilkørsel, spærringer, ensretning, bomme, bredde og opstillingsmuligheder er ikke verificeret"
        ],

        "verification_status": "Vejdata/trafikale forhold ikke verificeret"
    }


# -------------------------------------------------------
# Flask routes
# -------------------------------------------------------

@app.route("/")
def home():
    return "IndsatsBrief API kører"


@app.route("/privacy", methods=["GET"])
def privacy_policy():
    html = """
    <!DOCTYPE html>
    <html lang="da">
    <head>
        <meta charset="UTF-8">
        <title>Privacy Policy - IndsatsBrief Brand</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 850px;
                margin: 40px auto;
                padding: 0 20px;
                line-height: 1.6;
                color: #222;
            }
            h1, h2 {
                color: #111;
            }
            .note {
                background: #f4f4f4;
                padding: 12px;
                border-left: 4px solid #555;
            }
        </style>
    </head>
    <body>
        <h1>Privacy Policy for IndsatsBrief Brand</h1>

        <p><strong>Last updated:</strong> 23 June 2026</p>

        <p>
            IndsatsBrief Brand is a custom GPT and API service that helps generate
            incident briefing information based on address-related public and operationally
            relevant data.
        </p>

        <h2>What data is sent to the API</h2>
        <p>
            When a user asks for an incident brief, the GPT may send the provided address
            and selected radius to the IndsatsBrief API.
        </p>

        <p>The API may use the address to retrieve:</p>
        <ul>
            <li>Address and coordinate data</li>
            <li>Map links</li>
            <li>Weather and wind data</li>
            <li>BBR/building register data</li>
            <li>Possible fire hydrant data from open sources, if available</li>
            <li>OpenStreetMap risk tags near the address, if available</li>
            <li>External map links and attempted ortofoto image links for manual visual assessment</li>
        </ul>

        <h2>What data is not intentionally collected</h2>
        <p>
            The API does not intentionally collect names, phone numbers, CPR numbers,
            private login information, payment information, or user account credentials.
        </p>

        <h2>Logging</h2>
        <p>
            The hosting provider may process standard technical logs such as request time,
            endpoint, IP address, and error logs for operation, security, and troubleshooting.
        </p>

        <h2>Data sharing and third-party services</h2>
        <p>
            Data may be processed by third-party services used to provide the response,
            including Dataforsyningen/DAWA, Datafordeleren/BBR, Open-Meteo,
            OpenStreetMap/Overpass, Google Maps links, and Render hosting.
        </p>

        <h2>Purpose</h2>
        <p>
            The data is used only to generate incident briefing information, operate the
            service, and troubleshoot the API.
        </p>

        <h2>Important limitation</h2>
        <div class="note">
            This service is decision support only. It does not replace verified emergency
            services systems, local procedures, incident commander assessment, GIS,
            object plans, official databases, or local operational knowledge.
        </div>

        <h2>Contact</h2>
        <p>
            For questions about this API or privacy policy, contact:<br>
            <strong>Frederik Racher</strong>
        </p>
    </body>
    </html>
    """

    return html


@app.route("/test-bbr", methods=["GET"])
def test_bbr():
    result = test_bbr_graphql_connection()
    status_code = result.get("status_code", 500)

    if result.get("status") == "error":
        return jsonify(result), 500

    return jsonify(result), status_code


@app.route("/test-bbr-graphql-address", methods=["GET"])
def test_bbr_graphql_address_route():
    address = request.args.get("address", "")

    if not address:
        return jsonify({
            "status": "error",
            "message": "Mangler address parameter"
        }), 400

    address_data = lookup_address(address)

    if not address_data or "error" in address_data:
        return jsonify({
            "status": "error",
            "message": "Adresse kunne ikke slås op",
            "address_lookup": address_data
        }), 400

    access_address_id = address_data.get("access_address_id")
    bbr_address_result = test_bbr_graphql_address(access_address_id)
    normalized_building = normalize_bbr_building_from_graphql(bbr_address_result, address_data)
    fallback_building = get_bbr_with_fallback(address_data)

    heating_debug_nodes = []
    for node in bbr_address_result.get("nodes") or []:
        heating_debug_nodes.append({
            "id_lokalId": node.get("id_lokalId"),
            "byg056Varmeinstallation": node.get("byg056Varmeinstallation"),
            "byg057Opvarmningsmiddel": node.get("byg057Opvarmningsmiddel"),
            "byg058SupplerendeVarme": node.get("byg058SupplerendeVarme")
        })

    return jsonify({
        "address_data": address_data,
        "bbr_address_result": bbr_address_result,
        "normalized_building": normalized_building,
        "fallback_building": fallback_building,
        "fallback_debug": fallback_building.get("bbr_fallback", {}),
        "heating_debug": {
            "original_nodes": heating_debug_nodes,
            "normalized": {
                "heating_installation": normalized_building.get("heating_installation"),
                "heating_installation_text": normalized_building.get("heating_installation_text"),
                "heating_fuel": normalized_building.get("heating_fuel"),
                "heating_fuel_text": normalized_building.get("heating_fuel_text"),
                "supplementary_heating": normalized_building.get("supplementary_heating"),
                "supplementary_heating_text": normalized_building.get("supplementary_heating_text")
            },
            "fallback": {
                "heating_installation": fallback_building.get("heating_installation"),
                "heating_installation_text": fallback_building.get("heating_installation_text"),
                "heating_fuel": fallback_building.get("heating_fuel"),
                "heating_fuel_text": fallback_building.get("heating_fuel_text"),
                "supplementary_heating": fallback_building.get("supplementary_heating"),
                "supplementary_heating_text": fallback_building.get("supplementary_heating_text")
            }
        }
    })


@app.route("/test-hydrants", methods=["GET"])
def test_hydrants():
    address = request.args.get("address", "")
    radius_m = parse_radius(request.args.get("radius_m", 250), 250)

    if not address:
        return jsonify({
            "status": "error",
            "message": "Mangler address parameter"
        }), 400

    address_data = lookup_address(address)

    if not address_data or "error" in address_data:
        return jsonify({
            "status": "error",
            "message": "Adresse kunne ikke slås op",
            "address_lookup": address_data
        }), 400

    hydrants = get_possible_hydrants_from_osm(
        address_data.get("latitude"),
        address_data.get("longitude"),
        radius_m
    )

    return jsonify({
        "address_data": address_data,
        "water_supply": hydrants
    })


@app.route("/osm-risk-check", methods=["GET"])
def osm_risk_check_route():
    address = request.args.get("address", "")
    radius_m = parse_radius(request.args.get("radius_m", 250), 250)

    if not address:
        return jsonify({
            "status": "error",
            "message": "Mangler address parameter"
        }), 400

    address_data = lookup_address(address)

    if not address_data or "error" in address_data:
        return jsonify({
            "status": "error",
            "message": "Adresse kunne ikke slås op",
            "address_lookup": address_data
        }), 400

    osm_risk_check = get_osm_risk_check(
        address_data.get("latitude"),
        address_data.get("longitude"),
        radius_m
    )

    return jsonify({
        "address_data": address_data,
        "osm_risk_check": osm_risk_check
    })


@app.route("/aerial-check", methods=["GET"])
def aerial_check_route():
    address = request.args.get("address", "")
    radius_m = parse_radius(request.args.get("radius_m", 250), 250)

    if not address:
        return jsonify({
            "status": "error",
            "message": "Mangler address parameter"
        }), 400

    address_data = lookup_address(address)

    if not address_data or "error" in address_data:
        return jsonify({
            "status": "error",
            "message": "Adresse kunne ikke slås op",
            "address_lookup": address_data
        }), 400

    aerial_check = get_aerial_check(address_data, radius_m)

    return jsonify({
        "address_data": address_data,
        "aerial_check": aerial_check
    })


@app.route("/aerial-image", methods=["GET"])
def aerial_image_route():
    address = request.args.get("address", "")

    if not address:
        return jsonify({
            "status": "error",
            "message": "Mangler address parameter"
        }), 400

    address_data = lookup_address(address)

    if not address_data or "error" in address_data:
        return jsonify({
            "status": "error",
            "message": "Adresse kunne ikke slås op",
            "address_lookup": address_data
        }), 400

    latitude = address_data.get("latitude")
    longitude = address_data.get("longitude")

    ortofoto_result = fetch_ortofoto_image(latitude, longitude)

    public_image_url = (
        f"https://indsatsbrief-api.onrender.com/aerial-image.jpg"
        f"?address={quote(address_data.get('normalized_address', address))}"
    )

    return jsonify({
        "address_data": address_data,
        "ortofoto": ortofoto_result,
        "public_image_url": public_image_url,
        "visual_analysis": {
            "status": "not_performed",
            "note": "Automatisk analyse af solceller, tanke, oplag mv. er ikke bygget endnu.",
            "verification_status": "Ingen visuel konklusion"
        }
    })


@app.route("/aerial-image.jpg", methods=["GET"])
def aerial_image_jpg_route():
    address = request.args.get("address", "")

    if not address:
        return Response("Mangler address parameter", status=400, mimetype="text/plain")

    address_data = lookup_address(address)

    if not address_data or "error" in address_data:
        return Response("Adresse kunne ikke slås op", status=400, mimetype="text/plain")

    latitude = address_data.get("latitude")
    longitude = address_data.get("longitude")

    image_bytes, content_type, error = get_ortofoto_image_bytes(latitude, longitude)

    if error or not image_bytes:
        return Response(
            f"Ortofoto kunne ikke hentes: {error}",
            status=502,
            mimetype="text/plain"
        )

    return Response(image_bytes, status=200, mimetype=content_type)


@app.route("/hazmat", methods=["GET"])
def hazmat_lookup():
    query = request.args.get("query", "").strip()

    if not query:
        return jsonify({
            "status": "error",
            "message": "Mangler query parameter. Brug fx /hazmat?query=UN1203 eller /hazmat?query=benzin"
        }), 400

    cleaned_query = query.upper().replace(" ", "")

    if cleaned_query.startswith("UN"):
        search_text = cleaned_query
        un_number = cleaned_query.replace("UN", "")
    elif cleaned_query.isdigit():
        search_text = f"UN{cleaned_query}"
        un_number = cleaned_query
    else:
        search_text = query
        un_number = None

    return jsonify({
        "query": query,
        "search_text": search_text,
        "un_number": un_number,

        "official_lookup": {
            "source": "Beredskabsstyrelsen Kemikalieberedskab",
            "url": "https://kemikalieberedskab.dk/",
            "instruction": f"Åbn linket og søg på: {search_text}"
        },

        "app_lookup": {
            "source": "Beredskabsstyrelsens app",
            "name": "Farlige stoffer",
            "instruction": f"Brug samme søgetekst i appen: {search_text}"
        },

        "safety_note": [
            "Denne API gengiver ikke kemikaliedata direkte fra opslagsværket.",
            "Brug Kemikalieberedskab.dk, appen Farlige stoffer, ADR-oplysninger, sikkerhedsdatablad eller Kemisk Beredskab til verificeret indsatsinformation.",
            "Gæt aldrig indsatsafstand, evakueringsafstand, slukningsmiddel, reaktionsfare eller værnemidler ud fra uverificerede data."
        ],

        "verification_status": "Officielt opslag påkrævet før brug ved indsats"
    })


@app.route("/incident-brief", methods=["GET"])
def incident_brief():
    address = request.args.get("address", "")
    radius_m = parse_radius(request.args.get("radius_m", 250), 250)

    if not address:
        return jsonify({"error": "Missing address"}), 400

    address_data = lookup_address(address)

    if address_data and "error" not in address_data:
        normalized_address = address_data["normalized_address"]
        municipality = address_data["municipality"]
        postal_code = address_data["postal_code"]
        city = address_data["city"]
        latitude = address_data["latitude"]
        longitude = address_data["longitude"]
    else:
        address_data = {}
        normalized_address = address
        municipality = "Ikke verificeret"
        postal_code = "Ikke verificeret"
        city = "Ikke verificeret"
        latitude = None
        longitude = None

    if latitude and longitude:
        map_url = f"https://www.openstreetmap.org/?mlat={latitude}&mlon={longitude}#map=18/{latitude}/{longitude}"
    else:
        map_url = None

    weather_data = get_weather(latitude, longitude)

    if not isinstance(weather_data, dict):
        weather_data = {
            "source": "Open-Meteo ikke forsøgt",
            "timestamp": datetime.now().isoformat(),
            "temperature_c": None,
            "wind_direction_degrees": None,
            "wind_direction_text": "Ikke verificeret",
            "wind_speed_ms": None,
            "wind_gust_ms": None,
            "precipitation": None,
            "smoke_direction_text": "Ikke verificeret",
            "tactical_note": "Vejrdata kunne ikke bygges",
            "error": "invalid_weather_response",
            "debug": {
                "weather_response_type": str(type(weather_data))
            }
        }

    if address_data and address_data.get("access_address_id"):
        building_data = get_bbr_with_fallback(address_data)
    else:
        building_data = get_building_placeholder(address_data)

    water_supply_data = get_possible_hydrants_from_osm(latitude, longitude, radius_m)
    aerial_check_data = get_aerial_check(address_data, radius_m)
    osm_risk_check_data = get_osm_risk_check(latitude, longitude, radius_m)
    short_report_data = build_short_report_data(
        address,
        normalized_address,
        municipality,
        postal_code,
        city,
        latitude,
        longitude,
        map_url,
        aerial_check_data,
        building_data,
        osm_risk_check_data,
        weather_data
    )

    data = {
        "normalized_address": normalized_address,
        "municipality": municipality,
        "postal_code": postal_code,
        "city": city,
        "latitude": latitude,
        "longitude": longitude,

        "address_details": address_data,

        "map": {
            "map_url": map_url,
            "image_url": None,
            "radius_m": int(radius_m),
            "source": "OpenStreetMap-link genereret ud fra koordinater",
            "timestamp": datetime.now().isoformat()
        },

        "aerial_photo": aerial_check_data,
        "short_report_data": short_report_data,

        "weather": weather_data,
        "building": building_data,
        "road": get_road_placeholder(address_data),
        "water_supply": water_supply_data,
        "osm_risk_check": osm_risk_check_data,

        "utilities": {
            "gas": "Ikke verificeret",
            "electricity": "Ikke verificeret",
            "note": "Gas/el/forsyning skal verificeres via relevante systemer",
            "verification_status": "Ikke verificeret"
        },

        "hazmat": {
            "source": "Farlige stoffer kan slås op via /hazmat?query=UN1203 eller /hazmat?query=benzin",
            "official_lookup": "https://kemikalieberedskab.dk/",
            "verification_status": "Farlige stoffer ikke automatisk verificeret i incident-brief"
        },

        "local_risk_notes": [],

        "limitations": [
            "Adresse og koordinater forsøgt hentet via Dataforsyningen/DAWA",
            "Vejnavn og husnummer forsøgt hentet via Dataforsyningen/DAWA",
            "Kortlink genereret via OpenStreetMap",
            "Vejr/vind forsøgt hentet via Open-Meteo testintegration",
            "BBR GraphQL forsøgt via adgangsadresse-id og fallback-adresser",
            "BBR-koder er oversat programmatisk, men bør verificeres ved kritisk indsats",
            "Mulige brandhaner forsøgt hentet fra OpenStreetMap/Overpass, men er ikke verificeret",
            "OSM-risikotjek er baseret på åbne OpenStreetMap-tags og må ikke betragtes som verificeret indsatsdata",
            "Luftfoto/satellitlinks og ortofoto er kun til manuel visuel vurdering og må ikke betragtes som verificeret indsatsdata",
            "Solceller, tanke, oplag, adgangsforhold og andre visuelle farer må kun omtales som mulige, ikke verificerede observationer",
            "Vejdata/trafikhændelser er strukturelt klargjort, men ikke koblet på endnu",
            "Gas og el er ikke verificeret",
            "Farlige stoffer skal verificeres via Kemikalieberedskab.dk, appen Farlige stoffer, ADR/SDS eller Kemisk Beredskab"
        ]
    }

    return jsonify(data)


if __name__ == "__main__":
    app.run(debug=True)
