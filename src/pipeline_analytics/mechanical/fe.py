"""Pipe-soil finite-element model for permanent ground deformation (PGD).

Formulation
-----------
* The pipe is a 3D beam along x on nonlinear soil springs (ALA 2001, Appendix B).
  Each node has five degrees of freedom: axial ``u``, horizontal ``v`` with
  slope ``v'``, and vertical ``w`` with slope ``w'``. Torsion is not loaded.
* Total-Lagrangian, moderate-rotation (von Karman) kinematics. The membrane
  strain is ``u' + (v'^2 + w'^2) / 2``. This captures the axial tension that
  large transverse offsets induce (the "cable" effect that governs fault
  crossings). Rotations above about 0.3 rad are flagged, because the
  approximation degrades there.
* Elements: linear ``u``, cubic Hermite ``v`` and ``w``. The membrane strain is
  averaged over each element, which avoids membrane locking.
* Section: fibers on a ring whose radius gives the exact second moment of the
  tube. The stress-strain curve is Ramberg-Osgood, bilinear or elastic. The
  curves are path-independent, which suits monotonic ground movement; cyclic
  loading is out of scope.
* Soil: elastic-perfectly-plastic springs lumped at the nodes, acting on the
  displacement of the pipe relative to the ground. Uplift and bearing differ.
* Load: the ground displacement field is scaled from 0 to 1 by Newton-Raphson,
  with adaptive steps and a backtracking line search. The ends of the model
  follow the ground with zero slope; make the model long enough that they are
  anchored (``FeResult.end_force_ratio`` reports this).

Local buckling and ovalisation are not modelled. Compressive strain is judged
against the ALA strain limits, as in ALA 2001.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Literal

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from pydantic import Field

from pipeline_analytics.mechanical.common import PipeSection, _Frozen
from pipeline_analytics.mechanical.soil import SoilSprings

NDOF = 5  # u, v, v', w, w'
_GX = np.array([0.5 - math.sqrt(15) / 10, 0.5, 0.5 + math.sqrt(15) / 10])
_GW = np.array([5 / 18, 8 / 18, 5 / 18])

# ------------------------------------------------------------------ steel

# Ramberg-Osgood parameters (ASCE 1984 / ALA 2001 seismic guidance) for
# eps = sigma/E * (1 + n/(1+r) * (sigma/sigma_y)^r). VERIFY against the
# licensed document; grades between table entries use the next lower entry.
RAMBERG_OSGOOD = {
    "B": (10.0, 100.0),
    "X42": (15.0, 32.0),
    "X52": (9.0, 10.0),
    "X60": (10.0, 12.0),
    "X70": (5.5, 16.6),
}


class SteelCurve(_Frozen):
    model: Literal["ramberg_osgood", "bilinear", "elastic"] = "ramberg_osgood"
    yield_pa: float = Field(gt=0)
    youngs_pa: float = Field(default=207e9, gt=0)
    n: float = Field(default=10.0, gt=0, description="Ramberg-Osgood n")
    r: float = Field(default=12.0, gt=1, description="Ramberg-Osgood r")
    hardening_ratio: float = Field(default=0.02, ge=0, lt=1, description="bilinear E_h / E")
    source: str = ""

    @classmethod
    def for_grade(cls, grade: str, smys_pa: float, youngs_pa: float = 207e9) -> SteelCurve:
        """Ramberg-Osgood curve for an API 5L grade (table entry at or below it)."""
        g = grade.upper().removesuffix("M").removesuffix("Q").removesuffix("N")
        if g in RAMBERG_OSGOOD:
            key = g
        else:
            lvl = int(g[1:]) if g.startswith("X") and g[1:].isdigit() else 0
            keys = [k for k in RAMBERG_OSGOOD if k != "B" and int(k[1:]) <= lvl]
            key = max(keys, key=lambda k: int(k[1:])) if keys else "B"
        n, r = RAMBERG_OSGOOD[key]
        note = "" if key == g else f" (parameters of {key} used for {grade})"
        return cls(yield_pa=smys_pa, youngs_pa=youngs_pa, n=n, r=r,
                   source=f"Ramberg-Osgood, ASCE 1984 / ALA 2001{note}")  # fmt: skip

    def stress(self, eps: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Stress and tangent modulus for strains ``eps`` (any shape)."""
        e_mod, fy = self.youngs_pa, self.yield_pa
        a = np.abs(eps)
        sgn = np.sign(eps)
        if self.model == "elastic":
            return e_mod * eps, np.full_like(eps, e_mod)
        if self.model == "bilinear":
            ey = fy / e_mod
            eh = self.hardening_ratio * e_mod
            s = np.where(a <= ey, e_mod * a, fy + eh * (a - ey))
            return sgn * s, np.where(a <= ey, e_mod, eh)
        c = self.n / (1 + self.r)
        # Newton from above on a convex, increasing function: monotone convergence
        s = np.minimum(e_mod * a, fy * (e_mod * a / (c * fy)) ** (1 / (self.r + 1)))
        for _ in range(60):
            pw = (s / fy) ** self.r
            g = s / e_mod * (1 + c * pw) - a
            dg = (1 + c * (1 + self.r) * pw) / e_mod
            s = np.maximum(s - g / dg, 0.0)
            if np.max(np.abs(g), initial=0.0) <= 1e-13 + 1e-10 * np.max(a, initial=0.0):
                break
        pw = (s / fy) ** self.r
        et = e_mod / (1 + c * (1 + self.r) * pw)
        return sgn * s, et

    def strain_of(self, stress_pa: float) -> float:
        """Strain at a given stress (used for an initial axial stress)."""
        s, e_mod, fy = abs(stress_pa), self.youngs_pa, self.yield_pa
        if self.model == "elastic":
            e = s / e_mod
        elif self.model == "bilinear":
            ey = fy / e_mod
            e = s / e_mod if s <= fy else ey + (s - fy) / (self.hardening_ratio * e_mod or 1e-30)
        else:
            e = s / e_mod * (1 + self.n / (1 + self.r) * (s / fy) ** self.r)
        return math.copysign(e, stress_pa)


# ------------------------------------------------------------------ springs


class SpringSet(_Frozen):
    """Per unit length: ultimate force (N/m) and yield displacement (m) per direction."""

    axial_force: float = Field(gt=0)
    axial_yield: float = Field(gt=0)
    lateral_force: float = Field(gt=0)
    lateral_yield: float = Field(gt=0)
    uplift_force: float = Field(gt=0)
    uplift_yield: float = Field(gt=0)
    bearing_force: float = Field(gt=0)
    bearing_yield: float = Field(gt=0)

    @classmethod
    def from_ala(cls, s: SoilSprings) -> SpringSet:
        return cls(
            axial_force=s.axial.max_force_n_per_m, axial_yield=s.axial.yield_displacement_m,
            lateral_force=s.lateral.max_force_n_per_m,
            lateral_yield=s.lateral.yield_displacement_m,
            uplift_force=s.uplift.max_force_n_per_m, uplift_yield=s.uplift.yield_displacement_m,
            bearing_force=s.bearing.max_force_n_per_m,
            bearing_yield=s.bearing.yield_displacement_m,
        )  # fmt: skip


def _ep_spring(d: np.ndarray, fmax: float, dy: float) -> tuple[np.ndarray, np.ndarray]:
    k = fmax / dy
    f = np.clip(k * d, -fmax, fmax)
    kt = np.where(np.abs(d) < dy, k, 1e-6 * k)  # tiny residual stiffness aids Newton
    return f, kt


# ------------------------------------------------------------------ mesh


def graded_mesh(
    x_min: float,
    x_max: float,
    refine: list[float],
    h_fine: float,
    h_max: float,
    fine_half_width: float,
    growth: float = 0.15,
) -> np.ndarray:
    """Node positions: ``h_fine`` within ``fine_half_width`` of each refine point,
    growing linearly with distance beyond it, capped at ``h_max``."""
    pts = np.asarray(refine or [0.0])
    nodes = [x_min]
    x = x_min
    while x < x_max - 1e-9:
        d = float(np.min(np.abs(pts - x)))
        h = min(h_max, h_fine + growth * max(0.0, d - fine_half_width))
        ahead = pts[pts > x + 1e-9]
        if ahead.size:  # do not jump over a refine zone
            h = min(h, max(h_fine, float(np.min(ahead)) - fine_half_width - x))
        x = min(x + h, x_max)
        if x_max - x < 0.3 * h:
            x = x_max
        nodes.append(x)
    return np.array(nodes)


# ------------------------------------------------------------------ model

GroundField = Callable[[np.ndarray], np.ndarray]
"""x (n,) -> ground displacement (3, n): axial, horizontal, vertical (m)."""


class FeOptions(_Frozen):
    fibers: int = Field(default=24, ge=8, le=96)
    max_steps: int = Field(default=400, ge=1)
    first_step: float = Field(default=0.05, gt=0, le=1)
    min_step: float = Field(default=1e-4, gt=0)
    max_iterations: int = Field(default=25, ge=3)
    tolerance: float = Field(default=1e-6, gt=0, description="relative force residual")
    far_field_springs: bool = Field(
        default=True,
        description="axial spring at each end for the semi-infinite pipe beyond the model, "
        "F = sqrt(2 A E T_u delta) (elastic pipe, full friction)",
    )


class FeHistoryPoint(_Frozen):
    fraction: float
    max_tensile_strain: float
    max_compressive_strain: float


class FeResult(_Frozen):
    converged: bool
    fraction: float
    """Share of the ground movement applied when the analysis stopped (1 = all)."""
    message: str
    x_m: list[float]
    u_m: list[float]
    v_m: list[float]
    w_m: list[float]
    ground_m: list[list[float]]
    x_elem_m: list[float]
    axial_force_n: list[float]
    moment_horizontal_nm: list[float]
    moment_vertical_nm: list[float]
    tensile_strain: list[float]
    """Largest fiber strain in each element (positive = tension)."""
    compressive_strain: list[float]
    """Largest compressive fiber strain in each element, as a positive number."""
    max_tensile_strain: float
    max_compressive_strain: float
    x_max_tensile_m: float
    x_max_compressive_m: float
    max_rotation_rad: float
    end_force_ratio: float
    """End axial force / peak axial force (informative; the far-field springs carry it)."""
    end_strain_ratio: float
    """Axial strain at the model ends / yield strain. Above about 0.8 the elastic
    far-field spring is no longer valid: lengthen the model."""
    history: list[FeHistoryPoint]
    elements: int
    steps: int
    iterations: int

    def fraction_at(self, tensile_limit: float, compressive_limit: float) -> float | None:
        """Load fraction at which a strain limit is first reached (None if never)."""
        prev = FeHistoryPoint(fraction=0.0, max_tensile_strain=0.0, max_compressive_strain=0.0)
        for h in self.history:
            for attr, lim in (("max_tensile_strain", tensile_limit),
                              ("max_compressive_strain", compressive_limit)):  # fmt: skip
                a, b = float(getattr(prev, attr)), float(getattr(h, attr))
                if b >= lim > a:
                    return prev.fraction + (lim - a) / (b - a) * (h.fraction - prev.fraction)
            prev = h
        return None


class PipeSoilModel:
    """Assemble and solve a pipe on nonlinear springs under a ground displacement field."""

    def __init__(
        self,
        pipe: PipeSection,
        springs: SpringSet,
        steel: SteelCurve,
        nodes_x: np.ndarray,
        *,
        initial_axial_stress_pa: float = 0.0,
        options: FeOptions | None = None,
        spring_scale: np.ndarray | None = None,
    ) -> None:
        self.pipe, self.springs, self.steel = pipe, springs, steel
        self.opt = options or FeOptions()
        self.x = np.asarray(nodes_x, dtype=float)
        if np.any(np.diff(self.x) <= 0):
            raise ValueError("node positions must increase")
        self.nn, self.ne = self.x.size, self.x.size - 1
        self.le = np.diff(self.x)
        self.eps0 = steel.strain_of(initial_axial_stress_pa)
        trib = np.zeros(self.nn)
        trib[:-1] += self.le / 2
        trib[1:] += self.le / 2
        if spring_scale is not None:
            if np.shape(spring_scale) != (self.nn,) or np.any(np.asarray(spring_scale) <= 0):
                raise ValueError("spring_scale needs one positive factor per node")
            trib = trib * np.asarray(spring_scale, dtype=float)
        self.trib = trib
        # fibers: ring radius giving the exact tube second moment
        ro, ri = pipe.od_m / 2, pipe.id_m / 2
        rf = math.sqrt((ro**2 + ri**2) / 2)
        phi = (np.arange(self.opt.fibers) + 0.5) * 2 * math.pi / self.opt.fibers
        self.fy, self.fz = rf * np.cos(phi), rf * np.sin(phi)
        self.fa = pipe.steel_area_m2 / self.opt.fibers
        self.ro, self.rf = ro, rf
        self._shape_functions()
        self.dofs = NDOF * np.arange(self.ne)[:, None] + np.arange(2 * NDOF)[None, :]

    # -------------------------------------------------------------- shape
    def _shape_functions(self) -> None:
        xi, le = _GX, self.le[:, None]
        d1 = np.stack([-6 * xi + 6 * xi**2, 1 - 4 * xi + 3 * xi**2,
                       6 * xi - 6 * xi**2, -2 * xi + 3 * xi**2])  # dN/dxi  # fmt: skip
        d2 = np.stack([-6 + 12 * xi, -4 + 6 * xi, 6 - 12 * xi, -2 + 6 * xi])
        ne, ng = self.ne, xi.size
        gv = np.zeros((ne, ng, 2 * NDOF))
        hv = np.zeros((ne, ng, 2 * NDOF))
        gw, hw = np.zeros_like(gv), np.zeros_like(hv)
        # local dof order: u1 v1 v1' w1 w1' u2 v2 v2' w2 w2'
        for (iv, isv, iv2, isv2), (g, h) in (((1, 2, 6, 7), (gv, hv)), ((3, 4, 8, 9), (gw, hw))):
            g[:, :, iv] = d1[0][None, :] / le
            g[:, :, isv] = d1[1][None, :]
            g[:, :, iv2] = d1[2][None, :] / le
            g[:, :, isv2] = d1[3][None, :]
            h[:, :, iv] = d2[0][None, :] / le**2
            h[:, :, isv] = d2[1][None, :] / le
            h[:, :, iv2] = d2[2][None, :] / le**2
            h[:, :, isv2] = d2[3][None, :] / le
        bu = np.zeros((ne, 2 * NDOF))
        bu[:, 0], bu[:, 5] = -1 / self.le, 1 / self.le
        self.gv, self.gw, self.hv, self.hw, self.bu = gv, gw, hv, hw, bu
        self.s_geo = np.einsum("g,egi,egj->eij", _GW, gv, gv) + np.einsum(
            "g,egi,egj->eij", _GW, gw, gw
        )

    # -------------------------------------------------------------- element state
    def _element_state(self, q: np.ndarray, outer: bool = False) -> dict[str, np.ndarray]:
        """Kinematics; ``outer`` gives fiber strains at the outside surface (for reporting)."""
        qe = q[self.dofs]  # (ne, 10)
        vp = np.einsum("egk,ek->eg", self.gv, qe)
        wp = np.einsum("egk,ek->eg", self.gw, qe)
        kv = np.einsum("egk,ek->eg", self.hv, qe)
        kw = np.einsum("egk,ek->eg", self.hw, qe)
        em = np.einsum("ek,ek->e", self.bu, qe) + 0.5 * np.einsum("g,eg->e", _GW, vp**2 + wp**2)
        sc = self.ro / self.rf if outer else 1.0
        eps = (em[:, None, None] + self.eps0 - sc * self.fy[None, None, :] * kv[:, :, None]
               - sc * self.fz[None, None, :] * kw[:, :, None])  # fmt: skip
        return {"vp": vp, "wp": wp, "kv": kv, "kw": kw, "em": em, "eps": eps}

    def _internal(self, q: np.ndarray, tangent: bool) -> tuple[np.ndarray, sp.csr_matrix | None]:
        st = self._element_state(q)
        sig, et = self.steel.stress(st["eps"])
        fa, fy, fz = self.fa, self.fy, self.fz
        n = (sig * fa).sum(-1)
        mv = -(sig * fa * fy).sum(-1)
        mw = -(sig * fa * fz).sum(-1)
        bm = (
            self.bu
            + np.einsum("g,eg,egk->ek", _GW, st["vp"], self.gv)
            + np.einsum("g,eg,egk->ek", _GW, st["wp"], self.gw)
        )
        ne, ng = self.ne, _GX.size
        bg = np.empty((ne, ng, 3, 2 * NDOF))
        bg[:, :, 0, :] = bm[:, None, :]
        bg[:, :, 1, :] = self.hv
        bg[:, :, 2, :] = self.hw
        res = np.stack([n, mv, mw], axis=-1)  # (ne, ng, 3)
        fe = self.le[:, None] * np.einsum("g,egik,egi->ek", _GW, bg, res)
        f = np.zeros(NDOF * self.nn)
        np.add.at(f, self.dofs, fe)
        if not tangent:
            return f, None
        c = np.stack([np.ones_like(fy), -fy, -fz])  # (3, nf)
        dmat = np.einsum("egf,if,jf->egij", et * fa, c, c)
        ke = self.le[:, None, None] * (
            np.einsum("g,egik,egij,egjl->ekl", _GW, bg, dmat, bg)
            + np.einsum("g,eg,ekl->ekl", _GW, n, self.s_geo)
        )
        rows = np.repeat(self.dofs, 2 * NDOF, axis=1).ravel()
        cols = np.tile(self.dofs, (1, 2 * NDOF)).ravel()
        k = sp.coo_matrix((ke.ravel(), (rows, cols)), shape=(f.size, f.size)).tocsr()
        return f, k

    def _soil(self, q: np.ndarray, ground: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        s, t = self.springs, self.trib
        du = q[0::NDOF] - ground[0]
        dv = q[1::NDOF] - ground[1]
        dw = q[3::NDOF] - ground[2]
        fu, ku = _ep_spring(du, s.axial_force, s.axial_yield)
        fv, kv = _ep_spring(dv, s.lateral_force, s.lateral_yield)
        up = dw >= 0
        fw_up, kw_up = _ep_spring(dw, s.uplift_force, s.uplift_yield)
        fw_dn, kw_dn = _ep_spring(dw, s.bearing_force, s.bearing_yield)
        fw, kw = np.where(up, fw_up, fw_dn), np.where(up, kw_up, kw_dn)
        f = np.zeros(NDOF * self.nn)
        kd = np.zeros_like(f)
        f[0::NDOF], f[1::NDOF], f[3::NDOF] = fu * t, fv * t, fw * t
        kd[0::NDOF], kd[1::NDOF], kd[3::NDOF] = ku * t, kv * t, kw * t
        return f, kd

    # -------------------------------------------------------------- solve
    def solve(self, ground_field: GroundField) -> FeResult:
        opt = self.opt
        g_full = np.asarray(ground_field(self.x), dtype=float)
        if g_full.shape != (3, self.nn):
            raise ValueError("ground field must return an array of shape (3, n)")
        nd = NDOF * self.nn
        ends = np.r_[np.arange(NDOF), nd - NDOF + np.arange(NDOF)]
        # with far-field springs the end axial dofs are free (held by those springs)
        fixed = ends[[1, 2, 3, 4, 6, 7, 8, 9]] if opt.far_field_springs else ends
        free = np.setdiff1d(np.arange(nd), fixed)

        def prescribed(lam: float) -> np.ndarray:
            out = np.zeros(ends.size)
            for k, end in enumerate((0, self.nn - 1)):
                out[NDOF * k + 0] = lam * g_full[0, end]
                out[NDOF * k + 1] = lam * g_full[1, end]
                out[NDOF * k + 3] = lam * g_full[2, end]
            return out[np.isin(ends, fixed)]

        ae = self.pipe.steel_area_m2 * self.steel.youngs_pa
        ff = math.sqrt(2 * ae * self.springs.axial_force)
        d0 = self.springs.axial_yield

        def far_field(q: np.ndarray, lam: float) -> tuple[np.ndarray, np.ndarray]:
            f, kd = np.zeros(nd), np.zeros(nd)
            if not opt.far_field_springs:
                return f, kd
            for dof, end in ((0, 0), (nd - NDOF, self.nn - 1)):
                d = q[dof] - lam * g_full[0, end]
                a = abs(d)
                if a < d0:
                    f[dof], kd[dof] = ff / math.sqrt(d0) * d, ff / math.sqrt(d0)
                else:
                    f[dof], kd[dof] = math.copysign(ff * math.sqrt(a), d), ff / (2 * math.sqrt(a))
            return f, kd

        def residual(q: np.ndarray, lam: float, tangent: bool):  # type: ignore[no-untyped-def]
            fi, k = self._internal(q, tangent)
            fs, kd = self._soil(q, lam * g_full)
            fe_, ke_ = far_field(q, lam)
            fs, kd = fs + fe_, kd + ke_
            r = fi + fs
            if tangent:
                assert k is not None
                k = k + sp.diags(kd)
            return r, k, np.abs(fi).max(initial=0.0) + np.abs(fs).max(initial=0.0)

        q = np.zeros(nd)
        rate = np.zeros(nd)  # dq/dlambda of the last converged step (predictor)
        lam, dlam = 0.0, opt.first_step
        history: list[FeHistoryPoint] = []
        steps = iters = 0
        msg, ok = "converged", True
        while lam < 1 - 1e-12 and steps < opt.max_steps:
            target = min(1.0, lam + dlam)
            trial = q + (target - lam) * rate  # secant predictor
            trial[fixed] = prescribed(target)
            conv = small_step = False
            for it in range(opt.max_iterations):
                r, k, ref = residual(trial, target, True)
                rn = float(np.abs(r[free]).max(initial=0.0))
                tol = opt.tolerance * max(ref, 1.0)
                if it > 0 and (rn <= tol or (small_step and rn <= 100 * tol)):
                    conv = True
                    break
                assert k is not None
                kff = k[free][:, free].tocsc()
                try:
                    dq = spla.spsolve(kff, -r[free])
                except RuntimeError:
                    break
                if not np.all(np.isfinite(dq)):
                    break
                step = 1.0
                for _ in range(6):  # backtracking line search on the residual
                    cand = trial.copy()
                    cand[free] += step * dq
                    rc, _, _ = residual(cand, target, False)
                    if (
                        float(np.abs(rc[free]).max(initial=0.0)) <= max(rn, 1e-12) * 1.5
                        or step < 0.1
                    ):
                        break
                    step *= 0.5
                trial = cand
                small_step = float(np.abs(step * dq).max(initial=0.0)) < 1e-9
                iters += 1
            if conv:
                rate = (trial - q) / (target - lam)
                q, lam = trial, target
                steps += 1
                st = self._element_state(q, outer=True)
                history.append(FeHistoryPoint(
                    fraction=lam,
                    max_tensile_strain=max(float(st["eps"].max()), 0.0),
                    max_compressive_strain=max(float(-st["eps"].min()), 0.0),
                ))  # fmt: skip
                dlam = min(dlam * (1.5 if it <= 6 else 1.0), 0.25)
            else:
                dlam *= 0.5
                if dlam < opt.min_step:
                    ok, msg = False, f"did not converge beyond {lam:.3f} of the ground movement"
                    break
        if lam < 1 - 1e-12 and ok:
            ok, msg = False, f"step limit reached at {lam:.3f} of the ground movement"
        return self._result(q, lam, g_full, history, ok, msg, steps, iters)

    def _result(self, q: np.ndarray, lam: float, g_full: np.ndarray,
                history: list[FeHistoryPoint], ok: bool, msg: str, steps: int,
                iters: int) -> FeResult:  # fmt: skip
        st = self._element_state(q)
        sig, _ = self.steel.stress(st["eps"])
        fa = self.fa
        n = (sig * fa).sum(-1)
        mv = -(sig * fa * self.fy).sum(-1)
        mw = -(sig * fa * self.fz).sum(-1)
        eps = self._element_state(q, outer=True)["eps"]
        e_t = eps.max(axis=(1, 2))
        e_c = -eps.min(axis=(1, 2))
        xm = self.x[:-1] + self.le / 2
        nmid = n[:, 1]
        peak_n = float(np.abs(nmid).max(initial=0.0))
        end_n = max(abs(float(nmid[0])), abs(float(nmid[-1])))
        rot = float(max(np.abs(st["vp"]).max(initial=0), np.abs(st["wp"]).max(initial=0)))
        if ok and rot > 0.3:
            msg = f"converged; rotation {rot:.2f} rad exceeds the moderate-rotation range"
        it, ic = int(np.argmax(e_t)), int(np.argmax(e_c))
        return FeResult(
            converged=ok, fraction=lam, message=msg,
            x_m=self.x.tolist(), u_m=q[0::NDOF].tolist(), v_m=q[1::NDOF].tolist(),
            w_m=q[3::NDOF].tolist(), ground_m=(lam * g_full).tolist(),
            x_elem_m=xm.tolist(), axial_force_n=nmid.tolist(),
            moment_horizontal_nm=mv[:, 1].tolist(), moment_vertical_nm=mw[:, 1].tolist(),
            tensile_strain=e_t.tolist(), compressive_strain=e_c.tolist(),
            max_tensile_strain=max(float(e_t.max()), 0.0),
            max_compressive_strain=max(float(e_c.max()), 0.0),
            x_max_tensile_m=float(xm[it]), x_max_compressive_m=float(xm[ic]),
            max_rotation_rad=rot,
            end_force_ratio=end_n / peak_n if peak_n > 0 else 0.0,
            end_strain_ratio=end_n / self.pipe.steel_area_m2 / self.steel.yield_pa,
            history=history, elements=self.ne, steps=steps, iterations=iters,
        )  # fmt: skip
