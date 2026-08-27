"""Production entrypoint with full DanskAdresseAPI operational enrichment."""

import asbestos_guard
import bbr_danskadresse
import danskadresse_full
import property_inventory
import wsgi as runtime


app = runtime.app
legacy = runtime.legacy


# wsgi._provider_bbr_lookup resolves bbr_danskadresse.get_building at request
# time. Wrap the full provider so every lookup also performs the property-wide
# asbestos check and captures all registered BBR buildings on the access address.
_original_full_get_building = danskadresse_full.get_building


def get_building_with_asbestos(address_data, app_module):
    building = _original_full_get_building(address_data, app_module)
    access_address_id = (
        (address_data or {}).get("access_address_id")
        or (building or {}).get("access_address_id")
    )
    building = asbestos_guard.enrich_building(building, access_address_id)
    # This reuses asbestos_guard's 24h cache, so the building inventory normally
    # does not create a second external API request.
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


# Versioned front-end assets. Changing this value is intentional: the previous
# smoke-map v1 and v2 used the same URL, which allowed browsers to keep a cached
# v1 script even after the v2 container had been deployed.
FRONTEND_ASSET_VERSION = "20260827-smoke-v21-ux2"
SMOKE_MAP_JS_TAG = (
    f'<script defer src="/static/smoke-map.js?v={FRONTEND_ASSET_VERSION}"></script>'
)
OPERATIONAL_UI_CSS_TAG = (
    f'<link rel="stylesheet" href="/static/operational-ui.css?v={FRONTEND_ASSET_VERSION}">'
)
OPERATIONAL_UI_JS_TAG = (
    f'<script defer src="/static/operational-ui.js?v={FRONTEND_ASSET_VERSION}"></script>'
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

            if OPERATIONAL_UI_CSS_TAG not in page_html and "</head>" in page_html:
                page_html = page_html.replace(
                    "</head>",
                    f"{OPERATIONAL_UI_CSS_TAG}</head>",
                    1,
                )

            scripts = []
            if "map-frame" in page_html and SMOKE_MAP_JS_TAG not in page_html:
                scripts.append(SMOKE_MAP_JS_TAG)
            if OPERATIONAL_UI_JS_TAG not in page_html:
                scripts.append(OPERATIONAL_UI_JS_TAG)

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
