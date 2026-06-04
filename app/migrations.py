from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


CUSTOMER_CHECK_COLUMNS = {
    "request_theme": "VARCHAR(160) NOT NULL DEFAULT ''",
    "deal_value": "INTEGER NOT NULL DEFAULT 0",
    "deal_stage": "VARCHAR(80) NOT NULL DEFAULT ''",
    "deal_blocker": "BOOLEAN NOT NULL DEFAULT 0",
    "source": "VARCHAR(30) NOT NULL DEFAULT 'manual'",
    "resolved": "BOOLEAN NOT NULL DEFAULT 0",
    "resolved_at": "DATETIME",
    "customer_updated_at": "DATETIME",
    "product_shared_at": "DATETIME",
}


def migrate_database(engine: Engine) -> None:
    inspector = inspect(engine)
    if "customer_checks" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("customer_checks")}
    with engine.begin() as connection:
        for name, definition in CUSTOMER_CHECK_COLUMNS.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE customer_checks ADD COLUMN {name} {definition}"))
