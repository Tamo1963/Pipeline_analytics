"""Scenario tree: every evaluated option, with where it came from.

Each node stores what changed relative to its parent and the headline
results, so the study can show *why* the recommendation moved between
iterations (plan section 6.3).
"""

from __future__ import annotations

import json
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pipeline_analytics.cost.rates import crore
from pipeline_analytics.economics.option import OptionInput, OptionResult


class ScenarioNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", ser_json_inf_nan="constants")

    id: int
    parent: int | None
    iteration: int
    label: str
    changes: dict[str, Any] = Field(default_factory=dict)
    feasible: bool = True
    issue: str = ""
    nps: str | None = None
    grade: str | None = None
    design_pressure_barg: float | None = None
    length_km: float | None = None
    lcc_pv: float = math.inf
    pv_capex: float = math.inf
    pv_opex: float = math.inf
    levelised_cost: float = math.inf
    max_stations: int = 0
    first_station_year: int | None = None


class ScenarioTree:
    def __init__(self) -> None:
        self.nodes: list[ScenarioNode] = []

    def add_group(
        self, label: str, parent: int | None = None, iteration: int = 0, **changes: Any
    ) -> int:
        """A grouping node (an iteration, a corridor, a sensitivity case)."""
        node = ScenarioNode(
            id=len(self.nodes), parent=parent, iteration=iteration, label=label,
            changes=changes, feasible=False, issue="group",
        )  # fmt: skip
        self.nodes.append(node)
        return node.id

    def add_result(
        self,
        inp: OptionInput,
        res: OptionResult,
        *,
        label: str,
        parent: int | None = None,
        iteration: int = 0,
        **changes: Any,
    ) -> ScenarioNode:
        stations = [h.stations for h in res.hydraulics]
        first = next((h.operating_year for h in res.hydraulics if h.stations), None)
        node = ScenarioNode(
            id=len(self.nodes),
            parent=parent,
            iteration=iteration,
            label=label,
            changes=changes,
            feasible=res.feasible,
            issue="; ".join(res.issues),
            nps=res.nps,
            grade=res.grade,
            design_pressure_barg=inp.design_pressure_pa_g / 1e5,
            length_km=inp.profile.length_m / 1000,
            lcc_pv=res.lcc_pv,
            pv_capex=res.pv_capex,
            pv_opex=res.pv_opex,
            levelised_cost=res.levelised_cost,
            max_stations=max(stations, default=0),
            first_station_year=first,
        )
        self.nodes.append(node)
        return node

    def results(self) -> list[ScenarioNode]:
        return [n for n in self.nodes if n.issue != "group"]

    def best(self, parent: int | None = None) -> ScenarioNode:
        """Lowest-LCC feasible node, optionally among the descendants of ``parent``."""
        pool = [n for n in self.results() if n.feasible]
        if parent is not None:
            pool = [n for n in pool if parent in self.lineage(n.id)]
        if not pool:
            raise ValueError("no feasible scenario")
        return min(pool, key=lambda n: n.lcc_pv)

    def lineage(self, node_id: int) -> list[int]:
        """Ids from ``node_id`` up to the root."""
        out: list[int] = []
        cur: int | None = node_id
        while cur is not None:
            out.append(cur)
            cur = self.nodes[cur].parent
        return out

    def table(self, parent: int | None = None) -> list[dict[str, Any]]:
        rows = []
        for n in self.results():
            if parent is not None and parent not in self.lineage(n.id):
                continue
            rows.append(
                {
                    "id": n.id,
                    "iteration": n.iteration,
                    "label": n.label,
                    "NPS": n.nps,
                    "grade": n.grade,
                    "MAOP barg": n.design_pressure_barg,
                    "km": n.length_km,
                    "feasible": n.feasible,
                    "LCC cr": round(crore(n.lcc_pv), 1) if n.feasible else None,
                    "stations": n.max_stations,
                    "issue": n.issue,
                }
            )
        return rows

    def to_json(self) -> str:
        return json.dumps([n.model_dump(mode="json") for n in self.nodes], indent=1)
