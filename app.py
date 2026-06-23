from flask import Flask, request, jsonify
from datetime import datetime
import requests
import os

app = Flask(__name__)
app.json.ensure_ascii = False


# -------------------------------------------------------
# Retningshjælp
# -------------------------------------------------------

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


# -------------------------------------------------------
# Adresseopslag via Dataforsyningen/DAWA
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
# Vejrdata via Open-Meteo testintegration
# -------------------------------------------------------

def get_weather(latitude, longitude):
    if latitude is None or longitude is None:
        return None

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
        response.raise_for_status()
        result = response.json()

        current = result.get("current", {})

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
            "tactical_note": f"Overvej opstilling på vindsiden. Røg kan påvirke området mod {smoke_to_text}." if smoke_to_text != "Ikke verificeret" else "Ikke verificeret"
        }

    except Exception as e:
        return {
            "source": "Vejrdata kunne ikke hentes",
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
            "temperature_c": None,
            "wind_direction_degrees": None,
            "wind_direction_text": "Ikke verificeret",
            "wind_speed_ms": None,
            "wind_gust_ms": None,
            "precipitation": "Ikke verificeret",
            "smoke_direction_text": "Ikke verificeret",
            "tactical_note": "Live vejrdata ikke tilgængeligt i denne rapport"
        }


# -------------------------------------------------------
# BBR GraphQL
# -------------------------------------------------------

def get_datafordeler_api_key():
    return os.getenv("DATAFORDELER_API_KEY")


def call_bbr_graphql(query, variables=None):
    api_key = get_datafordeler_api_key()

    if not api_key:
        return {
            "status": "error",
            "message": "DATAFORDELER_API_KEY mangler som environment variable"
        }

    url = f"https://graphql.datafordeler.dk/BBR/v3?apiKey={api_key}"

    try:
        response = requests.post(
            url,
            json={
                "query": query,
                "variables": variables or {}
            },
            timeout=20
        )

        return {
            "status": "ok",
            "status_code": response.status_code,
            "response_json": response.json() if response.headers.get("content-type", "").startswith("application/json") else None,
            "response_text": response.text[:5000]
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


def test_bbr_bygning_query(access_address_id):
    if not access_address_id:
        return {
            "status": "error",
            "message": "Mangler access_address_id fra adresseopslag"
        }

    query = """
    query($id: UUID!) {
      Bygning(
        where: {
          adgangsadresseid: { eq: $id }
        }
      ) {
        nodes {
          byg007Bygningsnummer
          byg021BygningensAnvendelse
          byg026Opfoerelsesaar
          byg030Vandforsyning
          byg031Afloebsforhold
          byg032YdervaeggenesMateriale
          byg033Tagdaekningsmateriale
          byg038SamletBygningsareal
          byg039BygningensSamledeBoligAreal
          byg041BebyggetAreal
          byg054AntalEtager
        }
      }
    }
    """

    variables = {
        "id": access_address_id
    }

    return call_bbr_graphql(query, variables)


def normalize_bbr_building(bbr_result, address_data):
    if not bbr_result or bbr_result.get("status") == "error":
        return get_building_placeholder(address_data)

    response_json = bbr_result.get("response_json") or {}

    if response_json.get("errors"):
        return {
            **get_building_placeholder(address_data),
            "source": "BBR GraphQL forsøgt, men query gav fejl",
            "bbr_query_error": response_json.get("errors"),
            "verification_status": "BBR/bygningsdata ikke verificeret"
        }

    nodes = (
        response_json
        .get("data", {})
        .get("Bygning", {})
        .get("nodes", [])
    )

    if not nodes:
        return {
            **get_building_placeholder(address_data),
            "source": "BBR GraphQL svarede, men fandt ingen bygninger på adgangsadressen",
            "verification_status": "BBR/bygningsdata ikke fundet"
        }

    building = nodes[0]

    return {
        "source": "BBR GraphQL via Datafordeleren",
        "bbr_id": building.get("byg007Bygningsnummer"),
        "access_address_id": address_data.get("access_address_id") if address_data else None,
        "address_id": address_data.get("address_id") if address_data else None,

        "usage": building.get("byg021BygningensAnvendelse", "Ikke verificeret"),
        "building_type": building.get("byg021BygningensAnvendelse", "Ikke verificeret"),
        "construction_year": building.get("byg026Opfoerelsesaar"),
        "area_m2": building.get("byg038SamletBygningsareal"),
        "residential_area_m2": building.get("byg039BygningensSamledeBoligAreal"),
        "built_area_m2": building.get("byg041BebyggetAreal"),
        "floors": building.get("byg054AntalEtager"),
        "basement": "Ikke verificeret",
        "roof_material": building.get("byg033Tagdaekningsmateriale", "Ikke verificeret"),
        "outer_wall_material": building.get("byg032YdervaeggenesMateriale", "Ikke verificeret"),
        "water_supply": building.get("byg030Vandforsyning", "Ikke verificeret"),
        "drainage": building.get("byg031Afloebsforhold", "Ikke verificeret"),
        "heating_installation": "Ikke verificeret",
        "technical_installations": [],

        "raw_bbr_building": building,

        "fire_relevant_notes": [
            "BBR-data er registerdata og skal vurderes kritisk ved indsats",
            "Kælder, tekniske anlæg, ABA, nøgleboks, stigrør og aktuelle adgangsforhold er ikke verificeret af denne BBR-query"
        ],

        "verification_status": "BBR/bygningsdata forsøgt hentet via Datafordeleren"
    }


# -------------------------------------------------------
# BBR-klar placeholder
# -------------------------------------------------------

def get_building_placeholder(address_data):
    return {
        "source": "BBR ikke koblet på incident-brief endnu",
        "bbr_id": None,
        "access_address_id": address_data.get("access_address_id") if address_data else None,
        "address_id": address_data.get("address_id") if address_data else None,

        "usage": "Ikke verificeret",
        "building_type": "Ikke verificeret",
        "construction_year": None,
        "area_m2": None,
        "residential_area_m2": None,
        "built_area_m2": None,
        "floors": None,
        "basement": "Ikke verificeret",
        "roof_material": "Ikke verificeret",
        "outer_wall_material": "Ikke verificeret",
        "water_supply": "Ikke verificeret",
        "drainage": "Ikke verificeret",
        "heating_installation": "Ikke verificeret",
        "technical_installations": [],

        "fire_relevant_notes": [
            "BBR/bygningsdata er ikke koblet på incident-brief endnu",
            "Bygningstype, areal, etager, kælder, tag og tekniske anlæg skal verificeres i BBR/beredskabets egne systemer"
        ],

        "verification_status": "BBR/bygningsdata ikke verificeret"
    }


# -------------------------------------------------------
# Vejdata-klar placeholder
# -------------------------------------------------------

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


@app.route("/test-bbr", methods=["GET"])
def test_bbr():
    result = test_bbr_graphql_connection()
    status_code = result.get("status_code", 500)

    if result.get("status") == "error":
        return jsonify(result), 500

    return jsonify(result), status_code


@app.route("/test-bbr-address", methods=["GET"])
def test_bbr_address():
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
    bbr_result = test_bbr_bygning_query(access_address_id)

    return jsonify({
        "address_data": address_data,
        "bbr_test": bbr_result,
        "normalized_building": normalize_bbr_building(bbr_result, address_data)
    })


@app.route("/incident-brief", methods=["GET"])
def incident_brief():
    address = request.args.get("address", "")
    radius_m = request.args.get("radius_m", 250)

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

    if not weather_data:
        weather_data = {
            "source": "Ikke tilgængeligt i denne version",
            "timestamp": datetime.now().isoformat(),
            "temperature_c": None,
            "wind_direction_degrees": None,
            "wind_direction_text": "Ikke verificeret",
            "wind_speed_ms": None,
            "wind_gust_ms": None,
            "precipitation": "Ikke verificeret",
            "smoke_direction_text": "Ikke verificeret",
            "tactical_note": "Live vejrdata ikke tilgængeligt i denne rapport"
        }

    bbr_result = None

    if address_data and address_data.get("access_address_id"):
        bbr_result = test_bbr_bygning_query(address_data.get("access_address_id"))
        building_data = normalize_bbr_building(bbr_result, address_data)
    else:
        building_data = get_building_placeholder(address_data)

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

        "aerial_photo": {
            "image_url": None,
            "source": "Ikke tilgængeligt i denne version",
            "year": None,
            "note": "Luftfoto/ortofoto ikke hentet i denne version"
        },

        "weather": weather_data,

        "building": building_data,

        "road": get_road_placeholder(address_data),

        "water_supply": {
            "source": "Ikke tilgængeligt",
            "hydrants": [],
            "alternative_water": [],
            "verification_status": "Brandhaner/vandforsyning ikke verificeret"
        },

        "utilities": {
            "gas": "Ikke verificeret",
            "electricity": "Ikke verificeret",
            "note": "Gas/el/forsyning skal verificeres via relevante systemer",
            "verification_status": "Ikke verificeret"
        },

        "local_risk_notes": [],

        "limitations": [
            "Adresse og koordinater forsøgt hentet via Dataforsyningen/DAWA",
            "Vejnavn og husnummer forsøgt hentet via Dataforsyningen/DAWA",
            "Kortlink genereret via OpenStreetMap",
            "Vejr/vind forsøgt hentet via Open-Meteo testintegration",
            "BBR-bygningsdata forsøgt hentet via BBR GraphQL/Bygning",
            "Vejdata/trafikhændelser er strukturelt klargjort, men ikke koblet på endnu",
            "Brandhaner, gas og el er ikke verificeret"
        ]
    }

    return jsonify(data)


if __name__ == "__main__":
    app.run(debug=True)
