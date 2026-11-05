# Phase 8: risk analysis, unit-rate management and multi-user projects

See ADR 0009 for the design decisions.

## 1. Monte Carlo risk (`economics/risk.py`)

```python
from pipeline_analytics.economics.risk import RiskInputs, Triangular, monte_carlo

result = monte_carlo(designs, RiskInputs(samples=10_000))  # designs: list[OptionInput]
for d in result.designs:
    print(d.label, d.lcc_p10, d.lcc_p50, d.lcc_p90, d.prob_cheapest, d.prob_infeasible)
```

**Uncertain inputs**

| Group | What it scales | Default triangular factor (low-mode-high) |
|---|---|---|
| `line_pipe` | pipe price per tonne | 0.85-1.0-1.30 |
| `construction` | laying, HDD, open cut, road/rail crossings, concrete coating | 0.85-1.0-1.40 |
| `stations` | compressor/pump stations, terminals, valve and scraper stations | 0.85-1.0-1.30 |
| `other_capex` | external coating, CP, SCADA/telecom, right of use, station land | 0.90-1.0-1.25 |
| `energy` | electricity and fuel-gas prices | 0.70-1.0-1.50 |
| `o_and_m` | line O&M, manpower, in-line inspection | 0.90-1.0-1.20 |
| throughput | every year's flow | low ×0.8 (25 %), base (50 %), high ×1.2 (25 %) |

These defaults are screening judgement. Replace them with project ranges.

**Method**
* For a fixed design, lifecycle cost is exactly linear in the group factors. Each design is therefore evaluated 7 times per throughput scenario: the base case, plus each group at ×2. Every sample after that is a weighted sum.
* Infeasible samples count as the worst outcome. They appear as `inf` in the percentiles and "infeasible" in the reports.
* All designs share the same samples, so **P(cheapest)** compares like with like.
* Groups are sampled independently. Correlated markets would widen the spread.

**Where it appears**
* **Bot:** the `run_risk_analysis` tool. It uses the best grade and MAOP of each size from the last design search. Ranges and throughput cases can be overridden.
* **Streamlit (Economics tab):** the "Run Monte Carlo risk analysis" button.
* **Reports:** a "Risk (Monte Carlo)" section in Word and a "Risk" sheet in Excel, both included when a risk run exists.

## 2. Project unit rates (`cost/rate_library.py`, `pipeline-rates`)

Rate sets are YAML files in `./rates`, or in `$PIPELINE_RATES_DIR` if set. The bundled `placeholder` set is always available and read-only.

```bash
pipeline-rates new gail_2026 --region "Western India" --source "Contractor quotes, Oct 2026"
```

```bash
pipeline-rates validate gail_2026
```

```bash
pipeline-rates approve gail_2026 --by "Cost engineering" --valid-until 2027-06-30
```

```bash
pipeline-rates escalate gail_2026 gail_2028 --to-year 2028 --annual 0.05 --group energy=0.07
```

```bash
pipeline-rates diff gail_2026 gail_2028
```

```bash
pipeline-rates list
```

* `new` copies a set, by default the placeholder. The copy keeps `placeholder: true` until you replace the values and set it to `false`. Placeholder sets cannot be approved.
* `validate` reports:
  * errors: missing terrain, crossing or land-use entries, or a missing 3LPE coating;
  * warnings: placeholder or unapproved sets, expiry, a base year more than 3 years old, unusually high indirects, and rates more than 4× above or below the reference set;
  * informational notes: grades or processes that cannot be priced.

  The command exits with code 1 if there are errors.
* `escalate` compounds the annual rate per group. Percentages are not escalated. It records `meta.escalated_from` and clears approval.
* Saves are atomic (a temporary file, then rename) and locked.
* Each set carries `meta`: `name`, `region`, `approved_by`, `approved_on`, `valid_until` and `notes`. Older YAML files without `meta` still load.

The Streamlit Economics tab has a rate-set picker. It shows the source and approval status of the selected set, and the findings of the checks.

## 3. Multi-user projects (`projects/`)

A project holds a feasibility-bot study (basis, route, rates, assumptions, best result, sensitivity and risk rows, scenario tree, chat transcript) with its full history.

```python
from pipeline_analytics.projects import ProjectStore
from pipeline_analytics.projects.payload import study_to_payload, payload_to_study

store = ProjectStore()  # ./projects or $PIPELINE_PROJECTS_DIR
p = store.create("Bhola - Khulna", "asha@example.com", payload=study_to_payload(bot.study))
store.share(p.id, "asha@example.com", "ravi@example.com", "editor")
v = store.load(p.id, "ravi@example.com")
store.save(p.id, "ravi@example.com", new_payload, "higher plateau", expected_version=v.version)
```

* **Roles:**
  * owner: everything;
  * editor: read and save new versions;
  * viewer: read only.

  Only the owner shares, renames or archives. Other users get "not found", so project ids do not leak.
* **History:** versions are append-only. "Restore" saves an old version again as the newest.
* **Concurrency:** a save whose `expected_version` is not the latest raises `VersionConflictError`, which names who saved in the meantime. The per-project file lock makes the check-and-write atomic across processes.
* **Not saved:** the last design-search object. After opening a project, run the design search again before generating a report.

### Sign-in

The Projects tab uses Streamlit's built-in OIDC sign-in (`st.login`) when `.streamlit/secrets.toml` has an `[auth]` section. For example, with Google:

```toml
[auth]
redirect_uri = "https://your-host/oauth2callback"
cookie_secret = "a long random string"
client_id = "..."
client_secret = "..."
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

Microsoft Entra ID, Okta and other OIDC providers work the same way. Without `[auth]` the tab runs in **local mode**: users type a name, nothing verifies it, and the tab says so. Use local mode only on a single-user machine or a trusted network.

### Deployment notes
* Put `projects/` and `rates/` on storage with working POSIX `flock` (local disk, or NFSv4 with locking). Some network shares ignore locks.
* Back up the folders. Version files are append-only, so incremental backups work well.
* For many concurrent users or formal audit trails, move the store's API onto a database. The interface (`create`, `save` with `expected_version`, `load`, `history`, `share`) stays the same.
