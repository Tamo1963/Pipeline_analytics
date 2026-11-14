import os
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "apps" / "streamlit" / "app.py"
# Each AppTest run executes the whole script (economics, routing, ...). Slow or busy
# machines can need several minutes on the first, uncached run: override with
# APP_TEST_TIMEOUT (seconds).
TIMEOUT = float(os.environ.get("APP_TEST_TIMEOUT", "600"))


def test_app_renders_gas_basis() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    assert not at.exception
    assert any("OISD-STD-226" in m.value for m in at.markdown)


def test_app_switches_to_liquid() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.radio[0].set_value("liquid").run()
    assert not at.exception
    assert any("OISD-STD-141" in m.value for m in at.markdown)


def test_app_liquid_mass_to_volume_uses_density() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.radio[0].set_value("liquid").run()
    # default: 1 MMTPA -> bpd at 850 kg/m3
    expected = 1e9 / (365.25 * 850) / 0.158987295
    assert at.metric[0].value == f"{expected:,.4g} bpd"


def test_library_tab_renders() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    assert not at.exception
    assert any("OD 610.0 mm" in m.value for m in at.markdown)  # default NPS 24
    assert at.metric[1].value == "12.7 mm"  # 10 mm -> next schedule WT


def test_library_tab_increment_policy() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.radio(key="lib_policy").set_value("increment").run()
    assert at.metric[1].value == "10 mm"


def test_mechanical_tab_default_passes() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    assert not at.exception
    result = next(m for m in at.metric if m.label == "Result")
    assert result.value == "PASS"
    assert "hoop stress" in result.delta


def test_mechanical_tab_buoyancy_fails_and_sizes_concrete() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.checkbox(key="m_buoy").check().run()
    result = next(m for m in at.metric if m.label == "Result")
    assert result.value == "FAIL"
    assert any("Concrete weight coating needed: 65 mm" in i.value for i in at.info)


def test_hydraulics_tab_gas_default() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    assert not at.exception
    stations = next(m for m in at.metric if m.label == "Stations")
    assert int(stations.value) >= 1
    assert any("Class 3" in m.value for m in at.markdown)


def test_hydraulics_tab_liquid() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.selectbox(key="h_service").set_value("liquid").run()
    assert not at.exception
    delivery = next(m for m in at.metric if m.label == "Delivery pressure (barg)")
    assert float(delivery.value) >= 2.0


def test_heavy_tabs_wait_for_their_buttons() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    assert not at.exception
    assert not any(m.label == "Lowest lifecycle cost" for m in at.metric)
    assert not at.get("deck_gl_json_chart")
    infos = [i.value for i in at.info]
    assert any("Evaluate diameters" in v for v in infos)
    assert any("Find corridors" in v for v in infos)
    at.button(key="e_go").click().run()
    at.number_input(key="e_q1").set_value(11.0).run()  # inputs changed since the run
    assert any("Inputs changed" in i.value for i in at.info)


def test_economics_tab_renders_with_placeholder_warning() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    assert not at.exception
    assert any("PLACEHOLDER unit rates" in w.value for w in at.warning)
    at.button(key="e_go").click().run()
    best = next(m for m in at.metric if m.label == "Lowest lifecycle cost")
    assert best.value.endswith('"')


def test_route_tab_finds_corridors() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.button(key="r_go").click().run()
    assert not at.exception
    assert not [e.value for e in at.error]
    assert len(at.dataframe) >= 1


LLM_ENV = ("PIPELINE_BOT_PROVIDER", "PIPELINE_BOT_MODEL", "PIPELINE_BOT_BASE_URL",
           "PIPELINE_BOT_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY",
           "TOGETHER_API_KEY", "MISTRAL_API_KEY", "OLLAMA_API_KEY", "ANTHROPIC_API_KEY",
           "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")  # fmt: skip


def test_bot_needs_no_particular_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no keys anywhere the bot asks for a provider; a local server needs no key, and a
    failed call explains itself without losing the bot."""
    for var in LLM_ENV:
        monkeypatch.delenv(var, raising=False)
    from pipeline_analytics.bot import providers

    def unreachable(kind: str, base: str, key: str) -> list[str]:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(providers, "list_models", unreachable)
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    assert not at.exception
    assert at.sidebar.selectbox(key="llm_provider").value == "Select a provider..."
    assert any("Set up the bot's model" in i.value for i in at.info)
    assert not at.get("chat_input")
    at.sidebar.selectbox(key="llm_provider").set_value("Ollama (local)").run()
    assert any("Could not load the model list" in w.value for w in at.sidebar.warning)
    at.sidebar.selectbox(key="llm_model_Ollama (local)").set_value("llama3.1").run()
    at.chat_input(key="bot_input").set_value("Wall for 24 inch X70 at 92 barg?").run()
    assert not at.exception
    assert any("model call failed" in e.value for e in at.error)
    assert at.session_state["bot"] is not None  # study kept for a retry


def test_report_downloads_and_route_map() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.button(key="e_go").click().run()
    at.button(key="r_go").click().run()
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
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    assert at.selectbox(key="e_rateset").options == ["placeholder", "plus2"]
    at.multiselect(key="e_sizes").set_value(["20", "24", "28"]).run()
    at.button(key="e_go").click().run()
    at.select_slider(key="e_samples").set_value(2000).run()
    at.button(key="e_risk").click().run()
    assert not at.exception
    top = next(m for m in at.metric if m.label == "Most often cheapest")
    assert top.value.endswith("barg") and "of samples" in top.delta


def test_projects_tab_local_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPELINE_PROJECTS_DIR", str(tmp_path))
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
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


def test_mechanical_fe_fault_crossing() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.button(key="f_run").click().run()
    assert not at.exception
    res = [m for m in at.metric if m.label == "Max tensile strain"]
    assert res and res[0].value.endswith("%")
    at.button(key="f_sweep").click().run()
    assert any("Lowest strain utilisation" in s.value for s in at.success)


def test_route_tab_geohazard_checks() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.number_input(key="r_k").set_value(2).run()
    at.button(key="r_go").click().run()
    at.button(key="r_geo").click().run()
    assert not at.exception
    frames = [d.value for d in at.dataframe]
    assert any("Strain UR" in getattr(f, "columns", []) for f in frames)


def test_mechanical_liquefaction_panel() -> None:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.button(key="lq_run").click().run()
    assert not at.exception
    lpi = next(m for m in at.metric if m.label == "LPI")
    assert float(lpi.value) > 0
    assert any(m.label == "HDD setback needed" for m in at.metric)


def test_wiki_tab_write_search_and_bot_uploader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPELINE_WIKI_DIR", str(tmp_path))
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    assert not at.exception
    assert at.get("file_uploader")  # bot attachments + wiki uploads
    at.text_input(key="w_new_title").set_value("HDD practice").run()
    at.text_area(key="w_new_text").set_value("Entry angle 8-12 deg at wide rivers.").run()
    at.button(key="w_save").click().run()
    assert any("Saved article" in s.value for s in at.success)
    at.text_input(key="w_q").set_value("entry angle rivers").run()
    frames = [d.value for d in at.dataframe]
    assert any("HDD practice" in str(getattr(f, "values", "")) for f in frames)
    from pipeline_analytics.wiki import WikiStore

    assert WikiStore(tmp_path / "pipeline_wiki.db").count() == 1


def test_sidebar_key_loads_models_and_switches_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in LLM_ENV:
        monkeypatch.delenv(var, raising=False)
    from pipeline_analytics.bot import providers

    calls: list[tuple[str, str, str]] = []

    def fake_list(kind: str, base: str, key: str) -> list[str]:
        calls.append((kind, base, key))
        if key != "sk-or-good":
            raise PermissionError("the API key was rejected (401)")
        return ["anthropic/claude-opus-5.5", "openai/gpt-5", "qwen/qwen3-235b"]

    monkeypatch.setattr(providers, "list_models", fake_list)
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    at.sidebar.selectbox(key="llm_provider").set_value("OpenRouter").run()
    # no key yet: the model list waits for it
    assert any("Enter your OpenRouter API key" in i.value for i in at.sidebar.info)
    assert not calls
    at.sidebar.text_input(key="llm_key_OpenRouter").set_value("sk-or-bad").run()
    assert any("rejected" in w.value for w in at.sidebar.warning)
    at.sidebar.text_input(key="llm_key_OpenRouter").set_value("sk-or-good").run()
    assert calls[-1] == ("openai", "https://openrouter.ai/api/v1", "sk-or-good")
    model_box = at.sidebar.selectbox(key="llm_model_OpenRouter")
    assert model_box.options[:3] == [
        "anthropic/claude-opus-5.5", "openai/gpt-5", "qwen/qwen3-235b"]  # fmt: skip
    model_box.set_value("openai/gpt-5").run()
    bot = at.session_state["bot"]
    assert type(bot.provider).__name__ == "OpenAIChatProvider"
    assert bot.model == "openai/gpt-5" and bot.provider.api_key == "sk-or-good"
    n_calls = len(calls)
    at.run()  # cached: no refetch on rerun
    assert len(calls) == n_calls
    # custom server, custom model name
    at.sidebar.selectbox(key="llm_provider").set_value("Custom (OpenAI-compatible)").run()
    at.sidebar.text_input(key="llm_base_Custom (OpenAI-compatible)").set_value(
        "http://gpu-box:9000/v1").run()  # fmt: skip
    at.sidebar.selectbox(key="llm_model_Custom (OpenAI-compatible)").set_value(
        "Custom model name...").run()  # fmt: skip
    at.sidebar.text_input(key="llm_model_custom_Custom (OpenAI-compatible)").set_value(
        "my-model").run()  # fmt: skip
    bot = at.session_state["bot"]
    assert bot.model == "my-model" and bot.provider.base_url == "http://gpu-box:9000/v1"


def test_model_selection_follows_a_newly_loaded_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that only lists models once the key is entered: the dropdown must move from
    'Custom model name...' to the first real model."""
    for var in LLM_ENV:
        monkeypatch.delenv(var, raising=False)
    from pipeline_analytics.bot import providers

    def needs_key(kind: str, base: str, key: str) -> list[str]:
        if key != "k":
            raise PermissionError("the API key was rejected (401)")
        return ["acme/fast-8b", "acme/pipeline-70b"]

    monkeypatch.setattr(providers, "list_models", needs_key)
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT).run()
    name = "Custom (OpenAI-compatible)"
    at.sidebar.selectbox(key="llm_provider").set_value(name).run()
    at.sidebar.text_input(key=f"llm_base_{name}").set_value("http://srv/v1").run()
    assert at.sidebar.selectbox(key=f"llm_model_{name}").value == "Custom model name..."
    at.sidebar.text_input(key=f"llm_key_{name}").set_value("k").run()
    assert at.sidebar.selectbox(key=f"llm_model_{name}").value == "acme/fast-8b"
    assert at.session_state["bot"].model == "acme/fast-8b"
