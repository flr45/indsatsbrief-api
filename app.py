from flask import Flask, request, jsonify, Response, redirect, session, url_for
from datetime import datetime, timezone, timedelta
from functools import wraps
from urllib.parse import quote
import requests
import os
import json
import math
import base64
import re
import hmac
import time
import sys
import html
import secrets
import hashlib
import smtplib
from email.message import EmailMessage
from io import BytesIO
from openai import OpenAI
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

try:
    from flask_sqlalchemy import SQLAlchemy
    from sqlalchemy import inspect
except Exception:  # pragma: no cover - dependency is installed in production via requirements.txt
    SQLAlchemy = None
    inspect = None

app = Flask(__name__)
app.json.ensure_ascii = False
APP_START_TIME = datetime.now(timezone.utc)

FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
BRIEF_ACCESS_CODE = os.getenv("BRIEF_ACCESS_CODE")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_APPROVAL_REQUIRED = os.getenv("ADMIN_APPROVAL_REQUIRED", "true").lower() == "true"
ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
ADMIN_NOTIFY_EMAIL = (os.getenv("ADMIN_NOTIFY_EMAIL") or ADMIN_EMAIL).strip().lower()
CONTACT_EMAIL = (os.getenv("CONTACT_EMAIL") or ADMIN_EMAIL).strip().lower()
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "").rstrip("/")
SHOW_CODE_LOGIN = os.getenv("SHOW_CODE_LOGIN", "false").lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST")
try:
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
except ValueError:
    SMTP_PORT = 587
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM") or SMTP_USERNAME
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

app.secret_key = FLASK_SECRET_KEY or os.getenv("SECRET_KEY", "dev-only-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = bool(os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_HOSTNAME"))

database_uri = DATABASE_URL or f"sqlite:///{os.path.join(os.path.dirname(__file__), 'indsatsbrief.sqlite3')}"
if database_uri.startswith("postgres://"):
    database_uri = database_uri.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_uri
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app) if SQLAlchemy else None

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
Du må ikke udelade bygningsdata, sekundære bygninger, kælderdata, varme eller vejroplysninger, hvis de findes i input. Du må kun udelade sektioner uden data.

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
    "tankvogn": ["tankvogn", "vandtankvogn", "vandtank", "vandforsyning", "vandressource", "vand", "tank"],
    "stige": ["stige", "drejestige", "stigevogn", "redningslift", "lift", "højderedning", "hoejderedning", "redning fra højde", "tagarbejde"],
    "redningsvogn": ["redningsvogn", "pionervogn", "frigørelse", "frigoerelse", "tung frigørelse", "tung redning", "trafikuheld", "fastklemt", "redning", "frigørelsesværktøj", "frigoerelsesvaerktoej", "hydraulisk værktøj", "hydraulisk vaerktoej"],
    "kemi": ["kemi", "kemikalie", "CBRN", "cbrn", "kemivogn", "miljøvogn", "miljoevogn", "miljø", "miljoe", "farlige stoffer", "hazmat", "forurening", "rens", "renseplads"],
    "båd": ["båd", "baad", "bådenhed", "baadenhed", "redningsbåd", "redningsbaad", "gummibåd", "gummibaad", "redningsflåde", "redningsflaade", "vandredning", "overfladeredning", "søredning", "soeredning", "havneredning", "isbåd", "isbaad"],
    "robot": ["robot", "robotter", "luf", "luf-60", "luf60", "crawler", "R6", "TAF", "TAF 60", "TAF60", "fjernstyret", "fjernstyret slukningsenhed", "fjernstyret robotenhed", "slukningsrobot", "indsatsrobot", "robot/TAF 60"],
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
    "kran": ["kran", "redningskran", "køretøjskran", "koeretoejskran", "lastbil med kran", "kranvogn"],
    "frivillige": ["frivillige", "frivilligenhed", "supplerende beredskab", "forplejning", "logistik", "støtteberedskab"],
    "drone": ["drone", "droner", "uas", "rpas", "uav", "indsatsdrone", "beredskabsdrone", "luftrekognoscering", "luftfoto", "termisk kamera"],
}
STRICT_RESOURCE_CATEGORIES = {
    "robot": {
        "queries": ["robot", "robotter", "luf", "luf-60", "luf60", "taf", "taf60", "taf 60", "crawler", "fjernstyret slukningsenhed"],
        "terms": ["robot", "robotter", "slukningsrobot", "indsatsrobot", "fjernstyret slukningsenhed", "fjernstyret", "luf", "luf-60", "luf60", "taf", "taf60", "taf 60", "crawler"],
        "broad_terms": ["slukning", "slukningsenhed", "hurtig slukning", "hurtig slukningsenhed", "brandslukning", "redning", "indsats", "containerberedskab", "logistik", "hse", "hse1"],
    },
    "drone": {
        "queries": ["drone", "droner", "uas", "rpas", "uav"],
        "terms": ["drone", "droner", "uas", "rpas", "uav", "indsatsdrone", "beredskabsdrone", "luftrekognoscering", "luftfoto", "termisk kamera"],
        "broad_terms": ["kamera", "overblik", "rekognoscering", "termisk"],
    },
    "båd": {
        "queries": ["båd", "baad", "bådenhed", "baadenhed", "redningsbåd", "redningsbaad", "vandredning", "overfladeredning"],
        "terms": ["båd", "baad", "bådenhed", "baadenhed", "redningsbåd", "redningsbaad", "gummibåd", "gummibaad", "redningsflåde", "redningsflaade", "vandredning", "overfladeredning", "søredning", "soeredning", "havneredning", "isbåd", "isbaad"],
        "broad_terms": ["vand", "vandforsyning", "tankvogn", "redning", "miljø", "miljoe", "pumpe", "oversvømmelse", "oversvoemmelse"],
    },
    "kran": {
        "queries": ["kran", "redningskran", "kranvogn", "køretøjskran", "koeretoejskran", "lastbil med kran"],
        "terms": ["kran", "redningskran", "kranvogn", "køretøjskran", "koeretoejskran", "lastbil med kran"],
        "broad_terms": ["redningsvogn", "pionervogn", "tung redning", "frigørelse", "frigoerelse"],
    },
    "dykker": {
        "queries": ["dykker", "vanddykker", "dykkervogn"],
        "terms": ["dykker", "vanddykker", "dykkervogn", "dykkerberedskab"],
        "broad_terms": ["vand", "redning", "båd", "baad"],
    },
    "cbrn": {
        "queries": ["cbrn", "kemi", "kemikalie", "kemivogn", "miljøvogn", "miljoevogn", "farlige stoffer", "hazmat"],
        "terms": ["cbrn", "kemi", "kemikalie", "kemivogn", "miljøvogn", "miljoevogn", "farlige stoffer", "hazmat", "rens", "renseplads"],
        "broad_terms": ["miljø", "miljoe", "forurening", "redning"],
    },
    "kemi": {
        "queries": ["kemi", "kemikalie", "kemivogn", "miljøvogn", "miljoevogn", "farlige stoffer", "hazmat"],
        "terms": ["kemi", "kemikalie", "kemivogn", "miljøvogn", "miljoevogn", "farlige stoffer", "hazmat", "cbrn", "rens", "renseplads"],
        "broad_terms": ["miljø", "miljoe", "forurening", "redning"],
    },
    "mirg": {
        "queries": ["mirg", "maritime incident response group", "skibsbrand", "maritim indsats"],
        "terms": ["mirg", "maritime incident response group", "skibsbrand", "maritim indsats", "brandslukning til søs", "brandslukning til soes"],
        "broad_terms": ["skib", "slukning", "redning"],
    },
    "stige": {
        "queries": ["stige", "drejestige", "stigevogn", "redningslift", "lift", "højderedning", "hoejderedning"],
        "terms": ["stige", "drejestige", "stigevogn", "redningslift", "lift", "specialstige", "afprodsstige"],
        "broad_terms": ["højderedning", "hoejderedning", "redning fra højde", "redning fra hoejde", "tagarbejde", "redning"],
    },
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
    "/api/stations",
    "/address-autocomplete",
    "/knowledge/ask",
    "/admin/knowledge/test-search",
    "/test-bbr",
    "/test-hydrants",
    "/osm-risk-check",
    "/aerial-check",
    "/aerial-image",
    "/hazmat",
)


if db:
    class User(db.Model):
        __tablename__ = "users"

        id = db.Column(db.Integer, primary_key=True)
        email = db.Column(db.String(255), unique=True, nullable=False, index=True)
        password_hash = db.Column(db.String(255), nullable=False)
        name = db.Column(db.String(255), nullable=False)
        organization = db.Column(db.String(255), nullable=False)
        role = db.Column(db.String(50), nullable=False, default="user")
        is_active = db.Column(db.Boolean, nullable=False, default=True)
        is_approved = db.Column(db.Boolean, nullable=False, default=False)
        email_verified = db.Column(db.Boolean, nullable=False, default=False)
        email_verified_at = db.Column(db.DateTime, nullable=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        last_login_at = db.Column(db.DateTime, nullable=True)

        def set_password(self, password):
            self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

        def check_password(self, password):
            return check_password_hash(self.password_hash, password or "")


    class PasswordResetToken(db.Model):
        __tablename__ = "password_reset_tokens"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
        token_hash = db.Column(db.String(128), nullable=False, unique=True, index=True)
        expires_at = db.Column(db.DateTime, nullable=False)
        used_at = db.Column(db.DateTime, nullable=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

        user = db.relationship("User", backref=db.backref("password_reset_tokens", lazy=True))


    class EmailVerificationToken(db.Model):
        __tablename__ = "email_verification_tokens"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
        token_hash = db.Column(db.String(128), nullable=False, unique=True, index=True)
        expires_at = db.Column(db.DateTime, nullable=False)
        used_at = db.Column(db.DateTime, nullable=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

        user = db.relationship("User", backref=db.backref("email_verification_tokens", lazy=True))


    class AuditLog(db.Model):
        __tablename__ = "audit_logs"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
        user_email = db.Column(db.String(255), nullable=True)
        action = db.Column(db.String(120), nullable=False, index=True)
        entity_type = db.Column(db.String(120), nullable=True, index=True)
        entity_id = db.Column(db.String(120), nullable=True)
        entity_name = db.Column(db.String(255), nullable=True)
        details = db.Column(db.JSON, nullable=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
        ip_address = db.Column(db.String(80), nullable=True)


    class KnowledgeDocument(db.Model):
        __tablename__ = "knowledge_documents"

        id = db.Column(db.Integer, primary_key=True)
        title = db.Column(db.String(255), nullable=False)
        category = db.Column(db.String(120), nullable=True)
        publisher = db.Column(db.String(160), nullable=True)
        version_date = db.Column(db.String(80), nullable=True)
        source_url = db.Column(db.String(600), nullable=True)
        original_filename = db.Column(db.String(255), nullable=True)
        is_active = db.Column(db.Boolean, nullable=False, default=True)
        uploaded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
        import_status = db.Column(db.String(120), nullable=False, default="pending")
        import_error = db.Column(db.Text, nullable=True)
        page_count = db.Column(db.Integer, nullable=True)
        chunk_count = db.Column(db.Integer, nullable=False, default=0)

        chunks = db.relationship("KnowledgeChunk", backref="document", cascade="all, delete-orphan", lazy=True)


    class KnowledgeChunk(db.Model):
        __tablename__ = "knowledge_chunks"

        id = db.Column(db.Integer, primary_key=True)
        document_id = db.Column(db.Integer, db.ForeignKey("knowledge_documents.id"), nullable=False, index=True)
        chunk_index = db.Column(db.Integer, nullable=False)
        page_start = db.Column(db.Integer, nullable=True)
        page_end = db.Column(db.Integer, nullable=True)
        text = db.Column(db.Text, nullable=False)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


    class Station(db.Model):
        __tablename__ = "stations"

        id = db.Column(db.Integer, primary_key=True)
        source_ref_id = db.Column(db.String(120), nullable=True, unique=True, index=True)
        name = db.Column(db.String(255), nullable=False)
        aliases = db.Column(db.JSON, nullable=False, default=list)
        type = db.Column(db.String(120), nullable=True)
        organization = db.Column("organisation", db.String(255), nullable=True)
        authority = db.Column(db.String(255), nullable=True)
        operator = db.Column(db.String(255), nullable=True)
        area = db.Column(db.String(160), nullable=True)
        address = db.Column(db.String(255), nullable=True)
        postal_code = db.Column(db.String(20), nullable=True)
        city = db.Column(db.String(120), nullable=True)
        lat = db.Column(db.Float, nullable=True)
        lon = db.Column(db.Float, nullable=True)
        source = db.Column(db.String(80), nullable=True)
        is_active = db.Column(db.Boolean, nullable=False, default=True)
        operational_response_station = db.Column(db.Boolean, nullable=False, default=True)
        notes = db.Column(db.Text, nullable=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

        vehicles = db.relationship("StationVehicle", backref="station", cascade="all, delete-orphan", lazy=True)
        resources = db.relationship("StationResource", backref="station", cascade="all, delete-orphan", lazy=True)
        contacts = db.relationship("StationContact", backref="station", cascade="all, delete-orphan", lazy=True)


    class StationVehicle(db.Model):
        __tablename__ = "station_vehicles"

        id = db.Column(db.Integer, primary_key=True)
        station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False, index=True)
        name = db.Column(db.String(120), nullable=False)
        vehicle_type = db.Column(db.String(160), nullable=True)
        callsign = db.Column(db.String(120), nullable=True)
        description = db.Column(db.Text, nullable=True)
        aliases = db.Column(db.JSON, nullable=False, default=list)
        capabilities = db.Column(db.JSON, nullable=False, default=list)
        tags = db.Column(db.JSON, nullable=False, default=list)
        raw_data = db.Column(db.JSON, nullable=False, default=dict)
        is_active = db.Column(db.Boolean, nullable=False, default=True)
        sort_order = db.Column(db.Integer, nullable=False, default=0)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


    class StationResource(db.Model):
        __tablename__ = "station_resources"

        id = db.Column(db.Integer, primary_key=True)
        station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False, index=True)
        name = db.Column(db.String(160), nullable=False)
        resource_type = db.Column(db.String(160), nullable=True)
        description = db.Column(db.Text, nullable=True)
        aliases = db.Column(db.JSON, nullable=False, default=list)
        capabilities = db.Column(db.JSON, nullable=False, default=list)
        tags = db.Column(db.JSON, nullable=False, default=list)
        raw_data = db.Column(db.JSON, nullable=False, default=dict)
        is_active = db.Column(db.Boolean, nullable=False, default=True)
        sort_order = db.Column(db.Integer, nullable=False, default=0)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


    class StationContact(db.Model):
        __tablename__ = "station_contacts"

        id = db.Column(db.Integer, primary_key=True)
        station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False, index=True)
        phone = db.Column(db.String(80), nullable=True)
        email = db.Column(db.String(255), nullable=True)
        note = db.Column(db.Text, nullable=True)
else:
    User = None
    PasswordResetToken = None
    EmailVerificationToken = None
    AuditLog = None
    KnowledgeDocument = None
    KnowledgeChunk = None
    Station = None
    StationVehicle = None
    StationResource = None
    StationContact = None


def ensure_user_schema():
    """Add small auth columns when an existing DB predates this release."""
    if not db or not inspect:
        return
    try:
        inspector = inspect(db.engine)
        if "users" not in inspector.get_table_names():
            return
        existing_columns = {column["name"] for column in inspector.get_columns("users")}
        with db.engine.begin() as connection:
            if "email_verified" not in existing_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT FALSE"
                )
                connection.exec_driver_sql(
                    "UPDATE users SET email_verified = TRUE WHERE is_approved = TRUE OR role = 'admin'"
                )
            if "email_verified_at" not in existing_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN email_verified_at TIMESTAMP"
                )
    except Exception as error:
        app.logger.exception("Database schema kunne ikke opdateres: %s", error)


def ensure_station_schema():
    """Add station search columns when an existing DB predates this release."""
    if not db or not inspect:
        return
    try:
        inspector = inspect(db.engine)
        table_names = set(inspector.get_table_names())
        is_postgres = db.engine.dialect.name == "postgresql"
        json_type = "JSONB" if is_postgres else "JSON"
        empty_list_default = "'[]'::jsonb" if is_postgres else "'[]'"
        empty_object_default = "'{}'::jsonb" if is_postgres else "'{}'"
        with db.engine.begin() as connection:
            for table_name in ["station_vehicles", "station_resources"]:
                if table_name not in table_names:
                    continue
                existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
                if "tags" not in existing_columns:
                    connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN tags {json_type} DEFAULT {empty_list_default}")
                if "raw_data" not in existing_columns:
                    connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN raw_data {json_type} DEFAULT {empty_object_default}")
    except Exception as error:
        app.logger.exception("Stationsschema kunne ikke opdateres: %s", error)


def init_database():
    if not db:
        app.logger.warning("Flask-SQLAlchemy er ikke installeret. Brugerlogin er ikke aktivt.")
        return
    try:
        with app.app_context():
            db.create_all()
            ensure_user_schema()
            ensure_station_schema()
    except Exception as error:
        app.logger.exception("Database kunne ikke initialiseres: %s", error)


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


def load_fire_rescue_stations_from_json():
    """Load manual station/resource JSON data without making app startup fragile."""
    global STATION_DATA_CACHE
    if STATION_DATA_CACHE is None:
        station_data = load_json_file(FIRE_RESCUE_STATIONS_FILE, [])
        root_data = load_json_file(ROOT_FIRE_RESCUE_STATIONS_FILE, []) if os.path.exists(ROOT_FIRE_RESCUE_STATIONS_FILE) else []
        combined = []
        seen = set()
        seen_names = set()
        for station in (station_data if isinstance(station_data, list) else []):
            key = station.get("id") or normalize_text(station.get("name"))
            name_key = normalize_text(station.get("name"))
            if key:
                seen.add(key)
            if name_key:
                seen_names.add(name_key)
            combined.append(station)
        for station in (root_data if isinstance(root_data, list) else []):
            key = station.get("id") or normalize_text(station.get("name"))
            name_key = normalize_text(station.get("name"))
            if (key and key in seen) or (name_key and name_key in seen_names):
                continue
            if key:
                seen.add(key)
            if name_key:
                seen_names.add(name_key)
            combined.append(station)
        STATION_DATA_CACHE = combined
    return STATION_DATA_CACHE


def station_list_value(value):
    if value is None:
        return []
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(station_list_value(item))
        return values
    if isinstance(value, dict):
        values = []
        for key, item in value.items():
            values.extend(station_list_value(key))
            values.extend(station_list_value(item))
        return values
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") or text.startswith("{"):
            try:
                parsed = json.loads(text.replace("'", '"'))
                parsed_values = station_list_value(parsed)
                if parsed_values:
                    return parsed_values
            except Exception:
                pass
        parts = [line.strip().strip("'\"") for line in re.split(r"[\n,;]+", text) if line.strip().strip("'\"")]
        expanded = []
        for part in parts:
            if part not in expanded:
                expanded.append(part)
            if " " in part and not re.search(r"\s+(fra|til|med|og|af|i|på|paa)\s+", part, flags=re.IGNORECASE):
                for token in part.split():
                    if token and token not in expanded:
                        expanded.append(token)
        return expanded
    return [str(value).strip()] if str(value).strip() else []


def station_search_values(value):
    values = []
    for item in station_list_value(value):
        if item and item not in values:
            values.append(item)
    return values


def normalize_terms(value):
    terms = []
    for item in station_search_values(value):
        normalized = normalize_text(item)
        if normalized and normalized not in terms:
            terms.append(normalized)
    return terms


def station_float_value(value):
    if value in [None, ""]:
        return None
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def invalidate_station_data_cache():
    global STATION_DATA_CACHE
    STATION_DATA_CACHE = None


def split_station_postal_city(address):
    match = re.search(r"\b(\d{4})\s+([^,]+)$", str(address or "").strip())
    if not match:
        return None, None
    return match.group(1), match.group(2).strip()


def station_db_to_dict(station, include_inactive=False):
    vehicles = []
    for vehicle in sorted(station.vehicles or [], key=lambda item: (item.sort_order or 0, item.name or "")):
        if not include_inactive and not vehicle.is_active:
            continue
        vehicles.append({
            "id": vehicle.id,
            "name": vehicle.name,
            "type": vehicle.vehicle_type,
            "vehicle_type": vehicle.vehicle_type,
            "callsign": vehicle.callsign,
            "description": vehicle.description,
            "aliases": station_list_value(vehicle.aliases),
            "capabilities": station_list_value(vehicle.capabilities),
            "tags": station_list_value(getattr(vehicle, "tags", None)),
            "raw_data": station_list_value(getattr(vehicle, "raw_data", None)),
            "is_active": bool(vehicle.is_active),
            "sort_order": vehicle.sort_order or 0,
        })

    resources = []
    for resource in sorted(station.resources or [], key=lambda item: (item.sort_order or 0, item.name or "")):
        if not include_inactive and not resource.is_active:
            continue
        resources.append({
            "id": resource.id,
            "name": resource.name,
            "type": resource.resource_type,
            "resource_type": resource.resource_type,
            "description": resource.description,
            "aliases": station_list_value(resource.aliases),
            "capabilities": station_list_value(resource.capabilities),
            "tags": station_list_value(getattr(resource, "tags", None)),
            "raw_data": station_list_value(getattr(resource, "raw_data", None)),
            "is_active": bool(resource.is_active),
            "sort_order": resource.sort_order or 0,
        })

    return {
        "id": station.id,
        "source_ref_id": station.source_ref_id,
        "name": station.name,
        "aliases": station_list_value(station.aliases),
        "type": station.type or "Brand/redning",
        "organization": station.organization,
        "organisation": station.organization,
        "authority": station.authority,
        "operator": station.operator,
        "area": station.area,
        "address": station.address,
        "postal_code": station.postal_code,
        "city": station.city,
        "lat": station.lat,
        "lon": station.lon,
        "source": station.source or "database",
        "is_active": bool(station.is_active),
        "operational_response_station": bool(station.operational_response_station),
        "vehicles": vehicles,
        "trailers": [],
        "containers": [],
        "resources": resources,
        "special_resources": [resource["name"] for resource in resources],
        "resource_aliases": sorted({
            term
            for item in [*vehicles, *resources]
            for term in [item.get("name"), item.get("type"), *(item.get("aliases") or []), *(item.get("capabilities") or [])]
            if term
        }),
        "notes": station.notes,
    }


def station_json_to_db(station_data):
    postal_code, city = split_station_postal_city(station_data.get("address"))
    station = Station(
        source_ref_id=station_data.get("id"),
        name=station_data.get("name") or "Brand/redningsstation",
        aliases=station_list_value(station_data.get("aliases")),
        type=station_data.get("type") or "Brand/redning",
        organization=station_data.get("organization") or station_data.get("organisation"),
        authority=station_data.get("authority"),
        operator=station_data.get("operator"),
        area=station_data.get("area"),
        address=station_data.get("address"),
        postal_code=station_data.get("postal_code") or postal_code,
        city=station_data.get("city") or city,
        lat=station_float_value(station_data.get("lat")),
        lon=station_float_value(station_data.get("lon")),
        source=station_data.get("source") or "manual",
        is_active=station_data.get("is_active", True) is not False,
        operational_response_station=station_data.get("operational_response_station", True) is not False,
        notes=station_data.get("notes"),
    )

    sort_order = 0
    for vehicle_data in station_data.get("vehicles") or []:
        sort_order += 10
        station.vehicles.append(StationVehicle(
            name=vehicle_data.get("name") or vehicle_data.get("callsign") or "Køretøj",
            vehicle_type=vehicle_data.get("type") or vehicle_data.get("vehicle_type"),
            callsign=vehicle_data.get("callsign"),
            description=vehicle_data.get("description"),
            aliases=station_list_value(vehicle_data.get("aliases")),
            capabilities=station_list_value(vehicle_data.get("capabilities")),
            tags=station_list_value(vehicle_data.get("tags")),
            raw_data=vehicle_data.get("raw_data") or {},
            is_active=vehicle_data.get("is_active", True) is not False,
            sort_order=vehicle_data.get("sort_order") or sort_order,
        ))

    for collection_name, resource_type in [("trailers", "trailer"), ("containers", "container")]:
        for resource_data in station_data.get(collection_name) or []:
            sort_order += 10
            station.resources.append(StationResource(
                name=resource_data.get("name") or resource_data.get("type") or resource_type,
                resource_type=resource_data.get("type") or resource_type,
                description=resource_data.get("description"),
                aliases=station_list_value(resource_data.get("aliases")),
                capabilities=station_list_value(resource_data.get("capabilities")),
                tags=station_list_value(resource_data.get("tags")),
                raw_data=resource_data.get("raw_data") or {},
                is_active=resource_data.get("is_active", True) is not False,
                sort_order=resource_data.get("sort_order") or sort_order,
            ))

    for resource_name in station_data.get("special_resources") or []:
        sort_order += 10
        station.resources.append(StationResource(
            name=str(resource_name),
            resource_type=str(resource_name),
            aliases=[],
            capabilities=[str(resource_name)],
            is_active=True,
            sort_order=sort_order,
        ))

    return station


def seed_fire_rescue_stations_if_empty():
    if not db or not Station:
        return
    try:
        if Station.query.first():
            return
        for station_data in load_fire_rescue_stations_from_json():
            db.session.add(station_json_to_db(station_data))
        db.session.commit()
        app.logger.info("Importerede stationer fra JSON til databasen.")
    except Exception as error:
        db.session.rollback()
        app.logger.exception("Stationsdata kunne ikke importeres fra JSON: %s", error)


def load_fire_rescue_stations_from_db(include_inactive=False):
    if not db or not Station:
        return []
    try:
        query = Station.query
        if not include_inactive:
            query = query.filter(Station.is_active.is_(True))
        stations = query.order_by(Station.name.asc()).all()
        return [station_db_to_dict(station, include_inactive=include_inactive) for station in stations]
    except Exception as error:
        app.logger.warning("Kunne ikke hente stationer fra database: %s", error)
        return []


def station_resource_data_source():
    if db and Station:
        try:
            if Station.query.filter(Station.is_active.is_(True)).count() > 0:
                return "postgres"
            if Station.query.count() > 0:
                return "postgres_no_active_stations"
        except Exception as error:
            app.logger.warning("Kunne ikke afgøre stationsdatakilde: %s", error)
    return "json_fallback"


def load_fire_rescue_stations():
    """Load fresh DB station/resource data, with JSON only when DB has no stations."""
    if db and Station:
        try:
            if Station.query.filter(Station.is_active.is_(True)).count() > 0:
                return load_fire_rescue_stations_from_db(include_inactive=False)
            if Station.query.count() > 0:
                return []
            seed_fire_rescue_stations_if_empty()
            db_stations = load_fire_rescue_stations_from_db(include_inactive=False)
            if db_stations:
                return db_stations
        except Exception as error:
            app.logger.warning("Kunne ikke læse stationsdata fra database: %s", error)
    return load_fire_rescue_stations_from_json()


def get_searchable_stations(include_non_operational=True):
    """Return normalized searchable station data and the active source."""
    data_source = station_resource_data_source()
    stations = load_fire_rescue_stations()
    normalized = []
    for station in stations:
        if station.get("is_active") is False:
            continue
        if not include_non_operational and station.get("operational_response_station") is False:
            continue
        normalized.append({
            "id": station.get("id") or station.get("source_ref_id"),
            "source_ref_id": station.get("source_ref_id"),
            "name": station.get("name"),
            "aliases": station_list_value(station.get("aliases")),
            "type": station.get("type") or "Brand/redning",
            "organization": station.get("organization") or station.get("organisation"),
            "organisation": station.get("organization") or station.get("organisation"),
            "authority": station.get("authority"),
            "operator": station.get("operator"),
            "area": station.get("area"),
            "address": station.get("address"),
            "postal_code": station.get("postal_code"),
            "city": station.get("city"),
            "lat": station.get("lat"),
            "lon": station.get("lon"),
            "source": station.get("source") or data_source,
            "is_active": station.get("is_active", True) is not False,
            "operational_response_station": station.get("operational_response_station", True) is not False,
            "notes": station.get("notes"),
            "tags": station_search_values(station.get("tags")),
            "raw_data": station_search_values(station.get("raw_data")),
            "vehicles": [
                {
                    "id": vehicle.get("id"),
                    "name": vehicle.get("name"),
                    "callsign": vehicle.get("callsign"),
                    "type": vehicle.get("vehicle_type") or vehicle.get("type"),
                    "vehicle_type": vehicle.get("vehicle_type") or vehicle.get("type"),
                    "description": vehicle.get("description"),
                    "aliases": station_list_value(vehicle.get("aliases")),
                    "capabilities": station_list_value(vehicle.get("capabilities")),
                    "tags": station_search_values(vehicle.get("tags")),
                    "raw_data": station_search_values(vehicle.get("raw_data")),
                    "is_active": vehicle.get("is_active", True) is not False,
                }
                for vehicle in station.get("vehicles") or []
                if vehicle.get("is_active", True) is not False
            ],
            "resources": [
                {
                    "id": resource.get("id"),
                    "name": resource.get("name"),
                    "type": resource.get("resource_type") or resource.get("type"),
                    "resource_type": resource.get("resource_type") or resource.get("type"),
                    "description": resource.get("description"),
                    "aliases": station_list_value(resource.get("aliases")),
                    "capabilities": station_list_value(resource.get("capabilities")),
                    "tags": station_search_values(resource.get("tags")),
                    "raw_data": station_search_values(resource.get("raw_data")),
                    "is_active": resource.get("is_active", True) is not False,
                }
                for resource in station.get("resources") or []
                if resource.get("is_active", True) is not False
            ],
            "trailers": station.get("trailers") or [],
            "containers": station.get("containers") or [],
            "special_resources": station_list_value(station.get("special_resources")),
            "resource_aliases": station_list_value(station.get("resource_aliases")),
        })
    return normalized, data_source


def load_resource_aliases():
    """Load centralized resource aliases for natural-language resource search."""
    global RESOURCE_ALIAS_CACHE
    if RESOURCE_ALIAS_CACHE is None:
        data = load_json_file(RESOURCE_ALIASES_FILE, {})
        merged = {}
        for source in [RESOURCE_ALIAS_MAP, data if isinstance(data, dict) else {}]:
            for canonical, terms in source.items():
                key = str(canonical)
                merged.setdefault(key, [])
                for term in [key, *(terms or [])]:
                    if term not in merged[key]:
                        merged[key].append(term)
        RESOURCE_ALIAS_CACHE = merged
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


def normalize_station_identity_text(value):
    text = str(value or "").strip().lower()
    text = re.sub(r"[.,;:()]+", " ", text)
    text = re.sub(r"\b(station|brandstation|beredskabsstation)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


init_database()
if db:
    with app.app_context():
        seed_fire_rescue_stations_if_empty()


def canonical_station_key(station):
    if not isinstance(station, dict):
        return ""
    name = normalize_station_identity_text(
        station.get("station_name")
        or station.get("display_name")
        or station.get("name")
    )
    area = normalize_station_identity_text(station.get("area"))
    organization = normalize_station_identity_text(
        station.get("organization")
        or station.get("authority")
        or station.get("operator")
    )

    if area and (not name or area == name or area in name or name in area):
        return f"area:{area}"
    address = normalize_station_identity_text(station.get("address"))
    if address:
        return f"address:{address}"
    if area:
        return f"area:{area}"
    if name and organization:
        return f"name-org:{name}:{organization}"
    if name:
        return f"name:{name}"

    station_id = station.get("station_id") or station.get("id")
    if station_id:
        return f"id:{station_id}"
    return ""


def station_resource_richness(value):
    if not isinstance(value, dict):
        return 0
    station = value.get("station") if isinstance(value.get("station"), dict) else value
    richness = 0
    for key in ["vehicles", "trailers", "containers", "special_resources", "resources"]:
        collection = station.get(key) or []
        if isinstance(collection, list):
            richness += len(collection)
    if value.get("display_resource") or value.get("matched_resource") or value.get("resource") or value.get("resource_text"):
        richness += 100
    return richness


def station_pretty_name_score(name):
    text = str(name or "")
    score = 0
    if "Brandstation" in text or "brandstation" in text:
        score += 20
    if text.lower().startswith("station "):
        score -= 5
    score -= len(text) / 1000
    return score


def station_distance_sort_tuple(item):
    road_time = item.get("road_time_min", item.get("drive_time_min"))
    road_distance = item.get("road_distance_km")
    air_distance = item.get("air_distance_km")
    return (
        road_time is None,
        float(road_time) if road_time is not None else 999999.0,
        road_distance is None,
        float(road_distance) if road_distance is not None else 999999.0,
        air_distance is None,
        float(air_distance) if air_distance is not None else 999999.0,
    )


def dedupe_station_results(results, resource_mode=False):
    grouped = {}
    order = []
    for item in results or []:
        key = canonical_station_key(item)
        if not key:
            key = f"fallback:{len(order)}"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(item)

    deduped = []
    for key in order:
        group = grouped[key]
        pretty_name = max(
            [
                item.get("display_name")
                or item.get("station_name")
                or item.get("name")
                or ((item.get("station") or {}).get("name") if isinstance(item.get("station"), dict) else "")
                for item in group
            ],
            key=station_pretty_name_score,
            default="",
        )

        def winner_key(item):
            road_time = item.get("road_time_min", item.get("drive_time_min"))
            road_distance = item.get("road_distance_km")
            air_distance = item.get("air_distance_km")
            return (
                0 if (item.get("display_resource") or item.get("matched_resource") or item.get("resource") or item.get("resource_text")) else 1,
                -station_resource_richness(item),
                float(road_time) if road_time is not None else 999999.0,
                float(road_distance) if road_distance is not None else 999999.0,
                float(air_distance) if air_distance is not None else 999999.0,
                -station_pretty_name_score(item.get("station_name") or item.get("name")),
            )

        winner = dict(sorted(group, key=winner_key)[0])
        if pretty_name:
            if "station_name" in winner:
                winner["station_name"] = pretty_name
            if "name" in winner:
                winner["name"] = pretty_name
            winner["display_name"] = pretty_name
        winner["station_key"] = key
        deduped.append(winner)

    if resource_mode:
        deduped.sort(key=lambda item: (
            *station_distance_sort_tuple(item),
            -(item.get("match_score") or 0),
            item.get("station_name") or item.get("name") or "",
        ))
    else:
        deduped.sort(key=lambda item: (
            *station_distance_sort_tuple(item),
            item.get("station_name") or item.get("name") or "",
        ))
    return deduped


def expand_resource_query(query):
    query_text = str(query or "").strip()
    if not query_text:
        return []

    strict_category = strict_query_category([query_text])
    if strict_category:
        return allowed_strict_terms(strict_category)

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


def strict_category_config(category):
    return STRICT_RESOURCE_CATEGORIES.get(category or "") or {}


def allowed_strict_terms(category):
    terms = strict_category_config(category).get("terms") or []
    unique_terms = []
    seen = set()
    for term in terms:
        key = normalize_text(term)
        if key and key not in seen:
            seen.add(key)
            unique_terms.append(term)
    return unique_terms


def strict_query_category(query_terms):
    normalized_terms = [normalize_text(term) for term in query_terms or [] if normalize_text(term)]
    if not normalized_terms:
        return None
    for category, config in STRICT_RESOURCE_CATEGORIES.items():
        category_queries = {normalize_text(term) for term in config.get("queries", [])}
        category_terms = {normalize_text(term) for term in config.get("terms", [])}
        if any(term in category_queries or term in category_terms for term in normalized_terms):
            return category
    return None


def is_strict_query(query_terms):
    return bool(strict_query_category(query_terms))


def normalized_strict_terms(category):
    return [normalize_text(term) for term in allowed_strict_terms(category)]


def normalized_broad_terms(category):
    return [normalize_text(term) for term in strict_category_config(category).get("broad_terms", [])]


def station_text_fields(station):
    return [
        station.get("name"),
        station.get("organization"),
        station.get("authority"),
        station.get("operator"),
        station.get("area"),
        station.get("notes"),
        *(station.get("aliases") or []),
        *(station_search_values(station.get("tags"))),
        *(station_search_values(station.get("raw_data"))),
        *(station.get("special_resources") or []),
        *(station.get("resource_aliases") or []),
    ]


def is_strict_resource_query(expanded_terms):
    return is_strict_query(expanded_terms)


def strict_text_match(normalized_value, normalized_term):
    if normalized_value == normalized_term:
        return True
    return bool(re.search(rf"(^|\s){re.escape(normalized_term)}(\s|$)", normalized_value))


def strict_match_allowed(query_category, searchable_values):
    if not query_category:
        return True, []
    allowed = normalized_strict_terms(query_category)
    matched = []
    for value in station_search_values(searchable_values):
        normalized_value = normalize_text(value)
        if not normalized_value:
            continue
        for term in allowed:
            if strict_text_match(normalized_value, term):
                original_term = next((original for original in allowed_strict_terms(query_category) if normalize_text(original) == term), term)
                if original_term not in matched:
                    matched.append(original_term)
    return bool(matched), matched


def strict_rejection_reason(query_category, item, item_kind):
    values = [
        item.get("name"),
        item.get("callsign"),
        item.get("type") or item.get("vehicle_type") or item.get("resource_type"),
        item.get("description"),
        item.get("aliases"),
        item.get("capabilities"),
        item.get("tags"),
        item.get("raw_data"),
    ]
    normalized_blob = " ".join(normalize_terms(values))
    broad_hits = [
        term for term in normalized_broad_terms(query_category)
        if term and strict_text_match(normalized_blob, term)
    ]
    display_resource = resource_display_name(
        item.get("name") or item.get("callsign"),
        item.get("type") or item.get("vehicle_type") or item.get("resource_type"),
    ) or item_kind
    if broad_hits:
        return (
            f"Strict {query_category} query: {display_resource} matched broad term "
            f"'{broad_hits[0]}', but no explicit strict term found."
        )
    return None


def score_resource_text(value, expanded_terms, base_score, strict=False):
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
        elif strict and strict_text_match(normalized_value, normalized_term):
            score = max(base_score - 8, 1)
        elif strict:
            continue
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


def is_category_only_resource(item, item_kind):
    if item_kind != "station_resource":
        return False
    name = normalize_text(item.get("name"))
    item_type = normalize_text(item.get("type") or item.get("resource_type"))
    aliases = [normalize_text(value) for value in item.get("aliases") or [] if normalize_text(value)]
    capabilities = [normalize_text(value) for value in item.get("capabilities") or [] if normalize_text(value)]
    return bool(name and (not item_type or item_type == name) and not aliases and set(capabilities).issubset({name}))


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
    strict = is_strict_resource_query(expanded_terms)
    strict_category = strict_query_category(expanded_terms)
    category_only = is_category_only_resource(item, item_kind)
    if item_kind == "vehicle":
        name_score, type_score, alias_score, capability_score, description_score = 100, 90, 75, 65, 45
    elif item_kind in ["trailer", "container"]:
        name_score, type_score, alias_score, capability_score, description_score = 100, 90, 75, 65, 45
    else:
        name_score, type_score, alias_score, capability_score, description_score = 100, 90, 75, 65, 45
    if category_only:
        name_score = min(name_score, 60)
        type_score = min(type_score, 55)
        alias_score = min(alias_score, 50)
        capability_score = min(capability_score, 45)
        description_score = min(description_score, 30)

    if item_kind == "vehicle":
        name_source = "vehicle.name"
        callsign_source = "vehicle.callsign"
        type_source = "vehicle.vehicle_type"
    elif item_kind in ["trailer", "container"]:
        name_source = f"{item_kind}.name"
        callsign_source = f"{item_kind}.callsign"
        type_source = f"{item_kind}.type"
    else:
        name_source = "resource.name"
        callsign_source = "resource.callsign"
        type_source = "resource.resource_type"

    candidates = [
        (item.get("name"), name_score, name_source),
        (item.get("callsign"), name_score, callsign_source),
        (item.get("type") or item.get("vehicle_type") or item.get("resource_type"), type_score, type_source),
    ]
    candidates.extend((alias, alias_score, f"{item_kind}.aliases") for alias in item.get("aliases") or [])
    candidates.extend((tag, alias_score, f"{item_kind}.tags") for tag in station_search_values(item.get("tags")))
    candidates.extend((capability, capability_score, f"{item_kind}.capabilities") for capability in item.get("capabilities") or [])
    candidates.append((item.get("description"), description_score, f"{item_kind}.description"))
    candidates.extend((raw_value, description_score, f"{item_kind}.raw_data") for raw_value in station_search_values(item.get("raw_data")))
    best = None
    best_source = None
    matched_terms = []

    if strict:
        allowed, explicit_terms = strict_match_allowed(strict_category, [value for value, _base_score, _source in candidates])
        if not allowed:
            return None

    for value, base_score, source in candidates:
        scored = score_resource_text(value, expanded_terms, base_score, strict=strict)
        if not scored:
            continue
        score, terms = scored
        if best is None or score > best:
            best = score
            best_source = source
        matched_terms.extend(term for term in terms if term not in matched_terms)

    if best is None:
        return None

    best += height_resource_priority_bonus(item, expanded_terms)
    display_resource = resource_display_name(
        item.get("name") or item.get("callsign"),
        item.get("type") or item.get("vehicle_type") or item.get("resource_type"),
    ) or item_kind

    return {
        "matched_resource": display_resource,
        "display_resource": display_resource,
        "resource": display_resource,
        "matched_type": item_kind,
        "matched_resource_name": item.get("name") or item.get("callsign"),
        "matched_resource_type": item.get("type") or item.get("vehicle_type") or item.get("resource_type"),
        "matched_resource_kind": item_kind,
        "matched_capabilities": item.get("capabilities") or [],
        "match_source": best_source,
        "match_score": best,
        "matched_terms": matched_terms,
    }


def match_resource_query(query_terms, station, vehicle=None, resource=None):
    if vehicle is not None:
        scored = score_station_resource(vehicle, query_terms, "vehicle")
        return {
            "matched": bool(scored),
            **(scored or {
                "score": 0,
                "match_score": 0,
                "match_source": None,
                "display_resource": None,
            }),
        }
    if resource is not None:
        scored = score_station_resource(resource, query_terms, "station_resource")
        return {
            "matched": bool(scored),
            **(scored or {
                "score": 0,
                "match_score": 0,
                "match_source": None,
                "display_resource": None,
            }),
        }

    best = None
    strict = is_strict_resource_query(query_terms)
    for value in station_text_fields(station or {}):
        scored = score_resource_text(value, query_terms, 20, strict=strict)
        if not scored:
            continue
        score, terms = scored
        if strict:
            continue
        if not best or score > best["match_score"]:
            best = {
                "matched": True,
                "matched_resource": "Generelt stationsmatch",
                "display_resource": "Generelt stationsmatch",
                "resource": "Generelt stationsmatch",
                "matched_type": "station",
                "matched_resource_name": None,
                "matched_resource_type": None,
                "matched_resource_kind": "station",
                "matched_capabilities": [],
                "match_source": "station.text",
                "match_score": score,
                "matched_terms": terms,
            }
    return best or {
        "matched": False,
        "match_score": 0,
        "match_source": None,
        "display_resource": None,
    }


def match_station_resource(query_terms, station):
    station_matches = []
    for vehicle in station.get("vehicles") or []:
        match = match_resource_query(query_terms, station, vehicle=vehicle)
        if match.get("matched"):
            station_matches.append({
                **match,
                "matched_object_type": "vehicle",
                "matched_object_id": vehicle.get("id"),
            })

    for resource in station.get("resources") or []:
        match = match_resource_query(query_terms, station, resource=resource)
        if match.get("matched"):
            station_matches.append({
                **match,
                "matched_object_type": "resource",
                "matched_object_id": resource.get("id"),
            })

    for collection_name, object_type in [("trailers", "trailer"), ("containers", "container")]:
        for item in station.get(collection_name) or []:
            match = score_station_resource(item, query_terms, object_type)
            if match:
                station_matches.append({
                    **match,
                    "matched": True,
                    "matched_object_type": object_type,
                    "matched_object_id": item.get("id"),
                })

    if station_matches:
        def concrete_sort_key(match):
            type_priority = {"vehicle": 0, "resource": 1, "trailer": 2, "container": 3}
            return (
                -(match.get("match_score") or 0),
                type_priority.get(match.get("matched_object_type"), 9),
                match.get("display_resource") or "",
            )
        return sorted(station_matches, key=concrete_sort_key)[0]

    station_match = match_resource_query(query_terms, station)
    if station_match.get("matched"):
        return {
            **station_match,
            "matched_object_type": "station",
            "matched_object_id": station.get("id"),
        }
    return {
        "matched": False,
        "match_score": 0,
        "match_source": None,
        "display_resource": None,
        "matched_terms": [],
        "matched_object_type": None,
        "matched_object_id": None,
    }


def rejected_strict_matches_for_station(query_terms, station):
    category = strict_query_category(query_terms)
    if not category:
        return []
    rejected = []
    for collection_name, object_type in [
        ("vehicles", "vehicle"),
        ("resources", "resource"),
        ("trailers", "trailer"),
        ("containers", "container"),
    ]:
        for item in station.get(collection_name) or []:
            reason = strict_rejection_reason(category, item, object_type)
            if not reason:
                continue
            rejected.append({
                "station_id": station.get("id"),
                "station_name": station.get("name"),
                "matched_object_type": object_type,
                "matched_object_id": item.get("id"),
                "display_resource": resource_display_name(
                    item.get("name") or item.get("callsign"),
                    item.get("type") or item.get("vehicle_type") or item.get("resource_type"),
                ) or object_type,
                "rejected_reason": reason,
            })
    return rejected


def find_matching_station_resources(resource_query, include_non_operational=False):
    expanded_terms = expand_resource_query(resource_query)
    if not expanded_terms:
        return []
    matches = []
    stations, _data_source = get_searchable_stations(include_non_operational=include_non_operational)
    for station in stations:
        best_match = match_station_resource(expanded_terms, station)
        if not best_match.get("matched"):
            continue
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


def search_resources(query, origin_lat, origin_lon, limit=10, radius_km=None, include_non_operational=True):
    """Central resource-search core used by nearest-resource and debug tooling."""
    matches = find_matching_station_resources(query, include_non_operational=include_non_operational)
    ranked = rank_stations_by_distance(
        origin_lat,
        origin_lon,
        matches,
        limit=min(max(int(limit or 10), 1), 30),
        radius_km=radius_km,
    )
    return {
        "query": query,
        "expanded_terms": expand_resource_query(query),
        "data_source": station_resource_data_source(),
        "matches": ranked,
    }


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
    if not db and not BRIEF_ACCESS_CODE:
        return "Brugerlogin kræver Flask-SQLAlchemy eller BRIEF_ACCESS_CODE."
    return None


def current_user():
    user_id = session.get("user_id")
    if not user_id or not db or not User:
        return None
    try:
        return db.session.get(User, int(user_id))
    except Exception:
        return None


def is_logged_in():
    user = current_user()
    return bool(
        user
        and user.is_active
        and user.is_approved
        and user.email_verified
        and session.get("user_id") == user.id
    ) or bool(session.get("access_granted") or session.get("brief_authenticated"))


def is_admin_user(user=None):
    user = user or current_user()
    return bool(user and user.is_active and user.is_approved and user.email_verified and user.role == "admin")


def login_required(api=False):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if is_logged_in():
                return func(*args, **kwargs)
            if api:
                return jsonify({"ok": False, "error": "Login kræves."}), 401
            return redirect(url_for("login", next=request.path))
        return wrapper
    return decorator


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not is_admin_user():
            return redirect(url_for("login", next=request.path))
        return func(*args, **kwargs)
    return wrapper


def brief_api_access_error():
    configuration_message = brief_configuration_message()
    if configuration_message:
        return jsonify({"ok": False, "error": configuration_message}), 503
    if not is_logged_in():
        return jsonify({"ok": False, "error": "Login kræves."}), 401
    return None


def auth_page_html(title, body_html, error_message=None, info_message=None):
    error_html = f'<p class="message error">{error_message}</p>' if error_message else ""
    info_html = f'<p class="message info">{info_message}</p>' if info_message else ""
    return f"""
<!DOCTYPE html>
<html lang="da">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title} - IndsatsBrief Brand</title>
    <style>
        :root {{ --bg:#0f172a; --card:#111827; --border:rgba(255,255,255,.08); --primary:#2563eb; --text:#f8fafc; --muted:#cbd5e1; --error:#ef4444; --success:#22c55e; }}
        html, body {{ width:100%; max-width:100%; overflow-x:hidden; }}
        * {{ box-sizing: border-box; }}
        body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:radial-gradient(circle at top left, rgba(37,99,235,.22), transparent 32rem), var(--bg); color:var(--text); font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; padding:20px; }}
        main {{ width:min(100%, 520px); border:1px solid var(--border); border-radius:20px; background:var(--card); box-shadow:0 24px 70px rgba(0,0,0,.35); padding:28px; }}
        h1 {{ margin:0 0 6px; font-size:28px; letter-spacing:0; }}
        .subtitle {{ margin:0 0 22px; color:var(--muted); }}
        form {{ display:grid; gap:14px; }}
        label {{ display:grid; gap:7px; color:var(--muted); font-size:14px; font-weight:800; }}
        input, button {{ width:100%; max-width:100%; min-height:48px; border-radius:14px; font:inherit; }}
        input {{ border:1px solid var(--border); background:#020617; color:var(--text); padding:11px 13px; }}
        button {{ border:0; background:var(--primary); color:#fff; font-weight:850; cursor:pointer; }}
        a {{ color:#93c5fd; overflow-wrap:anywhere; }}
        .links {{ display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-top:18px; }}
        .message {{ margin:0 0 16px; padding:12px 14px; border-radius:12px; }}
        .error {{ background:rgba(239,68,68,.14); color:#fecaca; }}
        .info {{ background:rgba(34,197,94,.12); color:#bbf7d0; }}
        @media (max-width:520px) {{ body {{ padding:12px; }} main {{ padding:22px; }} h1 {{ font-size:24px; }} }}
    </style>
</head>
<body><main>
    <h1>IndsatsBrief Brand</h1>
    <p class="subtitle">{title}</p>
    {error_html}
    {info_html}
    {body_html}
</main></body>
</html>
    """


def normalize_email(email):
    return (email or "").strip().lower()


def email_looks_valid(email):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""))


def safe_next_url(value):
    if not value or not str(value).startswith("/") or str(value).startswith("//"):
        return url_for("brief_page")
    return str(value)


def admin_count():
    if not db or not User:
        return 0
    return User.query.filter_by(role="admin", is_active=True, is_approved=True).count()


def should_make_admin(email):
    if ADMIN_EMAIL and email == ADMIN_EMAIL:
        return True
    return db and User and User.query.count() == 0


def hash_reset_token(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def build_external_url(endpoint, **values):
    path = url_for(endpoint, **values)
    if APP_BASE_URL:
        return f"{APP_BASE_URL}{path}"
    return url_for(endpoint, _external=True, **values)


def smtp_is_configured():
    return bool(SMTP_HOST and SMTP_FROM)


def log_reset_link_allowed():
    return not bool(os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_HOSTNAME"))


def send_email(to_email, subject, body):
    if not smtp_is_configured():
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = SMTP_FROM
    message["To"] = to_email
    message.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
        if SMTP_USE_TLS:
            smtp.starttls()
        if SMTP_USERNAME and SMTP_PASSWORD:
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.send_message(message)
    return True


def send_password_reset_email(user, reset_link):
    body = (
        "Hej.\n\n"
        "Du har bedt om at nulstille din adgangskode til IndsatsBrief Brand.\n"
        "Linket udløber om 30 minutter.\n\n"
        f"{reset_link}\n\n"
        "Hvis du ikke har bedt om nulstilling, kan du ignorere denne mail."
    )
    if not smtp_is_configured():
        if log_reset_link_allowed():
            app.logger.warning("Password reset link for %s: %s", user.email, reset_link)
        return False
    return send_email(user.email, "Nulstil adgangskode til IndsatsBrief Brand", body)


def create_password_reset_link(user):
    if not db or not PasswordResetToken or not user:
        return None
    token = secrets.token_urlsafe(32)
    reset_token = PasswordResetToken(
        user_id=user.id,
        token_hash=hash_reset_token(token),
        expires_at=datetime.utcnow() + timedelta(minutes=30),
    )
    db.session.add(reset_token)
    db.session.commit()
    return build_external_url("reset_password", token=token)


def find_valid_password_reset_token(token):
    if not db or not PasswordResetToken or not token:
        return None
    token_hash = hash_reset_token(token)
    reset_token = PasswordResetToken.query.filter_by(token_hash=token_hash).first()
    if not reset_token or reset_token.used_at:
        return None
    if reset_token.expires_at < datetime.utcnow():
        return None
    return reset_token


def create_email_verification_link(user):
    if not db or not EmailVerificationToken or not user:
        return None
    token = secrets.token_urlsafe(32)
    verification_token = EmailVerificationToken(
        user_id=user.id,
        token_hash=hash_reset_token(token),
        expires_at=datetime.utcnow() + timedelta(hours=24),
    )
    db.session.add(verification_token)
    db.session.commit()
    return build_external_url("verify_email", token=token)


def find_valid_email_verification_token(token):
    if not db or not EmailVerificationToken or not token:
        return None
    token_hash = hash_reset_token(token)
    verification_token = EmailVerificationToken.query.filter_by(token_hash=token_hash).first()
    if not verification_token or verification_token.used_at:
        return None
    if verification_token.expires_at < datetime.utcnow():
        return None
    return verification_token


def send_verification_email(user, verification_link):
    body = (
        f"Hej {user.name}\n\n"
        "Tak fordi du oprettede en bruger til IndsatsBrief Brand.\n\n"
        "Klik på linket herunder for at bekræfte din e-mailadresse:\n\n"
        f"{verification_link}\n\n"
        "Linket udløber om 24 timer.\n\n"
        "Når din e-mail er bekræftet, skal din bruger godkendes af administrator, før du kan logge ind.\n\n"
        "Hvis du ikke kan finde mailen, så tjek spam/uønsket mail.\n\n"
        "Venlig hilsen\n"
        "IndsatsBrief Brand"
    )
    if not smtp_is_configured():
        if log_reset_link_allowed():
            app.logger.warning("Email verification link for %s: %s", user.email, verification_link)
        return False
    return send_email(user.email, "Bekræft din e-mail til IndsatsBrief Brand", body)


def send_admin_pending_user_email(user):
    recipient = ADMIN_NOTIFY_EMAIL or ADMIN_EMAIL
    if not recipient:
        return False
    admin_url = f"{APP_BASE_URL}/admin/users" if APP_BASE_URL else build_external_url("admin_users")
    body = (
        "Der er oprettet en ny bruger i IndsatsBrief Brand.\n\n"
        f"Navn: {user.name}\n"
        f"E-mail: {user.email}\n"
        f"Organisation: {user.organization}\n"
        f"Tidspunkt: {format_datetime(user.created_at)}\n\n"
        "Brugeren skal først bekræfte sin e-mail og derefter godkendes af admin.\n\n"
        f"Adminside:\n{admin_url}"
    )
    if not smtp_is_configured():
        if log_reset_link_allowed():
            app.logger.warning("Admin notification for pending user %s would be sent to %s", user.email, recipient)
        return False
    return send_email(recipient, "Ny bruger afventer godkendelse", body)


def send_user_approved_email(user):
    login_url = f"{APP_BASE_URL}/login" if APP_BASE_URL else build_external_url("login")
    body = (
        f"Hej {user.name}\n\n"
        "Din konto til IndsatsBrief Brand er nu godkendt.\n\n"
        "Du kan nu logge ind her:\n"
        f"{login_url}\n\n"
        "Venlig hilsen\n"
        "IndsatsBrief Brand"
    )
    if not smtp_is_configured():
        if log_reset_link_allowed():
            app.logger.warning("Approval mail for %s would include login link: %s", user.email, login_url)
        return False
    return send_email(user.email, "Din konto til IndsatsBrief Brand er godkendt", body)


def send_user_verification_and_admin_notice(user):
    try:
        verification_link = create_email_verification_link(user)
        send_verification_email(user, verification_link)
    except Exception as error:
        app.logger.exception("Bekræftelsesmail kunne ikke sendes: %s", error)
    try:
        send_admin_pending_user_email(user)
    except Exception as error:
        app.logger.exception("Admin-notifikation kunne ikke sendes: %s", error)


KNOWLEDGE_SYSTEM_PROMPT = """
Du er en assistent til IndsatsBrief Brand.

Du skal først og fremmest bruge de medsendte indlæste dokumenter som kilde. Hvis dokumenterne indeholder relevante oplysninger, skal svaret primært bygge på dem.

Du må gerne supplere med generel faglig viden, hvis dokumenterne ikke dækker spørgsmålet fuldt ud. Når du gør det, skal du tydeligt markere det som 'Supplerende generel viden'.

Du må ikke påstå, at noget står i de indlæste dokumenter, hvis det ikke gør.

Du skal altid vise kilder. For indlæste dokumenter skal du vise dokumenttitel og sidetal. For supplerende generel viden skal du skrive, at det er generel viden, medmindre der senere implementeres eksterne webkilder.

Skriv dansk, kort, praktisk, indsatsvenligt og let at læse på mobil.
Undgå lange afsnit, gentagelser, akademisk stil og mange forbehold.
Svar som udgangspunkt på højst ca. 1200-1800 tegn. Hvis brugeren spørger bredt, skal du stadig svare kort og evt. afslutte med: "Spørg gerne mere specifikt for flere detaljer."
Hvis brugeren udtrykkeligt beder om et detaljeret svar, må svaret være længere.

Undgå live-disponering og operative kommandoer. Skriv ikke 'send', 'disponér', 'anbefalet afsendelse', 'du skal afsende' eller lignende.
Brug formuleringer som 'dokumentet beskriver', 'vær opmærksom på', 'kontrollér' og 'overvej efter lokal instruks'.

Svar altid i dette format:

Kort svar:
1-3 korte sætninger.

Dokumentgrundlag:
- 2-5 korte punkter fra indlæste dokumenter.
- Kun det vigtigste.
- Ingen lange forklaringer.

Praktisk betydning:
- 2-4 korte punkter.
- Fokus på hvad brugeren skal være opmærksom på.
- Ingen konkrete afsendelses- eller disponeringsanbefalinger.

Supplerende viden:
- Vis kun denne sektion hvis dokumenterne ikke dækker spørgsmålet fuldt ud.
- Hold den kort.
- Markér tydeligt at det er supplerende/generel viden.

Forbehold:
- Vejledende svar – kontrollér altid lokale instrukser og gældende procedurer.

Kilder:
- Dokumenttitel, side X-Y.
- Supplerende generel viden, hvis brugt.

Hvis ingen relevante dokumentbidder er medsendt, skal svaret være:

Kort svar:
Jeg fandt ikke et direkte svar i de indlæste dokumenter.

Supplerende viden:
- Kort generelt svar.
- Tydeligt at det ikke kommer fra dokumentarkivet.

Forbehold:
- Vejledende svar – kontrollér altid lokale instrukser og gældende procedurer.

Kilder:
- Ingen relevante indlæste dokumenter fundet
- Supplerende generel viden
"""


def import_pypdf_reader():
    from pypdf import PdfReader
    return PdfReader


def split_words_into_chunks(page_texts, target_words=1000):
    chunks = []
    current_words = []
    page_start = None
    page_end = None
    for page_number, text in page_texts:
        words = (text or "").split()
        if not words:
            continue
        if page_start is None:
            page_start = page_number
        page_end = page_number
        current_words.extend(words)
        if len(current_words) >= target_words:
            chunks.append({
                "text": " ".join(current_words),
                "page_start": page_start,
                "page_end": page_end,
            })
            current_words = []
            page_start = None
            page_end = None
    if current_words:
        chunks.append({
            "text": " ".join(current_words),
            "page_start": page_start,
            "page_end": page_end,
        })
    return chunks


def extract_pdf_chunks(file_bytes):
    PdfReader = import_pypdf_reader()
    reader = PdfReader(BytesIO(file_bytes))
    page_texts = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            page_texts.append((index, text))
    return len(reader.pages), split_words_into_chunks(page_texts)


def normalize_search_terms(question):
    words = re.findall(r"[A-Za-zÆØÅæøå0-9]{3,}", (question or "").lower())
    stopwords = {
        "hvad", "hvor", "hvordan", "hvilke", "skal", "kan", "der", "det", "den",
        "til", "med", "for", "fra", "som", "eller", "ikke", "jeg", "ved", "om",
    }
    return [word for word in words if word not in stopwords]


def score_knowledge_chunk(chunk, terms):
    if not terms:
        return 0
    document = chunk.document
    haystack = " ".join([
        chunk.text or "",
        document.title or "",
        document.category or "",
        document.publisher or "",
    ]).lower()
    score = 0
    for term in terms:
        score += haystack.count(term)
    return score


def search_knowledge_chunks(question, limit=8):
    if not db or not KnowledgeDocument or not KnowledgeChunk:
        return []
    terms = normalize_search_terms(question)
    chunks = (
        KnowledgeChunk.query
        .join(KnowledgeDocument)
        .filter(KnowledgeDocument.is_active.is_(True))
        .order_by(KnowledgeChunk.id.desc())
        .limit(800)
        .all()
    )
    scored = []
    for chunk in chunks:
        score = score_knowledge_chunk(chunk, terms)
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda item: (-item[0], item[1].document.title or "", item[1].chunk_index))
    return [chunk for _, chunk in scored[:limit]]


def format_page_range(start, end):
    if start and end and start != end:
        return f"{start}-{end}"
    if start:
        return str(start)
    return "-"


def knowledge_sources_from_chunks(chunks):
    sources = []
    seen = set()
    for chunk in chunks:
        document = chunk.document
        page = format_page_range(chunk.page_start, chunk.page_end)
        key = (document.id, page)
        if key in seen:
            continue
        seen.add(key)
        sources.append({
            "document_id": document.id,
            "title": document.title,
            "category": document.category,
            "publisher": document.publisher,
            "page": page,
            "label": f"{document.title}, side {page}",
        })
    return sources


def build_knowledge_context(chunks):
    if not chunks:
        return "Ingen relevante indlæste dokumentbidder blev fundet."
    blocks = []
    for index, chunk in enumerate(chunks, start=1):
        document = chunk.document
        blocks.append(
            f"[DOKUMENT {index}]\n"
            f"Titel: {document.title}\n"
            f"Kategori: {document.category or '-'}\n"
            f"Udgiver: {document.publisher or '-'}\n"
            f"Side: {format_page_range(chunk.page_start, chunk.page_end)}\n"
            f"Tekst:\n{chunk.text[:5000]}"
        )
    return "\n\n".join(blocks)


def fallback_knowledge_answer(question, chunks):
    sources = knowledge_sources_from_chunks(chunks)
    if chunks:
        excerpts = []
        for chunk in chunks[:3]:
            excerpt = chunk.text[:220].strip()
            if excerpt:
                excerpts.append(f"- {excerpt}...")
        answer = (
            "Kort svar:\n"
            "Jeg fandt relevante dokumentbidder, men AI-svaret kunne ikke genereres lige nu.\n\n"
            "Dokumentgrundlag:\n"
            + ("\n".join(excerpts) if excerpts else "- Relevante dokumentbidder blev fundet.")
            + "\n\nPraktisk betydning:\n"
            "- Brug dokumentkilderne som pejlemærke.\n"
            "- Kontrollér lokale instrukser og gældende procedurer.\n\n"
            "Forbehold:\n- Vejledende svar – kontrollér altid lokale instrukser og gældende procedurer.\n\n"
            "Kilder:\n"
            + "\n".join(f"- {source['label']}" for source in sources)
        )
        return answer, sources, False
    answer = (
        "Kort svar:\n"
        "Jeg fandt ikke et direkte svar i de indlæste dokumenter.\n\n"
        "Supplerende viden:\n"
        "- Der kan gives et kort generelt svar, når AI er tilgængelig.\n"
        "- Dette kommer ikke fra dokumentarkivet.\n\n"
        "Forbehold:\n- Vejledende svar – kontrollér altid lokale instrukser og gældende procedurer.\n\n"
        "Kilder:\n"
        "- Ingen relevante indlæste dokumenter fundet\n"
        "- Supplerende generel viden"
    )
    return answer, [{"label": "Ingen relevante indlæste dokumenter fundet"}], True


def ask_openai_knowledge(question, chunks):
    context = build_knowledge_context(chunks)
    no_docs_instruction = ""
    if not chunks:
        no_docs_instruction = (
            "Der blev ikke fundet relevante dokumentbidder. Brug formatet for ingen dokumentkilder. "
            "Svar kort, og placer generelle oplysninger under Supplerende viden."
        )
    payload = {
        "question": question,
        "document_context": context,
        "has_document_context": bool(chunks),
        "instruction": no_docs_instruction,
    }
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.responses.create(
        model=OPENAI_MODEL,
        reasoning={"effort": "low"},
        instructions=KNOWLEDGE_SYSTEM_PROMPT,
        input=json.dumps(payload, ensure_ascii=False),
    )
    return response.output_text


def answer_uses_supplemental_knowledge(answer, chunks):
    if not chunks:
        return True
    text = (answer or "").lower()
    if "supplerende generel viden" not in text and "supplerende viden" not in text:
        return False
    markers_without_supplement = [
        "ikke tilføjet",
        "ikke relevant",
        "ingen supplerende",
        "ikke nødvendig",
    ]
    return not any(marker in text for marker in markers_without_supplement)


@app.route("/register", methods=["GET", "POST"])
def register():
    if not db or not User:
        return Response(auth_page_html("Opret bruger", "", error_message="Database/login er ikke konfigureret."), status=503, mimetype="text/html")

    error = None
    info = None
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = normalize_email(request.form.get("email"))
        organization = (request.form.get("organization") or "").strip()
        password = request.form.get("password") or ""
        password_repeat = request.form.get("password_repeat") or ""

        if not name or not organization or not email or not password:
            error = "Udfyld alle felter."
        elif not email_looks_valid(email):
            error = "E-mail skal være gyldig."
        elif len(password) < 8:
            error = "Password skal være mindst 8 tegn."
        elif password != password_repeat:
            error = "Password og gentag password skal matche."
        elif User.query.filter_by(email=email).first():
            error = "E-mail er allerede oprettet."
        else:
            make_admin = should_make_admin(email)
            is_approved = bool(make_admin or not ADMIN_APPROVAL_REQUIRED)
            user = User(
                email=email,
                name=name,
                organization=organization,
                role="admin" if make_admin else "user",
                is_active=True,
                is_approved=is_approved,
                email_verified=False,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            session.clear()
            send_user_verification_and_admin_notice(user)
            info = (
                'Din bruger er oprettet. Vi har sendt en bekræftelsesmail til dig. '
                'Klik på linket i mailen for at bekræfte din e-mailadresse. '
                'Tjek også spam/uønsket mail. <a href="/login">Til login</a>'
            )

    body = """
    <form method="post" action="/register">
        <label>Navn<input name="name" required autocomplete="name"></label>
        <label>E-mail<input type="email" name="email" required autocomplete="email"></label>
        <label>Organisation/arbejdssted<input name="organization" required autocomplete="organization"></label>
        <label>Password<input type="password" name="password" required autocomplete="new-password"></label>
        <label>Gentag password<input type="password" name="password_repeat" required autocomplete="new-password"></label>
        <button type="submit">Opret bruger</button>
    </form>
    <div class="links"><a href="/login">Til login</a><a href="/contact">Kontakt support</a><a href="/privacy">Privatliv</a></div>
    """
    return Response(auth_page_html("Opret bruger", body, error, info), status=400 if error else 200, mimetype="text/html")


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    next_url = safe_next_url(request.args.get("next") or request.form.get("next"))
    if request.method == "POST":
        access_code = request.form.get("access_code") or ""
        if SHOW_CODE_LOGIN and BRIEF_ACCESS_CODE and access_code and hmac.compare_digest(access_code, BRIEF_ACCESS_CODE):
            session.clear()
            session["access_granted"] = True
            session["brief_authenticated"] = True
            return redirect(next_url)

        email = normalize_email(request.form.get("email"))
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first() if db and User and email else None
        if not user or not user.check_password(password):
            error = "Forkert e-mail eller password."
        elif not user.email_verified:
            error = (
                'Du skal bekræfte din e-mail, før du kan logge ind. '
                'Tjek også spam/uønsket mail. '
                f'<a href="/resend-verification?email={quote(email)}">Send bekræftelsesmail igen</a>'
            )
        elif not user.is_approved:
            error = "Din konto afventer godkendelse af administrator. Du får besked, når den er godkendt."
        elif not user.is_active:
            error = "Brugeren er deaktiveret."
        else:
            user.last_login_at = datetime.utcnow()
            db.session.commit()
            session.clear()
            session["user_id"] = user.id
            return redirect(next_url)

    code_login_html = (
        '<label>Adgangskode<input type="password" name="access_code" autocomplete="off"></label>'
        if SHOW_CODE_LOGIN else ""
    )
    body = f"""
    <form method="post" action="/login">
        <input type="hidden" name="next" value="{html.escape(next_url)}">
        <label>E-mail<input type="email" name="email" autocomplete="email"></label>
        <label>Password<input type="password" name="password" autocomplete="current-password"></label>
        {code_login_html}
        <button type="submit">Log ind</button>
    </form>
    <div class="links"><a href="/register">Opret bruger</a><a href="/forgot-password">Glemt adgangskode?</a><a href="/contact">Kontakt support</a><a href="/privacy">Privatliv</a></div>
    """
    return Response(auth_page_html("Log ind", body, error), status=401 if error else 200, mimetype="text/html")


@app.route("/code-login", methods=["GET", "POST"])
def code_login():
    if not BRIEF_ACCESS_CODE:
        return Response(auth_page_html("Adgangskode", "", error_message="Kode-login er ikke konfigureret."), status=503, mimetype="text/html")
    error = None
    next_url = safe_next_url(request.args.get("next") or request.form.get("next"))
    if request.method == "POST":
        access_code = request.form.get("access_code") or ""
        if hmac.compare_digest(access_code, BRIEF_ACCESS_CODE):
            session.clear()
            session["access_granted"] = True
            session["brief_authenticated"] = True
            return redirect(next_url)
        error = "Forkert adgangskode."
    body = f"""
    <form method="post" action="/code-login">
        <input type="hidden" name="next" value="{html.escape(next_url)}">
        <label>Adgangskode<input type="password" name="access_code" required autocomplete="off"></label>
        <button type="submit">Åbn</button>
    </form>
    <div class="links"><a href="/login">Log ind med bruger</a><a href="/privacy">Privatliv</a></div>
    """
    return Response(auth_page_html("Adgangskode", body, error), status=401 if error else 200, mimetype="text/html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if not db or not User or not PasswordResetToken:
        return Response(auth_page_html("Glemt password", "", error_message="Database/login er ikke konfigureret."), status=503, mimetype="text/html")

    info = None
    if request.method == "POST":
        email = normalize_email(request.form.get("email"))
        user = User.query.filter_by(email=email).first() if email else None
        if user and user.is_active:
            try:
                reset_link = create_password_reset_link(user)
                send_password_reset_email(user, reset_link)
            except Exception as error:
                app.logger.exception("Password reset kunne ikke sendes: %s", error)
        info = "Hvis e-mailen findes, er der sendt et link til nulstilling. Tjek også spam/uønsket mail."

    body = """
    <form method="post" action="/forgot-password">
        <label>E-mail<input type="email" name="email" required autocomplete="email"></label>
        <button type="submit">Send nulstillingslink</button>
    </form>
    <div class="links"><a href="/login">Til login</a><a href="/contact">Kontakt support</a><a href="/privacy">Privatliv</a></div>
    """
    return Response(auth_page_html("Glemt password", body, info_message=info), mimetype="text/html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if not db or not User or not PasswordResetToken:
        return Response(auth_page_html("Nulstil password", "", error_message="Database/login er ikke konfigureret."), status=503, mimetype="text/html")

    reset_token = find_valid_password_reset_token(token)
    if not reset_token:
        return Response(auth_page_html("Nulstil password", "", error_message="Linket er ugyldigt eller udløbet."), status=400, mimetype="text/html")

    error = None
    if request.method == "POST":
        password = request.form.get("password") or ""
        password_repeat = request.form.get("password_repeat") or ""
        if len(password) < 8:
            error = "Password skal være mindst 8 tegn."
        elif password != password_repeat:
            error = "Password og gentag password skal matche."
        else:
            reset_token.user.set_password(password)
            reset_token.used_at = datetime.utcnow()
            db.session.commit()
            session.clear()
            return Response(auth_page_html("Nulstil password", '<div class="links"><a href="/login">Til login</a></div>', info_message="Password er ændret."), mimetype="text/html")

    body = f"""
    <form method="post" action="/reset-password/{html.escape(token)}">
        <label>Nyt password<input type="password" name="password" required autocomplete="new-password"></label>
        <label>Gentag password<input type="password" name="password_repeat" required autocomplete="new-password"></label>
        <button type="submit">Gem nyt password</button>
    </form>
    <div class="links"><a href="/login">Til login</a><a href="/privacy">Privatliv</a></div>
    """
    return Response(auth_page_html("Nulstil password", body, error), status=400 if error else 200, mimetype="text/html")


@app.route("/verify-email/<token>", methods=["GET"])
def verify_email(token):
    if not db or not User or not EmailVerificationToken:
        return Response(auth_page_html("Bekræft e-mail", "", error_message="Database/login er ikke konfigureret."), status=503, mimetype="text/html")

    verification_token = find_valid_email_verification_token(token)
    if not verification_token:
        body = '<div class="links"><a href="/resend-verification">Send bekræftelsesmail igen</a><a href="/contact">Kontakt support</a></div>'
        return Response(auth_page_html("Bekræft e-mail", body, error_message="Linket er ugyldigt eller udløbet."), status=400, mimetype="text/html")

    user = verification_token.user
    user.email_verified = True
    user.email_verified_at = datetime.utcnow()
    verification_token.used_at = datetime.utcnow()
    db.session.commit()
    session.clear()
    body = '<div class="links"><a href="/login">Til login</a><a href="/contact">Kontakt support</a></div>'
    if user.is_approved and user.is_active:
        message = "Din e-mail er nu bekræftet. Du kan nu logge ind."
        body = '<div class="links"><a href="/login">Log ind</a><a href="/contact">Kontakt support</a></div>'
    else:
        message = (
            "Din e-mail er bekræftet. Din konto afventer stadig godkendelse "
            "af administrator. Du får besked, når kontoen er godkendt."
        )
    return Response(auth_page_html("Bekræft e-mail", body, info_message=message), mimetype="text/html")


def send_verification_for_email(email):
    user = User.query.filter_by(email=normalize_email(email)).first() if email else None
    if user and user.is_active and not user.email_verified:
        verification_link = create_email_verification_link(user)
        send_verification_email(user, verification_link)


@app.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    if not db or not User or not EmailVerificationToken:
        return Response(auth_page_html("Send bekræftelsesmail igen", "", error_message="Database/login er ikke konfigureret."), status=503, mimetype="text/html")

    info = None
    email = request.args.get("email", "").strip()
    if request.method == "POST":
        email = request.form.get("email", "").strip()
    if email:
        try:
            send_verification_for_email(email)
        except Exception as error:
            app.logger.exception("Bekræftelsesmail kunne ikke gensendes: %s", error)
        info = "Hvis e-mailen findes, har vi sendt en ny bekræftelsesmail. Tjek også spam/uønsket mail."

    body = f"""
    <form method="post" action="/resend-verification">
        <label>E-mail<input type="email" name="email" value="{html.escape(email)}" required autocomplete="email"></label>
        <button type="submit">Send bekræftelsesmail igen</button>
    </form>
    <div class="links"><a href="/login">Til login</a><a href="/contact">Kontakt support</a><a href="/privacy">Privatliv</a></div>
    """
    return Response(auth_page_html("Send bekræftelsesmail igen", body, info_message=info), mimetype="text/html")


@app.route("/contact", methods=["GET", "POST"])
def contact():
    error = None
    info = None
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = normalize_email(request.form.get("email"))
        subject = (request.form.get("subject") or "").strip()
        message = (request.form.get("message") or "").strip()
        recipient = CONTACT_EMAIL or ADMIN_EMAIL

        if not name or not subject or not email or not message:
            error = "Udfyld alle felter."
        elif not email_looks_valid(email):
            error = "E-mail skal være gyldig."
        elif len(message) < 10:
            error = "Besked skal være mindst 10 tegn."
        elif not recipient:
            error = "Kontaktmail er ikke konfigureret lige nu."
        elif not smtp_is_configured():
            error = "Beskeden kunne ikke sendes lige nu. Prøv igen senere."
        else:
            body = (
                f"Navn: {name}\n"
                f"E-mail: {email}\n\n"
                "Besked:\n"
                f"{message}"
            )
            try:
                send_email(recipient, f"Kontakt fra IndsatsBrief Brand: {subject}", body)
                info = "Din besked er sendt. Vi vender tilbage hurtigst muligt."
            except Exception as mail_error:
                app.logger.exception("Kontaktbesked kunne ikke sendes: %s", mail_error)
                error = "Beskeden kunne ikke sendes lige nu. Prøv igen senere."

    body = """
    <form method="post" action="/contact">
        <label>Navn<input name="name" required autocomplete="name"></label>
        <label>E-mail<input type="email" name="email" required autocomplete="email"></label>
        <label>Emne<input name="subject" required></label>
        <label>Besked<textarea name="message" required minlength="10" style="min-height:140px;border:1px solid var(--border);background:#020617;color:var(--text);padding:11px 13px;border-radius:14px;font:inherit;resize:vertical;"></textarea></label>
        <button type="submit">Send besked</button>
    </form>
    <div class="links"><a href="/login">Til login</a><a href="/privacy">Privatliv</a></div>
    """
    return Response(auth_page_html("Kontakt support", body, error, info), status=400 if error else 200, mimetype="text/html")


@app.route("/logout", methods=["GET"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/brief-login", methods=["GET", "POST"])
def brief_login():
    return redirect(url_for("login"))


@app.route("/brief-logout", methods=["GET"])
def brief_logout():
    return redirect(url_for("logout"))


def format_datetime(value):
    return value.strftime("%Y-%m-%d %H:%M") if value else "-"


def admin_nav_html():
    return (
        '<nav class="actions admin-nav">'
        '<a class="button secondary" href="/admin">Dashboard</a>'
        '<a class="button secondary" href="/admin/users">Brugere</a>'
        '<a class="button secondary" href="/admin/stations">Stationer</a>'
        '<a class="button secondary" href="/admin/resource-test">Ressourcetest</a>'
        '<a class="button secondary" href="/admin/knowledge">Viden</a>'
        '<a class="button secondary" href="/admin/status">Status</a>'
        '<a class="button secondary" href="/admin/audit">Ændringslog</a>'
        '<a class="button secondary" href="/brief">App</a>'
        '<a class="button secondary" href="/logout">Log ud</a>'
        '</nav>'
    )


def admin_layout(title, body):
    return f"""
<!doctype html>
<html lang="da">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} - IndsatsBrief Brand</title>
<style>
html,body{{width:100%;max-width:100%;overflow-x:hidden}}*{{box-sizing:border-box}}body{{margin:0;background:#0f172a;color:#f8fafc;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}.admin-shell{{width:min(100%,1180px);margin:0 auto;padding:24px}}header{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:18px;padding:18px 20px;border:1px solid rgba(255,255,255,.08);border-radius:16px;background:#111827}}h1,h2,h3,p{{margin-top:0}}p,small{{color:#cbd5e1}}a{{color:#93c5fd;overflow-wrap:anywhere}}main{{display:grid;gap:14px}}.admin-message{{margin-bottom:14px;padding:14px 16px;border:1px solid rgba(34,197,94,.35);border-radius:14px;background:rgba(34,197,94,.12);color:#bbf7d0;overflow-wrap:anywhere}}.admin-message.warning{{border-color:rgba(250,204,21,.4);background:rgba(250,204,21,.13);color:#fde68a}}.card,.user-card{{width:100%;max-width:100%;padding:18px;border:1px solid rgba(255,255,255,.08);border-radius:16px;background:#1e293b;overflow-wrap:anywhere}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}.actions{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}.admin-nav{{justify-content:flex-end}}button,.button{{min-height:42px;border:0;border-radius:12px;background:#2563eb;color:white!important;font-weight:800;padding:9px 13px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}}button.secondary,.button.secondary{{background:#334155}}button.warning{{background:#b45309}}input,textarea,select{{width:100%;min-height:44px;border-radius:12px;border:1px solid rgba(255,255,255,.08);background:#020617;color:#f8fafc;padding:9px 11px}}textarea{{min-height:110px}}label{{display:grid;gap:6px;color:#cbd5e1;font-weight:800}}.form-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}}.full{{grid-column:1/-1}}.badge{{display:inline-block;margin:2px 4px 2px 0;padding:3px 8px;border-radius:999px;background:rgba(255,255,255,.08);font-size:12px;color:#cbd5e1;font-weight:800}}.metric{{font-size:30px;font-weight:900;color:#f8fafc;margin:0}}.status-ok{{color:#bbf7d0}}.status-warning{{color:#fde68a}}.status-error{{color:#fecaca}}.warning-badge{{display:inline-block;margin-left:8px;padding:3px 7px;border-radius:999px;background:rgba(250,204,21,.16);color:#fde68a;font-size:12px;font-weight:800}}.stack-form{{display:grid;gap:12px}}@media(max-width:700px){{.admin-shell{{padding:12px}}header{{display:block}}.actions form,.actions a,.actions button{{width:100%}}button,.button{{width:100%}}}}
</style>
</head>
<body>
<div class="admin-shell">
<header><div><h1>{html.escape(title)}</h1><p>Admin · IndsatsBrief Brand</p></div>{admin_nav_html()}</header>
<main>{body}</main>
</div>
</body>
</html>
"""


def safe_model_count(model, filter_pending=False):
    if not db or not model:
        return None
    try:
        query = model.query
        if filter_pending:
            query = query.filter(model.is_approved.is_(False))
        return query.count()
    except Exception as error:
        app.logger.warning("Admin count failed for %s: %s", getattr(model, "__name__", model), error)
        return None


def display_count(value):
    return "–" if value is None else str(value)


def status_line(label, ok, detail=""):
    css = "status-ok" if ok else "status-warning"
    status = "OK" if ok else "Mangler"
    return f"<p><strong>{html.escape(label)}:</strong> <span class='{css}'>{status}</span>{' · ' + html.escape(detail) if detail else ''}</p>"


def env_set_label(name):
    return "sat" if os.getenv(name) else "mangler"


def test_email_recipient():
    return ADMIN_NOTIFY_EMAIL or ADMIN_EMAIL or SMTP_USERNAME


def send_admin_test_email():
    recipient = test_email_recipient()
    if not recipient:
        return False, "Ingen modtager er konfigureret."
    if not smtp_is_configured():
        return False, "SMTP mangler host eller afsender."
    body = (
        "Hej\n\n"
        "Dette er en testmail fra IndsatsBrief Brand.\n\n"
        "Hvis du modtager denne mail, virker SMTP-opsætningen.\n\n"
        "Venlig hilsen\n"
        "IndsatsBrief Brand"
    )
    try:
        send_email(recipient, "Testmail fra IndsatsBrief Brand", body)
        return True, "Testmail sendt."
    except Exception as error:
        app.logger.exception("Testmail kunne ikke sendes: %s", error)
        return False, f"Testmail kunne ikke sendes: {str(error)[:180]}"


def audit_log(action, entity_type=None, entity_id=None, entity_name=None, details=None):
    if not db or not AuditLog:
        return
    try:
        user = current_user()
        db.session.add(AuditLog(
            user_id=user.id if user else None,
            user_email=user.email if user else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            entity_name=entity_name,
            details=details or {},
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip(),
        ))
        db.session.commit()
    except Exception as error:
        try:
            db.session.rollback()
        except Exception:
            pass
        app.logger.exception("Audit-log kunne ikke gemmes: %s", error)


@app.route("/admin", methods=["GET"])
@admin_required
def admin_dashboard():
    user_count = safe_model_count(User)
    pending_users = safe_model_count(User, filter_pending=True)
    station_count = safe_model_count(Station)
    vehicle_count = safe_model_count(StationVehicle)
    resource_count = safe_model_count(StationResource)
    document_count = safe_model_count(KnowledgeDocument)
    chunk_count = safe_model_count(KnowledgeChunk)
    audit_count = safe_model_count(AuditLog)
    cards = [
        ("Brugere", "/admin/users", display_count(user_count), f"{display_count(pending_users)} afventer godkendelse"),
        ("Stationer", "/admin/stations", display_count(station_count), f"{display_count(vehicle_count)} køretøjer · {display_count(resource_count)} ressourcer"),
        ("Ressourcetest", "/admin/resource-test", "Test", "Afprøv søgeord og match-score"),
        ("Viden / Protokoller", "/admin/knowledge", display_count(document_count), f"{display_count(chunk_count)} tekstbidder"),
        ("Systemstatus", "/admin/status", "Status", "Database, SMTP, OpenAI og config"),
        ("Ændringslog", "/admin/audit", display_count(audit_count), "Seneste adminhandlinger"),
        ("Til appen", "/brief", "Åbn", "Gå til IndsatsBrief Brand"),
    ]
    body = '<section class="grid">'
    for title, link, metric, description in cards:
        body += f"""
        <article class="card">
            <h2>{html.escape(title)}</h2>
            <p class="metric">{html.escape(str(metric))}</p>
            <p>{html.escape(description)}</p>
            <p><a class="button" href="{html.escape(link)}">Åbn</a></p>
        </article>
        """
    body += "</section>"
    return Response(admin_layout("Admin", body), mimetype="text/html")


def database_status_payload():
    payload = {
        "ok": False,
        "detail": "Database er ikke konfigureret.",
        "users": None,
        "stations": None,
        "vehicles": None,
        "resources": None,
        "documents": None,
        "chunks": None,
        "audit_logs": None,
    }
    if not db:
        return payload
    try:
        with db.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        payload["ok"] = True
        payload["detail"] = "Database kan queries."
        payload["users"] = safe_model_count(User)
        payload["stations"] = safe_model_count(Station)
        payload["vehicles"] = safe_model_count(StationVehicle)
        payload["resources"] = safe_model_count(StationResource)
        payload["documents"] = safe_model_count(KnowledgeDocument)
        payload["chunks"] = safe_model_count(KnowledgeChunk)
        payload["audit_logs"] = safe_model_count(AuditLog)
    except Exception as error:
        app.logger.exception("Database status kunne ikke læses: %s", error)
        payload["detail"] = str(error)[:180]
    return payload


@app.route("/admin/status", methods=["GET"])
@admin_required
def admin_status():
    message = session.pop("admin_status_message", None)
    message_kind = session.pop("admin_status_kind", "success")
    message_html = f'<section class="admin-message {html.escape(message_kind)}">{html.escape(message)}</section>' if message else ""

    db_status = database_status_payload()
    smtp_required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_USE_TLS"]
    missing_smtp = [name for name in smtp_required if not os.getenv(name)]
    smtp_ok = not missing_smtp
    runtime_env = os.getenv("FLASK_ENV") or os.getenv("ENVIRONMENT") or ("production" if os.getenv("RENDER") else "development")
    uptime_seconds = max(0, int((datetime.now(timezone.utc) - APP_START_TIME).total_seconds()))

    body = f"""
    {message_html}
    <section class="grid">
        <article class="card">
            <h2>Database</h2>
            {status_line("Status", db_status["ok"], db_status["detail"])}
            <p>Brugere: {display_count(db_status["users"])}</p>
            <p>Stationer: {display_count(db_status["stations"])}</p>
            <p>Køretøjer: {display_count(db_status["vehicles"])}</p>
            <p>Ressourcer: {display_count(db_status["resources"])}</p>
            <p>Knowledge dokumenter: {display_count(db_status["documents"])}</p>
            <p>Knowledge chunks: {display_count(db_status["chunks"])}</p>
            <p>Audit logs: {display_count(db_status["audit_logs"])}</p>
        </article>
        <article class="card">
            <h2>SMTP/mail</h2>
            {status_line("Status", smtp_ok, "Alle SMTP env vars er sat." if smtp_ok else "Mangler: " + ", ".join(missing_smtp))}
            <p>SMTP_HOST: {html.escape(SMTP_HOST or "mangler")}</p>
            <p>SMTP_PORT: {html.escape(str(SMTP_PORT or "mangler"))}</p>
            <p>SMTP_USERNAME: {env_set_label("SMTP_USERNAME")}</p>
            <p>SMTP_PASSWORD: {env_set_label("SMTP_PASSWORD")}</p>
            <p>SMTP_FROM: {html.escape(SMTP_FROM or "mangler")}</p>
            <p>SMTP_USE_TLS: {html.escape(os.getenv("SMTP_USE_TLS") or "mangler")}</p>
            <form method="post" action="/admin/status/send-test-email"><button type="submit">Send testmail</button></form>
        </article>
        <article class="card">
            <h2>OpenAI</h2>
            {status_line("OPENAI_API_KEY", bool(os.getenv("OPENAI_API_KEY")))}
            <p>OPENAI_MODEL: {html.escape(OPENAI_MODEL or "mangler")}</p>
        </article>
        <article class="card">
            <h2>Datafordeler</h2>
            {status_line("DATAFORDELER_API_KEY", bool(os.getenv("DATAFORDELER_API_KEY")))}
        </article>
        <article class="card">
            <h2>App config</h2>
            <p>ADMIN_APPROVAL_REQUIRED: {str(ADMIN_APPROVAL_REQUIRED).lower()}</p>
            <p>ADMIN_EMAIL: {html.escape(ADMIN_EMAIL or "mangler")}</p>
            <p>ADMIN_NOTIFY_EMAIL: {html.escape(ADMIN_NOTIFY_EMAIL or "mangler")}</p>
            <p>CONTACT_EMAIL: {html.escape(CONTACT_EMAIL or "mangler")}</p>
            <p>APP_BASE_URL: {html.escape(APP_BASE_URL or "mangler")}</p>
            <p>DATABASE_URL: {env_set_label("DATABASE_URL")}</p>
            <p>FLASK_SECRET_KEY: {env_set_label("FLASK_SECRET_KEY")}</p>
            {('<p class="status-warning">APP_BASE_URL mangler. Links i mails kan blive forkerte.</p>' if not APP_BASE_URL else '')}
            {('<p class="status-warning">APP_BASE_URL bruger Render-domæne. Når custom domæne er sat op, bør den ændres.</p>' if 'onrender.com' in (APP_BASE_URL or '') else '')}
        </article>
        <article class="card">
            <h2>PWA</h2>
            <p>Manifest: <a href="/manifest.webmanifest">/manifest.webmanifest</a></p>
            <p>Service worker: <a href="/service-worker.js">/service-worker.js</a></p>
            <p>Kan installeres på telefon via “Føj til hjemmeskærm”.</p>
        </article>
        <article class="card">
            <h2>Runtime</h2>
            <p>Starttid: {html.escape(APP_START_TIME.strftime("%Y-%m-%d %H:%M:%S UTC"))}</p>
            <p>Oppetid: {uptime_seconds} sekunder</p>
            <p>Python: {html.escape(sys.version.split()[0])}</p>
            <p>Environment: {html.escape(runtime_env)}</p>
        </article>
    </section>
    """
    return Response(admin_layout("Systemstatus", body), mimetype="text/html")


@app.route("/admin/status/send-test-email", methods=["POST"])
@admin_required
def admin_status_send_test_email():
    ok, message = send_admin_test_email()
    audit_log("testmail_sent" if ok else "testmail_failed", "system", None, "SMTP testmail", {"ok": ok, "message": message})
    session["admin_status_message"] = message
    session["admin_status_kind"] = "success" if ok else "warning"
    return redirect(url_for("admin_status"))


RESOURCE_TEST_TERMS = [
    "stige", "drejestige", "lift", "robot", "luf", "luf-60", "drone", "droner",
    "uas", "båd", "redningsbåd", "kran", "tankvogn", "kemi", "cbrn", "frigørelse",
    "pumpe", "lys", "logistik",
]

RESOURCE_TEST_EXPECTATIONS = {
    "robot": "Bør kun vise stationer/køretøjer/ressourcer med eksplicit robot/LUF/TAF/crawler/fjernstyret.",
    "båd": "Bør kun vise båd/redningsbåd/vandredning/overfladeredning.",
    "kran": "Bør kun vise eksplicit kran/redningskran/kranvogn/køretøjskran.",
    "stige": "Bør vise konkrete køretøjer som S1 – drejestige, S2 – lift, specialstige osv.",
    "drone": "Bør vise drone/UAS/RPAS/UAV, ikke kamera/overblik alene.",
}


def admin_resource_test_results(query, address):
    if not query:
        return [], []
    matches = find_matching_station_resources(query, include_non_operational=True)
    warnings = []
    origin_lat = origin_lon = None
    if address:
        try:
            address_data = lookup_address(address)
            origin_lat = address_data.get("latitude")
            origin_lon = address_data.get("longitude")
            if origin_lat is None or origin_lon is None:
                warnings.append("Adressen kunne ikke slås op. Viser resultater uden afstand.")
        except Exception as error:
            app.logger.warning("Resource test address lookup failed: %s", error)
            warnings.append("Adressen kunne ikke slås op. Viser resultater uden afstand.")
    if origin_lat is not None and origin_lon is not None:
        matches = rank_stations_by_distance(origin_lat, origin_lon, matches, limit=10, radius_km=None)
    return matches[:10], warnings


@app.route("/admin/resource-test", methods=["GET"])
@admin_required
def admin_resource_test():
    query = (request.args.get("q") or "").strip()
    address = (request.args.get("address") or "").strip()
    matches, warnings = admin_resource_test_results(query, address)
    quick_buttons = "".join(
        f'<a class="button secondary" href="/admin/resource-test?q={quote(term)}">{html.escape(term)}</a>'
        for term in RESOURCE_TEST_TERMS
    )
    expectation_cards = "".join(
        f'<article class="card"><h3>{html.escape(term)}</h3><p>{html.escape(text)}</p></article>'
        for term, text in RESOURCE_TEST_EXPECTATIONS.items()
    )
    rows = []
    for item in matches:
        station = item.get("station") or {}
        rows.append(f"""
        <article class="card">
            <h2>{html.escape(station.get("name") or "Station")}</h2>
            <p><strong>Ressource:</strong> {html.escape(item.get("display_resource") or item.get("matched_resource") or "-")}</p>
            <p>matched_resource: {html.escape(str(item.get("matched_resource") or "-"))}</p>
            <p>match_source: {html.escape(str(item.get("match_source") or "-"))} · match_score: {html.escape(str(item.get("match_score") or "-"))}</p>
            <p>matched_terms: {html.escape(", ".join(item.get("matched_terms") or []) or "-")}</p>
            <p>Afstand/tid: {html.escape(str(item.get("air_distance_km") if item.get("air_distance_km") is not None else "-"))} km luftlinje · {html.escape(str(item.get("road_distance_km") if item.get("road_distance_km") is not None else "-"))} km vej · {html.escape(str(item.get("road_time_min") if item.get("road_time_min") is not None else "-"))} min</p>
            <p>data_source: {html.escape(station_resource_data_source())}</p>
        </article>
        """)
    body = f"""
    <section class="card">
        <h2>Test ressourcesøgning</h2>
        <form method="get" action="/admin/resource-test" class="form-grid">
            <label>Søgeord<input name="q" value="{html.escape(query)}" placeholder="fx robot"></label>
            <label>Adresse / koordinater<input name="address" value="{html.escape(address)}" placeholder="valgfrit, fx Antvorskov Alle 135, 4200 Slagelse"></label>
            <p class="actions full"><button type="submit">Test søgning</button></p>
        </form>
        <div class="actions">{quick_buttons}</div>
    </section>
    {' '.join(f'<section class="admin-message warning">{html.escape(warning)}</section>' for warning in warnings)}
    <section class="grid">{''.join(rows) if query else '<article class="card"><p>Indtast et søgeord eller brug hurtigknapperne.</p></article>'}</section>
    <section><h2>Forventningsliste</h2><div class="grid">{expectation_cards}</div></section>
    """
    return Response(admin_layout("Ressourcetest", body), mimetype="text/html")


@app.route("/admin/audit", methods=["GET"])
@admin_required
def admin_audit():
    if not db or not AuditLog:
        return Response(admin_layout("Ændringslog", '<section class="admin-message warning">Audit-log er ikke konfigureret.</section>'), status=503, mimetype="text/html")
    try:
        logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(100).all()
    except Exception as error:
        app.logger.exception("Audit-log kunne ikke læses: %s", error)
        logs = []
    cards = []
    for entry in logs:
        details = entry.details if isinstance(entry.details, dict) else {}
        detail_text = ", ".join(f"{key}: {value}" for key, value in list(details.items())[:5])
        cards.append(f"""
        <article class="card">
            <h2>{html.escape(entry.action or "-")}</h2>
            <p>{format_datetime(entry.created_at)} · {html.escape(entry.user_email or "system")}</p>
            <p>{html.escape(entry.entity_type or "-")} {html.escape(entry.entity_id or "")} · {html.escape(entry.entity_name or "")}</p>
            <p>{html.escape(detail_text or "-")}</p>
        </article>
        """)
    audit_cards = "".join(cards) or '<article class="card"><p>Ingen audit-log endnu.</p></article>'
    body = f'<section class="grid">{audit_cards}</section>'
    return Response(admin_layout("Ændringslog", body), mimetype="text/html")


def lines_from_form(name):
    return station_list_value(request.form.get(name))


def checkbox_value(name):
    return request.form.get(name) == "on"


def station_form_values(station=None):
    station = station or {}
    return {
        "name": (request.form.get("name") or station.get("name") or "").strip(),
        "aliases": lines_from_form("aliases") if request.method == "POST" else station_list_value(station.get("aliases")),
        "type": (request.form.get("type") or station.get("type") or "Brand/redning").strip(),
        "organization": (request.form.get("organization") or station.get("organization") or "").strip(),
        "operator": (request.form.get("operator") or station.get("operator") or "").strip(),
        "area": (request.form.get("area") or station.get("area") or "").strip(),
        "address": (request.form.get("address") or station.get("address") or "").strip(),
        "postal_code": (request.form.get("postal_code") or station.get("postal_code") or "").strip(),
        "city": (request.form.get("city") or station.get("city") or "").strip(),
        "lat": request.form.get("lat") if request.method == "POST" else station.get("lat"),
        "lon": request.form.get("lon") if request.method == "POST" else station.get("lon"),
        "source": (request.form.get("source") or station.get("source") or "manual").strip(),
        "is_active": checkbox_value("is_active") if request.method == "POST" else station.get("is_active", True),
        "operational_response_station": checkbox_value("operational_response_station") if request.method == "POST" else station.get("operational_response_station", True),
        "notes": (request.form.get("notes") or station.get("notes") or "").strip(),
    }


def apply_station_values(station, values):
    station.name = values["name"]
    station.aliases = values["aliases"]
    station.type = values["type"]
    station.organization = values["organization"]
    station.operator = values["operator"]
    station.area = values["area"]
    station.address = values["address"]
    station.postal_code = values["postal_code"]
    station.city = values["city"]
    station.lat = station_float_value(values["lat"])
    station.lon = station_float_value(values["lon"])
    station.source = values["source"]
    station.is_active = bool(values["is_active"])
    station.operational_response_station = bool(values["operational_response_station"])
    station.notes = values["notes"]
    if (station.lat is None or station.lon is None) and station.address:
        coordinates = get_station_coordinates({"address": station.address, "name": station.name})
        if coordinates:
            station.lat, station.lon = coordinates


def station_form_html(action, station=None, error=None):
    values = station_form_values(station_db_to_dict(station, include_inactive=True) if station else {})
    aliases_text = "\n".join(values["aliases"])
    active_checked = "checked" if values["is_active"] else ""
    operational_checked = "checked" if values["operational_response_station"] else ""
    error_html = f'<section class="admin-message warning">{html.escape(error)}</section>' if error else ""
    return f"""
{error_html}
<form method="post" action="{html.escape(action)}" class="card">
<div class="form-grid">
<label>Navn<input name="name" required value="{html.escape(values['name'])}"></label>
<label>Type<input name="type" value="{html.escape(values['type'])}"></label>
<label>Organisation<input name="organization" value="{html.escape(values['organization'])}"></label>
<label>Operatør<input name="operator" value="{html.escape(values['operator'])}"></label>
<label>Område<input name="area" value="{html.escape(values['area'])}"></label>
<label>Adresse<input name="address" value="{html.escape(values['address'])}"></label>
<label>Postnummer<input name="postal_code" value="{html.escape(values['postal_code'])}"></label>
<label>By<input name="city" value="{html.escape(values['city'])}"></label>
<label>Latitude<input name="lat" value="{'' if values['lat'] is None else html.escape(str(values['lat']))}"></label>
<label>Longitude<input name="lon" value="{'' if values['lon'] is None else html.escape(str(values['lon']))}"></label>
<label>Kilde<input name="source" value="{html.escape(values['source'])}"></label>
<label class="full">Aliases, én pr. linje<textarea name="aliases">{html.escape(aliases_text)}</textarea></label>
<label class="full">Noter<textarea name="notes">{html.escape(values['notes'])}</textarea></label>
<label><input type="checkbox" name="is_active" {active_checked}> Aktiv</label>
<label><input type="checkbox" name="operational_response_station" {operational_checked}> Operativ responsstation</label>
</div>
<p class="actions"><button type="submit">Gem station</button><a class="button secondary" href="/admin/stations">Tilbage</a></p>
</form>
"""


def resource_form_values(item=None, kind="vehicle"):
    item = item or {}
    name_attr = "vehicle_type" if kind == "vehicle" else "resource_type"
    return {
        "name": (request.form.get("name") or item.get("name") or "").strip(),
        "type": (request.form.get("type") or item.get(name_attr) or "").strip(),
        "description": (request.form.get("description") or item.get("description") or "").strip(),
        "aliases": lines_from_form("aliases") if request.method == "POST" else station_list_value(item.get("aliases")),
        "capabilities": lines_from_form("capabilities") if request.method == "POST" else station_list_value(item.get("capabilities")),
        "tags": lines_from_form("tags") if request.method == "POST" else station_list_value(item.get("tags")),
        "is_active": checkbox_value("is_active") if request.method == "POST" else item.get("is_active", True),
        "sort_order": request.form.get("sort_order") if request.method == "POST" else item.get("sort_order", 0),
    }


def resource_form_html(action, station, item=None, kind="vehicle", error=None):
    values = resource_form_values(item, kind)
    title = "Køretøj" if kind == "vehicle" else "Ressource"
    active_checked = "checked" if values["is_active"] else ""
    error_html = f'<section class="admin-message warning">{html.escape(error)}</section>' if error else ""
    return f"""
{error_html}
<form method="post" action="{html.escape(action)}" class="card">
<h2>{html.escape(title)} for {html.escape(station.name)}</h2>
<p>For at kunne findes i ressourcesøgning, bør relevante søgeord skrives i aliases eller capabilities, fx drone, UAS, RPAS, termisk kamera.</p>
<div class="form-grid">
<label>Navn/callsign<input name="name" required value="{html.escape(values['name'])}"></label>
<label>Type<input name="type" value="{html.escape(values['type'])}"></label>
<label>Sortering<input name="sort_order" type="number" value="{html.escape(str(values['sort_order'] or 0))}"></label>
<label class="full">Beskrivelse<textarea name="description">{html.escape(values['description'])}</textarea></label>
<label class="full">Aliases, én pr. linje<textarea name="aliases">{html.escape(chr(10).join(values['aliases']))}</textarea></label>
<label class="full">Capabilities, én pr. linje<textarea name="capabilities">{html.escape(chr(10).join(values['capabilities']))}</textarea></label>
<label class="full">Tags, én pr. linje<textarea name="tags">{html.escape(chr(10).join(values['tags']))}</textarea></label>
<label><input type="checkbox" name="is_active" {active_checked}> Aktiv</label>
</div>
<p class="actions"><button type="submit">Gem {html.escape(title.lower())}</button><a class="button secondary" href="/admin/stations/{station.id}/{kind}s">Tilbage</a></p>
</form>
"""


def apply_vehicle_values(vehicle, values):
    vehicle.name = values["name"]
    vehicle.vehicle_type = values["type"]
    vehicle.callsign = values["name"]
    vehicle.description = values["description"]
    vehicle.aliases = values["aliases"]
    vehicle.capabilities = values["capabilities"]
    vehicle.tags = values["tags"]
    vehicle.is_active = bool(values["is_active"])
    try:
        vehicle.sort_order = int(values["sort_order"] or 0)
    except Exception:
        vehicle.sort_order = 0


def apply_resource_values(resource, values):
    resource.name = values["name"]
    resource.resource_type = values["type"]
    resource.description = values["description"]
    resource.aliases = values["aliases"]
    resource.capabilities = values["capabilities"]
    resource.tags = values["tags"]
    resource.is_active = bool(values["is_active"])
    try:
        resource.sort_order = int(values["sort_order"] or 0)
    except Exception:
        resource.sort_order = 0


def user_action_button(user, action, label):
    return f'<form method="post" action="/admin/users/{user.id}/{action}"><button type="submit">{label}</button></form>'


@app.route("/admin/users", methods=["GET"])
@admin_required
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    current = current_user()
    reset_link = session.pop("admin_reset_link", None)
    reset_email = session.pop("admin_reset_email", None)
    admin_status_message = session.pop("admin_status_message", None)
    admin_status_kind = session.pop("admin_status_kind", "success")
    reset_message = (
        f'<section class="admin-message"><strong>Nulstillingslink til {html.escape(reset_email or "")}</strong><br><a href="{html.escape(reset_link)}">{html.escape(reset_link)}</a></section>'
        if reset_link else ""
    )
    status_message = (
        f'<section class="admin-message {html.escape(admin_status_kind)}">{html.escape(admin_status_message)}</section>'
        if admin_status_message else ""
    )
    rows = []
    for user in users:
        actions = []
        if not user.is_approved:
            actions.append(user_action_button(user, "approve", "Godkend"))
        if not user.email_verified:
            actions.append(user_action_button(user, "resend-verification", "Send bekræftelsesmail igen"))
        actions.append(user_action_button(user, "enable" if not user.is_active else "disable", "Aktivér" if not user.is_active else "Deaktiver"))
        actions.append(user_action_button(user, "remove-admin" if user.role == "admin" else "make-admin", "Fjern admin" if user.role == "admin" else "Gør til admin"))
        actions.append(user_action_button(user, "reset-password", "Nulstil kodeord"))
        self_note = " <span class='badge'>dig</span>" if current and user.id == current.id else ""
        email_note = " <span class='warning-badge'>E-mail ikke bekræftet</span>" if not user.email_verified else ""
        rows.append(f"""
        <article class="user-card">
            <h2>{html.escape(user.name or '')}{self_note}</h2>
            <p>{html.escape(user.email or '')}{email_note}</p>
            <p>Organisation: {html.escape(user.organization or '')}</p>
            <p>Rolle: {html.escape(user.role or '')} · E-mail bekræftet: {'ja' if user.email_verified else 'nej'} · Godkendt: {'ja' if user.is_approved else 'nej'} · Aktiv: {'ja' if user.is_active else 'nej'}</p>
            <p>Oprettet: {format_datetime(user.created_at)} · Seneste login: {format_datetime(user.last_login_at)}</p>
            <div class="actions">{''.join(actions)}</div>
        </article>
        """)
    body = f"""
    {status_message}
    {reset_message}
    <section class="grid">{''.join(rows) if rows else '<article class="card"><p>Ingen brugere.</p></article>'}</section>
    """
    return Response(admin_layout("Brugere", body), mimetype="text/html")


def update_user_admin_action(user_id, action):
    user = db.session.get(User, int(user_id))
    if not user:
        return redirect(url_for("admin_users"))
    current = current_user()
    if action == "approve":
        user.is_approved = True
        user.is_active = True
        db.session.commit()
        try:
            if send_user_approved_email(user):
                session["admin_status_message"] = "Brugeren er godkendt, og der er sendt besked pr. mail."
                session["admin_status_kind"] = "success"
            else:
                session["admin_status_message"] = "Brugeren er godkendt, men mailen kunne ikke sendes."
                session["admin_status_kind"] = "warning"
        except Exception as error:
            app.logger.exception("Godkendelsesmail kunne ikke sendes: %s", error)
            session["admin_status_message"] = "Brugeren er godkendt, men mailen kunne ikke sendes."
            session["admin_status_kind"] = "warning"
        audit_log("user_approved", "user", user.id, user.email)
        return redirect(url_for("admin_users"))
    elif action == "disable":
        if current and user.id == current.id:
            return redirect(url_for("admin_users"))
        if user.role == "admin" and admin_count() <= 1:
            return redirect(url_for("admin_users"))
        user.is_active = False
        audit_log("user_disabled", "user", user.id, user.email)
    elif action == "enable":
        user.is_active = True
        audit_log("user_enabled", "user", user.id, user.email)
    elif action == "make-admin":
        user.role = "admin"
        user.is_approved = True
        user.is_active = True
        audit_log("user_role_changed", "user", user.id, user.email, {"role": "admin"})
    elif action == "remove-admin":
        if user.role == "admin" and admin_count() <= 1:
            return redirect(url_for("admin_users"))
        user.role = "user"
        audit_log("user_role_changed", "user", user.id, user.email, {"role": "user"})
    db.session.commit()
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/approve", methods=["POST"])
@admin_required
def admin_approve_user(user_id):
    return update_user_admin_action(user_id, "approve")


@app.route("/admin/users/<int:user_id>/disable", methods=["POST"])
@admin_required
def admin_disable_user(user_id):
    return update_user_admin_action(user_id, "disable")


@app.route("/admin/users/<int:user_id>/enable", methods=["POST"])
@admin_required
def admin_enable_user(user_id):
    return update_user_admin_action(user_id, "enable")


@app.route("/admin/users/<int:user_id>/make-admin", methods=["POST"])
@admin_required
def admin_make_admin(user_id):
    return update_user_admin_action(user_id, "make-admin")


@app.route("/admin/users/<int:user_id>/remove-admin", methods=["POST"])
@admin_required
def admin_remove_admin(user_id):
    return update_user_admin_action(user_id, "remove-admin")


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def admin_reset_user_password(user_id):
    user = db.session.get(User, int(user_id))
    if user:
        try:
            reset_link = create_password_reset_link(user)
            try:
                send_password_reset_email(user, reset_link)
            except Exception as error:
                app.logger.exception("Admin password reset mail kunne ikke sendes: %s", error)
            session["admin_reset_link"] = reset_link
            session["admin_reset_email"] = user.email
        except Exception as error:
            app.logger.exception("Admin password reset kunne ikke oprettes: %s", error)
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/resend-verification", methods=["POST"])
@admin_required
def admin_resend_user_verification(user_id):
    user = db.session.get(User, int(user_id))
    if user and not user.email_verified:
        try:
            verification_link = create_email_verification_link(user)
            send_verification_email(user, verification_link)
        except Exception as error:
            app.logger.exception("Admin kunne ikke gensende bekræftelsesmail: %s", error)
    return redirect(url_for("admin_users"))


@app.route("/admin/stations", methods=["GET"])
@admin_required
def admin_stations():
    if not db or not Station:
        return Response(admin_layout("Stationer", '<section class="admin-message warning">Stationsdatabase er ikke konfigureret.</section>'), status=503, mimetype="text/html")

    message = session.pop("station_admin_message", None)
    message_kind = session.pop("station_admin_kind", "success")
    status_html = f'<section class="admin-message {html.escape(message_kind)}">{html.escape(message)}</section>' if message else ""
    cards = []
    for station in Station.query.order_by(Station.name.asc()).all():
        vehicle_count = StationVehicle.query.filter_by(station_id=station.id, is_active=True).count()
        resource_count = StationResource.query.filter_by(station_id=station.id, is_active=True).count()
        cards.append(f"""
<article class="card">
<h2>{html.escape(station.name)}</h2>
<p>{html.escape(station.organization or station.authority or station.operator or "Organisation ikke angivet")}</p>
<p>{html.escape(station.area or "")} {html.escape(station.address or "")}</p>
<p><span class="badge">{'Aktiv' if station.is_active else 'Deaktiveret'}</span><span class="badge">{'Primær/operativ' if station.operational_response_station else 'Støtte/ikke primær'}</span><span class="badge">{vehicle_count} køretøjer</span><span class="badge">{resource_count} ressourcer</span></p>
<div class="actions">
<a class="button" href="/admin/stations/{station.id}/edit">Rediger</a>
<a class="button secondary" href="/admin/stations/{station.id}/vehicles">Køretøjer</a>
<a class="button secondary" href="/admin/stations/{station.id}/resources">Ressourcer</a>
<form method="post" action="/admin/stations/{station.id}/toggle"><button class="warning" type="submit">{'Deaktiver' if station.is_active else 'Aktivér'}</button></form>
</div>
</article>
""")
    body = f"""
{status_html}
<section class="card">
<div class="actions"><a class="button" href="/admin/stations/new">Opret station</a><form method="post" action="/admin/stations/import-json"><button class="secondary" type="submit">Importer fra JSON hvis tom</button></form></div>
<p>Ressourcerne er vejledende og ikke live disponering.</p>
</section>
<section class="grid">{''.join(cards) or '<article class="card"><p>Ingen stationer oprettet endnu.</p></article>'}</section>
"""
    return Response(admin_layout("Stationer", body), mimetype="text/html")


@app.route("/admin/stations/import-json", methods=["POST"])
@admin_required
def admin_import_stations_json():
    if not db or not Station:
        return redirect(url_for("admin_stations"))
    before = Station.query.count()
    seed_fire_rescue_stations_if_empty()
    after = Station.query.count()
    if after > before:
        session["station_admin_message"] = f"Importerede {after - before} stationer fra JSON."
        audit_log("stations_imported", "station", None, "JSON import", {"count": after - before})
    else:
        session["station_admin_message"] = "Databasen har allerede stationer. Importen overskrev ikke admin-data."
        session["station_admin_kind"] = "warning"
    return redirect(url_for("admin_stations"))


def debug_station_item_payload(item, kind, expanded_terms):
    if kind == "vehicle":
        item_dict = {
            "id": item.id,
            "name": item.name,
            "callsign": item.callsign,
            "type": item.vehicle_type,
            "vehicle_type": item.vehicle_type,
            "description": item.description,
            "aliases": item.aliases,
            "capabilities": item.capabilities,
            "tags": getattr(item, "tags", None),
            "raw_data": getattr(item, "raw_data", None),
        }
    else:
        item_dict = {
            "id": item.id,
            "name": item.name,
            "type": item.resource_type,
            "resource_type": item.resource_type,
            "description": item.description,
            "aliases": item.aliases,
            "capabilities": item.capabilities,
            "tags": getattr(item, "tags", None),
            "raw_data": getattr(item, "raw_data", None),
        }

    search_values = []
    for key in ["name", "callsign", "type", "vehicle_type", "resource_type", "description", "aliases", "capabilities", "tags", "raw_data"]:
        search_values.extend(station_search_values(item_dict.get(key)))
    match = match_resource_query(
        expanded_terms,
        {},
        vehicle=item_dict if kind == "vehicle" else None,
        resource=item_dict if kind == "resource" else None,
    )
    strict_category = strict_query_category(expanded_terms)
    return {
        **item_dict,
        "is_active": bool(item.is_active),
        "aliases_normalized": normalize_terms(item.aliases),
        "capabilities_normalized": normalize_terms(item.capabilities),
        "tags_normalized": normalize_terms(getattr(item, "tags", None)),
        "normalized_search_terms": sorted(set(normalize_terms(search_values))),
        "matched": bool(match.get("matched")),
        "match_source": match.get("match_source"),
        "match_score": match.get("match_score"),
        "display_resource": match.get("display_resource"),
        "matched_terms": match.get("matched_terms", []),
        "rejected_reason": None if match.get("matched") else strict_rejection_reason(strict_category, item_dict, kind),
    }


@app.route("/admin/debug/resource-search", methods=["GET"])
@admin_required
def admin_debug_resource_search():
    query = request.args.get("q", "").strip()
    expanded_terms = expand_resource_query(query)
    strict_category = strict_query_category(expanded_terms)
    data_source = station_resource_data_source()
    searchable_stations, searchable_source = get_searchable_stations(include_non_operational=True)

    if not db or not Station:
        return jsonify({
            "query": query,
            "expanded_terms": expanded_terms,
            "data_source": "json_fallback",
            "error": "Database er ikke konfigureret.",
        })

    matched_results = []
    near_matches = []
    rejected_matches = []
    for station in searchable_stations:
        match = match_station_resource(expanded_terms, station)
        if match.get("matched"):
            matched_results.append({
                "station_id": station.get("id"),
                "station_name": station.get("name"),
                "matched_object_type": match.get("matched_object_type"),
                "matched_object_id": match.get("matched_object_id"),
                "display_resource": match.get("display_resource"),
                "match_source": match.get("match_source"),
                "match_score": match.get("match_score"),
                "matched_terms": match.get("matched_terms", []),
            })
        else:
            normalized_blob = " ".join(normalize_terms([
                station.get("name"),
                station.get("area"),
                station.get("organization"),
                station.get("notes"),
                station.get("aliases"),
            ]))
            if any(normalize_text(term) in normalized_blob for term in expanded_terms):
                near_matches.append({
                    "station_id": station.get("id"),
                    "station_name": station.get("name"),
                    "reason": "station text contains expanded term but no concrete resource matched",
                })
        rejected_matches.extend(rejected_strict_matches_for_station(expanded_terms, station))

    stations = Station.query.order_by(Station.name.asc()).all()
    active_stations = [station for station in stations if station.is_active]
    station_summaries = []
    vesterbro_details = []
    for station in stations:
        station_summaries.append({
            "station_id": station.id,
            "station_name": station.name,
            "is_active": bool(station.is_active),
            "operational_response_station": bool(station.operational_response_station),
            "vehicles_count": StationVehicle.query.filter_by(station_id=station.id, is_active=True).count(),
            "resources_count": StationResource.query.filter_by(station_id=station.id, is_active=True).count(),
        })
        if "vesterbro" in normalize_text(station.name) or normalize_text(query) in normalize_text(station.name):
            vesterbro_details.append({
                "station_id": station.id,
                "station_name": station.name,
                "is_active": bool(station.is_active),
                "operational_response_station": bool(station.operational_response_station),
                "vehicles": [
                    debug_station_item_payload(vehicle, "vehicle", expanded_terms)
                    for vehicle in sorted(station.vehicles or [], key=lambda item: (item.sort_order or 0, item.name or ""))
                ],
                "resources": [
                    debug_station_item_payload(resource, "resource", expanded_terms)
                    for resource in sorted(station.resources or [], key=lambda item: (item.sort_order or 0, item.name or ""))
                ],
            })

    return jsonify({
        "query": query,
        "expanded_terms": expanded_terms,
        "expanded_terms_normalized": normalize_terms(expanded_terms),
        "strict_query": bool(strict_category),
        "strict_category": strict_category,
        "allowed_strict_terms": allowed_strict_terms(strict_category) if strict_category else [],
        "data_source": data_source,
        "searchable_data_source": searchable_source,
        "postgres_active_station_count": len(active_stations),
        "postgres_active_vehicle_count": StationVehicle.query.filter_by(is_active=True).count(),
        "postgres_active_resource_count": StationResource.query.filter_by(is_active=True).count(),
        "matched_results": matched_results,
        "near_matches": near_matches,
        "rejected_matches": rejected_matches,
        "stations_reviewed": station_summaries,
        "vesterbro": vesterbro_details,
    })


@app.route("/admin/stations/new", methods=["GET", "POST"])
@admin_required
def admin_station_new():
    if not db or not Station:
        return Response(admin_layout("Opret station", '<section class="admin-message warning">Stationsdatabase er ikke konfigureret.</section>'), status=503, mimetype="text/html")
    error = None
    if request.method == "POST":
        values = station_form_values()
        if not values["name"]:
            error = "Navn er påkrævet."
        else:
            station = Station()
            apply_station_values(station, values)
            db.session.add(station)
            db.session.commit()
            invalidate_station_data_cache()
            audit_log("station_created", "station", station.id, station.name)
            session["station_admin_message"] = "Stationen er oprettet."
            return redirect(url_for("admin_stations"))
    return Response(admin_layout("Opret station", station_form_html("/admin/stations/new", None, error)), status=400 if error else 200, mimetype="text/html")


@app.route("/admin/stations/<int:station_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_station_edit(station_id):
    station = db.session.get(Station, station_id) if db and Station else None
    if not station:
        return Response(admin_layout("Rediger station", '<section class="admin-message warning">Stationen blev ikke fundet.</section>'), status=404, mimetype="text/html")
    error = None
    if request.method == "POST":
        values = station_form_values(station_db_to_dict(station, include_inactive=True))
        if not values["name"]:
            error = "Navn er påkrævet."
        else:
            apply_station_values(station, values)
            db.session.commit()
            invalidate_station_data_cache()
            audit_log("station_updated", "station", station.id, station.name)
            session["station_admin_message"] = "Stationen er gemt."
            return redirect(url_for("admin_stations"))
    return Response(admin_layout("Rediger station", station_form_html(f"/admin/stations/{station.id}/edit", station, error)), status=400 if error else 200, mimetype="text/html")


@app.route("/admin/stations/<int:station_id>/toggle", methods=["POST"])
@admin_required
def admin_station_toggle(station_id):
    station = db.session.get(Station, station_id) if db and Station else None
    if station:
        station.is_active = not station.is_active
        db.session.commit()
        invalidate_station_data_cache()
        audit_log("station_toggled", "station", station.id, station.name, {"is_active": station.is_active})
        session["station_admin_message"] = "Stationens status er opdateret."
    return redirect(url_for("admin_stations"))


def admin_station_items(station_id, kind):
    station = db.session.get(Station, station_id) if db and Station else None
    if not station:
        return Response(admin_layout("Station", '<section class="admin-message warning">Stationen blev ikke fundet.</section>'), status=404, mimetype="text/html")
    is_vehicle = kind == "vehicles"
    items = station.vehicles if is_vehicle else station.resources
    title = "Køretøjer" if is_vehicle else "Ressourcer"
    new_url = f"/admin/stations/{station.id}/{kind}/new"
    message = session.pop("station_admin_message", None)
    message_kind = session.pop("station_admin_kind", "success")
    status_html = f'<section class="admin-message {html.escape(message_kind)}">{html.escape(message)}</section>' if message else ""
    cards = []
    for item in sorted(items, key=lambda entry: (entry.sort_order or 0, entry.name or "")):
        type_text = item.vehicle_type if is_vehicle else item.resource_type
        edit_url = f"/admin/{'vehicles' if is_vehicle else 'resources'}/{item.id}/edit"
        toggle_url = f"/admin/{'vehicles' if is_vehicle else 'resources'}/{item.id}/delete"
        capability_badges = "".join(f'<span class="badge">{html.escape(term)}</span>' for term in station_list_value(item.capabilities))
        cards.append(f"""
<article class="card">
<h2>{html.escape(item.name)}</h2>
<p>{html.escape(type_text or "")}</p>
<p>{capability_badges}</p>
<p><span class="badge">{'Aktiv' if item.is_active else 'Deaktiveret'}</span></p>
<div class="actions"><a class="button" href="{edit_url}">Rediger</a><form method="post" action="{toggle_url}"><button class="warning" type="submit">{'Deaktiver' if item.is_active else 'Aktivér'}</button></form></div>
</article>
""")
    body = f"""
{status_html}
<section class="card"><h2>{html.escape(station.name)}</h2><p class="actions"><a class="button" href="{new_url}">Opret {'køretøj' if is_vehicle else 'ressource'}</a><a class="button secondary" href="/admin/stations">Til stationer</a></p></section>
<section class="grid">{''.join(cards) or '<article class="card"><p>Ingen poster endnu.</p></article>'}</section>
"""
    return Response(admin_layout(f"{title} - {station.name}", body), mimetype="text/html")


@app.route("/admin/stations/<int:station_id>/vehicles", methods=["GET"])
@admin_required
def admin_station_vehicles(station_id):
    return admin_station_items(station_id, "vehicles")


@app.route("/admin/stations/<int:station_id>/resources", methods=["GET"])
@admin_required
def admin_station_resources(station_id):
    return admin_station_items(station_id, "resources")


@app.route("/admin/stations/<int:station_id>/vehicles/new", methods=["GET", "POST"])
@admin_required
def admin_vehicle_new(station_id):
    station = db.session.get(Station, station_id) if db and Station else None
    if not station:
        return Response(admin_layout("Køretøj", '<section class="admin-message warning">Stationen blev ikke fundet.</section>'), status=404, mimetype="text/html")
    error = None
    if request.method == "POST":
        values = resource_form_values(kind="vehicle")
        if not values["name"]:
            error = "Navn er påkrævet."
        else:
            vehicle = StationVehicle(station_id=station.id)
            apply_vehicle_values(vehicle, values)
            db.session.add(vehicle)
            db.session.commit()
            invalidate_station_data_cache()
            audit_log("vehicle_created", "vehicle", vehicle.id, vehicle.name, {"station_id": station.id})
            session["station_admin_message"] = "Køretøj gemt. Ændringen bruges nu i ressourcesøgningen."
            return redirect(url_for("admin_station_vehicles", station_id=station.id))
    return Response(admin_layout("Opret køretøj", resource_form_html(f"/admin/stations/{station.id}/vehicles/new", station, kind="vehicle", error=error)), status=400 if error else 200, mimetype="text/html")


@app.route("/admin/stations/<int:station_id>/resources/new", methods=["GET", "POST"])
@admin_required
def admin_resource_new(station_id):
    station = db.session.get(Station, station_id) if db and Station else None
    if not station:
        return Response(admin_layout("Ressource", '<section class="admin-message warning">Stationen blev ikke fundet.</section>'), status=404, mimetype="text/html")
    error = None
    if request.method == "POST":
        values = resource_form_values(kind="resource")
        if not values["name"]:
            error = "Navn er påkrævet."
        else:
            resource = StationResource(station_id=station.id)
            apply_resource_values(resource, values)
            db.session.add(resource)
            db.session.commit()
            invalidate_station_data_cache()
            audit_log("resource_created", "resource", resource.id, resource.name, {"station_id": station.id})
            session["station_admin_message"] = "Ressource gemt. Ændringen bruges nu i ressourcesøgningen."
            return redirect(url_for("admin_station_resources", station_id=station.id))
    return Response(admin_layout("Opret ressource", resource_form_html(f"/admin/stations/{station.id}/resources/new", station, kind="resource", error=error)), status=400 if error else 200, mimetype="text/html")


@app.route("/admin/vehicles/<int:vehicle_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_vehicle_edit(vehicle_id):
    vehicle = db.session.get(StationVehicle, vehicle_id) if db and StationVehicle else None
    if not vehicle:
        return Response(admin_layout("Køretøj", '<section class="admin-message warning">Køretøjet blev ikke fundet.</section>'), status=404, mimetype="text/html")
    item = {"name": vehicle.name, "vehicle_type": vehicle.vehicle_type, "description": vehicle.description, "aliases": vehicle.aliases, "capabilities": vehicle.capabilities, "tags": getattr(vehicle, "tags", []), "is_active": vehicle.is_active, "sort_order": vehicle.sort_order}
    if request.method == "POST":
        values = resource_form_values(item, kind="vehicle")
        if values["name"]:
            apply_vehicle_values(vehicle, values)
            db.session.commit()
            invalidate_station_data_cache()
            audit_log("vehicle_updated", "vehicle", vehicle.id, vehicle.name, {"station_id": vehicle.station_id})
            session["station_admin_message"] = "Køretøj gemt. Ændringen bruges nu i ressourcesøgningen."
            return redirect(url_for("admin_station_vehicles", station_id=vehicle.station_id))
    return Response(admin_layout("Rediger køretøj", resource_form_html(f"/admin/vehicles/{vehicle.id}/edit", vehicle.station, item, "vehicle")), mimetype="text/html")


@app.route("/admin/resources/<int:resource_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_resource_edit(resource_id):
    resource = db.session.get(StationResource, resource_id) if db and StationResource else None
    if not resource:
        return Response(admin_layout("Ressource", '<section class="admin-message warning">Ressourcen blev ikke fundet.</section>'), status=404, mimetype="text/html")
    item = {"name": resource.name, "resource_type": resource.resource_type, "description": resource.description, "aliases": resource.aliases, "capabilities": resource.capabilities, "tags": getattr(resource, "tags", []), "is_active": resource.is_active, "sort_order": resource.sort_order}
    if request.method == "POST":
        values = resource_form_values(item, kind="resource")
        if values["name"]:
            apply_resource_values(resource, values)
            db.session.commit()
            invalidate_station_data_cache()
            audit_log("resource_updated", "resource", resource.id, resource.name, {"station_id": resource.station_id})
            session["station_admin_message"] = "Ressource gemt. Ændringen bruges nu i ressourcesøgningen."
            return redirect(url_for("admin_station_resources", station_id=resource.station_id))
    return Response(admin_layout("Rediger ressource", resource_form_html(f"/admin/resources/{resource.id}/edit", resource.station, item, "resource")), mimetype="text/html")


@app.route("/admin/vehicles/<int:vehicle_id>/delete", methods=["POST"])
@app.route("/admin/vehicles/<int:vehicle_id>/toggle", methods=["POST"])
@admin_required
def admin_vehicle_toggle(vehicle_id):
    vehicle = db.session.get(StationVehicle, vehicle_id) if db and StationVehicle else None
    if vehicle:
        vehicle.is_active = not vehicle.is_active
        db.session.commit()
        invalidate_station_data_cache()
        audit_log("vehicle_toggled", "vehicle", vehicle.id, vehicle.name, {"is_active": vehicle.is_active})
        session["station_admin_message"] = "Køretøjets status er opdateret. Ændringen bruges nu i ressourcesøgningen."
        return redirect(url_for("admin_station_vehicles", station_id=vehicle.station_id))
    return redirect(url_for("admin_stations"))


@app.route("/admin/resources/<int:resource_id>/delete", methods=["POST"])
@app.route("/admin/resources/<int:resource_id>/toggle", methods=["POST"])
@admin_required
def admin_resource_toggle(resource_id):
    resource = db.session.get(StationResource, resource_id) if db and StationResource else None
    if resource:
        resource.is_active = not resource.is_active
        db.session.commit()
        invalidate_station_data_cache()
        audit_log("resource_toggled", "resource", resource.id, resource.name, {"is_active": resource.is_active})
        session["station_admin_message"] = "Ressourcens status er opdateret. Ændringen bruges nu i ressourcesøgningen."
        return redirect(url_for("admin_station_resources", station_id=resource.station_id))
    return redirect(url_for("admin_stations"))


def knowledge_document_card(document):
    return f"""
    <article class="user-card">
        <h2>{html.escape(document.title or '')}</h2>
        <p>Kategori: {html.escape(document.category or '-')} · Udgiver: {html.escape(document.publisher or '-')}</p>
        <p>Version/dato: {html.escape(document.version_date or '-')} · Aktiv: {'ja' if document.is_active else 'nej'}</p>
        <p>Sider: {document.page_count or 0} · Tekstbidder: {document.chunk_count or 0} · Status: {html.escape(document.import_status or '-')} · Oprettet: {format_datetime(document.created_at)}</p>
        {f'<p>Fejl: {html.escape(document.import_error)}</p>' if document.import_error else ''}
        <div class="actions">
            <form method="post" action="/admin/knowledge/{document.id}/toggle"><button type="submit">{'Deaktiver' if document.is_active else 'Aktivér'}</button></form>
            <form method="post" action="/admin/knowledge/{document.id}/delete"><button type="submit">Slet dokument</button></form>
        </div>
    </article>
    """


@app.route("/admin/knowledge", methods=["GET"])
@admin_required
def admin_knowledge():
    if not db or not KnowledgeDocument:
        return Response(auth_page_html("Viden / Protokoller", "", error_message="Knowledge-database er ikke konfigureret."), status=503, mimetype="text/html")

    documents = KnowledgeDocument.query.order_by(KnowledgeDocument.created_at.desc()).all()
    message = session.pop("knowledge_admin_message", None)
    message_html = f'<section class="admin-message">{html.escape(message)}</section>' if message else ""
    cards = "".join(knowledge_document_card(document) for document in documents) or '<article class="card"><p>Ingen dokumenter importeret endnu.</p></article>'
    body = f"""
    {message_html}
    <section class="card">
        <h2>Upload PDF</h2>
        <form method="post" action="/admin/knowledge/upload" enctype="multipart/form-data" class="stack-form">
            <label>Titel<input name="title" required></label>
            <label>Kategori<input name="category" placeholder="Fx Beredskabsstyrelsen, CBRN, Brand"></label>
            <label>Udgiver<input name="publisher"></label>
            <label>Version/dato<input name="version_date"></label>
            <label>Kilde-URL<input name="source_url" type="url"></label>
            <label>PDF-fil<input name="pdf_file" type="file" accept="application/pdf,.pdf" required></label>
            <button type="submit">Importer PDF</button>
        </form>
    </section>
    <section class="card">
        <h2>Test spørgsmål mod dokumenter</h2>
        <form method="post" action="/admin/knowledge/test-search" class="stack-form">
            <label>Spørgsmål<input name="question" placeholder="Fx Hvad siger dokumenterne om lithiumbatterier?" required></label>
            <button type="submit">Test søgning</button>
        </form>
    </section>
    <section class="grid">{cards}</section>
    """
    return Response(admin_layout("Viden / Protokoller", body), mimetype="text/html")


@app.route("/admin/knowledge/upload", methods=["POST"])
@admin_required
def admin_knowledge_upload():
    if not db or not KnowledgeDocument or not KnowledgeChunk:
        return Response(auth_page_html("Viden / Protokoller", "", error_message="Knowledge-database er ikke konfigureret."), status=503, mimetype="text/html")

    upload = request.files.get("pdf_file")
    title = (request.form.get("title") or "").strip()
    if not upload or not upload.filename or not upload.filename.lower().endswith(".pdf"):
        session["knowledge_admin_message"] = "Vælg en PDF-fil."
        return redirect(url_for("admin_knowledge"))
    file_bytes = upload.read()
    if len(file_bytes) > 25 * 1024 * 1024:
        session["knowledge_admin_message"] = "PDF-filen er for stor. Maksimum er 25 MB."
        return redirect(url_for("admin_knowledge"))
    if not title:
        title = os.path.splitext(secure_filename(upload.filename))[0] or "Uden titel"

    document = KnowledgeDocument(
        title=title,
        category=(request.form.get("category") or "").strip(),
        publisher=(request.form.get("publisher") or "").strip(),
        version_date=(request.form.get("version_date") or "").strip(),
        source_url=(request.form.get("source_url") or "").strip(),
        original_filename=secure_filename(upload.filename),
        uploaded_by_user_id=(current_user().id if current_user() else None),
        import_status="importerer",
    )
    db.session.add(document)
    db.session.commit()
    try:
        page_count, chunks = extract_pdf_chunks(file_bytes)
        document.page_count = page_count
        if not chunks:
            document.import_status = "PDF indeholder ingen læsbar tekst"
            document.import_error = "Der blev ikke fundet tekst i PDF'en. OCR er ikke implementeret i første version."
            document.chunk_count = 0
        else:
            for index, chunk in enumerate(chunks, start=1):
                db.session.add(KnowledgeChunk(
                    document_id=document.id,
                    chunk_index=index,
                    page_start=chunk.get("page_start"),
                    page_end=chunk.get("page_end"),
                    text=chunk.get("text") or "",
                ))
            document.chunk_count = len(chunks)
            document.import_status = "importeret"
            document.import_error = None
        db.session.commit()
        audit_log("document_uploaded", "knowledge_document", document.id, document.title, {"chunks": document.chunk_count, "status": document.import_status})
        session["knowledge_admin_message"] = f"Dokumentet er importeret med {document.chunk_count} tekstbidder."
    except Exception as error:
        app.logger.exception("PDF kunne ikke importeres: %s", error)
        document.import_status = "fejl"
        document.import_error = str(error)
        db.session.commit()
        audit_log("document_upload_failed", "knowledge_document", document.id, document.title, {"error": str(error)[:300]})
        session["knowledge_admin_message"] = "PDF kunne ikke læses. Kontrollér at filen indeholder tekst."
    return redirect(url_for("admin_knowledge"))


@app.route("/admin/knowledge/test-search", methods=["POST"])
@admin_required
def admin_knowledge_test_search():
    question = (request.form.get("question") or "").strip()
    if not question:
        return redirect(url_for("admin_knowledge"))
    chunks = search_knowledge_chunks(question, limit=5)
    cards = []
    for chunk in chunks:
        document = chunk.document
        excerpt = (chunk.text or "")[:650].strip()
        cards.append(f"""
        <article class="card">
            <h2>{html.escape(document.title or 'Dokument')}</h2>
            <p>Side: {html.escape(format_page_range(chunk.page_start, chunk.page_end))} · Chunk: {chunk.chunk_index}</p>
            <p>{html.escape(excerpt)}{'...' if len(chunk.text or '') > 650 else ''}</p>
        </article>
        """)
    audit_log("knowledge_test_search", "knowledge", None, "Test spørgsmål", {"question": question, "chunks": len(chunks)})
    body = f"""
    <section class="card"><h2>Testresultat</h2><p><strong>Spørgsmål:</strong> {html.escape(question)}</p><p><a class="button secondary" href="/admin/knowledge">Tilbage</a></p></section>
    <section class="grid">{''.join(cards) or '<article class="card"><p>Ingen dokumentbidder fundet.</p></article>'}</section>
    """
    return Response(admin_layout("Knowledge test", body), mimetype="text/html")


@app.route("/admin/knowledge/<int:document_id>/toggle", methods=["POST"])
@admin_required
def admin_knowledge_toggle(document_id):
    document = db.session.get(KnowledgeDocument, int(document_id)) if KnowledgeDocument else None
    if document:
        document.is_active = not document.is_active
        document.updated_at = datetime.utcnow()
        db.session.commit()
        audit_log("document_toggled", "knowledge_document", document.id, document.title, {"is_active": document.is_active})
    return redirect(url_for("admin_knowledge"))


@app.route("/admin/knowledge/<int:document_id>/delete", methods=["POST"])
@admin_required
def admin_knowledge_delete(document_id):
    document = db.session.get(KnowledgeDocument, int(document_id)) if KnowledgeDocument else None
    if document:
        title = document.title
        db.session.delete(document)
        db.session.commit()
        audit_log("document_deleted", "knowledge_document", document_id, title)
    return redirect(url_for("admin_knowledge"))


@app.route("/knowledge", methods=["GET"])
def knowledge_page():
    configuration_message = brief_configuration_message()
    if configuration_message:
        return Response(configuration_message, status=503, mimetype="text/plain")
    if not is_logged_in():
        return redirect(url_for("login", next="/knowledge"))
    admin_link = '<a class="nav-link" href="/admin/knowledge">Administrer dokumenter</a>' if is_admin_user() else ""
    html_page = f"""
<!DOCTYPE html>
<html lang="da">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Viden / Protokoller</title>
    <style>
        :root {{ --bg:#0f172a; --card:#111827; --card-soft:#1e293b; --border:rgba(255,255,255,.08); --primary:#2563eb; --text:#f8fafc; --muted:#cbd5e1; --warning:#facc15; }}
        html,body{{width:100%;max-width:100%;overflow-x:hidden}}*{{box-sizing:border-box}}
        body{{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
        main{{width:min(100%,1050px);margin:0 auto;padding:22px}}
        .topline,.card{{border:1px solid var(--border);border-radius:16px;background:var(--card);padding:18px 20px;box-shadow:0 16px 45px rgba(0,0,0,.22)}}
        .topline{{display:flex;justify-content:space-between;gap:14px;align-items:center;margin-bottom:16px}}
        h1,h2,p{{margin-top:0}}p,li{{color:var(--muted)}}a{{color:#93c5fd;overflow-wrap:anywhere}}.nav{{display:flex;gap:10px;flex-wrap:wrap}}.nav-link{{min-height:42px;display:inline-flex;align-items:center;padding:0 12px;border-radius:12px;background:rgba(255,255,255,.06);text-decoration:none;color:var(--text);font-weight:800}}
        textarea{{width:100%;min-height:145px;border-radius:14px;border:1px solid var(--border);background:#020617;color:var(--text);padding:12px;font:inherit;resize:vertical}}
        button{{min-height:48px;border:0;border-radius:12px;background:var(--primary);color:white;font-weight:900;padding:0 16px;cursor:pointer}}
        .stack{{display:grid;gap:14px}}.answer{{white-space:pre-wrap;line-height:1.55;overflow-wrap:anywhere}}.answer-section{{padding:12px 0;border-top:1px solid var(--border)}}.answer-section:first-child{{border-top:0;padding-top:0}}.answer-heading{{margin:0 0 8px;color:#bfdbfe;font-weight:900;text-transform:uppercase;font-size:13px;letter-spacing:.03em}}.answer-body{{white-space:pre-wrap;color:var(--muted)}}.source-list{{display:grid;gap:8px}}.badge{{display:inline-block;padding:4px 8px;border-radius:999px;background:rgba(250,204,21,.13);color:#fde68a;font-size:12px;font-weight:850}}
        @media(max-width:700px){{main{{padding:12px}}.topline{{display:block}}button{{width:100%}}}}
    </style>
</head>
<body><main class="stack">
    <section class="topline"><div><h1>Viden / Protokoller</h1><p>Stil spørgsmål til uploadede vejledninger og protokoller.</p></div><nav class="nav"><a class="nav-link" href="/brief">Til brief</a>{admin_link}</nav></section>
    <section class="card">
        <p><strong>AI’en bruger først de indlæste dokumenter.</strong> Hvis dokumenterne ikke dækker spørgsmålet, kan svaret suppleres med generel viden. Kontrollér altid mod lokale instrukser og gældende procedurer.</p>
        <p>Svaret erstatter ikke lokale instrukser, indsatslederens beslutninger eller gældende procedurer.</p>
        <div class="stack">
            <textarea id="question" placeholder="Spørg fx: Hvad beskriver dokumenterne om lithium-ion batterier, CBRN eller røgdykning?"></textarea>
            <button id="ask-button" type="button">Spørg</button>
        </div>
    </section>
    <section id="answer-card" class="card" style="display:none">
        <h2>Svar</h2>
        <p id="supplemental-badge" class="badge" hidden>Supplerende generel viden brugt</p>
        <div id="answer" class="answer"></div>
    </section>
    <section id="sources-card" class="card" style="display:none">
        <h2>Kilder</h2>
        <div id="sources" class="source-list"></div>
    </section>
</main>
<script>
const askButton = document.getElementById('ask-button');
const answerCard = document.getElementById('answer-card');
const answer = document.getElementById('answer');
const sourcesCard = document.getElementById('sources-card');
const sources = document.getElementById('sources');
const supplementalBadge = document.getElementById('supplemental-badge');
function renderAnswer(text) {{
    answer.replaceChildren();
    const raw = String(text || '').trim();
    if (!raw) return;
    const headingPattern = /^(Kort svar|Dokumentgrundlag|Praktisk betydning|Supplerende viden|Forbehold|Kilder):\\s*$/gmi;
    const matches = [...raw.matchAll(headingPattern)];
    if (!matches.length) {{
        answer.textContent = raw;
        return;
    }}
    matches.forEach((match, index) => {{
        const start = match.index + match[0].length;
        const end = index + 1 < matches.length ? matches[index + 1].index : raw.length;
        const section = document.createElement('section');
        section.className = 'answer-section';
        const heading = document.createElement('h3');
        heading.className = 'answer-heading';
        heading.textContent = match[1];
        const body = document.createElement('div');
        body.className = 'answer-body';
        body.textContent = raw.slice(start, end).trim();
        section.append(heading, body);
        answer.appendChild(section);
    }});
}}
async function fetchJson(url, options) {{
    const response = await fetch(url, options);
    const text = await response.text();
    let data;
    try {{ data = JSON.parse(text); }} catch (error) {{ throw new Error('API’en returnerede ikke JSON.'); }}
    if (!response.ok) throw new Error(data.error || `API-fejl ${{response.status}}`);
    return data;
}}
askButton.addEventListener('click', async () => {{
    const question = document.getElementById('question').value.trim();
    if (!question) return;
    askButton.disabled = true;
    answerCard.style.display = 'block';
    answer.textContent = 'Søger i dokumenter...';
    sourcesCard.style.display = 'none';
    try {{
        const data = await fetchJson('/knowledge/ask', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ question }})
        }});
        renderAnswer(data.answer || '');
        supplementalBadge.hidden = !data.used_supplemental_knowledge;
        sources.innerHTML = '';
        (data.sources || []).forEach(source => {{
            const div = document.createElement('div');
            div.textContent = source.label || source.title || 'Kilde';
            sources.appendChild(div);
        }});
        if (data.sources && data.sources.length) sourcesCard.style.display = 'block';
    }} catch (error) {{
        answer.textContent = error.message || 'Kunne ikke hente svar.';
    }} finally {{
        askButton.disabled = false;
    }}
}});
</script>
</body></html>
    """
    return Response(html_page, mimetype="text/html")


@app.route("/knowledge/ask", methods=["POST"])
def knowledge_ask():
    access_error = brief_api_access_error()
    if access_error:
        return access_error
    try:
        payload = request.get_json(silent=True) or {}
        question = (payload.get("question") or "").strip()
        if not question:
            return jsonify({"ok": False, "error": "Spørgsmål mangler"}), 400
        chunks = search_knowledge_chunks(question)
        sources = knowledge_sources_from_chunks(chunks)
        try:
            if os.getenv("OPENAI_API_KEY"):
                answer = ask_openai_knowledge(question, chunks)
                used_supplemental = answer_uses_supplemental_knowledge(answer, chunks)
            else:
                answer, sources, used_supplemental = fallback_knowledge_answer(question, chunks)
        except Exception as error:
            app.logger.exception("Knowledge AI-svar fejlede: %s", error)
            answer, sources, used_supplemental = fallback_knowledge_answer(question, chunks)
        if not sources and not chunks:
            sources = [{"label": "Ingen relevante indlæste dokumenter fundet"}, {"label": "Supplerende generel viden"}]
            used_supplemental = True
        elif used_supplemental:
            labels = {source.get("label") for source in sources}
            if "Supplerende generel viden" not in labels:
                sources.append({"label": "Supplerende generel viden"})
        return jsonify({
            "ok": True,
            "answer": answer,
            "sources": sources,
            "used_supplemental_knowledge": bool(used_supplemental),
            "document_chunks_found": len(chunks),
        })
    except Exception as error:
        app.logger.exception("Knowledge ask error")
        return jsonify({"ok": False, "error": "Kunne ikke besvare spørgsmålet.", "details": str(error)}), 500


@app.route("/brief", methods=["GET"])
def brief_page():
    """Small browser client for the short, presentation-safe incident brief."""
    configuration_message = brief_configuration_message()
    if configuration_message:
        return Response(configuration_message, status=503, mimetype="text/plain")
    if not is_logged_in():
        return redirect(url_for("login", next="/brief"))
    user = current_user()
    user_status = (
        f"Logget ind som: {user.name or user.email}"
        if user else "Adgang via kode"
    )

    html = r"""
<!DOCTYPE html>
<html lang="da">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>IndsatsBrief Brand</title>
    <link rel="manifest" href="/manifest.webmanifest">
    <meta name="theme-color" content="#0f172a">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-title" content="IndsatsBrief">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
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
        .user-status { margin: 6px 0 0; color: var(--muted); font-size: 14px; }
        .topline { display: flex; justify-content: space-between; gap: 16px; align-items: center; max-width: 100%; min-width: 0; margin-bottom: 22px; padding: 18px 20px; border: 1px solid var(--border); border-radius: 16px; background: linear-gradient(135deg, rgba(30,41,59,.95), rgba(17,24,39,.95)); box-shadow: 0 20px 60px rgba(0,0,0,.28); }
        .top-actions { display: flex; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
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
        .station-details { margin-top: 12px; padding: 12px; border-radius: 12px; border: 1px solid var(--border); background: rgba(15,23,42,.8); }
        .station-details h4 { margin: 12px 0 6px; }
        .station-detail-item { padding: 8px 0; border-top: 1px solid var(--border); }
        .station-detail-title { margin: 0 0 4px; color: var(--text); font-weight: 900; }
        .station-detail-subtitle { margin: 0 0 10px; color: var(--muted); }
        .badge { display: inline-flex; align-items: center; min-height: 26px; border-radius: 999px; padding: 3px 9px; background: rgba(255,255,255,.08); color: var(--muted); font-size: 12px; font-weight: 850; }
        .resource-form { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: end; max-width: 100%; }
        @media (max-width: 1000px) { main { padding: 20px 18px 44px; } .main-grid { grid-template-columns: 1fr; } #map-frame { height: 320px; } }
        @media (max-width: 700px) { main { padding-left: 12px; padding-right: 12px; } .topline { align-items: flex-start; } .search, .commands, .assistance-controls, .resource-form { grid-template-columns: minmax(0, 1fr); } .commands { gap: 9px; } button, select { width: 100%; } .card, #result, #map-section, .tool-panel, #assistance-section, #resource-section { padding: 16px; } #map-frame { height: 280px; } }
        @media (max-width: 480px) { main { padding: 14px 12px 34px; } h1 { font-size: 24px; } .topline { display: block; } .top-actions { margin-top: 14px; display: grid; grid-template-columns: 1fr; } .logout { width: 100%; } #map-frame { height: 260px; } #report { font-size: 15px; } }
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
        <div class="topline"><div><h1>IndsatsBrief Brand</h1><p class="intro">Adressebrief · BBR · OSM · Vejr · Assistance</p><p class="user-status">__USER_STATUS__</p><p class="user-status">Kan installeres på telefon via “Føj til hjemmeskærm”.</p></div><div class="top-actions"><a class="logout" href="/knowledge">Viden / Protokoller</a><a class="logout" href="/contact">Kontakt support</a><a class="logout" href="/logout">Log ud</a></div></div>
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
                <section id="resource-section"><h2>Ressourcesøgning</h2><div class="resource-form"><label>Ressource<input id="resource-search-input" placeholder="Søg fx stige, tankvogn, kemi, redningsbåd, TAF 60, kran…"></label><button id="resource-search-button" type="button">Søg ressource</button></div><div id="resource-result" class="tool-result"></div></section>
                <section class="tool-panel"><h2>Spørg til rapporten</h2><textarea id="side-followup-question" placeholder="Spørg fx: Er der kælder? Hvad er tagmaterialet? Hvad viser OSM?"></textarea><button id="side-ask-followup" type="button">Spørg</button><div id="side-followup-result" class="tool-result"></div></section>
            </div>
        </div>
    </main>
    <script>
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/service-worker.js').catch(() => {});
            });
        }
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
            if (item.display_resource) return item.display_resource;
            if (item.matched_resource) return item.matched_resource;
            if (item.resource) return item.resource;
            const name = item.matched_resource_name;
            const type = item.matched_resource_type;
            if (name && type && String(name).toLowerCase() !== String(type).toLowerCase()) return `${name} – ${type}`;
            if (name || type) return name || type;
            return null;
        }

        function appendBadgeList(container, values) {
            (values || []).filter(Boolean).forEach(value => {
                const badge = document.createElement('span');
                badge.className = 'badge';
                badge.textContent = value;
                container.appendChild(badge);
            });
        }

        function uniqueResourceBadges(item) {
            const seen = new Set();
            return [...(item.tags || []), ...(item.capabilities || []), ...(item.aliases || [])].filter(value => {
                const key = String(value || '').toLowerCase();
                if (!key || seen.has(key)) return false;
                seen.add(key);
                return true;
            });
        }

        function renderStationDetails(container, data) {
            container.replaceChildren();
            const title = document.createElement('h3');
            title.textContent = data.name || 'Stationsdetaljer';
            container.appendChild(title);
            const subtitle = document.createElement('p');
            subtitle.className = 'station-detail-subtitle';
            subtitle.textContent = [data.organization || data.organisation, data.area].filter(Boolean).join(' · ');
            if (subtitle.textContent) container.appendChild(subtitle);
            const address = [data.address, data.postal_code, data.city].filter(Boolean).join(', ');
            [
                data.type && `Type: ${data.type}`,
                data.operator && `Operatør: ${data.operator}`,
                address && `Adresse: ${address}`,
                data.source && `Kilde: ${data.source}`,
                'Vejledende ressourceoversigt – ikke live disponering.'
            ].filter(Boolean).forEach(text => { const p = document.createElement('p'); p.textContent = text; container.appendChild(p); });

            const vehicles = data.vehicles || [];
            const resources = data.resources || [];
            if (vehicles.length) {
                const heading = document.createElement('h4');
                heading.textContent = 'Køretøjer';
                container.appendChild(heading);
                vehicles.forEach(vehicle => {
                    const item = document.createElement('div');
                    item.className = 'station-detail-item';
                    const label = vehicle.vehicle_type ? `${vehicle.name} – ${vehicle.vehicle_type}` : vehicle.name;
                    const p = document.createElement('p');
                    p.className = 'station-detail-title';
                    p.textContent = label;
                    item.appendChild(p);
                    if (vehicle.description) {
                        const description = document.createElement('p');
                        description.textContent = vehicle.description;
                        item.appendChild(description);
                    }
                    appendBadgeList(item, uniqueResourceBadges(vehicle));
                    container.appendChild(item);
                });
            }
            if (resources.length) {
                const heading = document.createElement('h4');
                heading.textContent = 'Særlige ressourcer';
                container.appendChild(heading);
                resources.forEach(resource => {
                    const item = document.createElement('div');
                    item.className = 'station-detail-item';
                    const p = document.createElement('p');
                    p.className = 'station-detail-title';
                    p.textContent = resource.resource_type && resource.resource_type !== resource.name ? `${resource.name} – ${resource.resource_type}` : resource.name;
                    item.appendChild(p);
                    if (resource.description) {
                        const description = document.createElement('p');
                        description.textContent = resource.description;
                        item.appendChild(description);
                    }
                    appendBadgeList(item, uniqueResourceBadges(resource));
                    container.appendChild(item);
                });
            }
            if (!vehicles.length && !resources.length) {
                const empty = document.createElement('p');
                empty.textContent = 'Der er ikke registreret detaljerede ressourcer for denne station endnu.';
                container.appendChild(empty);
            }
            const disclaimer = document.createElement('p');
            disclaimer.textContent = data.disclaimer || 'Ressourcerne er vejledende og ikke live disponering.';
            container.appendChild(disclaimer);
        }

        function addStationDetailsControl(card, station) {
            if (!station.station_id || station.source === 'OSM') return;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'secondary';
            button.textContent = 'Se ressourcer';
            const details = document.createElement('div');
            details.className = 'station-details';
            details.hidden = true;
            button.addEventListener('click', async event => {
                event.stopPropagation();
                if (!details.hidden) { details.hidden = true; return; }
                details.hidden = false;
                details.textContent = 'Henter ressourcer…';
                try {
                    const data = await fetchJson(`/api/stations/${encodeURIComponent(station.station_id)}`);
                    renderStationDetails(details, data);
                } catch (error) {
                    details.textContent = error.message || 'Kunne ikke hente stationsdetaljer.';
                }
            });
            card.appendChild(button);
            card.appendChild(details);
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
                    addStationDetailsControl(card, station);
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
                    addStationDetailsControl(card, result);
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
    html = html.replace("__USER_STATUS__", user_status)
    return Response(html, mimetype="text/html")


@app.route("/manifest.webmanifest", methods=["GET"])
def manifest_webmanifest():
    payload = {
        "name": "IndsatsBrief Brand",
        "short_name": "IndsatsBrief",
        "start_url": "/brief",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#7f1d1d",
        "description": "Adressebrief, BBR, OSM, vejr og assistance til brand/redning.",
        "icons": [
            {"src": "/static/icons/indsatsbrief-icon.svg", "sizes": "192x192", "type": "image/svg+xml", "purpose": "any maskable"},
            {"src": "/static/icons/indsatsbrief-icon.svg", "sizes": "512x512", "type": "image/svg+xml", "purpose": "any maskable"},
        ],
    }
    return Response(json.dumps(payload, ensure_ascii=False), mimetype="application/manifest+json")


@app.route("/static/icons/indsatsbrief-icon.svg", methods=["GET"])
def indsatsbrief_icon_svg():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" rx="96" fill="#0f172a"/><path d="M256 66c50 54 86 105 86 167 0 73-45 129-86 129s-86-56-86-129c0-62 36-113 86-167z" fill="#ef4444"/><path d="M256 178c30 33 52 64 52 101 0 44-27 78-52 78s-52-34-52-78c0-37 22-68 52-101z" fill="#facc15"/><path d="M128 398h256v38H128z" fill="#f8fafc"/></svg>"""
    return Response(svg, mimetype="image/svg+xml")


@app.route("/service-worker.js", methods=["GET"])
def service_worker_js():
    js = """
const CACHE_NAME = 'indsatsbrief-shell-v1';
const SHELL_ASSETS = ['/manifest.webmanifest', '/static/icons/indsatsbrief-icon.svg'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL_ASSETS)).catch(() => null));
  self.skipWaiting();
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)))));
  self.clients.claim();
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') return;
  if (url.pathname.startsWith('/admin') || url.pathname.startsWith('/api') || url.pathname.includes('brief') || url.pathname.startsWith('/login') || url.pathname.startsWith('/register')) return;
  if (SHELL_ASSETS.includes(url.pathname)) {
    event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
  }
});
"""
    return Response(js, mimetype="application/javascript")


@app.route("/privacy", methods=["GET"])
def privacy_policy():
    html = """
    <!DOCTYPE html>
    <html lang="da">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Privatliv - IndsatsBrief Brand</title>
        <style>
            html, body { width: 100%; max-width: 100%; overflow-x: hidden; }
            * { box-sizing: border-box; }
            body { margin: 0; background: #0f172a; color: #f8fafc; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.65; }
            main { width: min(100%, 880px); margin: 0 auto; padding: 28px 18px 56px; }
            h1 { margin: 0 0 8px; }
            h2 { margin-top: 28px; color: #bfdbfe; }
            p, li { color: #cbd5e1; overflow-wrap: anywhere; }
            a { color: #93c5fd; }
            .card { border: 1px solid rgba(255,255,255,.08); border-radius: 16px; background: #111827; padding: 20px; }
            .note { background: rgba(250,204,21,.12); border-left: 4px solid #facc15; padding: 12px 14px; border-radius: 10px; color: #f8fafc; }
        </style>
    </head>
    <body><main><div class="card">
        <h1>Privatliv og cookies</h1>
        <p><strong>Senest opdateret:</strong> 28. juni 2026</p>

        <p>IndsatsBrief Brand er et støtteværktøj til brand-/redningsbriefs. Værktøjet samler oplysninger fra adresseopslag, BBR, OSM, vejrdata, kortlinks, stations-/ressourcedata og indlæste vidensdokumenter.</p>

        <h2>Brugeroplysninger</h2>
        <p>Ved brugeroprettelse gemmes navn, e-mail, organisation/arbejdssted, rolle/status, e-mailbekræftelse, godkendelsesstatus, oprettelsestidspunkt og seneste login. Oplysningerne bruges til login og adgangsstyring. Password gemmes som hash og aldrig i klartekst.</p>

        <h2>Adminændringer og audit-log</h2>
        <p>Adminhandlinger kan gemmes i en ændringslog med tidspunkt, bruger, handling, berørt post og tekniske oplysninger som IP-adresse. Det bruges til drift, sikkerhed og fejlfinding.</p>

        <h2>Viden og dokumenter</h2>
        <p>Uploadede PDF’er kan udtrækkes til dokumenttekster og tekstbidder/chunks, så de kan søges i og bruges som kildegrundlag for AI-svar.</p>

        <h2>Adresseopslag og eksterne datakilder</h2>
        <p>Når du laver en brief, kan adressen og radius sendes til relevante datakilder/API’er som DAWA/Dataforsyningen, Datafordeleren/BBR, Open-Meteo, OpenStreetMap/Overpass og kort-/luftfotolinks. Data bruges til at danne briefen og til fejlfinding.</p>

        <h2>Cookies</h2>
        <p>IndsatsBrief bruger nødvendige session-cookies til login og sikker adgang. Der bruges ikke marketing- eller trackingcookies i denne version.</p>

        <h2>Logs og drift</h2>
        <p>Hostingplatformen kan behandle tekniske logs som tidspunkt, endpoint, IP-adresse og fejlbeskeder til drift, sikkerhed og fejlfinding.</p>

        <h2>Sletning af konto</h2>
        <p>Brugere kan kontakte admin/support for sletning eller rettelse af konto og tilknyttede oplysninger.</p>

        <h2>Begrænsning</h2>
        <div class="note">IndsatsBrief og AI-svar er vejledende støtteoplysninger. Det erstatter ikke beredskabets egne systemer, lokale procedurer, objektplaner, officielle databaser eller faglig vurdering.</div>

        <p><a href="/brief">Tilbage til IndsatsBrief</a> · <a href="/contact">Kontakt support</a></p>
    </div></main>
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


def build_short_report_sections(incident_data):
    """Stable short-report sections: same incident data gives same report sections."""
    return build_deterministic_report_structured(incident_data, "short")


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
    access_error = brief_api_access_error()
    if access_error:
        return access_error

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

        if report_mode == "short":
            report_structured = build_short_report_sections(raw_incident_data)
            return jsonify({
                "report_text": build_report_text(report_structured),
                "report_structured": report_structured,
                "raw_incident_data": raw_incident_data,
                "report_mode": report_mode,
                **build_analyze_debug(raw_incident_data, payload),
            })

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            fallback_report = build_deterministic_report_structured(raw_incident_data, report_mode)
            return jsonify({
                "error": "OPENAI_API_KEY is not configured",
                "report_text": build_report_text(fallback_report),
                "report_structured": fallback_report,
                "raw_incident_data": raw_incident_data,
                "report_mode": report_mode,
                **build_analyze_debug(raw_incident_data, payload),
            }), 503

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

    search_result = search_resources(
        resource_query,
        origin_lat,
        origin_lon,
        limit=min(max(limit * 3, limit), 30),
        radius_km=radius_km,
        include_non_operational=True,
    )
    expanded_terms = search_result["expanded_terms"]
    ranked = search_result["matches"]
    data_source = search_result["data_source"]

    results = []
    for item in ranked:
        station = item.get("station") or {}
        results.append({
            "station_id": station.get("id"),
            "name": station.get("name"),
            "station_name": station.get("name"),
            "display_name": station.get("name"),
            "station_key": canonical_station_key(station),
            "type": station.get("type"),
            "organization": station.get("organization"),
            "authority": station.get("authority"),
            "operator": station.get("operator"),
            "area": station.get("area"),
            "address": station.get("address"),
            "operational_response_station": station.get("operational_response_station") is not False,
            "display_resource": item.get("display_resource") or item.get("matched_resource"),
            "resource": item.get("resource") or item.get("matched_resource"),
            "matched_resource": item.get("matched_resource"),
            "matched_type": item.get("matched_type"),
            "matched_resource_name": item.get("matched_resource_name"),
            "matched_resource_type": item.get("matched_resource_type"),
            "matched_resource_kind": item.get("matched_resource_kind"),
            "matched_capabilities": item.get("matched_capabilities", []),
            "matched_terms": item.get("matched_terms", []),
            "matched_object_type": item.get("matched_object_type"),
            "matched_object_id": item.get("matched_object_id"),
            "match_source": item.get("match_source"),
            "air_distance_km": item.get("air_distance_km"),
            "road_distance_km": item.get("road_distance_km"),
            "road_time_min": item.get("road_time_min"),
            "source": station.get("source", "manual"),
            "data_source": data_source,
            "notes": station.get("notes"),
            "match_score": item.get("match_score"),
        })
    results = dedupe_station_results(results, resource_mode=True)[:limit]

    payload = {
        "address": address_data.get("normalized_address") or address,
        "resource_query": resource_query,
        "expanded_terms": expanded_terms,
        "data_source": data_source,
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


def station_detail_from_json(identifier):
    identifier_text = str(identifier or "")
    for station in load_fire_rescue_stations_from_json():
        if str(station.get("id")) == identifier_text or canonical_station_key(station) == identifier_text:
            return {
                "id": station.get("id"),
                "name": station.get("name"),
                "aliases": station_list_value(station.get("aliases")),
                "type": station.get("type"),
                "organization": station.get("organization") or station.get("organisation"),
                "organisation": station.get("organization") or station.get("organisation"),
                "authority": station.get("authority"),
                "operator": station.get("operator"),
                "area": station.get("area"),
                "address": station.get("address"),
                "postal_code": station.get("postal_code"),
                "city": station.get("city"),
                "lat": station.get("lat"),
                "lon": station.get("lon"),
                "source": station.get("source") or "manual",
                "vehicles": [
                    {
                        "id": vehicle.get("id"),
                        "name": vehicle.get("name"),
                        "vehicle_type": vehicle.get("type") or vehicle.get("vehicle_type"),
                        "description": vehicle.get("description"),
                        "aliases": station_list_value(vehicle.get("aliases")),
                        "capabilities": station_list_value(vehicle.get("capabilities")),
                    }
                    for vehicle in station.get("vehicles") or []
                ],
                "resources": [
                    {
                        "id": resource.get("id"),
                        "name": resource.get("name") or resource.get("type"),
                        "resource_type": resource.get("type") or "ressource",
                        "description": resource.get("description"),
                        "aliases": station_list_value(resource.get("aliases")),
                        "capabilities": station_list_value(resource.get("capabilities")),
                    }
                    for collection in ["trailers", "containers"]
                    for resource in station.get(collection) or []
                ] + [
                    {
                        "id": None,
                        "name": name,
                        "resource_type": name,
                        "description": "",
                        "aliases": [],
                        "capabilities": [name],
                    }
                    for name in station.get("special_resources") or []
                ],
                "notes": station.get("notes"),
                "disclaimer": "Ressourcerne er vejledende og ikke live disponering.",
            }
    return None


def station_detail_payload(identifier):
    if db and Station:
        try:
            station = None
            if str(identifier).isdigit():
                station = db.session.get(Station, int(identifier))
            if not station:
                station = Station.query.filter_by(source_ref_id=str(identifier)).first()
            if station and (station.is_active or is_admin_user()):
                payload = station_db_to_dict(station, include_inactive=is_admin_user())
                payload["vehicles"] = [
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "vehicle_type": item["vehicle_type"],
                        "description": item["description"],
                        "aliases": item["aliases"],
                        "capabilities": item["capabilities"],
                        "tags": item.get("tags", []),
                        "sort_order": item.get("sort_order", 0),
                    }
                    for item in sorted(payload.get("vehicles", []), key=lambda value: (value.get("sort_order") or 0, value.get("name") or ""))
                    if item.get("is_active", True) or is_admin_user()
                ]
                payload["resources"] = [
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "resource_type": item["resource_type"],
                        "description": item["description"],
                        "aliases": item["aliases"],
                        "capabilities": item["capabilities"],
                        "tags": item.get("tags", []),
                        "sort_order": item.get("sort_order", 0),
                    }
                    for item in sorted(payload.get("resources", []), key=lambda value: (value.get("sort_order") or 0, value.get("name") or ""))
                    if item.get("is_active", True) or is_admin_user()
                ]
                if not is_admin_user():
                    payload.pop("notes", None)
                payload["disclaimer"] = "Ressourcerne er vejledende og ikke live disponering."
                return payload
        except Exception as error:
            app.logger.warning("Stationsdetaljer kunne ikke hentes fra database: %s", error)
    return station_detail_from_json(identifier)


@app.route("/api/stations/<station_id>", methods=["GET"])
def api_station_details(station_id):
    access_error = brief_api_access_error()
    if access_error:
        return access_error

    payload = station_detail_payload(station_id)
    if not payload:
        return jsonify({"ok": False, "error": "Stationen blev ikke fundet"}), 404
    if not is_admin_user():
        payload.pop("notes", None)
    payload["ok"] = True
    return jsonify(payload)


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
    resource = result.get("display_resource") or result.get("matched_resource") or result.get("resource") or resource_query
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
    access_error = brief_api_access_error()
    if access_error:
        return access_error

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
    for station in sorted(nearby_stations, key=lambda item: item["air_distance_km"])[:min(max(limit * 3, limit), 30)]:
        route = get_driving_route_osrm(
            incident_lat, incident_lon, station["lat"], station["lon"]
        )
        stations.append({
            "station_id": station.get("id") or station.get("osm_id"),
            "name": station["name"],
            "station_name": station["name"],
            "display_name": station["name"],
            "station_key": canonical_station_key(station),
            "type": station["type"],
            "organization": station.get("organization"),
            "authority": station.get("authority"),
            "operator": station.get("operator"),
            "area": station["area"],
            "address": station.get("address"),
            "air_distance_km": station["air_distance_km"],
            "source": station.get("source", "manual"),
            **route,
        })

    stations = dedupe_station_results(stations, resource_mode=False)[:limit]
    stations.sort(key=lambda item: (
        item["drive_time_min"] is None,
        item["drive_time_min"] if item["drive_time_min"] is not None else item["air_distance_km"],
        item["air_distance_km"],
        item.get("name") or "",
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
