"""
run_csp_on_rts.py
Run the ORIGINAL CSP pipeline on the RTS dataset.
Creates a 5th ablation baseline: CSP-only pipeline vs. RTS-extended pipeline.

How: Forces targetModule="csp" for all 16 cases, then runs the standard
Full_Pipeline/pipeline.py. The pipeline's adaptive routing automatically uses:
  - CSP engine (-csp, NOT -rts)
  - CSP syntax knowledge (syntax-dataset.json)
  - CSP RAG examples (database-rag-claude.json)
  - Full action extraction (NOT skipped)
  - Original 3 diagnostic modes (NOT the RTS 5-mode system)

Results saved to: generated_code_csp_on_rts/
"""
import json, os, sys, subprocess, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
FULL_DIR = ROOT / "Automated_Pipelines" / "Full_Pipeline"
RTS_SRC  = ROOT / "Datasets" / "PAT-RT.json"
RTS_CSP  = FULL_DIR / "PAT-RT-csp-mode.json"
OUT_DIR  = FULL_DIR / "generated_code_csp_on_rts"

# 1. Build CSP-forced dataset
print("=" * 55)
print("Step 1: Creating CSP-forced dataset...")
with open(RTS_SRC, encoding='utf-8') as f:
    data = json.load(f)
for item in data:
    item['targetModule'] = 'csp'
with open(RTS_CSP, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print(f"  Saved {len(data)} cases to {RTS_CSP.name}")

# 2. Run pipeline with redirected output
print("\nStep 2: Running CSP pipeline on RTS data (~70 min)...")
print("=" * 55)
os.chdir(str(FULL_DIR))
env = os.environ.copy()
env["PAT_OUTPUT_DIR"] = str(OUT_DIR)
# Point pipeline to our CSP-forced dataset
env["PAT_DATASET_PATH"] = str(RTS_CSP)
subprocess.run([sys.executable, str(FULL_DIR / "pipeline.py")], env=env)

# 3. Done
print("\n" + "=" * 55)
print(f"Results: {OUT_DIR}")
print(f"Dataset copy kept at: {RTS_CSP}")
print("\nDone! Run check_case() on generated_code_csp_on_rts/ to compute CSR/FPR/APR")
