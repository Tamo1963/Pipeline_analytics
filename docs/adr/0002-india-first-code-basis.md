# 0002: India first; gas and liquids

**Status:** Accepted (2026-11-04)

## Context
The suite targets cross-country pipelines. Codes and regulations differ by jurisdiction.

## Decision
* First jurisdiction: **India**. Default currency INR; SI units with Indian industry units (MMSCMD, MMTPA, kgf/cm²) supported.
* Services in scope: **natural gas** and **liquid hydrocarbons** (crude, products).
  * Gas: PNGRB T4S Regulations for Natural Gas Pipelines, OISD-STD-226, ASME B31.8.
  * Liquids: PNGRB T4S Regulations for Petroleum and Petroleum Products Pipelines, OISD-STD-141, ASME B31.4.
* The code basis is chosen per study (`core.jurisdiction.code_basis`), so other jurisdictions can be added as more entries plus rule modules.
* Gas volumes default to ISO 13443 reference conditions (15 °C, 101.325 kPa), overridable per project.

## Consequences
* Phase 1 adds Indian pipe standards (IS 3589, IS 1239) alongside API 5L / ISO 3183 and ASME B36.10M.
* Water, slurry, H₂ and CO₂ are out of scope for now.
* Code values (design factors, location-class rules) must be verified against current licensed editions before release.
