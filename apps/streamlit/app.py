"""Streamlit entry point.

Run with:  uv run streamlit run apps/streamlit/app.py
"""

from __future__ import annotations

import streamlit as st

from pipeline_analytics import __version__
from pipeline_analytics.core.jurisdiction import Service, StudyDefaults, code_basis
from pipeline_analytics.core.units import Q_
from pipeline_analytics.mechanical import (
    BuoyancyInput,
    Coating,
    LocationClass,
    PgdInput,
    SegmentInput,
    SeismicInput,
    Soil,
    SoilType,
    assess_segment,
    pipe_from_library,
)
from pipeline_analytics.pipespec import WtPolicy, default_library

st.set_page_config(page_title="Pipeline Feasibility", layout="wide")
st.title("Cross-Country Pipeline Feasibility")
st.caption(f"pipeline-analytics v{__version__}")

basis_tab, library_tab, mech_tab = st.tabs(["Study basis", "Pipe library", "Mechanical (ALA)"])

# --------------------------------------------------------------------- basis
with basis_tab:
    defaults = StudyDefaults()
    service = Service(st.radio("Service", [s.value for s in Service], horizontal=True))
    basis = code_basis(service, defaults.jurisdiction)

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Code basis (India)")
        st.write("**Regulations:** " + "; ".join(basis.regulations))
        st.write("**Design codes:** " + ", ".join(basis.design_codes))
    with col2:
        st.subheader("Unit converter")
        value = st.number_input("Value", value=1.0)
        units = (
            ["MMSCMD", "MMSCFD", "m**3/h"] if service is Service.GAS else ["MMTPA", "bpd", "t/h"]
        )
        src = st.selectbox("From", units)
        dst = st.selectbox("To", units, index=1)
        q = Q_(value, src)
        if not q.is_compatible_with(dst):
            # Mass <-> volume flow (e.g. MMTPA <-> bpd) needs the liquid density.
            rho = Q_(st.number_input("Density (kg/m³)", value=850.0, min_value=1.0), "kg/m**3")
            q = q / rho if q.is_compatible_with("kg/s") else q * rho
        st.metric(f"{value} {src}", f"{q.to(dst).magnitude:,.4g} {dst}")

# ------------------------------------------------------------------- library
with library_tab:
    lib = default_library()
    st.warning(
        f"{lib.unverified_count()} grade records are not yet verified against licensed "
        "copies of the standards. Use for screening only."
    )

    st.subheader("Steel grades")
    g1, g2 = st.columns(2)
    psl = g1.selectbox("PSL", [2, 1], key="lib_psl")
    min_smys = g2.number_input("Minimum SMYS (MPa)", value=0.0, step=10.0, key="lib_smys")
    st.dataframe(
        [
            {
                "Grade": g.name,
                "ISO": g.iso_name,
                "Conditions": ",".join(g.conditions),
                "SMYS MPa": g.smys,
                "SMYS max": g.smys_max,
                "SMTS MPa": g.smts,
                "SMTS max": g.smts_max,
                "Y/T max": g.yt_max,
            }
            for g in lib.find_grades(standard="API-5L", psl=psl, min_smys=min_smys or None)
        ],
        hide_index=True,
    )

    st.subheader("Pipe sizes (ASME B36.10M)")
    s1, s2 = st.columns(2)
    nps = s1.selectbox("NPS", [s.nps for s in lib.sizes], index=20, key="lib_nps")
    size = lib.size(nps)
    s1.write(f"DN {size.dn} · OD {size.od_mm} mm ({size.od_in:.3f} in)")
    s1.write("Processes: " + ", ".join(p.code for p in lib.processes_for(size.od_mm)))
    s1.dataframe(
        [
            {
                "Designation": "/".join(w.designations),
                "WT in": w.wt_in,
                "WT mm": w.wt_mm,
                "kg/m": round(size.weight_kg_m(w.wt_mm), 2),
            }
            for w in size.wall_thicknesses
        ],
        hide_index=True,
    )
    with s2:
        st.markdown("**Round up a required wall thickness**")
        req = st.number_input("Required WT (mm)", value=10.0, min_value=0.1, key="lib_req")
        policy = WtPolicy(st.radio("Policy", [p.value for p in WtPolicy], key="lib_policy"))
        try:
            wt = lib.next_wall_thickness(size, req, policy)
            st.metric("Orderable WT", f"{wt:g} mm", f"{size.weight_kg_m(wt):.2f} kg/m")
        except ValueError as e:
            st.error(str(e))

# ---------------------------------------------------------------- mechanical
with mech_tab:
    st.caption(
        "Buried-pipe checks per ALA 2001 Guidelines for the Design of Buried Steel Pipe, "
        "with pressure design per ASME B31.4/B31.8 (OISD/PNGRB T4S). Screening only: "
        "see docs/mechanical-ala.md for values to verify."
    )
    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown("**Pipe and service**")
        m_service = Service(st.selectbox("Service", [s.value for s in Service], key="m_service"))
        m_nps = st.selectbox("NPS", [s.nps for s in lib.sizes], index=20, key="m_nps")
        m_wt = st.number_input("Wall thickness (mm)", value=9.53, min_value=1.0, key="m_wt")
        m_grade = st.selectbox(
            "Grade (PSL2)", [g.name for g in lib.find_grades(psl=2)], index=7, key="m_grade"
        )
        m_lc = LocationClass(
            st.selectbox("Location class", [c.value for c in LocationClass], index=1, key="m_lc")
        )
        m_p = st.number_input("Design pressure (barg)", value=92.0, min_value=0.0, key="m_p")
        m_t = st.number_input("Design temperature (°C)", value=50.0, key="m_t")
        m_ti = st.number_input("Installation temperature (°C)", value=25.0, key="m_ti")
    with m2:
        st.markdown("**Burial and soil**")
        m_soil = SoilType(st.selectbox("Soil", [s.value for s in SoilType], key="m_soil"))
        m_gamma = st.number_input("Unit weight (kN/m³)", value=18.0, min_value=10.0, key="m_g")
        m_phi = st.number_input(
            "Friction angle φ (°)",
            value=36.0 if m_soil.granular else 0.0,
            min_value=0.0,
            max_value=45.0,
            key="m_phi",
        )
        m_c = st.number_input(
            "Cohesion c (kPa)", value=0.0 if m_soil.granular else 25.0, min_value=0.0, key="m_c"
        )
        m_cover = st.number_input("Cover to crown (m)", value=1.2, min_value=0.3, key="m_cover")
        m_coat = Coating(st.selectbox("Coating", [c.value for c in Coating], index=5, key="m_coat"))
        m_wtab = st.checkbox("Below water table", key="m_wtab")
    with m3:
        st.markdown("**Optional checks**")
        do_buoy = st.checkbox("Buoyancy", key="m_buoy")
        rho_f = st.number_input("Fluid density (kg/m³)", value=1000.0, key="m_rho")
        do_seis = st.checkbox("Seismic wave propagation", key="m_seis")
        pgv = st.number_input("PGV (m/s)", value=0.5, min_value=0.01, key="m_pgv")
        pga = st.number_input("PGA (g)", value=0.3, min_value=0.01, key="m_pga")
        do_pgd = st.checkbox("Longitudinal PGD", key="m_pgdc")
        pgd_d = st.number_input("Ground displacement (m)", value=1.0, min_value=0.01, key="m_pgd")
        pgd_l = st.number_input("Block length (m)", value=100.0, min_value=1.0, key="m_pgdl")

    try:
        m_pipe = pipe_from_library(m_nps, m_wt, m_grade)
        m_soil_obj = Soil(
            soil_type=m_soil,
            unit_weight_n_m3=m_gamma * 1e3,
            friction_angle_deg=m_phi,
            cohesion_pa=m_c * 1e3,
            below_water_table=m_wtab,
        )
        result = assess_segment(
            m_pipe,
            SegmentInput(
                service=m_service,
                location_class=m_lc,
                design_pressure_pa=m_p * 1e5,
                design_temp_c=m_t,
                install_temp_c=m_ti,
                cover_m=m_cover,
                soil=m_soil_obj,
                coating=m_coat,
                water_above_crown_m=m_cover if m_wtab else 0.0,
                buoyancy=BuoyancyInput(fluid_density_kg_m3=rho_f) if do_buoy else None,
                seismic=SeismicInput(pgv_m_s=pgv, pga_m_s2=pga * 9.80665) if do_seis else None,
                pgd=PgdInput(ground_displacement_m=pgd_d, block_length_m=pgd_l) if do_pgd else None,
            ),
        )
    except ValueError as e:
        st.error(str(e))
    else:
        gov = result.governing
        st.metric(
            "Result",
            "PASS" if result.passed else "FAIL",
            f"governing: {gov.name} (UR {gov.utilisation:.2f})",
            delta_color="normal" if result.passed else "inverse",
        )
        st.dataframe(
            [
                {
                    "Check": c.name,
                    "Demand": f"{c.demand:.4g}",
                    "Capacity": f"{c.capacity:.4g}",
                    "Unit": c.unit,
                    "UR": round(c.utilisation, 3),
                    "Status": "PASS" if c.passed else "FAIL",
                    "Reference": c.ref,
                }
                for c in result.checks
            ],
            hide_index=True,
        )
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Required WT (mm)", f"{result.pressure.required_wt_m * 1e3:.2f}")
        k2.metric("MAOP (barg)", f"{result.pressure.maop_pa / 1e5:.1f}")
        anchor = result.thermal.virtual_anchor_length_m
        k3.metric("Virtual anchor length (m)", f"{anchor:.0f}" if anchor else "-")
        k4.metric("Ring deflection Δy/D", f"{result.vertical.deflection_ratio:.2%}")
        if result.buoyancy is not None:
            t_cwc = result.buoyancy.required_concrete_thickness_m
            st.info(f"Concrete weight coating needed: {t_cwc * 1e3:.0f} mm")
        st.markdown("**ALA soil springs (Appendix B)**")
        st.dataframe(
            [
                {
                    "Direction": sp.direction,
                    "Max force (kN/m)": round(sp.max_force_n_per_m / 1e3, 2),
                    "Yield displacement (mm)": round(sp.yield_displacement_m * 1e3, 1),
                }
                for sp in (
                    result.springs.axial,
                    result.springs.lateral,
                    result.springs.uplift,
                    result.springs.bearing,
                )
            ],
            hide_index=True,
        )
