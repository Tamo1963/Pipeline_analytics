"""Published benchmark: Karamitros, Bouckovalas & Kouretzis (2007), strike-slip fault.

36" x 11.9 mm API 5L X65 (bilinear: 490 MPa, E1 210 GPa, E2 1.088 GPa), springs
per their Table 3, crossing angles 30-60 deg, offsets up to 2 D. The authors'
analytical method agrees with their 3D shell FE within about 10 %. Our FE is
compared with that method (implemented in mechanical/fault_analytical.py).
The full 12-case table is in docs/mechanical-fe.md.
"""

import math

import numpy as np
import pytest

from pipeline_analytics.mechanical.common import PipeSection, SteelProperties
from pipeline_analytics.mechanical.fault_analytical import karamitros_strike_slip
from pipeline_analytics.mechanical.fe import PipeSoilModel, SpringSet, SteelCurve, graded_mesh

D, T = 0.9144, 0.0119
PIPE = PipeSection(od_m=D, wt_m=T, smys_pa=490e6, steel=SteelProperties(youngs_modulus_pa=210e9))
SPRINGS = SpringSet(axial_force=40.5e3, axial_yield=0.003, lateral_force=318.6e3,
                    lateral_yield=0.0114, uplift_force=52e3, uplift_yield=0.0022,
                    bearing_force=1360e3, bearing_yield=0.1)  # fmt: skip
STEEL = SteelCurve(model="bilinear", yield_pa=490e6, youngs_pa=210e9, hardening_ratio=1.088 / 210)


def analytical(df: float, beta: float):  # type: ignore[no-untyped-def]
    return karamitros_strike_slip(
        od_m=D,
        wt_m=T,
        e1_pa=210e9,
        e2_pa=1.088e9,
        yield_pa=490e6,
        axial_friction_n_m=40.5e3,
        lateral_ultimate_n_m=318.6e3,
        lateral_yield_m=0.0114,
        fault_offset_m=df,
        crossing_angle_deg=beta,
    )


def fe(df: float, beta: float):  # type: ignore[no-untyped-def]
    ax, tr = df * math.cos(math.radians(beta)), df * math.sin(math.radians(beta))
    x = graded_mesh(-500, 500, [0.0], 0.2, 5.0, 25.0)
    return PipeSoilModel(PIPE, SPRINGS, STEEL, x).solve(
        lambda xx: np.vstack([ax / 2 * np.sign(xx), tr / 2 * np.sign(xx), 0 * xx])
    )


@pytest.mark.parametrize(
    ("beta", "ratio", "strain_tol"),
    [(30, 1.5, 0.05), (30, 2.0, 0.05), (45, 2.0, 0.05), (60, 0.5, 0.12), (45, 1.0, 0.25)],
)
def test_strike_slip_against_published_method(beta: float, ratio: float, strain_tol: float) -> None:
    a, r = analytical(ratio * D, beta), fe(ratio * D, beta)
    assert r.converged
    i0 = int(np.argmin(np.abs(np.array(r.x_elem_m))))
    assert r.axial_force_n[i0] == pytest.approx(a.axial_force_n, rel=0.05)
    assert r.max_tensile_strain == pytest.approx(a.max_strain, rel=strain_tol)
    # the FE is not less conservative than the published method by more than 5 %
    assert r.max_tensile_strain >= 0.95 * a.max_strain


def test_analytical_method_range() -> None:
    with pytest.raises(ValueError, match="beta"):
        analytical(1.0, 120)
    a = analytical(0.01, 45)  # tiny offset: elastic axial stress sqrt(E t_u dx / A)
    area = math.pi * (D**2 - (D - 2 * T) ** 2) / 4
    assert a.axial_stress_at_fault_pa == pytest.approx(
        math.sqrt(210e9 * 40.5e3 * 0.01 * math.cos(math.radians(45)) / area)
    )
