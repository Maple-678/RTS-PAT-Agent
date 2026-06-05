"""Batch experiment runner for RQ1/RQ2."""
import argparse, json, os, subprocess, sys, time, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
NO_PLAN_DIR = ROOT / "Automated_Pipelines" / "No_Planning"
FULL_DIR = ROOT / "Automated_Pipelines" / "Full_Pipeline"
DATASET = ROOT / "Datasets" / "PAT-RT.json"
PAT_EXE = r"C:\Program Files\Process Analysis Toolkit\Process Analysis Toolkit 3.5.1\PAT3.Console.exe"

with open(DATASET, 'r', encoding='utf-8') as f:
    ds = json.load(f)
    ALL_MODELS = [item['modelName'] for item in ds]
    EXPECTED_MAP = {}
    for item in ds:
        EXPECTED_MAP[item['modelName']] = [
            a.get('assertionTruth', 'Valid') or 'Valid'
            for a in item.get('assertions', [])
        ]


def check_case(model_name, gen_dir):
    result = {
        'model': model_name, 'compiled': False,
        'assertions_total': len(EXPECTED_MAP.get(model_name, [])),
        'assertions_passed': 0
    }
    d = gen_dir / model_name
    # Check if verification was done (verifiedCode.rts OR verification_results.json)
    vf = d / 'verifiedCode.rts'
    vr = d / 'verification_results.json'
    if not vf.exists() and not vr.exists():
        return result
    
    # If we have verification_results.json, use it directly
    if vr.exists():
        try:
            vdata = json.load(open(vr, 'r', encoding='utf-8'))
            # Check if any result has a non-empty actualResult (means PAT actually ran)
            has_valid = any(item.get('actualResult', '') != '' for item in vdata)
            has_parse_err = any('syntax error' in item.get('patResult', '').lower() or 'not found' in item.get('patResult', '').lower() for item in vdata)
            if has_parse_err or not has_valid:
                return result  # compilation actually failed
            result['compiled'] = True
            for item in vdata:
                actual = item.get('actualResult', '')
                expected = item.get('desiredOutcome', 'Valid')
                if actual == expected:
                    result['assertions_passed'] += 1
            return result
        except Exception:
            pass
    
    # Fallback: re-verify from verifiedCode.rts
    if not vf.exists():
        return result
    out = d / '_check.txt'
    subprocess.run([PAT_EXE, '-rts', str(vf), str(out)],
                   capture_output=True, text=True, timeout=60)
    if not out.exists():
        return result
    c = out.read_text(encoding='utf-8', errors='ignore')
    if 'Parsing Error' in c:
        return result
    result['compiled'] = True
    actuals = []
    for sec in c.split('=' * 55):
        if 'is VALID' in sec and 'NOT' not in sec:
            actuals.append('Valid')
        elif 'is NOT valid' in sec:
            actuals.append('Invalid')
    expecteds = EXPECTED_MAP.get(model_name, [])
    for a, e in zip(actuals, expecteds):
        if a == e:
            result['assertions_passed'] += 1
    return result


def compute_metrics(results):
    compiled = sum(1 for r in results if r['compiled'])
    full = sum(1 for r in results if r['compiled'] and r['assertions_passed'] == r['assertions_total'])
    ta = sum(r['assertions_total'] for r in results)
    pa = sum(r['assertions_passed'] for r in results)
    n = len(results)
    return {'CSR': compiled/n, 'FPR': full/n, 'APR': pa/ta if ta else 0,
            'compiled': compiled, 'full_pass': full, 'total_a': ta, 'passed_a': pa, 'total': n}


def clear(path):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def run_one(config):
    print(f"\n{'='*60}\n  {config}\n{'='*60}")

    if config == 'noplan':
        script = str(NO_PLAN_DIR / 'pipeline_rt_no_planning.py')
        out_dir = NO_PLAN_DIR / f'generated_code_{config}'
        env = {**os.environ, 'PAT_OUTPUT_DIR': str(out_dir)}
        cwd = str(NO_PLAN_DIR)
    elif config == 'norepair':
        script = str(FULL_DIR / 'pipeline_single.py')
        out_dir = FULL_DIR / f'generated_code_{config}'
        env = {**os.environ, 'PAT_NO_REPAIR': '1', 'PAT_OUTPUT_DIR': str(out_dir)}
        cwd = str(FULL_DIR)
    elif config == 'both':
        script = str(NO_PLAN_DIR / 'pipeline_rt_no_planning.py')
        out_dir = NO_PLAN_DIR / f'generated_code_{config}'
        env = {**os.environ, 'PAT_NO_REPAIR': '1', 'PAT_OUTPUT_DIR': str(out_dir)}
        cwd = str(NO_PLAN_DIR)
    else:
        raise ValueError(config)

    clear(out_dir)

    for m in ALL_MODELS:
        print(f"  {m} ... ", end='', flush=True)
        t0 = time.time()
        r = subprocess.run(
            [sys.executable, script, '--model', m, '--dataset', str(DATASET)],
            capture_output=True, text=True, timeout=900, cwd=cwd, env=env
        )
        print(f"{time.time()-t0:.0f}s rc={r.returncode}")

    rs = [check_case(m, out_dir) for m in ALL_MODELS]
    m = compute_metrics(rs)

    path = FULL_DIR / f'experiment_{config}.json'
    json.dump({'config': config, 'metrics': m, 'cases': rs}, open(path, 'w', encoding='utf-8'),
              indent=2, ensure_ascii=False)

    print(f"  CSR={m['CSR']:.1%}  FPR={m['FPR']:.1%}  APR={m['APR']:.1%}  -> {path.name}")


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True, choices=['noplan', 'norepair', 'both', 'all'])
    args = p.parse_args()
    if args.config == 'all':
        for c in ['noplan', 'norepair', 'both']:
            run_one(c)
    else:
        run_one(args.config)
