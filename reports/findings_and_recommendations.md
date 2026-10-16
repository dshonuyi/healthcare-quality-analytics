# Findings and Recommendations

**Population:** 3,393 synthetic patients (San Diego, CA), 184,109 encounters, 2.35M observations
**Measurement year:** 2025  |  **Model hold-out year:** 2025

All patient data is synthetic (Synthea). The patterns are realistic, but they come from a simulator,
so the findings show the method, not conclusions about a real population.

---

## 1. Data quality changes the answer before any analysis starts

| What the audit found | Why it matters | Action |
|---|---|---|
| **49% of HbA1c results (11,888) fall below 4%**, the floor of standard assay reportable ranges | With raw values the diabetes measure reads **79.9%**. With invalid results excluded it reads **47.2%**, a 33-point swing that decides whether the organization is near 3 stars or far below it | Treat this as a systemic interface problem, not 11,888 separate typos. Put GSD on a data-quality hold, escalate to the lab interface team to check units and mapping, and do not report the measure externally until it is fixed |
| 70 BP pairs with diastolic >= systolic, plus 5 systolic readings under 50 mmHg | Transposed or mis-keyed vitals would be counted as "controlled" | Excluded from CBP, returned to clinics for chart correction |
| 360 encounters dated after the patient's death | All 360 are administrative "Death Certification" visits filed 1-14 days after death | Investigated and reclassified as expected behavior (rule T2a). No correction needed, but excluded from utilization |

The rule set has 25 checks across uniqueness, referential integrity, temporal validity, clinical
plausibility and completeness. A fault-injection test confirms 16 rules catch exactly the planted errors and 4 others raise no false positives.

## 2. Quality measures: all four below the Medicare 3-star cut point

| Measure | 2025 rate | Eligible | 3-star | 4-star | Gap to 4 stars |
|---|---:|---:|---:|---:|---|
| Controlling High Blood Pressure | 66.9% | 490 | 75% | 80% | 64 more patients controlled |
| Diabetes HbA1c <= 9% (validated) | 47.2% | 229 | 77% | 87% | Data-quality hold (see above) |
| Colorectal Cancer Screening | 58.2% | 894 | 60% | 70% | 106 more patients screened |
| Breast Cancer Screening | 2.9% | 311 | 71% | 76% | See note |

**Breast cancer screening at 2.9% is a data-capture finding, not a care finding.** No real
organization screens 3% of eligible women. A rate this far below every benchmark almost always means
mammograms are happening at outside imaging centers and never reach the EHR. (In this dataset, the
cause is that Synthea rarely simulates routine screening mammograms.) The right first step is a
supplemental data feed, such as claims or imaging-center results, not an outreach campaign.

**Trends are flat:** CBP has drifted down slightly (68.2% to 66.9%) and COL has improved slightly
(55.9% to 58.2%) over four years. Neither is moving fast enough to reach 3 stars on its own.

## 3. Where the gaps are

**Pareto of 959 open care gaps (725 patients):**
- 39%: no colorectal screening on record (374)
- 31%: no mammogram on record (302)
- 16%: last BP at or above 140/90 (156)
- 13%: no valid HbA1c in the year (121)

Two "never documented" reasons make up 70% of gap volume. Those are closed by scheduling and
data capture. Uncontrolled BP is the largest gap that requires a clinical intervention.

**Equity stratification (chi-square tests across payer, race/ethnicity, income, sex):**
- **Colorectal screening differs significantly by payer** (p = 0.008): Medicare 66.2% and Dual
  Eligible 67.5%, versus Commercial 53.5%, Medicaid 52.9% and Uninsured 54.8%. Members under 65
  are the ones being missed.
- No other stratifier reached significance at p < 0.05. Small groups, such as 26 Dual Eligible
  patients with hypertension, show large point gaps that are not statistically reliable. Those are
  the groups to keep watching as more data accrues, not to act on yet.

## 4. Predicting unplanned acute care

**Question:** which adult patients are most likely to have an ED visit or unplanned hospital stay next year?

**Design:** trained on 2022-2023 baselines (4,279 patient-years, 502 events) and tested on a held-out
2025 outcome year (2,183 patients, 266 events), so the model is judged on a year it never saw.

| Model | AUC | PR-AUC | Events captured by top 10% |
|---|---:|---:|---:|
| Simple rule: prior-year acute visits | 0.556 | 0.175 | 18% |
| **Logistic regression (selected)** | **0.647** | **0.263** | **28% (2.8x random)** |
| XGBoost | 0.654 | 0.256 | 28% |

Logistic regression was selected because XGBoost added only 0.007 AUC. Care managers need to see why
a patient was flagged, and odds ratios provide that. The model is well calibrated in the top deciles
(predicted 33% vs observed 34% in the highest decile), so its risk scores can be read as probabilities.
An AUC in the mid-0.6s is typical for utilization prediction from EHR data alone. Adding social-needs
data (housing, transportation) would be the next lever for accuracy.

**Adjusted risk factors (odds ratios, p < 0.05):**
- Substance use disorder: **2.0x**
- Asthma: 1.9x
- Chronic kidney disease: 1.8x
- Chronic pain: 1.3x
- Each additional SD of chronic-condition count: 1.25x

**Correlation check:** prior-year ED visits correlate with next-year acute use (r = 0.14), but once
chronic conditions are controlled for, the effect is no longer significant. In other words, past ED
use is a symptom of underlying disease burden, not an independent driver. Open care gaps show a
small correlation (r = 0.06) that also disappears after adjustment.

## 5. Recommendations

| # | Recommendation | Owner | How to measure it |
|---|---|---|---|
| 1 | **Fix the HbA1c feed before reporting GSD.** Escalate the sub-4% results as a systemic lab-interface issue and hold the measure until the units and mapping are verified | Quality analytics + lab/IT interface team | Share of HbA1c results passing rule C4 (target > 99%); GSD recomputed after fix |
| 2 | **Close the mammography data gap.** Ingest claims or imaging-center results as supplemental data before launching outreach | Quality analytics + HIM | BCS rate before vs after supplemental feed |
| 3 | **Target colorectal screening outreach at under-65 Commercial and Medicaid members.** Mailed FIT kits are low-cost and fit patients who are not due for colonoscopy | Population health / care coordination | COL rate by payer, monthly; the payer gap should narrow |
| 4 | **Run a BP-control huddle for the 156 uncontrolled patients.** Repeat the reading when the first is elevated, book a follow-up within 30 days and review medication intensification | Primary care clinical effectiveness team | CBP rate monthly vs 75% (3-star) target |
| 5 | **Pilot care-management outreach to the top 10% risk tier (219 patients).** Prioritize those with substance use disorder, and connect them to behavioral health | Care management | Acute-care visits per 1,000 in the pilot group vs a matched comparison group |
| 6 | **Schedule the 25 validation rules on every data refresh** and trend failure rates as a data-quality scorecard | Quality analytics | Rule failure rates over time |

### Monitoring plan (feedback loop)

1. Refresh measures monthly and show year-to-date rates against 3-star and 4-star cut points.
2. Send each clinic its care-gap list (`data/processed/care_gap_list.csv`) with the gap reason.
3. Re-run the stratification tests quarterly to confirm the COL payer gap is closing.
4. Re-score the risk model each January and check calibration against the previous year's outcomes.
5. Review rule failures with HIM and IT at each refresh, and escalate any rule above 1% failure.
