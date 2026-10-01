"""Attacks used in the robustness evaluation.

Every attack is a function ``f(code: str, language: str) -> str``. The attacked code is
detected with the same key; nothing from the embedding is reused.

Code-editing attacks (an attacker who does not know the rules):
  format      formatting: Python black; Java/C++ token re-emission with a fixed layout
  lint        default linting with automatic fixes: Python ruff, Java Error Prone, C++ clang-tidy
  comments    comment removal
  rename      renaming of parameters and local variables to v0, v1, ...
  llm         LLM rewriting (see llm_rewrite.py; needs a GPU)

Rule-aware attack (an attacker who knows the rule set but not the key):
  flipNN      each style site is selected with probability NN/100 and set to a random variant
  flip100     every style site is inverted
"""
from .edit_attacks import atk_format, atk_lint, atk_strip_comments, atk_rename
from .flip import atk_flip

ATTACKS = {
    "format": atk_format,
    "lint": atk_lint,
    "comments": atk_strip_comments,
    "rename": atk_rename,
    "flip100": lambda code, language: atk_flip(code, 1.0, language=language),
}
for _q in range(1, 100):
    ATTACKS[f"flip{_q:02d}"] = (lambda q: lambda code, language: atk_flip(code, q / 100, language=language))(_q)

EDIT_ATTACKS = ["format", "lint", "comments", "rename"]
FLIP_ATTACKS = ["flip25", "flip50", "flip75", "flip100"]


def attack(name, code, language):
    if name == "llm":
        from .llm_rewrite import atk_llm
        return atk_llm([code], language)[0]
    return ATTACKS[name](code, language)


__all__ = ["ATTACKS", "EDIT_ATTACKS", "FLIP_ATTACKS", "attack", "atk_format", "atk_lint",
           "atk_strip_comments", "atk_rename", "atk_flip"]
