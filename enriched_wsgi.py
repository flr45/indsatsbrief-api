"""Production entrypoint with full DanskAdresseAPI operational enrichment."""

import bbr_danskadresse
import danskadresse_full
import wsgi as runtime


app = runtime.app
legacy = runtime.legacy


# wsgi._provider_bbr_lookup resolves bbr_danskadresse.get_building at request
# time, so replacing this one function upgrades the provider without touching
# the proven fallback/routing layer.
bbr_danskadresse.get_building = danskadresse_full.get_building


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
