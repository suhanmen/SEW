"""Download CodeContests and write the human solutions used as negatives.

    python scripts/prepare_codecontests.py --out-dir data/codecontests

The problems are CodeContests valid + test (deepmind/code_contests on the Hugging Face Hub, about 110 MB).
For each language, scripts/codecontests_selection.json lists the problems we evaluate on and, for each problem,
which of its human solutions we use: one that passes the problem's tests (the test harness is not part of this
repository). Problems are identified as CodeContests/<split>/<row>.

Writes <out-dir>/<lang>/human.jsonl with one {"id", "code"} per line. Requires pyarrow.
"""
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SELECTION = HERE / "codecontests_selection.json"
SPLIT_FILES = {"valid": "data/valid-00000-of-00001-5e672c5751f060d3.parquet",
               "test": "data/test-00000-of-00001-9c49eeff30aacaa8.parquet"}
LANG_CODE = {"python": 3, "cpp": 2, "java": 4}


def load_split(split, cache_dir=None):
    import pyarrow.parquet as pq
    from huggingface_hub import hf_hub_download
    path = hf_hub_download("deepmind/code_contests", SPLIT_FILES[split], repo_type="dataset", cache_dir=cache_dir)
    return pq.read_table(path, columns=["description", "solutions"]).to_pylist()


def load_rows(cache_dir=None):
    return {split: load_split(split, cache_dir) for split in SPLIT_FILES}


def row_of(rows, pid):
    _, split, i = pid.split("/")
    return rows[split][int(i)]


def selection():
    return json.loads(SELECTION.read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser(description="Download CodeContests and write the selected human solutions")
    ap.add_argument("--out-dir", default="data/codecontests")
    ap.add_argument("--langs", default="python,java,cpp")
    ap.add_argument("--cache-dir", help="Hugging Face cache directory for the dataset")
    a = ap.parse_args()
    rows = load_rows(a.cache_dir)
    sel = selection()
    for lang in a.langs.split(","):
        out = Path(a.out_dir) / lang / "human.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            for pid, k in sel[lang]:
                sol = row_of(rows, pid)["solutions"]
                if sol["language"][k] != LANG_CODE[lang]:
                    raise SystemExit(f"{pid}: solution {k} is not {lang}; the dataset on the Hub has changed")
                f.write(json.dumps({"id": pid, "code": sol["solution"][k]}, ensure_ascii=False) + "\n")
        print(f"[prepare] {lang}: {len(sel[lang])} human solutions -> {out}")


if __name__ == "__main__":
    main()
