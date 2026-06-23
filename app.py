from flask import Flask, request, jsonify
from datetime import datetime
import requests

app = Flask(__name__)
app.json.ensure_ascii = False


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

        # Dataforsyningen returnerer typisk [lon, lat]
        longitude = coords[0] if coords else None
        latitude = coords[1] if coords else None

        return {
            "normalized_address": item.get("adressebetegnelse", address),
            "municipality": adgangsadresse.get("kommune", {}).get("navn", "Ikke verificeret"),
            "postal_code": adgangsadresse.get("postnummer", {}).get("nr", "Ikke verificeret"),
            "city": adgangsadresse.get("postnummer", {}).get("navn", "Ikke verificeret"),
            "latitude": latitude,
            "longitude": longitude
        }

    except Exception as e:
        return {
            "error": str(e)
        }


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


@app.route("/")
def home():
    return "IndsatsBrief API kører"


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

    data = {
        "normalized_address": normalized_address,
        "municipality": municipality,
        "postal_code": postal_code,
        "city": city,
        "latitude": latitude,
        "longitude": longitude,

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
            "note": "Luftfoto ikke hentet i denne version"
        },

        "weather": weather_data,

        "building": {
            "source": "Ikke tilgængeligt i denne version",
            "bbr_id": None,
            "usage": "Ikke verificeret",
            "building_type": "Ikke verificeret",
            "construction_year": None,
            "area_m2": None,
            "floors": None,
            "basement": "Ikke verificeret",
            "roof_material": "Ikke verificeret",
            "outer_wall_material": "Ikke verificeret",
            "technical_installations": [],
            "verification_status": "Ikke verificeret"
        },

        "road": {
            "source": "Ikke tilgængeligt i denne version",
            "roadworks": [],
            "traffic_events": [],
            "access_notes": [],
            "verification_status": "Ikke verificeret"
        },

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
            "Kortlink genereret via OpenStreetMap",
            "Vejr/vind forsøgt hentet via Open-Meteo testintegration",
            "BBR, vejdata, brandhaner, gas og el er endnu ikke verificeret"
        ]
    }

    return jsonify(data)


if __name__ == "__main__":
    app.run(debug=True)
