"""Structural smoke tests for the RFS v3 (orchestration) suite definition.

Built entirely in memory (no ecflow server, no files on disk): ``builder()``
runs the builder's ``build()`` and then ``defs.check()`` - the same structural
validation ``gdeploy`` performs - so a malformed tree raises here. These pin
the core v3 tree so changes to ``builder_v3`` are caught.

Requires the ``ecflow`` Python bindings; skipped where they are not installed.
"""

import pytest

pytest.importorskip("ecflow")

from geoglows_ecflow.workflow.comfies.config import Config  # noqa: E402
from geoglows_ecflow.workflow.comfies.sdeploy import (  # noqa: E402
    DeployError,
)
from geoglows_ecflow.workflow.builders.builder_v3 import Builder  # noqa: E402

SUITE = "geoglows_v3_test"

# The three compute tasks of a cycle, in dependency order.
CYCLE_TASKS = ["retrieve_ifs", "route_forecast", "discharge_zarr"]


class _DictConfigSource:
    """Minimal in-memory config source, so no config file is needed."""

    def __init__(self, data):
        self.name = SUITE
        self.origin = "<in-memory test config>"
        self.data = data


def _make_config(cycles=None):
    """A minimal in-memory ``Config`` sufficient to construct the builder."""
    data = {
        "name": SUITE,
        "mode": "test",
        "first_date": "20230101",
        "last_date": "20230102",
        "routing": "/tmp/routing",
        "hydrography": "/tmp/hydrography",
        "forecasts": "/tmp/forecasts",
        "forecast_workroot": "/tmp/work",
        "target": {"root": "/tmp/target"},
        "jobs": {
            "root": "/tmp/jobs",
            "destinations": {"default": {"name": "localhost"}},
        },
    }
    if cycles is not None:
        data["cycles"] = list(cycles)
    return Config(_DictConfigSource(data))


def build_defs(cycles=None):
    """Build the suite definition in memory.

    Building runs the builder's own structural validation (``defs.check()``),
    so a malformed tree raises here rather than producing a bad definition.
    """
    builder = Builder(_make_config(cycles=cycles))
    builder()  # runs build() and defs.check()
    return builder.defs


def _child_names(defs, path):
    node = defs.find_abs_node(path)
    if node is None:
        return None
    return [n.name() for n in node.nodes]


@pytest.fixture(scope="module")
def defs():
    """A default-shape (single 00Z cycle) suite, built once, read-only."""
    return build_defs()


def test_make_family_holds_build_venv(defs):
    assert _child_names(defs, f"/{SUITE}/make") == ["build_venv"]


def test_main_repeats_over_ymd(defs):
    # YMD is a RepeatDate on main, not a variable.
    repeat = defs.find_abs_node(f"/{SUITE}/main").get_repeat()
    assert repeat.name() == "YMD"
    assert repeat.start() == 20230101


def test_default_runs_a_single_00z_cycle(defs):
    cycles = [n for n in _child_names(defs, f"/{SUITE}/main") if n != "YMD"]
    assert cycles == ["00"]


def test_cycle_holds_the_three_compute_tasks_in_order(defs):
    assert _child_names(defs, f"/{SUITE}/main/00") == CYCLE_TASKS


def test_cycle_carries_its_hour_in_emos_base(defs):
    assert (
        defs.find_abs_node(f"/{SUITE}/main/00").find_variable(
            "EMOS_BASE"
        ).value()
        == "00"
    )


def test_heavy_tasks_reserve_memory_and_cpus(defs):
    route = defs.find_abs_node(f"/{SUITE}/main/00/route_forecast")
    zarr = defs.find_abs_node(f"/{SUITE}/main/00/discharge_zarr")
    assert route.find_variable("MEM").value() == "120000"
    assert route.find_variable("NCPUS").value() == "32"
    assert zarr.find_variable("MEM").value() == "64000"
    assert zarr.find_variable("NCPUS").value() == "16"


def test_compute_tasks_are_chained_by_triggers(defs):
    # ooflow renders triggers into the generated definition (what is
    # deployed), so assert on the rendered text. "== complete" appears only in
    # trigger expressions, never in task declarations.
    text = str(defs)
    assert "retrieve_ifs == complete" in text
    assert "route_forecast == complete" in text


def test_suite_declares_the_variables_the_scripts_read(defs):
    suite = defs.find_abs_node(f"/{SUITE}")
    assert suite.find_variable("ROUTING").value() == "/tmp/routing"
    assert suite.find_variable("HYDROGRAPHY").value() == "/tmp/hydrography"
    assert suite.find_variable("FORECASTS").value() == "/tmp/forecasts"
    assert (
        suite.find_variable("FORECAST_WORK_DIR").value()
        == "%FORECAST_WORKROOT%/%YMD%/%EMOS_BASE%"
    )
    # v3-model-scripts ref defaults to main until a release tag is pinned.
    assert suite.find_variable("V3_SCRIPTS_REF").value() == "main"


def test_cycles_config_adds_a_second_cycle():
    defs = build_defs(cycles=["00", "12"])
    cycles = [n for n in _child_names(defs, f"/{SUITE}/main") if n != "YMD"]
    assert cycles == ["00", "12"]
    assert _child_names(defs, f"/{SUITE}/main/12") == CYCLE_TASKS


def test_single_digit_cycle_hour_is_zero_padded():
    # "0" must become the "00" cycle so EMOS_BASE / initialization stay
    # two-digit.
    defs = build_defs(cycles=["0"])
    cycles = [n for n in _child_names(defs, f"/{SUITE}/main") if n != "YMD"]
    assert cycles == ["00"]
    assert (
        defs.find_abs_node(f"/{SUITE}/main/00").find_variable(
            "EMOS_BASE"
        ).value()
        == "00"
    )


def test_empty_cycles_is_rejected():
    with pytest.raises(DeployError):
        build_defs(cycles=[])
