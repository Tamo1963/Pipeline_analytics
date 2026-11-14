import pytest
from pydantic import ValidationError

from pipeline_analytics.economics import CashFlow, Concession, build_cashflow, irr, tornado

C = Concession(
    construction_years=2, capex_phasing=(0.4, 0.6), operating_years=10, discount_rate=0.10
)


def flows(**kw: object) -> CashFlow:
    args: dict[str, object] = {
        "concession": C,
        "initial_capex": 1000.0,
        "opex_by_op_year": [50.0] * 10,
        "throughput_by_op_year": [100.0] * 10,
    }
    args.update(kw)
    return build_cashflow(**args)  # type: ignore[arg-type]


def annuity(rate: float, n: int) -> float:
    return (1 - (1 + rate) ** -n) / rate


def test_pv_hand_calc() -> None:
    cf = flows()
    assert cf.pv_capex == pytest.approx(400 / 1.1 + 600 / 1.1**2)
    pv_ops = annuity(0.10, 10) / 1.1**2  # operating years 3..12
    assert cf.pv_opex == pytest.approx(50 * pv_ops)
    assert cf.pv_throughput == pytest.approx(100 * pv_ops)
    assert cf.lcc_pv == pytest.approx(cf.pv_capex + cf.pv_opex)
    assert cf.levelised_cost * cf.pv_throughput == pytest.approx(cf.lcc_pv)


def test_levelised_cost_is_zero_npv_tariff() -> None:
    cf = flows()
    assert cf.npv(cf.levelised_cost) == pytest.approx(0, abs=1e-6)
    assert cf.tariff_for_return(0.10, post_tax=False) == pytest.approx(cf.levelised_cost)


def test_residual_and_sustaining() -> None:
    cf = flows(sustaining_capex_by_year={5: 200.0})
    assert cf.years[4].capex == 200.0
    assert cf.total_capex == pytest.approx(1200)
    res = build_cashflow(
        C.model_copy(update={"residual_value_fraction": 0.2}), 1000.0, [50.0] * 10, [100.0] * 10
    )
    assert res.years[-1].residual == 200.0
    assert res.lcc_pv == pytest.approx(flows().lcc_pv - 200 / 1.1**12)


def test_irr() -> None:
    assert irr([-100, 110]) == pytest.approx(0.10)
    assert irr([-100, 0, 121]) == pytest.approx(0.10)
    with pytest.raises(ValueError):
        irr([100, 100])


def test_post_tax_tariff_hits_target_return() -> None:
    cf = flows()
    t = cf.tariff_for_return(0.12)
    assert cf.irr(t) == pytest.approx(0.12, abs=1e-6)
    assert t > cf.tariff_for_return(0.12, post_tax=False)  # tax raises the tariff
    no_tax = build_cashflow(
        C.model_copy(update={"tax_rate": 0.0}), 1000.0, [50.0] * 10, [100.0] * 10
    )
    assert no_tax.tariff_for_return(0.12) == pytest.approx(
        no_tax.tariff_for_return(0.12, post_tax=False)
    )


def test_tax_with_loss_carry_forward() -> None:
    cf = flows()
    tariff = 2.0  # revenue 200 vs opex 50 + depreciation 100 -> taxable 50 / yr
    post = cf.post_tax_flows(tariff)
    pre = cf.pre_tax_flows(tariff)
    assert pre[2] - post[2] == pytest.approx(0.2517 * (200 - 50 - 100))
    # Year-1 loss of 150 (revenue 200 - opex 250 - depreciation 100) is carried
    # forward and shelters the following profits of 50 / yr for three years.
    lossy = build_cashflow(C, 1000.0, [250.0] + [50.0] * 9, [100.0] * 10)
    tax = [p - t for p, t in zip(lossy.pre_tax_flows(2.0), lossy.post_tax_flows(2.0), strict=True)]
    ops = tax[2:]  # operating years 1..10
    assert ops[:4] == [pytest.approx(0.0)] * 4  # loss year, then -100, -50, 0 carried
    assert ops[4] == pytest.approx(0.2517 * 50)


def test_validation() -> None:
    with pytest.raises(ValidationError):
        Concession(construction_years=2, capex_phasing=(0.5, 0.4))
    with pytest.raises(ValidationError):
        Concession(construction_years=3, capex_phasing=(0.5, 0.5))
    with pytest.raises(ValueError):
        build_cashflow(C, 1000.0, [50.0] * 9, [100.0] * 10)


def test_tornado_ranks_by_swing() -> None:
    def f(p: dict[str, float]) -> float:
        return 3 * p["a"] + p["b"]

    rows = tornado(f, {"a": 10.0, "b": 10.0}, 0.1)
    assert [r[0] for r in rows] == ["a", "b"]
    assert rows[0][1] == pytest.approx(37.0)
    assert rows[0][2] == pytest.approx(43.0)
