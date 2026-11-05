"""Streamlit entry point.

Run with:  uv run streamlit run apps/streamlit/app.py
"""

from __future__ import annotations

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
from pipeline_analytics.pipespec import WtPolicy, default_library

st.set_page_config(page_title="Pipeline Feasibility", layout="wide")
st.title("Cross-Country Pipeline Feasibility")
st.caption(f"pipeline-analytics v{__version__}")

basis_tab, library_tab, mech_tab, hyd_tab, econ_tab = st.tabs(
    ["Study basis", "Pipe library", "Mechanical (ALA)", "Class location & hydraulics", "Economics"]
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


with econ_tab:
    e_rates = placeholder_rates()
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
