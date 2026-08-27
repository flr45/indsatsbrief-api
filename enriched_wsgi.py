"""Production entrypoint with full DanskAdresseAPI operational enrichment."""

import asbestos_guard
import bbr_danskadresse
import danskadresse_full
import datafordeler_asbestos
import property_inventory
import wsgi as runtime


app = runtime.app
legacy = runtime.legacy


# wsgi._provider_bbr_lookup resolves bbr_danskadresse.get_building at request
# time. Wrap the full provider so every lookup also performs the property-wide
# asbestos check, then uses the modern Datafordeler GraphQL field as a targeted
# fallback when DanskAdresseAPI did not return an unambiguous registration.
_original_full_get_building = danskadresse_full.get_building


def get_building_with_asbestos(address_data, app_module):
    building = _original_full_get_building(address_data, app_module)
    access_address_id = (
        (address_data or {}).get("access_address_id")
        or (building or {}).get("access_address_id")
    )
    building = asbestos_guard.enrich_building(building, access_address_id)
    building = datafordeler_asbestos.enrich_if_needed(building, access_address_id)
    # asbestos_guard and the Datafordeler fallback cache independently, so the
    # operational building inventory can remain unchanged.
    return property_inventory.enrich_building(building, access_address_id)


bbr_danskadresse.get_building = get_building_with_asbestos


# Always surface the asbestos control result and all registered BBR buildings.
# The older enrichment only emitted an asbestos line when BBR said yes;
# operationally we also need no/unknown/not-returned to be explicit.
_original_operational_report_additions = danskadresse_full.operational_report_additions


def operational_report_additions_with_asbestos(building):
    additions = _original_operational_report_additions(building)

    risk_lines = [
        line
        for line in list(additions.get("risk_context_lines") or [])
        if "asbest" not in str(line).lower()
    ]
    for line in asbestos_guard.report_lines(building):
        if line not in risk_lines:
            risk_lines.append(line)

    fallback = (building or {}).get("asbestos_fallback") or {}
    if fallback.get("status") == "yes" and fallback.get("location_text"):
        detail = f"Asbesttype: {fallback['location_text']}"
        if detail not in risk_lines:
            risk_lines.append(detail)

    additions["risk_context_lines"] = risk_lines

    details = list(additions.get("building_details") or [])
    for line in property_inventory.report_lines(building):
        if line not in details:
            details.append(line)
    additions["building_details"] = details

    return additions


danskadresse_full.operational_report_additions = operational_report_additions_with_asbestos


_original_build_deterministic_report_structured = legacy.build_deterministic_report_structured


def enriched_build_deterministic_report_structured(raw_incident_data, report_mode="short"):
    report = _original_build_deterministic_report_structured(raw_incident_data, report_mode)
    return danskadresse_full.augment_report(report, raw_incident_data, legacy)


legacy.build_deterministic_report_structured = enriched_build_deterministic_report_structured


# Full/AI reports use this function as a deterministic safety net. Keep the
# traditional building sections, while also exposing the new operational facts
# to any code path that consumes the fallback structure.
_original_build_deterministic_building_sections = legacy.build_deterministic_building_sections


def enriched_build_deterministic_building_sections(raw_incident_data):
    sections = _original_build_deterministic_building_sections(raw_incident_data)
    additions = danskadresse_full.operational_report_additions(
        (raw_incident_data or {}).get("building") or {}
    )
    for field, lines in additions.items():
        current = list(sections.get(field) or [])
        for line in lines:
            if line not in current:
                current.append(line)
        sections[field] = current
    return legacy.clean_report_sections(sections)


legacy.build_deterministic_building_sections = enriched_build_deterministic_building_sections


# Versioned front-end assets. Smoke v3 remains the calculation engine,
# Operational Intelligence v4 is the main UX layer, and Field Observations v1
# can temporarily override wind inputs for the smoke model in the browser.
FRONTEND_ASSET_VERSION = "20260827-smoke-v30-ux4-field1"
SMOKE_MAP_JS_TAG = (
    f'<script defer src="/static/smoke-v3.js?v={FRONTEND_ASSET_VERSION}"></script>'
)
SMOKE_MAP_CSS_TAG = (
    f'<link rel="stylesheet" href="/static/smoke-v3.css?v={FRONTEND_ASSET_VERSION}">'
)
OPERATIONAL_UI_CSS_TAG = (
    f'<link rel="stylesheet" href="/static/operational-ui.css?v={FRONTEND_ASSET_VERSION}">'
)
OPERATIONAL_UI_JS_TAG = (
    f'<script defer src="/static/operational-ui.js?v={FRONTEND_ASSET_VERSION}"></script>'
)
OPERATIONAL_INTELLIGENCE_CSS_TAG = (
    f'<link rel="stylesheet" href="/static/operational-intelligence-v4.css?v={FRONTEND_ASSET_VERSION}">'
)
OPERATIONAL_INTELLIGENCE_JS_TAG = (
    f'<script defer src="/static/operational-intelligence-v4.js?v={FRONTEND_ASSET_VERSION}"></script>'
)
FIELD_OBSERVATIONS_CSS_TAG = (
    f'<link rel="stylesheet" href="/static/field-observations-v1.css?v={FRONTEND_ASSET_VERSION}">'
)
FIELD_OBSERVATIONS_JS_TAG = (
    f'<script defer src="/static/field-observations-v1.js?v={FRONTEND_ASSET_VERSION}"></script>'
)


@app.after_request
def inject_operational_frontend(response):
    if (
        response.mimetype == "text/html"
        and not response.direct_passthrough
        and response.status_code < 500
    ):
        try:
            page_html = response.get_data(as_text=True)

            styles = []
            if OPERATIONAL_UI_CSS_TAG not in page_html:
                styles.append(OPERATIONAL_UI_CSS_TAG)
            if OPERATIONAL_INTELLIGENCE_CSS_TAG not in page_html:
                styles.append(OPERATIONAL_INTELLIGENCE_CSS_TAG)
            if FIELD_OBSERVATIONS_CSS_TAG not in page_html:
                styles.append(FIELD_OBSERVATIONS_CSS_TAG)
            if "map-frame" in page_html and SMOKE_MAP_CSS_TAG not in page_html:
                styles.append(SMOKE_MAP_CSS_TAG)
            if styles and "</head>" in page_html:
                page_html = page_html.replace(
                    "</head>",
                    "".join(styles) + "</head>",
                    1,
                )

            scripts = []
            if "map-frame" in page_html and SMOKE_MAP_JS_TAG not in page_html:
                scripts.append(SMOKE_MAP_JS_TAG)
            if OPERATIONAL_UI_JS_TAG not in page_html:
                scripts.append(OPERATIONAL_UI_JS_TAG)
            if OPERATIONAL_INTELLIGENCE_JS_TAG not in page_html:
                scripts.append(OPERATIONAL_INTELLIGENCE_JS_TAG)
            if FIELD_OBSERVATIONS_JS_TAG not in page_html:
                scripts.append(FIELD_OBSERVATIONS_JS_TAG)

            if scripts and "</body>" in page_html:
                page_html = page_html.replace(
                    "</body>",
                    "".join(scripts) + "</body>",
                    1,
                )

            response.set_data(page_html)
            response.headers.pop("Content-Length", None)
        except Exception:
            app.logger.exception("Kunne ikke injicere operationelle frontend-assets")
    return response
