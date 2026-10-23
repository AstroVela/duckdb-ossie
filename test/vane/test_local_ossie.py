"""Exercise the packaged provider through Vane's native local runner."""

import json

import pytest

from conftest import sql_string


def test_provider_is_loaded_dynamically(connection):
    assert connection.execute(
        "SELECT current_setting('allow_unsigned_extensions'), "
        "current_setting('autoinstall_known_extensions'), current_setting('autoload_known_extensions')"
    ).fetchone() == (False, False, False)
    assert connection.execute(
        "SELECT loaded, installed, install_mode FROM duckdb_extensions() WHERE extension_name = 'ossie'"
    ).fetchall() == [(True, False, "NOT_INSTALLED")]


def test_no_model_is_an_error(connection):
    import vane

    with pytest.raises(vane.InvalidInputException, match="no Ossie model is loaded"):
        connection.sql("SELECT * FROM ossie_metrics()").fetchall()


def test_model_discovery(connection, semantic_data):
    assert connection.sql(
        "SELECT name, resolved FROM ossie_datasets() ORDER BY name"
    ).fetchall() == [
        ("customers", True),
        ("orders", True),
    ]
    assert connection.sql("SELECT name FROM ossie_metrics()").fetchall() == [
        ("revenue",)
    ]
    assert connection.sql("SELECT count(*) FROM ossie_fields()").fetchone() == (5,)
    assert connection.sql(
        "SELECT cardinality FROM ossie_relationships()"
    ).fetchall() == [("many_to_one",)]


def test_cross_catalog_query_and_compiled_sql(connection, semantic_data):
    assert connection.sql("SELECT * FROM ossie_query(['revenue'])").fetchone() == (
        semantic_data["total"],
    )
    actual = connection.sql(
        "SELECT * FROM ossie_query(['revenue'], ['customers.region']) ORDER BY 1"
    ).fetchall()
    assert actual == semantic_data["expected"]
    compiled = connection.sql(
        "SELECT ossie_compile(['revenue'], ['customers.region'], [])"
    ).fetchone()[0]
    assert "orders_catalog.main.orders" in compiled
    assert "customers_catalog.main.customers" in compiled
    assert connection.sql(f"SELECT * FROM ({compiled}) ORDER BY 1").fetchall() == actual


def test_filtered_query_and_relation_api(connection, semantic_data):
    expected = [row for row in semantic_data["expected"] if row[0] == "West"]
    assert (
        connection.sql(
            "SELECT * FROM ossie_query(['revenue'], ['customers.region'], [$$customers.region = 'West'$$])"
        ).fetchall()
        == expected
    )
    assert (
        connection.sql("SELECT * FROM ossie_query(['revenue'], ['customers.region'])")
        .filter("\"customers.region\" = 'West'")
        .fetchall()
        == expected
    )


def test_model_is_shared_by_cursors_and_isolated_by_database(connection, semantic_data):
    import vane

    with connection.cursor() as cursor:
        assert cursor.sql("SELECT name FROM ossie_metrics()").fetchall() == [
            ("revenue",)
        ]
    with vane.connect() as other:
        vane.load_installed_extension("ossie", connection=other)
        with pytest.raises(
            vane.InvalidInputException, match="no Ossie model is loaded"
        ):
            other.sql("SELECT * FROM ossie_metrics()").fetchall()


def test_filter_policy_is_preserved(connection, semantic_data):
    import vane

    with pytest.raises(vane.InvalidInputException, match="allow_filter_functions"):
        connection.sql(
            "SELECT * FROM ossie_query(['revenue'], [], ['abs(orders.amount) > 1'])"
        ).fetchall()


def test_coordinator_load_preserves_validation_rebinding_and_policy(
    connection, semantic_data
):
    import vane

    model = semantic_data["model"]
    for dataset in model["semantic_model"][0]["datasets"]:
        dataset["source"] = f"warehouse_{dataset['name']}.public.{dataset['name']}"
    path = semantic_data["model_path"]
    path.write_text(json.dumps(model), encoding="utf-8")
    with pytest.raises(
        vane.InvalidInputException, match="dataset sources do not exist"
    ):
        connection.execute(
            f"PRAGMA vane_ossie_load({sql_string(path)}, validate_sources=true)"
        )
    # A failed replacement must leave the previous model usable.
    assert connection.sql("SELECT * FROM ossie_query(['revenue'])").fetchone() == (
        semantic_data["total"],
    )
    connection.execute(
        f"PRAGMA vane_ossie_load({sql_string(path)}, validate_sources=true, "
        "rebind=MAP{'warehouse_orders.public': 'orders_catalog.main', "
        "'warehouse_customers.public': 'customers_catalog.main'}, "
        "allow_filter_functions=true)"
    )
    assert connection.sql(
        "SELECT * FROM ossie_query(['revenue'], [], ['abs(orders.amount) > 0'])"
    ).fetchone() == (semantic_data["total"],)
