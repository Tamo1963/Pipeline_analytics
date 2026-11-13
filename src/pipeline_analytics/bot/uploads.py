"""Parse user file uploads for the bot and the wiki.

Supported:

* **PDF** (text extraction, ``pypdf``)
* **DOCX** (paragraphs and tables, ``python-docx``)
* **Images** (png/jpg/jpeg/gif/webp: passed to the model as image blocks)
* **KMZ / KML** (Google Earth): placemarks are summarised - paths with their
  lengths (WGS84 great-circle) and start/end coordinates, points, polygons -
  so a drawn route can seed ``define_route``
* **TXT / MD / CSV** (as text)

Extraction never truncates silently: long text is cut at ``MAX_CHARS`` with an
explicit note. Parsing failures come back as ``kind="error"`` with a message,
never as an exception.
"""

from __future__ import annotations

import base64
import io
import itertools
import math
import re
import zipfile
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree

MAX_CHARS = 60_000
MAX_IMAGE_BYTES = 4_500_000
IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
               "gif": "image/gif", "webp": "image/webp"}  # fmt: skip
TEXT_TYPES = {"txt", "md", "csv"}
EARTH_R_M = 6_371_008.8


@dataclass(frozen=True)
class Upload:
    name: str
    kind: str  # "text" | "image" | "error"
    text: str = ""
    media_type: str = ""
    data_b64: str = ""

    @property
    def ok(self) -> bool:
        return self.kind != "error"

    @property
    def summary(self) -> str:
        if self.kind == "error":
            return f"{self.name}: {self.text}"
        if self.kind == "image":
            return f"{self.name}: image ({self.media_type})"
        return f"{self.name}: {len(self.text):,} characters extracted"


def _clip(text: str, name: str) -> str:
    text = text.strip()
    if len(text) <= MAX_CHARS:
        return text
    return (
        text[:MAX_CHARS] + f"\n\n[{name}: truncated at {MAX_CHARS:,} characters of "
        f"{len(text):,}; ask for a specific section if more is needed]"
    )


def parse_upload(name: str, data: bytes) -> Upload:
    """Parse one uploaded file by its extension."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    try:
        if ext == "pdf":
            return Upload(name, "text", _clip(_pdf_text(data), name))
        if ext == "docx":
            return Upload(name, "text", _clip(_docx_text(data), name))
        if ext in IMAGE_TYPES:
            if len(data) > MAX_IMAGE_BYTES:
                return Upload(name, "error",
                              f"image is {len(data) / 1e6:.1f} MB; resize it below "
                              f"{MAX_IMAGE_BYTES / 1e6:.1f} MB")  # fmt: skip
            return Upload(name, "image", media_type=IMAGE_TYPES[ext],
                          data_b64=base64.standard_b64encode(data).decode())  # fmt: skip
        if ext == "kmz":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                kmls = [n for n in z.namelist() if n.lower().endswith(".kml")]
                if not kmls:
                    return Upload(name, "error", "KMZ archive holds no .kml file")
                return Upload(name, "text", _clip(kml_summary(z.read(kmls[0])), name))
        if ext == "kml":
            return Upload(name, "text", _clip(kml_summary(data), name))
        if ext in TEXT_TYPES:
            return Upload(name, "text", _clip(data.decode("utf-8", errors="replace"), name))
        return Upload(name, "error", f"unsupported file type .{ext} "
                      "(pdf, docx, png/jpg/gif/webp, kml, kmz, txt, md, csv)")  # fmt: skip
    except ImportError as e:
        return Upload(name, "error", f"missing parser dependency: {e}")
    except Exception as e:  # zip/xml/pdf corruption and the like
        return Upload(name, "error", f"could not parse: {e}")


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages, 1):
        t = (page.extract_text() or "").strip()
        if t:
            pages.append(f"[page {i}]\n{t}")
    if not pages:
        return "(no extractable text: the PDF appears to be scanned images)"
    return "\n\n".join(pages)


def _docx_text(data: bytes) -> str:
    import docx

    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for table in d.tables:
        rows = [" | ".join(c.text.strip() for c in row.cells) for row in table.rows]
        parts.append("\n".join(rows))
    return "\n".join(parts)


# ------------------------------------------------------------------ KML / KMZ


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    (lon1, lat1), (lon2, lat2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl, dp = math.radians(lon2 - lon1), p2 - p1
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(h))


def _coords(text: str) -> list[tuple[float, float]]:
    out = []
    for token in text.split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                out.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def kml_summary(data: bytes) -> str:
    """Placemarks of a KML document as text: paths with lengths, points, polygons."""
    root = ElementTree.fromstring(data)
    lines: list[str] = ["KML map content:"]
    n = 0
    for pm in root.iter():
        if _local(pm.tag) != "Placemark":
            continue
        n += 1
        name = desc = ""
        geoms: list[str] = []
        for el in pm.iter():
            tag = _local(el.tag)
            if tag == "name" and not name:
                name = (el.text or "").strip()
            elif tag == "description" and not desc:
                desc = re.sub(r"<[^>]+>", " ", el.text or "").strip()[:200]
            elif tag in ("LineString", "LinearRing", "Point"):
                coord_el = next((c for c in el.iter() if _local(c.tag) == "coordinates"), None)
                pts = _coords(coord_el.text or "") if coord_el is not None else []
                if not pts:
                    continue
                if tag == "Point":
                    geoms.append(f"point at {pts[0][1]:.5f} N, {pts[0][0]:.5f} E")
                else:
                    length = sum(_haversine_m(a, b) for a, b in itertools.pairwise(pts))
                    kind = "polygon ring" if tag == "LinearRing" else "path"
                    geoms.append(
                        f"{kind}: {len(pts)} vertices, {length / 1000:.1f} km, from "
                        f"{pts[0][1]:.5f} N, {pts[0][0]:.5f} E to "
                        f"{pts[-1][1]:.5f} N, {pts[-1][0]:.5f} E"
                    )
        entry = f"- {name or f'placemark {n}'}: " + ("; ".join(geoms) if geoms else "no geometry")
        if desc:
            entry += f" ({desc})"
        lines.append(entry)
    if n == 0:
        return "KML map content: no placemarks found"
    paths = [ln for ln in lines if "path:" in ln]
    if paths:
        lines.append(f"{n} placemark(s), {len(paths)} path(s). Lengths are great-circle "
                     "over the drawn vertices, not a surveyed alignment.")  # fmt: skip
    return "\n".join(lines)


def upload_blocks(uploads: object) -> list[dict[str, Any]]:
    """Content blocks (internal format) for parsed uploads, errors included."""
    blocks: list[dict[str, Any]] = []
    for u in uploads:  # type: ignore[attr-defined]
        if u.kind == "image":
            blocks.append({"type": "image", "source": {"type": "base64",
                                                       "media_type": u.media_type,
                                                       "data": u.data_b64}})  # fmt: skip
            blocks.append({"type": "text", "text": f"[attached image: {u.name}]"})
        elif u.kind == "text":
            blocks.append({"type": "text",
                           "text": f"[attached file {u.name}]\n{u.text}"})  # fmt: skip
        else:
            note = f"[attachment {u.name} could not be read: {u.text}]"
            blocks.append({"type": "text", "text": note})
    return blocks
