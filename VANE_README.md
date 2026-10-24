# Ossie for Vane

This branch adds Vane integration to Ossie `v0.1.1`, which targets DuckDB
1.5.5. The original DuckDB source files, build inputs, submodule pins and tests
are unchanged. The native distribution workflow installs DuckDB 1.5.5 explicitly
for its MCP and artifact-loading checks.

## Execution

Load the installed provider on the coordinator, then load a model:

```python
import vane

connection = vane.connect(config={"arrow_lossless_conversion": "true"})
vane.load_installed_extension("ossie", connection=connection)
connection.execute("PRAGMA vane_ossie_load('retail.json', validate_sources=true)")
connection.sql("SELECT * FROM ossie_metrics()").show()
connection.sql("SELECT * FROM ossie_query(['revenue'], ['customers.region'])").show()
```

The model declares datasets, fields, relationships and aggregate metrics.
Dataset sources must resolve as tables or views in this connection. Attached
catalogs and views over Parquet files are supported. Lance and Iceberg tables
can be named after loading their matching providers and attaching their catalogs;
their storage adapters remain responsible for distributed reads. The automated
tests qualify cross-catalog Parquet views, not a Lance/Iceberg deployment.

`ossie_query` expands into ordinary SQL during coordinator binding. The resulting
scan, join, filter and aggregation plan executes through the selected Vane
runner. Workers do not read the model file or receive its mutable model cache.
Enable `arrow_lossless_conversion` when aggregates return `HUGEINT` so Ray's
Arrow transport preserves the exact integer result.

`PRAGMA vane_ossie_load(...)` loads the model on the coordinator using Ossie's
original loader. It accepts the same `rebind`, `validate_sources` and
`allow_filter_functions` named parameters, using `=` in PRAGMA syntax. Vane's
Ray runner rejects SQL `CALL`; native DuckDB retains its original `CALL
ossie_load(...)` interface. `ossie_compile`,
`ossie_datasets`, `ossie_fields`, `ossie_metrics` and `ossie_relationships` are
explicit coordinator metadata reads. Ray queries combining these metadata
functions with ordinary data, or using `ossie_load` as a worker table scan, are
rejected. This is an execution contract, with no local fallback for data queries.

## Builds

Use the original `make` commands for native DuckDB. Vane uses a separate entry
point and the exact Vane, vcpkg and CI-tools SHAs in `vane-extension.toml`:

```bash
git submodule update --init --recursive
git clone https://github.com/AstroVela/vane-extension-ci-tools.git build/vane-ci-tools
git -C build/vane-ci-tools checkout --detach 9de078021e3cb0ee09d50568aca114e58f2c2014
export VCPKG_TOOLCHAIN_PATH=/path/to/exact/vcpkg/scripts/buildsystems/vcpkg.cmake
make -f Makefile.vane vane_native
make -f Makefile.vane vane_wheel
```

The wheel lane requires Linux x86-64, Python 3.12, a C++20 compiler, CMake
3.31.6, Ninja, auditwheel, patchelf, and Vane's non-editable wheel build
dependencies. It emits a matching Vane wheel and `vane-extension-ossie` provider wheel in
`build/vane-dist`. Install that exact pair on the coordinator and every Ray
node. Extension download and installation never happen at query runtime.

These are CI artifacts signed with Vane's public integration-test key. The
matching Vane runtime explicitly enables that key. The workflow has read-only
repository permissions and does not publish packages or use production keys.

## Verification

The new `Vane extension` workflow checks that original `v0.1.1` files other than
the CI workflow are unchanged, runs the original SQLLogicTests against Vane DuckDB,
builds and verifies the installed wheel pair, and runs local and Ray tests in separate processes.
The Ray test cluster has a head with no CPUs and two one-CPU worker nodes.

After installing the generated wheels and `pytest`:

```bash
VANE_RUNNER=local python -m pytest -c test/vane/pytest.ini test/vane/test_local_ossie.py
VANE_RUNNER=ray python -m pytest -c test/vane/pytest.ini test/vane/test_ray_ossie.py
```

Ray sizes the object store automatically. `VANE_TEST_RAY_OBJECT_STORE_BYTES`
can explicitly bound this test cluster's capacity on a shared development host.
