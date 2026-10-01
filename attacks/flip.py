"""Rule-aware style flipping.

The attacker knows the rule set (and therefore finds the same style sites that SEW uses) but
not the key. Each site is selected with probability ``q``; a selected site is rewritten to a
uniformly random variant, so on average about half of the selected sites actually change.
With ``q >= 1`` every site is inverted.

The random stream is seeded from ``hash(code)``. Python randomises ``hash`` for strings per
process, so run with ``PYTHONHASHSEED=0`` to reproduce the reported numbers.
"""
import random

from sew.rules.base import apply_edits
from sew.sites.discovery import discover


def atk_flip(code, q, seed=0, language="python", rules=None):
    rnd = random.Random(hash(code) & 0xFFFFFFFF ^ seed)
    tree, data, recs = discover(code.encode(), language)
    if tree is None:
        return code
    edits = []
    for r in recs:
        if rules is not None and r.rule.rule_id not in rules:
            continue
        if q >= 1.0 or rnd.random() < q:
            edits += r.rule.write(r.site, data, 1 - r.value if q >= 1.0 else rnd.randint(0, 1))
    return apply_edits(data, edits).decode()
