"""Streamlit entry point (Phase 0 shell).

Run with:  uv run --extra ui streamlit run apps/streamlit/app.py
"""

from __future__ import annotations

import streamlit as st

from pipeline_analytics import __version__
from pipeline_analytics.core.jurisdiction import Service, StudyDefaults, code_basis
from pipeline_analytics.core.units import Q_

st.set_page_config(page_title="Pipeline Feasibility", layout="wide")
st.title("Cross-Country Pipeline Feasibility")
st.caption(f"pipeline-analytics v{__version__} · Phase 0 shell")

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
    units = ["MMSCMD", "MMSCFD", "m**3/h"] if service is Service.GAS else ["MMTPA", "bpd", "t/h"]
    src = st.selectbox("From", units)
    dst = st.selectbox("To", units, index=1)
    q = Q_(value, src)
    if not q.is_compatible_with(dst):
        # Mass <-> volume flow (e.g. MMTPA <-> bpd) needs the liquid density.
        rho = Q_(st.number_input("Density (kg/m³)", value=850.0, min_value=1.0), "kg/m**3")
        q = q / rho if q.is_compatible_with("kg/s") else q * rho
    st.metric(f"{value} {src}", f"{q.to(dst).magnitude:,.4g} {dst}")

st.info(
    "The pipe specification library, engineering calculations and feasibility bot "
    "will appear here in later phases."
)
