from flask import Flask, request, jsonify
from datetime import datetime, timezone
import requests
import os

app = Flask(__name__)
app.json.ensure_ascii = False


# -------------------------------------------------------
# BBR kodelister
# -------------------------------------------------------

BBR_BUILDING_USAGE = {
    # Bygninger til helårsbeboelse
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

    # Landbrug
    "211": "Stald til svin",
    "212": "Stald til kvæg, får mv.",
    "213": "Stald til fjerkræ",
    "214": "Minkhal",
    "215": "Væksthus",
    "216": "Lade til foder, afgrøder mv.",
    "217": "Maskinhus, garage mv.",
    "218": "Lade til halm, hø mv.",
    "219": "Anden bygning til landbrug mv.",

    # Produktion
    "221": "Bygning til industri med integreret produktionsapparat",
    "222": "Bygning til industri uden integreret produktionsapparat",
    "223": "Værksted",
    "229": "Anden bygning til produktion",

    # Energi/forsyning
    "231": "Bygning til energiproduktion",
    "232": "Bygning til energidistribution",
    "233": "Bygning til vandforsyning",
    "234": "Bygning til håndtering af affald og spildevand",
    "239": "Anden bygning til energiproduktion og forsyning",

    # Transport/parkering
    "311": "Bygning til jernbane- og busdrift",
    "312": "Bygning til luftfart",
    "313": "Bygning til parkering- og transportanlæg",
    "314": "Bygning til parkering af flere end to køretøjer i tilknytning til boliger",
    "315": "Havneanlæg",
    "319": "Andet transportanlæg",

    # Kontor, handel og lager
    "321": "Bygning til kontor",
    "322": "Bygning til detailhandel",
    "323": "Bygning til lager",
    "324": "Butikscenter",
    "325": "Tankstation",
    "329": "Anden bygning til kontor, handel og lager",

    # Hotel, restaurant og service
    "331": "Hotel, kro eller konferencecenter med overnatning",
    "332": "Bed & breakfast mv.",
    "333": "Restaurant, café og konferencecenter uden overnatning",
    "334": "Privat servicevirksomhed som frisør, vaskeri, netcafé mv.",
    "339": "Anden bygning til serviceerhverv",

    # Kultur mv.
    "411": "Biograf, teater, koncertsted mv.",
    "412": "Museum",
    "413": "Bibliotek",
    "414": "Kirke eller anden bygning til trosudøvelse",
    "415": "Forsamlingshus",
    "416": "Forlystelsespark",
    "419": "Anden bygning til kulturelle formål",

    # Undervisning og forskning
    "421": "Grundskole",
    "422": "Universitet",
    "429": "Anden bygning til undervisning og forskning",

    # Sygehus og sundhed
    "431": "Hospital og sygehus",
    "432": "Hospice, behandlingshjem mv.",
    "433": "Sundhedscenter, lægehus, fødeklinik mv.",
    "439": "Anden bygning til sundhedsformål",

    # Institution
    "441": "Daginstitution",
    "442": "Servicefunktion på døgninstitution",
    "443": "Kaserne",
    "444": "Fængsel, arresthus mv.",
    "449": "Anden bygning til institutionsformål",
    "451": "Beskyttelsesrum",

    # Ferie
    "510": "Sommerhus",
    "521": "Feriecenter, center til campingplads mv.",
    "522": "Bygning med ferielejligheder til erhvervsmæssig udlejning",
    "523": "Bygning med ferielejligheder til eget brug",
    "529": "Anden bygning til ferieformål",

    # Sport
    "531": "Klubhus i forbindelse med fritid og idræt",
    "532": "Svømmehal",
    "533": "Idrætshal",
    "534": "Tribune i forbindelse med stadion",
    "535": "Bygning til træning og opstaldning af heste",
    "539": "Anden bygning til idrætsformål",

    # Fritid
    "540": "Kolonihavehus",
    "585": "Anneks i tilknytning til fritids- og sommerhus",
    "590": "Anden bygning til fritidsformål",

    # Mindre bygninger
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

BBR_HEATING_INSTALLATION = {
    "1": "Fjernvarme/blokvarme",
    "2": "Centralvarme med én fyringsenhed",
    "3": "Ovn til fast og flydende brændsel",
    "5": "Varmepumpe",
    "6": "Centralvarme med to fyringsenheder",
    "7": "Elvarme",
    "8": "Gasradiator",
    "9": "Ingen varmeinstallation",
    "99": "Blandet",
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

BBR_SUPPLEMENTARY_HEATING = {
    "0": "Ikke oplyst",
    "1": "Varmepumpe",
    "2": "Brændeovne og lignende med skorsten",
    "3": "Biopejse og lignende uden skorsten",
    "4": "Solpaneler",
    "5": "Pejs",
    "6": "Gasradiator",
    "7": "Elvarme",
    "10": "Biogasanlæg",
    "80": "Andet",
    "90": "(UDFASES) Bygningen har ingen supplerende varme",
}

BBR_WATER_SUPPLY = {
    # Vi fylder denne ud senere, når vi har den rigtige kodeliste.
}

BBR_ASBEST_MATERIAL = {
    # Vi fylder denne ud senere, når vi har den rigtige kodeliste.
}

BBR_STATUS = {
    # Vi mangler stadig den præcise BBR-statuskodeliste.
    # Derfor oversætter vi ikke status endnu.
}


def translate_bbr_code(code, mapping):
    if code is None:
        return "Ikke oplyst i BBR-svar"

    code_str = str(code)

    if code_str in mapping:
        return mapping[code_str]

    return f"Ukendt/ikke oversat BBR-kode: {code_str}"


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
          byg057Opvarmningsmiddel
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
                  byg057Opvarmningsmiddel
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


def select_best_bbr_building(nodes):
    if not nodes:
        return None

    def score(node):
        points = 0

        # Status 6 ser ud til at være den aktive/brugbare i vores testdata.
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

    usage_code = building.get("byg021BygningensAnvendelse")
    outer_wall_code = building.get("byg032YdervaeggensMateriale")
    roof_code = building.get("byg033Tagdaekningsmateriale")
    water_code = building.get("byg030Vandforsyning")
    asbestos_code = building.get("byg036AsbestholdigtMateriale")
    heating_fuel_code = building.get("byg057Opvarmningsmiddel")
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

        "heating_fuel": heating_fuel_code,
        "heating_fuel_text": translate_bbr_code(heating_fuel_code, BBR_HEATING_FUEL),

        "ground": building.get("grund"),
        "cadastre_parcel": building.get("jordstykke"),

        "status": status_code,
        "status_text": translate_bbr_code(status_code, BBR_STATUS),

        "raw_bbr_building": building,

        "all_bbr_nodes_for_address": nodes,

        "fire_relevant_notes": [
            "BBR-data er registerdata og skal vurderes kritisk ved indsats",
            "BBR-koder er oversat programmatisk, men bør verificeres ved kritisk indsats",
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
        "usage_text": "Ikke verificeret",

        "building_type": "Ikke verificeret",
        "building_type_text": "Ikke verificeret",

        "construction_year": None,
        "renovation_year": None,
        "area_m2": None,
        "residential_area_m2": None,
        "business_area_m2": None,
        "built_area_m2": None,
        "floors": None,

        "basement": "Ikke verificeret",

        "roof_material": "Ikke verificeret",
        "roof_material_text": "Ikke verificeret",

        "outer_wall_material": "Ikke verificeret",
        "outer_wall_material_text": "Ikke verificeret",

        "water_supply": "Ikke verificeret",
        "water_supply_text": "Ikke verificeret",

        "asbestos_material": "Ikke verificeret",
        "asbestos_material_text": "Ikke verificeret",

        "heating_fuel": "Ikke verificeret",
        "heating_fuel_text": "Ikke verificeret",

        "heating_installation": "Ikke verificeret",
        "heating_installation_text": "Ikke verificeret",

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
            "BBR GraphQL koblet via husnummer/adgangsadresse-id",
            "BBR-koder er oversat programmatisk, men bør verificeres ved kritisk indsats",
            "BBR-status, vandforsyning og asbest-koder er endnu ikke fuldt oversat",
            "Vejdata/trafikhændelser er strukturelt klargjort, men ikke koblet på endnu",
            "Brandhaner, gas og el er ikke verificeret"
        ]
    }

    return jsonify(data)


if __name__ == "__main__":
    app.run(debug=True)
