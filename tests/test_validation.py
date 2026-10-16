"""
Fault-injection test for the validation rules.

Synthea output is cleaner than a production EHR, so this test builds a tiny fixture
with known, deliberately planted errors and confirms each Critical rule catches
exactly the records it should.

Run from the repo root:
    python -m pytest tests/ -q
"""
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import importlib

validate = importlib.import_module("02_validate")


def fixture() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("""
        CREATE TABLE patients AS SELECT * FROM (VALUES
            ('P1', DATE '1960-01-01', NULL,              'F', 'white', 'nonhispanic', '92101'),
            ('P1', DATE '1960-01-01', NULL,              'F', 'white', 'nonhispanic', '92101'),  -- U1 duplicate
            ('P2', DATE '1980-05-05', DATE '2024-01-01', 'M', 'black', 'nonhispanic', '92102'),
            ('P3', NULL,              NULL,              'M', NULL,    'hispanic',    NULL)      -- M1, M2, M3
        ) t(patient_id, birth_date, death_date, gender, race, ethnicity, zip);

        CREATE TABLE payers AS SELECT 'PAY1' AS payer_id, 'Medicare' AS payer_name;

        CREATE TABLE encounters AS SELECT * FROM (VALUES
            ('E1', 'P1', 'PAY1', 'PR1', 'wellness',  'Wellness',    TIMESTAMPTZ '2025-01-01 09:00', TIMESTAMPTZ '2025-01-01 10:00'),
            ('E2', 'P1', 'PAY1', 'PR1', 'ambulatory','Visit',       TIMESTAMPTZ '2025-02-01 10:00', TIMESTAMPTZ '2025-02-01 09:00'),  -- T1
            ('E3', 'P2', 'PAY1', 'PR1', 'ambulatory','Visit',       TIMESTAMPTZ '2024-06-01 09:00', TIMESTAMPTZ '2024-06-01 10:00'),  -- T2
            ('E4', 'PX', 'PAY9', NULL,  'ambulatory','Visit',       TIMESTAMPTZ '2025-03-01 09:00', TIMESTAMPTZ '2025-03-01 10:00')   -- R1, R4, M4
        ) t(encounter_id, patient_id, payer_id, provider_id, encounter_class, description, start_ts, stop_ts);

        CREATE TABLE conditions AS SELECT * FROM (VALUES
            ('P1', 'E1', '59621000', 'Essential hypertension (disorder)', DATE '2020-01-01', DATE '2019-01-01')  -- T4
        ) t(patient_id, encounter_id, code, description, start_date, stop_date);

        CREATE TABLE observations AS SELECT * FROM (VALUES
            ('P1', 'E1', '8480-6',  120.0, '120', 'mm[Hg]', TIMESTAMPTZ '2025-01-01 09:10'),
            ('P1', 'E1', '8462-4',  130.0, '130', 'mm[Hg]', TIMESTAMPTZ '2025-01-01 09:10'),  -- C3 diastolic >= systolic
            ('P1', 'E1', '8480-6',  400.0, '400', 'mm[Hg]', TIMESTAMPTZ '2025-01-01 09:20'),  -- C1
            ('P1', 'E1', '4548-4',    2.5, '2.5', '%',      TIMESTAMPTZ '2025-01-01 09:30'),  -- C4
            ('P1', 'E1', '4548-4',   53.0, '53',  'mmol/mol', TIMESTAMPTZ '2025-01-01 09:31'),-- C4 and C5
            ('P1', 'EX', '39156-5',  NULL, 'n/a', 'kg/m2',  TIMESTAMPTZ '2025-01-01 09:40')   -- R2, C6
        ) t(patient_id, encounter_id, code, value_num, value_raw, units, obs_ts);
    """)
    return con


EXPECTED_FAILURES = {
    "U1": 1, "R1": 1, "R2": 1, "R4": 1, "T1": 1, "T2": 1, "T4": 1,
    "C1": 1, "C3": 1, "C4": 2, "C5": 1, "C6": 1,
    "M1": 1, "M2": 1, "M3": 1, "M4": 1,
}


def test_rules_catch_planted_errors():
    results = {r["rule_id"]: r["records_failing"] for r in validate.run(fixture()).iter_rows(named=True)}
    for rule_id, expected in EXPECTED_FAILURES.items():
        assert results[rule_id] == expected, f"{rule_id}: expected {expected}, got {results[rule_id]}"


def test_clean_rules_stay_clean():
    results = {r["rule_id"]: r["records_failing"] for r in validate.run(fixture()).iter_rows(named=True)}
    for rule_id in ["U2", "T3", "T5", "C2"]:
        assert results[rule_id] == 0, f"{rule_id} raised a false positive"
