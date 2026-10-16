"""
Step 3 - Quality measure calculation, stratification and care-gap analysis.

Outputs (data/processed/):
  measure_patient_level.parquet   one row per eligible patient x measure x year
  measure_rates_by_year.csv       trend table
  measure_rates_by_strata.csv     measurement-year rates by payer, race/ethnicity, income, sex
  stratification_tests.csv        chi-square tests: does the rate differ across groups?
  care_gap_list.csv               patients open on a measure in the measurement year (outreach list)
  gap_reasons.csv                 why patients are open (for Pareto analysis)

Run from the repo root:
    python src/03_quality_measures.py
"""
from pathlib import Path

import duckdb
import polars as pl
from scipy.stats import chi2_contingency

DB = Path("data/processed/ehr.duckdb")
OUT = Path("data/processed")
MEASUREMENT_YEAR = 2025          # last complete calendar year in the extract
TREND_YEARS = [2022, 2023, 2024, 2025]
MEASURES = ["cbp", "gsd", "bcs", "col"]

# Public 2026 Medicare Part C Star Ratings cut points (CY2024 performance), used as
# external reference points. See README for source.
STAR_CUTPOINTS = {
    "CBP": {"3_star": 75, "4_star": 80, "5_star": 86},
    "GSD": {"3_star": 77, "4_star": 87, "5_star": 91},
    "BCS": {"3_star": 71, "4_star": 76, "5_star": 84},
    "COL": {"3_star": 60, "4_star": 70, "5_star": 78},
}
STRATA = ["payer_category", "race_ethnicity", "income_band", "gender"]


def star_level(measure: str, rate: float) -> str:
    c = STAR_CUTPOINTS[measure]
    if rate >= c["5_star"]:
        return "5 stars"
    if rate >= c["4_star"]:
        return "4 stars"
    if rate >= c["3_star"]:
        return "3 stars"
    return "Below 3 stars"


def main() -> None:
    con = duckdb.connect(str(DB))
    con.execute(open("sql/02_quality_measures.sql").read())

    frames = []
    for my in TREND_YEARS:
        for m in MEASURES:
            frames.append(
                pl.from_arrow(con.execute(
                    f"""SELECT measure, measure_name, patient_id, measurement_year, age, gender,
                               race_ethnicity, payer_category, income, income_band, zip,
                               last_service_date, in_numerator, gap_reason
                        FROM measure_{m}({my})"""
                ).arrow())
            )
    detail = pl.concat(frames, how="vertical_relaxed")
    detail.write_parquet(OUT / "measure_patient_level.parquet")

    # ---- Trend ----
    trend = (
        detail.group_by("measure", "measure_name", "measurement_year")
        .agg(denominator=pl.len(), numerator=pl.col("in_numerator").sum())
        .with_columns(rate=(100 * pl.col("numerator") / pl.col("denominator")).round(1))
        .sort("measure", "measurement_year")
    )
    trend = trend.with_columns(
        pl.struct("measure", "rate").map_elements(lambda r: star_level(r["measure"], r["rate"]),
                                                  return_dtype=pl.String).alias("star_level"),
        pl.col("measure").replace_strict({k: v["4_star"] for k, v in STAR_CUTPOINTS.items()}).alias("four_star_target"),
    )
    trend.write_csv(OUT / "measure_rates_by_year.csv")

    my = detail.filter(pl.col("measurement_year") == MEASUREMENT_YEAR)

    # ---- Stratification + chi-square tests ----
    strata_rows, tests = [], []
    for s in STRATA:
        g = (my.group_by("measure", s)
               .agg(denominator=pl.len(), numerator=pl.col("in_numerator").sum())
               .with_columns(rate=(100 * pl.col("numerator") / pl.col("denominator")).round(1),
                             stratifier=pl.lit(s))
               .rename({s: "group"}))
        strata_rows.append(g.select("measure", "stratifier", "group", "denominator", "numerator", "rate"))
        for meas in g["measure"].unique().to_list():
            sub = g.filter((pl.col("measure") == meas) & (pl.col("denominator") >= 10))
            if sub.height < 2:
                continue
            table = [[r["numerator"], r["denominator"] - r["numerator"]] for r in sub.iter_rows(named=True)]
            chi2, p, dof, _ = chi2_contingency(table)
            tests.append({"measure": meas, "stratifier": s, "groups_tested": sub.height,
                          "chi2": round(chi2, 2), "dof": dof, "p_value": round(p, 4),
                          "significant_at_05": p < 0.05,
                          "max_gap_pts": round(sub["rate"].max() - sub["rate"].min(), 1)})
    strata = pl.concat(strata_rows).sort("measure", "stratifier", "group")
    strata.write_csv(OUT / "measure_rates_by_strata.csv")
    pl.DataFrame(tests).sort("measure", "p_value").write_csv(OUT / "stratification_tests.csv")

    # ---- Care gaps ----
    gaps = my.filter(~pl.col("in_numerator"))
    gaps.select("measure", "patient_id", "age", "gender", "payer_category", "race_ethnicity",
                "zip", "last_service_date", "gap_reason").sort("measure", "payer_category") \
        .write_csv(OUT / "care_gap_list.csv")
    reasons = (gaps.group_by("measure", "gap_reason").agg(patients=pl.len())
                    .sort("measure", "patients", descending=[False, True]))
    reasons.write_csv(OUT / "gap_reasons.csv")

    # ---- Sensitivity: effect of validation on the diabetes measure ----
    raw = pl.from_arrow(con.execute(f"SELECT * FROM measure_gsd_unvalidated({MEASUREMENT_YEAR})").arrow())
    val = my.filter(pl.col("measure") == "GSD")
    pl.DataFrame({
        "version": ["Raw HbA1c values (no validation)", "Validated HbA1c values"],
        "denominator": [raw.height, val.height],
        "numerator": [int(raw["in_numerator"].sum()), int(val["in_numerator"].sum())],
    }).with_columns(rate=(100 * pl.col("numerator") / pl.col("denominator")).round(1)) \
      .write_csv(OUT / "gsd_validation_sensitivity.csv")

    # ---- Console summary ----
    with pl.Config(tbl_rows=40, tbl_width_chars=160):
        print(trend)
        print(pl.read_csv(OUT / "stratification_tests.csv"))
        print(reasons)
        print(pl.read_csv(OUT / "gsd_validation_sensitivity.csv"))
    print(f"\nOpen care gaps in {MEASUREMENT_YEAR}: {gaps.height:,} patient-measures "
          f"across {gaps['patient_id'].n_unique():,} patients")


if __name__ == "__main__":
    main()
