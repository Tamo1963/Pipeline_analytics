from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "apps" / "streamlit" / "app.py"


def test_app_renders_gas_basis() -> None:
    at = AppTest.from_file(str(APP)).run()
    assert not at.exception
    assert any("OISD-STD-226" in m.value for m in at.markdown)


def test_app_switches_to_liquid() -> None:
    at = AppTest.from_file(str(APP)).run()
    at.radio[0].set_value("liquid").run()
    assert not at.exception
    assert any("OISD-STD-141" in m.value for m in at.markdown)


def test_app_liquid_mass_to_volume_uses_density() -> None:
    at = AppTest.from_file(str(APP)).run()
    at.radio[0].set_value("liquid").run()
    # default: 1 MMTPA -> bpd at 850 kg/m3
    expected = 1e9 / (365.25 * 850) / 0.158987295
    assert at.metric[0].value == f"{expected:,.4g} bpd"
