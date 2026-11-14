"""Analytical strike-slip fault crossing strains (Karamitros, Bouckovalas & Kouretzis 2007).

Karamitros D.K., Bouckovalas G.D., Kouretzis G.P. (2007), *Stress analysis of
buried steel pipelines at strike-slip fault crossings*, Soil Dynamics and
Earthquake Engineering 27, 200-211.

The method applies to crossing angles beta <= 90 deg, where the pipe is
stretched. Steps (paper section 3):

1. Beyond the high-curvature zone the pipe is a beam on elastic foundation:
   ``lambda = (k / 4 E1 I)^(1/4)``, rotational stiffness ``C_r = 2 lambda E1 I``.
2. The curved length ``L_c`` (fault to the first point of zero offset) is an
   elastic beam with ``C_r`` at one end and half the transverse offset at the
   fault. It carries the ultimate lateral soil load ``q_u``. ``L_c`` follows
   from ``V_A = -lambda M_A``, then ``M_max = V_B^2 / (2 q_u)``.
3. The axial stress at the fault comes from matching the required elongation
   (``Delta x``) with the elongation available over the unanchored length.
   The steel is bilinear and friction ``t_u`` is uniform.
4. Bending strain: ``1/eps_b = 1/eps_b^I + 1/eps_b^II``, where
   ``eps_b^I = M_max D / (2 E I)`` and ``eps_b^II = q_u D / (2 F_a)`` (cable).
5. Axial strain at the section of peak bending, from equilibrium of the axial
   force over a bilinear cross-section.
6. Iterate steps 2 to 5 with a secant modulus.

The authors report agreement within about 10 % with 3D shell finite-element
analyses for beta = 30 to 60 deg and offsets up to 2 D. The module serves here
as a screening method and as the published benchmark for the FE model
(see tests/mechanical/test_fe_benchmarks.py).
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq

from pipeline_analytics.mechanical.common import _Frozen


class KaramitrosResult(_Frozen):
    axial_stress_at_fault_pa: float
    axial_force_n: float
    axial_strain_at_fault: float
    curved_length_m: float
    bending_strain: float
    axial_strain_at_peak_bending: float
    max_strain: float
    min_strain: float
    iterations: int


def _bilinear(eps: np.ndarray, e1: float, e2: float, s1: float) -> np.ndarray:
    e_y = s1 / e1
    a = np.abs(eps)
    return np.sign(eps) * np.where(a <= e_y, e1 * a, s1 + e2 * (a - e_y))


def karamitros_strike_slip(
    *,
    od_m: float,
    wt_m: float,
    e1_pa: float,
    e2_pa: float,
    yield_pa: float,
    axial_friction_n_m: float,
    lateral_ultimate_n_m: float,
    lateral_yield_m: float,
    fault_offset_m: float,
    crossing_angle_deg: float,
    max_iterations: int = 200,
) -> KaramitrosResult:
    """Strains in a buried pipe crossing a strike-slip fault (beta <= 90 deg)."""
    if not 0 < crossing_angle_deg <= 90:
        raise ValueError("the method covers crossing angles 0 < beta <= 90 deg (pipe in tension)")
    d, t = od_m, wt_m
    rm = (d - t) / 2
    area = math.pi * (d**2 - (d - 2 * t) ** 2) / 4
    inertia = math.pi * (d**4 - (d - 2 * t) ** 4) / 64
    b = math.radians(crossing_angle_deg)
    dx, dy = fault_offset_m * math.cos(b), fault_offset_m * math.sin(b)
    tu, qu = axial_friction_n_m, lateral_ultimate_n_m
    k = qu / lateral_yield_m
    lam = (k / (4 * e1_pa * inertia)) ** 0.25
    cr = 2 * lam * e1_pa * inertia
    delta = dy / 2

    # step 3: axial stress at the fault (independent of bending)
    kk = tu / area
    if dx * kk <= yield_pa**2 / e1_pa:
        sa = math.sqrt(e1_pa * kk * dx)
    else:
        sa = (
            yield_pa * (e1_pa - e2_pa)
            + math.sqrt(yield_pa**2 * (e2_pa**2 - e1_pa * e2_pa) + e1_pa**2 * e2_pa * dx * kk)
        ) / e1_pa
    fa = sa * area
    eps_fault = sa / e1_pa if sa <= yield_pa else yield_pa / e1_pa + (sa - yield_pa) / e2_pa

    # fibres for the cross-section integrals (eqs 25-28, 33)
    th = (np.arange(720) + 0.5) * math.pi / 720
    cos_th = np.cos(th)
    da = 2 * rm * t * (math.pi / 720)  # both halves of the ring

    def force(ea: float, eb: float) -> float:
        return float((_bilinear(ea + eb * cos_th, e1_pa, e2_pa, yield_pa) * da).sum())

    def moment(ea: float, eb: float) -> float:
        return float((_bilinear(ea + eb * cos_th, e1_pa, e2_pa, yield_pa) * da * rm * cos_th).sum())

    e_sec = e1_pa
    eb = ea = 0.0
    lc = 0.0
    it = 0
    for it in range(1, max_iterations + 1):  # noqa: B007 - reported below
        ei = e_sec * inertia

        def ma(lc_: float, ei: float = ei) -> float:
            num = 24 * ei * delta * cr - qu * cr * lc_**4
            return float(num / (24 * ei * lc_ + 8 * cr * lc_**2))

        def va(lc_: float, ei: float = ei) -> float:
            num = 24 * ei * delta * cr - 12 * ei * qu * lc_**3 - 5 * qu * cr * lc_**4
            return float(num / (24 * ei * lc_**2 + 8 * cr * lc_**3))

        lc = brentq(lambda x: va(x) + lam * ma(x), 1e-3, 5000.0)
        vb = (24 * ei * delta * cr + 12 * ei * qu * lc**3 + 3 * qu * cr * lc**4) / (
            24 * ei * lc**2 + 8 * cr * lc**3
        )
        m_max = vb**2 / (2 * qu)
        eb1 = m_max * d / (2 * ei)
        eb2 = qu * d / (2 * fa) if fa > 0 else math.inf
        eb = 1 / (1 / eb1 + 1 / eb2)
        # step 5: axial strain at the section of peak bending
        lo, hi = -0.1, 0.1
        ea = brentq(lambda x, eb=eb: force(x, eb) - fa, lo, hi)
        # step 6: secant modulus
        m = moment(ea, eb)
        eb_i = 1 / (1 / eb - 1 / eb2) if eb2 != math.inf else eb
        e_new = m * d / (2 * inertia * eb_i)
        e_new = min(max(e_new, e2_pa), e1_pa)
        if abs(e_new - e_sec) <= 1e-6 * e1_pa:
            e_sec = e_new
            break
        e_sec = 0.5 * (e_sec + e_new)  # damped update
    return KaramitrosResult(
        axial_stress_at_fault_pa=sa,
        axial_force_n=fa,
        axial_strain_at_fault=eps_fault,
        curved_length_m=lc,
        bending_strain=eb,
        axial_strain_at_peak_bending=ea,
        max_strain=ea + eb,
        min_strain=ea - eb,
        iterations=it,
    )
