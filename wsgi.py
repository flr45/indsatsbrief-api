"""Production entrypoint for IndsatsBrief.

The legacy application still lives in app.py. This thin runtime layer lets us
improve deployment, security, provider selection, performance and presentation
without a large risky rewrite of the working application.
"""

from copy import deepcopy
from functools import wraps
import importlib
import os
import sys
import threading
import time

import psycopg2
from flask import jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix


STARTUP_LOCK_ID = 731240817


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return bool(default)
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default, minimum=0, maximum=None):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


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
    env_int(
        "MAX_UPLOAD_BYTES",
        int(app.config.get("MAX_CONTENT_LENGTH") or 26 * 1024 * 1024),
        minimum=1024 * 1024,
    ),
    26 * 1024 * 1024,
)


# ---------------------------------------------------------------------------
# Small, bounded per-worker caches for public external data.
# We deliberately do NOT cache report responses, user data, OpenAI answers or
# admin content. TTL values reflect how quickly each public source can change.
# ---------------------------------------------------------------------------
def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted(_freeze(item) for item in value))
    try:
        hash(value)
        return value
    except TypeError:
        return repr(value)


def ttl_memoize(ttl_seconds, max_entries=256, cacheable=None):
    ttl_seconds = max(0, int(ttl_seconds))
    max_entries = max(1, int(max_entries))

    def decorator(func):
        cache = {}
        lock = threading.RLock()

        @wraps(func)
        def wrapper(*args, **kwargs):
            if ttl_seconds <= 0:
                return func(*args, **kwargs)

            key = (_freeze(args), _freeze(kwargs))
            now = time.monotonic()

            with lock:
                cached = cache.get(key)
                if cached and now - cached["stored_at"] < ttl_seconds:
                    return deepcopy(cached["value"])
                if cached:
                    cache.pop(key, None)

            value = func(*args, **kwargs)
            should_cache = cacheable(value) if cacheable else True
            if not should_cache:
                return value

            with lock:
                if len(cache) >= max_entries:
                    expired = [
                        cache_key
                        for cache_key, item in cache.items()
                        if now - item["stored_at"] >= ttl_seconds
                    ]
                    for cache_key in expired:
                        cache.pop(cache_key, None)

                while len(cache) >= max_entries:
                    oldest = min(cache, key=lambda cache_key: cache[cache_key]["stored_at"])
                    cache.pop(oldest, None)

                cache[key] = {"stored_at": now, "value": deepcopy(value)}

            return value

        wrapper._runtime_ttl_seconds = ttl_seconds
        return wrapper

    return decorator


def _dict_without_error(value):
    return isinstance(value, dict) and not value.get("error")


def _working_hydrant_result(value):
    return isinstance(value, dict) and bool(value.get("working_overpass_url"))


def _working_osm_result(value):
    return isinstance(value, dict) and value.get("ok") is True


def _install_public_data_caches():
    cache_specs = [
        (
            "lookup_address",
            env_int("ADDRESS_CACHE_TTL_SECONDS", 3600, maximum=86400),
            512,
            _dict_without_error,
        ),
        (
            "get_weather_data",
            env_int("WEATHER_CACHE_TTL_SECONDS", 300, maximum=1800),
            256,
            _dict_without_error,
        ),
        (
            "get_possible_hydrants_from_osm",
            env_int("HYDRANT_CACHE_TTL_SECONDS", 900, maximum=86400),
            256,
            _working_hydrant_result,
        ),
        (
            "get_osm_risk_check",
            env_int("OSM_RISK_CACHE_TTL_SECONDS", 900, maximum=86400),
            256,
            _working_osm_result,
        ),
    ]

    for function_name, ttl_seconds, max_entries, cacheable in cache_specs:
        original = getattr(legacy, function_name, None)
        if not callable(original) or getattr(original, "_runtime_ttl_seconds", None) is not None:
            continue
        setattr(
            legacy,
            function_name,
            ttl_memoize(ttl_seconds, max_entries=max_entries, cacheable=cacheable)(original),
        )


_install_public_data_caches()


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
def _database_health():
    database = getattr(legacy, "db", None)
    if not database:
        return True, "not_configured"
    try:
        with database.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        return True, "ok"
    except Exception as error:
        app.logger.warning("Health check database error: %s", error)
        return False, "unavailable"


if not any(rule.rule == "/health" for rule in app.url_map.iter_rules()):

    @app.get("/health")
    def runtime_health():
        database_ok, database_status = _database_health()
        payload = {
            "ok": database_ok,
            "service": "indsatsbrief",
            "database": database_status,
            "bbr_provider": (
                "danskadresse"
                if bbr_danskadresse.configured()
                else "datafordeler"
            ),
        }
        return jsonify(payload), 200 if database_ok else 503


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
