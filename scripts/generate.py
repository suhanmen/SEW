"""Generate solutions to CodeContests problems with an instruction-tuned model (the corpora in data/codecontests/).

    python scripts/generate.py --model Qwen/Qwen3.5-9B --lang python --out results/generations/python/Qwen3.5-9B.jsonl

The problems are CodeContests valid + test (deepmind/code_contests on the Hugging Face Hub, downloaded on first
use); the problems of scripts/codecontests_selection.json are generated, in that order. Decoding is greedy with at most 8192 new tokens, bf16. Batches are the consecutive windows of 20
problems in that order (minus problems already generated); with left padding the batch composition can change a
greedy output, so the windows, and the 5-problem first batch of the gemma-4 runs, follow our runs.

Each output line is {"id", "code", "complete", "n_new_tokens"}. "code" is the last closed fenced block whose
language tag matches (else the last closed block; an unterminated block gives everything after its opening
fence; no block gives ""). "complete" is true when the answer contains a closed fenced block (an even, non-zero
number of ```); answers that hit the token limit are therefore incomplete.

Thinking is disabled for Qwen3.5 and DeepSeek-R1 models, and gpt-oss models use reasoning effort "low", as in
our experiments. The gpt-oss chat template writes the current date into the prompt; it is fixed to the date of our
runs (--prompt-date). Existing ids in --out are skipped, so an interrupted run can be resumed.
Requires torch, transformers and pyarrow.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from attacks.llm_rewrite import extract_fenced_code  # noqa: E402
from prepare_codecontests import load_rows, row_of, selection  # noqa: E402

LANG_NAME = {"python": "Python", "java": "Java", "cpp": "C++"}

PROMPT = """You are an expert {lang_name} programmer.

Solve the following problem.

Requirements:
- The program reads its input from standard input and writes the answer to standard output, exactly in the format the problem specifies.
- The code must be complete and runnable on its own.

Output format:
- Return ONLY one fenced code block: ```{lang}
- No explanation, no test code, no example usage outside the code block.
- Output exactly ONE code block; do not offer alternative solutions.

Problem:
{problem}

Answer:
"""
THINK_OFF_MODELS = ("Qwen3.5", "DeepSeek-R1")


def render(tokenizer, prompt, think_off, reasoning_effort, prompt_date=None):
    kwargs = {"enable_thinking": False} if think_off else {}
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    msg = [{"role": "user", "content": prompt}]
    try:
        text = tokenizer.apply_chat_template(msg, tokenize=False, add_generation_prompt=True, **kwargs)
    except TypeError:
        text = tokenizer.apply_chat_template(msg, tokenize=False, add_generation_prompt=True)
    if think_off and re.search(r"<think>\s*$", text):
        text += "</think>\n\n"
    if prompt_date:
        text = re.sub(r"Current date: \d{4}-\d{2}-\d{2}", "Current date: " + prompt_date, text)
    return text


def eos_ids(model, tokenizer):
    cfg = model.config
    ids = []
    for src in (getattr(model, "generation_config", None), cfg, getattr(cfg, "text_config", None)):
        v = getattr(src, "eos_token_id", None) if src is not None else None
        if v is not None:
            ids.extend(v if isinstance(v, (list, tuple)) else [v])
    if tokenizer.eos_token_id is not None:
        ids.append(tokenizer.eos_token_id)
    return list(dict.fromkeys(int(i) for i in ids)) or None


def pad_id(model, tokenizer):
    cfg = model.config
    for v in (getattr(cfg, "pad_token_id", None), getattr(getattr(cfg, "text_config", None), "pad_token_id", None),
              tokenizer.pad_token_id, tokenizer.eos_token_id):
        if v is not None and not isinstance(v, (list, tuple)):
            return int(v)
    return None


def count_new(ids, eos, pad):
    ids = ids.tolist()
    for i, t in enumerate(ids):
        if t in eos:
            return i + 1
    return sum(1 for t in ids if t != pad) if pad is not None else len(ids)


def main():
    ap = argparse.ArgumentParser(description="Generate CodeContests solutions (greedy, 8192 new tokens)")
    ap.add_argument("--model", required=True, help="Hugging Face model id, e.g. Qwen/Qwen3.5-9B")
    ap.add_argument("--lang", required=True, choices=["python", "java", "cpp"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--ids-from", help="JSONL whose ids are generated (default: the problems of scripts/codecontests_selection.json)")
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--first-batch", default="auto",
                    help="size of a separate first batch (auto: 5 for gemma-4 models, whose runs began with a 5-problem trial, else 0)")
    ap.add_argument("--max-new-tokens", type=int, default=8192)
    ap.add_argument("--max-prompt-tokens", type=int, default=4096)
    ap.add_argument("--thinking", choices=["auto", "off", "default"], default="auto",
                    help="auto: off for Qwen3.5 / DeepSeek-R1 models, the chat template's default otherwise")
    ap.add_argument("--reasoning-effort", default="auto", help="auto: 'low' for gpt-oss models; 'none' to leave the template default")
    ap.add_argument("--prompt-date", default="2026-09-17",
                    help="date written by chat templates that include the current date (gpt-oss); 'today' keeps the template's date")
    ap.add_argument("--cache-dir", help="Hugging Face cache directory for the model")
    ap.add_argument("--data-cache-dir", help="Hugging Face cache directory for CodeContests (about 110 MB)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=0, help="generate the first N problems of --ids-from only")
    a = ap.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed
    set_seed(a.seed)
    think_off = a.thinking == "off" or (a.thinking == "auto" and any(s in a.model for s in THINK_OFF_MODELS))
    effort = {"auto": "low" if "gpt-oss" in a.model else None, "none": None}.get(a.reasoning_effort, a.reasoning_effort)

    if a.ids_from:
        with open(a.ids_from, encoding="utf-8") as f:
            wanted = [json.loads(line)["id"] for line in f if line.strip()]
    else:
        wanted = [pid for pid, _ in selection()[a.lang]]
    rows = load_rows(a.data_cache_dir)
    problems = {pid: row_of(rows, pid)["description"].strip() for pid in wanted}
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        with open(out, encoding="utf-8") as f:
            done = {json.loads(line)["id"] for line in f if line.strip()}
    if a.limit:
        wanted = wanted[:a.limit]
    first = int(a.first_batch) if a.first_batch != "auto" else (5 if "gemma-4" in a.model else 0)
    head = set(wanted[:first])
    batches = [wanted[:first]] if first else []
    batches += [[i for i in wanted[b:b + a.batch_size] if i not in head] for b in range(0, len(wanted), a.batch_size)]
    batches = [bt for bt in ([i for i in bt if i not in done] for bt in batches) if bt]
    todo = [i for bt in batches for i in bt]
    print(f"[generate] {a.model} {a.lang}: {len(todo)} to generate, {len(done)} already in {out} "
          f"(thinking {'off' if think_off else 'default'}, reasoning effort {effort or 'default'})", flush=True)
    if not todo:
        return

    tk = AutoTokenizer.from_pretrained(a.model, trust_remote_code=True, cache_dir=a.cache_dir)
    tk.padding_side = "left"
    model = AutoModelForCausalLM.from_pretrained(a.model, dtype=torch.bfloat16, device_map="auto",
                                                 trust_remote_code=True, cache_dir=a.cache_dir).eval()
    dev = next(model.parameters()).device
    eos, pad = eos_ids(model, tk), pad_id(model, tk)

    n_done = 0
    for batch in batches:
        prompts = [render(tk, PROMPT.format(lang_name=LANG_NAME[a.lang], lang=a.lang, problem=problems[i]), think_off, effort,
                          None if a.prompt_date == "today" else a.prompt_date) for i in batch]
        enc = tk(prompts, return_tensors="pt", padding=True, truncation=True, max_length=a.max_prompt_tokens).to(dev)
        with torch.no_grad():
            gen = model.generate(input_ids=enc.input_ids, attention_mask=enc.attention_mask, pad_token_id=pad,
                                 eos_token_id=eos, do_sample=False, max_new_tokens=a.max_new_tokens, use_cache=True)
        with open(out, "a", encoding="utf-8") as f:
            for j, pid in enumerate(batch):
                new = gen[j, enc.input_ids.shape[1]:]
                text = tk.decode(new, skip_special_tokens=True).strip()
                n_fence = text.count("```")
                rec = {"id": pid, "code": extract_fenced_code(text, a.lang), "complete": n_fence > 0 and n_fence % 2 == 0,
                       "n_new_tokens": count_new(new, set(eos or []), pad)}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        n_done += len(batch)
        print(f"[generate] {n_done}/{len(todo)}", flush=True)


if __name__ == "__main__":
    main()
