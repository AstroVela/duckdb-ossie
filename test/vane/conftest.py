"""Shared fixtures for installed Ossie provider wheels."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest


@pytest.fixture
def connection():
    import vane

    connection = vane.connect(
        ":memory:",
        config={
            "allow_unsigned_extensions": "false",
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
            "arrow_lossless_conversion": "true",
        },
    )
    try:
        resolved = vane.load_installed_extension("ossie", connection=connection)
        assert resolved.descriptor.name == "ossie"
        assert resolved.descriptor.trust_identity == "vane-ci-test-key"
        assert not resolved.descriptor.dependencies
        yield connection
    finally:
        connection.close()


def sql_string(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture
def semantic_data(connection, tmp_path: Path):
    # Files and model belong to this test. Workers only need the Parquet files;
    # the loaded model is held in the coordinator database's object cache.
    count = 16384
    amounts = [(index % 97) + 1 for index in range(count)]
    customer_ids = [index % 3 for index in range(count)]
    orders = tmp_path / "orders"
    orders.mkdir()
    for part in range(8):
        start, stop = part * 2048, (part + 1) * 2048
        pq.write_table(
            pa.table(
                {
                    "id": list(range(start, stop)),
                    "customer_id": customer_ids[start:stop],
                    "amount": amounts[start:stop],
                }
            ),
            orders / f"part-{part}.parquet",
        )
    customers = tmp_path / "customers.parquet"
    regions = ["East", "West", "West"]
    pq.write_table(pa.table({"id": [0, 1, 2], "region": regions}), customers)
    for catalog in ("orders_catalog", "customers_catalog"):
        connection.execute(f"ATTACH ':memory:' AS {catalog}")
    connection.execute(
        "CREATE VIEW orders_catalog.main.orders AS "
        f"SELECT * FROM read_parquet({sql_string(orders / '*.parquet')})"
    )
    connection.execute(
        "CREATE VIEW customers_catalog.main.customers AS "
        f"SELECT * FROM read_parquet({sql_string(customers)})"
    )

    def expression(sql):
        return {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}

    datasets = []
    for name, columns in (
        ("orders", ["id", "customer_id", "amount"]),
        ("customers", ["id", "region"]),
    ):
        datasets.append(
            {
                "name": name,
                "source": f"{name}_catalog.main.{name}",
                "primary_key": ["id"],
                "fields": [
                    {"name": column, "expression": expression(column)}
                    for column in columns
                ],
            }
        )
    model = {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {
                "name": "retail",
                "datasets": datasets,
                "relationships": [
                    {
                        "name": "order_customer",
                        "from": "orders",
                        "to": "customers",
                        "from_columns": ["customer_id"],
                        "to_columns": ["id"],
                    }
                ],
                "metrics": [
                    {"name": "revenue", "expression": expression("sum(orders.amount)")}
                ],
            }
        ],
    }
    model_path = tmp_path / "retail.json"
    model_path.write_text(json.dumps(model), encoding="utf-8")
    connection.execute(
        f"PRAGMA vane_ossie_load({sql_string(model_path)}, validate_sources=true)"
    )
    expected = sorted(
        (
            region,
            sum(
                value
                for value, customer in zip(amounts, customer_ids)
                if regions[customer] == region
            ),
        )
        for region in set(regions)
    )
    return {
        "model_path": model_path,
        "model": model,
        "expected": expected,
        "total": sum(amounts),
    }
