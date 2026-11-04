"""Streamlit entry point.

Run with:  uv run streamlit run apps/streamlit/app.py
"""

from __future__ import annotations

import streamlit as st

from pipeline_analytics import __version__
from pipeline_analytics.core.jurisdiction import Service, StudyDefaults, code_basis
from pipeline_analytics.core.units import Q_
from pipeline_analytics.pipespec import WtPolicy, default_library

st.set_page_config(page_title="Pipeline Feasibility", layout="wide")
st.title("Cross-Country Pipeline Feasibility")
st.caption(f"pipeline-analytics v{__version__}")

basis_tab, library_tab = st.tabs(["Study basis", "Pipe library"])

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
