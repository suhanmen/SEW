"""Embed SEW, apply an attack to the watermarked code, and detect on the attacked code.

    PYTHONHASHSEED=0 python scripts/evaluate_attacks.py --lang python \
        --generations data/codecontests/python/Qwen3.5-9B.jsonl --human data/codecontests/python/human.jsonl \
        --attacks format,lint,comments,rename,flip25,flip50,flip75,flip100 --out results/attacks__python__Qwen3.5-9B.json

Negatives are the human solutions (unattacked). A generation that cannot be watermarked, or that an
attack turns into an empty file, keeps score 0 and stays in the denominator. PYTHONHASHSEED=0 is
needed only for the flip attacks (their random stream is seeded from hash(code)).
"""
import argparse
import difflib
import json
import math
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sew import EXPERIMENT_KEY, detect, embed  # noqa: E402
from attacks import ATTACKS, EDIT_ATTACKS, FLIP_ATTACKS, attack  # noqa: E402
from evaluate import auroc, read_jsonl, score, tpr_at_fpr  # noqa: E402

TOK = re.compile(r"[A-Za-z_]\w*|\d+\.?\d*|\S")


def edit_ratio(a, b):
    ta, tb = TOK.findall(a), TOK.findall(b)
    sm = difflib.SequenceMatcher(None, ta, tb)
    return sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal") / max(len(ta), 1)


def missing_tools(names, lang):
    """Stop before running when an attack's tool is missing: its files would otherwise score 0 as if the
    watermark had been removed."""
    need = []
    if "format" in names and lang == "python":
        try:
            import black  # noqa: F401
        except ImportError:
            need.append("black (see environment/environment.yaml)")
    if "lint" in names:
        tool = {"python": "ruff", "java": "javac", "cpp": "clang-tidy"}[lang]
        if shutil.which(tool) is None:
            need.append(f"{tool} on PATH")
        if lang == "java":
            from attacks.edit_attacks import EP_DIR, EP_JARS
            if not all(j.exists() for j in EP_JARS):
                need.append(f"the Error Prone jars in {EP_DIR} (set ERRORPRONE_DIR)")
    if need:
        sys.exit("missing for the requested attacks: " + "; ".join(need))


def main():
    ap = argparse.ArgumentParser(description="Detection of SEW after attacks on the watermarked code")
    ap.add_argument("--lang", required=True, choices=["python", "java", "cpp"])
    ap.add_argument("--generations", required=True, help="JSONL with id, code, complete")
    ap.add_argument("--human", required=True, help="JSONL with id, code (negatives, matched by id)")
    ap.add_argument("--attacks", default="edit", help="'edit' (format,lint,comments,rename), 'flip' (flip25,flip50,flip75,flip100), "
                                                      "'all' (both), 'llm' (needs a GPU), or a comma-separated list of these and attack names, e.g. all,llm")
    ap.add_argument("--key", help="secret key as hex (default: the key used in the paper's experiments)")
    ap.add_argument("--alpha", type=float, default=0.01)
    ap.add_argument("--uniform-q", action="store_true", help="use q = 1/2 for every rule instead of the fitted q")
    ap.add_argument("--include-incomplete", action="store_true", help="keep generations that hit the token limit")
    ap.add_argument("--limit", type=int, default=0, help="use the first N lines of --generations only")
    ap.add_argument("--out", help="write per-item results and the summary to this JSON file")
    a = ap.parse_args()
    key = bytes.fromhex(a.key) if a.key else EXPERIMENT_KEY
    calib = not a.uniform_q
    groups = {"edit": EDIT_ATTACKS, "flip": FLIP_ATTACKS, "all": EDIT_ATTACKS + FLIP_ATTACKS}
    names = [n for item in a.attacks.split(",") for n in groups.get(item, [item])]
    for n in names:
        if n != "llm" and n not in ATTACKS:
            sys.exit(f"unknown attack: {n}")

    missing_tools(names, a.lang)
    rows_all = read_jsonl(a.generations)
    if a.limit:
        rows_all = rows_all[:a.limit]
    gens = [g for g in rows_all if a.include_incomplete or g.get("complete", True)]
    human = {h["id"]: h["code"] for h in read_jsonl(a.human)}

    # watermark once
    items = []
    for g in gens:
        row = {"id": g["id"], "orig": g["code"], "wm": g["code"], "inserted": False, "p": 1.0}
        if g["code"]:
            r = embed(g["code"], a.lang, key)
            row["status"] = r["status"]
            if r["status"] == "ok":
                row.update(wm=r["code"], inserted=True, p=detect(r["code"], a.lang, key, calibrate=calib)["p"])
        else:
            row["status"] = "abstain:no_code"
        items.append(row)
    neg = [score(detect(human[g["id"]], a.lang, key, calibrate=calib)["p"]) for g in gens if g["id"] in human]

    pct = lambda x: round(100 * x, 2)

    def summarize(name, per):
        pos = [r["score"] for r in per]
        return {"attack": name, "n": len(per), "n_inserted": sum(r["inserted"] for r in per), "n_negatives": len(neg),
                "tpr_at_alpha": pct(sum(x > score(a.alpha) for x in pos) / max(1, len(pos))),
                "tpr_at_fpr_1": pct(tpr_at_fpr(pos, neg, 0.01)), "tpr_at_fpr_5": pct(tpr_at_fpr(pos, neg, 0.05)),
                "tpr_at_fpr_10": pct(tpr_at_fpr(pos, neg, 0.10)), "auroc": round(auroc(pos, neg), 4),
                "edit_ratio": pct(sum(r["edit"] for r in per) / max(1, len(per))),
                "attack_failures": sum(1 for r in per if r.get("error"))}

    rows, per_out = [], {}
    base = [{"id": r["id"], "inserted": r["inserted"], "score": score(r["p"]) if r["inserted"] else 0.0, "edit": 0.0} for r in items]
    rows.append(summarize("none", base))
    per_out["none"] = base
    print(json.dumps(rows[-1]), flush=True)
    for name in names:
        t0 = time.time()
        per = []
        if name == "llm":
            from attacks.llm_rewrite import atk_llm
            # as in our runs, the attacker rewrites every file of the corpus (incomplete ones too) in file order,
            # 8 per batch; the batch a file is in can change the greedy rewrite
            wm_by_id = {r["id"]: r["wm"] for r in items}
            inputs = []
            for g in rows_all:
                if g["id"] not in wm_by_id and g["code"]:
                    r = embed(g["code"], a.lang, key)
                    wm_by_id[g["id"]] = r["code"] if r["status"] == "ok" else g["code"]
                inputs.append(wm_by_id.get(g["id"], g["code"]))
            out = dict(zip([g["id"] for g in rows_all], atk_llm(inputs, a.lang)))
            attacked = [out[r["id"]] for r in items]
        for i, r in enumerate(items):
            rec = {"id": r["id"], "inserted": r["inserted"], "score": 0.0, "edit": 0.0}
            if r["inserted"]:
                try:
                    ac = attacked[i] if name == "llm" else attack(name, r["wm"], a.lang)
                except Exception as ex:  # the attack failed on this file: empty output, score 0
                    ac, rec["error"] = "", f"{type(ex).__name__}: {ex}"
                rec["edit"] = edit_ratio(r["wm"], ac)
                if ac.strip():
                    d = detect(ac, a.lang, key, calibrate=calib)
                    rec.update(score=score(d["p"]), n_grades=d["n_grades"])
                if name == "llm":
                    rec["attacked_code"] = ac
            per.append(rec)
        rows.append(summarize(name, per))
        per_out[name] = per
        print(json.dumps({**rows[-1], "seconds": round(time.time() - t0, 1)}), flush=True)

    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({"summary": {"lang": a.lang, "generations": a.generations, "alpha": a.alpha, "calibrated": calib,
                                   "attacks": names, "rows": rows},
                       "per_item": per_out}, f, indent=1)


if __name__ == "__main__":
    main()
