import argparse
import json
import sys
from pathlib import Path

from sew import EXPERIMENT_KEY, detect, embed

EXT = {".py": "python", ".java": "java", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".h": "cpp"}


def main():
    ap = argparse.ArgumentParser(description="SEW: post-hoc code style watermarking")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("embed", "detect"):
        p = sub.add_parser(name)
        p.add_argument("path")
        p.add_argument("--lang", choices=["python", "java", "cpp"])
        p.add_argument("--key", help="secret key as hex (default: the key used in the paper's experiments)")
    sub.choices["embed"].add_argument("-o", "--output")
    sub.choices["detect"].add_argument("--uniform-q", action="store_true", help="use q = 1/2 for every rule instead of the fitted q")
    a = ap.parse_args()
    lang = a.lang or EXT.get(Path(a.path).suffix)
    if lang is None:
        ap.error("cannot infer the language; pass --lang")
    key = bytes.fromhex(a.key) if a.key else EXPERIMENT_KEY
    code = Path(a.path).read_bytes()
    if a.cmd == "embed":
        r = embed(code, lang, key)
        if a.output:
            Path(a.output).write_bytes(r["code"])
        else:
            sys.stdout.write(r["code"].decode("utf-8", "replace"))
        print(json.dumps({k: v for k, v in r.items() if k != "code"}), file=sys.stderr)
    else:
        print(json.dumps(detect(code, lang, key, calibrate=not a.uniform_q), indent=1))


if __name__ == "__main__":
    main()
