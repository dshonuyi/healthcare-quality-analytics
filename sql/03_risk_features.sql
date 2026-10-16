-- Risk-stratification dataset: one row per adult patient per baseline year.
-- Features describe the patient as of 31 Dec of the baseline year (yr);
-- the outcome is any unplanned acute-care use (ED visit or unplanned inpatient stay)
-- during the following calendar year (yr + 1).
-- Requires sql/02_quality_measures.sql to be loaded first (uses base_population and measures).

-- Planned / scheduled inpatient care is not "unplanned acute care":
-- oncology cycles, rehab/detox, transplant, elective orthopedic, and obstetric deliveries.
CREATE OR REPLACE MACRO is_unplanned_acute(cls, descr, reason) AS
    (cls = 'emergency' AND descr <> 'Obstetric emergency hospital admission (procedure)')
    OR (cls = 'inpatient'
        AND NOT lower(coalesce(reason, '')) SIMILAR TO '.*(neoplasm|carcinoma|leukemia|lymphoma|pregnan).*'
        AND descr NOT IN ('Drug rehabilitation and detoxification (regime/therapy)',
                          'Admission to surgical transplant department (procedure)',
                          'Non-urgent orthopedic admission (procedure)'));

CREATE OR REPLACE MACRO active_dx(yr, pattern) AS TABLE
    SELECT DISTINCT patient_id FROM conditions
    WHERE start_date <= make_date(yr, 12, 31)
      AND (stop_date IS NULL OR stop_date > make_date(yr, 12, 31))
      AND lower(description) SIMILAR TO pattern;

CREATE OR REPLACE MACRO risk_dataset(yr) AS TABLE
WITH b AS (SELECT * FROM base_population(yr) WHERE age >= 18),
util AS (
    SELECT patient_id,
        count(*) FILTER (WHERE year(start_ts) = yr AND is_unplanned_acute(encounter_class, description, reason_description)) AS acute_visits_prior_yr,
        count(*) FILTER (WHERE year(start_ts) = yr AND encounter_class = 'emergency')                                          AS ed_visits_prior_yr,
        count(*) FILTER (WHERE year(start_ts) = yr AND encounter_class IN ('ambulatory','outpatient','virtual'))               AS outpatient_visits_prior_yr,
        count(*) FILTER (WHERE year(start_ts) = yr AND encounter_class = 'wellness') > 0                                       AS had_wellness_visit,
        count(*) FILTER (WHERE year(start_ts) = yr + 1 AND is_unplanned_acute(encounter_class, description, reason_description)) > 0 AS outcome_acute_next_yr,
        coalesce(sum(total_claim_cost) FILTER (WHERE year(start_ts) = yr), 0)                                                   AS cost_prior_yr
    FROM encounters GROUP BY patient_id
),
burden AS (
    SELECT patient_id, count(DISTINCT code) AS active_conditions
    FROM conditions
    WHERE start_date <= make_date(yr, 12, 31) AND (stop_date IS NULL OR stop_date > make_date(yr, 12, 31))
      AND description LIKE '%(disorder)%'
    GROUP BY patient_id
),
meds AS (
    SELECT patient_id, count(DISTINCT code) AS active_meds
    FROM medications
    WHERE start_ts <= make_date(yr, 12, 31) AND (stop_ts IS NULL OR stop_ts > make_date(yr, 12, 31))
    GROUP BY patient_id
),
last_bmi AS (
    SELECT patient_id, arg_max(value_num, obs_ts) AS bmi FROM observations
    WHERE code = '39156-5' AND obs_ts <= make_date(yr, 12, 31) AND value_num BETWEEN 10 AND 100
    GROUP BY patient_id
),
open_gaps AS (
    SELECT patient_id, count(*) AS open_care_gaps FROM (
        SELECT patient_id, in_numerator FROM measure_cbp(yr) UNION ALL
        SELECT patient_id, in_numerator FROM measure_gsd(yr) UNION ALL
        SELECT patient_id, in_numerator FROM measure_col(yr)
    ) WHERE NOT in_numerator GROUP BY patient_id
)
SELECT
    b.patient_id, yr AS baseline_year, yr + 1 AS outcome_year,
    b.age, b.gender, b.race_ethnicity, b.payer_category, b.income,
    coalesce(u.acute_visits_prior_yr, 0)      AS acute_visits_prior_yr,
    coalesce(u.ed_visits_prior_yr, 0)         AS ed_visits_prior_yr,
    coalesce(u.outpatient_visits_prior_yr, 0) AS outpatient_visits_prior_yr,
    coalesce(u.had_wellness_visit, false)     AS had_wellness_visit,
    coalesce(u.cost_prior_yr, 0)              AS cost_prior_yr,
    coalesce(bu.active_conditions, 0)         AS active_conditions,
    coalesce(m.active_meds, 0)                AS active_meds,
    lb.bmi,
    coalesce(g.open_care_gaps, 0)             AS open_care_gaps,
    b.patient_id IN (SELECT patient_id FROM active_dx(yr, '.*heart failure.*'))                      AS dx_heart_failure,
    b.patient_id IN (SELECT patient_id FROM active_dx(yr, '.*chronic kidney disease.*'))             AS dx_ckd,
    b.patient_id IN (SELECT patient_id FROM active_dx(yr, '.*(diabetes mellitus|due to type 2 diabetes).*')) AS dx_diabetes,
    b.patient_id IN (SELECT patient_id FROM active_dx(yr, '.*essential hypertension.*'))             AS dx_hypertension,
    b.patient_id IN (SELECT patient_id FROM active_dx(yr, '.*(chronic obstructive|emphysema).*'))    AS dx_copd,
    b.patient_id IN (SELECT patient_id FROM active_dx(yr, '.*asthma.*'))                             AS dx_asthma,
    b.patient_id IN (SELECT patient_id FROM active_dx(yr, '.*(drug abuse|misuses drugs|opioid abuse|alcoholism|unhealthy alcohol).*')) AS dx_substance_use,
    b.patient_id IN (SELECT patient_id FROM active_dx(yr, '.*(anxiety|depressi).*'))                 AS dx_anxiety_depression,
    b.patient_id IN (SELECT patient_id FROM active_dx(yr, '.*chronic pain.*'))                       AS dx_chronic_pain,
    b.patient_id IN (SELECT patient_id FROM active_dx(yr, '.*alzheimer.*'))                          AS dx_dementia,
    coalesce(u.outcome_acute_next_yr, false)  AS outcome_acute_next_yr
FROM b
LEFT JOIN util u     USING (patient_id)
LEFT JOIN burden bu  USING (patient_id)
LEFT JOIN meds m     USING (patient_id)
LEFT JOIN last_bmi lb USING (patient_id)
LEFT JOIN open_gaps g USING (patient_id)
-- outcome-year death without acute use is not observable follow-up; keep patients alive through outcome year
WHERE b.patient_id NOT IN (SELECT patient_id FROM patients WHERE death_date <= make_date(yr + 1, 12, 31));
