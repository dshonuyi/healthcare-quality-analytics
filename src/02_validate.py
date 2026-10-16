"""
Step 2 - Data validation.

Runs a rule-based data quality audit against the EHR warehouse before any measure is
calculated. Each rule returns the number of failing records out of the records it
applies to, a severity, and the action an analyst would take.

Categories follow the dimensions most quality teams report on:
  - Uniqueness           (no duplicate keys)
  - Referential integrity (child records point to a real parent)
  - Temporal validity    (dates are in a logical order)
  - Clinical plausibility (vitals and labs are physiologically possible)
  - Completeness         (fields needed for stratification are populated)

Run from the repo root:
    python src/02_validate.py
"""
from dataclasses import dataclass
from pathlib import Path

import duckdb
import polars as pl

DB = Path("data/processed/ehr.duckdb")
OUT_CSV = Path("data/processed/data_quality_results.csv")
OUT_MD = Path("reports/data_quality_report.md")


@dataclass
class Rule:
    rule_id: str
    category: str
    description: str
    severity: str          # Critical = blocks measure use, Warning = review, Info = monitor
    failing_sql: str       # returns one integer: failing records
    population_sql: str    # returns one integer: records the rule applies to
    action: str


EXTRACT_TS = "(SELECT max(start_ts) FROM encounters)"

RULES: list[Rule] = [
    # ---------- Uniqueness ----------
    Rule("U1", "Uniqueness", "Duplicate patient IDs", "Critical",
         "SELECT count(*) - count(DISTINCT patient_id) FROM patients",
         "SELECT count(*) FROM patients",
         "Merge duplicate MRNs before attribution; duplicates double-count denominators."),
    Rule("U2", "Uniqueness", "Duplicate encounter IDs", "Critical",
         "SELECT count(*) - count(DISTINCT encounter_id) FROM encounters",
         "SELECT count(*) FROM encounters",
         "Deduplicate encounters; duplicates inflate utilization and readmissions."),
    Rule("U3", "Uniqueness", "Same patient, same vital sign, same timestamp recorded more than once", "Info",
         """SELECT coalesce(sum(n - 1), 0) FROM (
                SELECT count(*) n FROM observations
                WHERE code IN ('8480-6','8462-4','4548-4')
                GROUP BY patient_id, code, obs_ts HAVING count(*) > 1)""",
         "SELECT count(*) FROM observations WHERE code IN ('8480-6','8462-4','4548-4')",
         "Keep one value per timestamp so 'most recent reading' logic is deterministic."),

    # ---------- Referential integrity ----------
    Rule("R1", "Referential integrity", "Encounters whose patient does not exist", "Critical",
         "SELECT count(*) FROM encounters e LEFT JOIN patients p USING (patient_id) WHERE p.patient_id IS NULL",
         "SELECT count(*) FROM encounters",
         "Route to registration/HIM to resolve orphaned visits."),
    Rule("R2", "Referential integrity", "Observations linked to a non-existent encounter", "Warning",
         """SELECT count(*) FROM observations o LEFT JOIN encounters e USING (encounter_id)
            WHERE o.encounter_id IS NOT NULL AND e.encounter_id IS NULL""",
         "SELECT count(*) FROM observations WHERE encounter_id IS NOT NULL",
         "Labs without a visit cannot be attributed to a provider or date of service."),
    Rule("R3", "Referential integrity", "Conditions linked to a non-existent encounter", "Warning",
         """SELECT count(*) FROM conditions c LEFT JOIN encounters e USING (encounter_id)
            WHERE c.encounter_id IS NOT NULL AND e.encounter_id IS NULL""",
         "SELECT count(*) FROM conditions WHERE encounter_id IS NOT NULL",
         "Diagnoses without a source visit weaken denominator evidence."),
    Rule("R4", "Referential integrity", "Encounters with no payer on file", "Warning",
         """SELECT count(*) FROM encounters e LEFT JOIN payers p USING (payer_id)
            WHERE p.payer_id IS NULL""",
         "SELECT count(*) FROM encounters",
         "Payer is needed to assign the measure to the right program (Medicare Stars, Medi-Cal, commercial)."),

    # ---------- Temporal validity ----------
    Rule("T1", "Temporal validity", "Encounter ends before it starts", "Critical",
         "SELECT count(*) FROM encounters WHERE stop_ts < start_ts",
         "SELECT count(*) FROM encounters",
         "Fix timestamps; negative length of stay breaks readmission windows."),
    Rule("T2", "Temporal validity", "Clinical encounter starts after the patient's date of death", "Critical",
         """SELECT count(*) FROM encounters e JOIN patients p USING (patient_id)
            WHERE p.death_date IS NOT NULL AND e.start_ts::DATE > p.death_date
              AND e.description <> 'Death Certification'""",
         "SELECT count(*) FROM encounters",
         "Post-mortem clinical activity indicates a wrong patient match or a death-date error."),
    Rule("T2a", "Temporal validity", "Administrative 'Death Certification' encounter after date of death", "Info",
         """SELECT count(*) FROM encounters e JOIN patients p USING (patient_id)
            WHERE p.death_date IS NOT NULL AND e.start_ts::DATE > p.death_date
              AND e.description = 'Death Certification'""",
         "SELECT count(*) FROM encounters",
         "Expected: paperwork filed 1-14 days after death. Exclude from utilization counts; no correction needed."),
    Rule("T3", "Temporal validity", "Encounter starts before the patient's birth date", "Critical",
         """SELECT count(*) FROM encounters e JOIN patients p USING (patient_id)
            WHERE e.start_ts::DATE < p.birth_date""",
         "SELECT count(*) FROM encounters",
         "Pre-birth activity indicates a mis-keyed birth date (affects age-based eligibility)."),
    Rule("T4", "Temporal validity", "Condition resolved before it began", "Warning",
         "SELECT count(*) FROM conditions WHERE stop_date < start_date",
         "SELECT count(*) FROM conditions",
         "Correct problem-list dates; drives whether a diagnosis is 'active'."),
    Rule("T5", "Temporal validity", f"Records dated after the extract date", "Warning",
         f"SELECT count(*) FROM observations WHERE obs_ts > {EXTRACT_TS}",
         "SELECT count(*) FROM observations",
         "Future-dated results usually mean a data-entry or interface time-zone error."),
    Rule("T6", "Temporal validity", "Inpatient stays longer than 60 days", "Info",
         """SELECT count(*) FROM encounters WHERE encounter_class = 'inpatient'
            AND date_diff('day', start_ts, stop_ts) > 60""",
         "SELECT count(*) FROM encounters WHERE encounter_class = 'inpatient'",
         "Review outliers; long stays can be real but often reflect a missing discharge."),

    # ---------- Clinical plausibility ----------
    Rule("C1", "Clinical plausibility", "Systolic BP outside 50-300 mmHg", "Critical",
         "SELECT count(*) FROM observations WHERE code = '8480-6' AND (value_num < 50 OR value_num > 300)",
         "SELECT count(*) FROM observations WHERE code = '8480-6'",
         "Exclude from CBP numerator and send back to the clinic for correction."),
    Rule("C2", "Clinical plausibility", "Diastolic BP outside 20-200 mmHg", "Critical",
         "SELECT count(*) FROM observations WHERE code = '8462-4' AND (value_num < 20 OR value_num > 200)",
         "SELECT count(*) FROM observations WHERE code = '8462-4'",
         "Exclude from CBP numerator and send back to the clinic for correction."),
    Rule("C3", "Clinical plausibility", "Diastolic reading >= systolic reading at the same visit", "Critical",
         """SELECT count(*) FROM observations s JOIN observations d
              ON s.patient_id = d.patient_id AND s.obs_ts = d.obs_ts
            WHERE s.code = '8480-6' AND d.code = '8462-4' AND d.value_num >= s.value_num""",
         "SELECT count(*) FROM observations WHERE code = '8480-6'",
         "Likely transposed values; flag for chart review."),
    Rule("C4", "Clinical plausibility", "HbA1c outside the 4-18 % assay reportable range", "Critical",
         "SELECT count(*) FROM observations WHERE code = '4548-4' AND (value_num < 4 OR value_num > 18)",
         "SELECT count(*) FROM observations WHERE code = '4548-4'",
         "Values below 4% are not reportable by standard assays; in production this pattern usually means an interface or mapping error. Excluded from GSD; send to lab interface team."),
    Rule("C5", "Clinical plausibility", "HbA1c recorded in a unit other than %", "Warning",
         "SELECT count(*) FROM observations WHERE code = '4548-4' AND coalesce(units, '') <> '%'",
         "SELECT count(*) FROM observations WHERE code = '4548-4'",
         "Mixed units (e.g. mmol/mol) must be converted before applying the 8% / 9% thresholds."),
    Rule("C6", "Clinical plausibility", "Numeric lab or vital that will not parse as a number", "Warning",
         """SELECT count(*) FROM observations
            WHERE code IN ('8480-6','8462-4','4548-4','39156-5') AND value_num IS NULL""",
         "SELECT count(*) FROM observations WHERE code IN ('8480-6','8462-4','4548-4','39156-5')",
         "Text in numeric result fields cannot be counted toward a measure."),
    Rule("C7", "Clinical plausibility", "BMI outside 10-100", "Warning",
         "SELECT count(*) FROM observations WHERE code = '39156-5' AND (value_num < 10 OR value_num > 100)",
         "SELECT count(*) FROM observations WHERE code = '39156-5'",
         "Check height/weight entry."),

    # ---------- Completeness ----------
    Rule("M1", "Completeness", "Patient missing race or ethnicity", "Warning",
         "SELECT count(*) FROM patients WHERE race IS NULL OR ethnicity IS NULL",
         "SELECT count(*) FROM patients",
         "Needed for health-equity stratification (CMS and NCQA now require it)."),
    Rule("M2", "Completeness", "Patient missing gender or birth date", "Critical",
         "SELECT count(*) FROM patients WHERE gender IS NULL OR birth_date IS NULL",
         "SELECT count(*) FROM patients",
         "Age and sex define eligibility for nearly every measure."),
    Rule("M3", "Completeness", "Patient missing ZIP code", "Info",
         "SELECT count(*) FROM patients WHERE zip IS NULL",
         "SELECT count(*) FROM patients",
         "ZIP supports geographic outreach and SDOH linkage."),
    Rule("M4", "Completeness", "Encounter missing provider", "Warning",
         "SELECT count(*) FROM encounters WHERE provider_id IS NULL",
         "SELECT count(*) FROM encounters",
         "Provider is needed for attribution and provider-level scorecards."),
]


def run(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    rows = []
    for r in RULES:
        failing = con.execute(r.failing_sql).fetchone()[0] or 0
        population = con.execute(r.population_sql).fetchone()[0] or 0
        rows.append({
            "rule_id": r.rule_id,
            "category": r.category,
            "description": r.description,
            "severity": r.severity,
            "records_checked": population,
            "records_failing": failing,
            "pct_failing": round(100 * failing / population, 3) if population else 0.0,
            "status": "PASS" if failing == 0 else ("FLAG" if r.severity == "Info" else "FAIL"),
            "action": r.action,
        })
    return pl.DataFrame(rows)


def write_report(df: pl.DataFrame) -> None:
    total = df.height
    failed = df.filter(pl.col("status") == "FAIL")
    flagged = df.filter(pl.col("status") == "FLAG")
    crit = failed.filter(pl.col("severity") == "Critical")
    lines = [
        "# Data Quality Report - Synthetic EHR Extract",
        "",
        f"**Rules run:** {total}  |  **Passed:** {total - failed.height - flagged.height}  |  "
        f"**Failed:** {failed.height} (Critical: {crit.height})  |  **Flagged for monitoring:** {flagged.height}",
        "",
        "Rules are grouped by data quality dimension. A *Critical* failure means the affected records are "
        "excluded from measure calculation until corrected; *Warning* means review; *Info* means monitor.",
        "",
        "| ID | Dimension | Rule | Severity | Checked | Failing | % | Status |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for r in df.iter_rows(named=True):
        lines.append(
            f"| {r['rule_id']} | {r['category']} | {r['description']} | {r['severity']} | "
            f"{r['records_checked']:,} | {r['records_failing']:,} | {r['pct_failing']:.3f} | {r['status']} |"
        )
    lines += ["", "## Findings and actions", ""]
    if failed.height == 0:
        lines.append("All rules passed.")
    for r in df.filter(pl.col("status") != "PASS").iter_rows(named=True):
        lines.append(f"- **{r['rule_id']} - {r['description']}** [{r['severity']}] ({r['records_failing']:,} records, "
                     f"{r['pct_failing']:.2f}%). {r['action']}")
    lines += [
        "",
        "## Notes",
        "",
        "- Synthea data is generated by simulation, so it is much cleaner than a production EHR. The rules "
        "are written for production data; `tests/test_validation.py` injects known errors into a small "
        "fixture and confirms 16 rules catch exactly the planted errors while 4 others raise no false positives.",
        "- The same rule set can be scheduled to run on every refresh, with results trended over time as a "
        "data-quality scorecard for the clinical effectiveness team.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n")


def main() -> None:
    con = duckdb.connect(str(DB), read_only=True)
    df = run(con)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.write_csv(OUT_CSV)
    write_report(df)
    with pl.Config(tbl_rows=50, tbl_width_chars=160, fmt_str_lengths=60):
        print(df.select("rule_id", "description", "records_checked", "records_failing", "pct_failing", "status"))


if __name__ == "__main__":
    main()
