"""
pipeline_rt_no_planning.py
No-Planning variant of the PAT-Agent pipeline with RTS (Real-Time System) support.

Key difference from pipeline_a.py (CSP-only):
- Supports targetModule: rts with -rts engine flag
- Uses RTS-specific RAG examples and syntax rules
- Injects timed modeling annotations

Key difference from Full_Pipeline/pipeline.py:
- SKIPS gen_const_and_vars, gen_actions, gen_nl_instructions (NO Planning LLM)
- Builds prompt directly from system description + time constraints
- Used as Condition B in the Planning vs No-Planning ablation experiment
"""

import json
import time
import datetime
import os
import sys
from openai import OpenAI
import subprocess
import re
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.feature_extraction.text import TfidfVectorizer

# --- API Setup ---
deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
if not deepseek_key:
    raise RuntimeError(
        "DEEPSEEK_API_KEY not set!\n"
        "Please set it in your terminal:\n"
        "  PowerShell: $env:DEEPSEEK_API_KEY = 'sk-your-key'\n"
        "  CMD:        set DEEPSEEK_API_KEY=sk-your-key"
    )

client = OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com")
client_code = OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com")

# ===== HELPER: Target Module Detection =====

def _get_target_module(structured_data):
    target_module = (structured_data.get('targetModule') or 'csp').strip().lower()
    if target_module not in {'csp', 'ta', 'rts'}:
        target_module = 'csp'
    return target_module

def _get_module_file_extension(target_module):
    if target_module == 'ta':
        return 'ta'
    if target_module == 'rts':
        return 'rts'
    return 'csp'

def _get_module_resources(target_module):
    if target_module == 'ta':
        return {
            'rag_path': '../Full_Pipeline/database-rag-ta.json',
            'syntax_path': '../Full_Pipeline/syntax-dataset-rt.json',
            'syntax_section': 'ta_syntax'
        }
    if target_module == 'rts':
        return {
            'rag_path': '../Full_Pipeline/database-rag-rts.json',
            'syntax_path': '../Full_Pipeline/syntax-dataset-rt.json',
            'syntax_section': None  # flat structure, not section-based
        }
    return {
        'rag_path': '../Full_Pipeline/database-rag-claude.json',
        'syntax_path': '../Full_Pipeline/syntax-dataset.json',
        'syntax_section': None
    }

def _build_timed_nl_annotations(structured_data):
    """Build RTS-specific NL annotations for code generation prompt."""
    target_module = _get_target_module(structured_data)
    if target_module == 'csp':
        return ""

    lines = [
        "// === Timed Modeling Instructions (RTS Module) ===",
        f"// Target module: {target_module.upper()}. Use {target_module.upper()} timing primitives."
    ]

    if target_module == 'rts':
        tc_items = structured_data.get('timeConstraints', [])
        if tc_items:
            lines.append("// Key timing requirements:")
            for tc in tc_items[:3]:
                lines.append(f"// - {tc.get('description', '')}")
        lines.append("// CRITICAL RTS rules:")
        lines.append("// 1. Model time using Wait[n]/timeout[n]/deadline[n]/within[n].")
        lines.append("// 2. Wait[n] MUST use ; (semicolon) NOT -> for sequential composition.")
        lines.append("// 3. Do NOT use Wait[t1,t2] bounded form — causes crash in PAT 3.5.1.")
        lines.append("// 4. RTS keywords (timeout/deadline/within/interrupt/Wait) CANNOT be event names.")
        lines.append("// 5. Timed processes CANNOT appear inside [guard]P or ifa{}. Use ifb(guard){P}.")
        lines.append("// 6. RTS only supports || and ||| — NOT alphabetized parallel |[A]|.")
        lines.append("// 7. timing operators must be at TOP-LEVEL process expression, NOT inside guards.")

    lines.append("// Output ONLY pure PAT code — no markdown, no comments outside the code.")
    return "\n".join(lines)


# ===== HELPER: Save Runtime =====

def save_run_time(model_name, stage, run_time, hasMismatch=None, codegenFailed=None):
    run_time_record_path = f'./run_time_record/{model_name}.json'
    os.makedirs('./run_time_record', exist_ok=True)
    try:
        with open(run_time_record_path, 'r', encoding='utf-8') as f:
            run_time_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        run_time_data = {}

    save_content = {"runTime": run_time}
    if hasMismatch is not None and hasMismatch != "":
        save_content["hasMismatch"] = hasMismatch
    if codegenFailed is not None and codegenFailed != "":
        save_content["codegenFailed"] = codegenFailed

    run_time_data[stage] = save_content
    with open(run_time_record_path, 'w', encoding='utf-8') as f:
        json.dump(run_time_data, f, indent=4)


# ===== HELPER: Build System Description (No Planning) =====

def _process_assertions(assertions):
    """Build assertion instruction strings."""
    nl_annotations = []
    for i, item in enumerate(assertions):
        comp = item.get("component", "")
        scope = f'subsystem {comp}' if comp else 'system'
        atype = item.get("assertionType", "").strip().lower()

        if atype == "deadlock-free":
            nl_annotations.append(f"Assertion {i}: assert that the system is deadlockfree")
        elif atype == "reachability":
            state = item.get("stateName", "").strip()
            conds = item.get("conditions", [])
            if conds:
                cond_str = ", ".join(str(c) for c in conds)
                nl_annotations.append(
                    f"Assertion {i}: assert that {scope} reaches a state where {state} "
                    f"with conditions: {cond_str}"
                )
            else:
                nl_annotations.append(
                    f"Assertion {i}: assert that {scope} reaches a state where {state}"
                )
        elif atype == "ltl":
            formula = item.get("formula", "").strip()
            nl_annotations.append(f"Assertion {i}: assert that {scope} satisfies LTL: {formula}")
        else:
            nl_annotations.append(f"Assertion {i}: assert {atype} for {scope}")
    return nl_annotations


def _build_no_planning_prompt(structured_data):
    """Build a prompt directly from system description — NO intermediate planning LLM."""
    model_name = structured_data.get('modelName', 'unknown')
    model_desc = structured_data.get('modelDesc', '')
    subsystems = structured_data.get('subsystems', [])
    interaction = structured_data.get('interactionMode', 'interleaving')
    assertions = structured_data.get('assertions', [])

    # Timed annotations
    timed_annotations = _build_timed_nl_annotations(structured_data)

    # Build process descriptions
    proc_lines = []
    for sub in subsystems:
        name = sub.get('name', 'Unnamed')
        desc = sub.get('description', '')
        proc_lines.append(f"- Process '{name}': {desc}")

    # Build assertion lines
    assertion_lines = _process_assertions(assertions)
    assertion_text = "\n".join(f"  {a}" for a in assertion_lines)

    prompt = f"""System: {model_name}
Description: {model_desc}

Processes ({len(subsystems)} total):
{chr(10).join(proc_lines)}

Interaction mode: {interaction}

Required Assertions:
{assertion_text}

{timed_annotations}

Based on the above, generate complete PAT code with #define propositions and #assert statements.
Output ONLY code — no markdown, no explanatory text."""
    return prompt


# ===== RAG Retrieval =====

def _get_most_relevant_rag_example_basic(instruction, rag_database_path):
    try:
        with open(rag_database_path, 'r', encoding='utf-8') as f:
            database = json.load(f)
        if not instruction:
            return {"nl": "", "code": ""}

        nls = [entry["nl"] for entry in database if entry.get("nl")]
        if not nls:
            return {"nl": "", "code": ""}

        vectorizer = TfidfVectorizer().fit(nls + [instruction])
        vectors = vectorizer.transform(nls + [instruction])
        scores = cosine_similarity(vectors[-1], vectors[:-1])[0]
        best = database[scores.argmax()]
        return {"nl": best["nl"], "code": best["code"]}
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"RAG error: {e}")
        return {"nl": "", "code": ""}


# ===== LLM Code Generation =====

def _get_llm_code_completion(prompt_text, history_file_path):
    try:
        response = client_code.chat.completions.create(
            model="deepseek-v4-flash",
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt_text}]
        )
        answer = response.choices[0].message.content

        interaction = {
            'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'question': prompt_text,
            'answerClaude': answer,
            'PAT': ""
        }
        try:
            with open(history_file_path, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            history = []
        history.append(interaction)
        with open(history_file_path, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=4)
        return answer
    except Exception as e:
        print(f"LLM error: {e}")
        return ""


# ===== Code Generation (RTS-aware, No-Planning) =====

def gen_code(structured_data, direct_prompt):
    model_name = structured_data.get('modelName', 'unknown_model')
    target_module = _get_target_module(structured_data)
    resources = _get_module_resources(target_module)
    print(f"[gen_code] {model_name} (module={target_module})")
    start_time = time.perf_counter()

    # 1. RAG
    retrieved = _get_most_relevant_rag_example_basic(direct_prompt, resources['rag_path'])
    print(f"  RAG matched: {retrieved['nl'][:60]}...")

    # 2. Syntax rules
    syntax_general = ""
    syntax_pitfalls = ""
    syntax_reference = ""
    _operator_keys = ['wait', 'timeout', 'deadline', 'within', 'interrupt', 'ifb', 'urgent', 'assertions']
    try:
        with open(resources['syntax_path'], 'r', encoding='utf-8') as f:
            sdata = json.load(f)
        if resources['syntax_section']:
            section = sdata.get(resources['syntax_section'], {})
            syntax_general = section.get("general_info", "")
            syntax_pitfalls = section.get("pitfalls_rules", "")
        else:
            syntax_general = sdata.get("general_info", "")
            syntax_pitfalls = sdata.get("pitfalls_rules", "")
            _ref_parts = [syntax_general]
            for k in _operator_keys:
                if k in sdata:
                    _ref_parts.append(f"{k}: {sdata[k]}")
            syntax_reference = "\n".join(_ref_parts)
    except Exception as e:
        print(f"  Syntax load error: {e}")

    _syntax_section = syntax_reference if syntax_reference else (syntax_general + "\n" + syntax_pitfalls)

    # 3. Prompt
    final_prompt = f"""You are an expert in PAT (Process Analysis Toolkit).

--- Quick Reference ---
{_syntax_section}

Pitfalls: {syntax_pitfalls}

--- Example ---
Description: {retrieved['nl']}
Code: {retrieved['code']}

Now generate PAT code for:
{direct_prompt}

### Response:"""

    print(f"  Prompt length: {len(final_prompt)} chars")
    code_output = _get_llm_code_completion(final_prompt, './history/claude-code.json')

    end_time = time.perf_counter()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    save_run_time(model_name, f'codegen-time_{timestamp}', end_time - start_time)
    print(f"  Code generation: {end_time - start_time:.1f}s")
    return code_output


# ===== Code Splitting =====

def _split_code_and_assertions(code):
    pat = re.compile(
        r'(?m)'
        r'(?:'
            r'^(?!\s*//)\s*'
            r'#define[^\n]*\n'
        r')*'
        r'\s*'
        r'^(?!\s*//)\s*'
        r'#assert[^\n]*;?'
    )
    body = pat.sub('', code).strip()
    blocks = pat.findall(code)
    defs, asserts = [], []
    for blk in blocks:
        lines = blk.splitlines()
        for line in lines[:-1]:
            if line.startswith('#define'):
                defs.append(line)
        asserts.append(lines[-1])
    seen = set()
    uniq_defs = [d for d in defs if not (d in seen or seen.add(d))]
    return [body + '\n\n' + '\n'.join(uniq_defs + [a]) for a in asserts]


def _extract_longest_code_block(text):
    pattern = r"```(?:[a-zA-Z]*\n)?(.*?)```"
    blocks = re.findall(pattern, text, re.DOTALL)
    if not blocks:
        return text.strip()
    print(f"  Extracted longest of {len(blocks)} code blocks")
    return max(blocks, key=len).strip()


# ===== Verification (RTS-aware) =====

def verify_code(structured_data, code_to_verify, is_refine=False, refine_round=0):
    model_name = structured_data.get('modelName', 'unknown_model')
    target_module = _get_target_module(structured_data)
    file_ext = _get_module_file_extension(target_module)

    # TA guard
    if target_module == 'ta':
        print("  TA module not available in CLI — skipping")
        return [], True, True

    print(f"[verify] {model_name} (module={target_module})")
    start_time = time.perf_counter()

    # Save to refinement history
    try:
        record = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "processedCode": code_to_verify
        }
        try:
            with open("./history/claude-refinement.json", "r", encoding="utf-8") as f:
                ref_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            ref_data = []
        ref_data.append(record)
        with open("./history/claude-refinement.json", "w", encoding="utf-8") as f:
            json.dump(ref_data, f, indent=2)
    except Exception as e:
        print(f"  Refinement save error: {e}")

    root_path = "c:/Users/njr/Desktop/PAT-Agent-master"
    pat_exe = "C:/Program Files/Process Analysis Toolkit/Process Analysis Toolkit 3.5.1/PAT3.Console.exe"
    gen_root = os.environ.get("PAT_OUTPUT_DIR", f"{root_path}/Automated_Pipelines/No_Planning/generated_code")

    if is_refine:
        folder = f"{gen_root}/{model_name}/refine_round_{refine_round}"
    else:
        folder = f"{gen_root}/{model_name}"

    os.makedirs(folder, exist_ok=True)
    if not is_refine:
        for fn in os.listdir(folder):
            fp = os.path.join(folder, fn)
            if os.path.isfile(fp):
                try:
                    os.remove(fp)
                except Exception:
                    pass

    code_blocks = _split_code_and_assertions(code_to_verify)
    assertions_list = structured_data.get('assertions', [])

    if len(code_blocks) != len(assertions_list):
        print(f"  Block count mismatch: {len(code_blocks)} vs {len(assertions_list)}")
        return [], True, True

    results = []
    any_empty = False
    engine_flag = f"-{target_module}"

    for i, block in enumerate(code_blocks):
        in_file = f"{folder}/{i}.{file_ext}"
        out_file = f"{folder}/pat_output_{i}.txt"

        try:
            with open(in_file, 'w', encoding='utf-8') as f:
                f.write(block)
        except Exception as e:
            print(f"  Write error block {i}: {e}")
            any_empty = True
            continue

        if ('reaches' in block) or ('deadlockfree' in block):
            cmd = [pat_exe, engine_flag, "-engine", "1", in_file, out_file]
        else:
            cmd = [pat_exe, engine_flag, in_file, out_file]

        try:
            subprocess.run(cmd, check=True, timeout=300)
            with open(out_file, 'r', encoding='utf-8') as f:
                output = f.read()

            if not output:
                any_empty = True
                print(f"  Block {i}: empty PAT output")

            start_m = "********Verification Result********"
            end_m = "********Verification Setting********"
            si = output.find(start_m)
            ei = output.find(end_m)

            pat_result = ""
            if si != -1 and ei != -1:
                pat_result = output[si + len(start_m):ei].strip()
            else:
                any_empty = True
                pat_result = "Verification result not found"

            actual = ""
            m = re.search(r"is\s+(\w+)", pat_result, re.IGNORECASE)
            if m:
                actual = "Valid" if m.group(1).upper() == "VALID" else "Invalid"
            else:
                any_empty = True

            assertion_line = ""
            for line in block.splitlines():
                if line.strip().startswith("#assert"):
                    assertion_line = line.strip()
                    break

            results.append({
                'assertion': assertion_line,
                'patResult': pat_result,
                'actualResult': actual
            })
            print(f"  Block {i}: {actual}")

        except subprocess.TimeoutExpired:
            print(f"  Block {i}: TIMEOUT")
            return [], True, True
        except subprocess.CalledProcessError as e:
            print(f"  Block {i}: PAT error {e}")
            return [], True, True

    # Compare with expected
    has_mismatch = False
    mismatches = []
    for i, result in enumerate(results):
        expected = "Valid"
        if i < len(assertions_list):
            expected = assertions_list[i].get("assertionTruth", "Valid") or "Valid"
        result['desiredOutcome'] = expected

        if result.get('actualResult', '') != expected:
            has_mismatch = True
            mismatches.append({
                'assertion': result['assertion'],
                'expected': expected,
                'actual': result.get('actualResult', ''),
                'patResult': result.get('patResult', '')
            })

    # Save results
    try:
        with open(f"{folder}/verification_results.json", 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"  Save results error: {e}")

    end_time = time.perf_counter()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    save_run_time(model_name, f'verification-time_{timestamp}', end_time - start_time,
                  hasMismatch=has_mismatch, codegenFailed=any_empty)
    print(f"  Verification: {end_time - start_time:.1f}s, mismatch={has_mismatch}, empty={any_empty}")
    return results, has_mismatch, any_empty


# ===== Refinement =====

def gen_refine(structured_data, current_code, mismatches, refine_round):
    model_name = structured_data.get('modelName', 'unknown_model')
    target_module = _get_target_module(structured_data)
    resources = _get_module_resources(target_module)

    print(f"[refine] {model_name} round {refine_round}")
    start_time = time.perf_counter()

    # Build mismatch feedback
    mismatch_text = ""
    for m in mismatches:
        mismatch_text += (
            f"- Assertion: {m['assertion']}\n"
            f"  Expected: {m['expected']}, Got: {m['actual']}\n"
            f"  PAT output: {m['patResult'][:200]}\n\n"
        )

    refine_prompt = f"""The following PAT code has verification failures. Fix the code to satisfy all assertions.

Current code:
```
{current_code}
```

Mismatches:
{mismatch_text}

Generate corrected PAT code. Output ONLY code — no markdown, no explanations.
### Response:"""

    refined_output = _get_llm_code_completion(refine_prompt, './history/claude-refinement.json')
    refined_code = _extract_longest_code_block(refined_output)

    end_time = time.perf_counter()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    save_run_time(model_name, f'refine-time_round{refine_round}_{timestamp}', end_time - start_time)
    print(f"  Refinement round {refine_round}: {end_time - start_time:.1f}s")
    return refined_code


# ===== MAIN: No-Planning RTS Pipeline =====

if __name__ == '__main__':
    dataset_path = '../../Datasets/PAT-RT.json'

    # Allow --model flag to run a single case
    target_model = None
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == '--model' and i + 1 < len(args):
            target_model = args[i + 1]
        elif arg == '--dataset' and i + 1 < len(args):
            dataset_path = args[i + 1]

    print(f"=== No-Planning RTS Pipeline ===")
    print(f"Dataset: {dataset_path}")
    if target_model:
        print(f"Target model: {target_model}")

    with open(dataset_path, 'r', encoding='utf-8') as f:
        cases = json.load(f)

    for idx, case in enumerate(cases):
        model_name = case.get('modelName', f'case_{idx}')

        # Skip if targeting a specific model
        if target_model and model_name != target_model:
            continue

        print(f"\n{'='*60}")
        print(f"Case {idx}: {model_name}")
        print(f"{'='*60}")

        # Build prompt directly (NO planning LLMs)
        direct_prompt = _build_no_planning_prompt(case)

        # Generate + verify + refine loop
        gen_count = 0
        max_gen = 3
        verified = False

        while gen_count < max_gen and not verified:
            if gen_count > 0:
                print(f"\n--- Regeneration attempt {gen_count}/{max_gen - 1} ---")

            # Generate code
            raw_output = gen_code(case, direct_prompt)
            code = _extract_longest_code_block(raw_output)

            if not code:
                print("  Empty code — retrying")
                gen_count += 1
                continue

            # Verify
            results, has_mismatch, any_empty = verify_code(case, code)

            if any_empty:
                print("  Empty/malformed verification — retrying")
                gen_count += 1
                continue

            if not has_mismatch:
                print(f"  ALL ASSERTIONS PASSED!")
                gen_root = os.environ.get("PAT_OUTPUT_DIR", "./generated_code")
                vf_path = f"{gen_root}/{model_name}/verifiedCode.{_get_module_file_extension(_get_target_module(case))}"
                os.makedirs(os.path.dirname(vf_path), exist_ok=True)
                with open(vf_path, 'w', encoding='utf-8') as vf:
                    vf.write(code)
                verified = True
                break

            # Refine (skip if PAT_NO_REPAIR is set)
            if os.environ.get('PAT_NO_REPAIR'):
                print("  Skipping refinement (PAT_NO_REPAIR=1)")
                gen_count += 1
                continue
            mismatches = [
                {'assertion': r['assertion'], 'expected': r.get('desiredOutcome', 'Valid'),
                 'actual': r.get('actualResult', ''), 'patResult': r.get('patResult', '')}
                for r in results
                if r.get('actualResult', '') != r.get('desiredOutcome', 'Valid')
            ]

            for refine_round in range(1, 4):
                print(f"\n--- Refinement round {refine_round} ---")
                refined_code = gen_refine(case, code, mismatches, refine_round)
                if not refined_code:
                    print("  Empty refinement — skipping")
                    continue

                r_results, r_mismatch, r_empty = verify_code(case, refined_code, is_refine=True, refine_round=refine_round)

                if r_empty:
                    continue

                if not r_mismatch:
                    print(f"  REFINEMENT SUCCESS at round {refine_round}!")
                    ext = _get_module_file_extension(_get_target_module(case))
                    gen_root = os.environ.get("PAT_OUTPUT_DIR", "./generated_code")
                    vf_path = f"{gen_root}/{model_name}/verifiedCode.{ext}"
                    os.makedirs(os.path.dirname(vf_path), exist_ok=True)
                    with open(vf_path, 'w', encoding='utf-8') as vf:
                        vf.write(refined_code)
                    verified = True
                    break

                code = refined_code
                mismatches = [
                    {'assertion': r['assertion'], 'expected': r.get('desiredOutcome', 'Valid'),
                     'actual': r.get('actualResult', ''), 'patResult': r.get('patResult', '')}
                    for r in r_results
                    if r.get('actualResult', '') != r.get('desiredOutcome', 'Valid')
                ]

            gen_count += 1

        if not verified:
            print(f"  FAILED after {gen_count} generation attempts")

    print("\n=== Pipeline Complete ===")
