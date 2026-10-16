-- Typed views over the raw EHR extract.
-- TRY_CAST is used on purpose: values that fail to parse become NULL here and are
-- counted by the validation step instead of crashing the pipeline.

CREATE OR REPLACE VIEW patients AS
SELECT
    Id                                   AS patient_id,
    TRY_CAST(BIRTHDATE AS DATE)          AS birth_date,
    TRY_CAST(DEATHDATE AS DATE)          AS death_date,
    GENDER                               AS gender,
    RACE                                 AS race,
    ETHNICITY                            AS ethnicity,
    CITY                                 AS city,
    COUNTY                               AS county,
    ZIP                                  AS zip,
    TRY_CAST(INCOME AS DOUBLE)           AS income
FROM raw_patients;

CREATE OR REPLACE VIEW encounters AS
SELECT
    Id                                   AS encounter_id,
    PATIENT                              AS patient_id,
    ORGANIZATION                         AS organization_id,
    PROVIDER                             AS provider_id,
    PAYER                                AS payer_id,
    ENCOUNTERCLASS                       AS encounter_class,
    CODE                                 AS code,
    DESCRIPTION                          AS description,
    TRY_CAST(START AS TIMESTAMPTZ)       AS start_ts,
    TRY_CAST(STOP  AS TIMESTAMPTZ)       AS stop_ts,
    TRY_CAST(TOTAL_CLAIM_COST AS DOUBLE) AS total_claim_cost,
    TRY_CAST(PAYER_COVERAGE AS DOUBLE)   AS payer_coverage,
    REASONCODE                           AS reason_code,
    REASONDESCRIPTION                    AS reason_description
FROM raw_encounters;

CREATE OR REPLACE VIEW conditions AS
SELECT
    PATIENT                              AS patient_id,
    ENCOUNTER                            AS encounter_id,
    CODE                                 AS code,
    DESCRIPTION                          AS description,
    TRY_CAST(START AS DATE)              AS start_date,
    TRY_CAST(STOP  AS DATE)              AS stop_date
FROM raw_conditions;

CREATE OR REPLACE VIEW observations AS
SELECT
    PATIENT                              AS patient_id,
    ENCOUNTER                            AS encounter_id,
    CATEGORY                             AS category,
    CODE                                 AS code,
    DESCRIPTION                          AS description,
    VALUE                                AS value_raw,
    TRY_CAST(VALUE AS DOUBLE)            AS value_num,
    UNITS                                AS units,
    TRY_CAST(DATE AS TIMESTAMPTZ)        AS obs_ts
FROM raw_observations;

CREATE OR REPLACE VIEW procedures AS
SELECT
    PATIENT                              AS patient_id,
    ENCOUNTER                            AS encounter_id,
    CODE                                 AS code,
    DESCRIPTION                          AS description,
    TRY_CAST(START AS TIMESTAMPTZ)       AS start_ts
FROM raw_procedures;

CREATE OR REPLACE VIEW medications AS
SELECT
    PATIENT                              AS patient_id,
    ENCOUNTER                            AS encounter_id,
    CODE                                 AS code,
    DESCRIPTION                          AS description,
    TRY_CAST(START AS TIMESTAMPTZ)       AS start_ts,
    TRY_CAST(STOP  AS TIMESTAMPTZ)       AS stop_ts
FROM raw_medications;

CREATE OR REPLACE VIEW payers AS
SELECT Id AS payer_id, NAME AS payer_name FROM raw_payers;

-- Coverage periods; payer category is rolled up for stratification.
CREATE OR REPLACE VIEW payer_periods AS
SELECT
    pt.PATIENT                           AS patient_id,
    TRY_CAST(pt.START_DATE AS TIMESTAMPTZ)::DATE      AS start_date,
    TRY_CAST(pt.END_DATE   AS TIMESTAMPTZ)::DATE      AS end_date,
    p.payer_name,
    CASE
        WHEN p.payer_name = 'Medicare'      THEN 'Medicare'
        WHEN p.payer_name = 'Medicaid'      THEN 'Medicaid'
        WHEN p.payer_name = 'Dual Eligible' THEN 'Dual Eligible'
        WHEN p.payer_name = 'NO_INSURANCE'  THEN 'Uninsured'
        ELSE 'Commercial'
    END                                  AS payer_category
FROM raw_payer_transitions pt
LEFT JOIN payers p ON p.payer_id = pt.PAYER;
