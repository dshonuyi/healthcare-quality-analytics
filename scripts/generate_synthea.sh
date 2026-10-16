#!/usr/bin/env bash
# Regenerate the synthetic EHR extract used in this project.
# Requires Java 17+. Output: data/raw/*.csv (about 2 GB; not committed to git).
set -euo pipefail
mkdir -p synthea data/raw
if [ ! -f synthea/synthea.jar ]; then
  curl -L -o synthea/synthea.jar \
    https://github.com/synthetichealth/synthea/releases/download/master-branch-latest/synthea-with-dependencies.jar
fi
# 3,000 living patients from San Diego, CA; fixed seeds make the population reproducible.
java -jar synthea/synthea.jar -p 3000 -s 42 -cs 42 \
  --exporter.csv.export=true --exporter.fhir.export=false \
  --exporter.hospital.fhir.export=false --exporter.practitioner.fhir.export=false \
  --exporter.years_of_history=10 --exporter.baseDirectory=synthea/output \
  California "San Diego"
cp synthea/output/csv/*.csv data/raw/
echo "Synthetic EHR written to data/raw/"
