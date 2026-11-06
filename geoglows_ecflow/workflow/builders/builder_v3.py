"""Suite builder for the RFS v3 forecast (orchestration branch).

A deliberately small builder: it wires the core v3 forecast tree and leaves
all computation to v3-model-scripts (called by the forecast_v3 task scripts).
It is NOT the v2 operational builder - there is no admin/barrier/lag
machinery, no per-VPU fan-out and no NetCDF-to-zarr conversion. Routing is
global (one job per cycle for the whole world), matching v3-model-scripts.

Tree built:

    suite
      make/   build_venv                              (once per install)
      main/   RepeatDate YMD, one family per cycle
        00/   retrieve_ifs -> route_forecast -> discharge_zarr

Scheduling note: the cycles carry no wall-clock release time. On a fresh v3
test server the operator starts the suite; real IFS-availability gating is an
Atos operational value to add later, not guessed here.
"""

from geoglows_ecflow.workflow.comfies.sdeploy import (
    BaseBuilder as ComfiesBaseBuilder,
)
from geoglows_ecflow.workflow.comfies.config import ConfigError
from geoglows_ecflow.workflow.comfies.ooflow import InLimit, RepeatDate
from geoglows_ecflow.workflow.parts.nodes import Family, Task, NominalTime

# Scheduler reservations (MB / cpus) for the two heavy global tasks.
ROUTE_MEM_MB = 120000
ROUTE_NCPUS = 32
ZARR_MEM_MB = 64000
ZARR_NCPUS = 16


class Builder(ComfiesBaseBuilder):

    comfies_minimum_version = "1.6.2"

    ecflow_module = "geoglows_ecflow.workflow.parts.nodes"

    # forecast_v3 holds the v3 task scripts; common/routing supply the shared
    # includes the wrappers pull in (ecflow_bash.h, suite.h, mars.h, ...).
    scripts = [
        "geoglows_ecflow/workflow/scripts/forecast_v3",
        "geoglows_ecflow/workflow/scripts/common",
    ]

    includes = [
        "geoglows_ecflow/workflow/scripts/forecast_v3",
        "geoglows_ecflow/workflow/scripts/routing",
        "geoglows_ecflow/workflow/scripts/common",
    ]

    def build(self):
        """Create the parts and wire them into the v3 forecast suite."""
        cfg = self.config
        suite = self.suite

        # Validate the run mode (consumed by suite.h via templating); the
        # builder does not branch on it.
        cfg.get("mode", choices=["prod", "test"])
        first_date = cfg.get("first_date", type=int)
        last_date = cfg.get("last_date", type=int, default="20300101")
        # Which daily cycles to run. download_ifs.sh is 00Z-oriented, so the
        # first-test default is a single 00Z cycle; add "12" when ready.
        # Hours are zero-padded so EMOS_BASE and the derived initialization
        # (YYYY-MM-DDTHH) are always two-digit.
        raw_cycles = cfg.get("cycles", type=list, default=["00"])
        cycles = [str(hh).zfill(2) for hh in raw_cycles]
        if not cycles:
            raise ConfigError("'cycles' must list at least one cycle hour")

        # Concurrency limits.
        suite.add_limit("make", 1)
        suite.add_limit("main", cfg.get("jobs.limit", type=int, default=4))

        # Suite-level variables the forecast_v3 task scripts read as %VAR%.
        # YMD and EMOS_BASE are dynamic (RepeatDate + NominalTime); the rest
        # come from the deploy config.
        suite.add_variable("ROUTING", cfg.get("routing"))
        suite.add_variable("HYDROGRAPHY", cfg.get("hydrography"))
        suite.add_variable("FORECASTS", cfg.get("forecasts"))
        suite.add_variable("FORECAST_WORKROOT", cfg.get("forecast_workroot"))
        suite.add_variable(
            "FORECAST_WORK_DIR", "%FORECAST_WORKROOT%/%YMD%/%EMOS_BASE%"
        )
        threads = cfg.get("forecast_threads", type=int, default=4)
        jobs = cfg.get("forecast_jobs", type=int, default=4)
        suite.add_variable("FORECAST_THREADS", threads)
        suite.add_variable("FORECAST_JOBS", jobs)

        # make: build the v3 environment once per suite install.
        n_make = Family("make")
        n_make.add(InLimit("make"))
        n_make.add(Task("build_venv"))
        suite.add(n_make)

        # main: the daily forecast, one family per cycle.
        n_main = Family("main")
        n_main.add(InLimit("main"))
        n_main.trigger = n_make.complete
        n_main.add(RepeatDate("YMD", int(first_date), int(last_date)))

        for hh in cycles:
            n_cycle = NominalTime(hh)

            n_retrieve = Task("retrieve_ifs")

            n_route = Task("route_forecast")
            n_route.trigger = n_retrieve.complete
            n_route.add_variable("MEM", ROUTE_MEM_MB)
            n_route.add_variable("NCPUS", ROUTE_NCPUS)

            n_zarr = Task("discharge_zarr")
            n_zarr.trigger = n_route.complete
            n_zarr.add_variable("MEM", ZARR_MEM_MB)
            n_zarr.add_variable("NCPUS", ZARR_NCPUS)

            n_cycle.add(n_retrieve, n_route, n_zarr)
            n_main.add(n_cycle)

        suite.add(n_main)
