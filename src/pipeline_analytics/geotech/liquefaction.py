"""SPT-based liquefaction triggering: Idriss & Boulanger (2008).

Idriss I.M., Boulanger R.W. (2008), *Soil liquefaction during earthquakes*,
EERI MNO-12; equations as summarised in Idriss & Boulanger (2010), report
UCD/CGM-10/02.

For each layer midpoint:

* CSR = 0.65 (a_max/g) (sigma_v / sigma'_v) r_d, with
  r_d = exp(alpha(z) + beta(z) M), alpha = -1.012 - 1.126 sin(z/11.73 + 5.133),
  beta = 0.106 + 0.118 sin(z/11.28 + 5.142) (z in m).
* (N1)60 = C_N N60, C_N = (P_a / sigma'_v)^m <= 1.7,
  m = 0.784 - 0.0768 sqrt((N1)60cs) (solved by iteration).
* (N1)60cs = (N1)60 + exp(1.63 + 9.7/(FC+0.01) - (15.7/(FC+0.01))^2).
* CRR(M=7.5, sigma'_v=1 atm) = exp(N/14.1 + (N/126)^2 - (N/23.6)^3 + (N/25.4)^4 - 2.8),
  N = (N1)60cs.
* MSF = 6.9 exp(-M/4) - 0.058 <= 1.8;
  K_sigma = 1 - C_sigma ln(sigma'_v / P_a) <= 1.1,
  C_sigma = 1 / (18.9 - 2.55 sqrt((N1)60cs)) <= 0.3.
* FS = CRR(M=7.5, 1 atm) x MSF x K_sigma / CSR.

Clay-like layers (plasticity index >= 7, Boulanger & Idriss 2006) and layers
above the water table are not susceptible. The N values must be corrected to
60 % hammer energy (and for rod length, sampler and borehole diameter) before
use: ``SptLayer.n60``.

Also provided: the liquefaction potential index (LPI, Iwasaki et al. 1982),
and T15 / F15 / D50_15 for the lateral-spread regression of Youd et al. (2002).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

PA = 101.325  # kPa
GAMMA_W = 9.81  # kN/m3


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_inf_nan="constants")


class SptLayer(_M):
    top_m: float = Field(ge=0)
    bottom_m: float = Field(gt=0)
    n60: float = Field(ge=0, description="SPT blow count corrected to 60 % energy etc.")
    fines_pct: float = Field(default=5.0, ge=0, le=100)
    d50_mm: float | None = Field(default=None, gt=0)
    unit_weight_kn_m3: float = Field(default=18.0, gt=0)
    plasticity_index: float | None = Field(default=None, ge=0)
    description: str = ""

    @model_validator(mode="after")
    def _check(self) -> SptLayer:
        if self.bottom_m <= self.top_m:
            raise ValueError("layer bottom must be below its top")
        return self

    @property
    def thickness_m(self) -> float:
        return self.bottom_m - self.top_m

    @property
    def sand_like(self) -> bool:
        return self.plasticity_index is None or self.plasticity_index < 7


class SptProfile(_M):
    layers: tuple[SptLayer, ...] = Field(min_length=1)
    water_table_m: float = Field(ge=0)
    name: str = ""

    @model_validator(mode="after")
    def _check(self) -> SptProfile:
        for a, b in zip(self.layers, self.layers[1:], strict=False):
            if abs(a.bottom_m - b.top_m) > 1e-6:
                raise ValueError("layers must be contiguous and ordered by depth")
        return self


class Earthquake(_M):
    magnitude: float = Field(ge=4, le=9.5)
    pga_g: float = Field(gt=0, le=2, description="peak ground acceleration at the surface")
    distance_km: float | None = Field(
        default=None, ge=0, description="to the nearest edge of the rupture (lateral spread)"
    )
    name: str = ""


class LayerResult(_M):
    layer: int
    depth_m: float
    sigma_v_kpa: float
    sigma_v_eff_kpa: float
    n1_60: float
    n1_60cs: float
    rd: float
    csr: float
    crr: float
    msf: float
    k_sigma: float
    factor_of_safety: float
    susceptible: bool
    liquefies: bool


class TriggeringResult(_M):
    profile: SptProfile
    earthquake: Earthquake
    layers: tuple[LayerResult, ...]
    lpi: float
    """Liquefaction potential index (Iwasaki): < 5 low, 5-15 high, > 15 very high."""

    @property
    def liquefied_thickness_m(self) -> float:
        return sum(self.profile.layers[r.layer].thickness_m for r in self.layers if r.liquefies)

    def youd_inputs(self, max_depth_m: float = 20.0) -> tuple[float, float, float]:
        """(T15 m, F15 %, D50_15 mm) for Youd et al. (2002): saturated cohesionless
        layers with (N1)60 < 15 that liquefy (FS < 1), within ``max_depth_m``.
        Zero thickness means no lateral spread is predicted."""
        t = f = d = 0.0
        for r in self.layers:
            lay = self.profile.layers[r.layer]
            if not r.liquefies or r.n1_60 >= 15 or lay.top_m >= max_depth_m:
                continue
            top = max(lay.top_m, self.profile.water_table_m)
            h = max(0.0, min(lay.bottom_m, max_depth_m) - top)
            if h <= 0:
                continue
            if lay.d50_mm is None:
                raise ValueError(f"layer {r.layer} needs d50_mm for the lateral-spread model")
            t += h
            f += h * lay.fines_pct
            d += h * lay.d50_mm
        return (t, f / t, d / t) if t > 0 else (0.0, 0.0, 0.0)


def rd(z: float, m: float) -> float:
    a = -1.012 - 1.126 * math.sin(z / 11.73 + 5.133)
    b = 0.106 + 0.118 * math.sin(z / 11.28 + 5.142)
    return math.exp(a + b * m)


def msf(m: float) -> float:
    return min(1.8, 6.9 * math.exp(-m / 4) - 0.058)


def delta_n_fines(fc: float) -> float:
    return math.exp(1.63 + 9.7 / (fc + 0.01) - (15.7 / (fc + 0.01)) ** 2)


def crr_75(n1_60cs: float) -> float:
    n = n1_60cs
    return math.exp(n / 14.1 + (n / 126) ** 2 - (n / 23.6) ** 3 + (n / 25.4) ** 4 - 2.8)


def k_sigma(sig_eff_kpa: float, n1_60cs: float) -> float:
    c = min(0.3, 1 / (18.9 - 2.55 * math.sqrt(min(n1_60cs, 37.0))))
    return min(1.1, 1 - c * math.log(sig_eff_kpa / PA))


def normalise_n(n60: float, sig_eff_kpa: float, fc: float) -> tuple[float, float]:
    """((N1)60, (N1)60cs) with the iterative C_N of Idriss & Boulanger."""
    dn = delta_n_fines(fc)
    n1 = n60
    for _ in range(50):
        ncs = min(n1 + dn, 46.0)
        m = 0.784 - 0.0768 * math.sqrt(ncs)
        cn = min(1.7, (PA / sig_eff_kpa) ** m)
        new = cn * n60
        if abs(new - n1) < 1e-8:
            break
        n1 = new
    return n1, n1 + dn


def triggering(profile: SptProfile, eq: Earthquake) -> TriggeringResult:
    out: list[LayerResult] = []
    sv_top = 0.0
    zw = profile.water_table_m
    lpi = 0.0
    for i, lay in enumerate(profile.layers):
        z = (lay.top_m + lay.bottom_m) / 2
        sv = sv_top + lay.unit_weight_kn_m3 * (z - lay.top_m)
        u = GAMMA_W * max(0.0, z - zw)
        se = max(sv - u, 1.0)
        sv_top += lay.unit_weight_kn_m3 * lay.thickness_m
        n1, ncs = normalise_n(lay.n60, se, lay.fines_pct)
        r = rd(z, eq.magnitude)
        csr = 0.65 * eq.pga_g * sv / se * r
        mf, ks = msf(eq.magnitude), k_sigma(se, ncs)
        crr = crr_75(ncs) * mf * ks if ncs < 37.5 else math.inf  # dense: not liquefiable
        susceptible = lay.sand_like and z > zw
        fs = crr / csr if susceptible else math.inf
        out.append(LayerResult(
            layer=i, depth_m=z, sigma_v_kpa=sv, sigma_v_eff_kpa=se, n1_60=n1, n1_60cs=ncs,
            rd=r, csr=csr, crr=crr, msf=mf, k_sigma=ks, factor_of_safety=fs,
            susceptible=susceptible, liquefies=susceptible and fs < 1.0,
        ))  # fmt: skip
        # LPI = integral over 0-20 m of F(z) w(z), F = 1 - FS (FS < 1), w = 10 - 0.5 z
        if susceptible and fs < 1:
            a, b = max(lay.top_m, zw), min(lay.bottom_m, 20.0)
            if b > a:
                lpi += (1 - fs) * ((10 * b - 0.25 * b * b) - (10 * a - 0.25 * a * a))
    return TriggeringResult(profile=profile, earthquake=eq, layers=tuple(out), lpi=lpi)


def deltaic_profile(name: str = "Deltaic river bank (ASSUMED)") -> SptProfile:
    """A generic young deltaic river-bank profile for screening (not site data)."""
    layers: Sequence[SptLayer] = (
        SptLayer(top_m=0, bottom_m=2, n60=4, fines_pct=85, plasticity_index=15,
                 unit_weight_kn_m3=17.5, description="silty clay crust"),
        SptLayer(top_m=2, bottom_m=5, n60=6, fines_pct=30, d50_mm=0.10,
                 unit_weight_kn_m3=18.0, description="loose silty fine sand"),
        SptLayer(top_m=5, bottom_m=9, n60=9, fines_pct=15, d50_mm=0.15,
                 unit_weight_kn_m3=18.5, description="loose to medium fine sand"),
        SptLayer(top_m=9, bottom_m=15, n60=18, fines_pct=10, d50_mm=0.20,
                 unit_weight_kn_m3=19.0, description="medium dense fine sand"),
        SptLayer(top_m=15, bottom_m=25, n60=30, fines_pct=8, d50_mm=0.25,
                 unit_weight_kn_m3=19.5, description="dense sand"),
    )  # fmt: skip
    return SptProfile(layers=tuple(layers), water_table_m=1.0, name=name)
