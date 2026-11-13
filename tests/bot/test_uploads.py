"""File uploads: PDF, DOCX, images, KML/KMZ, text; and attachment flow through the bot."""

import io
import math
import zipfile
from typing import Any, ClassVar

from pipeline_analytics.bot.agent import FeasibilityBot
from pipeline_analytics.bot.providers import Block
from pipeline_analytics.bot.uploads import parse_upload, upload_blocks

KML = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>Proposed route</name>
<LineString><coordinates>90.0,22.0,0 90.0,23.0,0</coordinates></LineString></Placemark>
<Placemark><name>Compressor site</name>
<Point><coordinates>90.5,22.5,0</coordinates></Point></Placemark>
</Document></kml>"""


def tiny_pdf(text: bytes) -> bytes:
    """A minimal but well-formed one-page PDF with real xref offsets."""
    stream = b"BT /F1 12 Tf 72 712 Td (" + text + b") Tj ET"
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj".encode() + body + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    return out + (
        b"trailer<</Size " + str(len(objs) + 1).encode() + b"/Root 1 0 R>>\n"
        b"startxref\n" + str(xref).encode() + b"\n%%EOF"
    )


MINIMAL_PDF = tiny_pdf(b"MAOP 92 barg, wall 9.5 mm")


def test_kml_route_summary() -> None:
    u = parse_upload("route.kml", KML)
    assert u.ok and "Proposed route" in u.text
    # one degree of latitude is ~111.2 km
    km = float(u.text.split("vertices, ")[1].split(" km")[0])
    assert math.isclose(km, 111.2, rel_tol=0.01)
    assert "Compressor site: point at 22.50000 N, 90.50000 E" in u.text
    assert "not a surveyed alignment" in u.text


def test_kmz_unwraps_kml_and_rejects_empty() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("doc.kml", KML)
    u = parse_upload("route.kmz", buf.getvalue())
    assert u.ok and "Proposed route" in u.text
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", "x")
    assert not parse_upload("route.kmz", empty.getvalue()).ok


def test_pdf_docx_text_image_and_unknown() -> None:
    pdf = parse_upload("note.pdf", MINIMAL_PDF)
    assert pdf.ok and "MAOP 92 barg" in pdf.text

    import docx

    d = docx.Document()
    d.add_paragraph("Throughput 180 MMSCFD")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text = "NPS"
    t.rows[0].cells[1].text = "24"
    buf = io.BytesIO()
    d.save(buf)
    w = parse_upload("brief.docx", buf.getvalue())
    assert w.ok and "Throughput 180 MMSCFD" in w.text and "NPS | 24" in w.text

    img = parse_upload("map.png", b"\x89PNG fake")
    assert img.kind == "image" and img.media_type == "image/png" and img.data_b64
    assert not parse_upload("huge.png", b"x" * 5_000_000).ok
    assert parse_upload("notes.txt", b"pressure 62 barg").text == "pressure 62 barg"
    assert not parse_upload("model.step", b"x").ok
    assert not parse_upload("broken.pdf", b"not a pdf").ok  # error result, no exception


def test_attachments_reach_the_model_and_the_guardrail() -> None:
    sent: dict[str, Any] = {}

    class C:
        class beta:  # noqa: N801
            class messages:  # noqa: N801
                @staticmethod
                def create(**kw: object) -> object:
                    sent.update(kw)

                    class R:
                        content: ClassVar = [
                            Block(type="text", text="The note gives MAOP 92 barg and wall 9.5 mm.")
                        ]
                        stop_reason = "end_turn"

                    return R()

    bot = FeasibilityBot(C())
    ups = [parse_upload("note.pdf", MINIMAL_PDF), parse_upload("map.png", b"\x89PNG")]
    reply = bot.ask("What does the note say?", attachments=ups)
    content = sent["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "What does the note say?"}
    assert any("MAOP 92 barg" in b.get("text", "") for b in content[1:] if b["type"] == "text")
    assert any(b["type"] == "image" for b in content)
    assert reply.unsupported == []  # 92 and 9.5 are supported by the attachment text


def test_upload_blocks_report_errors_inline() -> None:
    bad = parse_upload("x.step", b"")
    (block,) = upload_blocks([bad])
    assert "could not be read" in str(block["text"])
