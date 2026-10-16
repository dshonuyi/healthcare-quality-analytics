"""
Step 4 - Correlation analysis and predictive risk stratification.

Question: which adult patients are most likely to have unplanned acute-care use
(an ED visit or an unplanned hospital stay) next year, so care management can reach
them first?

Design
  * Features are measured as of 31 Dec of a baseline year; the outcome is any unplanned
    acute-care use in the following calendar year.
  * Temporal validation: train on baseline years 2022-2023 (outcomes 2023-2024),
    test on baseline year 2024 (outcomes 2025). The model never sees the test year,
    which mirrors how it would be used in production.
  * Two models: logistic regression (interpretable odds ratios) and XGBoost
    (non-linear benchmark).

Outputs (data/processed/):
  risk_dataset.parquet, correlations.csv, odds_ratios.csv, model_metrics.csv,
  lift_table.csv, calibration.csv, feature_importance.csv, risk_scores_2025.csv

Run from the repo root:
    python src/04_risk_model.py
"""
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import polars.selectors as cs
import statsmodels.api as sm
from scipy import stats
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

DB = Path("data/processed/ehr.duckdb")
OUT = Path("data/processed")
TRAIN_YEARS = [2022, 2023]
TEST_YEAR = 2024
TARGET = "outcome_acute_next_yr"
SEED = 42

NUMERIC = ["age", "income", "acute_visits_prior_yr", "ed_visits_prior_yr",
           "outpatient_visits_prior_yr", "cost_prior_yr", "active_conditions",
           "active_meds", "bmi", "open_care_gaps"]
BINARY = ["had_wellness_visit", "dx_ckd", "dx_diabetes", "dx_hypertension", "dx_copd",
          "dx_asthma", "dx_substance_use", "dx_anxiety_depression", "dx_chronic_pain", "dx_dementia"]
CATEGORICAL = {"gender": "M", "payer_category": "Commercial", "race_ethnicity": "White (non-Hispanic)"}

LABELS = {
    "acute_visits_prior_yr": "Acute-care visits, prior year",
    "ed_visits_prior_yr": "ED visits, prior year",
    "outpatient_visits_prior_yr": "Outpatient visits, prior year",
    "cost_prior_yr": "Total cost, prior year",
    "active_conditions": "Active chronic conditions",
    "active_meds": "Active medications",
    "open_care_gaps": "Open quality care gaps",
    "had_wellness_visit": "Had annual wellness visit",
    "dx_substance_use": "Substance use disorder",
    "dx_asthma": "Asthma",
    "dx_copd": "COPD",
    "dx_ckd": "Chronic kidney disease",
    "dx_diabetes": "Diabetes",
    "dx_hypertension": "Hypertension",
    "dx_anxiety_depression": "Anxiety / depression",
    "dx_chronic_pain": "Chronic pain",
    "dx_dementia": "Dementia",
    "age": "Age", "income": "Household income", "bmi": "BMI",
}


def load() -> pl.DataFrame:
    con = duckdb.connect(str(DB))
    con.execute(open("sql/02_quality_measures.sql").read())
    con.execute(open("sql/03_risk_features.sql").read())
    df = pl.concat([pl.from_arrow(con.execute(f"SELECT * FROM risk_dataset({y})").arrow())
                    for y in TRAIN_YEARS + [TEST_YEAR]], how="vertical_relaxed")
    df.write_parquet(OUT / "risk_dataset.parquet")
    return df


def design_matrix(df: pl.DataFrame, bmi_median: float) -> tuple[np.ndarray, list[str]]:
    x = df.with_columns(
        pl.col("bmi").fill_null(bmi_median),
        pl.col("income").fill_null(pl.col("income").median()),
        pl.col("cost_prior_yr").log1p(),                     # heavy right tail
        *[pl.col(c).cast(pl.Int8) for c in BINARY],
    )
    cols = NUMERIC + BINARY
    for c, ref in CATEGORICAL.items():
        for level in sorted(df[c].unique().to_list()):
            if level == ref:
                continue
            name = f"{c}={level}"
            x = x.with_columns((pl.col(c) == level).cast(pl.Int8).alias(name))
            cols.append(name)
    return x.select(cols).to_numpy().astype(float), cols


def correlations(train: pl.DataFrame) -> pl.DataFrame:
    """Point-biserial (Pearson with a binary outcome) and Spearman correlations, with p-values."""
    y = train[TARGET].cast(pl.Int8).to_numpy()
    rows = []
    for c in NUMERIC + BINARY:
        x = train[c].cast(pl.Float64)
        x = x.fill_null(x.median()).to_numpy()
        r, p = stats.pointbiserialr(y, x)
        rho, p_s = stats.spearmanr(x, y)
        rows.append({"feature": c, "label": LABELS.get(c, c), "pearson_r": round(r, 3),
                     "pearson_p": p, "spearman_rho": round(rho, 3), "spearman_p": p_s})
    return pl.DataFrame(rows).sort("pearson_r", descending=True)


def lift_table(y: np.ndarray, score: np.ndarray) -> pl.DataFrame:
    order = np.argsort(-score)
    y_sorted = y[order]
    total = y.sum()
    rows = []
    for pct in [5, 10, 20, 30, 50]:
        k = int(round(len(y) * pct / 100))
        captured = y_sorted[:k].sum()
        rows.append({"top_pct_of_patients": pct, "patients": k, "events_captured": int(captured),
                     "pct_of_all_events": round(100 * captured / total, 1),
                     "event_rate_in_group": round(100 * captured / k, 1),
                     "lift_vs_random": round((captured / k) / (total / len(y)), 2)})
    return pl.DataFrame(rows)


def main() -> None:
    df = load()
    train = df.filter(pl.col("baseline_year").is_in(TRAIN_YEARS))
    test = df.filter(pl.col("baseline_year") == TEST_YEAR)
    bmi_median = train["bmi"].median()

    # ---------------- Correlations ----------------
    corr = correlations(train)
    corr.write_csv(OUT / "correlations.csv")
    full_corr = (train.select(NUMERIC + BINARY + [TARGET])
                      .with_columns(cs.boolean().cast(pl.Int8))
                      .with_columns(pl.col("bmi").fill_null(bmi_median), pl.col("income").fill_null(pl.col("income").median()))
                      .corr())
    full_corr.write_csv(OUT / "correlation_matrix.csv")

    X_tr, cols = design_matrix(train, bmi_median)
    X_te, _ = design_matrix(test, bmi_median)
    y_tr = train[TARGET].cast(pl.Int8).to_numpy()
    y_te = test[TARGET].cast(pl.Int8).to_numpy()

    # ---------------- Logistic regression: odds ratios ----------------
    # Continuous features are standardized so each odds ratio is "per 1 SD".
    cont_idx = [cols.index(c) for c in NUMERIC]
    mu, sd = X_tr[:, cont_idx].mean(0), X_tr[:, cont_idx].std(0)
    Xs_tr = X_tr.copy(); Xs_tr[:, cont_idx] = (Xs_tr[:, cont_idx] - mu) / sd
    # drop features with no variation in training (would make the fit singular)
    keep = [i for i in range(Xs_tr.shape[1]) if Xs_tr[:, i].std() > 0]
    logit = sm.Logit(y_tr, sm.add_constant(Xs_tr[:, keep])).fit(disp=0, method="bfgs", maxiter=2000)
    ci = logit.conf_int()
    or_rows = []
    for j, i in enumerate(keep, start=1):
        c = cols[i]
        or_rows.append({"feature": c, "label": LABELS.get(c, c),
                        "scale": "per 1 SD" if c in NUMERIC else "yes vs no",
                        "odds_ratio": round(float(np.exp(logit.params[j])), 3),
                        "ci_low": round(float(np.exp(ci[j][0])), 3),
                        "ci_high": round(float(np.exp(ci[j][1])), 3),
                        "p_value": float(logit.pvalues[j])})
    odds = pl.DataFrame(or_rows).sort("p_value")
    odds.write_csv(OUT / "odds_ratios.csv")

    # ---------------- Predictive models ----------------
    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=0.5, class_weight=None))
    lr.fit(X_tr, y_tr)
    p_lr = lr.predict_proba(X_te)[:, 1]

    xgb = XGBClassifier(n_estimators=300, max_depth=3, learning_rate=0.03, subsample=0.8,
                        colsample_bytree=0.8, min_child_weight=5, reg_lambda=1.0,
                        eval_metric="logloss", random_state=SEED)
    xgb.fit(X_tr, y_tr)
    p_xgb = xgb.predict_proba(X_te)[:, 1]

    # Simple rule-based comparator that a clinic could use without a model
    baseline = test["acute_visits_prior_yr"].to_numpy().astype(float)

    metrics = []
    for name, p in [("Rule: prior-year acute visits", baseline),
                    ("Logistic regression", p_lr), ("XGBoost", p_xgb)]:
        top10 = lift_table(y_te, p).filter(pl.col("top_pct_of_patients") == 10).row(0, named=True)
        metrics.append({"model": name,
                        "auc_roc": round(roc_auc_score(y_te, p), 3),
                        "pr_auc": round(average_precision_score(y_te, p), 3),
                        "brier": round(brier_score_loss(y_te, p), 4) if p.max() <= 1 else None,
                        "top10_pct_events_captured": top10["pct_of_all_events"],
                        "top10_lift": top10["lift_vs_random"]})
    metrics = pl.DataFrame(metrics)
    metrics.write_csv(OUT / "model_metrics.csv")

    # Prefer the interpretable model unless XGBoost is materially better (> 0.02 AUC):
    # care managers and clinicians need to see why a patient was flagged.
    auc = dict(zip(metrics["model"], metrics["auc_roc"]))
    best_name = "XGBoost" if auc["XGBoost"] - auc["Logistic regression"] > 0.02 else "Logistic regression"
    p_best = p_lr if best_name == "Logistic regression" else p_xgb
    lift = lift_table(y_te, p_best).with_columns(pl.lit(best_name).alias("model"))
    lift.write_csv(OUT / "lift_table.csv")

    # Calibration by risk decile
    dec = pl.DataFrame({"pred": p_best, "obs": y_te}).with_columns(
        decile=(pl.col("pred").rank("ordinal") * 10 / pl.len()).ceil().cast(pl.Int8))
    dec.group_by("decile").agg(predicted=pl.col("pred").mean().round(3), observed=pl.col("obs").mean().round(3),
                               patients=pl.len()).sort("decile").write_csv(OUT / "calibration.csv")

    # Permutation importance on the held-out year (model-agnostic)
    model = lr if best_name == "Logistic regression" else xgb
    pi = permutation_importance(model, X_te, y_te, scoring="roc_auc", n_repeats=20, random_state=SEED)
    pl.DataFrame({"feature": cols, "label": [LABELS.get(c, c) for c in cols],
                  "auc_drop_mean": pi.importances_mean.round(4), "auc_drop_std": pi.importances_std.round(4)}) \
        .sort("auc_drop_mean", descending=True).write_csv(OUT / "feature_importance.csv")

    # Risk scores for the 2025 outreach list
    test.select("patient_id", "age", "gender", "payer_category", "acute_visits_prior_yr", "active_conditions",
                "open_care_gaps", "dx_substance_use") \
        .with_columns(risk_score=pl.Series(p_best).round(3), had_event_2025=pl.Series(y_te).cast(pl.Boolean)) \
        .with_columns(risk_tier=pl.when(pl.col("risk_score").rank(descending=True) <= len(y_te) * 0.10).then(pl.lit("High (top 10%)"))
                               .when(pl.col("risk_score").rank(descending=True) <= len(y_te) * 0.30).then(pl.lit("Rising (next 20%)"))
                               .otherwise(pl.lit("Low"))) \
        .sort("risk_score", descending=True).write_csv(OUT / "risk_scores_2025.csv")

    with pl.Config(tbl_rows=40, tbl_width_chars=180, fmt_str_lengths=40):
        print(f"train: {len(y_tr):,} rows, {y_tr.sum()} events | test: {len(y_te):,} rows, {y_te.sum()} events")
        print(corr.select("label", "pearson_r", "pearson_p", "spearman_rho"))
        print(odds.select("label", "scale", "odds_ratio", "ci_low", "ci_high", "p_value"))
        print(metrics)
        print(lift)
        print(pl.read_csv(OUT / "feature_importance.csv").head(10))
        print(pl.read_csv(OUT / "calibration.csv"))


if __name__ == "__main__":
    main()
