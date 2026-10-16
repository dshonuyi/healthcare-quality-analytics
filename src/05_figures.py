"""
Step 5 - Static figures for the README and summary report (written to figures/).

Run from the repo root after steps 1-4:
    python src/05_figures.py
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import polars as pl

P = Path("data/processed")
FIG = Path("figures")
FIG.mkdir(exist_ok=True)

# Palette (validated reference palette, light mode)
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#8a8985"
GRID = "#e6e5e1"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
BLUE_LIGHT = "#86b6ef"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
    "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "axes.titlesize": 11,
    "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlecolor": INK,
    "xtick.color": INK_2, "ytick.color": INK_2, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.8, "axes.spines.top": False, "axes.spines.right": False,
    "axes.spines.left": False, "legend.frameon": False,
})


def suptitle(fig, title, subtitle):
    fig.text(0.01, 0.985, title, fontsize=13, fontweight="bold", va="top", color=INK)
    fig.text(0.01, 0.935, subtitle, fontsize=9.5, va="top", color=INK_2)


def measure_trend():
    t = pl.read_csv(P / "measure_rates_by_year.csv")
    cut = {"CBP": (75, 80), "GSD": (77, 87), "BCS": (71, 76), "COL": (60, 70)}
    order = ["CBP", "GSD", "COL", "BCS"]
    fig, axes = plt.subplots(1, 4, figsize=(13, 3.6), sharey=True)
    for ax, m in zip(axes, order):
        d = t.filter(pl.col("measure") == m).sort("measurement_year")
        x, y = d["measurement_year"].to_list(), d["rate"].to_list()
        three, four = cut[m]
        ax.axhline(four, color=INK_2, lw=1, ls="--")
        ax.axhline(three, color=MUTED, lw=1, ls=":")
        ax.plot(x, y, color=BLUE, lw=2, marker="o", ms=6, mec=SURFACE, mew=1.5, zorder=3)
        ax.annotate(f"{y[-1]:.1f}%", (x[-1], y[-1]), xytext=(0, 10 if y[-1] < 15 else -16), textcoords="offset points",
                    ha="center", fontsize=9.5, fontweight="bold", color=INK)
        name = {"GSD": "Diabetes: HbA1c <= 9%"}.get(m, d["measure_name"][0])
        ax.set_title(f"{name}\n3-star {three}%  |  4-star {four}%", fontsize=10)
        ax.set_xticks(x); ax.set_ylim(0, 100); ax.grid(axis="x", visible=False)
        if m == "BCS":
            ax.text(2023.5, 40, "Rate this low points to\nmissing data capture,\nnot missing care\n(see report)",
                    ha="center", fontsize=8.5, color=INK_2, style="italic")
    axes[0].set_ylabel("% of eligible patients meeting measure")
    from matplotlib.lines import Line2D
    fig.legend([Line2D([], [], color=BLUE, lw=2, marker="o"), Line2D([], [], color=MUTED, ls=":"), Line2D([], [], color=INK_2, ls="--")],
               ["Rate", "Medicare 3-star cut point", "Medicare 4-star cut point"], loc="upper right", ncol=3, fontsize=8.5,
               bbox_to_anchor=(0.995, 0.99))
    suptitle(fig, "Quality measure performance, 2022-2025",
             "All four measures sit below the Medicare 3-star cut point; diabetes uses validated HbA1c values")
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(FIG / "measure_trend.png", dpi=160); plt.close(fig)


def gsd_validation():
    d = pl.read_csv(P / "gsd_validation_sensitivity.csv")
    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    labels = ["Raw HbA1c values", "After validation"]
    vals = d["rate"].to_list()
    ax.barh(labels, vals, color=[BLUE_LIGHT, BLUE], height=0.5)
    for i, v in enumerate(vals):
        ax.text(v + 1, i, f"{v:.1f}%", va="center", fontweight="bold", color=INK)
    ax.axvline(87, color=INK_2, ls="--", lw=1); ax.text(86, -0.42, "4-star 87%", ha="right", fontsize=8, color=INK_2)
    ax.set_xlim(0, 100); ax.invert_yaxis(); ax.grid(axis="y", visible=False)
    ax.set_xlabel("% of diabetic patients with last HbA1c <= 9%")
    suptitle(fig, "Validation moved the diabetes measure by 33 points",
             "49% of HbA1c results fall below the 4% assay floor; excluding them reopens 75 patients' care gaps")
    fig.tight_layout(rect=(0, 0, 1, 0.82))
    fig.savefig(FIG / "gsd_validation_impact.png", dpi=160); plt.close(fig)


def disparity():
    s = pl.read_csv(P / "measure_rates_by_strata.csv").filter(pl.col("stratifier") == "payer_category")
    tests = pl.read_csv(P / "stratification_tests.csv").filter(pl.col("stratifier") == "payer_category")
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.6), sharey=True)
    order = ["Medicare", "Dual Eligible", "Commercial", "Medicaid", "Uninsured"]
    for ax, m, title in zip(axes, ["COL", "CBP"], ["Colorectal Cancer Screening", "Controlling High Blood Pressure"]):
        d = {r["group"]: r for r in s.filter(pl.col("measure") == m).iter_rows(named=True)}
        vals = [d[g]["rate"] for g in order]
        ax.barh(order, vals, color=BLUE, height=0.55)
        for i, g in enumerate(order):
            ax.text(vals[i] + 1, i, f"{vals[i]:.1f}%  (n={d[g]['denominator']})", va="center", fontsize=8.5, color=INK_2)
        p = tests.filter(pl.col("measure") == m)["p_value"][0]
        ax.set_title(f"{title}\nchi-square p = {p:.3f}" + ("  (significant)" if p < 0.05 else "  (not significant)"), fontsize=10)
        ax.set_xlim(0, 105); ax.grid(axis="y", visible=False)
        ax.set_xlabel("% meeting measure, 2025")
    axes[0].invert_yaxis()
    suptitle(fig, "Screening gaps differ by payer; BP control gaps do not",
             "Commercial and Medicaid members trail Medicare by 13 points on colorectal screening")
    fig.tight_layout(rect=(0, 0, 1, 0.84))
    fig.savefig(FIG / "disparity_by_payer.png", dpi=160); plt.close(fig)


def odds_ratios():
    o = pl.read_csv(P / "odds_ratios.csv").filter(~pl.col("feature").str.contains("=")).sort("odds_ratio")
    o = o.filter(pl.col("p_value") < 0.2)
    fig, ax = plt.subplots(figsize=(8, 3.8))
    y = np.arange(o.height)
    sig = (o["p_value"] < 0.05).to_list()
    for i, r in enumerate(o.iter_rows(named=True)):
        c = BLUE if sig[i] else MUTED
        ax.plot([r["ci_low"], r["ci_high"]], [i, i], color=c, lw=2)
        ax.plot(r["odds_ratio"], i, "o", color=c, ms=8, mec=SURFACE, mew=1.5, zorder=3)
        ax.text(max(r["ci_high"], 1) + 0.08, i, f"OR {r['odds_ratio']:.2f}" + ("" if sig[i] else "  (ns)"),
                va="center", fontsize=8.5, color=INK_2)
    ax.axvline(1, color=INK_2, lw=1)
    lbl = [f"{r['label']} ({'per SD' if r['scale'] == 'per 1 SD' else 'yes/no'})" for r in o.iter_rows(named=True)]
    ax.set_yticks(y, lbl); ax.set_xscale("log"); ax.minorticks_off(); ax.set_xticks([0.5, 1, 2, 3], ["0.5", "1", "2", "3"])
    ax.set_xlim(0.4, 4.5); ax.grid(axis="y", visible=False)
    ax.set_xlabel("Adjusted odds ratio of unplanned acute care next year (95% CI, log scale)")
    suptitle(fig, "What raises a patient's risk of an ED visit or unplanned admission",
             "Multivariable logistic regression, 4,279 patient-years (2022-2023 baselines); blue = p < 0.05")
    fig.tight_layout(rect=(0, 0, 1, 0.86))
    fig.savefig(FIG / "risk_odds_ratios.png", dpi=160); plt.close(fig)


def gains():
    lift = pl.read_csv(P / "lift_table.csv")
    x = [0] + lift["top_pct_of_patients"].to_list() + [100]
    y = [0] + lift["pct_of_all_events"].to_list() + [100]
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    ax.plot([0, 100], [0, 100], color=MUTED, lw=2, ls="--", label="Random outreach")
    ax.plot(x, y, color=BLUE, lw=2, marker="o", ms=6, mec=SURFACE, mew=1.5, label="Risk model", zorder=3)
    r10 = lift.filter(pl.col("top_pct_of_patients") == 10).row(0, named=True)
    ax.annotate(f"Top 10% of patients\n= {r10['pct_of_all_events']:.0f}% of acute events\n({r10['lift_vs_random']:.1f}x random)",
                (10, r10["pct_of_all_events"]), xytext=(40, 8), textcoords="data", fontsize=9, color=INK,
                arrowprops=dict(arrowstyle="-", color=INK_2, lw=1))
    ax.text(70, 62, "Random", color=MUTED, fontsize=9)
    ax.text(38, 64, "Risk model", color=BLUE, fontsize=9, fontweight="bold")
    ax.set_xlim(0, 100); ax.set_ylim(0, 100)
    ax.set_xlabel("% of patients contacted (highest risk first)")
    ax.set_ylabel("% of 2025 acute-care events captured")
    ax.legend(loc="lower right", fontsize=8.5)
    suptitle(fig, "Who to call first: cumulative gains, 2025 hold-out year",
             "Model trained on 2023-2024 outcomes, tested on 2025 (2,183 patients, 266 events)")
    fig.tight_layout(rect=(0, 0, 1, 0.86))
    fig.savefig(FIG / "risk_gains_curve.png", dpi=160); plt.close(fig)


def pareto():
    g = pl.read_csv(P / "gap_reasons.csv").sort("patients", descending=True)
    labels = [f"{r['measure']}: {r['gap_reason']}" for r in g.iter_rows(named=True)]
    vals = g["patients"].to_list()
    cum = np.cumsum(vals) / sum(vals) * 100
    fig, ax = plt.subplots(figsize=(8.5, 3.6))
    ax.barh(labels, vals, color=BLUE, height=0.55)
    for i, (v, c) in enumerate(zip(vals, cum)):
        ax.text(v + 4, i, f"{v}  (cumulative {c:.0f}%)", va="center", fontsize=8.5, color=INK_2)
    ax.invert_yaxis(); ax.grid(axis="y", visible=False); ax.set_xlim(0, max(vals) * 1.45)
    ax.set_xlabel("Patients with an open care gap, 2025")
    suptitle(fig, "Pareto of open care gaps: two reasons account for most of them",
             "Screening never documented drives the volume; uncontrolled BP is the largest clinical gap")
    fig.tight_layout(rect=(0, 0, 1, 0.84))
    fig.savefig(FIG / "care_gap_pareto.png", dpi=160); plt.close(fig)


if __name__ == "__main__":
    measure_trend(); gsd_validation(); disparity(); odds_ratios(); gains(); pareto()
    print("figures written:", sorted(p.name for p in FIG.glob("*.png")))
