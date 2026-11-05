import pytest

from pipeline_analytics.pipespec.cli import main


def run(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    assert main(list(args)) == 0
    return capsys.readouterr().out


def test_grades(capsys: pytest.CaptureFixture[str]) -> None:
    out = run(capsys, "grades", "--psl", "2", "--min-smys", "485")
    assert "X70" in out and "X65" not in out


def test_grade(capsys: pytest.CaptureFixture[str]) -> None:
    out = run(capsys, "grade", "L485M")
    assert "SMYS 485 MPa (max 635)" in out


def test_sizes(capsys: pytest.CaptureFixture[str]) -> None:
    assert "1219.0" in run(capsys, "sizes")


def test_wt(capsys: pytest.CaptureFixture[str]) -> None:
    out = run(capsys, "wt", "1-1/4")
    assert "OD 42.2 mm" in out and "40/STD" in out


def test_next_wt(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(capsys, "next-wt", "24", "11.2").startswith("12.7 mm")
    assert run(capsys, "next-wt", "24", "11.23", "--policy", "increment").startswith("11.3 mm")


def test_find(capsys: pytest.CaptureFixture[str]) -> None:
    out = run(capsys, "find", "36", "--min-grade", "X65", "--process", "SAWH", "--max-wt", "15.9")
    assert out.startswith("24 options")


def test_error_exit(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        main(["grade", "X99"])
    assert e.value.code == 2
    assert "no API-5L grade" in capsys.readouterr().err
