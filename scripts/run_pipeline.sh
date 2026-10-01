#!/usr/bin/env bash
# End-to-end run: CodeContests download (if missing) -> code generation (GPU) -> SEW embedding + detection -> attacks.
#
#   bash scripts/run_pipeline.sh                                   # everything except the LLM attack
#   ATTACKS=all,llm bash scripts/run_pipeline.sh                   # also the LLM rewriting attack (GPU)
#   STEPS="detect attack" bash scripts/run_pipeline.sh             # reuse the generations already in $OUT/generations
#   LIMIT=10 bash scripts/run_pipeline.sh                          # quick check on the first 10 problems
#
# Variables (default in brackets):
#   STEPS          [generate detect attack]
#   LANGS          [python java cpp]
#   MODELS         [Qwen/Qwen3.5-9B google/gemma-4-12B-it openai/gpt-oss-20b]
#   ATTACK_MODELS  [Qwen/Qwen3.5-9B]  models whose code is attacked (the paper attacks the Qwen3.5-9B code)
#   ATTACKS        [all]            --attacks of evaluate_attacks.py: edit, flip, all, llm, or a comma-separated list
#   OUT            [results]
#   GEN_DIR        [$OUT/generations]
#   DATA_DIR       [data/codecontests]  human solutions; written by scripts/prepare_codecontests.py if missing
#   KEY            []               secret key as hex; empty = the key of the paper's experiments
#   LIMIT          [0]              first N problems only (0 = all)
#   HF_CACHE       []               Hugging Face cache directory for the models (generation and the LLM attack)
#   ERRORPRONE_DIR                  the two Error Prone jars (Java lint attack), see attacks/edit_attacks.py
set -euo pipefail
cd "$(dirname "$0")/.."

STEPS=${STEPS:-"generate detect attack"}
LANGS=${LANGS:-"python java cpp"}
MODELS=${MODELS:-"Qwen/Qwen3.5-9B google/gemma-4-12B-it openai/gpt-oss-20b"}
ATTACK_MODELS=${ATTACK_MODELS:-"Qwen/Qwen3.5-9B"}
ATTACKS=${ATTACKS:-all}
OUT=${OUT:-results}
LIMIT=${LIMIT:-0}
GEN_DIR=${GEN_DIR:-$OUT/generations}
DATA_DIR=${DATA_DIR:-data/codecontests}
export PYTHONHASHSEED=0

KEY_ARG=(); [ -n "${KEY:-}" ] && KEY_ARG=(--key "$KEY")
CACHE_ARG=(); [ -n "${HF_CACHE:-}" ] && CACHE_ARG=(--cache-dir "$HF_CACHE") && export HF_HUB_CACHE="$HF_CACHE"   # also used by the LLM attack
LIMIT_ARG=(); [ "$LIMIT" != 0 ] && LIMIT_ARG=(--limit "$LIMIT")
has_step() { [[ " $STEPS " == *" $1 "* ]]; }
log() { echo "[$(date +%H:%M:%S)] $*"; }

for L in $LANGS; do
  if [ ! -f "$DATA_DIR/$L/human.jsonl" ]; then
    log "prepare   CodeContests human solutions  $L"
    python scripts/prepare_codecontests.py --out-dir "$DATA_DIR" --langs "$L"
  fi
done

if has_step generate; then
  for M in $MODELS; do
    for L in $LANGS; do
      log "generate  $M  $L"
      python scripts/generate.py --model "$M" --lang "$L" --out "$GEN_DIR/$L/${M##*/}.jsonl" "${CACHE_ARG[@]}" "${LIMIT_ARG[@]}"
    done
  done
fi

if has_step detect; then
  for M in $MODELS; do
    for L in $LANGS; do
      log "detect    $M  $L"
      python scripts/evaluate.py --lang "$L" --generations "$GEN_DIR/$L/${M##*/}.jsonl" \
        --human "$DATA_DIR/$L/human.jsonl" --out "$OUT/detect/${L}__${M##*/}.json" \
        "${KEY_ARG[@]}" "${LIMIT_ARG[@]}"
    done
  done
fi

if has_step attack; then
  for M in $ATTACK_MODELS; do
    for L in $LANGS; do
      log "attack    $M  $L  ($ATTACKS)"
      python scripts/evaluate_attacks.py --lang "$L" --generations "$GEN_DIR/$L/${M##*/}.jsonl" \
        --human "$DATA_DIR/$L/human.jsonl" --attacks "$ATTACKS" --out "$OUT/attacks/${L}__${M##*/}.json" \
        "${KEY_ARG[@]}" "${LIMIT_ARG[@]}"
    done
  done
fi

log "done: results in $OUT/"
