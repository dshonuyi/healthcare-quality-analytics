"""Run the full pipeline end to end from the repo root:  python run_all.py"""
import subprocess
import sys
from pathlib import Path

STEPS = ["src/01_load_ehr.py", "src/02_validate.py", "src/03_quality_measures.py",
         "src/04_risk_model.py", "src/05_figures.py"]
if any(Path("data/cms").glob("*.csv")):
    STEPS.append("src/06_cms_hospital_analysis.py")

for step in STEPS:
    print(f"\n=== {step} ===")
    subprocess.run([sys.executable, step], check=True)
subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"], check=True)
