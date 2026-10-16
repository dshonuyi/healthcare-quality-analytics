# Measure Definitions and Simplifications

Official HEDIS technical specifications and value sets are licensed by NCQA, so they are not
used here. Each measure below is a simplified approximation built from publicly described
definitions, using the SNOMED-CT and LOINC codes present in the Synthea extract. The SQL lives in
[`sql/02_quality_measures.sql`](../sql/02_quality_measures.sql).

**Measurement year (MY):** calendar 2025, the last complete year in the extract. Trends cover 2022-2025.

## Shared eligibility rules (all measures)

| Rule | Implementation | Real HEDIS equivalent |
|---|---|---|
| Attribution | At least one encounter in MY or MY-1 | Continuous enrollment in a health plan |
| Age | MY minus birth year (age on 31 Dec of MY) | Same |
| Alive | No death date on or before 31 Dec of MY | Same |
| Hospice exclusion | No hospice encounter in MY | Required exclusion (hospice, palliative care) |
| Payer | Coverage active on 31 Dec of MY, grouped into Medicare / Medicaid / Dual / Commercial / Uninsured | Product line reporting |

## Measures

| Measure | Denominator | Numerator | Codes used |
|---|---|---|---|
| **CBP** - Controlling High Blood Pressure | Age 18-85, essential hypertension diagnosed on or before 30 Jun of MY | Most recent *valid* BP reading in MY is <140 systolic **and** <90 diastolic. No reading = not controlled | SNOMED 59621000; LOINC 8480-6, 8462-4 |
| **GSD** - Glycemic Status, Patients with Diabetes | Age 18-75 with diabetes diagnosed on or before 31 Dec of MY | Most recent *valid* HbA1c in MY is <= 9.0%. No test = not controlled (matches the Medicare Stars direction) | Diabetes and diabetic-complication SNOMED codes (prediabetes excluded); LOINC 4548-4 |
| **BCS** - Breast Cancer Screening | Women 52-74 | Mammogram between 1 Oct of MY-2 and 31 Dec of MY | SNOMED 71651007, 24623002, 241055006 |
| **COL** - Colorectal Cancer Screening | Age 46-75 | Colonoscopy in MY-9 through MY, or fecal occult blood test in MY | SNOMED 73761001, 104435004 |

"Valid" means the reading passed validation rules C1-C4 (see the
[data quality report](../reports/data_quality_report.md)).

## Known simplifications

- No diagnosis-count logic (HEDIS CBP requires two outpatient hypertension visits).
- Optional exclusions (frailty with advanced illness, bilateral mastectomy, total colectomy) are not applied.
- Screening options outside the extract (CT colonography, FIT-DNA, sigmoidoscopy) are not counted.
- COL's 10-year colonoscopy lookback is only fully covered for MY 2025; Synthea history starts around 2016, so earlier trend years slightly undercount COL.
- Rates are not risk adjusted.

## Benchmarks

Reference lines are the **2026 Medicare Part C Star Ratings cut points** (based on 2024 performance),
from a public plan resource (Texas Health Partners, "PY2026 Stars Cut Points"). Star Ratings apply to
Medicare Advantage contracts, so they are used here as external reference points, not as a direct
comparison for an all-payer population.

| Measure | 3 stars | 4 stars | 5 stars |
|---|---:|---:|---:|
| CBP | 75% | 80% | 86% |
| GSD (HbA1c <= 9%) | 77% | 87% | 91% |
| BCS | 71% | 76% | 84% |
| COL | 60% | 70% | 78% |

## Risk model outcome

**Unplanned acute-care use** = any ED visit (obstetric emergency admissions excluded) or any inpatient stay
that is not planned. Planned stays are oncology treatment cycles, rehab and detox, transplant,
elective orthopedic admissions and pregnancy-related admissions. Features are taken as of 31 Dec of
the baseline year; the outcome is measured in the following calendar year. The SQL lives in
[`sql/03_risk_features.sql`](../sql/03_risk_features.sql).

A 30-day readmission model was scoped first, but after planned oncology cycles were excluded the
synthetic cohort had only 13 readmissions in 366 index stays, too few to train on without overfitting.
Readmissions are analyzed with real CMS hospital data instead.
