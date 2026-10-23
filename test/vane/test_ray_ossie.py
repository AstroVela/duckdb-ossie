"""Qualify coordinator model state and real Ray execution using installed wheels."""

from __future__ import annotations

import os
from contextlib import ExitStack

import pytest

from conftest import sql_string

pytestmark = [pytest.mark.real_ray, pytest.mark.ray_cluster_owner]


@pytest.fixture(scope="module", autouse=True)
def ray_runner():
    import ray
    import vane
    from ray.cluster_utils import Cluster

    assert os.environ.get("VANE_RUNNER") == "ray"
    assert not ray.is_initialized(), "this test process must own its Ray cluster"
    options = {}
    if "VANE_TEST_RAY_OBJECT_STORE_BYTES" in os.environ:
        capacity = int(os.environ["VANE_TEST_RAY_OBJECT_STORE_BYTES"])
        assert capacity > 0
        options["object_store_memory"] = capacity
    with ExitStack() as cleanup:
        settings = cleanup.enter_context(pytest.MonkeyPatch.context())
        # Keep each file split schedulable so this small fixture uses both nodes.
        settings.setenv("VANE_FTE_DYNAMIC_SCAN_MAX_SPLITS_PER_PARTITION", "1")
        cluster = Cluster(shutdown_at_exit=False)
        cleanup.callback(cluster.shutdown)
        cleanup.callback(ray.shutdown)
        cleanup.callback(vane.teardown_runner)
        cluster.add_node(num_cpus=0, num_gpus=0, include_dashboard=False, **options)
        for _ in range(2):
            cluster.add_node(num_cpus=1, num_gpus=0, include_dashboard=False, **options)
        ray.init(address=cluster.address, ignore_reinit_error=False)
        runner = vane.set_runner_ray(noop_if_initialized=True)
        assert runner.name == "ray"
        yield runner


def test_semantic_join_runs_on_ray(connection, semantic_data, ray_runner, monkeypatch):
    import ray
    import vane

    plans = []
    original = ray_runner.run_iter_tables

    def dispatch(plan):
        assert isinstance(plan, vane.ray_cxx.PyLogicalPlan)
        plans.append(plan)
        return original(plan)

    monkeypatch.setattr(ray_runner, "run_iter_tables", dispatch)
    # The semantic model file disappears before workers execute anything.
    # A worker-side rebind or model reload would therefore fail.
    semantic_data["model_path"].unlink()
    query = "SELECT * FROM ossie_query(['revenue'], ['customers.region']) ORDER BY 1"
    assert connection.sql(query).fetchall() == semantic_data["expected"]
    assert len(plans) == 1
    assert connection.execute(query).fetchall() == semantic_data["expected"]
    assert len(plans) == 2
    handwritten = (
        "SELECT c.region, sum(o.amount) FROM orders_catalog.main.orders o "
        "JOIN customers_catalog.main.customers c ON o.customer_id = c.id GROUP BY c.region ORDER BY c.region"
    )
    assert connection.sql(handwritten).fetchall() == semantic_data["expected"]
    assert len(plans) == 3
    client = ray_runner.query_driver_client
    assert client is not None
    stats = ray.get(client.runner.fragment_stats.remote())
    assert len(stats["workers"]) == 2
    assert all(worker["lookup_hits"] > 0 for worker in stats["workers"].values())


def test_model_metadata_stays_on_coordinator(
    connection, semantic_data, ray_runner, monkeypatch
):
    def unexpected_dispatch(_plan):
        raise AssertionError("model metadata must not be dispatched to a Ray worker")

    monkeypatch.setattr(ray_runner, "run_iter_tables", unexpected_dispatch)
    connection.execute(
        f"PRAGMA vane_ossie_load({sql_string(semantic_data['model_path'])}, validate_sources=true)"
    )
    assert connection.sql("SELECT name FROM ossie_metrics()").fetchall() == [
        ("revenue",)
    ]
    assert connection.sql(
        "SELECT name, resolved FROM ossie_datasets() ORDER BY name"
    ).fetchall() == [
        ("customers", True),
        ("orders", True),
    ]
    assert connection.sql("SELECT count(*) FROM ossie_fields()").fetchone() == (5,)
    assert connection.sql(
        "SELECT cardinality FROM ossie_relationships()"
    ).fetchone() == ("many_to_one",)
    assert (
        "sum(orders.amount)"
        in connection.sql("SELECT ossie_compile(['revenue'], [], [])").fetchone()[0]
    )


@pytest.mark.parametrize(
    "query",
    [
        "SELECT m.name, o.amount FROM ossie_metrics() m CROSS JOIN orders_catalog.main.orders o",
        "SELECT ossie_compile(['revenue'], [], []) FROM orders_catalog.main.orders LIMIT 1",
    ],
)
def test_mixed_model_metadata_and_data_is_rejected(connection, semantic_data, query):
    import vane

    with pytest.raises(
        vane.NotImplementedException, match="cannot mix client-context sources"
    ):
        connection.sql(query).fetchall()


def test_model_loading_cannot_be_a_worker_scan(connection, semantic_data):
    import vane

    path = str(semantic_data["model_path"]).replace("'", "''")
    with pytest.raises(
        vane.NotImplementedException, match="client-context table function ossie_load"
    ):
        connection.sql(f"SELECT * FROM ossie_load('{path}')").fetchall()
