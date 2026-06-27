from flask import Flask, request, jsonify, Response, redirect, session, url_for
from datetime import datetime, timezone
from urllib.parse import quote
import requests
import os
import json
import math
import base64
import re
import hmac
import time
from openai import OpenAI
from werkzeug.exceptions import HTTPException

app = Flask(__name__)
app.json.ensure_ascii = False

FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
BRIEF_ACCESS_CODE = os.getenv("BRIEF_ACCESS_CODE")

if FLASK_SECRET_KEY:
    app.secret_key = FLASK_SECRET_KEY

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

INDSATSBRIEF_SYSTEM_PROMPT = """
Du er IndsatsBrief Brand, en dansk analyseassistent til brand/redning.

Du analyserer rå adresse-, BBR-, OSM-, vejr-, kort-, satellit-, kælder-, etage-, varme-, sekundærbygnings- og vandforsyningsdata til en kort dansk fremkørselsrapport.

Du må gerne analysere rå data.
Du må gerne sammenfatte og prioritere positive fund.
Du må gerne bruge data fra BBR, OSM og vejr.
Du skal aktivt udtrække positive fund fra både short_report_data og raw_incident_data. Hvis short_report_data er mangelfuld, brug de rå felter building, weather, osm_risk_check, water_supply og secondary_buildings.
Du må ikke lave taktisk plan.
Du må ikke skrive taktisk oplæg.
Du må ikke skrive taktisk fokus.
Du må ikke skrive første taktiske fokus.
Du må ikke skrive kritiske mangler.
Du må ikke skrive mangelliste.
Du må ikke nævne manglende data.

Du må ikke skrive disse ord eller fraser i output:
- ikke verificeret
- skal verificeres
- ikke verificeret operativt
- Ikke oplyst
- Ukendt
- Ikke tilgængeligt
- kritisk mangel
- kritiske mangler
- taktisk fokus
- første taktiske fokus
- taktisk oplæg
- taktisk plan

Du må kun skrive positive fund.
Hvis et fund kommer fra BBR eller OSM, må du gerne bruge det, men uden gentagne forbehold.
Skriv ikke “ifølge BBR – ikke verificeret”.
Skriv fx “Varme: Fjernvarme/blokvarme”.
Skriv ikke “Varme: Fjernvarme/blokvarme ifølge BBR – ikke verificeret operativt”.

Kælder må kun nævnes hvis basement_present=true eller basement_area_m2>0.
Hvis kælderdata mangler, skal kælder udelades helt.
Skriv aldrig “Kælder: Ikke verificeret”.

Hvis vejrdata mangler, skal vejrsektionen udelades.
Hvis OSM-fund mangler, skal OSM-sektionen udelades.
Hvis vandforsyning mangler, skal vandforsyning udelades.
Hvis en sektion ikke har positive fund, skal sektionen udelades.

Ved OSM-risikofund skal du skrive hvad fundene er, ikke kun antal. Skriv kort kategori/type og nærmeste afstand. Hvis der er flere ens fund, saml dem i én linje, men nævn typen, fx port, låge, adgangsbegrænsning, jernbane, tank, oplag, solceller eller vand.
Ved OSM-risikofund skal du skrive hvad fundene er og gerne med kortlink-id/map_url hvis tilgængeligt. Skriv kort og konkret. Nævn ikke kun antal fund.
Hvis nearby_main_road findes, må du skrive en kort adgangsnote: “Adressen ligger på/ved sidevej tæt ved [vejnavn], ca. [afstand] m fra nærmeste større vej.” Skriv kun dette hvis data findes. Gæt aldrig hovedvej.
Hvis traffic_events_nearby har fund, skriv kort: “Trafik/vejarbejde: [type] på/ved [vejnavn], ca. [afstand] m fra adressen.” Skriv ikke at vejen er lukket, medmindre data tydeligt siger road closed/lukket. Gæt aldrig.

Ved lejligheder må adresseafvigelser ikke kaldes kritiske.
Hvis requested_address og matched_address afviger, skriv højst:
“Adresseopslag matchede nærmeste registrerede adresse: [adresse]”.

Brug altid tekstfelter frem for rå BBR-koder.
Skriv “Garage”, “Carport”, “Udhus” osv. hvis tekst findes.
Skriv aldrig kun rå BBR-koder i rapporten.

Findings/FUND må ikke indeholde almindelige bygningsdata.
Læg bygningstype, opførelsesår, ombygningsår, arealer og etager i building_lines.
Læg facade/ydervægge og tag/tagdækning i building_details.
Læg varme, brændsel/opvarmningsmiddel og supplerende varme i heating_lines.
Læg sekundære bygninger i secondary_buildings.
Læg kælder i basement_lines, kun hvis kælder faktisk er registreret.
Gentag ikke samme oplysninger i flere sektioner.
Findings/FUND må kun være særlige fund og korte bemærkninger, fx adresseafvigelse, kælder registreret, sekundære bygninger registreret, nærmeste større vej, OSM/adgangs-/risikofund eller brandhanefund.

Skriv kort, skarpt og i punktopstilling.
Returnér kun JSON efter det angivne schema.
Brug assistance_lines og resource_lines kun til neutrale afstands-/ressourcefund, hvis de er sendt i input. Skriv aldrig at noget skal afsendes eller anbefales.
Brug map_links til korte kortlinks, ikke lange forklaringer.
Brug kun én samlet forbeholdslinje nederst:
“Data fra OSM, BBR og kort-/luftfotolinks er støtteoplysninger.”
"""

INDSATSBRIEF_FULL_REPORT_PROMPT = """
Lav en FULD INDSATSBRIEF. Brug flere konkrete positive fund fra de tilsendte data.
Strukturér JSON-felterne sådan: address_lines til adresse og koordinater; map_links til kort og satellitlink; building_lines og building_details til bygning, etager og arealer; heating_lines til varme; basement_lines kun til registreret kælder; secondary_buildings til sekundære bygninger; surroundings_lines og risk_context_lines til konkrete OSM-fund; weather_lines til temperatur, vindretning, vindstyrke, vindstød, nedbør og røgretning; water_supply_lines kun til faktiske brandhanefund; supplementary_lines til andre relevante positive fund.
Skriv konkrete OSM-fund og ikke kun antal. Fuld rapport er ikke en taktisk plan og må ikke indeholde taktisk oplæg, taktisk fokus eller kritiske mangler. Nævn kun manglende data i supplementary_lines, hvis det er nødvendigt og i én kort samlet linje.
"""

BRIEF_FOLLOWUP_PROMPT = """
Du er IndsatsBrief Brand.
Du besvarer opfølgende spørgsmål til en konkret adressebrief.
Du må gerne analysere de tilsendte data.
Du må ikke gætte på manglende data.
Hvis svaret ikke findes i data, sig kort at det ikke fremgår af de tilgængelige data.
Skriv dansk, kort og praktisk.
Skriv ikke taktisk plan, medmindre brugeren direkte beder om taktisk vurdering.
Skriv ikke “ikke verificeret” efter hvert fund.
Skriv ikke disponeringsforslag, “send”, “bør afsendes” eller “anbefalet station”.
"""

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "address_lines": {
            "type": "array",
            "items": {"type": "string"}
        },
        "findings": {
            "type": "array",
            "items": {"type": "string"}
        },
        "building_lines": {
            "type": "array",
            "items": {"type": "string"}
        },
        "surroundings_lines": {
            "type": "array",
            "items": {"type": "string"}
        },
        "osm_risk_lines": {
            "type": "array",
            "items": {"type": "string"}
        },
        "weather_lines": {
            "type": "array",
            "items": {"type": "string"}
        },
        "water_supply_lines": {
            "type": "array",
            "items": {"type": "string"}
        },
        "assistance_lines": {
            "type": "array",
            "items": {"type": "string"}
        },
        "map_links": {
            "type": "array",
            "items": {"type": "string"}
        },
        "building_details": {
            "type": "array",
            "items": {"type": "string"}
        },
        "access_lines": {
            "type": "array",
            "items": {"type": "string"}
        },
        "secondary_buildings": {
            "type": "array",
            "items": {"type": "string"}
        },
        "basement_lines": {
            "type": "array",
            "items": {"type": "string"}
        },
        "heating_lines": {
            "type": "array",
            "items": {"type": "string"}
        },
        "risk_context_lines": {
            "type": "array",
            "items": {"type": "string"}
        },
        "resource_lines": {
            "type": "array",
            "items": {"type": "string"}
        },
        "supplementary_lines": {
            "type": "array",
            "items": {"type": "string"}
        },
        "traffic_lines": {
            "type": "array",
            "items": {"type": "string"}
        },
        "disclaimer": {"type": "string"}
    },
    "required": [
        "title",
        "address_lines",
        "findings",
        "osm_risk_lines",
        "weather_lines",
        "water_supply_lines",
        "assistance_lines",
        "map_links",
        "building_details",
        "access_lines",
        "secondary_buildings",
        "basement_lines",
        "heating_lines",
        "risk_context_lines",
        "resource_lines",
        "building_lines",
        "surroundings_lines",
        "supplementary_lines",
        "traffic_lines",
        "disclaimer"
    ],
    "additionalProperties": False
}

REPORT_DISCLAIMER = "Data fra OSM, BBR og kort-/luftfotolinks er støtteoplysninger."
ASSISTANCE_DISCLAIMER = (
    "Listen viser vejledende nærmeste brand-/redningsressourcer. "
    "Det er ikke live disponering og ikke en anbefaling om afsendelse."
)
STATION_DATA_DIR = os.path.join(os.path.dirname(__file__), "station_data")
ROOT_FIRE_RESCUE_STATIONS_FILE = os.path.join(os.path.dirname(__file__), "fire_rescue_stations.json")
FIRE_RESCUE_STATIONS_FILE = os.path.join(STATION_DATA_DIR, "fire_rescue_stations.json")
RESOURCE_ALIASES_FILE = os.path.join(STATION_DATA_DIR, "resource_aliases.json")
STATION_DATA_CACHE = None
RESOURCE_ALIAS_CACHE = None
STATION_GEOCODE_CACHE = {}
RESOURCE_ALIAS_MAP = {
    "sprøjte": ["sprøjte", "autosprøjte", "automobilsprøjte", "automobilsproejte", "brandsprøjte", "brandbil", "basis", "basisenhed", "tanksprøjte", "tanksproejte"],
    "tankvogn": ["tankvogn", "vandtankvogn", "vandtank", "vandforsyning", "vandressource", "vand"],
    "stige": ["stige", "drejestige", "stigevogn", "redningslift", "lift", "højderedning", "hoejderedning", "redning fra højde", "tagarbejde"],
    "redningsvogn": ["redningsvogn", "pionervogn", "frigørelse", "frigoerelse", "tung frigørelse", "tung redning", "trafikuheld", "fastklemt", "redning"],
    "kemi": ["kemi", "CBRN", "cbrn", "kemivogn", "miljøvogn", "miljoevogn", "miljø", "miljoe", "farlige stoffer", "forurening", "rens", "renseplads"],
    "båd": ["båd", "baad", "redningsbåd", "redningsbaad", "bådtrækker", "baadtraekker", "vandredning", "overfladeredning", "søredning", "soeredning", "SAR", "hovercraft", "dykker", "vanddykker", "dykkervogn"],
    "robot": ["robot", "R6", "TAF 60", "TAF60", "fjernstyret slukningsenhed", "fjernstyret robotenhed", "fjernstyret indsats", "robot/TAF 60"],
    "MIRG": ["MIRG", "mirg", "skibsbrand", "skib", "maritim indsats", "slukning til søs", "brandslukning til søs", "Maritime Incident Response Group"],
    "slangetender": ["slangetender", "slange", "slanger", "slangeudlægning", "A-slange", "B-slange", "taktisk vandforsyning"],
    "indsatsleder": ["indsatsleder", "ISL", "isl", "ledervogn", "indsatsledervogn", "holdleder", "ledelse"],
    "container": ["container", "kroghejs", "kroghejskøretøj", "containerbil", "containerberedskab", "vandtankcontainer", "klimacontainer", "elbilslukningscontainer"],
    "logistik": ["logistik", "transport", "materieltransport", "mandskabsvogn", "personvogn", "støttevogn", "servicevogn", "trailer", "materieltrailer"],
    "pumpe": ["pumpe", "påhængspumpe", "påhængssprøjte", "pumpetrailer", "efterløbspumpe", "dykpumpe", "lænsepumpe", "pumpeopgaver"],
    "klima": ["klima", "klimacontainer", "klimatrailer", "oversvømmelse", "stormflod", "vand på vej", "pumpeopgaver"],
    "elbil": ["elbil", "elbilslukning", "batteribrand", "battericontainer", "elbilslukningscontainer"],
    "jernbane": ["jernbane", "banevej", "banevejkøretøj", "BVK", "mobilovergang", "togulykke", "jordingsvogn", "jordingsudstyr", "kørestrøm", "Storebælt", "broberedskab"],
    "lys": ["lys", "belysning", "arbejdslys", "lysgiraf"],
    "kran": ["kran", "redningskran", "bjærgning", "tung redning"],
    "frivillige": ["frivillige", "frivilligenhed", "supplerende beredskab", "forplejning", "logistik", "støtteberedskab"],
}

ROUTE_CACHE = {}

API_ERROR_PREFIXES = (
    "/incident-brief",
    "/analyze-brief",
    "/full-brief",
    "/brief-followup",
    "/hazmat-analyze",
    "/assistance-stations",
    "/nearest-resource",
    "/address-autocomplete",
    "/test-bbr",
    "/test-hydrants",
    "/osm-risk-check",
    "/aerial-check",
    "/aerial-image",
    "/hazmat",
)


def is_api_request_path():
    return request.path.startswith(API_ERROR_PREFIXES)


@app.errorhandler(HTTPException)
def handle_api_http_error(error):
    if is_api_request_path():
        return jsonify({
            "ok": False,
            "error": error.description or "API-fejl",
            "route": request.path,
        }), error.code
    return error


@app.errorhandler(Exception)
def handle_api_unexpected_error(error):
    if is_api_request_path():
        app.logger.exception("API error")
        return jsonify({
            "ok": False,
            "error": "Serverfejl under behandling af forespørgslen.",
            "details": str(error),
            "route": request.path,
        }), 500
    return "Internal Server Error", 500


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

BBR_PRESERVATION_STATUS = {
    "1": "Fredet",
    "2": "Bevaringsværdig",
    "3": "Ikke fredet",
    "4": "Ikke bevaringsværdig",
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
        radius = int(value)
        return radius if radius > 0 else default
    except Exception:
        return default


def parse_assistance_radius(value, default=40):
    try:
        radius = float(value)
        return radius if radius > 0 else default
    except Exception:
        return default


def load_json_file(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception as error:
        app.logger.warning("Kunne ikke loade JSON-data fra %s: %s", path, error)
        return fallback


def load_fire_rescue_stations():
    """Load manual station/resource data without making app startup fragile."""
    global STATION_DATA_CACHE
    if STATION_DATA_CACHE is None:
        station_file = (
            ROOT_FIRE_RESCUE_STATIONS_FILE
            if os.path.exists(ROOT_FIRE_RESCUE_STATIONS_FILE)
            else FIRE_RESCUE_STATIONS_FILE
        )
        data = load_json_file(station_file, [])
        STATION_DATA_CACHE = data if isinstance(data, list) else []
    return STATION_DATA_CACHE


def load_resource_aliases():
    """Load centralized resource aliases for natural-language resource search."""
    global RESOURCE_ALIAS_CACHE
    if RESOURCE_ALIAS_CACHE is None:
        data = load_json_file(RESOURCE_ALIASES_FILE, {})
        RESOURCE_ALIAS_CACHE = {**RESOURCE_ALIAS_MAP, **data} if isinstance(data, dict) else dict(RESOURCE_ALIAS_MAP)
    return RESOURCE_ALIAS_CACHE


def normalize_text(value):
    if value is None:
        return ""
    normalized = str(value).strip().lower()
    normalized = (
        normalized
        .replace("æ", "ae")
        .replace("ø", "oe")
        .replace("å", "aa")
    )
    normalized = re.sub(r"[-/.,;:()]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def expand_resource_query(query):
    query_text = str(query or "").strip()
    if not query_text:
        return []

    aliases = load_resource_aliases()
    normalized_query = normalize_text(query_text)
    expanded = [query_text]

    for canonical, terms in aliases.items():
        candidate_terms = [canonical, *(terms or [])]
        normalized_terms = {normalize_text(term) for term in candidate_terms}
        if normalized_query in normalized_terms:
            expanded.extend(candidate_terms)

    unique_terms = []
    seen = set()
    for term in expanded:
        key = normalize_text(term)
        if key and key not in seen:
            seen.add(key)
            unique_terms.append(term)
    return unique_terms


def station_text_fields(station):
    return [
        station.get("name"),
        station.get("organization"),
        station.get("authority"),
        station.get("operator"),
        station.get("area"),
        *(station.get("special_resources") or []),
        *(station.get("resource_aliases") or []),
    ]


def score_resource_text(value, expanded_terms, base_score):
    normalized_value = normalize_text(value)
    if not normalized_value:
        return None

    best_score = None
    matched_terms = []
    for term in expanded_terms:
        normalized_term = normalize_text(term)
        if not normalized_term:
            continue
        if normalized_value == normalized_term:
            score = base_score
        elif normalized_term in normalized_value or normalized_value in normalized_term:
            score = max(base_score - 20, 1)
        else:
            continue
        if best_score is None or score > best_score:
            best_score = score
        if term not in matched_terms:
            matched_terms.append(term)

    if best_score is None:
        return None
    return best_score, matched_terms


def resource_display_name(name, resource_type):
    if name and resource_type and normalize_text(name) != normalize_text(resource_type):
        return f"{name} – {resource_type}"
    return name or resource_type


def height_resource_priority_bonus(item, expanded_terms):
    query_term = normalize_text(expanded_terms[0]) if expanded_terms else ""
    text = normalize_text(" ".join([
        str(item.get("name") or ""),
        str(item.get("type") or ""),
        " ".join(item.get("aliases") or []),
        " ".join(item.get("capabilities") or []),
    ]))

    has_drejestige = any(term in text for term in ["drejestige", "stigevogn"])
    has_lift = any(term in text for term in ["redningslift", "lift"])
    asks_drejestige = query_term in ["drejestige", "stigevogn"]
    asks_lift = query_term in ["lift", "redningslift"]
    asks_height = query_term in ["hoejderedning", "højderedning"]
    asks_generic_stige = query_term == "stige"

    if asks_drejestige:
        if has_drejestige:
            return 18
        if has_lift:
            return -8
    if asks_lift:
        if has_lift:
            return 18
        if has_drejestige:
            return -8
    if asks_generic_stige or asks_height:
        if has_drejestige or has_lift:
            return 10
    return 0


def score_station_resource(item, expanded_terms, item_kind):
    candidates = [
        (item.get("name"), 100),
        (item.get("type"), 85),
    ]
    candidates.extend((alias, 82) for alias in item.get("aliases") or [])
    candidates.extend((capability, 80) for capability in item.get("capabilities") or [])
    best = None
    matched_terms = []

    for value, base_score in candidates:
        scored = score_resource_text(value, expanded_terms, base_score)
        if not scored:
            continue
        score, terms = scored
        if best is None or score > best:
            best = score
        matched_terms.extend(term for term in terms if term not in matched_terms)

    if best is None:
        return None

    best += height_resource_priority_bonus(item, expanded_terms)

    return {
        "matched_resource": resource_display_name(item.get("name"), item.get("type")) or item_kind,
        "matched_type": item_kind,
        "matched_resource_name": item.get("name"),
        "matched_resource_type": item.get("type"),
        "matched_resource_kind": item_kind,
        "matched_capabilities": item.get("capabilities") or [],
        "match_score": best,
        "matched_terms": matched_terms,
    }


def find_matching_station_resources(resource_query, include_non_operational=False):
    expanded_terms = expand_resource_query(resource_query)
    if not expanded_terms:
        return []

    matches = []
    for station in load_fire_rescue_stations():
        if not include_non_operational and station.get("operational_response_station") is False:
            continue

        station_matches = []
        for collection_name, match_type in [
            ("vehicles", "vehicle"),
            ("trailers", "trailer"),
            ("containers", "container"),
        ]:
            for item in station.get(collection_name) or []:
                scored = score_station_resource(item, expanded_terms, match_type)
                if scored:
                    station_matches.append(scored)

        for value in station.get("special_resources") or []:
            scored = score_resource_text(value, expanded_terms, 70)
            if scored:
                score, terms = scored
                station_matches.append({
                    "matched_resource": value,
                    "matched_type": "station_resource",
                    "matched_resource_name": value,
                    "matched_resource_type": None,
                    "matched_resource_kind": "station_resource",
                    "matched_capabilities": [],
                    "match_score": score,
                    "matched_terms": terms,
                })

        for value in station_text_fields(station):
            scored = score_resource_text(value, expanded_terms, 55)
            if scored:
                score, terms = scored
                station_matches.append({
                    "matched_resource": value,
                    "matched_type": "alias",
                    "matched_resource_name": value,
                    "matched_resource_type": None,
                    "matched_resource_kind": "station_resource",
                    "matched_capabilities": [],
                    "match_score": score,
                    "matched_terms": terms,
                })

        if not station_matches:
            continue

        best_match = sorted(station_matches, key=lambda item: item["match_score"], reverse=True)[0]
        matches.append({
            "station": station,
            **best_match,
        })

    return sorted(matches, key=lambda item: item["match_score"], reverse=True)


def get_station_coordinates(station):
    if station.get("lat") is not None and station.get("lon") is not None:
        try:
            return float(station["lat"]), float(station["lon"])
        except Exception:
            return None

    address = (station.get("address") or "").strip()
    if not address:
        return None

    cache_key = normalize_text(address)
    if cache_key in STATION_GEOCODE_CACHE:
        return STATION_GEOCODE_CACHE[cache_key]

    try:
        address_data = lookup_address(address)
        if address_data and not address_data.get("error"):
            latitude = address_data.get("latitude")
            longitude = address_data.get("longitude")
            if latitude is not None and longitude is not None:
                STATION_GEOCODE_CACHE[cache_key] = (float(latitude), float(longitude))
                return STATION_GEOCODE_CACHE[cache_key]
    except Exception as error:
        app.logger.warning("Kunne ikke geokode station %s: %s", station.get("name"), error)

    STATION_GEOCODE_CACHE[cache_key] = None
    return None


def rank_stations_by_distance(origin_lat, origin_lon, matches, limit=5, radius_km=None):
    ranked = []
    for match in matches:
        station = match.get("station") or {}
        coordinates = get_station_coordinates(station)
        if not coordinates:
            continue
        station_lat, station_lon = coordinates
        try:
            air_distance_km = haversine_distance_km(origin_lat, origin_lon, station_lat, station_lon)
        except Exception:
            continue

        if radius_km is not None and air_distance_km > radius_km:
            continue

        route = get_driving_route_osrm(origin_lat, origin_lon, station_lat, station_lon)
        road_time_min = route.get("drive_time_min")
        road_distance_km = route.get("road_distance_km")
        ranked.append({
            **match,
            "station_lat": station_lat,
            "station_lon": station_lon,
            "air_distance_km": air_distance_km,
            "road_distance_km": road_distance_km,
            "road_time_min": road_time_min,
            "drive_time_min": road_time_min,
            "route_source": route.get("route_source"),
        })

    def result_sort_key(item):
        road_time = item.get("road_time_min")
        air_distance = item.get("air_distance_km")
        match_score = item.get("match_score", 0)
        station = item.get("station") or {}

        if road_time is not None:
            primary = float(road_time)
        elif air_distance is not None:
            primary = float(air_distance) * 2.0
        else:
            primary = 999999.0

        return (
            primary,
            -match_score,
            station.get("name") or "",
            item.get("matched_resource") or "",
        )

    ranked.sort(key=result_sort_key)
    return ranked[:max(1, int(limit or 5))]


def detect_resource_question(question):
    normalized = normalize_text(question)
    if not normalized:
        return False
    question_markers = ["hvor", "naermeste", "find", "ressource", "station"]
    if not any(marker in normalized for marker in question_markers):
        return False
    aliases = load_resource_aliases()
    for canonical, terms in aliases.items():
        for term in [canonical, *(terms or [])]:
            normalized_term = normalize_text(term)
            if normalized_term and normalized_term in normalized:
                return True
    return False


def extract_resource_query(question):
    normalized = normalize_text(question)
    aliases = load_resource_aliases()
    best = None
    for canonical, terms in aliases.items():
        for term in [canonical, *(terms or [])]:
            normalized_term = normalize_text(term)
            if normalized_term and normalized_term in normalized:
                if best is None or len(normalized_term) > len(normalize_text(best)):
                    best = canonical
    return best


def haversine_distance_km(lat1, lon1, lat2, lon2):
    """Return straight-line distance in km, rounded to one decimal."""
    earth_radius_km = 6371.0
    lat1_rad, lon1_rad = math.radians(float(lat1)), math.radians(float(lon1))
    lat2_rad, lon2_rad = math.radians(float(lat2)), math.radians(float(lon2))
    delta_lat = lat2_rad - lat1_rad
    delta_lon = lon2_rad - lon1_rad
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    return round(earth_radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 1)


def get_driving_route_osrm(origin_lat, origin_lon, dest_lat, dest_lon):
    """Fetch ordinary-road distance and duration without failing the brief."""
    unavailable = {
        "road_distance_km": None,
        "drive_time_min": None,
        "route_source": "unavailable",
    }

    try:
        cache_key = tuple(
            round(float(value), 5)
            for value in [origin_lat, origin_lon, dest_lat, dest_lon]
        )
    except Exception:
        return unavailable

    if cache_key in ROUTE_CACHE:
        return ROUTE_CACHE[cache_key]

    url = (
        "https://router.project-osrm.org/route/v1/driving/"
        f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}?overview=false"
    )

    try:
        response = requests.get(url, timeout=(3, 6))
        response.raise_for_status()
        routes = response.json().get("routes") or []
        if not routes:
            ROUTE_CACHE[cache_key] = unavailable
            return unavailable

        route = routes[0]
        result = {
            "road_distance_km": round(route.get("distance", 0) / 1000, 1),
            "drive_time_min": round(route.get("duration", 0) / 60),
            "route_source": "OSRM",
        }
        ROUTE_CACHE[cache_key] = result
        return result
    except Exception:
        ROUTE_CACHE[cache_key] = unavailable
        return unavailable


def get_osm_fire_rescue_stations_nearby(lat, lon, radius_km):
    """Find nearby fire/rescue stations in OSM without excluding BRS stations."""
    radius_m = max(1, int(float(radius_km) * 1000))
    query = f"""
    [out:json][timeout:4];
    (
      nwr(around:{radius_m},{lat},{lon})["emergency"="fire_station"];
      nwr(around:{radius_m},{lat},{lon})["amenity"="fire_station"];
      nwr(around:{radius_m},{lat},{lon})["name"~"brand|fire|redning|beredskab",i];
      nwr(around:{radius_m},{lat},{lon})["operator"~"Beredskabsstyrelsen|beredskab|BRS",i];
    );
    out center tags;
    """
    stations = []
    seen = set()
    started_at = time.monotonic()

    for overpass_url in [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]:
        if time.monotonic() - started_at > 5:
            break
        try:
            response = requests.get(overpass_url, params={"data": query}, timeout=(2, 4))
            response.raise_for_status()
            for element in response.json().get("elements", []):
                key = (element.get("type"), element.get("id"))
                if key in seen:
                    continue
                seen.add(key)

                tags = element.get("tags") or {}
                center = element.get("center") or {}
                station_lat = element.get("lat", center.get("lat"))
                station_lon = element.get("lon", center.get("lon"))
                if station_lat is None or station_lon is None:
                    continue

                name = tags.get("name") or tags.get("operator") or "Brand/redningsstation"
                organization = tags.get("operator") or tags.get("brand")
                stations.append({
                    "name": name,
                    "type": "Brand/redning",
                    "organization": organization,
                    "area": tags.get("addr:city") or tags.get("is_in:municipality"),
                    "lat": station_lat,
                    "lon": station_lon,
                    "source": "OSM",
                    "osm_id": element.get("id"),
                    "osm_type": element.get("type"),
                    "osm_tags": tags,
                })
            return stations
        except Exception:
            continue

    return stations


def merge_fire_rescue_stations(manual_stations, osm_stations):
    """Merge obvious manual/OSM duplicates, with manual names and orgs preferred."""
    merged = [dict(station) for station in (osm_stations or [])]

    def normalized_name(value):
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    for manual in manual_stations or []:
        if manual.get("lat") is None or manual.get("lon") is None:
            continue
        match_index = None
        manual_name = normalized_name(manual.get("name"))
        for index, osm_station in enumerate(merged):
            if osm_station.get("lat") is None or osm_station.get("lon") is None:
                continue
            same_name = manual_name == normalized_name(osm_station.get("name"))
            try:
                close_enough = haversine_distance_km(
                    manual["lat"], manual["lon"], osm_station["lat"], osm_station["lon"]
                ) <= 0.3
            except Exception:
                close_enough = False
            if same_name and close_enough:
                match_index = index
                break

        if match_index is None:
            merged.append(dict(manual))
            continue

        osm_station = merged[match_index]
        combined = {**osm_station, **manual}
        combined["source"] = "manual+OSM"
        combined["osm_id"] = osm_station.get("osm_id")
        combined["osm_type"] = osm_station.get("osm_type")
        combined["osm_tags"] = osm_station.get("osm_tags")
        merged[match_index] = combined

    return merged


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
        response = requests.get(url, params=params, timeout=(3, 6))
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


def get_address_autocomplete(query, limit=8):
    if len(query.strip()) < 3:
        return []

    try:
        response = requests.get(
            "https://api.dataforsyningen.dk/adresser",
            params={"q": query, "per_side": limit},
            timeout=(3, 6),
        )
        response.raise_for_status()
        suggestions = []
        for item in response.json():
            access_address = item.get("adgangsadresse") or {}
            point = access_address.get("adgangspunkt") or {}
            coordinates = point.get("koordinater") or []
            suggestions.append({
                "text": item.get("adressebetegnelse"),
                "address_id": item.get("id"),
                "access_address_id": access_address.get("id"),
                "lat": coordinates[1] if len(coordinates) > 1 else None,
                "lon": coordinates[0] if coordinates else None,
            })
        return suggestions
    except Exception:
        return []


def get_nearby_main_roads(lat, lon, radius_m=400):
    if lat is None or lon is None:
        return {"ok": False, "summary": "", "source": "OSM", "error": "Mangler koordinater"}

    priority = {
        "motorway": 0, "trunk": 1, "primary": 2, "secondary": 3,
        "tertiary": 4, "unclassified": 5, "residential": 6,
    }
    highway_values = "|".join(priority)
    query = f"""
    [out:json][timeout:4];
    way(around:{int(radius_m)},{lat},{lon})["highway"~"^({highway_values})$"]["name"];
    out center tags;
    """
    try:
        response = requests.get(
            "https://overpass-api.de/api/interpreter",
            params={"data": query},
            timeout=(2, 4),
        )
        response.raise_for_status()
        candidates = []
        for element in response.json().get("elements", []):
            tags = element.get("tags") or {}
            center = element.get("center") or {}
            road_lat, road_lon = center.get("lat"), center.get("lon")
            if not tags.get("name") or road_lat is None or road_lon is None:
                continue
            distance = distance_meters(lat, lon, road_lat, road_lon)
            if distance is not None:
                candidates.append((priority.get(tags.get("highway"), 99), distance, tags))
        if not candidates:
            return {"ok": False, "summary": "", "source": "OSM"}
        _, distance, tags = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
        road_name = tags.get("name")
        distance_rounded = round(distance)
        if distance_rounded <= 25:
            summary = f"Adressen ligger på/ved {road_name}."
        else:
            summary = (
                f"Adressen ligger på/ved sidevej tæt på {road_name}, "
                f"ca. {distance_rounded} m fra nærmeste større vej."
            )
        return {
            "ok": True,
            "nearest_main_road": road_name,
            "nearest_road_name": road_name,
            "highway_type": tags.get("highway"),
            "distance_m": distance_rounded,
            "summary": summary,
            "source": "OSM",
        }
    except (requests.exceptions.Timeout, requests.exceptions.RequestException, ValueError) as error:
        return {"ok": False, "summary": "", "source": "OSM", "error": str(error)}
    except Exception as error:
        return {"ok": False, "summary": "", "source": "OSM", "error": str(error)}


def get_traffic_events_nearby(lat, lon, radius_km=5):
    """Optional DATEX/Traffic Events adapter; returns no events when unavailable."""
    traffic_url = os.getenv("VEJDIREKTORATET_TRAFFIC_EVENTS_URL")
    if not traffic_url:
        return []
    try:
        response = requests.get(traffic_url, timeout=(3, 6))
        response.raise_for_status()
        payload = response.json()
        events = payload.get("events", payload) if isinstance(payload, dict) else payload
        normalized = []
        for event in events if isinstance(events, list) else []:
            event_lat = event.get("lat") or event.get("latitude")
            event_lon = event.get("lon") or event.get("longitude")
            distance = distance_meters(lat, lon, event_lat, event_lon)
            if distance is None or distance > radius_km * 1000:
                continue
            normalized.append({
                "title": event.get("title") or event.get("description"),
                "type": event.get("type") or "trafikhændelse",
                "road_name": event.get("road_name") or event.get("road"),
                "distance_m": round(distance),
                "start_time": event.get("start_time"),
                "end_time": event.get("end_time"),
                "lat": event_lat,
                "lon": event_lon,
                "source": "Vejdirektoratet/DATEX",
            })
        return normalized
    except Exception:
        return []


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
        response = requests.get(url, params=params, timeout=(3, 6))

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
    [out:json][timeout:4];
    (
      node["emergency"="fire_hydrant"](around:{int(radius_m)},{latitude},{longitude});
      way["emergency"="fire_hydrant"](around:{int(radius_m)},{latitude},{longitude});
      relation["emergency"="fire_hydrant"](around:{int(radius_m)},{latitude},{longitude});
    );
    out center tags 50;
    """

    attempts = []
    started_at = time.monotonic()

    for overpass_url in overpass_urls:
        if time.monotonic() - started_at > 5:
            break
        try:
            response = requests.get(
                overpass_url,
                params={"data": query},
                headers={
                    "User-Agent": "IndsatsBrief-Brand/1.0",
                    "Accept": "application/json"
                },
                timeout=(2, 4)
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


def osm_overpass_fallback(error_message=None, attempts=None, radius_m=250):
    return {
        "ok": False,
        "source": "osm_overpass",
        "error": error_message or "OSM/Overpass kunne ikke hentes inden for timeout.",
        "query_radius_m": int(radius_m),
        "finding_count": 0,
        "findings": [],
        "grouped_summary": [],
        "osm_risk_summary": [],
        "links": [],
        "attempts": attempts or [],
    }


def get_osm_risk_check(latitude, longitude, radius_m=250):
    if latitude is None or longitude is None:
        return {
            "ok": False,
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
    [out:json][timeout:4];
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
    started_at = time.monotonic()

    for overpass_url in overpass_urls:
        if time.monotonic() - started_at > 5:
            break
        try:
            response = requests.get(
                overpass_url,
                params={"data": query},
                headers={
                    "User-Agent": "IndsatsBrief-Brand/1.0",
                    "Accept": "application/json"
                },
                timeout=(2, 4)
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
                "ok": True,
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

        except requests.exceptions.Timeout as e:
            attempts.append({
                "url": overpass_url,
                "error": "timeout",
                "details": str(e)
            })
        except requests.exceptions.RequestException as e:
            attempts.append({
                "url": overpass_url,
                "error": "request_error",
                "details": str(e)
            })
        except ValueError as e:
            attempts.append({
                "url": overpass_url,
                "error": "invalid_json",
                "details": str(e)
            })
        except Exception as e:
            attempts.append({
                "url": overpass_url,
                "error": "unexpected_error",
                "details": str(e)
            })

    return osm_overpass_fallback(
        "OSM/Overpass kunne ikke hentes inden for timeout.",
        attempts,
        radius_m,
    )


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
        response = requests.get(wms["url"], timeout=(3, 6))
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
        response = requests.get(wms["url"], timeout=(3, 6))
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


def bbr_etage_candidate_queries(building_id):
    now = current_graphql_time()

    return [
        {
            "name": "etage_bygning_string_eq",
            "query": """
            query($tid: DafDateTime!, $bygningId: String!) {
              BBR_Etage(
                first: 50,
                virkningstid: $tid,
                registreringstid: $tid,
                where: {
                  bygning: { eq: $bygningId }
                }
              ) {
                nodes {
                  id_lokalId
                  id_namespace
                  datafordelerRowId
                  datafordelerOpdateringstid
                  bygning
                  eta006BygningensEtagebetegnelse
                  eta020SamletArealAfEtage
                  eta021ArealAfUdnyttetDelAfTagetage
                  eta022Kaelderareal
                  eta023ArealAfLovligBeboelseIKaelder
                  eta024EtagensAdgangsareal
                  eta025Etagetype
                  eta026ErhvervIKaelder
                  eta500Notatlinjer
                  status
                }
              }
            }
            """,
            "variables": {
                "tid": now,
                "bygningId": building_id
            }
        }
    ]


def test_bbr_graphql_etage(building_id):
    attempts = []

    if not building_id:
        return {
            "status": "error",
            "message": "Mangler bygning id_lokalId",
            "nodes": [],
            "attempts": attempts
        }

    for candidate in bbr_etage_candidate_queries(building_id):
        result = call_bbr_graphql(candidate["query"], candidate["variables"])
        response_json = result.get("response_json") or {}
        errors = response_json.get("errors")
        data = response_json.get("data")

        nodes = []
        if data and data.get("BBR_Etage"):
            nodes = data.get("BBR_Etage", {}).get("nodes", []) or []

        attempt = {
            "name": candidate["name"],
            "building_id": building_id,
            "status_code": result.get("status_code"),
            "errors": errors,
            "nodes_count": len(nodes),
            "data_preview": data,
            "response_text_preview": result.get("response_text", "")[:4000]
        }

        attempts.append(attempt)

        if result.get("status_code") == 200 and not errors:
            return {
                "status": "success" if nodes else "query_worked_but_no_nodes",
                "working_candidate": candidate["name"],
                "nodes": nodes,
                "attempts": attempts
            }

    return {
        "status": "no_candidate_worked",
        "nodes": [],
        "attempts": attempts
    }


def test_bbr_graphql_floors_for_building(building):
    building_id = building.get("id_lokalId") if building else None

    return test_bbr_graphql_etage(building_id)


def enrich_bbr_result_with_etage(address_result):
    if not address_result or address_result.get("status") not in ["success", "query_worked_but_no_nodes"]:
        return address_result

    nodes = address_result.get("nodes") or []
    etage_by_building_id = {}
    etage_attempts = []
    seen_building_ids = set()

    for node in nodes:
        building_id = node.get("id_lokalId")

        if not building_id or building_id in seen_building_ids:
            continue

        seen_building_ids.add(building_id)
        etage_result = test_bbr_graphql_floors_for_building(node)
        etage_by_building_id[building_id] = etage_result.get("nodes") or []
        etage_attempts.append({
            "building_id": building_id,
            "status": etage_result.get("status"),
            "working_candidate": etage_result.get("working_candidate"),
            "nodes_count": len(etage_result.get("nodes") or []),
            "attempts": etage_result.get("attempts", [])
        })

    address_result["etage_nodes_by_building_id"] = etage_by_building_id
    address_result["bbr_etage_attempts"] = etage_attempts

    return address_result


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
                  byg024AntalLejlighederMedKoekken
                  byg025AntalLejlighederUdenKoekken
                  byg026Opfoerelsesaar
                  byg027OmTilbygningsaar
                  byg030Vandforsyning
                  byg032YdervaeggensMateriale
                  byg033Tagdaekningsmateriale
                  byg036AsbestholdigtMateriale
                  byg038SamletBygningsareal
                  byg039BygningensSamledeBoligAreal
                  byg040BygningensSamledeErhvervsAreal
                  byg041BebyggetAreal
                  byg042ArealIndbyggetGarage
                  byg043ArealIndbyggetCarport
                  byg044ArealIndbyggetUdhus
                  byg048AndetAreal
                  byg051Adgangsareal
                  byg054AntalEtager
                  byg056Varmeinstallation
                  byg057Opvarmningsmiddel
                  byg058SupplerendeVarme
                  byg070Fredning
                  byg071BevaringsvaerdighedReference
                  byg500Notatlinjer
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
            return enrich_bbr_result_with_etage({
                "status": "success",
                "working_candidate": candidate["name"],
                "nodes": nodes,
                "attempts": attempts
            })

        if result.get("status_code") == 200 and not errors:
            return enrich_bbr_result_with_etage({
                "status": "query_worked_but_no_nodes",
                "working_candidate": candidate["name"],
                "nodes": nodes,
                "attempts": attempts
            })

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


def parse_positive_number(value):
    if value is None:
        return None

    try:
        number = float(str(value).replace(",", "."))
    except Exception:
        return None

    if number <= 0:
        return None

    if number.is_integer():
        return int(number)

    return number


def get_first_present(source, keys):
    if not source:
        return None

    for key in keys:
        if key in source and source.get(key) is not None:
            return source.get(key)

    return None


def normalize_bbr_basement_data(building, address_result):
    building_id = building.get("id_lokalId") if building else None
    etage_nodes_by_building_id = (
        address_result.get("etage_nodes_by_building_id", {})
        if address_result else {}
    )
    etage_nodes = etage_nodes_by_building_id.get(building_id, []) if building_id else []

    basement_raw = {
        "building_id": building_id,
        "checked_entities": ["BBR_Etage"],
        "etage_nodes": etage_nodes,
        "etage_attempts": address_result.get("bbr_etage_attempts", []) if address_result else []
    }

    basement_area_m2 = 0
    basement_living_area_m2 = 0
    basement_commercial_area_m2 = 0
    attic_used_area_m2 = 0
    basement_label_nodes = []
    floors_summary = []

    for node in etage_nodes:
        area = parse_positive_number(
            get_first_present(node, [
                "eta022Kaelderareal",
                "eta022Kælderareal",
                "kælderareal",
                "kaelderareal"
            ])
        )
        living_area = parse_positive_number(
            get_first_present(node, [
                "eta023ArealAfLovligBeboelseIKaelder",
                "eta023ArealAfLovligBeboelseIKælder"
            ])
        )
        commercial_area = parse_positive_number(
            get_first_present(node, [
                "eta026ErhvervIKaelder",
                "eta026ErhvervIKælder"
            ])
        )
        attic_area = parse_positive_number(node.get("eta021ArealAfUdnyttetDelAfTagetage"))
        floor_area = parse_positive_number(node.get("eta020SamletArealAfEtage"))
        access_area = parse_positive_number(node.get("eta024EtagensAdgangsareal"))

        if area:
            basement_area_m2 += area

        if living_area:
            basement_living_area_m2 += living_area

        if commercial_area:
            basement_commercial_area_m2 += commercial_area

        if attic_area:
            attic_used_area_m2 += attic_area

        etage_label = str(node.get("eta006BygningensEtagebetegnelse") or "").strip().lower()
        floor_type = str(node.get("eta025Etagetype") or "").strip().lower()

        if (
            "kælder" in etage_label
            or "kaelder" in etage_label
            or "kælder" in floor_type
            or "kaelder" in floor_type
            or etage_label in ["kl", "kld", "k"]
            or floor_type in ["kl", "kld", "k"]
        ):
            basement_label_nodes.append(node)

        floor_summary = {
            "floor_label": node.get("eta006BygningensEtagebetegnelse"),
            "floor_type": node.get("eta025Etagetype"),
            "floor_area_m2": floor_area,
            "attic_used_area_m2": attic_area,
            "basement_area_m2": area,
            "basement_living_area_m2": living_area,
            "basement_commercial_area_m2": commercial_area,
            "access_area_m2": access_area
        }
        floor_summary = {
            key: value
            for key, value in floor_summary.items()
            if value is not None
        }

        if floor_summary:
            floors_summary.append(floor_summary)

    basement_present = None
    basement_source = "BBR_Etage forsøgt, men kælderdata ikke fundet"

    if basement_area_m2 > 0:
        basement_present = True
        basement_source = "BBR_Etage.eta022Kaelderareal"
    elif basement_living_area_m2 > 0:
        basement_present = True
        basement_source = "BBR_Etage.eta023ArealAfLovligBeboelseIKaelder"
    elif basement_commercial_area_m2 > 0:
        basement_present = True
        basement_source = "BBR_Etage.eta026ErhvervIKaelder"
    elif basement_label_nodes:
        basement_present = True
        basement_source = "BBR_Etage.eta006BygningensEtagebetegnelse/eta025Etagetype"
        basement_raw["basement_label_nodes"] = basement_label_nodes

    if basement_present is True:
        return {
            "floors_raw": etage_nodes,
            "basement_present": True,
            "basement_area_m2": basement_area_m2 if basement_area_m2 > 0 else None,
            "basement_living_area_m2": basement_living_area_m2 if basement_living_area_m2 > 0 else None,
            "basement_commercial_area_m2": basement_commercial_area_m2 if basement_commercial_area_m2 > 0 else None,
            "basement_source": basement_source,
            "basement_raw": basement_raw,
            "attic_used_area_m2": attic_used_area_m2 if attic_used_area_m2 > 0 else None,
            "floors_summary": floors_summary
        }

    basement_raw["debug"] = (
        "Kælderdata blev ikke fundet i de hentede BBR-entiteter. "
        "Der blev undersøgt BBR_Etage-felterne eta022Kaelderareal og "
        "eta006BygningensEtagebetegnelse."
    )

    return {
        "floors_raw": etage_nodes,
        "basement_area_m2": None,
        "basement_present": None,
        "basement_source": "BBR_Etage forsøgt, men kælderdata ikke fundet",
        "basement_raw": basement_raw,
        "basement_living_area_m2": None,
        "basement_commercial_area_m2": None,
        "attic_used_area_m2": attic_used_area_m2 if attic_used_area_m2 > 0 else None,
        "floors_summary": floors_summary
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

    building = select_best_bbr_building(nodes)
    secondary_buildings = build_secondary_bbr_buildings(nodes, building)
    basement_data = normalize_bbr_basement_data(building, address_result)

    usage_code = building.get("byg021BygningensAnvendelse")
    outer_wall_code = building.get("byg032YdervaeggensMateriale")
    roof_code = building.get("byg033Tagdaekningsmateriale")
    water_code = building.get("byg030Vandforsyning")
    asbestos_code = building.get("byg036AsbestholdigtMateriale")
    heating_installation_code = building.get("byg056Varmeinstallation")
    heating_fuel_code = building.get("byg057Opvarmningsmiddel")
    supplementary_heating_code = building.get("byg058SupplerendeVarme")
    preservation_code = building.get("byg070Fredning")
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

        "floors_count": building.get("byg054AntalEtager"),
        "apartments_with_kitchen": building.get("byg024AntalLejlighederMedKoekken"),
        "apartments_without_kitchen": building.get("byg025AntalLejlighederUdenKoekken"),

        "construction_year": building.get("byg026Opfoerelsesaar"),
        "renovation_year": building.get("byg027OmTilbygningsaar"),
        "area_m2": building.get("byg038SamletBygningsareal"),
        "residential_area_m2": building.get("byg039BygningensSamledeBoligAreal"),
        "commercial_area_m2": building.get("byg040BygningensSamledeErhvervsAreal"),
        "built_area_m2": building.get("byg041BebyggetAreal"),
        "built_in_garage_area_m2": building.get("byg042ArealIndbyggetGarage"),
        "built_in_carport_area_m2": building.get("byg043ArealIndbyggetCarport"),
        "built_in_shed_area_m2": building.get("byg044ArealIndbyggetUdhus"),
        "other_area_m2": building.get("byg048AndetAreal"),
        "access_area_m2": building.get("byg051Adgangsareal"),

        "basement": "Ikke verificeret",
        "floors_raw": basement_data.get("floors_raw"),
        "basement_area_m2": basement_data.get("basement_area_m2"),
        "basement_present": basement_data.get("basement_present"),
        "basement_living_area_m2": basement_data.get("basement_living_area_m2"),
        "basement_commercial_area_m2": basement_data.get("basement_commercial_area_m2"),
        "basement_source": basement_data.get("basement_source"),
        "basement_raw": basement_data.get("basement_raw"),
        "attic_used_area_m2": basement_data.get("attic_used_area_m2"),
        "floors_summary": basement_data.get("floors_summary"),

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

        "preservation_status": preservation_code,
        "preservation_status_text": translate_bbr_code(preservation_code, BBR_PRESERVATION_STATUS),
        "preservation_reference": building.get("byg071BevaringsvaerdighedReference"),
        "bbr_notes_raw": building.get("byg500Notatlinjer"),

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
            "skal verificeres",
            "ikke verificeret operativt",
            "kritiske mangler",
            "kritisk mangel",
            "første taktiske fokus",
            "taktisk fokus",
            "ansvarsfraskrivelse",
            "ansvarsfraskrivelser",
        ]

        return not any(part.lower() in stripped.lower() for part in bad_parts)

    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0

    return True


def clean_short_report_text(value):
    if value is None:
        return None

    if not isinstance(value, str):
        return value

    cleaned = value.strip()

    if not cleaned:
        return None

    cut_patterns = [
        r"\s+ifølge\s+BBR\b.*$",
        r"\s+ifølge\s+[^–-]+[–-]\s*ikke\s+verificeret.*$",
        r"\s+ifølge\s+[^–-]+[–-]\s*skal\s+verificeres.*$",
        r"\s+[–-]\s*ikke\s+verificeret.*$",
        r"\s+[–-]\s*skal\s+verificeres.*$",
        r"\s+skal\s+verificeres.*$",
        r"\s+ikke\s+verificeret.*$",
        r"\s+ikke\s+verificeret\s+operativt.*$",
    ]

    for pattern in cut_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–")

    if not is_positive_report_value(cleaned):
        return None

    return cleaned


def add_positive_field(target, source, source_key, target_key=None):
    value = source.get(source_key) if source else None
    value = clean_short_report_text(value)

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

    cleaned_value = clean_short_report_text(value)

    return cleaned_value if is_positive_report_value(cleaned_value) else None


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

    text_fields = [
        "usage_text",
        "building_type_text",
        "outer_wall_material_text",
        "roof_material_text",
        "water_supply_text",
        "asbestos_material_text",
        "heating_installation_text",
        "heating_fuel_text",
        "supplementary_heating_text",
        "preservation_status_text",
        "status_text",
    ]

    for field in text_fields:
        add_positive_field(building, building_data, field)

    for field in [
        "bbr_id",
        "floors_count",
        "apartments_with_kitchen",
        "apartments_without_kitchen",
        "construction_year",
        "renovation_year",
        "area_m2",
        "residential_area_m2",
        "commercial_area_m2",
        "built_area_m2",
        "built_in_garage_area_m2",
        "built_in_carport_area_m2",
        "built_in_shed_area_m2",
        "other_area_m2",
        "access_area_m2",
        "municipality_code",
        "husnummer",
        "preservation_reference",
    ]:
        add_positive_field(building, building_data, field)

    basement_area_m2 = building_data.get("basement_area_m2") if building_data else None
    basement_present = building_data.get("basement_present") if building_data else None

    if basement_area_m2 and basement_area_m2 > 0:
        building["basement"] = f"Kælder: {basement_area_m2} m²"
        building["basement_area_m2"] = basement_area_m2
        building["basement_present"] = True
        add_positive_field(building, building_data, "basement_living_area_m2")
        add_positive_field(building, building_data, "basement_commercial_area_m2")
    elif basement_present is True:
        building["basement"] = "Kælder registreret"
        building["basement_present"] = True

    attic_used_area_m2 = building_data.get("attic_used_area_m2") if building_data else None

    if attic_used_area_m2 and attic_used_area_m2 > 0:
        building["attic_used_area_m2"] = attic_used_area_m2

    secondary_buildings = []

    for secondary in building_data.get("secondary_buildings", []) if building_data else []:
        normalized_secondary = {}

        for field in [
            "usage_text",
            "building_type_text",
            "outer_wall_material_text",
            "roof_material_text",
            "status_text",
        ]:
            add_positive_field(normalized_secondary, secondary, field)

        for field in [
            "bbr_id",
            "construction_year",
            "renovation_year",
            "area_m2",
        ]:
            add_positive_field(normalized_secondary, secondary, field)

        normalized_secondary = prune_positive_report_data(normalized_secondary)

        if normalized_secondary:
            display_text_parts = []
            secondary_type = (
                normalized_secondary.get("building_type_text")
                or normalized_secondary.get("usage_text")
            )

            if secondary_type:
                display_text_parts.append(secondary_type)

            if normalized_secondary.get("construction_year"):
                display_text_parts.append(f"fra {normalized_secondary['construction_year']}")

            if normalized_secondary.get("roof_material_text"):
                display_text_parts.append(f"tag {normalized_secondary['roof_material_text']}")

            if display_text_parts:
                normalized_secondary["display_text"] = ", ".join(display_text_parts)

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


def build_short_report_water_supply(water_supply_data):
    if not water_supply_data or not water_supply_data.get("hydrant_count"):
        return {}

    hydrants = []

    for hydrant in water_supply_data.get("hydrants", []):
        cleaned_hydrant = {}

        for field in [
            "distance_m",
            "map_url",
            "hydrant_type",
            "position",
            "diameter",
            "pressure",
            "ref",
            "operator",
        ]:
            add_positive_field(cleaned_hydrant, hydrant, field)

        cleaned_hydrant = prune_positive_report_data(cleaned_hydrant)

        if cleaned_hydrant:
            hydrants.append(cleaned_hydrant)

    return prune_positive_report_data({
        "hydrant_count": water_supply_data.get("hydrant_count"),
        "hydrants": hydrants
    })


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
    weather_data,
    water_supply_data
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

    water_supply = build_short_report_water_supply(water_supply_data)

    if water_supply:
        short_report_data["water_supply"] = water_supply

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
            timeout=(3, 6)
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

        "floors_count": None,
        "apartments_with_kitchen": None,
        "apartments_without_kitchen": None,

        "construction_year": None,
        "renovation_year": None,
        "area_m2": None,
        "residential_area_m2": None,
        "commercial_area_m2": None,
        "built_area_m2": None,
        "built_in_garage_area_m2": None,
        "built_in_carport_area_m2": None,
        "built_in_shed_area_m2": None,
        "other_area_m2": None,
        "access_area_m2": None,
        "basement": "Ikke verificeret",
        "floors_raw": [],
        "basement_area_m2": None,
        "basement_present": None,
        "basement_living_area_m2": None,
        "basement_commercial_area_m2": None,
        "basement_source": "BBR GraphQL ikke forsøgt - ingen bygning fundet",
        "basement_raw": {
            "debug": "Kælderdata ikke undersøgt, fordi BBR/bygningsdata ikke blev fundet."
        },
        "attic_used_area_m2": None,
        "floors_summary": [],

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

        "preservation_status": "Ikke verificeret",
        "preservation_status_text": "Ikke verificeret",
        "preservation_reference": None,
        "bbr_notes_raw": None,

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
    return redirect("/brief")


def brief_configuration_message():
    if not BRIEF_ACCESS_CODE:
        return "BRIEF_ACCESS_CODE mangler i Render Environment."
    if not FLASK_SECRET_KEY:
        return "FLASK_SECRET_KEY mangler i Render Environment."
    return None


def brief_api_access_error():
    configuration_message = brief_configuration_message()
    if configuration_message:
        return jsonify({"error": configuration_message}), 503
    if not session.get("brief_authenticated"):
        return jsonify({"error": "Login kræves for denne brief-funktion."}), 401
    return None


def brief_login_html(error_message=None):
    error_html = f'<p class="error">{error_message}</p>' if error_message else ""
    return f"""
<!DOCTYPE html>
<html lang="da">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>IndsatsBrief Brand</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #eef2f3; color: #162126; font-family: Arial, sans-serif; padding: 20px; }}
        main {{ width: min(100%, 420px); background: #fff; border: 1px solid #d2dbde; border-radius: 6px; padding: 28px; }}
        h1 {{ margin: 0 0 24px; font-size: 26px; }}
        label {{ display: grid; gap: 8px; font-weight: 700; }}
        input, button {{ width: 100%; min-height: 48px; border-radius: 4px; font: inherit; }}
        input {{ border: 1px solid #aebbc0; padding: 10px 12px; }}
        button {{ margin-top: 18px; border: 0; background: #b91f2b; color: #fff; font-weight: 700; cursor: pointer; }}
        .error {{ color: #a31824; margin: 0 0 16px; }}
        @media (max-width: 520px) {{ main {{ padding: 22px; }} h1 {{ font-size: 23px; }} }}
    </style>
</head>
<body><main>
    <h1>IndsatsBrief Brand</h1>
    {error_html}
    <form method="post" action="/brief-login">
        <label>Adgangskode<input type="password" name="access_code" required autofocus autocomplete="current-password"></label>
        <button type="submit">Åbn</button>
    </form>
</main></body>
</html>
    """


@app.route("/brief-login", methods=["GET", "POST"])
def brief_login():
    configuration_message = brief_configuration_message()
    if configuration_message:
        return Response(configuration_message, status=503, mimetype="text/plain")

    if request.method == "POST":
        submitted_code = request.form.get("access_code", "")
        if hmac.compare_digest(submitted_code, BRIEF_ACCESS_CODE):
            session["brief_authenticated"] = True
            return redirect(url_for("brief_page"))
        return Response(brief_login_html("Forkert adgangskode."), status=401, mimetype="text/html")

    return Response(brief_login_html(), mimetype="text/html")


@app.route("/brief-logout", methods=["GET"])
def brief_logout():
    configuration_message = brief_configuration_message()
    if configuration_message:
        return Response(configuration_message, status=503, mimetype="text/plain")
    session.pop("brief_authenticated", None)
    return redirect(url_for("brief_login"))


@app.route("/brief", methods=["GET"])
def brief_page():
    """Small browser client for the short, presentation-safe incident brief."""
    configuration_message = brief_configuration_message()
    if configuration_message:
        return Response(configuration_message, status=503, mimetype="text/plain")
    if not session.get("brief_authenticated"):
        return redirect(url_for("brief_login"))

    html = r"""
<!DOCTYPE html>
<html lang="da">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>IndsatsBrief Brand</title>
    <style>
        :root {
            color-scheme: dark;
            --bg: #0f172a;
            --card: #111827;
            --card-soft: #1e293b;
            --border: rgba(255,255,255,0.08);
            --primary: #2563eb;
            --assist: #f97316;
            --success: #22c55e;
            --warning: #facc15;
            --error: #ef4444;
            --text: #f8fafc;
            --muted: #cbd5e1;
        }
        html, body { width: 100%; max-width: 100%; overflow-x: hidden; }
        * { box-sizing: border-box; }
        body { margin: 0; background: var(--bg); color: var(--text); font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
        a, p, li, .report-line, .result-line, .disclaimer { color: inherit; overflow-wrap: anywhere; word-break: break-word; }
        a { color: #93c5fd; }
        main { width: min(100%, 1200px); max-width: 100%; overflow-x: hidden; margin: 0 auto; padding: 24px 24px 56px; }
        h1, h2, h3 { letter-spacing: 0; }
        h1 { margin: 0 0 4px; font-size: 30px; }
        .intro { margin: 0; color: var(--muted); }
        .topline { display: flex; justify-content: space-between; gap: 16px; align-items: center; max-width: 100%; min-width: 0; margin-bottom: 22px; padding: 18px 20px; border: 1px solid var(--border); border-radius: 16px; background: linear-gradient(135deg, rgba(30,41,59,.95), rgba(17,24,39,.95)); box-shadow: 0 20px 60px rgba(0,0,0,.28); }
        .logout { min-height: 44px; display: inline-flex; align-items: center; justify-content: center; padding: 0 14px; border-radius: 12px; border: 1px solid var(--border); color: var(--text); text-decoration: none; font-weight: 800; white-space: nowrap; background: rgba(255,255,255,.06); }
        .card, #result, #map-section, .tool-panel, #assistance-section, #resource-section { width: 100%; max-width: 100%; overflow-wrap: anywhere; word-break: break-word; border: 1px solid var(--border); border-radius: 16px; background: var(--card); padding: 20px; box-shadow: 0 16px 45px rgba(0,0,0,.24); }
        .search-card { margin-bottom: 14px; }
        .search { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 130px); gap: 14px; align-items: end; max-width: 100%; min-width: 0; }
        .search > *, .commands > *, .assistance-controls > *, .main-grid > *, .side-stack > *, .resource-form > * { min-width: 0; }
        .address-field { position: relative; }
        .commands { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; align-items: end; max-width: 100%; }
        .autocomplete-list { display: none; position: absolute; z-index: 30; left: 0; right: 0; top: calc(100% + 8px); max-width: 100%; max-height: 280px; overflow: auto; border: 1px solid var(--border); border-radius: 14px; background: #020617; box-shadow: 0 24px 60px rgba(0,0,0,.42); }
        .autocomplete-option { display: block; width: 100%; min-height: 52px; padding: 12px 14px; border: 0; border-bottom: 1px solid var(--border); background: transparent; color: var(--text); text-align: left; font: inherit; cursor: pointer; }
        .autocomplete-option:hover, .autocomplete-option:focus { background: rgba(37,99,235,.22); }
        label { display: grid; gap: 7px; color: var(--muted); font-size: 14px; font-weight: 800; }
        input, select, textarea { width: 100%; max-width: 100%; min-height: 48px; border: 1px solid var(--border); border-radius: 12px; padding: 11px 13px; background: #020617; color: var(--text); font: inherit; outline: none; }
        input:focus, select:focus, textarea:focus, button:focus, a:focus { outline: 3px solid rgba(37,99,235,.5); outline-offset: 2px; }
        textarea { min-height: 124px; resize: vertical; }
        button { max-width: 100%; min-height: 48px; border: 0; border-radius: 12px; padding: 10px 16px; background: var(--primary); color: #fff; font: inherit; font-weight: 850; cursor: pointer; transition: transform .12s ease, filter .12s ease, opacity .12s ease; }
        button:hover { filter: brightness(1.08); transform: translateY(-1px); }
        button.secondary { background: var(--card-soft); color: var(--text); }
        button:disabled { cursor: not-allowed; opacity: .48; transform: none; }
        .assistance-primary { background: var(--assist); color: #111827; font-size: 16px; }
        #status { min-height: 48px; display: flex; align-items: center; gap: 10px; margin: 14px 0; padding: 12px 14px; border-radius: 14px; border: 1px solid var(--border); background: rgba(30,41,59,.72); color: var(--muted); font-weight: 750; }
        #status::before { content: ""; width: 10px; height: 10px; border-radius: 999px; background: var(--warning); box-shadow: 0 0 0 4px rgba(250,204,21,.12); }
        #status[data-state="ready"]::before { background: var(--success); box-shadow: 0 0 0 4px rgba(34,197,94,.12); }
        #status[data-state="loading"]::before { background: var(--warning); animation: pulse 1s infinite; }
        #status[data-state="done"]::before { background: var(--success); }
        #status[data-state="error"]::before { background: var(--error); box-shadow: 0 0 0 4px rgba(239,68,68,.15); }
        @keyframes pulse { 0%, 100% { opacity: .45; } 50% { opacity: 1; } }
        .main-grid { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, .9fr); gap: 18px; align-items: start; max-width: 100%; overflow-x: hidden; }
        .side-stack { display: grid; gap: 18px; min-width: 0; max-width: 100%; }
        #result { display: none; }
        #report { font-size: 16px; line-height: 1.58; overflow-wrap: anywhere; word-break: break-word; }
        #report h2 { margin: 0 0 16px; font-size: 23px; }
        #report h3 { margin: 18px 0 8px; font-size: 15px; color: #bfdbfe; text-transform: uppercase; }
        #report ul { margin: 0; padding-left: 22px; }
        #report li { margin: 7px 0; }
        .actions { display: none; gap: 10px; flex-wrap: wrap; max-width: 100%; margin: 14px 0 0; }
        #map-section { display: none; }
        #map-section h2, .tool-panel h2, #assistance-section h2, #resource-section h2 { margin: 0 0 14px; font-size: 20px; }
        iframe, #map-frame { width: 100%; max-width: 100%; border: 0; }
        #map-frame { height: 360px; border-radius: 12px; background: #020617; }
        .map-links { display: flex; gap: 10px; flex-wrap: wrap; max-width: 100%; margin-top: 12px; }
        .map-links a { max-width: 100%; min-height: 42px; display: inline-flex; align-items: center; padding: 0 12px; border-radius: 10px; background: rgba(37,99,235,.16); text-decoration: none; font-weight: 800; }
        .tool-result { display: none; margin-top: 14px; padding: 14px; border: 1px solid var(--border); border-left: 4px solid var(--primary); border-radius: 12px; background: rgba(2,6,23,.52); overflow-wrap: anywhere; white-space: pre-wrap; color: var(--text); }
        #assistance-section, #resource-section { display: block; }
        .empty-hint { color: var(--muted); margin: 0; }
        .assistance-controls { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 130px) minmax(0, 130px); gap: 10px; align-items: end; max-width: 100%; margin-bottom: 14px; }
        .station-list { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; max-width: 100%; margin-top: 14px; }
        .station-card { width: 100%; max-width: 100%; min-width: 0; border: 1px solid var(--border); border-radius: 14px; padding: 14px; background: var(--card-soft); overflow-wrap: anywhere; word-break: break-word; }
        .station-card h3 { margin: 0 0 8px; font-size: 17px; }
        .station-card p { margin: 5px 0; color: var(--muted); }
        .badge { display: inline-flex; align-items: center; min-height: 26px; border-radius: 999px; padding: 3px 9px; background: rgba(255,255,255,.08); color: var(--muted); font-size: 12px; font-weight: 850; }
        .resource-form { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: end; max-width: 100%; }
        @media (max-width: 1000px) { main { padding: 20px 18px 44px; } .main-grid { grid-template-columns: 1fr; } #map-frame { height: 320px; } }
        @media (max-width: 700px) { main { padding-left: 12px; padding-right: 12px; } .topline { align-items: flex-start; } .search, .commands, .assistance-controls, .resource-form { grid-template-columns: minmax(0, 1fr); } .commands { gap: 9px; } button, select { width: 100%; } .card, #result, #map-section, .tool-panel, #assistance-section, #resource-section { padding: 16px; } #map-frame { height: 280px; } }
        @media (max-width: 480px) { main { padding: 14px 12px 34px; } h1 { font-size: 24px; } .topline { display: block; } .logout { margin-top: 14px; width: 100%; } #map-frame { height: 260px; } #report { font-size: 15px; } }
        @media print {
            body { background: #fff; color: #000; }
            main { max-width: none; padding: 0; }
            .topline, .search-card, #status, .actions, #map-section, #assistance-section, #resource-section, .tool-panel, .side-stack { display: none !important; }
            .main-grid { display: block; }
            #result { display: block !important; border: 0; box-shadow: none; padding: 0; background: #fff; color: #000; }
            #report h3 { color: #000; }
            a { color: #000; text-decoration: none; }
        }
    </style>
</head>
<body>
    <main>
        <div class="topline"><div><h1>IndsatsBrief Brand</h1><p class="intro">Adressebrief · BBR · OSM · Vejr · Assistance</p></div><a class="logout" href="/brief-logout">Log ud</a></div>
        <section class="card search-card" aria-label="Adresse og handlinger">
            <form id="brief-form" class="search">
                <div class="address-field"><label>Adresse<input id="address" name="address" required autocomplete="off" placeholder="Fx Hovedgaden 1, 4000 Roskilde"></label><div id="autocomplete-list" class="autocomplete-list" role="listbox"></div></div>
                <label>Radius (m)<input id="radius" name="radius" type="number" min="1" value="250"></label>
                <div class="commands"><button id="submit" data-mode="short" type="submit">Kort brief</button><button data-mode="full" type="submit" class="secondary">Fuld rapport</button></div>
            </form>
        </section>
        <p id="status" role="status" data-state="ready">Klar</p>
        <div class="main-grid">
            <div>
                <section id="result" aria-live="polite"><div id="report"></div></section>
                <div id="actions" class="actions">
                    <button id="copy" type="button" class="secondary">Kopiér rapport</button>
                    <button id="print" type="button" class="secondary">Print/gem som PDF</button>
                </div>
            </div>
            <div class="side-stack">
                <section id="map-section">
                    <h2>Kort</h2>
                    <iframe id="map-frame" title="Kort over adresse"></iframe>
                    <div class="map-links">
                        <a id="open-map" target="_blank" rel="noopener noreferrer">Link til kort</a>
                        <a id="open-satellite" target="_blank" rel="noopener noreferrer" hidden>Link til Google satellit</a>
                    </div>
                </section>
                <section id="assistance-section"><h2>Assistance</h2><div class="assistance-controls"><button id="assistance-button" type="button" class="assistance-primary" disabled>Assistance</button><label>Radius<select id="assistance-radius"><option value="20">20 km</option><option value="40" selected>40 km</option><option value="60">60 km</option><option value="100">100 km</option></select></label><label>Vis stationer<select id="assistance-limit"><option value="5" selected>5</option><option value="10">10</option></select></label></div><div id="assistance-result"><p class="empty-hint">Lav først et adresseopslag for at se nærmeste assistance.</p></div></section>
                <section id="resource-section"><h2>Ressourcesøgning</h2><div class="resource-form"><label>Ressource<input id="resource-search-input" placeholder="Søg fx stige, tankvogn, kemi, redningsbåd, TAF 60…"></label><button id="resource-search-button" type="button">Søg ressource</button></div><div id="resource-result" class="tool-result"></div></section>
                <section class="tool-panel"><h2>Spørg til rapporten</h2><textarea id="side-followup-question" placeholder="Spørg fx: Er der kælder? Hvad er tagmaterialet? Hvad viser OSM?"></textarea><button id="side-ask-followup" type="button">Spørg</button><div id="side-followup-result" class="tool-result"></div></section>
            </div>
        </div>
    </main>
    <script>
        const forbiddenKeys = new Set([
            'tactical_note', 'fire_relevant_notes', 'limitations', 'critical_missing',
            'preliminary_access_assessment', 'verification_status', 'utilities', 'hazmat'
        ]);
        const excludedText = /ikke oplyst|ukendt|ikke tilgængeligt/i;

        function cleanValue(value) {
            if (value === null || value === undefined || value === '') return null;
            if (typeof value === 'string') {
                if (excludedText.test(value)) return null;
                const cleaned = value
                    .replace(/\s*ifølge\s+bbr\s*[–-]\s*ikke verificeret operativt\b/gi, '')
                    .replace(/\s*[–,-]?\s*ikke verificeret operativt\b/gi, '')
                    .replace(/\s*[–,-]?\s*skal verificeres\b/gi, '')
                    .replace(/\s*[–,-]?\s*ikke verificeret\b/gi, '')
                    .replace(/\s{2,}/g, ' ')
                    .replace(/[\s,;–-]+$/, '')
                    .trim();
                return cleaned && !excludedText.test(cleaned) ? cleaned : null;
            }
            if (Array.isArray(value)) return value.map(cleanValue).filter(item => item !== null);
            if (typeof value === 'object') {
                return Object.fromEntries(Object.entries(value)
                    .filter(([key]) => !forbiddenKeys.has(key))
                    .map(([key, item]) => [key, cleanValue(item)])
                    .filter(([, item]) => item !== null && (!Array.isArray(item) || item.length)));
            }
            return value;
        }

        function hasValue(value) {
            return value !== null && value !== undefined && value !== '' && (!Array.isArray(value) || value.length > 0);
        }

        function positiveFallback(data) {
            const addressDetails = data.address_details || {};
            const building = data.building || {};
            const weather = data.weather && !data.weather.error ? data.weather : {};
            const osm = data.osm_risk_check || {};
            const water = data.water_supply || {};
            const fallback = {
                address: {
                    requested_address: data.normalized_address || addressDetails.normalized_address,
                    matched_address: addressDetails.normalized_address
                },
                coordinates: { latitude: data.latitude, longitude: data.longitude },
                map_url: data.map && data.map.map_url,
                google_maps_satellite: data.aerial_photo && data.aerial_photo.links && data.aerial_photo.links.google_maps_satellite,
                building: {
                    building_type_text: building.building_type_text || building.usage_text,
                    area_m2: building.area_m2,
                    construction_year: building.construction_year,
                    renovation_year: building.renovation_year,
                    outer_wall_material_text: building.outer_wall_material_text,
                    roof_material_text: building.roof_material_text,
                    heating_installation_text: building.heating_installation_text,
                    heating_fuel_text: building.heating_fuel_text,
                    supplementary_heating_text: building.supplementary_heating_text,
                    basement_present: building.basement_present,
                    basement_area_m2: building.basement_area_m2,
                    secondary_buildings: building.secondary_buildings
                },
                osm_risk_summary: osm.osm_risk_summary,
                weather: weather,
                water_supply: water.hydrant_count > 0 ? { hydrant_count: water.hydrant_count } : null
            };
            return cleanValue(fallback);
        }

        function formatValue(value, unit = '') {
            const cleaned = cleanValue(value);
            return hasValue(cleaned) ? `${cleaned}${unit}` : null;
        }

        function buildReport(data) {
            const report = cleanValue(data.short_report_data) || positiveFallback(data) || {};
            const lines = ['HURTIG INDSATSBRIEF', ''];
            const address = report.address || {};
            const addressLines = [];
            const requested = cleanValue(address.requested_address || address.matched_address);
            const matched = cleanValue(address.matched_address);
            if (requested) addressLines.push(requested);
            if (matched && matched !== requested) addressLines.push(`Registreret adresse: ${matched}`);
            const coordinates = report.coordinates || {};
            if (hasValue(coordinates.latitude) && hasValue(coordinates.longitude)) addressLines.push(`Koordinater: ${coordinates.latitude}, ${coordinates.longitude}`);
            if (cleanValue(report.map_url)) addressLines.push(`Kort: ${cleanValue(report.map_url)}`);
            if (cleanValue(report.google_maps_satellite)) addressLines.push(`Google satellit: ${cleanValue(report.google_maps_satellite)}`);
            if (address.note && cleanValue(address.note)) addressLines.push(cleanValue(address.note));
            if (addressLines.length) lines.push('Adresse:', ...addressLines.map(item => `- ${item}`), '');

            const building = report.building || {};
            const findings = [];
            const buildingLines = [];
            const buildingLabels = [
                ['building_type_text', 'Bygningstype'], ['construction_year', 'Opført'],
                ['renovation_year', 'Ombygget'], ['area_m2', 'Samlet bygningsareal', ' m²'],
                ['residential_area_m2', 'Boligareal', ' m²'], ['built_area_m2', 'Bebygget areal', ' m²'],
                ['floors_count', 'Antal etager']
            ];
            buildingLabels.forEach(([key, label, unit]) => {
                const value = formatValue(building[key], unit || '');
                if (value) buildingLines.push(`${label}: ${value}`);
            });

            const detailLines = [];
            [['outer_wall_material_text', 'Facade'], ['roof_material_text', 'Tagdækning'], ['preservation_status_text', 'Fredning/bevaring']].forEach(([key, label]) => {
                const value = formatValue(building[key]);
                if (value) detailLines.push(`${label}: ${value}`);
            });

            const heatingLines = [];
            [['heating_installation_text', 'Varme'], ['heating_fuel_text', 'Brændsel'], ['supplementary_heating_text', 'Supplerende varme']].forEach(([key, label]) => {
                const value = formatValue(building[key]);
                if (value) heatingLines.push(`${label}: ${value}`);
            });

            const basementArea = Number(building.basement_area_m2);
            const basementLines = [];
            if (building.basement_present === true || basementArea > 0) {
                findings.push('Kælder registreret');
                basementLines.push(basementArea > 0 ? `Kælder: ${basementArea} m²` : 'Kælder registreret');
            }
            const secondaryLines = [];
            (building.secondary_buildings || []).forEach(item => {
                const secondary = cleanValue(item) || {};
                const text = cleanValue(secondary.display_text) || [secondary.building_type_text || secondary.usage_text, secondary.construction_year && `fra ${secondary.construction_year}`, secondary.roof_material_text && `tag ${secondary.roof_material_text}`].filter(Boolean).join(', ');
                if (text) secondaryLines.push(text);
            });
            if (secondaryLines.length) {
                findings.push('Sekundære bygninger registreret');
            }
            if (findings.length) lines.push('Fund:', ...findings.map(item => `- ${item}`), '');
            if (buildingLines.length) lines.push('Bygning:', ...buildingLines.map(item => `- ${item}`), '');
            if (detailLines.length) lines.push('Bygningsdetaljer:', ...detailLines.map(item => `- ${item}`), '');
            if (heatingLines.length) lines.push('Varme:', ...heatingLines.map(item => `- ${item}`), '');
            if (secondaryLines.length) lines.push('Sekundære bygninger:', ...dedupeSecondaryLines(secondaryLines).map(item => `- ${item}`), '');
            if (basementLines.length) lines.push('Kælder:', ...basementLines.map(item => `- ${item}`), '');

            const osmFindings = (report.osm_risk_summary || []).map(cleanValue).filter(Boolean).map(item => {
                const detail = [item.category, item.count && `${item.count} fund`, item.nearest_distance_m && `${item.nearest_distance_m} m`].filter(Boolean);
                return detail.join(': ').replace(': ', ' - ');
            }).filter(Boolean);
            if (osmFindings.length) lines.push('OSM-risikotjek:', ...osmFindings.map(item => `- ${item}`), '');

            const weather = report.weather || {};
            const weatherLines = [
                formatValue(weather.temperature_c, ' °C') && `Temperatur: ${formatValue(weather.temperature_c, ' °C')}`,
                cleanValue(weather.wind_direction_text) && `Vindretning: ${cleanValue(weather.wind_direction_text)}`,
                formatValue(weather.wind_speed_ms, ' m/s') && `Vindhastighed: ${formatValue(weather.wind_speed_ms, ' m/s')}`,
                formatValue(weather.wind_gust_ms, ' m/s') && `Vindstød: ${formatValue(weather.wind_gust_ms, ' m/s')}`,
                formatValue(weather.precipitation, ' mm') && `Nedbør: ${formatValue(weather.precipitation, ' mm')}`
            ].filter(Boolean);
            if (weatherLines.length) lines.push('Vejr/vind:', ...weatherLines.map(item => `- ${item}`), '');

            const water = report.water_supply || {};
            if (Number(water.hydrant_count) > 0) lines.push('Vandforsyning:', `- Brandhanefund: ${water.hydrant_count}`, '');
            lines.push('Forbehold:', '- Data fra OSM, BBR og kort-/luftfotolinks er støtteoplysninger.');
            return lines.join('\n');
        }

        const form = document.getElementById('brief-form');
        const status = document.getElementById('status');
        const result = document.getElementById('result');
        const reportElement = document.getElementById('report');
        const actions = document.getElementById('actions');
        const mapSection = document.getElementById('map-section');
        const mapFrame = document.getElementById('map-frame');
        const openMap = document.getElementById('open-map');
        const openSatellite = document.getElementById('open-satellite');
        const assistanceButton = document.getElementById('assistance-button');
        const assistanceRadius = document.getElementById('assistance-radius');
        const assistanceLimit = document.getElementById('assistance-limit');
        const assistanceSection = document.getElementById('assistance-section');
        const assistanceResult = document.getElementById('assistance-result');
        const addressInput = document.getElementById('address');
        const autocompleteList = document.getElementById('autocomplete-list');
        const resourceSearchInput = document.getElementById('resource-search-input');
        const resourceSearchButton = document.getElementById('resource-search-button');
        const resourceResult = document.getElementById('resource-result');
        let reportText = '';
        let latestIncidentData = null;
        let latestReportText = '';
        let latestReportStructured = null;
        let assistanceResults = [];
        let nearestResourceResults = [];
        let followupAnswer = '';
        let followupMessages = [];
        let lastSearchedAddress = '';

        function setStatus(message, state = 'ready') {
            status.textContent = message;
            status.dataset.state = state;
        }

        function clearRenderedReport() {
            reportElement.replaceChildren();
            reportText = '';
            result.style.display = 'none';
            actions.style.display = 'none';
        }

        function currentAssistanceAddress() {
            return latestIncidentData?.requested_address || addressInput.value.trim();
        }

        function updateAssistanceButtonState() {
            assistanceButton.disabled = !currentAssistanceAddress();
        }

        function clearAssistanceResults(message = null) {
            assistanceResults = [];
            const text = message || (currentAssistanceAddress()
                ? 'Tryk Assistance for at hente resultater for adressen.'
                : 'Lav først et adresseopslag for at se nærmeste assistance.');
            assistanceResult.innerHTML = `<p class="empty-hint">${text}</p>`;
            updateAssistanceButtonState();
        }

        function clearNearestResourceResults() {
            nearestResourceResults = [];
            resourceSearchInput.value = '';
            resourceResult.replaceChildren();
            resourceResult.style.display = 'none';
        }

        function clearFollowupArea() {
            followupAnswer = '';
            followupMessages = [];
            document.getElementById('side-followup-question').value = '';
            document.getElementById('side-followup-result').replaceChildren();
            document.getElementById('side-followup-result').style.display = 'none';
        }

        function clearMapArea() {
            mapFrame.removeAttribute('src');
            openMap.removeAttribute('href');
            openSatellite.removeAttribute('href');
            openSatellite.hidden = true;
            mapSection.style.display = 'none';
        }

        function resetBriefStateForNewSearch() {
            latestIncidentData = null;
            latestReportText = '';
            latestReportStructured = null;
            clearRenderedReport();
            clearAssistanceResults(addressInput.value.trim() ? 'Tryk Assistance for at hente resultater for den nye adresse.' : null);
            clearNearestResourceResults();
            clearFollowupArea();
            clearMapArea();
        }

        async function fetchJson(url, options = {}) {
            const response = await fetch(url, options);
            const text = await response.text();
            let data;

            try {
                data = JSON.parse(text);
            } catch (error) {
                const preview = text.replace(/\s+/g, ' ').trim().slice(0, 180);
                throw new Error(`API’en returnerede ikke JSON (HTTP ${response.status}). ${preview ? `Svar: ${preview}` : 'Serveren sendte sandsynligvis en HTML-fejlside. Tjek Render logs.'}`);
            }

            if (!response.ok) {
                const apiError = new Error(data.error || `API-fejl ${response.status}`);
                apiError.data = data;
                throw apiError;
            }

            return data;
        }

        function hideAutocomplete() {
            autocompleteList.replaceChildren();
            autocompleteList.style.display = 'none';
        }

        function showAutocomplete(suggestions) {
            autocompleteList.replaceChildren();
            suggestions.forEach(suggestion => {
                const option = document.createElement('button');
                option.type = 'button';
                option.className = 'autocomplete-option';
                option.textContent = suggestion.text;
                option.addEventListener('click', () => {
                    if (suggestion.text.trim() !== lastSearchedAddress.trim()) {
                        resetBriefStateForNewSearch();
                        setStatus('Klar', 'ready');
                    }
                    addressInput.value = suggestion.text;
                    hideAutocomplete();
                });
                autocompleteList.appendChild(option);
            });
            autocompleteList.style.display = suggestions.length ? 'block' : 'none';
        }

        function appendLinkedText(container, text) {
            const source = String(text);
            const linkPattern = /\[kort\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s]+)/g;
            let cursor = 0;
            for (const match of source.matchAll(linkPattern)) {
                container.appendChild(document.createTextNode(source.slice(cursor, match.index)));
                const link = document.createElement('a');
                link.href = match[1] || match[2];
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                link.textContent = match[1] ? 'Link til kort' : 'Link';
                container.appendChild(link);
                cursor = match.index + match[0].length;
            }
            container.appendChild(document.createTextNode(source.slice(cursor)));
        }

        function matchedResourceLabel(item) {
            const name = item.matched_resource_name;
            const type = item.matched_resource_type;
            if (name && type && String(name).toLowerCase() !== String(type).toLowerCase()) return `${name} – ${type}`;
            if (name || type) return name || type;
            return item.matched_resource || null;
        }

        function translateReportLine(line) {
            return String(line)
                .replace(/^Attic used area\s*:/i, 'Udnyttet tagetage:')
                .replace(/\battic used area\b/gi, 'udnyttet tagetage');
        }

        function normalizeLine(line) {
            return translateReportLine(line)
                .toLowerCase()
                .replace(/[æøå]/g, char => ({ æ: 'ae', ø: 'oe', å: 'aa' }[char]))
                .replace(/\[[^\]]+\]\(https?:\/\/[^)]+\)/g, '')
                .replace(/https?:\/\/\S+/g, '')
                .replace(/m²/g, 'm2')
                .replace(/^(bygning|bygningstype|anvendelse|etager|antal etager|opfoert|opfoerelsesaar|ombygget|ombygningsaar|facade|ydervaeg|ydervaegge|tag|tagdaekning|areal|samlet bygningsareal|varme|braendsel|opvarmningsmiddel|supplerende varme|bebygget areal|boligareal|erhvervsareal|kaelder|udnyttet tagetage|attic used area|sekundaer bygning)\s*:?\s*/i, '')
                .replace(/[.:;(),/]+/g, ' ')
                .replace(/\s+etager?$/g, '')
                .replace(/\s+/g, ' ')
                .trim();
        }

        function isStandardBuildingFinding(line) {
            const lowered = translateReportLine(line).toLowerCase();
            return /\bbygningstype\b|\bopført\b|\bopforelsesår\b|\bopfoert\b|\bombygget\b|\bareal\b|\bboligareal\b|\bbebygget\b|\betager?\b|\bydervæg\b|\bydervaeg\b|\bfacade\b|\btag\b|\btagdækning\b|\bvarme\b|\bbrændsel\b|\bbraendsel\b|\bopvarmningsmiddel\b|\bsupplerende varme\b/.test(lowered);
        }

        function dedupeSecondaryLines(lines) {
            const normalized = lines.map(line => [line, normalizeLine(line)]).filter(([, key]) => key);
            return normalized
                .filter(([line, key]) => !normalized.some(([otherLine, otherKey]) => key !== otherKey && key.length < otherKey.length && otherKey.includes(key)))
                .map(([line]) => line);
        }

        function dedupeSections(sections) {
            const buildingHeadings = new Set(['Bygning', 'Bygningsdetaljer', 'Kælder', 'Varme', 'Sekundære bygninger']);
            const buildingLines = new Set();
            sections.forEach(section => {
                if (buildingHeadings.has(section.heading)) {
                    section.lines.forEach(line => {
                        const key = normalizeLine(line);
                        if (key) buildingLines.add(key);
                    });
                }
            });

            const seen = new Set();
            return sections.map(section => {
                if (!section.lines.length) return section;
                const lines = [];
                section.lines.forEach(line => {
                    const translated = translateReportLine(line);
                    const key = normalizeLine(translated);
                    if (!key) return;
                    if (section.heading === 'Fund' && (isStandardBuildingFinding(translated) || [...buildingLines].some(buildingKey => key === buildingKey || key.includes(buildingKey) || buildingKey.includes(key)))) return;
                    if (section.heading === 'Bygningsdetaljer' && /\bareal\b|\bboligareal\b|\bbebygget\b/i.test(translated) && sections.some(item => item.heading === 'Bygning' && item.lines.length)) return;
                    if (seen.has(`${section.heading}:${key}`)) return;
                    if (seen.has(key) && section.heading === 'Fund') return;
                    seen.add(`${section.heading}:${key}`);
                    if (section.heading !== 'Fund') seen.add(key);
                    lines.push(translated);
                });
                return { ...section, lines: section.heading === 'Sekundære bygninger' ? dedupeSecondaryLines(lines) : lines };
            }).filter((section, index) => index === 0 || section.lines.length);
        }

        function parseReportText(text) {
            const sections = [];
            let current = null;
            String(text).split('\n').forEach(line => {
                const trimmed = line.trim();
                if (!trimmed) return;
                if (trimmed === 'HURTIG INDSATSBRIEF' || trimmed === 'FULD INDSATSBRIEF') {
                    sections.push({ heading: trimmed, lines: [] });
                    return;
                }
                if (trimmed.endsWith(':')) {
                    current = { heading: trimmed.slice(0, -1), lines: [] };
                    sections.push(current);
                } else if (current && /^[*-]\s+/.test(trimmed)) {
                    current.lines.push(translateReportLine(trimmed.replace(/^[*-]\s+/, '')));
                }
            });
            return sections;
        }

        function reportSections(reportStructured, text) {
            if (!reportStructured || !reportStructured.title) return dedupeSections(parseReportText(text));
            const definitions = [
                ['Adresse', 'address_lines'], ['Kortlinks', 'map_links'], ['Fund', 'findings'],
                ['Bygning', 'building_lines'], ['Bygningsdetaljer', 'building_details'],
                ['Varme', 'heating_lines'], ['Adgang', 'access_lines'],
                ['Sekundære bygninger', 'secondary_buildings'], ['Kælder', 'basement_lines'],
                ['Omgivelser / OSM', 'surroundings_lines'], ['Risikokontekst', 'risk_context_lines'],
                ['OSM-risikotjek', 'osm_risk_lines'], ['Vejr/vind', 'weather_lines'],
                ['Vandforsyning', 'water_supply_lines'], ['Assistance', 'assistance_lines'],
                ['Ressourcer', 'resource_lines'], ['Trafik/vejarbejde', 'traffic_lines'],
                ['Supplerende oplysninger', 'supplementary_lines'], ['Forbehold', 'disclaimer']
            ];
            const sections = [{ heading: reportStructured.title, lines: [] }];
            definitions.forEach(([heading, key]) => {
                const value = reportStructured[key];
                const lines = Array.isArray(value) ? value : (value ? [value] : []);
                if (lines.length) sections.push({ heading, lines });
            });
            return dedupeSections(sections);
        }

        function renderReport(text, reportStructured) {
            reportElement.replaceChildren();
            reportSections(reportStructured, text).forEach((section, index) => {
                if (index === 0 && !section.lines.length) {
                    const title = document.createElement('h2');
                    title.textContent = section.heading;
                    reportElement.appendChild(title);
                    return;
                }
                const heading = document.createElement('h3');
                heading.textContent = section.heading;
                reportElement.appendChild(heading);
                const list = document.createElement('ul');
                section.lines.forEach(line => {
                    const item = document.createElement('li');
                    appendLinkedText(item, line);
                    list.appendChild(item);
                });
                reportElement.appendChild(list);
            });
        }

        function findCoordinates(rawData) {
            const short = rawData.short_report_data || {};
            const coordinates = short.coordinates || {};
            const latitude = rawData.latitude ?? coordinates.latitude;
            const longitude = rawData.longitude ?? coordinates.longitude;
            if (latitude === null || latitude === undefined || longitude === null || longitude === undefined) return null;
            return Number.isFinite(Number(latitude)) && Number.isFinite(Number(longitude))
                ? { latitude: Number(latitude), longitude: Number(longitude) }
                : null;
        }

        function updateMap(rawData) {
            const coordinates = findCoordinates(rawData || {});
            if (!coordinates) {
                mapSection.style.display = 'none';
                return;
            }
            const { latitude, longitude } = coordinates;
            const bbox = [longitude - 0.004, latitude - 0.003, longitude + 0.004, latitude + 0.003].join(',');
            mapFrame.src = `https://www.openstreetmap.org/export/embed.html?bbox=${encodeURIComponent(bbox)}&layer=mapnik&marker=${encodeURIComponent(`${latitude},${longitude}`)}`;
            openMap.href = `https://www.openstreetmap.org/?mlat=${latitude}&mlon=${longitude}#map=17/${latitude}/${longitude}`;
            const satellite = rawData.google_maps_satellite || (rawData.aerial_photo || {}).links?.google_maps_satellite || (rawData.short_report_data || {}).google_maps_satellite;
            if (satellite) {
                openSatellite.href = satellite;
                openSatellite.hidden = false;
            } else {
                openSatellite.hidden = true;
            }
            mapSection.style.display = 'block';
        }

        function showReport(text, rawData, reportStructured) {
            reportText = text;
            latestReportText = text;
            latestIncidentData = rawData;
            latestReportStructured = reportStructured || null;
            renderReport(reportText, reportStructured);
            updateMap(rawData);
            result.style.display = 'block';
            actions.style.display = 'flex';
            clearAssistanceResults('Tryk Assistance for at hente resultater for denne adresse.');
        }

        form.addEventListener('submit', async event => {
            event.preventDefault();
            const address = document.getElementById('address').value.trim();
            const radius = document.getElementById('radius').value || '250';
            const mode = event.submitter?.dataset.mode || 'short';
            if (!address) return;
            if (address.trim() !== lastSearchedAddress.trim()) {
                resetBriefStateForNewSearch();
            } else {
                clearRenderedReport();
                clearMapArea();
            }
            lastSearchedAddress = address.trim();
            document.getElementById('submit').disabled = true;
            setStatus('Henter data…', 'loading');
            try {
                const params = new URLSearchParams({ address: address, radius_m: radius, mode: mode });
                const url = mode === 'full' ? `/full-brief?${params.toString()}` : `/analyze-brief?${params.toString()}`;
                setStatus('AI analyserer…', 'loading');
                const data = await fetchJson(url);
                if (!data.report_text) throw new Error('Analyse returnerede ingen rapporttekst');
                showReport(data.report_text, data.raw_incident_data || data, data.report_structured);
                setStatus('Færdig', 'done');
            } catch (error) {
                const fallbackData = error.data && error.data.raw_incident_data;

                if (fallbackData) {
                    showReport(error.data.report_text || buildReport(fallbackData), fallbackData, error.data.report_structured);
                    setStatus(`${error.message}. Viser rapport uden AI-analyse.`, 'error');
                } else {
                    try {
                        const params = new URLSearchParams({ address: address, radius_m: radius });
                        const fallback = await fetchJson(`/incident-brief?${params.toString()}`);
                        showReport(buildReport(fallback), fallback);
                        setStatus('AI-analyse fejlede. Viser rapport uden AI-analyse.', 'error');
                    } catch (fallbackError) {
                        setStatus(fallbackError.message || error.message || 'Kunne ikke hente indsatsbrief.', 'error');
                    }
                }
            } finally {
                document.getElementById('submit').disabled = false;
            }
        });

        let autocompleteTimer;
        addressInput.addEventListener('input', () => {
            clearTimeout(autocompleteTimer);
            const query = addressInput.value.trim();
            if (lastSearchedAddress && query !== lastSearchedAddress.trim()) {
                resetBriefStateForNewSearch();
                setStatus('Klar', 'ready');
                lastSearchedAddress = '';
            }
            updateAssistanceButtonState();
            if (query.length < 3) { hideAutocomplete(); return; }
            autocompleteTimer = setTimeout(async () => {
                try {
                    const data = await fetchJson(`/address-autocomplete?${new URLSearchParams({ q: query }).toString()}`);
                    if (addressInput.value.trim() === query) showAutocomplete(data.suggestions || []);
                } catch (error) { hideAutocomplete(); }
            }, 250);
        });
        document.addEventListener('click', event => {
            if (!event.target.closest('.address-field')) hideAutocomplete();
        });

        document.getElementById('copy').addEventListener('click', async () => {
            if (!reportText) return;
            await navigator.clipboard.writeText(reportText);
            setStatus('Rapporten er kopieret.', 'done');
        });
        document.getElementById('print').addEventListener('click', () => window.print());
        async function askFollowup(questionElement, output) {
            const question = questionElement.value.trim();
            if (!latestIncidentData) { output.textContent = 'Lav først et adresseopslag.'; output.style.display = 'block'; return; }
            if (!question) { output.textContent = 'Skriv et spørgsmål.'; output.style.display = 'block'; return; }
            output.textContent = 'Henter svar...'; output.style.display = 'block';
            try {
                const data = await fetchJson('/brief-followup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question, incident_data: latestIncidentData, report_text: latestReportText, report_structured: latestReportStructured }) });
                output.textContent = data.answer || 'Intet svar modtaget.';
            } catch (error) { output.textContent = error.message || 'Kunne ikke hente svar.'; }
        }
        document.getElementById('side-ask-followup').addEventListener('click', async () => {
            await askFollowup(document.getElementById('side-followup-question'), document.getElementById('side-followup-result'));
        });
        assistanceButton.addEventListener('click', async () => {
            const address = currentAssistanceAddress();
            if (!address) {
                assistanceResult.innerHTML = '<p class="empty-hint">Indtast en adresse først.</p>';
                updateAssistanceButtonState();
                return;
            }
            assistanceResult.textContent = 'Henter assistance…';
            try {
                const params = new URLSearchParams({ address, radius_km: assistanceRadius.value, limit: assistanceLimit.value });
                const data = await fetchJson(`/assistance-stations?${params.toString()}`);
                assistanceResults = data.stations || [];
                assistanceResult.replaceChildren();
                const count = document.createElement('p');
                count.textContent = `Viser ${data.stations.length} nærmeste brand-/redningsberedskaber`;
                assistanceResult.appendChild(count);
                const list = document.createElement('div');
                list.className = 'station-list';
                data.stations.forEach(station => {
                    const card = document.createElement('article');
                    card.className = 'station-card';
                    const title = document.createElement('h3');
                    title.textContent = station.name;
                    card.appendChild(title);
                    const resourceLabel = matchedResourceLabel(station);
                    const parts = [
                        resourceLabel && `Ressource: ${resourceLabel}`,
                        station.type && `Type: ${station.type}`,
                        station.organization && `Organisation: ${station.organization}`,
                        station.operator && `Operatør: ${station.operator}`,
                        station.area && `Område: ${station.area}`,
                        `Luftlinje: ${String(station.air_distance_km).replace('.', ',')} km`
                    ].filter(Boolean);
                    if (station.road_distance_km !== null && station.drive_time_min !== null) {
                        parts.push(`Vej: ${String(station.road_distance_km).replace('.', ',')} km`);
                        parts.push(`Ca. ${station.drive_time_min} min.`);
                    } else {
                        parts.push('Vejafstand ikke tilgængelig.');
                    }
                    parts.push(`Kilde: ${station.source || 'ikke angivet'}`);
                    parts.forEach(part => { const line = document.createElement('p'); line.textContent = part; card.appendChild(line); });
                    list.appendChild(card);
                });
                assistanceResult.appendChild(list);
                const disclaimer = document.createElement('p');
                disclaimer.textContent = data.disclaimer;
                assistanceResult.appendChild(disclaimer);
            } catch (error) {
                assistanceResult.textContent = error.message || 'Kunne ikke hente assistanceoplysninger.';
            }
        });
        resourceSearchButton.addEventListener('click', async () => {
            const address = latestIncidentData?.requested_address || addressInput.value.trim();
            const resource = resourceSearchInput.value.trim();
            if (!address) { resourceResult.textContent = 'Udfyld en adresse eller lav en brief først.'; resourceResult.style.display = 'block'; return; }
            if (!resource) { resourceResult.textContent = 'Skriv en ressource at søge efter.'; resourceResult.style.display = 'block'; return; }
            resourceResult.textContent = 'Søger i stationsfilen…';
            resourceResult.style.display = 'block';
            try {
                const params = new URLSearchParams({ address, resource, radius_km: '100', limit: '5' });
                const data = await fetchJson(`/nearest-resource?${params.toString()}`);
                nearestResourceResults = data.results || [];
                resourceResult.replaceChildren();
                if (!data.results || !data.results.length) {
                    resourceResult.textContent = data.message || 'Ingen registrerede ressourcer fundet i stationsfilen for den søgning.';
                    return;
                }
                const list = document.createElement('div');
                list.className = 'station-list';
                data.results.forEach(result => {
                    const card = document.createElement('article');
                    card.className = 'station-card';
                    const title = document.createElement('h3');
                    title.textContent = result.station_name || result.matched_resource || resource;
                    card.appendChild(title);
                    const badge = document.createElement('span');
                    badge.className = 'badge';
                    badge.textContent = result.operational_response_station ? 'Primær station' : 'Støtte/ikke primær station';
                    card.appendChild(badge);
                    const resourceLabel = matchedResourceLabel(result);
                    [
                        resourceLabel && `Ressource: ${resourceLabel}`,
                        result.type && `Type: ${result.type}`,
                        result.organization && `Organisation: ${result.organization}`,
                        result.operator && `Operatør: ${result.operator}`,
                        !result.operator && !result.organization && result.authority && `Organisation: ${result.authority}`,
                        result.area && `Område: ${result.area}`,
                        result.air_distance_km !== null && result.air_distance_km !== undefined && `Luftlinje: ${String(result.air_distance_km).replace('.', ',')} km`,
                        result.road_distance_km !== null && result.road_time_min !== null && `Vej: ${String(result.road_distance_km).replace('.', ',')} km · ca. ${result.road_time_min} min.`,
                        result.source && `Kilde: ${result.source}`
                    ].filter(Boolean).forEach(text => { const p = document.createElement('p'); p.textContent = text; card.appendChild(p); });
                    list.appendChild(card);
                });
                resourceResult.appendChild(list);
                const disclaimer = document.createElement('p');
                disclaimer.textContent = data.disclaimer;
                resourceResult.appendChild(disclaimer);
            } catch (error) {
                resourceResult.textContent = error.message || 'Kunne ikke søge ressource.';
            }
        });
    </script>
</body>
</html>
    """
    return Response(html, mimetype="text/html")


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
        },
        "basement_debug": {
            "bbr_etage_attempts": bbr_address_result.get("bbr_etage_attempts", []),
            "etage_nodes_by_building_id": bbr_address_result.get("etage_nodes_by_building_id", {}),
            "normalized": {
                "basement_area_m2": normalized_building.get("basement_area_m2"),
                "basement_present": normalized_building.get("basement_present"),
                "basement_source": normalized_building.get("basement_source"),
                "basement_raw": normalized_building.get("basement_raw")
            },
            "fallback": {
                "basement_area_m2": fallback_building.get("basement_area_m2"),
                "basement_present": fallback_building.get("basement_present"),
                "basement_source": fallback_building.get("basement_source"),
                "basement_raw": fallback_building.get("basement_raw")
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


def build_incident_brief_data(address, radius_m):
    """Collect the existing incident brief data for API and AI routes alike."""
    warnings = []

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

    try:
        water_supply_data = get_possible_hydrants_from_osm(latitude, longitude, radius_m)
    except Exception as e:
        app.logger.exception("Hydrant/Overpass lookup failed")
        warnings.append("Brandhane-/vandforsyningsdata kunne ikke hentes inden for timeout.")
        water_supply_data = {
            "source": "osm_overpass",
            "error": str(e),
            "hydrants": [],
            "hydrant_count": 0,
            "alternative_water": [],
            "verification_status": "Brandhaner/vandforsyning ikke hentet"
        }
    aerial_check_data = get_aerial_check(address_data, radius_m)
    try:
        osm_risk_check_data = get_osm_risk_check(latitude, longitude, radius_m)
        if isinstance(osm_risk_check_data, dict) and osm_risk_check_data.get("ok") is False:
            warnings.append("OSM-risikodata kunne ikke hentes inden for timeout.")
    except Exception as e:
        app.logger.exception("OSM risk lookup failed")
        warnings.append("OSM-risikodata kunne ikke hentes inden for timeout.")
        osm_risk_check_data = osm_overpass_fallback(str(e), radius_m=radius_m)
    nearby_main_road = get_nearby_main_roads(latitude, longitude) if latitude is not None and longitude is not None else None
    access_context_lines = []
    if isinstance(nearby_main_road, dict) and is_positive_report_value(nearby_main_road.get("summary")):
        access_context_lines.append(nearby_main_road["summary"])
    traffic_events_nearby = get_traffic_events_nearby(latitude, longitude) if latitude is not None and longitude is not None else []
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
        weather_data,
        water_supply_data
    )

    data = {
        "normalized_address": normalized_address,
        "requested_address": address,
        "matched_address": normalized_address,
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
        "gpt_short_report_instruction": (
            "Brug short_report_data til kort rapport. Du må gerne analysere rå data, "
            "men skriv ikke taktisk fokus, kritiske mangler eller ikke-verificeret-tekster "
            "efter hvert fund. Brug kun én samlet forbeholdslinje nederst."
        ),

        "weather": weather_data,
        "building": building_data,
        "road": get_road_placeholder(address_data),
        "water_supply": water_supply_data,
        "osm_risk_check": osm_risk_check_data,
        "nearby_main_road": nearby_main_road,
        "nearest_main_road": nearby_main_road,
        "access_context_lines": access_context_lines,
        "traffic_events_nearby": traffic_events_nearby,
        "warnings": warnings,

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

    return data


AI_BLOCKED_REPORT_PHRASES = (
    "taktisk",
    "første taktiske fokus",
    "taktisk fokus",
    "kritiske mangler",
    "mangler",
    "disponering",
    "disponer",
    "afsend",
    "anbefalet station",
    "bør afsendes",
    "ikke fundet",
    "manglende",
    "ingen data",
    "ingen fund",
)


def build_osm_findings_for_ai(osm_risk_check, limit=10):
    """Keep the most useful OSM detail while keeping the model payload compact."""
    findings_for_ai = []

    for finding in (osm_risk_check or {}).get("findings", [])[:limit]:
        tags = finding.get("raw_tags") or {}
        categories = [
            category.get("category")
            for category in finding.get("categories", [])
            if category.get("category")
        ]
        relevant_tags = {
            key: tags.get(key)
            for key in [
                "barrier", "access", "gate", "locked", "amenity",
                "landuse", "railway", "waterway", "natural", "man_made",
            ]
            if tags.get(key) is not None
        }

        findings_for_ai.append({
            "category": categories[0] if categories else None,
            "type": (
                tags.get("barrier") or tags.get("railway") or tags.get("amenity")
                or tags.get("landuse") or tags.get("waterway") or tags.get("man_made")
            ),
            "name": finding.get("name"),
            "distance_m": finding.get("distance_m"),
            "lat": finding.get("latitude"),
            "lon": finding.get("longitude"),
            "map_url": finding.get("map_url"),
            "tags": relevant_tags,
        })

    return findings_for_ai


def build_concrete_osm_risk_lines(osm_risk_check):
    """Render OSM tags as short concrete lines instead of count-only summaries."""
    grouped = {}

    for finding in (osm_risk_check or {}).get("findings", []):
        tags = finding.get("raw_tags") or {}
        categories = finding.get("categories", []) or []
        category_names = [item.get("category") for item in categories if item.get("category")]
        category = category_names[0] if category_names else "OSM-fund"
        group = grouped.setdefault(category, {"distance_m": None, "types": [], "names": [], "map_url": None})

        distance_m = parse_positive_number(finding.get("distance_m"))
        if distance_m is not None and (
            group["distance_m"] is None or distance_m < group["distance_m"]
        ):
            group["distance_m"] = distance_m
            group["map_url"] = finding.get("map_url")

        name = clean_short_report_text(finding.get("name"))
        if is_positive_report_value(name) and name not in group["names"]:
            group["names"].append(name)

        if tags.get("barrier") in ["gate", "lift_gate"]:
            detail = "port/låge"
        elif tags.get("barrier"):
            detail = f"barriere ({tags['barrier']})"
        elif tags.get("access"):
            detail = f"adgangsbegrænsning ({tags['access']})"
        elif tags.get("railway"):
            detail = "jernbane/spor"
        elif tags.get("amenity"):
            detail = tags["amenity"]
        elif tags.get("landuse"):
            detail = tags["landuse"]
        elif tags.get("waterway"):
            detail = "vandløb"
        elif tags.get("natural") == "water":
            detail = "vand/sø"
        elif tags.get("man_made") == "storage_tank":
            detail = "tank/oplag"
        else:
            detail = None

        if detail and detail not in group["types"]:
            group["types"].append(detail)

    lines = []
    for category, group in grouped.items():
        description = ", ".join(group["types"][:2]) or category.lower()
        name_suffix = f" ({group['names'][0]})" if group["names"] else ""
        distance_suffix = (
            f", nærmeste ca. {round(group['distance_m'])} m"
            if group["distance_m"] is not None else ""
        )
        map_suffix = f" [kort]({group['map_url']})" if group["map_url"] else ""
        lines.append(f"{category}: {description} registreret i OSM{name_suffix}{distance_suffix}.{map_suffix}")

    return lines


def build_openai_brief_payload(raw_incident_data):
    """Provide both curated and relevant raw incident data to the model."""
    building = raw_incident_data.get("building", {})
    map_data = raw_incident_data.get("map", {})
    aerial_photo = raw_incident_data.get("aerial_photo", {})
    osm_risk_check = raw_incident_data.get("osm_risk_check", {})

    return {
        "short_report_data": raw_incident_data.get("short_report_data"),
        "address": raw_incident_data.get("address_details"),
        "requested_address": raw_incident_data.get("requested_address"),
        "matched_address": raw_incident_data.get("matched_address"),
        "coordinates": {
            "latitude": raw_incident_data.get("latitude"),
            "longitude": raw_incident_data.get("longitude"),
        },
        "map_url": map_data.get("map_url"),
        "google_maps_satellite": aerial_photo.get("links", {}).get("google_maps_satellite"),
        "building": building,
        "secondary_buildings": building.get("secondary_buildings", []),
        "floors_raw": building.get("floors_raw", []),
        "basement_present": building.get("basement_present"),
        "basement_area_m2": building.get("basement_area_m2"),
        "basement_living_area_m2": building.get("basement_living_area_m2"),
        "basement_commercial_area_m2": building.get("basement_commercial_area_m2"),
        "floors_and_basement": {
            "floors_count": building.get("floors_count"),
            "floors_summary": building.get("floors_summary"),
            "basement_present": building.get("basement_present"),
            "basement_area_m2": building.get("basement_area_m2"),
            "basement_living_area_m2": building.get("basement_living_area_m2"),
            "basement_commercial_area_m2": building.get("basement_commercial_area_m2"),
            "attic_used_area_m2": building.get("attic_used_area_m2"),
        },
        "weather": raw_incident_data.get("weather"),
        "osm_risk_check": osm_risk_check,
        "osm_risk_summary": osm_risk_check.get("osm_risk_summary", []),
        "grouped_summary": osm_risk_check.get("grouped_summary", {}),
        "osm_findings_for_ai": build_osm_findings_for_ai(osm_risk_check),
        "nearby_main_road": raw_incident_data.get("nearby_main_road"),
        "nearest_main_road": raw_incident_data.get("nearest_main_road"),
        "access_context_lines": raw_incident_data.get("access_context_lines", []),
        "traffic_events_nearby": raw_incident_data.get("traffic_events_nearby", []),
        "water_supply": raw_incident_data.get("water_supply"),
    }


def raw_incident_has_basement(raw_incident_data):
    building = raw_incident_data.get("building", {})
    basement_area = parse_positive_number(building.get("basement_area_m2"))
    return building.get("basement_present") is True or (basement_area is not None and basement_area > 0)


def translate_report_label_text(value):
    if not isinstance(value, str):
        return value
    return re.sub(
        r"^Attic used area\s*:",
        "Udnyttet tagetage:",
        value,
        flags=re.IGNORECASE,
    )


REPORT_SECTION_FIELDS = [
    "findings",
    "building_lines",
    "building_details",
    "access_lines",
    "secondary_buildings",
    "basement_lines",
    "heating_lines",
    "surroundings_lines",
    "risk_context_lines",
    "osm_risk_lines",
    "weather_lines",
    "water_supply_lines",
    "assistance_lines",
    "resource_lines",
    "traffic_lines",
    "supplementary_lines",
]


def normalize_report_line(line):
    """Normalize report bullets so duplicate facts can be compared across labels."""
    if not isinstance(line, str):
        return ""

    normalized = line.lower().strip()
    for source, target in [("æ", "ae"), ("ø", "oe"), ("å", "aa")]:
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"\[[^\]]+\]\(https?://[^)]+\)", "", normalized)
    normalized = re.sub(r"https?://\S+", "", normalized)
    normalized = normalized.replace("m²", "m2")
    normalized = re.sub(r"\b(iflg|ifolge|ifølge)\s+bbr\b", "", normalized)
    normalized = re.sub(
        r"^(bygningstype|bygning|anvendelse|facade|ydervaegge|ydervaeg|tagdaekning|tag|areal|samlet bygningsareal|boligareal|bebygget areal|varme|varmeinstallation|braendsel|opvarmningsmiddel|supplerende varme|kaelder|sekundaer bygning)\s*[:\-]\s*",
        "",
        normalized,
    )
    normalized = re.sub(r"[/.,:;()]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def is_standard_building_finding(line):
    normalized = normalize_report_line(line)
    lowered = line.lower() if isinstance(line, str) else ""
    standard_patterns = [
        r"\bbygningstype\b",
        r"\bopfoert\b|\bopført\b|\bopfoerelsesaar\b|\bopførelsesår\b",
        r"\bombygget\b|\bombygningsaar\b|\bombygningsår\b",
        r"\bareal\b|\bboligareal\b|\bbebygget\b|\berhvervsareal\b",
        r"\betager?\b|\betager\b",
        r"\bydervaeg\b|\bydervæg\b|\bfacade\b",
        r"\btag\b|\btagdaekning\b|\btagdækning\b",
        r"\bvarme\b|\bbraendsel\b|\bbrændsel\b|\bopvarmningsmiddel\b",
        r"\bsupplerende varme\b",
    ]
    if any(re.search(pattern, lowered) for pattern in standard_patterns):
        return True
    if re.search(r"\b(19|20)\d{2}\b", normalized) and re.search(r"\bm2\b|\bfra\b", normalized):
        return True
    return False


def dedupe_secondary_building_lines(lines):
    cleaned = [line for line in lines if normalize_report_line(line)]
    result = []
    normalized_lines = [(line, normalize_report_line(line)) for line in cleaned]
    for line, key in normalized_lines:
        if any(
            key != other_key
            and len(key) < len(other_key)
            and key in other_key
            for other_line, other_key in normalized_lines
        ):
            continue
        if line not in result:
            result.append(line)
    return result


def clean_report_sections(report_structured):
    """Keep facts in their intended section and remove repeated BBR facts from Fund."""
    if not isinstance(report_structured, dict):
        return report_structured

    cleaned = dict(report_structured)
    protected_fields = [
        "building_lines",
        "building_details",
        "heating_lines",
        "secondary_buildings",
        "basement_lines",
    ]
    protected_keys = set()
    for field in protected_fields:
        for line in cleaned.get(field, []) or []:
            key = normalize_report_line(line)
            if key:
                protected_keys.add(key)

    def is_protected_duplicate(line):
        key = normalize_report_line(line)
        if not key:
            return True
        if key in protected_keys:
            return True
        return any(
            protected_key
            and (
                key == protected_key
                or key in protected_key
                or protected_key in key
            )
            for protected_key in protected_keys
        )

    findings = []
    for line in cleaned.get("findings", []) or []:
        if is_standard_building_finding(line) or is_protected_duplicate(line):
            continue
        if line not in findings:
            findings.append(line)
    cleaned["findings"] = findings

    detail_lines = []
    building_area_keys = [
        normalize_report_line(line)
        for line in cleaned.get("building_lines", []) or []
        if re.search(r"\bareal\b|\bboligareal\b|\bbebygget\b", line.lower())
    ]
    for line in cleaned.get("building_details", []) or []:
        key = normalize_report_line(line)
        if building_area_keys and re.search(r"\bareal\b|\bboligareal\b|\bbebygget\b", line.lower()):
            continue
        if key and line not in detail_lines:
            detail_lines.append(line)
    cleaned["building_details"] = detail_lines

    cleaned["secondary_buildings"] = dedupe_secondary_building_lines(
        cleaned.get("secondary_buildings", []) or []
    )

    for field in REPORT_SECTION_FIELDS:
        if field not in cleaned:
            continue
        seen = set()
        lines = []
        for line in cleaned.get(field, []) or []:
            key = normalize_report_line(line)
            if not key or key in seen:
                continue
            seen.add(key)
            lines.append(line)
        cleaned[field] = lines

    return cleaned


def sanitize_ai_report(report, raw_incident_data, report_mode="short"):
    """Apply presentation rules again, even if the model ignores an instruction."""
    if not isinstance(report, dict):
        raise ValueError("OpenAI returnerede ikke et JSON-objekt")

    has_basement = raw_incident_has_basement(raw_incident_data)

    def clean_line(line):
        if not isinstance(line, str):
            return None

        # Preserve positive BBR facts while removing presentation-only caveats.
        cleaned = clean_short_report_text(line)
        if not isinstance(cleaned, str):
            return None

        cleaned = translate_report_label_text(cleaned.strip())
        lowered = cleaned.lower()

        if not cleaned or any(phrase in lowered for phrase in AI_BLOCKED_REPORT_PHRASES):
            return None

        if "kælder" in lowered and not has_basement:
            return None

        if REPORT_DISCLAIMER.lower() in lowered:
            return None

        return cleaned

    def clean_lines(value):
        if not isinstance(value, list):
            return []

        lines = []
        for line in value:
            cleaned = clean_line(line)
            if cleaned and cleaned not in lines:
                lines.append(cleaned)
        return lines

    cleaned_report = {
        "title": "FULD INDSATSBRIEF" if report_mode == "full" else "HURTIG INDSATSBRIEF",
        "address_lines": clean_lines(report.get("address_lines")),
        "findings": clean_lines(report.get("findings")),
        "building_lines": clean_lines(report.get("building_lines")),
        "surroundings_lines": clean_lines(report.get("surroundings_lines")),
        "osm_risk_lines": clean_lines(report.get("osm_risk_lines")),
        "weather_lines": clean_lines(report.get("weather_lines")),
        "water_supply_lines": clean_lines(report.get("water_supply_lines")),
        "assistance_lines": clean_lines(report.get("assistance_lines")),
        "map_links": clean_lines(report.get("map_links")),
        "building_details": clean_lines(report.get("building_details")),
        "access_lines": clean_lines(report.get("access_lines")),
        "secondary_buildings": clean_lines(report.get("secondary_buildings")),
        "basement_lines": clean_lines(report.get("basement_lines")),
        "heating_lines": clean_lines(report.get("heating_lines")),
        "risk_context_lines": clean_lines(report.get("risk_context_lines")),
        "resource_lines": clean_lines(report.get("resource_lines")),
        "supplementary_lines": clean_lines(report.get("supplementary_lines")),
        "traffic_lines": clean_lines(report.get("traffic_lines")),
        "disclaimer": REPORT_DISCLAIMER,
    }
    return clean_report_sections(cleaned_report)


def build_deterministic_building_sections(raw_incident_data):
    """Create presentation-safe BBR sections when the model leaves them out."""
    building = raw_incident_data.get("building") or {}
    sections = {
        "building_lines": [],
        "building_details": [],
        "heating_lines": [],
        "secondary_buildings": [],
        "basement_lines": [],
        "findings": [],
    }

    def text_value(*keys):
        for key in keys:
            value = clean_short_report_text(building.get(key))
            if is_positive_report_value(value):
                return value
        return None

    def number_value(key):
        value = building.get(key)
        return value if parse_positive_number(value) is not None else None

    building_type = text_value("building_type_text", "usage_text")
    if building_type:
        sections["building_lines"].append(f"Bygningstype: {building_type}")

    area_m2 = number_value("area_m2")
    if area_m2 is not None:
        sections["building_lines"].append(f"Samlet bygningsareal: {area_m2} m²")

    residential_area = number_value("residential_area_m2")
    if residential_area is not None:
        sections["building_lines"].append(f"Boligareal: {residential_area} m²")

    built_area = number_value("built_area_m2")
    if built_area is not None:
        sections["building_lines"].append(f"Bebygget areal: {built_area} m²")

    floors_count = number_value("floors_count")
    if floors_count is not None:
        sections["building_lines"].append(f"Antal etager: {floors_count}")

    construction_year = number_value("construction_year")
    if construction_year is not None:
        sections["building_lines"].append(f"Opført: {construction_year}")

    renovation_year = number_value("renovation_year")
    if renovation_year is not None:
        sections["building_lines"].append(f"Ombygget: {renovation_year}")

    outer_wall = text_value("outer_wall_material_text")
    if outer_wall:
        sections["building_details"].append(f"Facade: {outer_wall}")

    roof = text_value("roof_material_text")
    if roof:
        sections["building_details"].append(f"Tagdækning: {roof}")

    heating_installation = text_value("heating_installation_text")
    if heating_installation:
        sections["heating_lines"].append(f"Varme: {heating_installation}")

    heating_fuel = text_value("heating_fuel_text")
    if heating_fuel:
        sections["heating_lines"].append(f"Brændsel: {heating_fuel}")

    supplementary_heating = text_value("supplementary_heating_text")
    if supplementary_heating:
        sections["heating_lines"].append(f"Supplerende varme: {supplementary_heating}")

    basement_area = number_value("basement_area_m2")
    if raw_incident_has_basement(raw_incident_data):
        sections["findings"].append("Kælder registreret")
        sections["basement_lines"].append(
            f"Kælder: {basement_area} m²"
            if basement_area is not None else "Kælder registreret"
        )

    has_secondary = False
    for secondary in building.get("secondary_buildings", []) or []:
        secondary_type = (
            clean_short_report_text(secondary.get("building_type_text"))
            or clean_short_report_text(secondary.get("usage_text"))
        )
        if not is_positive_report_value(secondary_type):
            continue

        parts = [secondary_type]
        secondary_year = parse_positive_number(secondary.get("construction_year"))
        secondary_roof = clean_short_report_text(secondary.get("roof_material_text"))

        if secondary_year is not None:
            parts.append(f"fra {secondary_year}")
        if is_positive_report_value(secondary_roof):
            parts.append(f"tag {secondary_roof}")

        sections["secondary_buildings"].append(", ".join(parts))
        has_secondary = True

    if has_secondary:
        sections["findings"].append("Sekundære bygninger registreret")

    for key, lines in list(sections.items()):
        sections[key] = list(dict.fromkeys(lines))
    return clean_report_sections(sections)


def build_deterministic_building_findings(raw_incident_data):
    """Backward-compatible combined building facts for older fallback paths."""
    sections = build_deterministic_building_sections(raw_incident_data)
    combined = []
    for field in ["building_lines", "building_details", "heating_lines", "basement_lines", "secondary_buildings"]:
        combined.extend(sections.get(field, []))
    return list(dict.fromkeys(combined))


def build_deterministic_report_structured(raw_incident_data, report_mode="short"):
    """Build a short report from incident data when OpenAI is unavailable."""
    short_report_data = raw_incident_data.get("short_report_data") or {}
    short_address = short_report_data.get("address") or {}
    address_lines = []
    requested_address = (
        short_address.get("requested_address")
        or raw_incident_data.get("requested_address")
    )
    matched_address = (
        short_address.get("matched_address")
        or raw_incident_data.get("matched_address")
    )

    for address in [requested_address, matched_address]:
        cleaned = clean_short_report_text(address)
        if is_positive_report_value(cleaned) and cleaned not in address_lines:
            address_lines.append(cleaned)

    coordinates = short_report_data.get("coordinates") or {}
    latitude = coordinates.get("latitude", raw_incident_data.get("latitude"))
    longitude = coordinates.get("longitude", raw_incident_data.get("longitude"))
    if latitude is not None and longitude is not None:
        address_lines.append(f"Koordinater: {latitude}, {longitude}")

    map_url = short_report_data.get("map_url") or (raw_incident_data.get("map") or {}).get("map_url")
    if is_positive_report_value(clean_short_report_text(map_url)):
        address_lines.append(f"Kort: {map_url}")

    osm_risk_check = raw_incident_data.get("osm_risk_check") or {}
    osm_lines = build_concrete_osm_risk_lines(osm_risk_check)

    weather_lines = []
    weather = raw_incident_data.get("weather") or {}
    if not weather.get("error"):
        weather_fields = [
            ("temperature_c", "Temperatur", " °C"),
            ("wind_direction_text", "Vindretning", ""),
            ("wind_speed_ms", "Vindhastighed", " m/s"),
            ("wind_gust_ms", "Vindstød", " m/s"),
            ("precipitation", "Nedbør", " mm"),
        ]
        for field, label, unit in weather_fields:
            value = clean_short_report_text(weather.get(field))
            if is_positive_report_value(value):
                weather_lines.append(f"{label}: {value}{unit}")

    water_supply_lines = []
    water_supply = raw_incident_data.get("water_supply") or {}
    if parse_positive_number(water_supply.get("hydrant_count")) is not None:
        water_supply_lines.append(f"Brandhanefund: {water_supply['hydrant_count']}")

    traffic_lines = []
    for event in raw_incident_data.get("traffic_events_nearby", []) or []:
        event_type = clean_short_report_text(event.get("type"))
        road_name = clean_short_report_text(event.get("road_name"))
        distance_m = parse_positive_number(event.get("distance_m"))
        if is_positive_report_value(event_type):
            line = f"{event_type}"
            if is_positive_report_value(road_name):
                line += f" på/ved {road_name}"
            if distance_m is not None:
                line += f", ca. {round(distance_m)} m fra adressen"
            traffic_lines.append(line + ".")

    access_lines = []
    main_road = raw_incident_data.get("nearby_main_road") or {}
    for line in raw_incident_data.get("access_context_lines", []) or []:
        cleaned = clean_short_report_text(line)
        if is_positive_report_value(cleaned):
            access_lines.append(cleaned)
    if not access_lines and main_road.get("summary"):
        access_lines.append(main_road["summary"])
    if not access_lines and main_road.get("nearest_main_road") and main_road.get("distance_m") is not None:
        access_lines.append(
            f"Adressen ligger på/ved sidevej tæt på {main_road['nearest_main_road']}, "
            f"ca. {main_road['distance_m']} m fra nærmeste større vej."
        )

    building_sections = build_deterministic_building_sections(raw_incident_data)

    report = {
        "title": "FULD INDSATSBRIEF" if report_mode == "full" else "HURTIG INDSATSBRIEF",
        "address_lines": list(dict.fromkeys(address_lines)),
        "findings": building_sections.get("findings", []),
        "building_lines": building_sections.get("building_lines", []),
        "surroundings_lines": list(dict.fromkeys(osm_lines)) if report_mode == "full" else [],
        "osm_risk_lines": [] if report_mode == "full" else list(dict.fromkeys(osm_lines)),
        "weather_lines": list(dict.fromkeys(weather_lines)),
        "water_supply_lines": water_supply_lines,
        "assistance_lines": [],
        "map_links": [],
        "building_details": building_sections.get("building_details", []),
        "access_lines": list(dict.fromkeys(access_lines)),
        "secondary_buildings": building_sections.get("secondary_buildings", []),
        "basement_lines": building_sections.get("basement_lines", []),
        "heating_lines": building_sections.get("heating_lines", []),
        "risk_context_lines": [],
        "resource_lines": [],
        "supplementary_lines": [],
        "traffic_lines": traffic_lines,
        "disclaimer": REPORT_DISCLAIMER,
    }
    return clean_report_sections(report)


def build_analyze_debug(raw_incident_data, payload=None):
    building = raw_incident_data.get("building") or {}
    weather = raw_incident_data.get("weather") or {}
    osm_risk_check = raw_incident_data.get("osm_risk_check") or {}
    short_report_data = raw_incident_data.get("short_report_data") or {}
    address_details = raw_incident_data.get("address_details") or {}

    return {
        "model_used": OPENAI_MODEL,
        "openai_payload_keys": sorted(payload.keys()) if payload else [],
        "incident_has_address": bool(address_details and not address_details.get("error")),
        "incident_has_building": bbr_building_has_real_data(building),
        "incident_has_weather": bool(
            not weather.get("error") and any(
                weather.get(field) is not None
                for field in [
                    "temperature_c",
                    "wind_direction_degrees",
                    "wind_speed_ms",
                    "wind_gust_ms",
                    "precipitation",
                ]
            )
        ),
        "incident_has_osm": bool(
            osm_risk_check.get("finding_count")
            or osm_risk_check.get("osm_risk_summary")
            or osm_risk_check.get("grouped_summary")
        ),
        "short_report_data_keys": sorted(short_report_data.keys()),
    }


def build_report_text(report_structured):
    """Render the accepted structured result in a stable short-report format."""
    lines = [report_structured.get("title", "HURTIG INDSATSBRIEF"), ""]

    for heading, field in [
        ("Adresse", "address_lines"),
        ("Kortlinks", "map_links"),
        ("Fund", "findings"),
        ("Bygning", "building_lines"),
        ("Bygningsdetaljer", "building_details"),
        ("Varme", "heating_lines"),
        ("Adgang", "access_lines"),
        ("Sekundære bygninger", "secondary_buildings"),
        ("Kælder", "basement_lines"),
        ("Omgivelser / OSM", "surroundings_lines"),
        ("Risikokontekst", "risk_context_lines"),
        ("OSM-risikotjek", "osm_risk_lines"),
        ("Vejr/vind", "weather_lines"),
        ("Vandforsyning", "water_supply_lines"),
        ("Assistance", "assistance_lines"),
        ("Ressourcer", "resource_lines"),
        ("Trafik/vejarbejde", "traffic_lines"),
        ("Supplerende oplysninger", "supplementary_lines"),
    ]:
        section_lines = report_structured.get(field, [])
        if section_lines:
            lines.extend([f"{heading}:"])
            lines.extend(
                "* " + re.sub(
                    r"\[kort\]\((https?://[^)]+)\)",
                    r"kort: \1",
                    translate_report_label_text(line),
                )
                for line in section_lines
            )
            lines.append("")

    lines.extend(["Forbehold:", f"* {REPORT_DISCLAIMER}"])
    return "\n".join(lines)


@app.route("/incident-brief", methods=["GET"])
def incident_brief():
    address = request.args.get("address", "").strip()
    radius_m = parse_radius(request.args.get("radius_m", 250), 250)

    if not address:
        return jsonify({"error": "Adresse mangler"}), 400

    return jsonify(build_incident_brief_data(address, radius_m))


@app.route("/address-autocomplete", methods=["GET"])
def address_autocomplete():
    access_error = brief_api_access_error()
    if access_error:
        return access_error

    query = request.args.get("q", "").strip()
    if len(query) < 3:
        return jsonify({"suggestions": []})

    return jsonify({"suggestions": get_address_autocomplete(query)})


def analyze_brief_response(address, radius_m, report_mode="short"):
    raw_incident_data = None
    payload = None
    try:
        if not address:
            return jsonify({"error": "Adresse mangler"}), 400

        if report_mode not in ["short", "full"]:
            report_mode = "short"

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return jsonify({"error": "OPENAI_API_KEY is not configured"}), 503

        raw_incident_data = build_incident_brief_data(address, radius_m)
        if not raw_incident_data.get("address_details") or raw_incident_data.get("address_details", {}).get("error"):
            return jsonify({
                "error": "Adresse kunne ikke slås op",
                "suggestion": "Prøv med fuld adresse inkl. bynavn, fx 'Ingemansvej 50, 4200 Slagelse'",
                "raw_incident_data": raw_incident_data,
                **build_analyze_debug(raw_incident_data),
            }), 404

        payload = build_openai_brief_payload(raw_incident_data)
        payload["report_mode"] = report_mode
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.responses.create(
            model=OPENAI_MODEL,
            reasoning={"effort": "low"},
            instructions=(
                INDSATSBRIEF_SYSTEM_PROMPT
                + (INDSATSBRIEF_FULL_REPORT_PROMPT if report_mode == "full" else "")
            ),
            input=json.dumps(payload, ensure_ascii=False),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "incident_brief_report",
                    "strict": True,
                    "schema": REPORT_SCHEMA,
                }
            },
        )
        report_from_model = json.loads(response.output_text)
        report_structured = sanitize_ai_report(report_from_model, raw_incident_data, report_mode)

        concrete_osm_lines = build_concrete_osm_risk_lines(
            raw_incident_data.get("osm_risk_check") or {}
        )
        osm_field = "surroundings_lines" if report_mode == "full" else "osm_risk_lines"
        model_osm_lines = report_structured.get(osm_field, [])
        if concrete_osm_lines and (
            not model_osm_lines
            or all("fund" in line.lower() for line in model_osm_lines)
        ):
            report_structured[osm_field] = concrete_osm_lines

        fallback_sections = build_deterministic_building_sections(raw_incident_data)
        for field in ["findings", "building_lines", "building_details", "heating_lines", "basement_lines", "secondary_buildings"]:
            if not report_structured.get(field) and fallback_sections.get(field):
                report_structured[field] = fallback_sections[field]
        if not report_structured.get("access_lines"):
            access_lines = [
                clean_short_report_text(line)
                for line in raw_incident_data.get("access_context_lines", []) or []
            ]
            report_structured["access_lines"] = [
                line for line in access_lines if is_positive_report_value(line)
            ]
        report_structured = clean_report_sections(report_structured)

        return jsonify({
            "report_text": build_report_text(report_structured),
            "report_structured": report_structured,
            "raw_incident_data": raw_incident_data,
            "report_mode": report_mode,
            **build_analyze_debug(raw_incident_data, payload),
        })
    except json.JSONDecodeError as error:
        fallback_report = (
            build_deterministic_report_structured(raw_incident_data, report_mode)
            if raw_incident_data else None
        )
        return jsonify({
            "error": "OpenAI returnerede ugyldig JSON",
            "details": str(error),
            "route": "/analyze-brief",
            "report_text": build_report_text(fallback_report) if fallback_report else None,
            "report_structured": fallback_report,
            "raw_incident_data": raw_incident_data,
            "report_mode": report_mode,
            **build_analyze_debug(raw_incident_data or {}, payload),
        }), 502
    except Exception as error:
        fallback_report = (
            build_deterministic_report_structured(raw_incident_data, report_mode)
            if raw_incident_data else None
        )
        return jsonify({
            "error": "OpenAI-analyse kunne ikke gennemføres" if raw_incident_data else "Kort fejlbesked",
            "details": str(error),
            "route": "/analyze-brief",
            "report_text": build_report_text(fallback_report) if fallback_report else None,
            "report_structured": fallback_report,
            "raw_incident_data": raw_incident_data,
            "report_mode": report_mode,
            **build_analyze_debug(raw_incident_data or {}, payload),
        }), 502 if raw_incident_data else 500


@app.route("/analyze-brief", methods=["GET"])
def analyze_brief():
    access_error = brief_api_access_error()
    if access_error:
        return access_error
    address = request.args.get("address", "").strip()
    radius_m = parse_radius(request.args.get("radius_m", 250), 250)
    return analyze_brief_response(address, radius_m, request.args.get("mode", "short").lower())


@app.route("/full-brief", methods=["GET"])
def full_brief():
    access_error = brief_api_access_error()
    if access_error:
        return access_error
    address = request.args.get("address", "").strip()
    radius_m = parse_radius(request.args.get("radius_m", 250), 250)
    return analyze_brief_response(address, radius_m, "full")


def build_nearest_resource_payload(address, resource_query, radius_km=100, limit=5):
    if not address:
        return {"error": "Adresse mangler"}, 400
    if not resource_query:
        return {"error": "Ressource mangler"}, 400

    try:
        radius_km = float(radius_km)
        if radius_km <= 0:
            radius_km = 100
    except Exception:
        radius_km = 100

    try:
        limit = min(max(int(limit), 1), 10)
    except Exception:
        limit = 5

    address_data = lookup_address(address)
    if not address_data or address_data.get("error"):
        return {"error": "Adresse kunne ikke slås op"}, 400

    origin_lat = address_data.get("latitude")
    origin_lon = address_data.get("longitude")
    if origin_lat is None or origin_lon is None:
        return {"error": "Adresse kunne ikke slås op"}, 400

    expanded_terms = expand_resource_query(resource_query)
    matches = find_matching_station_resources(resource_query, include_non_operational=True)
    ranked = rank_stations_by_distance(
        origin_lat,
        origin_lon,
        matches,
        limit=limit,
        radius_km=radius_km,
    )

    results = []
    for item in ranked:
        station = item.get("station") or {}
        results.append({
            "station_id": station.get("id"),
            "station_name": station.get("name"),
            "type": station.get("type"),
            "organization": station.get("organization"),
            "authority": station.get("authority"),
            "operator": station.get("operator"),
            "area": station.get("area"),
            "address": station.get("address"),
            "operational_response_station": station.get("operational_response_station") is not False,
            "matched_resource": item.get("matched_resource"),
            "matched_type": item.get("matched_type"),
            "matched_resource_name": item.get("matched_resource_name"),
            "matched_resource_type": item.get("matched_resource_type"),
            "matched_resource_kind": item.get("matched_resource_kind"),
            "matched_capabilities": item.get("matched_capabilities", []),
            "matched_terms": item.get("matched_terms", []),
            "air_distance_km": item.get("air_distance_km"),
            "road_distance_km": item.get("road_distance_km"),
            "road_time_min": item.get("road_time_min"),
            "source": station.get("source", "manual"),
            "notes": station.get("notes"),
        })

    payload = {
        "address": address_data.get("normalized_address") or address,
        "resource_query": resource_query,
        "expanded_terms": expanded_terms,
        "results": results,
        "message": (
            None if results
            else "Ingen registrerede ressourcer fundet i stationsfilen for den søgning."
        ),
        "disclaimer": (
            "Listen viser vejledende nærmeste brand-/redningsressourcer ud fra stationsfilen. "
            "Det er ikke live disponering og ikke en anbefaling om afsendelse."
        ),
    }
    return payload, 200


def find_nearest_resource(address, resource_query, radius_km=100, limit=5):
    """Return neutral nearest-resource data from the station file."""
    payload, _status = build_nearest_resource_payload(
        address,
        resource_query,
        radius_km=radius_km,
        limit=limit,
    )
    return payload


def answer_resource_followup(question, incident_data):
    resource_query = extract_resource_query(question)
    if not resource_query:
        return None

    address = (
        incident_data.get("requested_address")
        or incident_data.get("matched_address")
        or incident_data.get("normalized_address")
    )
    if not address:
        return "Det fremgår ikke af de tilgængelige data, hvilken adresse der skal bruges til ressourcesøgning."

    payload, status = build_nearest_resource_payload(address, resource_query, radius_km=100, limit=1)
    if status != 200 or not payload.get("results"):
        return payload.get("message") or "Der er ingen registrerede ressourcer i stationsfilen for den søgning."

    result = payload["results"][0]
    station = result.get("station_name")
    resource = result.get("matched_resource") or resource_query
    organization = result.get("organization") or result.get("authority") or result.get("operator")
    distance = result.get("air_distance_km")
    road_time = result.get("road_time_min")
    road_distance = result.get("road_distance_km")
    status_text = "" if result.get("operational_response_station") else " Ressourceposten er markeret som støtte/ikke primær station."

    parts = [f"Nærmeste registrerede {resource} i stationsfilen er {station}"]
    if organization:
        parts.append(f"({organization})")
    if distance is not None:
        parts.append(f"luftlinje ca. {str(distance).replace('.', ',')} km")
    if road_distance is not None and road_time is not None:
        parts.append(
            f"vej ca. {str(road_distance).replace('.', ',')} km og ca. {road_time} min almindelig køretid"
        )
    return " ".join(parts).strip() + f".{status_text}"


@app.route("/brief-followup", methods=["POST"])
def brief_followup():
    access_error = brief_api_access_error()
    if access_error:
        return access_error

    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()
    incident_data = data.get("incident_data")

    if not question:
        return jsonify({"error": "Spørgsmål mangler"}), 400
    if not incident_data:
        return jsonify({"error": "Der er ingen tidligere rapport at spørge til. Lav først et adresseopslag."}), 400

    try:
        if detect_resource_question(question):
            resource_answer = answer_resource_followup(question, incident_data)
            if resource_answer:
                return jsonify({"answer": resource_answer})

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.responses.create(
            model=OPENAI_MODEL,
            reasoning={"effort": "low"},
            instructions=BRIEF_FOLLOWUP_PROMPT,
            input=json.dumps({
                "question": question,
                "incident_data": incident_data,
                "report_text": data.get("report_text"),
                "report_structured": data.get("report_structured"),
            }, ensure_ascii=False),
        )
        answer = clean_short_report_text(response.output_text)
        if not is_positive_report_value(answer):
            answer = "Det fremgår ikke af de tilgængelige data."
        return jsonify({"answer": answer})
    except Exception as error:
        return jsonify({"error": "Kunne ikke besvare spørgsmålet", "details": str(error)}), 502


@app.route("/nearest-resource", methods=["GET"])
def nearest_resource():
    access_error = brief_api_access_error()
    if access_error:
        return access_error

    payload, status = build_nearest_resource_payload(
        request.args.get("address", "").strip(),
        request.args.get("resource", "").strip(),
        request.args.get("radius_km", 100),
        request.args.get("limit", 5),
    )
    return jsonify(payload), status


@app.route("/assistance-stations", methods=["GET"])
def assistance_stations():
    configuration_message = brief_configuration_message()
    if configuration_message:
        return jsonify({"error": configuration_message}), 503
    if not session.get("brief_authenticated"):
        return jsonify({"error": "Ikke logget ind"}), 401

    address = request.args.get("address", "").strip()
    radius_km = parse_assistance_radius(request.args.get("radius_km", 40), 40)
    try:
        limit = int(request.args.get("limit", 5))
    except Exception:
        limit = 5
    limit = min(max(limit, 1), 10)

    if not address:
        return jsonify({"error": "Adresse mangler"}), 400

    try:
        address_data = lookup_address(address)
    except Exception:
        address_data = None

    if not address_data or address_data.get("error"):
        return jsonify({"error": "Adresse kunne ikke slås op"}), 400

    incident_lat = address_data.get("latitude")
    incident_lon = address_data.get("longitude")
    if incident_lat is None or incident_lon is None:
        return jsonify({"error": "Adresse kunne ikke slås op"}), 400

    manual_stations = []
    for station in load_fire_rescue_stations():
        if station.get("operational_response_station") is False:
            continue
        coordinates = get_station_coordinates(station)
        if not coordinates:
            continue
        station_lat, station_lon = coordinates
        manual_stations.append({
            **station,
            "lat": station_lat,
            "lon": station_lon,
            "type": station.get("type") or "Brand/redning",
            "source": station.get("source") or "manual",
        })
    osm_stations = get_osm_fire_rescue_stations_nearby(
        incident_lat, incident_lon, radius_km
    )
    all_stations = merge_fire_rescue_stations(manual_stations, osm_stations)
    if not all_stations:
        return jsonify({"error": "Ingen brand/redningsstationer med koordinater i stationslisten"}), 404

    nearby_stations = []
    for station in all_stations:
        try:
            air_distance_km = haversine_distance_km(
                incident_lat, incident_lon, station["lat"], station["lon"]
            )
        except Exception:
            continue

        if air_distance_km <= radius_km:
            nearby_stations.append({**station, "air_distance_km": air_distance_km})

    if not nearby_stations:
        return jsonify({
            "error": "Ingen stationer fundet inden for radius",
            "radius_km": radius_km,
        }), 404

    stations = []
    for station in sorted(nearby_stations, key=lambda item: item["air_distance_km"])[:limit]:
        route = get_driving_route_osrm(
            incident_lat, incident_lon, station["lat"], station["lon"]
        )
        stations.append({
            "name": station["name"],
            "type": station["type"],
            "organization": station.get("organization"),
            "authority": station.get("authority"),
            "operator": station.get("operator"),
            "area": station["area"],
            "air_distance_km": station["air_distance_km"],
            "source": station.get("source", "manual"),
            **route,
        })

    stations.sort(key=lambda item: (
        item["drive_time_min"] is None,
        item["drive_time_min"] if item["drive_time_min"] is not None else item["air_distance_km"],
        item["air_distance_km"],
    ))

    return jsonify({
        "incident_address": address_data.get("normalized_address") or address,
        "coordinates": {"lat": incident_lat, "lon": incident_lon},
        "radius_km": radius_km,
        "station_sources": {
            "manual_count": len(manual_stations),
            "osm_count": len(osm_stations),
            "merged_count": len(all_stations),
        },
        "stations": stations,
        "disclaimer": ASSISTANCE_DISCLAIMER,
    })


if __name__ == "__main__":
    app.run(debug=True)
