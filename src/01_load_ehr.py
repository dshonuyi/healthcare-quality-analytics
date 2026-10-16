"""
Step 1 - Load the Synthea EHR extract into a local DuckDB warehouse.

Synthea (https://github.com/synthetichealth/synthea) is an open-source generator of
realistic but fully synthetic patient records. No real patient data is used anywhere
in this project.

Run from the repo root:
    python src/01_load_ehr.py
"""
from pathlib import Path

import duckdb

RAW = Path("data/raw")
DB = Path("data/processed/ehr.duckdb")

TABLES = [
    "patients", "encounters", "conditions", "observations", "procedures",
    "medications", "immunizations", "payers", "payer_transitions",
    "organizations", "providers",
]


def main() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    con = duckdb.connect(str(DB))
    for t in TABLES:
        src = RAW / f"{t}.csv"
        # all_varchar keeps the raw extract untouched so validation can see bad values;
        # typed views are built on top of it.
        con.execute(
            f"CREATE TABLE raw_{t} AS SELECT * FROM read_csv('{src}', header=true, all_varchar=true)"
        )
        n = con.execute(f"SELECT count(*) FROM raw_{t}").fetchone()[0]
        print(f"loaded raw_{t:<18} {n:>10,} rows")

    # Typed views used by the analysis layer
    con.execute(open("sql/00_typed_views.sql").read())
    con.close()
    print(f"\nWarehouse written to {DB}")


if __name__ == "__main__":
    main()
