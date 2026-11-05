"""Export corridors as GeoJSON (projected) or KML (WGS84, needs the ``gis`` extra)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from xml.sax.saxutils import escape

XY = tuple[float, float]


def to_geojson(corridors: Sequence[tuple[str, Sequence[XY], dict[str, Any]]], crs: str) -> str:
    """FeatureCollection of LineStrings in the grid CRS (``name``, coords, properties)."""
    feats = [
        {
            "type": "Feature",
            "properties": {"name": name, **props},
            "geometry": {"type": "LineString", "coordinates": [list(p) for p in coords]},
        }
        for name, coords, props in corridors
    ]
    return json.dumps(
        {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": crs}},
            "features": feats,
        },
        indent=1,
    )


def to_wgs84(coords: Sequence[XY], crs: str) -> list[XY]:
    try:
        from pyproj import Transformer
    except ImportError as e:  # pragma: no cover
        raise ImportError("KML export needs pyproj: install the 'gis' extra") from e
    tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    return [tuple(tr.transform(x, y)) for x, y in coords]


def to_kml(corridors: Sequence[tuple[str, Sequence[XY], dict[str, Any]]], crs: str) -> str:
    """KML document with one Placemark per corridor (lon/lat)."""
    marks = []
    for name, coords, props in corridors:
        desc = "<br/>".join(f"{escape(str(k))}: {escape(str(v))}" for k, v in props.items())
        ll = " ".join(f"{lon:.7f},{lat:.7f},0" for lon, lat in to_wgs84(coords, crs))
        marks.append(
            f"<Placemark><name>{escape(name)}</name><description>{desc}</description>"
            f"<LineString><tessellate>1</tessellate><coordinates>{ll}</coordinates>"
            "</LineString></Placemark>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        + "".join(marks)
        + "</Document></kml>\n"
    )
