"""Production entrypoint with full DanskAdresseAPI operational enrichment."""

import asbestos_guard
import bbr_danskadresse
import danskadresse_full
import wsgi as runtime


app = runtime.app
legacy = runtime.legacy


# wsgi._provider_bbr_lookup resolves bbr_danskadresse.get_building at request
# time. Wrap the full provider so every lookup also performs the property-wide
# asbestos check across all registered BBR buildings on the access address.
_original_full_get_building = danskadresse_full.get_building


def get_building_with_asbestos(address_data, app_module):
    building = _original_full_get_building(address_data, app_module)
    access_address_id = (
        (address_data or {}).get("access_address_id")
        or (building or {}).get("access_address_id")
    )
    return asbestos_guard.enrich_building(building, access_address_id)


bbr_danskadresse.get_building = get_building_with_asbestos


# Always surface the asbestos control result. The older enrichment only emitted
# a line when BBR said yes; operationally we also need to know that the check was
# performed when BBR says no, unknown, or does not return the field.
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
