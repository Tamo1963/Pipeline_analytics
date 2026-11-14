from datetime import date
from pathlib import Path

import pytest

from pipeline_analytics.cost.cli import main
from pipeline_analytics.cost.rate_library import (
    PLACEHOLDER,
    RateLibrary,
    diff_rates,
    escalate,
    validate_rates,
)
from pipeline_analytics.cost.rates import UnitRates, placeholder_rates


def real(r: UnitRates, **upd: object) -> UnitRates:
    d = r.model_dump()
    d["placeholder"] = False
    d.update(upd)
    return UnitRates.model_validate(d)


def test_placeholder_always_listed_and_protected(tmp_path: Path) -> None:
    lib = RateLibrary(tmp_path)
    assert lib.names() == [PLACEHOLDER]
    assert lib.load(PLACEHOLDER) == placeholder_rates()
    with pytest.raises(ValueError, match="cannot be overwritten"):
        lib.save(PLACEHOLDER, placeholder_rates())


def test_save_load_round_trip_and_overwrite_guard(tmp_path: Path) -> None:
    lib = RateLibrary(tmp_path)
    r = real(placeholder_rates(), source="Contractor quotes 2026")
    p = lib.save("project_a", r)
    assert p.read_text().startswith("# Unit-rate set")
    assert lib.load("project_a") == r
    assert lib.load(p) == r  # by path too
    with pytest.raises(FileExistsError):
        lib.save("project_a", r)
    lib.save("project_a", r, overwrite=True)
    assert not list(tmp_path.glob(".*.tmp"))  # no temp files left behind
    assert [i.name for i in lib.list()] == [PLACEHOLDER, "project_a"]
    with pytest.raises(ValueError, match="invalid rate-set name"):
        lib.path("../escape")


def test_old_yaml_without_meta_still_loads() -> None:
    d = placeholder_rates().model_dump()
    del d["meta"]
    assert UnitRates.model_validate(d).meta.approved_by == ""


def test_validate_flags_gaps_outliers_and_status() -> None:
    ref = placeholder_rates()
    assert any(f.field == "placeholder" for f in validate_rates(ref, today=date(2026, 6, 1)))
    d = real(ref).model_dump()
    d["laying_per_inch_km"] *= 10
    del d["terrain_factor"]["marsh"]
    d["base_year"] = 2020
    r = UnitRates.model_validate(d)
    found = {f.field: f for f in validate_rates(r, reference=ref, today=date(2026, 6, 1))}
    assert found["laying_per_inch_km"].level == "warning"
    assert found["terrain_factor.marsh"].level == "error"
    assert "base_year" in found and "meta.approved_by" in found


def test_diff_and_escalate() -> None:
    base = placeholder_rates()
    up = escalate(base, base.base_year + 2, 0.05, {"energy": 0.10})
    assert up.base_year == base.base_year + 2
    assert up.line_pipe.base_rate_per_tonne == pytest.approx(
        base.line_pipe.base_rate_per_tonne * 1.05**2
    )
    assert up.opex.electricity_per_kwh == pytest.approx(base.opex.electricity_per_kwh * 1.1**2)
    assert up.indirects == base.indirects  # percentages do not escalate
    assert "+5.0%/yr" in up.meta.escalated_from
    changes = {c.field: c for c in diff_rates(base, up)}
    assert changes["line_pipe.base_rate_per_tonne"].ratio == pytest.approx(1.1025)
    assert "indirects.contingency" not in changes
    with pytest.raises(KeyError):
        escalate(base, 2030, 0.05, {"steel": 0.1})


def test_cli_workflow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = ["--root", str(tmp_path)]
    assert main([*root, "new", "proj", "--region", "Gujarat"]) == 0
    assert main([*root, "approve", "proj", "--by", "QS"]) == 2  # still placeholder values
    p = tmp_path / "proj.yaml"
    p.write_text(p.read_text().replace("placeholder: true", "placeholder: false"))
    assert main([*root, "approve", "proj", "--by", "QS", "--on", "2026-11-01"]) == 0
    assert main([*root, "escalate", "proj", "proj_2028", "--to-year", "2028", "--annual",
                 "0.04", "--group", "energy=0.06"]) == 0  # fmt: skip
    assert main([*root, "validate", "proj_2028"]) == 0
    capsys.readouterr()
    assert main([*root, "list"]) == 0
    listing = capsys.readouterr().out
    assert "proj " in listing and "approved" in listing and "draft" in listing
    assert main([*root, "diff", "proj", "proj_2028"]) == 0
    assert "base_year: 2026 -> 2028" in capsys.readouterr().out
    assert main([*root, "show", "missing"]) == 2
