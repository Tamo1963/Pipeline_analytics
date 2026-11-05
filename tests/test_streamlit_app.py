from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "apps" / "streamlit" / "app.py"


def test_app_renders_gas_basis() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception
    assert any("OISD-STD-226" in m.value for m in at.markdown)


def test_app_switches_to_liquid() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.radio[0].set_value("liquid").run()
    assert not at.exception
    assert any("OISD-STD-141" in m.value for m in at.markdown)


def test_app_liquid_mass_to_volume_uses_density() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.radio[0].set_value("liquid").run()
    # default: 1 MMTPA -> bpd at 850 kg/m3
    expected = 1e9 / (365.25 * 850) / 0.158987295
    assert at.metric[0].value == f"{expected:,.4g} bpd"


def test_library_tab_renders() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception
    assert any("OD 610.0 mm" in m.value for m in at.markdown)  # default NPS 24
    assert at.metric[1].value == "12.7 mm"  # 10 mm -> next schedule WT


def test_library_tab_increment_policy() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.radio(key="lib_policy").set_value("increment").run()
    assert at.metric[1].value == "10 mm"


def test_mechanical_tab_default_passes() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception
    result = next(m for m in at.metric if m.label == "Result")
    assert result.value == "PASS"
    assert "hoop stress" in result.delta


def test_mechanical_tab_buoyancy_fails_and_sizes_concrete() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.checkbox(key="m_buoy").check().run()
    result = next(m for m in at.metric if m.label == "Result")
    assert result.value == "FAIL"
    assert any("Concrete weight coating needed: 65 mm" in i.value for i in at.info)


def test_hydraulics_tab_gas_default() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception
    stations = next(m for m in at.metric if m.label == "Stations")
    assert int(stations.value) >= 1
    assert any("Class 3" in m.value for m in at.markdown)


def test_hydraulics_tab_liquid() -> None:
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    at.selectbox(key="h_service").set_value("liquid").run()
    assert not at.exception
    delivery = next(m for m in at.metric if m.label == "Delivery pressure (barg)")
    assert float(delivery.value) >= 2.0


def test_economics_tab_renders_with_placeholder_warning() -> None:
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    assert any("PLACEHOLDER unit rates" in w.value for w in at.warning)
    best = next(m for m in at.metric if m.label == "Lowest lifecycle cost")
    assert best.value.endswith('"')


def test_route_tab_finds_corridors() -> None:
    at = AppTest.from_file(str(APP), default_timeout=90).run()
    assert not at.exception
    assert not [e.value for e in at.error]
    assert len(at.dataframe) >= 1


def test_bot_tab_explains_missing_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception
    at.chat_input(key="bot_input").set_value("Wall for 24 inch X70 at 92 barg?").run()
    assert not at.exception
    assert any("Claude API call failed" in e.value for e in at.error)


def test_report_downloads_and_route_map() -> None:
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    assert not at.exception
    labels = [d.proto.label for d in at.get("download_button")]
    assert "Download report (Word)" in labels and "Download workbook (Excel)" in labels
    assert len(at.get("deck_gl_json_chart")) == 1  # corridor map


def test_economics_risk_button_and_rate_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipeline_analytics.cost.rate_library import RateLibrary, escalate
    from pipeline_analytics.cost.rates import placeholder_rates

    monkeypatch.setenv("PIPELINE_RATES_DIR", str(tmp_path))
    RateLibrary(tmp_path).save("plus2", escalate(placeholder_rates(), 2028, 0.05))
    at = AppTest.from_file(str(APP), default_timeout=300).run()
    assert at.selectbox(key="e_rateset").options == ["placeholder", "plus2"]
    at.multiselect(key="e_sizes").set_value(["20", "24", "28"]).run()
    at.select_slider(key="e_samples").set_value(2000).run()
    at.button(key="e_risk").click().run()
    assert not at.exception
    top = next(m for m in at.metric if m.label == "Most often cheapest")
    assert top.value.endswith("barg") and "of samples" in top.delta


def test_projects_tab_local_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPELINE_PROJECTS_DIR", str(tmp_path))
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert any("Local mode" in w.value for w in at.warning)
    at.text_input(key="p_user").set_value("asha@example.com").run()
    at.text_input(key="p_new_name").set_value("Demo line").run()
    at.button(key="p_create").click().run()
    assert not at.exception
    assert any("Created Demo line" in s.value for s in at.success)
    at.text_input(key="p_msg").set_value("second").run()
    at.button(key="p_save").click().run()
    assert any("Saved version 2" in s.value for s in at.success)
    at.button(key="p_load").click().run()
    assert any("Opened version 2" in s.value for s in at.success)
    from pipeline_analytics.projects import ProjectStore

    (p,) = ProjectStore(tmp_path).list_for("asha@example.com")
    assert p.latest_version == 2 and p.owner == "asha@example.com"
