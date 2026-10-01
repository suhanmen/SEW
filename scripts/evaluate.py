import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sew import EXPERIMENT_KEY, detect, embed


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def score(p):
    return -math.log10(max(p, 1e-300))


def auroc(pos, neg):
    allv = sorted([(s, 1) for s in pos] + [(s, 0) for s in neg])
    rank_sum, i = 0.0, 0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        rank_sum += (i + 1 + j) / 2.0 * sum(1 for k in range(i, j) if allv[k][1] == 1)
        i = j
    n1, n0 = len(pos), len(neg)
    return (rank_sum - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def tpr_at_fpr(pos, neg, fpr):
    best = 0.0
    for thr in sorted(set(pos) | set(neg), reverse=True):
        f = sum(s >= thr for s in neg) / len(neg)
        t = sum(s >= thr for s in pos) / len(pos)
        if f <= fpr and t >= best:
            best = t
    return best


def main():
    ap = argparse.ArgumentParser(description="Embed SEW into generated code and measure detection against human-written code")
    ap.add_argument("--lang", required=True, choices=["python", "java", "cpp"])
    ap.add_argument("--generations", required=True, help="JSONL with id, code, complete")
    ap.add_argument("--human", required=True, help="JSONL with id, code (negatives, matched by id)")
    ap.add_argument("--key", help="secret key as hex (default: the key used in the paper's experiments)")
    ap.add_argument("--alpha", type=float, default=0.01)
    ap.add_argument("--uniform-q", action="store_true", help="use q = 1/2 for every rule instead of the fitted q")
    ap.add_argument("--include-incomplete", action="store_true", help="keep generations that hit the token limit")
    ap.add_argument("--limit", type=int, default=0, help="use the first N lines of --generations only")
    ap.add_argument("--out", help="write per-item results and the summary to this JSON file")
    a = ap.parse_args()
    key = bytes.fromhex(a.key) if a.key else EXPERIMENT_KEY
    calib = not a.uniform_q

    gens = read_jsonl(a.generations)
    if a.limit:
        gens = gens[:a.limit]
    gens = [g for g in gens if a.include_incomplete or g.get("complete", True)]
    human = {h["id"]: h["code"] for h in read_jsonl(a.human)}
    t0 = time.time()
    pos_items, neg_items = [], []
    for g in gens:
        row = {"id": g["id"], "inserted": False, "p": 1.0}
        if g["code"]:
            r = embed(g["code"], a.lang, key)
            row["status"] = r["status"]
            if r["status"] == "ok":
                d = detect(r["code"], a.lang, key, calibrate=calib)
                row.update(inserted=True, p=d["p"], n_grades=d["n_grades"], n_edits=r["n_edits"],
                           changed=r["code"] != g["code"])
        else:
            row["status"] = "abstain:no_code"
        pos_items.append(row)
        if g["id"] in human:
            d = detect(human[g["id"]], a.lang, key, calibrate=calib)
            neg_items.append({"id": g["id"], "p": d["p"], "n_grades": d["n_grades"]})

    pos = [score(r["p"]) for r in pos_items]
    pos_ins = [score(r["p"]) for r in pos_items if r["inserted"]]
    neg = [score(r["p"]) for r in neg_items]
    pct = lambda x: round(100 * x, 2)
    both = lambda f: {"all": pct(f(pos)), "inserted_only": pct(f(pos_ins)) if pos_ins else None}
    summary = {
        "lang": a.lang, "generations": a.generations, "n": len(pos_items), "n_inserted": len(pos_ins),
        "n_negatives": len(neg), "alpha": a.alpha, "calibrated": calib,
        "fpr_at_alpha": pct(sum(r["p"] < a.alpha for r in neg_items) / max(1, len(neg))),
        "tpr_at_alpha": both(lambda s: sum(x > score(a.alpha) for x in s) / max(1, len(s))),
        "tpr_at_fpr_1": both(lambda s: tpr_at_fpr(s, neg, 0.01)),
        "tpr_at_fpr_5": both(lambda s: tpr_at_fpr(s, neg, 0.05)),
        "tpr_at_fpr_10": both(lambda s: tpr_at_fpr(s, neg, 0.10)),
        "auroc": {"all": round(auroc(pos, neg), 4), "inserted_only": round(auroc(pos_ins, neg), 4) if pos_ins else None},
        "seconds": round(time.time() - t0, 1),
    }
    print(json.dumps(summary, indent=1))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "positives": pos_items, "negatives": neg_items}, f, indent=1)


if __name__ == "__main__":
    main()
