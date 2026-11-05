# RFS v3 forecast on geoglows-ecflow (ECMWF) — orchestration design

## Goal

Run the river-route v3 15-day ensemble forecast on a geoglows-ecflow server on
an ECMWF (Atos) machine, reusing `river-forecast-system/v3-model-scripts` for
all computation.

## Principle: orchestrate, do not reimplement

Two layers, kept separate:

- **v3-model-scripts = computation** (route, aggregate, summaries). Plain,
  scheduler-agnostic Python, runnable standalone. The single home for the model
  logic and for `rfs_spec` (the published-store layout, encoding, keepbits,
  chunks and shards).
- **geoglows-ecflow = operational orchestration** on the ECMWF HPC. The ecflow
  suite, troika job submission, MARS retrieval, the v3 environment build, the
  ML inference step, scheduling, retries, monitoring, and publishing to S3.

geoglows-ecflow **calls** v3-model-scripts; it does not duplicate the
computation.

## Decisions (locked 2026-11-05)

- **Forcing:** `ecmwf_grib` on the native **O1280** reduced-gaussian GRIB, kept as
  GRIB (no `cdo -R` regrid). Routed globally in one job.
- **First-test scope:** core only - `retrieve_ifs -> route_forecast ->
  discharge_zarr`. Summaries and S3 publish are deferred.
- **ML step:** deferred. Build and test the core pipeline first; add the ML
  inference step (and PyTorch) once its design is settled.
- **Members:** 51 - control 0, perturbed 1..50. The old HRES member 52 is dropped.
- **Warm-state:** cold start for the first test; warm start later.
- **Deploy:** a fresh ecflow server for v3 testing, separate from operations.

## Forecast task tree (per cycle)

Routing is global (one job, all 51 members) as v3-model-scripts does it, not
per-VPU: the IFS GRIB is read once for the whole world.

1. `check_model` - verify the ML model on the bucket exists and is current.
2. `retrieve_ifs` - MARS retrieve the IFS ENS GRIB (`cf_00` + `pf_01..50`).
3. `ml_transform` - run the ML (PyTorch) inference on the IFS runoff.
4. `route_forecast` - `4_forecasts/1_route_forecast.py` -> per-member netCDF.
5. `discharge_zarr` - `4_forecasts/2_discharge_zarr.py` -> `discharge.zarr`.
6. `summary_files` - `4_forecasts/3_summary_files.py` -> stylesets + alerts.csv.
7. `publish` - upload the `forecasts15/` outputs to S3.

`build_venv` builds the v3 environment once per suite install (not per cycle).

## Owns vs delegates

- **geoglows-ecflow owns:** suite definition + task tree + dependencies; troika
  submission (`ECF_JOB_CMD`); MARS retrieval; `build_venv` (the v3 env);
  ML-step orchestration (model check + inference task); scheduling, retries,
  monitoring; S3 publish.
- **Delegated to v3-model-scripts:** all routing, aggregation and summary
  computation, plus `rfs_spec`.

## Dependency and config wiring

- `build_venv` installs, via conda-forge (the native-library combination that
  pip cannot resolve cleanly - eccodes, geopandas): Python 3.14, river-route v3,
  `v3-model-scripts` (provides `rfs_spec` + the `4_forecasts` scripts), PyTorch,
  and their deps.
- Config: map v3-model-scripts `config.sh` variables (`DATA_ROOT`, `IFS_DIR`,
  `ROUTING`, `HYDROGRAPHY`, `FORECASTS`, `INIT_STATE`, `INITIALIZATION`,
  `FORECAST_THREADS`, `FORECAST_JOBS`) to ecflow suite variables.
- Data staged on Atos: `routing/` + `hydrography/` from the bucket; IFS via
  MARS; the O1280 grid weights.

## Supersedes the rfs-v3 branch

The earlier `rfs-v3` branch reimplemented the routing and aggregation in
geoglows-ecflow. This branch delegates them to v3-model-scripts instead:

- `run_river_route_forecast.py` -> `4_forecasts/1_route_forecast.py`
- `forecast_discharge_store.py` -> `4_forecasts/2_discharge_zarr.py`
- vendored `rfs_spec.py` -> depend on `v3-model-scripts`
- the zarr-append member writer -> per-member netCDF + `2_discharge_zarr`

## Phased build

- **Phase 1:** `build_venv` v3 (install river-route + v3-model-scripts + PyTorch);
  confirm the scripts import and run where testable (cold start + bucket data).
- **Phase 2:** wire the forecast task tree (ecflow tasks calling the scripts) +
  the config mapping.
- **Phase 3:** `retrieve_ifs` (MARS) + `ml_transform`.
- **Phase 4:** deploy on Atos; run a small-scope real test (cold start); scale.

## Testing reality

Full runs need ECMWF access (MARS, modules, troika). Locally we can validate the
environment build and script invocation against bucket data with a cold start;
the real validation is on Atos.
