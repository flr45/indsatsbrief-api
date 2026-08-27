"""Production entrypoint for IndsatsBrief.

The legacy application still lives in app.py. This thin runtime layer lets us
improve deployment, security, provider selection and presentation without a
large risky rewrite of the working application.
"""

import importlib
import os
import sys
from functools import wraps

import psycopg2
from flask import jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix


STARTUP_LOCK_ID = 731240817


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return bool(default)
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _postgres_dsn():
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if dsn.startswith("postgresql+psycopg2://"):
        return "postgresql://" + dsn.split("postgresql+psycopg2://", 1)[1]
    return dsn


def _import_legacy_app_with_startup_lock():
    """Serialize app import across Gunicorn workers.

    app.py performs database initialization and station seeding at import time.
    With multiple workers two processes can otherwise observe an empty station
    table simultaneously and race on unique station IDs.
    """
    connection = None
    cursor = None
    dsn = _postgres_dsn()

    if dsn.startswith(("postgres://", "postgresql://")):
        try:
            connection = psycopg2.connect(dsn, connect_timeout=6)
            connection.autocommit = True
            cursor = connection.cursor()
            cursor.execute("SELECT pg_advisory_lock(%s)", (STARTUP_LOCK_ID,))
        except Exception as error:
            print(
                f"[runtime] startup lock unavailable; continuing without lock: {error}",
                file=sys.stderr,
                flush=True,
            )
            try:
                if cursor:
                    cursor.close()
                if connection:
                    connection.close()
            except Exception:
                pass
            cursor = None
            connection = None

    try:
        return importlib.import_module("app")
    finally:
        if cursor is not None:
            try:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (STARTUP_LOCK_ID,))
            except Exception as error:
                print(
                    f"[runtime] startup lock release failed: {error}",
                    file=sys.stderr,
                    flush=True,
                )
            try:
                cursor.close()
            except Exception:
                pass
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


legacy = _import_legacy_app_with_startup_lock()
app = legacy.app


# Respect Cloudflare/Nginx forwarded scheme + host so redirects and secure-cookie
# decisions reflect the public HTTPS request instead of the internal HTTP hop.
if env_bool("TRUST_PROXY_HEADERS", True):
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_port=1,
    )

base_url = (
    os.getenv("APP_BASE_URL")
    or os.getenv("INDSATSBRIEF_APP_BASE_URL")
    or getattr(legacy, "APP_BASE_URL", "")
    or ""
).strip()

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = env_bool(
    "SESSION_COOKIE_SECURE",
    base_url.lower().startswith("https://"),
)
app.config["MAX_CONTENT_LENGTH"] = min(
    int(app.config.get("MAX_CONTENT_LENGTH") or 26 * 1024 * 1024),
    26 * 1024 * 1024,
)


# ---------------------------------------------------------------------------
# BBR provider: DanskAdresseAPI first, existing Datafordeler implementation as
# an automatic safety fallback while the new integration is rolled out.
# ---------------------------------------------------------------------------
import bbr_danskadresse

_original_get_bbr_with_fallback = legacy.get_bbr_with_fallback


def _provider_bbr_lookup(address_data):
    primary = bbr_danskadresse.get_building(address_data, legacy)
    if legacy.bbr_building_has_real_data(primary):
        primary["provider_fallback"] = {
            "primary": "DanskAdresseAPI",
            "fallback_used": False,
        }
        return primary

    if env_bool("DANSKADRESSE_FALLBACK_TO_DATAFORDELER", True):
        fallback = _original_get_bbr_with_fallback(address_data)
        fallback["provider_fallback"] = {
            "primary": "DanskAdresseAPI",
            "fallback": "Datafordeleren",
            "fallback_used": True,
            "reason": primary.get("danskadresse_error") or primary.get("verification_status"),
        }
        return fallback

    return primary


if bbr_danskadresse.configured():
    legacy.get_bbr_with_fallback = _provider_bbr_lookup


# Expose a few high-value DanskAdresse fields in the deterministic short report
# without changing the existing report contract or AI prompts.
_original_build_short_report_building = legacy.build_short_report_building


def _clean_technical_installations(items):
    cleaned = []
    for item in items or []:
        if not isinstance(item, dict):
            continue

        normalized = {}
        field_aliases = {
            "type": ("anlaeg_type_tekst", "anlaegstype_tekst", "type_tekst", "type"),
            "established_year": ("etableringsaar",),
            "manufactured_year": ("fabrikationsaar",),
            "output": ("ydeevne", "ydelse"),
            "tank_size_l": ("tank_storrelse_liter", "tankstoerrelse"),
            "placement": ("placering",),
            "decommissioned": ("sloejfet",),
            "decommissioned_date": ("sloejfet_dato",),
        }
        for target, aliases in field_aliases.items():
            for source in aliases:
                value = item.get(source)
                if value is not None and value != "":
                    normalized[target] = value
                    break

        if normalized:
            cleaned.append(normalized)

    return cleaned[:12]


def improved_short_report_building(building_data):
    result = _original_build_short_report_building(building_data)
    if not isinstance(result, dict):
        result = {}

    if building_data:
        energy_label = building_data.get("energy_label")
        if legacy.is_positive_report_value(energy_label):
            result["energy_label"] = energy_label

        energy_valid_until = building_data.get("energy_label_valid_until")
        if legacy.is_positive_report_value(energy_valid_until):
            result["energy_label_valid_until"] = energy_valid_until

        installations = _clean_technical_installations(
            building_data.get("technical_installations")
        )
        if installations:
            result["technical_installations"] = installations

        entrances = building_data.get("bbr_entrances") or []
        if any(
            isinstance(entrance, dict) and entrance.get("elevator") is True
            for entrance in entrances
        ):
            result["elevator_registered"] = True

    return legacy.prune_positive_report_data(result)


legacy.build_short_report_building = improved_short_report_building


# ---------------------------------------------------------------------------
# Health endpoint and access hardening for diagnostic endpoints.
# ---------------------------------------------------------------------------
if not any(rule.rule == "/health" for rule in app.url_map.iter_rules()):

    @app.get("/health")
    def runtime_health():
        return jsonify(
            {
                "ok": True,
                "service": "indsatsbrief",
                "bbr_provider": (
                    "danskadresse"
                    if bbr_danskadresse.configured()
                    else "datafordeler"
                ),
            }
        )


ADMIN_ONLY_PATHS = {
    "/test-bbr",
    "/test-bbr-graphql-address",
    "/test-hydrants",
    "/osm-risk-check",
    "/aerial-check",
    "/aerial-image",
    "/aerial-image.jpg",
}
LOGIN_ONLY_PATHS = {"/hazmat"}


def _admin_protect(view_func):
    if getattr(view_func, "_runtime_admin_protected", False):
        return view_func
    protected = legacy.admin_required(view_func)
    protected._runtime_admin_protected = True
    return protected


def _login_protect(view_func):
    if getattr(view_func, "_runtime_login_protected", False):
        return view_func
    protected = legacy.login_required(api=True)(view_func)
    protected._runtime_login_protected = True
    return protected


for rule in list(app.url_map.iter_rules()):
    view = app.view_functions.get(rule.endpoint)
    if not view:
        continue
    if rule.rule in ADMIN_ONLY_PATHS:
        app.view_functions[rule.endpoint] = _admin_protect(view)
    elif rule.rule in LOGIN_ONLY_PATHS:
        app.view_functions[rule.endpoint] = _login_protect(view)


# ---------------------------------------------------------------------------
# Response policy + progressive visual refresh.
# The stylesheet is injected last, so it can modernize the inline legacy pages
# without replacing or risking their working JavaScript and forms.
# ---------------------------------------------------------------------------
MODERN_CSS_TAG = '<link rel="stylesheet" href="/static/modern.css?v=20260827">'
MODERN_JS_TAG = '<script defer src="/static/modern.js?v=20260827"></script>'


@app.after_request
def runtime_response_policy(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )

    if request.is_secure or base_url.lower().startswith("https://"):
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )

    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=86400"
    elif request.path.startswith(
        (
            "/brief",
            "/admin",
            "/knowledge",
            "/api/",
            "/incident-brief",
            "/analyze-brief",
            "/full-brief",
            "/brief-followup",
            "/nearest-resource",
            "/assistance-stations",
            "/hazmat",
        )
    ):
        response.headers["Cache-Control"] = "no-store"

    if (
        response.mimetype == "text/html"
        and not response.direct_passthrough
        and response.status_code < 500
    ):
        try:
            html = response.get_data(as_text=True)
            changed = False
            if MODERN_CSS_TAG not in html and "</head>" in html:
                html = html.replace("</head>", f"{MODERN_CSS_TAG}</head>", 1)
                changed = True
            if MODERN_JS_TAG not in html and "</body>" in html:
                html = html.replace("</body>", f"{MODERN_JS_TAG}</body>", 1)
                changed = True
            if changed:
                response.set_data(html)
                response.headers.pop("Content-Length", None)
        except Exception as error:
            app.logger.debug("Modern UI injection skipped: %s", error)

    return response
