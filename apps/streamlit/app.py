"""Streamlit entry point.

Run with:  uv run streamlit run apps/streamlit/app.py
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

from pipeline_analytics import __version__
from pipeline_analytics.codes.class_location import (
    Building,
    class_summary,
    classify_route,
    maop_along_route,
)
from pipeline_analytics.core.jurisdiction import Service, StudyDefaults, code_basis
from pipeline_analytics.core.units import Q_
from pipeline_analytics.cost import crore, placeholder_rates
from pipeline_analytics.economics import (
    Concession,
    OptionInput,
    OptionResult,
    best_option,
    economic_diameter_curve,
    evaluate_option,
    option_tornado,
    ramp_profile,
)
from pipeline_analytics.hydraulics import (
    GasStationInput,
    HydraulicsError,
    Liquid,
    Profile,
    PumpStationInput,
    gas_hydraulics,
    liquid_hydraulics,
)
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
from pipeline_analytics.mechanical.pgd import (
    fault_movement,
    longitudinal_pgd,
    settlement,
    transverse_pgd,
)
from pipeline_analytics.mechanical.seismic import PerformanceLevel
from pipeline_analytics.pipespec import WtPolicy, default_library
from pipeline_analytics.routing import (
    CostSurfaceParams,
    alternative_corridors,
    build_cost_surface,
    extract_route,
    opex_penalties,
    smooth_path_polyline,
    to_geojson,
    to_kml,
)
from pipeline_analytics.routing import sample as route_sample

st.set_page_config(page_title="Pipeline Feasibility", layout="wide")
st.title("Cross-Country Pipeline Feasibility")
st.caption(f"pipeline-analytics v{__version__}")

bot_tab, basis_tab, library_tab, mech_tab, hyd_tab, econ_tab, route_tab, proj_tab = st.tabs(
    [
        "Feasibility bot",
        "Study basis",
        "Pipe library",
        "Mechanical (ALA)",
        "Class location & hydraulics",
        "Economics",
        "Route",
        "Projects",
    ]
)

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
@st.cache_data(show_spinner="Running the pipe-soil FE model...")
def _fe_run(pipe_json: str, springs_json: str, mv_json: str, grade: str, p_barg: float,
            level: str) -> Any:  # fmt: skip
    from pipeline_analytics.mechanical import PipeSection
    from pipeline_analytics.mechanical.pgd import GroundMovement, analyse_pgd
    from pipeline_analytics.mechanical.soil import SoilSprings

    return analyse_pgd(
        PipeSection.model_validate_json(pipe_json),
        SoilSprings.model_validate_json(springs_json),
        GroundMovement.model_validate_json(mv_json),
        grade=grade,
        pressure=p_barg * 1e5,
        level=PerformanceLevel(level),
    )


@st.cache_data(show_spinner="Sweeping crossing angles...")
def _fe_sweep(pipe_json: str, springs_json: str, slip: float, dip: float, rake: float,
              grade: str, p_barg: float, level: str) -> Any:  # fmt: skip
    from pipeline_analytics.mechanical import PipeSection
    from pipeline_analytics.mechanical.pgd import crossing_angle_sweep
    from pipeline_analytics.mechanical.soil import SoilSprings

    return crossing_angle_sweep(
        PipeSection.model_validate_json(pipe_json),
        SoilSprings.model_validate_json(springs_json),
        slip,
        (15, 30, 45, 60, 75, 90, 105, 120),
        dip_deg=dip,
        rake_deg=rake,
        grade=grade,
        pressure=p_barg * 1e5,
        level=PerformanceLevel(level),
    )


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

        # ---------------------------------------------- FE: faults, PGD, settlement
        with st.expander("Ground movement: pipe-soil FE model (fault, PGD, settlement)"):
            st.caption(
                "Nonlinear beam on the ALA springs above (large displacement, Ramberg-Osgood "
                "steel), strains checked against ALA limits. Verified against closed forms and "
                "the Karamitros et al. (2007) fault benchmark: see docs/mechanical-fe.md."
            )
            g1, g2, g3 = st.columns(3)
            kind = g1.selectbox(
                "Scenario",
                ["Fault crossing", "Transverse PGD", "Settlement trough",
                 "Differential settlement", "Longitudinal PGD"],
                key="f_kind",
            )  # fmt: skip
            level = PerformanceLevel(
                g1.selectbox(
                    "Performance level", [lv.value for lv in PerformanceLevel], key="f_level"
                )
            )
            if kind == "Fault crossing":
                f_slip = g2.number_input("Fault slip (m)", value=1.0, min_value=0.01, key="f_slip")
                f_beta = g2.number_input("Crossing angle to strike (°)", value=60.0,
                                         min_value=5.0, max_value=175.0, key="f_beta")  # fmt: skip
                f_dip = g3.number_input("Dip (°)", value=90.0, min_value=10.0, max_value=90.0,
                                        key="f_dip")  # fmt: skip
                f_rake = g3.number_input("Rake (°): 0 strike-slip, 90 reverse, -90 normal",
                                         value=0.0, min_value=-180.0, max_value=180.0,
                                         key="f_rake")  # fmt: skip
                movement = fault_movement(f_slip, f_beta, dip_deg=f_dip, rake_deg=f_rake)
            elif kind == "Transverse PGD":
                f_d = g2.number_input("Displacement (m)", value=1.0, min_value=0.01, key="f_td")
                f_w = g2.number_input("Zone width (m)", value=50.0, min_value=2.0, key="f_tw")
                f_pat = g3.radio("Profile", ["cosine", "block"], key="f_tp")
                movement = transverse_pgd(f_d, f_w, f_pat)  # type: ignore[arg-type]
            elif kind == "Settlement trough":
                f_d = g2.number_input("Settlement (m)", value=0.3, min_value=0.01, key="f_sd")
                f_w = g2.number_input("Trough width (m)", value=40.0, min_value=2.0, key="f_sw")
                movement = settlement(f_d, f_w)
            elif kind == "Differential settlement":
                f_d = g2.number_input("Step (m)", value=0.2, min_value=0.01, key="f_ds")
                movement = settlement(f_d, pattern="step")
            else:
                f_d = g2.number_input("Displacement (m)", value=0.5, min_value=0.01, key="f_ld")
                f_w = g2.number_input("Block length (m)", value=100.0, min_value=2.0, key="f_lw")
                movement = longitudinal_pgd(f_d, f_w)
            run_fe = st.button("Run FE analysis", key="f_run")
            fe_key = (m_pipe.model_dump_json(), result.springs.model_dump_json(),
                      movement.model_dump_json(), m_grade, m_p, level.value)  # fmt: skip
            if run_fe:
                st.session_state.f_result = (fe_key, _fe_run(*fe_key))
            fe_res = st.session_state.get("f_result")
            if fe_res and fe_res[0] == fe_key:
                a = fe_res[1]
                h1, h2, h3, h4 = st.columns(4)
                h1.metric("Result", "PASS" if a.passed else "FAIL",
                          f"strain UR {a.utilisation:.2f}",
                          delta_color="normal" if a.passed else "inverse")  # fmt: skip
                h2.metric("Max tensile strain", f"{a.fe.max_tensile_strain:.3%}",
                          f"limit {a.tensile_limit:.2%}", delta_color="off")  # fmt: skip
                h3.metric("Max compressive strain", f"{a.fe.max_compressive_strain:.3%}",
                          f"limit {a.compressive_limit:.2%}", delta_color="off")  # fmt: skip
                lim = a.movement_at_limit_m
                h4.metric("Movement at first limit", "not reached" if lim is None
                          else f"{lim:.2f} m")  # fmt: skip
                for n in a.notes:
                    st.warning(n)
                xs = np.array(a.fe.x_elem_m)
                zoom = np.abs(xs) <= max(60.0, movement.width_m)
                st.line_chart(pd.DataFrame({
                    "x (m)": xs[zoom],
                    "tensile strain %": np.array(a.fe.tensile_strain)[zoom] * 100,
                    "compressive strain %": np.array(a.fe.compressive_strain)[zoom] * 100,
                }).set_index("x (m)"))  # fmt: skip
                xn = np.array(a.fe.x_m)
                zn = np.abs(xn) <= max(60.0, movement.width_m)
                comp = 2 if movement.vertical_m and not movement.transverse_m else 1
                if movement.kind == "block" and movement.axial_m and not movement.transverse_m:
                    comp = 0
                pipe_disp = [a.fe.u_m, a.fe.v_m, a.fe.w_m][comp]
                st.line_chart(pd.DataFrame({
                    "x (m)": xn[zn],
                    "pipe (m)": np.array(pipe_disp)[zn],
                    "ground (m)": np.array(a.fe.ground_m[comp])[zn],
                }).set_index("x (m)"))  # fmt: skip
                st.caption(f"{a.fe.elements} elements, {a.fe.steps} load steps; "
                           f"{a.steel.source or a.steel.model}.")  # fmt: skip
            if kind == "Fault crossing" and st.button("Find the best crossing angle",
                                                      key="f_sweep"):  # fmt: skip
                sw = _fe_sweep(m_pipe.model_dump_json(), result.springs.model_dump_json(),
                               f_slip, f_dip, f_rake, m_grade, m_p, level.value)  # fmt: skip
                st.dataframe([{"Crossing angle (°)": c.crossing_angle_deg,
                               "Tensile %": round(c.max_tensile_strain * 100, 3),
                               "Compressive %": round(c.max_compressive_strain * 100, 3),
                               "Strain UR": round(c.utilisation, 2)} for c in sw.cases],
                             hide_index=True)  # fmt: skip
                st.success(f"Lowest strain utilisation at {sw.best_angle_deg:g}° to the fault "
                           "strike (pipe in tension).")  # fmt: skip

# ------------------------------------------------- class location & hydraulics
with hyd_tab:
    st.caption(
        "Location class per ASME B31.8 840.2 (as adopted by PNGRB T4S / OISD-STD-226), "
        "MAOP envelope from the wall and grade, and pump/compressor station siting. "
        "Screening accuracy: Papay Z-factor, isothermal gas flow."
    )
    h1, h2 = st.columns(2)
    with h1:
        st.markdown("**Route elevation profile**")
        prof_df = st.data_editor(
            pd.DataFrame(
                {
                    "chainage_km": [0, 50, 100, 150, 200, 250, 300],
                    "elevation_m": [50, 120, 300, 250, 180, 90, 60],
                }
            ),
            num_rows="dynamic",
            key="h_profile",
        )
        st.markdown("**Buildings near the route** (dwelling units within 200 m)")
        bld_df = st.data_editor(
            pd.DataFrame(
                {
                    "chainage_km": [40.0, 120.0],
                    "offset_m": [80.0, 30.0],
                    "dwelling_units": [15, 60],
                    "storeys": [1, 2],
                }
            ),
            num_rows="dynamic",
            key="h_buildings",
        )
    with h2:
        st.markdown("**Pipe and flow**")
        h_service = Service(st.selectbox("Service", [s.value for s in Service], key="h_service"))
        h_nps = st.selectbox("NPS", [s.nps for s in lib.sizes], index=20, key="h_nps")
        h_wt = st.number_input("Wall thickness (mm)", value=9.53, min_value=1.0, key="h_wt")
        h_grade = st.selectbox(
            "Grade (PSL2)", [g.name for g in lib.find_grades(psl=2)], index=7, key="h_grade"
        )
        if h_service is Service.GAS:
            h_flow = st.number_input("Flow (MMSCMD)", value=6.0, min_value=0.1, key="h_flow")
            h_supply = st.number_input("Supply pressure (barg)", value=40.0, key="h_supply")
            h_deliv = st.number_input("Delivery pressure min (barg)", value=30.0, key="h_deliv")
        else:
            h_flow = st.number_input("Flow (MMTPA)", value=5.0, min_value=0.1, key="h_flow_l")
            h_rho = st.number_input("Density (kg/m³)", value=850.0, key="h_rho")
            h_visc = st.number_input("Viscosity (cSt)", value=10.0, min_value=0.1, key="h_visc")
            h_supply = st.number_input("Supply pressure (barg)", value=1.0, key="h_supply_l")
            h_deliv = st.number_input("Delivery pressure min (barg)", value=2.0, key="h_deliv_l")

    try:
        prof = Profile(
            chainage_m=tuple(float(x) * 1000 for x in prof_df["chainage_km"]),
            elevation_m=tuple(float(z) for z in prof_df["elevation_m"]),
        ).resample(1000)
        buildings = [
            Building(
                chainage_m=float(r["chainage_km"]) * 1000,
                offset_m=float(r["offset_m"]),
                dwelling_units=int(r["dwelling_units"]),
                storeys=int(r["storeys"]),
            )
            for r in bld_df.dropna().to_dict("records")
        ]
        h_pipe = pipe_from_library(h_nps, h_wt, h_grade)
        if h_service is Service.GAS:
            segs = classify_route(prof.length_m, buildings)
            maop = maop_along_route(segs, h_pipe, prof.chainage_m)
        else:
            segs = []
            maop = maop_along_route([], h_pipe, prof.chainage_m, service=Service.LIQUID)
        if h_service is Service.GAS:
            hyd = gas_hydraulics(
                prof,
                h_pipe.id_m,
                Q_(h_flow, "MMSCMD"),
                GasStationInput(
                    supply_pressure_pa_g=h_supply * 1e5, delivery_min_pa_g=h_deliv * 1e5
                ),
                maop_pa_g=maop,
            )
            hyd_checks = hyd.checks
            march = hyd.march
        else:
            liq_res = liquid_hydraulics(
                prof,
                h_pipe.id_m,
                Q_(h_flow, "MMTPA"),
                PumpStationInput(
                    supply_pressure_pa_g=h_supply * 1e5, delivery_min_pa_g=h_deliv * 1e5
                ),
                maop_pa_g=maop,
                liquid=Liquid(density_kg_m3=h_rho, kinematic_viscosity_m2_s=h_visc * 1e-6),
            )
            hyd_checks = liq_res.checks
            march = liq_res.march
    except (ValueError, HydraulicsError) as e:
        st.error(str(e))
    else:
        if segs:
            st.markdown("**Location classes**")
            st.dataframe(
                [
                    {
                        "From km": round(sg.start_m / 1000, 2),
                        "To km": round(sg.end_m / 1000, 2),
                        "Class": sg.location_class.value,
                        "Reason": sg.reason,
                    }
                    for sg in segs
                ],
                hide_index=True,
            )
            st.write(
                "Length by class: "
                + ", ".join(
                    f"Class {k.value}: {v / 1000:.1f} km" for k, v in class_summary(segs).items()
                )
            )
        n_st = len(march.stations)
        c1, c2, c3 = st.columns(3)
        c1.metric("Stations", str(n_st))
        c2.metric("Total power (MW)", f"{march.total_power_w / 1e6:.2f}")
        c3.metric("Delivery pressure (barg)", f"{march.delivery_pressure_pa_g / 1e5:.1f}")
        st.line_chart(
            pd.DataFrame(
                {
                    "chainage_km": [x / 1000 for x in march.chainage_m],
                    "pressure_barg": [p / 1e5 for p in march.pressure_pa_g],
                    "MAOP_barg": [m / 1e5 for m in maop],
                }
            ).set_index("chainage_km")
        )
        if march.stations:
            st.dataframe(
                [
                    {
                        "Chainage km": st_.chainage_m / 1000,
                        "Suction barg": round(st_.suction_pa_g / 1e5, 1),
                        "Discharge barg": round(st_.discharge_pa_g / 1e5, 1),
                        "Power MW": round(st_.power_w / 1e6, 2),
                    }
                    for st_ in march.stations
                ],
                hide_index=True,
            )
        st.dataframe(
            [
                {
                    "Check": c.name,
                    "UR": round(c.utilisation, 3),
                    "Status": "PASS" if c.passed else "FAIL",
                    "Reference": c.ref,
                }
                for c in hyd_checks
            ],
            hide_index=True,
        )


# ------------------------------------------------------------------ economics
@st.cache_data(show_spinner="Evaluating diameters...")
def _curve(base_json: str, sizes: tuple[str, ...]) -> list[OptionResult]:
    """Cached economic-diameter curve, keyed on the full validated input."""
    return economic_diameter_curve(OptionInput.model_validate_json(base_json), sizes)


@st.cache_data(show_spinner="Running Monte Carlo...")
def _risk(designs_json: tuple[str, ...], samples: int, per_unit: tuple[float, ...]) -> Any:
    from pipeline_analytics.economics.risk import RiskInputs, monte_carlo

    designs = [OptionInput.model_validate_json(j) for j in designs_json]
    return monte_carlo(designs, RiskInputs(samples=samples), per_unit=list(per_unit))


with econ_tab:
    from pipeline_analytics.cost.rate_library import RateLibrary, validate_rates

    rate_lib = RateLibrary()
    e_rate_name = st.selectbox(
        "Unit-rate set",
        rate_lib.names(),
        key="e_rateset",
        help="Rate sets live in ./rates (or $PIPELINE_RATES_DIR); manage them with the "
        "pipeline-rates command.",
    )
    try:
        e_rates = rate_lib.load(e_rate_name)
    except (ValueError, FileNotFoundError) as e:
        st.error(f"Could not load rate set {e_rate_name!r}: {e}")
        e_rates = placeholder_rates()
    findings = validate_rates(e_rates, reference=placeholder_rates())
    if not e_rates.placeholder:
        status = (f"approved by {e_rates.meta.approved_by} on {e_rates.meta.approved_on}"
                  if e_rates.meta.approved_by else "not approved")  # fmt: skip
        st.caption(f"{e_rates.source}; {e_rates.currency} {e_rates.base_year}; {status}.")
    if any(f.level != "info" for f in findings) and not e_rates.placeholder:
        with st.expander(f"Rate-set checks: {len(findings)} finding(s)"):
            st.dataframe([f.model_dump() for f in findings], hide_index=True)
    if e_rates.placeholder:
        st.warning(
            "PLACEHOLDER unit rates (India, INR, 2026): order-of-magnitude values for testing "
            "the method, not market data. Results are AACE Class 5 at best. Load project "
            "rates before sharing any number."
        )
    st.caption(
        "Lifecycle cost = PV(CAPEX) + PV(OPEX) - PV(residual) over the concession, for each "
        "diameter. Uses the route profile and buildings from the previous tab."
    )
    e1, e2, e3 = st.columns(3)
    with e1:
        e_service = Service(st.selectbox("Service", [s.value for s in Service], key="e_service"))
        all_nps = [s.nps for s in lib.sizes if s.nps_value >= 8]
        e_sizes = st.multiselect(
            "Diameters (NPS)", all_nps, default=["16", "20", "24", "28", "30", "36"], key="e_sizes"
        )
        e_grade = st.selectbox(
            "Grade (PSL2)", [g.name for g in lib.find_grades(psl=2)], index=7, key="e_grade"
        )
        e_p = st.number_input("Design pressure (barg)", value=92.0, min_value=1.0, key="e_p")
    with e2:
        unit = "MMSCMD" if e_service is Service.GAS else "MMTPA"
        e_q0 = st.number_input(f"Year-1 throughput ({unit})", value=6.0, min_value=0.1, key="e_q0")
        e_q1 = st.number_input(
            f"Plateau throughput ({unit})", value=10.0, min_value=0.1, key="e_q1"
        )
        e_ramp = st.number_input("Ramp-up years", value=8, min_value=1, max_value=30, key="e_ramp")
        e_years = st.number_input(
            "Operating years (concession)", value=25, min_value=5, max_value=40, key="e_years"
        )
    with e3:
        e_rate = st.number_input("Discount rate (real)", value=0.10, step=0.01, key="e_rate")
        e_target = st.number_input("Target post-tax return", value=0.12, step=0.01, key="e_target")
        e_supply = st.number_input("Supply pressure (barg)", value=40.0, key="e_supply")
        e_deliv = st.number_input("Delivery pressure min (barg)", value=30.0, key="e_deliv")

    try:
        e_prof = Profile(
            chainage_m=tuple(float(x) * 1000 for x in prof_df["chainage_km"]),
            elevation_m=tuple(float(z) for z in prof_df["elevation_m"]),
        ).resample(5000)
        e_segs: tuple = ()  # type: ignore[type-arg]
        if e_service is Service.GAS:
            e_segs = tuple(classify_route(e_prof.length_m, buildings, step_m=50))
        base = OptionInput(
            service=e_service,
            nps=e_sizes[0] if e_sizes else 24,
            grade=e_grade,
            design_pressure_pa_g=e_p * 1e5,
            profile=e_prof,
            class_segments=e_segs,
            throughput=ramp_profile(e_q0, e_q1, int(min(e_ramp, e_years)), int(e_years)),
            gas_stations=GasStationInput(
                supply_pressure_pa_g=e_supply * 1e5, delivery_min_pa_g=e_deliv * 1e5
            ),
            pump_stations=PumpStationInput(
                supply_pressure_pa_g=e_supply * 1e5, delivery_min_pa_g=e_deliv * 1e5
            ),
            concession=Concession(operating_years=int(e_years), discount_rate=e_rate),
            rates=e_rates,
        )
        results = _curve(base.model_dump_json(), tuple(e_sizes))
        best = best_option(results)
    except (ValueError, NameError) as e:
        st.error(str(e))
    else:
        per = "₹/MMBtu" if e_service is Service.GAS else "₹/t"
        rows: list[dict[str, object]] = []
        for r in results:
            if not r.feasible:
                rows.append({"NPS": r.nps, "Feasible": "no", "Issue": r.issues[0]})
                continue
            lev = r.levelised_cost_per_mmbtu if e_service is Service.GAS else r.levelised_cost
            rows.append(
                {
                    "NPS": r.nps,
                    "Feasible": "yes",
                    "Wall mm": ", ".join(f"{w.wt_m * 1e3:.1f}" for w in r.walls),
                    "Pipe t": round(r.pipe_tonnes),
                    "CAPEX ₹ cr": round(crore(r.capex.total)) if r.capex else None,
                    "PV CAPEX ₹ cr": round(crore(r.pv_capex)),
                    "PV OPEX ₹ cr": round(crore(r.pv_opex)),
                    "LCC ₹ cr": round(crore(r.lcc_pv)),
                    "Max stations": max(h.stations for h in r.hydraulics),
                    f"Levelised {per}": round(lev, 2),
                }
            )
        st.dataframe(rows, hide_index=True)
        ok = [r for r in results if r.feasible]
        st.line_chart(
            pd.DataFrame(
                {
                    "NPS": [r.nps for r in ok],
                    "PV CAPEX": [crore(r.pv_capex) for r in ok],
                    "PV OPEX": [crore(r.pv_opex) for r in ok],
                    "LCC": [crore(r.lcc_pv) for r in ok],
                }
            ).set_index("NPS")
        )
        cf = best.cashflow
        assert cf is not None and best.capex is not None
        tariff = cf.tariff_for_return(e_target)
        f1, f2, f3 = st.columns(3)
        f1.metric("Lowest lifecycle cost", f'{best.nps}"', f"₹ {crore(best.lcc_pv):,.0f} cr PV")
        lev_best = (
            best.levelised_cost_per_mmbtu if e_service is Service.GAS else best.levelised_cost
        )
        f2.metric(f"Levelised cost ({per})", f"{lev_best:,.2f}")
        tariff_shown = tariff * best.scm_per_mmbtu if e_service is Service.GAS else tariff
        f3.metric(f"Tariff for {e_target:.0%} post-tax ({per})", f"{tariff_shown:,.2f}")
        st.markdown(f'**CAPEX breakdown, {best.nps}" (₹ crore)**')
        st.bar_chart(
            pd.DataFrame({"₹ crore": {k: crore(v) for k, v in best.capex.by_group().items()}})
        )
        with st.expander("Cash flow (₹ crore, real)"):
            st.dataframe(
                [
                    {
                        "Year": y.year,
                        "Phase": "operation" if y.operating else "construction",
                        "CAPEX": round(crore(y.capex), 1),
                        "OPEX": round(crore(y.opex), 1),
                        "Throughput": y.throughput,
                    }
                    for y in cf.years
                ],
                hide_index=True,
            )
        # Monte Carlo risk on the best design of each feasible size
        st.markdown("**Risk: lifecycle cost under cost and throughput uncertainty**")
        k1, k2 = st.columns([1, 3])
        e_samples = k1.select_slider("Samples", [2_000, 5_000, 10_000, 20_000], 10_000,
                                     key="e_samples")  # fmt: skip
        ok_sizes = [n for n, r in zip(e_sizes, results, strict=True) if r.feasible]
        risk_key = (base.model_dump_json(), tuple(ok_sizes), e_samples)
        if k2.button("Run Monte Carlo risk analysis", key="e_risk", disabled=not ok_sizes):
            designs = tuple(
                OptionInput.model_validate({**base.model_dump(), "nps": n}).model_dump_json()
                for n in ok_sizes
            )
            per_u = tuple(
                r.scm_per_mmbtu if e_service is Service.GAS else 1.0
                for r in results if r.feasible
            )  # fmt: skip
            st.session_state.e_risk_result = (risk_key, _risk(designs, e_samples, per_u))
        risk_res = None
        if st.session_state.get("e_risk_result", (None,))[0] == risk_key:
            risk_res = st.session_state.e_risk_result[1]
            st.dataframe(
                [
                    {
                        "Design": d.label,
                        "P10 ₹ cr": None if d.lcc_p10 == float("inf") else round(crore(d.lcc_p10)),
                        "P50 ₹ cr": None if d.lcc_p50 == float("inf") else round(crore(d.lcc_p50)),
                        "P90 ₹ cr": None if d.lcc_p90 == float("inf") else round(crore(d.lcc_p90)),
                        "Deterministic ₹ cr": round(crore(d.deterministic_lcc)),
                        "Cheapest %": round(d.prob_cheapest * 100, 1),
                        "Infeasible %": round(d.prob_infeasible * 100, 1),
                    }
                    for d in risk_res.designs
                ],
                hide_index=True,
            )
            top = max(risk_res.designs, key=lambda d: d.prob_cheapest)
            st.metric("Most often cheapest", top.label, f"{top.prob_cheapest:.0%} of samples")
            from pipeline_analytics.reports import risk_note_for

            st.caption(risk_note_for(risk_res))
        else:
            st.caption("Samples unit-rate groups and throughput scenarios; P(cheapest) "
                       "compares sizes on the same samples.")  # fmt: skip

        # study report downloads (same report model as the bot and study scripts)
        try:
            from pipeline_analytics.reports import (
                Money,
                report_from_options,
                risk_note_for,
                risk_rows,
            )
            from pipeline_analytics.reports.docx import write_docx
            from pipeline_analytics.reports.xlsx import write_xlsx

            ev = [
                (OptionInput.model_validate({**base.model_dump(), "nps": n}), r)
                for n, r in zip(e_sizes, results, strict=True)
            ]
            rep = report_from_options(
                ev,
                title="Pipeline Diameter Screening",
                subtitle=f"{e_service.value} service, {e_prof.length_m / 1000:g} km route",
                money=Money.inr_crore(),
                target_return=e_target,
                inputs=[
                    ("Throughput", f"{e_q0:g} -> {e_q1:g} {unit} over {int(e_ramp)} yr"),
                    ("Operating years", f"{int(e_years)}"),
                    ("Discount rate (real)", f"{e_rate:.1%}"),
                    ("Design pressure", f"{e_p:g} barg"),
                ],
                disclosures=["PLACEHOLDER unit rates (screening only)."]
                if e_rates.placeholder
                else [],
                risk=risk_rows(risk_res, Money.inr_crore()) if risk_res else (),
                risk_note=risk_note_for(risk_res) if risk_res else "",
            )
            b_docx, b_xlsx = io.BytesIO(), io.BytesIO()
            write_docx(rep, b_docx)
            write_xlsx(rep, b_xlsx)
            r1, r2 = st.columns(2)
            r1.download_button("Download report (Word)", b_docx.getvalue(),
                               "pipeline_screening.docx", key="e_docx")  # fmt: skip
            r2.download_button("Download workbook (Excel)", b_xlsx.getvalue(),
                               "pipeline_screening.xlsx", key="e_xlsx")  # fmt: skip
        except ImportError:
            st.info("Install the 'reports' extra for Word and Excel reports.")
        if st.button("Run sensitivity (±20 %) on the best option", key="e_tornado"):
            best_inp = base.model_copy(update={"nps": best.nps})
            torn = option_tornado(best_inp, 0.2)
            scale = best.scm_per_mmbtu if e_service is Service.GAS else 1.0
            st.dataframe(
                [
                    {"Parameter": n, f"-20 % ({per})": lo * scale, f"+20 % ({per})": hi * scale}
                    for n, lo, hi in torn
                ],
                hide_index=True,
            )

# ---------------------------------------------------------------------- route
_COLOURS = [(230, 57, 70), (29, 120, 255), (46, 160, 67), (245, 159, 0), (160, 70, 200)]


@st.cache_data(show_spinner="Routing...")
def _route(nps: str, pressure_barg: float, k: int, base_json: str) -> tuple:  # type: ignore[type-arg]
    aoi = route_sample.sample_aoi()
    base_inp = OptionInput.model_validate_json(base_json)
    pen = opex_penalties(base_inp)
    surf = build_cost_surface(
        aoi,
        CostSurfaceParams(
            service=Service.GAS,
            nps=nps,
            design_pressure_pa_g=pressure_barg * 1e5,
            length_per_m=pen.length_per_m,
        ),
    )
    alts = alternative_corridors(surf, route_sample.START, route_sample.END, k=k)
    exs = [extract_route(surf, smooth_path_polyline(surf, a.cells)) for a in alts]
    res = [evaluate_option(ex.option_input(base_inp)) for ex in exs]
    # picture: log cost in grey, excluded in dark red, water blue, corridors in colour
    cost = surf.cell_cost
    finite = np.isfinite(cost)
    lg = np.log(np.where(finite, cost, np.nan))
    lo, hi = np.nanmin(lg), np.nanmax(lg)
    grey = np.where(finite, 230 - 150 * (lg - lo) / max(hi - lo, 1e-9), 0).astype(np.uint8)
    img = np.stack([grey, grey, grey], axis=-1)
    img[~finite] = (90, 20, 30)
    img[aoi.layer("water")] = (120, 170, 230)
    for i, a in enumerate(alts):
        for r, c in a.cells:
            img[r, c] = _COLOURS[i % len(_COLOURS)]
    img = np.kron(img, np.ones((2, 2, 1), dtype=np.uint8))
    return surf.grid.crs, alts, exs, res, img


with route_tab:
    st.caption(
        "Least-cost routing on a SYNTHETIC 40 x 24 km sample area (ridge with a northern saddle, "
        "protected forest, 400 m river, highways, railway, town, wetland, fault zone). "
        "Cell costs use the same unit rates as the Economics tab. Real areas: load a DEM, "
        "land cover and GeoJSON features (see docs/routing.md)."
    )
    if placeholder_rates().placeholder:
        st.warning("Placeholder unit rates: compare corridors relative to each other only.")
    r1, r2, r3 = st.columns(3)
    r_nps = r1.selectbox("NPS", ["20", "24", "28", "30", "36"], index=3, key="r_nps")
    r_p = r2.number_input("Design pressure (barg)", value=92.0, min_value=10.0, key="r_p")
    r_k = r3.number_input("Alternative corridors", value=4, min_value=1, max_value=5, key="r_k")
    r_base = OptionInput(
        service=Service.GAS,
        nps=r_nps,
        design_pressure_pa_g=r_p * 1e5,
        profile=Profile.flat(38e3),
        throughput=ramp_profile(6, 10, 8, 25),
        gas_stations=GasStationInput(supply_pressure_pa_g=60e5, delivery_min_pa_g=30e5),
    )
    try:
        crs, alts, exs, res, img = _route(r_nps, r_p, int(r_k), r_base.model_dump_json())
    except ValueError as e:
        st.error(str(e))
    else:
        st.image(img, caption="Cost surface (darker = dearer), corridors C1..Cn in colour")
        st.dataframe(
            [
                {
                    "Corridor": f"C{i + 1}",
                    "Length km": round(ex.length_m / 1000, 1),
                    "Max elevation m": round(max(ex.profile.elevation_m)),
                    "Crossings": ", ".join(f"{k} {v}" for k, v in ex.crossings.items()),
                    "HDD m": round(ex.hdd_m),
                    "Fault zone m": round(ex.hazard_m.get(3, 0.0)),
                    "Class 2+ m": round(
                        sum(
                            sg.length_m
                            for sg in ex.class_segments
                            if sg.location_class.value != "1-2"
                        )
                    ),
                    "Route cost ₹ cr": round(crore(a.cost)),
                    "LCC ₹ cr": round(crore(r.lcc_pv)) if r.feasible else None,
                }
                for i, (a, ex, r) in enumerate(zip(alts, exs, res, strict=True))
            ],
            hide_index=True,
        )
        items = [
            (f"C{i + 1}", ex.polyline, {"length_km": round(ex.length_m / 1000, 2)})
            for i, ex in enumerate(exs)
        ]
        try:
            import pydeck as pdk

            from pipeline_analytics.routing.export import to_wgs84

            paths: list[dict[str, Any]] = [
                {"name": name, "path": [list(p) for p in to_wgs84(coords, crs)],
                 "color": list(_COLOURS[i % len(_COLOURS)])}
                for i, (name, coords, _) in enumerate(items)
            ]  # fmt: skip
            lons = [p[0] for d in paths for p in d["path"]]
            lats = [p[1] for d in paths for p in d["path"]]
            st.pydeck_chart(
                pdk.Deck(
                    layers=[
                        pdk.Layer(
                            "PathLayer",
                            paths,
                            get_path="path",
                            get_color="color",
                            width_min_pixels=3,
                            pickable=True,
                        )
                    ],
                    initial_view_state=pdk.ViewState(
                        longitude=sum(lons) / len(lons), latitude=sum(lats) / len(lats), zoom=9
                    ),
                    tooltip={"text": "{name}"},
                ),
                key="route_map",
            )
            st.caption("Corridors on a real basemap (synthetic sample area near 73.1 E, 22.6 N).")
        except ImportError:
            st.info("Install the 'gis' extra (pyproj) for the map view.")
        d1, d2 = st.columns(2)
        d1.download_button("Download GeoJSON", to_geojson(items, crs), "corridors.geojson")
        try:
            d2.download_button("Download KML", to_kml(items, crs), "corridors.kml")
        except ImportError:
            d2.info("Install the 'gis' extra for KML export")

# ------------------------------------------------------------ feasibility bot
with bot_tab:
    st.caption(
        "Describe your project in plain language: fluid, flow, route legs, pressures, concession. "
        "The bot runs the engine's tools and answers only with figures they return; each answer "
        "is checked for numbers that no tool produced."
    )
    if "bot" not in st.session_state:
        try:
            from pipeline_analytics.bot import FeasibilityBot

            st.session_state.bot = FeasibilityBot()
            st.session_state.chat = []
        except Exception as e:  # missing extra or credentials
            st.session_state.bot = None
            st.session_state.bot_error = str(e)
    bot = st.session_state.bot
    if bot is None:
        st.info(
            "The bot needs the 'bot' extra and Claude API credentials: set ANTHROPIC_API_KEY "
            "(or sign in with `ant auth login`) and restart the app. "
            f"Details: {st.session_state.get('bot_error', '')}"
        )
    else:
        for role, msg in st.session_state.chat:
            st.chat_message(role).markdown(msg)
        prompt = st.chat_input("Ask about your pipeline project", key="bot_input")
        if prompt:
            st.chat_message("user").markdown(prompt)
            st.session_state.chat.append(("user", prompt))
            try:
                with st.spinner("Working: running the engine..."):
                    reply = bot.ask(prompt)
            except Exception as e:  # credentials, network, rate limits
                st.error(
                    "The Claude API call failed. If no credentials are set, set "
                    f"ANTHROPIC_API_KEY or sign in with `ant auth login`, then restart. ({e})"
                )
                st.session_state.pop("bot")
            else:
                answer = reply.text
                if reply.unsupported:
                    answer += (
                        "\n\n> Check: these figures were not found in any tool result: "
                        + ", ".join(reply.unsupported)
                    )
                st.chat_message("assistant").markdown(answer)
                st.session_state.chat.append(("assistant", answer))
        files = bot.study.last_report_files if st.session_state.get("bot") else {}
        if files:
            cols = st.columns(len(files))
            for col, (kind, fpath) in zip(cols, sorted(files.items()), strict=True):
                with open(fpath, "rb") as fh:
                    col.download_button(
                        f"Download {kind.upper()} report",
                        fh.read(),
                        fpath.rsplit("/", 1)[-1],
                        key=f"bot_{kind}",
                    )


# ------------------------------------------------------------------- projects
def _auth_configured() -> bool:
    try:
        return "auth" in st.secrets
    except Exception:  # no secrets file
        return False


def _current_user() -> str | None:
    """Signed-in e-mail with [auth] configured; otherwise a self-declared local name."""
    if _auth_configured():
        if not st.user.is_logged_in:
            st.info("Sign in to see and save your projects.")
            st.button("Sign in", on_click=st.login, key="p_login")
            return None
        u1, u2 = st.columns([4, 1])
        u1.caption(f"Signed in as {st.user.get('email') or st.user.get('name')}")
        u2.button("Sign out", on_click=st.logout, key="p_logout")
        return str(st.user.get("email") or st.user.get("name"))
    st.warning(
        "Local mode: no sign-in is configured, so a name is not verified. Anyone using this "
        "app can type any name. Add an [auth] section to .streamlit/secrets.toml for real "
        "accounts (see docs/hardening.md)."
    )
    name = st.text_input("Your name or e-mail", key="p_user").strip()
    return name or None


with proj_tab:
    from pipeline_analytics.projects import (
        AccessDeniedError,
        ProjectNotFoundError,
        ProjectStore,
        VersionConflictError,
    )
    from pipeline_analytics.projects.payload import (
        chat_of,
        payload_to_study,
        study_to_payload,
        summary_of,
    )

    st.caption(
        "Projects keep a feasibility-bot study (basis, route, rates, results) with its full "
        "version history. Share a project with colleagues as viewer or editor."
    )
    user = _current_user()
    store = ProjectStore()
    the_bot = st.session_state.get("bot")
    if user:
        with st.expander("New project"):
            n_name = st.text_input("Project name", key="p_new_name")
            n_desc = st.text_input("Description", key="p_new_desc")
            n_cur = st.checkbox("Start from the current bot study", value=the_bot is not None,
                                disabled=the_bot is None, key="p_new_cur")  # fmt: skip
            if st.button("Create project", key="p_create", disabled=not n_name.strip()):
                payload = (study_to_payload(the_bot.study, st.session_state.get("chat", []))
                           if n_cur and the_bot is not None else None)  # fmt: skip
                created = store.create(n_name, user, n_desc, payload=payload)
                st.session_state.p_open = (created.id, created.latest_version)
                st.success(f"Created {created.name}.")

        projects = store.list_for(user)
        if not projects:
            st.info("No projects yet.")
        else:
            st.dataframe(
                [
                    {
                        "Project": p.name,
                        "Your role": p.role_of(user),
                        "Owner": p.owner,
                        "Version": p.latest_version,
                        "Last saved by": p.latest_by,
                        "Updated (UTC)": p.updated_at.strftime("%Y-%m-%d %H:%M"),
                    }
                    for p in projects
                ],
                hide_index=True,
            )
            by_id = {p.id: p for p in projects}
            pid = st.selectbox("Project", list(by_id), format_func=lambda i: by_id[i].name,
                               key="p_pick")  # fmt: skip
            proj = by_id[pid]
            role = proj.role_of(user)
            hist = store.history(pid, user)
            if hist:
                st.dataframe(
                    [
                        {"Version": h.version, "Author": h.author, "Message": h.message,
                         "Saved (UTC)": h.created_at.strftime("%Y-%m-%d %H:%M")}
                        for h in reversed(hist)
                    ],
                    hide_index=True,
                )  # fmt: skip
            opened = st.session_state.get("p_open")
            c1, c2 = st.columns(2)
            with c1:
                version = st.selectbox(
                    "Version",
                    [h.version for h in reversed(hist)] or [0],
                    key=f"p_version_{pid}_{proj.latest_version}",
                )
                if st.button("Open in the bot", key="p_load", disabled=not hist or the_bot is None):
                    assert the_bot is not None
                    v = store.load(pid, user, version)
                    payload_to_study(v.payload, the_bot.study)
                    the_bot.messages = []  # the model starts fresh; the study is restored
                    st.session_state.chat = chat_of(v.payload)
                    st.session_state.p_open = (pid, proj.latest_version)
                    s = summary_of(v.payload)
                    st.success(f"Opened version {v.version}: {s['route'] or 'no route yet'}"
                               f"{', best ' + s['best'] if s['best'] else ''}. Run the design "
                               "search again before generating a report.")  # fmt: skip
                writer = role in ("owner", "editor")
                can_restore = writer and bool(hist) and version != proj.latest_version
                if can_restore and st.button(f"Restore version {version} as the latest",
                                             key="p_restore"):  # fmt: skip
                    store.restore(pid, user, version, expected_version=proj.latest_version)
                    st.rerun()
            with c2:
                if role in ("owner", "editor"):
                    msg = st.text_input("Save message", key="p_msg")
                    base_v = opened[1] if opened and opened[0] == pid else None
                    can_save = the_bot is not None and (base_v is not None or not hist)
                    if not can_save and the_bot is not None:
                        st.caption("Open this project in the bot before saving to it.")
                    if st.button("Save the bot study as a new version", key="p_save",
                                 disabled=not can_save):  # fmt: skip
                        assert the_bot is not None
                        try:
                            v = store.save(
                                pid, user,
                                study_to_payload(the_bot.study, st.session_state.get("chat", [])),
                                msg, expected_version=base_v or 0,
                            )  # fmt: skip
                        except VersionConflictError as e:
                            st.error(str(e))
                        except AccessDeniedError as e:
                            st.error(str(e))
                        else:
                            st.session_state.p_open = (pid, v.version)
                            st.success(f"Saved version {v.version}.")
                else:
                    st.caption("You can view this project; ask the owner for editor access.")
            if role == "owner":
                with st.expander("Sharing"):
                    s1, s2, s3 = st.columns([3, 1, 1])
                    who = s1.text_input("Share with (e-mail or name)", key="p_share_who")
                    r_ = s2.selectbox("Role", ["viewer", "editor"], key="p_share_role")
                    if s3.button("Share", key="p_share", disabled=not who.strip()):
                        try:
                            store.share(pid, user, who, r_)  # type: ignore[arg-type]
                            st.rerun()
                        except (ValueError, ProjectNotFoundError) as e:
                            st.error(str(e))
                    for other, orole in proj.shared_with.items():
                        o1, o2 = st.columns([4, 1])
                        o1.write(f"{other}: {orole}")
                        if o2.button("Remove", key=f"p_unshare_{other}"):
                            store.unshare(pid, user, other)
                            st.rerun()
