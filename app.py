from flask import Flask, request, jsonify
from datetime import datetime, timezone
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
# Datafordeler / BBR GraphQL
# -------------------------------------------------------

def get_datafordeler_api_key():
    return os.getenv("DATAFORDELER_API_KEY")


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


def test_bbr_graphql_sample():
    now = current_graphql_time()

    query = """
    query($tid: DafDateTime!) {
      BBR_Bygning(
        first: 5,
        virkningstid: $tid,
        registreringstid: $tid
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
          grund
          jordstykke
          status
        }
      }
    }
    """

    return call_bbr_graphql(query, {"tid": now})


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
        },
        {
            "name": "id_lokalId_string_eq",
            "query": """
            query($tid: DafDateTime!, $id: String!) {
              BBR_Bygning(
                first: 10,
                virkningstid: $tid,
                registreringstid: $tid,
                where: {
                  id_lokalId: { eq: $id }
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
                  grund
                  jordstykke
                  status
                }
              }
            }
            """,
            "variables": {
                "tid": now,
                "id": access_address_id
            }
        }
    ]


def test_bbr_graphql_address(access_address_id):
    attempts = []

    if not access_address_id:
        return {
            "status": "error",
            "message": "Mangler access_address_id"
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
        "attempts": attempts
    }


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

    building = nodes[0]

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

        "usage": building.get("byg021BygningensAnvendelse", "Ikke verificeret"),
        "building_type": building.get("byg021BygningensAnvendelse", "Ikke verificeret"),
        "construction_year": building.get("byg026Opfoerelsesaar"),
        "renovation_year": building.get("byg027OmTilbygningsaar"),

        "area_m2": building.get("byg038SamletBygningsareal"),

        "basement": "Ikke verificeret",
        "roof_material": building.get("byg033Tagdaekningsmateriale", "Ikke verificeret"),
        "outer_wall_material": building.get("byg032YdervaeggensMateriale", "Ikke verificeret"),
        "water_supply": building.get("byg030Vandforsyning", "Ikke verificeret"),
        "asbestos_material": building.get("byg036AsbestholdigtMateriale", "Ikke verificeret"),

        "ground": building.get("grund"),
        "cadastre_parcel": building.get("jordstykke"),
        "status": building.get("status"),

        "raw_bbr_building": building,

        "fire_relevant_notes": [
            "BBR-data er registerdata og skal vurderes kritisk ved indsats",
            "BBR-koder er ikke oversat endnu",
            "Kælder, ABA, nøgleboks, stigrør, solceller, gas/el og aktuelle adgangsforhold er ikke verificeret af denne query"
        ],

        "verification_status": "BBR/bygningsdata forsøgt hentet via Datafordeleren"
    }


# -------------------------------------------------------
# BBR placeholder
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
        "renovation_year": None,
        "area_m2": None,
        "residential_area_m2": None,
        "business_area_m2": None,
        "built_area_m2": None,
        "floors": None,
        "basement": "Ikke verificeret",
        "roof_material": "Ikke verificeret",
        "outer_wall_material": "Ikke verificeret",
        "water_supply": "Ikke verificeret",
        "asbestos_material": "Ikke verificeret",
        "heating_installation": "Ikke verificeret",
        "technical_installations": [],

        "fire_relevant_notes": [
            "BBR/bygningsdata er ikke koblet på incident-brief endnu",
            "Bygningstype, areal, etager, kælder, tag og tekniske anlæg skal verificeres i BBR/beredskabets egne systemer"
        ],

        "verification_status": "BBR/bygningsdata ikke verificeret"
    }


# -------------------------------------------------------
# Vejdata placeholder
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


@app.route("/test-bbr-graphql-sample", methods=["GET"])
def test_bbr_graphql_sample_route():
    result = test_bbr_graphql_sample()
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

    return jsonify({
        "address_data": address_data,
        "bbr_address_result": bbr_address_result,
        "normalized_building": normalized_building
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

    if address_data and address_data.get("access_address_id"):
        bbr_address_result = test_bbr_graphql_address(address_data.get("access_address_id"))
        building_data = normalize_bbr_building_from_graphql(bbr_address_result, address_data)
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
            "BBR GraphQL forsøgt koblet via husnummer/adgangsadresse-id",
            "BBR-koder er endnu ikke oversat til tekst",
            "Vejdata/trafikhændelser er strukturelt klargjort, men ikke koblet på endnu",
            "Brandhaner, gas og el er ikke verificeret"
        ]
    }

    return jsonify(data)


if __name__ == "__main__":
    app.run(debug=True)
