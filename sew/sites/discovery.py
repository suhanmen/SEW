from dataclasses import dataclass
from ..rules import active_rules
from ..rules.base import apply_edits, parse
from ..rules.python_rules_idiom import RULES as _IDIOM_RULES
from .siteid import site_id
from .ctx import context


@dataclass
class SiteRec:
    rule: object
    site: object
    sid: tuple
    ordinal: int = 0

    @property
    def rule_id(self): return self.rule.rule_id
    @property
    def span(self): return self.site.span
    @property
    def value(self): return self.site.variant


_TIE = {"E5": 0, "T2": 0, "SC14": 0}


def _overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


def discover(src, language):
    data = src if isinstance(src, bytes) else src.encode()
    tree, data = parse(data, language)
    if tree is None:
        return None, data, []
    cands = []
    for rule in active_rules(language).values():
        for s in rule.match(tree, data):
            if rule.guard(s, tree, data):
                cands.append((rule, s))
    cands.sort(key=lambda rs: (-(rs[1].span[1] - rs[1].span[0]), rs[1].span[0], _TIE.get(rs[0].rule_id, 1), rs[0].rule_id))
    kept = []
    for rule, s in cands:
        if any(_overlap(s.span, k[1].span) for k in kept):
            continue
        kept.append((rule, s))
    kept.sort(key=lambda rs: (rs[1].span[0], rs[1].span[1], rs[0].rule_id))
    ctx_tree = tree
    if language == "python":
        er = [e for r, s in kept if r.rule_id in _IDIOM_RULES for e in r.erase(s, data)]
        if er:
            sk_tree, _ = parse(apply_edits(data, er), language)
            if sk_tree is not None:
                ctx_tree = sk_tree
    ctx = context(ctx_tree, language)
    recs = [SiteRec(rule=r, site=s, sid=site_id(tree, r.rule_id, s.span, r.sid_mode, language) + ctx, ordinal=i)
            for i, (r, s) in enumerate(kept)]
    return tree, data, recs
