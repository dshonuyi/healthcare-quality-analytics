-- HEDIS-style quality measures, written as DuckDB table macros parameterized by
-- measurement year (my). Each macro returns one row per eligible patient with a
-- numerator flag, so rates can be stratified and care-gap lists can be pulled.
--
-- IMPORTANT: Official HEDIS specifications and value sets are licensed by NCQA.
-- These are simplified approximations based on publicly described measure definitions.
-- Simplifications are listed in docs/measure_definitions.md.

-- ---------------------------------------------------------------------------
-- Clean clinical inputs (records failing validation rules C1-C4 are dropped)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW valid_bp AS
SELECT s.patient_id, s.obs_ts, s.value_num AS systolic, d.value_num AS diastolic
FROM observations s
JOIN observations d
  ON d.patient_id = s.patient_id AND d.obs_ts = s.obs_ts AND d.code = '8462-4'
WHERE s.code = '8480-6'
  AND s.value_num BETWEEN 50 AND 300
  AND d.value_num BETWEEN 20 AND 200
  AND d.value_num < s.value_num;

CREATE OR REPLACE VIEW valid_a1c AS
SELECT patient_id, obs_ts, value_num AS a1c
FROM observations
WHERE code = '4548-4' AND value_num BETWEEN 4 AND 18;   -- typical assay reportable range

-- Diabetes diagnosis codes (type 2 and its complications; prediabetes excluded)
CREATE OR REPLACE VIEW diabetes_dx AS
SELECT patient_id, min(start_date) AS onset_date
FROM conditions
WHERE lower(description) LIKE '%diabetes mellitus%'
   OR lower(description) LIKE '%due to type 2 diabetes%'
   OR lower(description) LIKE '%due to type ii diabetes%'
GROUP BY patient_id;

CREATE OR REPLACE VIEW hypertension_dx AS
SELECT patient_id, min(start_date) AS onset_date
FROM conditions
WHERE code = '59621000'                       -- Essential hypertension
GROUP BY patient_id;

-- ---------------------------------------------------------------------------
-- Eligible base population for a measurement year
--   * alive through 31 Dec of the measurement year
--   * "attributed": at least one encounter in the measurement year or the year before
--     (stand-in for the continuous-enrollment requirement)
--   * no hospice encounter in the measurement year (required exclusion)
--   * strata: age, sex, race/ethnicity, payer on 31 Dec, income band
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO base_population(my) AS TABLE
WITH attributed AS (
    SELECT DISTINCT patient_id FROM encounters
    WHERE year(start_ts) IN (my - 1, my)
),
hospice AS (
    SELECT DISTINCT patient_id FROM encounters
    WHERE encounter_class = 'hospice' AND year(start_ts) = my
),
payer_dec31 AS (
    SELECT patient_id, any_value(payer_category) AS payer_category
    FROM payer_periods
    WHERE start_date <= make_date(my, 12, 31) AND end_date >= make_date(my, 12, 31)
    GROUP BY patient_id
)
SELECT
    p.patient_id,
    my                                                    AS measurement_year,
    my - year(p.birth_date)                               AS age,   -- age on 31 Dec of MY
    p.gender,
    CASE WHEN p.ethnicity = 'hispanic' THEN 'Hispanic/Latino'
         WHEN p.race = 'white'  THEN 'White (non-Hispanic)'
         WHEN p.race = 'black'  THEN 'Black (non-Hispanic)'
         WHEN p.race = 'asian'  THEN 'Asian (non-Hispanic)'
         ELSE 'Other (non-Hispanic)' END                   AS race_ethnicity,
    coalesce(pay.payer_category, 'Unknown')               AS payer_category,
    p.income,
    CASE WHEN p.income < 35000  THEN '1: <$35k'
         WHEN p.income < 75000  THEN '2: $35k-75k'
         WHEN p.income < 150000 THEN '3: $75k-150k'
         ELSE '4: $150k+' END                              AS income_band,
    p.zip
FROM patients p
JOIN attributed a USING (patient_id)
LEFT JOIN hospice h USING (patient_id)
LEFT JOIN payer_dec31 pay USING (patient_id)
WHERE h.patient_id IS NULL
  AND (p.death_date IS NULL OR p.death_date > make_date(my, 12, 31))
  AND p.birth_date <= make_date(my, 12, 31);

-- ---------------------------------------------------------------------------
-- CBP - Controlling High Blood Pressure
--   Denominator: age 18-85, hypertension diagnosed on or before 30 Jun of MY
--   Numerator:   most recent valid BP in MY is < 140/90 (no reading = not controlled)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO measure_cbp(my) AS TABLE
WITH denom AS (
    SELECT b.* FROM base_population(my) b
    JOIN hypertension_dx h USING (patient_id)
    WHERE b.age BETWEEN 18 AND 85 AND h.onset_date <= make_date(my, 6, 30)
),
last_bp AS (
    SELECT patient_id, arg_max(systolic, obs_ts) AS systolic, arg_max(diastolic, obs_ts) AS diastolic,
           max(obs_ts) AS last_reading
    FROM valid_bp WHERE year(obs_ts) = my GROUP BY patient_id
)
SELECT 'CBP' AS measure, 'Controlling High Blood Pressure' AS measure_name, d.*,
       bp.systolic, bp.diastolic, bp.last_reading::DATE AS last_service_date,
       coalesce(bp.systolic < 140 AND bp.diastolic < 90, false) AS in_numerator,
       CASE WHEN bp.patient_id IS NULL THEN 'No BP recorded in year'
            WHEN NOT (bp.systolic < 140 AND bp.diastolic < 90) THEN 'Last BP at or above 140/90'
            END AS gap_reason
FROM denom d LEFT JOIN last_bp bp USING (patient_id);

-- ---------------------------------------------------------------------------
-- GSD - Glycemic Status for Patients with Diabetes (HbA1c <= 9.0%)
--   Denominator: age 18-75 with diabetes diagnosed on or before 31 Dec of MY
--   Numerator:   most recent valid HbA1c in MY is <= 9.0% (no test = not controlled),
--                matching the direction used for the Medicare Stars measure
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO measure_gsd(my) AS TABLE
WITH denom AS (
    SELECT b.* FROM base_population(my) b
    JOIN diabetes_dx dx USING (patient_id)
    WHERE b.age BETWEEN 18 AND 75 AND dx.onset_date <= make_date(my, 12, 31)
),
last_a1c AS (
    SELECT patient_id, arg_max(a1c, obs_ts) AS a1c, max(obs_ts) AS last_test
    FROM valid_a1c WHERE year(obs_ts) = my GROUP BY patient_id
)
SELECT 'GSD' AS measure, 'Glycemic Status <=9% (Diabetes)' AS measure_name, d.*,
       a.a1c, a.last_test::DATE AS last_service_date,
       coalesce(a.a1c <= 9.0, false) AS in_numerator,
       CASE WHEN a.patient_id IS NULL THEN 'No valid HbA1c in year'
            WHEN a.a1c > 9.0 THEN 'Last HbA1c above 9%' END AS gap_reason
FROM denom d LEFT JOIN last_a1c a USING (patient_id);

-- ---------------------------------------------------------------------------
-- BCS - Breast Cancer Screening
--   Denominator: women age 52-74 at 31 Dec of MY
--   Numerator:   mammogram between 1 Oct of MY-2 and 31 Dec of MY
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO measure_bcs(my) AS TABLE
WITH denom AS (
    SELECT * FROM base_population(my) WHERE gender = 'F' AND age BETWEEN 52 AND 74
),
mammo AS (
    SELECT patient_id, max(start_ts)::DATE AS last_mammo
    FROM procedures
    WHERE code IN ('71651007', '24623002', '241055006')
      AND start_ts::DATE BETWEEN make_date(my - 2, 10, 1) AND make_date(my, 12, 31)
    GROUP BY patient_id
)
SELECT 'BCS' AS measure, 'Breast Cancer Screening' AS measure_name, d.*,
       m.last_mammo AS last_service_date,
       m.patient_id IS NOT NULL AS in_numerator,
       CASE WHEN m.patient_id IS NULL THEN 'No mammogram in 27-month window' END AS gap_reason
FROM denom d LEFT JOIN mammo m USING (patient_id);

-- ---------------------------------------------------------------------------
-- COL - Colorectal Cancer Screening
--   Denominator: age 46-75 at 31 Dec of MY
--   Numerator:   colonoscopy in MY-9..MY  OR  fecal occult blood test in MY
-- ---------------------------------------------------------------------------
CREATE OR REPLACE MACRO measure_col(my) AS TABLE
WITH denom AS (
    SELECT * FROM base_population(my) WHERE age BETWEEN 46 AND 75
),
screen AS (
    SELECT patient_id, max(start_ts)::DATE AS last_screen
    FROM procedures
    WHERE (code = '73761001' AND year(start_ts) BETWEEN my - 9 AND my)
       OR (code = '104435004' AND year(start_ts) = my)
    GROUP BY patient_id
)
SELECT 'COL' AS measure, 'Colorectal Cancer Screening' AS measure_name, d.*,
       s.last_screen AS last_service_date,
       s.patient_id IS NOT NULL AS in_numerator,
       CASE WHEN s.patient_id IS NULL THEN 'No colonoscopy (10 yr) or FOBT (1 yr)' END AS gap_reason
FROM denom d LEFT JOIN screen s USING (patient_id);

-- Sensitivity check: the same GSD measure computed on raw (unvalidated) HbA1c values.
-- Shows how much the reported rate depends on the validation step.
CREATE OR REPLACE MACRO measure_gsd_unvalidated(my) AS TABLE
WITH denom AS (
    SELECT b.* FROM base_population(my) b
    JOIN diabetes_dx dx USING (patient_id)
    WHERE b.age BETWEEN 18 AND 75 AND dx.onset_date <= make_date(my, 12, 31)
),
last_a1c AS (
    SELECT patient_id, arg_max(value_num, obs_ts) AS a1c
    FROM observations WHERE code = '4548-4' AND year(obs_ts) = my GROUP BY patient_id
)
SELECT d.patient_id, coalesce(a.a1c <= 9.0, false) AS in_numerator
FROM denom d LEFT JOIN last_a1c a USING (patient_id);
