"""Report charts drawn with Pillow (no plotting library needed).

Colours follow the report palette: navy for CAPEX, teal for OPEX, amber marks
the optimum.
"""

from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw, ImageFont

from pipeline_analytics.reports.model import StudyReport

NAVY = (18, 50, 79)
TEAL = (11, 122, 117)
AMBER = (232, 163, 23)
INK = (15, 31, 51)
MUTED = (74, 91, 112)
GRID = (201, 210, 220)
WHITE = (255, 255, 255)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _nice_max(v: float) -> tuple[float, float]:
    """Axis maximum and tick step covering ``v``."""
    if v <= 0:
        return 1.0, 0.2
    mag = 10 ** math.floor(math.log10(v))
    for step in (0.2, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0):
        s = step * mag
        if v / s <= 6:
            return math.ceil(v / s) * s, s
    return math.ceil(v / (10 * mag)) * 10 * mag, 10 * mag


def lifecycle_cost_png(report: StudyReport, scale: int = 2) -> bytes:
    """Stacked PV CAPEX + PV OPEX bars per pipe size, optimum marked. Returns PNG bytes."""
    rows = [r for r in report.options if r.feasible]
    w, h = 900 * scale, 420 * scale
    left, right, top, bottom = 80 * scale, 20 * scale, 50 * scale, 80 * scale
    img = Image.new("RGB", (w, h), WHITE)
    d = ImageDraw.Draw(img)
    f_ax, f_lab, f_t = _font(13 * scale), _font(13 * scale), _font(15 * scale)
    vmax, step = _nice_max(max((r.pv_capex + r.pv_opex for r in rows), default=1.0) * 1.08)

    def y(v: float) -> float:
        return top + (h - top - bottom) * (1 - v / vmax)

    d.text(
        (left, 12 * scale),
        f"Lifecycle cost by pipe size ({report.money.label}, PV)",
        fill=INK,
        font=f_t,
    )
    v = 0.0
    while v <= vmax + 1e-9:
        d.line([(left, y(v)), (w - right, y(v))], fill=GRID, width=scale)
        d.text((left - 8 * scale, y(v)), f"{v:g}", fill=MUTED, font=f_ax, anchor="rm")
        v += step
    if rows:
        cw = (w - left - right) / len(rows)
        bw = cw * 0.55
        for i, r in enumerate(rows):
            x0 = left + i * cw + (cw - bw) / 2
            d.rectangle([x0, y(r.pv_capex), x0 + bw, y(0)], fill=NAVY)
            d.rectangle([x0, y(r.pv_capex + r.pv_opex), x0 + bw, y(r.pv_capex)], fill=TEAL)
            d.text(
                (x0 + bw / 2, y(r.pv_capex + r.pv_opex) - 4 * scale),
                f"{r.lcc:,.1f}",
                fill=INK,
                font=f_lab,
                anchor="md",
            )
            d.text(
                (x0 + bw / 2, h - bottom + 10 * scale),
                f'{r.nps}"',
                fill=MUTED,
                font=f_ax,
                anchor="mt",
            )
            if r.nps == report.best.nps:
                d.rectangle(
                    [
                        x0 - 3 * scale,
                        h - bottom + 30 * scale,
                        x0 + bw + 3 * scale,
                        h - bottom + 36 * scale,
                    ],
                    fill=AMBER,
                )
    lx, ly = left, h - 26 * scale
    for colour, label in ((NAVY, "PV CAPEX"), (TEAL, "PV OPEX"), (AMBER, "Lowest lifecycle cost")):
        d.rectangle([lx, ly - 6 * scale, lx + 12 * scale, ly + 6 * scale], fill=colour)
        d.text((lx + 18 * scale, ly), label, fill=MUTED, font=f_ax, anchor="lm")
        lx += (len(label) * 8 + 50) * scale
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
