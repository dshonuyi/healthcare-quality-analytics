"""
Step 6 - Real-world hospital benchmarking with public CMS Care Compare data.

Source files (CMS Provider Data Catalog, Hospitals topic), saved to data/cms/:
  hospital_general_information.csv   overall star rating, ownership, type
  unplanned_hospital_visits.csv      30-day readmission rates (HRRP conditions) and return days
  hcahps_hospital.csv                HCAHPS patient experience survey (linear mean scores)

Scope: California short-term acute care hospitals (the hospitals HRRP and the
overall star rating apply to).

Questions
  1. Does patient experience track readmissions? In particular, do hospitals whose patients
     rate discharge information and medication communication highly readmit fewer patients?
  2. What moves with the CMS overall star rating?
  3. Do readmission rates differ by ownership type?
  4. How do San Diego County hospitals compare with the state?

Run from the repo root:
    python src/06_cms_hospital_analysis.py
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import statsmodels.api as sm
from matplotlib.colors import LinearSegmentedColormap
from scipy import stats

CMS = Path("data/cms")
OUT = Path("data/processed")
FIG = Path("figures")

READM = {"READM_30_HF": "Heart failure", "READM_30_PN": "Pneumonia",
         "READM_30_COPD": "COPD", "READM_30_AMI": "Heart attack"}
HCAHPS = {
    "H_COMP_1_LINEAR_SCORE": "Nurse communication",
    "H_COMP_2_LINEAR_SCORE": "Doctor communication",
    "H_COMP_5_LINEAR_SCORE": "Communication about medicines",
    "H_COMP_6_LINEAR_SCORE": "Discharge information",
    "H_CLEAN_LINEAR_SCORE": "Cleanliness",
    "H_QUIET_LINEAR_SCORE": "Quietness",
    "H_HSP_RATING_LINEAR_SCORE": "Overall hospital rating",
    "H_RECMND_LINEAR_SCORE": "Would recommend",
}

SURFACE, INK, INK_2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8985", "#e6e5e1"
BLUE, RED, NEUTRAL, BLUE_LIGHT = "#2a78d6", "#e34948", "#f0efec", "#86b6ef"
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": "DejaVu Sans", "font.size": 10, "text.color": INK, "axes.edgecolor": GRID,
    "axes.labelcolor": INK_2, "axes.titlesize": 11, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "xtick.color": INK_2, "ytick.color": INK_2, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.8, "axes.spines.top": False, "axes.spines.right": False,
    "axes.spines.left": False, "legend.frameon": False,
})


def num(col: str) -> pl.Expr:
    return pl.col(col).cast(pl.Float64, strict=False)


def build() -> pl.DataFrame:
    gen = pl.read_csv(CMS / "hospital_general_information.csv", infer_schema=False)
    gen = gen.filter((pl.col("State") == "CA") & (pl.col("Hospital Type") == "Acute Care Hospitals")).select(
        facility_id="Facility ID", name="Facility Name", city="City/Town", county="County/Parish",
        ownership="Hospital Ownership", emergency_services="Emergency Services",
        star_rating=num("Hospital overall rating"),
        readm_measures_worse=num("Count of READM Measures Worse"),
    ).with_columns(ownership_group=pl.when(pl.col("ownership").str.starts_with("Voluntary")).then(pl.lit("Non-profit"))
                   .when(pl.col("ownership") == "Proprietary").then(pl.lit("For-profit"))
                   .when(pl.col("ownership").str.starts_with("Government")).then(pl.lit("Government"))
                   .otherwise(pl.lit("Other")))

    uv = pl.read_csv(CMS / "unplanned_hospital_visits.csv", infer_schema=False).filter(
        (pl.col("State") == "CA") & pl.col("Measure ID").is_in(list(READM)))
    readm = uv.with_columns(score=num("Score"), denom=num("Denominator"),
                            lo=num("Lower Estimate"), hi=num("Higher Estimate"))
    wide = readm.pivot(on="Measure ID", index="Facility ID", values="score")
    ci = readm.filter(pl.col("Measure ID") == "READM_30_HF").select(
        pl.col("Facility ID"), hf_lo="lo", hf_hi="hi", hf_cases="denom",
        hf_vs_national="Compared to National")

    hc = pl.read_csv(CMS / "hcahps_hospital.csv", infer_schema=False).filter(
        (pl.col("State") == "CA") & pl.col("HCAHPS Measure ID").is_in(list(HCAHPS)))
    hc_w = hc.with_columns(v=num("HCAHPS Linear Mean Value")).pivot(on="HCAHPS Measure ID", index="Facility ID", values="v")
    surveys = (pl.read_csv(CMS / "hcahps_hospital.csv", infer_schema=False)
                 .filter((pl.col("State") == "CA") & (pl.col("HCAHPS Measure ID") == "H_STAR_RATING"))
                 .select(pl.col("Facility ID"), hcahps_summary_star=num("Patient Survey Star Rating"),
                         hcahps_surveys=num("Number of Completed Surveys")))

    df = (gen.join(wide.rename({"Facility ID": "facility_id"}), on="facility_id", how="left")
             .join(ci.rename({"Facility ID": "facility_id"}), on="facility_id", how="left")
             .join(hc_w.rename({"Facility ID": "facility_id"}), on="facility_id", how="left")
             .join(surveys.rename({"Facility ID": "facility_id"}), on="facility_id", how="left"))
    # Composite readmission index: mean z-score across HF, pneumonia, COPD (the three most-reported
    # HRRP conditions); requires all three so hospitals are compared on the same basis.
    z = [((pl.col(m) - pl.col(m).mean()) / pl.col(m).std()) for m in ["READM_30_HF", "READM_30_PN", "READM_30_COPD"]]
    df = df.with_columns(readm_index=pl.mean_horizontal(z).round(3)).with_columns(
        pl.when(pl.any_horizontal([pl.col(m).is_null() for m in ["READM_30_HF", "READM_30_PN", "READM_30_COPD"]]))
          .then(None).otherwise(pl.col("readm_index")).alias("readm_index"))
    return df


def spearman_table(df: pl.DataFrame, xs: dict, ys: dict) -> pl.DataFrame:
    rows = []
    for x, xl in xs.items():
        for y, yl in ys.items():
            d = df.select(x, y).drop_nulls()
            if d.height < 30:
                continue
            rho, p = stats.spearmanr(d[x], d[y])
            rows.append({"x": x, "x_label": xl, "y": y, "y_label": yl, "n_hospitals": d.height,
                         "spearman_rho": round(rho, 3), "p_value": p})
    return pl.DataFrame(rows)


def main() -> None:
    df = build()
    df.write_csv(OUT / "cms_ca_hospitals.csv")
    n = df.height
    print(f"California acute care hospitals: {n}")
    print(df.select([pl.col(c).is_not_null().sum().alias(c) for c in
                     ["star_rating", "READM_30_HF", "READM_30_PN", "READM_30_COPD", "readm_index", "H_COMP_6_LINEAR_SCORE"]]))

    # ---- Q1: patient experience vs readmissions ----
    ys = {**{k: v + " readmission" for k, v in READM.items()}, "readm_index": "Readmission index (HF, PN, COPD)"}
    q1 = spearman_table(df, HCAHPS, ys)
    # Benjamini-Hochberg correction across the whole grid of tests
    p = q1["p_value"].to_numpy(); order = np.argsort(p); m = len(p)
    adj = np.empty(m); prev = 1.0
    for rank, i in enumerate(order[::-1]):
        k = m - rank
        prev = min(prev, p[i] * m / k); adj[i] = prev
    q1 = q1.with_columns(p_fdr=pl.Series(adj)).sort("y", "spearman_rho")
    q1.write_csv(OUT / "cms_hcahps_vs_readmission.csv")

    # ---- Q2: what moves with the overall star rating ----
    q2 = spearman_table(df, {**HCAHPS, **{k: v + " readmission" for k, v in READM.items()},
                             "readm_index": "Readmission index"}, {"star_rating": "Overall star rating"}) \
        .sort("spearman_rho")
    q2.write_csv(OUT / "cms_star_rating_correlates.csv")

    # Multivariable check: does discharge information still relate to readmissions after
    # accounting for overall experience, ownership and size?
    reg = df.select("readm_index", "H_COMP_6_LINEAR_SCORE", "H_HSP_RATING_LINEAR_SCORE", "hf_cases",
                    "ownership_group").drop_nulls().filter(pl.col("ownership_group") != "Other")
    X = reg.select(
        discharge_info=(pl.col("H_COMP_6_LINEAR_SCORE") - pl.col("H_COMP_6_LINEAR_SCORE").mean()) / pl.col("H_COMP_6_LINEAR_SCORE").std(),
        overall_rating=(pl.col("H_HSP_RATING_LINEAR_SCORE") - pl.col("H_HSP_RATING_LINEAR_SCORE").mean()) / pl.col("H_HSP_RATING_LINEAR_SCORE").std(),
        log_hf_volume=pl.col("hf_cases").log(),
        for_profit=(pl.col("ownership_group") == "For-profit").cast(pl.Int8),
        government=(pl.col("ownership_group") == "Government").cast(pl.Int8),
    )
    ols = sm.OLS(reg["readm_index"].to_numpy(), sm.add_constant(X.to_numpy().astype(float))).fit(cov_type="HC3")
    ci = ols.conf_int()
    ols_tbl = pl.DataFrame({"term": ["intercept"] + X.columns, "coef": ols.params.round(3),
                            "ci_low": ci[:, 0].round(3), "ci_high": ci[:, 1].round(3),
                            "p_value": ols.pvalues.round(4)})
    ols_tbl.write_csv(OUT / "cms_readmission_regression.csv")

    # ---- Q3: ownership ----
    own = (df.filter(pl.col("ownership_group") != "Other")
             .group_by("ownership_group")
             .agg(hospitals=pl.len(), median_hf_readm=pl.col("READM_30_HF").median(),
                  median_index=pl.col("readm_index").median(), mean_star=pl.col("star_rating").mean().round(2),
                  median_discharge_info=pl.col("H_COMP_6_LINEAR_SCORE").median())
             .sort("ownership_group"))
    groups = [g["READM_30_HF"].drop_nulls().to_numpy() for _, g in
              df.filter(pl.col("ownership_group") != "Other").group_by("ownership_group")]
    kw = stats.kruskal(*groups)
    own = own.with_columns(kruskal_p_hf=pl.lit(round(kw.pvalue, 4)))
    own.write_csv(OUT / "cms_ownership.csv")

    # ---- Q4: San Diego County ----
    state_median_hf = df["READM_30_HF"].median()
    sd = (df.filter(pl.col("county") == "SAN DIEGO")
            .select("name", "city", "star_rating", "READM_30_HF",
                    "READM_30_PN", "READM_30_COPD", "H_COMP_6_LINEAR_SCORE", "hcahps_summary_star")
            .sort("READM_30_HF", nulls_last=True))
    sd.write_csv(OUT / "cms_san_diego_hospitals.csv")

    with pl.Config(tbl_rows=60, tbl_width_chars=200, fmt_str_lengths=45):
        print(q1.filter(pl.col("y") == "readm_index").select("x_label", "n_hospitals", "spearman_rho", "p_value", "p_fdr"))
        print(q1.filter(pl.col("x") == "H_COMP_6_LINEAR_SCORE").select("y_label", "n_hospitals", "spearman_rho", "p_fdr"))
        print(q2.select("x_label", "n_hospitals", "spearman_rho", "p_value"))
        print(ols_tbl, f"R2={ols.rsquared:.3f}, n={int(ols.nobs)}")
        print(own)
        print(f"state median HF readmission: {state_median_hf}")
        print(sd)

    figures(df, q1, q2, sd, state_median_hf)


def figures(df, q1, q2, sd, state_median_hf):
    # Correlation heatmap: HCAHPS dimensions x readmission measures
    ys = ["READM_30_HF", "READM_30_PN", "READM_30_COPD", "READM_30_AMI", "readm_index"]
    ylabels = ["Heart failure", "Pneumonia", "COPD", "Heart attack", "Composite\nindex"]
    xs = list(HCAHPS)
    mat = np.full((len(xs), len(ys)), np.nan); sig = np.zeros_like(mat, dtype=bool)
    for r in q1.iter_rows(named=True):
        i, j = xs.index(r["x"]), ys.index(r["y"])
        mat[i, j] = r["spearman_rho"]; sig[i, j] = r["p_fdr"] < 0.05
    cmap = LinearSegmentedColormap.from_list("div", [BLUE, NEUTRAL, RED])
    fig, ax = plt.subplots(figsize=(8.5, 5.2))
    lim = 0.5
    im = ax.imshow(mat, cmap=cmap, vmin=-lim, vmax=lim, aspect="auto")
    for i in range(len(xs)):
        for j in range(len(ys)):
            if not np.isnan(mat[i, j]):
                ax.text(j, i, f"{mat[i, j]:+.2f}" + ("" if sig[i, j] else "\nns"), ha="center", va="center",
                        fontsize=8.5, color=INK, fontweight="bold" if sig[i, j] else "normal")
    ax.set_xticks(range(len(ys)), ylabels); ax.set_yticks(range(len(xs)), [HCAHPS[x] for x in xs])
    ax.grid(False); ax.tick_params(length=0)
    for s in ax.spines.values(): s.set_visible(False)
    ax.set_xticks(np.arange(-.5, len(ys)), minor=True); ax.set_yticks(np.arange(-.5, len(xs)), minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=2); ax.tick_params(which="minor", length=0)
    cb = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02); cb.outline.set_visible(False)
    cb.set_label("Spearman rho  (blue = better experience, fewer readmissions)", color=INK_2, fontsize=8.5)
    n = q1.filter(pl.col("y") == "readm_index")["n_hospitals"][0]
    fig.text(0.01, 0.985, "Hospitals with better patient experience readmit fewer patients",
             fontsize=13, fontweight="bold", va="top")
    fig.text(0.01, 0.94, f"California acute care hospitals, CMS Care Compare; composite n = {n}; "
             "ns = not significant after false-discovery-rate correction", fontsize=9, color=INK_2, va="top")
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(FIG / "cms_experience_vs_readmission.png", dpi=160); plt.close(fig)

    # Scatter: discharge information vs composite readmission index
    d = df.select("H_COMP_6_LINEAR_SCORE", "readm_index").drop_nulls()
    x, y = d["H_COMP_6_LINEAR_SCORE"].to_numpy(), d["readm_index"].to_numpy()
    rho = q1.filter((pl.col("x") == "H_COMP_6_LINEAR_SCORE") & (pl.col("y") == "readm_index"))["spearman_rho"][0]
    b1, b0 = np.polyfit(x, y, 1)
    fig, ax = plt.subplots(figsize=(7, 4.3))
    ax.scatter(x, y, s=34, color=BLUE, alpha=0.75, edgecolor=SURFACE, linewidth=1)
    xx = np.linspace(x.min(), x.max(), 50); ax.plot(xx, b0 + b1 * xx, color=INK_2, lw=2)
    ax.axhline(0, color=MUTED, lw=1, ls=":")
    ax.text(x.min(), 0.05, "state average", fontsize=8, color=MUTED)
    ax.set_xlabel("HCAHPS discharge information score (linear mean, 0-100)")
    ax.set_ylabel("Readmission index (z-score; higher = worse)")
    fig.text(0.01, 0.985, "Better discharge information, lower readmissions", fontsize=13, fontweight="bold", va="top")
    fig.text(0.01, 0.935, f"Each dot is a California hospital (n = {len(x)}); Spearman rho = {rho:+.2f}",
             fontsize=9, color=INK_2, va="top")
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(FIG / "cms_discharge_info_scatter.png", dpi=160); plt.close(fig)

    # San Diego hospitals: HF readmission vs California distribution.
    # (This CMS release reports "Not Available" for the national comparison and interval
    # fields, so hospitals are placed against the state's own quartiles instead.)
    q25, q75 = df["READM_30_HF"].quantile(0.25), df["READM_30_HF"].quantile(0.75)
    s = sd.drop_nulls("READM_30_HF").sort("READM_30_HF", descending=True)
    h = 0.42 * s.height + 1.6
    fig, ax = plt.subplots(figsize=(8.5, h))
    ax.axvspan(q25, q75, color=GRID, alpha=0.6, lw=0)
    names = [n.title().replace("Uc ", "UC ").replace(" - Hillcrest Med", "")[:42].rstrip(" -") for n in s["name"]]
    for i, r in enumerate(s.iter_rows(named=True)):
        v = r["READM_30_HF"]
        c = BLUE if v < q25 else (RED if v > q75 else MUTED)
        tag = "  best 25%" if v < q25 else ("  worst 25%" if v > q75 else "")
        ax.plot(v, i, "o", color=c, ms=9, mec=SURFACE, mew=1.5, zorder=3)
        ax.text(v + 0.12, i, f"{v:.1f}%{tag}", va="center", fontsize=8.5, color=INK_2, zorder=4,
                bbox=dict(boxstyle="square,pad=0.1", fc=SURFACE, ec="none", alpha=0.85))
    ax.axvline(state_median_hf, color=INK_2, lw=1, ls="--", zorder=1)
    ax.text(state_median_hf, s.height - 0.35, f" CA median {state_median_hf:.1f}%", fontsize=8.5, color=INK_2)
    ax.text(q25, -0.9, f"middle 50% of CA hospitals ({q25:.1f}-{q75:.1f}%)", fontsize=8, color=INK_2)
    ax.set_yticks(range(s.height), names); ax.grid(axis="y", visible=False)
    ax.set_ylim(-1.3, s.height - 0.2)
    ax.set_xlabel("30-day heart failure readmission rate, risk-standardized (%), Jul 2023-Jun 2025")
    ax.set_xlim(s["READM_30_HF"].min() - 0.5, s["READM_30_HF"].max() + 1.3)
    fig.text(0.01, 0.995, "San Diego County hospitals vs. California: heart failure readmissions",
             fontsize=13, fontweight="bold", va="top")
    fig.text(0.01, 0.995 - 0.38 / h, f"Blue = best quarter of {df['READM_30_HF'].drop_nulls().len()} CA acute "
             "care hospitals; red = worst quarter; gray band = middle 50%", fontsize=9, color=INK_2, va="top")
    fig.tight_layout(rect=(0, 0, 1, 1 - 0.8 / h))
    fig.savefig(FIG / "cms_san_diego_hf_readmissions.png", dpi=160); plt.close(fig)


if __name__ == "__main__":
    main()
