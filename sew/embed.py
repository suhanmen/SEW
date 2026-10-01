from . import config
from .keying import target_bit
from .rules.base import apply_edits
from .sites.discovery import discover


def _masked(data, spans):
    out, pos = [], 0
    for s, e in sorted(spans):
        out.append(data[pos:s]); pos = e
    out.append(data[pos:])
    return b"".join(out)


def _masked_new(new, edits):
    out, pos, shift = [], 0, 0
    for e in sorted(edits, key=lambda e: e.start):
        s = e.start + shift
        out.append(new[pos:s]); pos = s + len(e.new); shift += len(e.new) - (e.end - e.start)
    out.append(new[pos:])
    return b"".join(out)


def _plan(recs, data, key):
    edits = []
    for r in recs:
        edits += r.rule.write(r.site, data, target_bit(key, r.sid))
    return edits


def embed(code, language, key: bytes):
    as_str = isinstance(code, str)
    tree, data, recs = discover(code, language)
    result = lambda status, out, **kw: {"status": status, "code": out.decode("utf-8", "replace") if as_str else out, **kw}
    if tree is None:
        return result("abstain:parse", data)
    if not recs:
        return result("abstain:no_sites", data, n_sites=0)
    cur, problem, n_edits, iters = data, None, 0, 0
    for it in range(config.FIXPOINT_MAX_ITER + 1):
        edits = _plan(recs, cur, key)
        if not edits:
            break
        if it == config.FIXPOINT_MAX_ITER:
            problem = "not_converged"; break
        iters += 1
        new = apply_edits(cur, edits)
        tree2, _, recs2 = discover(new, language)
        if tree2 is None:
            problem = "reparse_failed"; break
        if _masked(cur, [(e.start, e.end) for e in edits]) != _masked_new(new, edits):
            problem = "unexpected_diff_outside_footprint"; break
        n_edits += len(edits)
        cur, recs = new, recs2
    if problem:
        return result("abstain:" + problem, data, n_sites=len(recs))
    return result("ok", cur, n_sites=len(recs), n_grades=len({r.sid for r in recs}), n_edits=n_edits, iterations=iters)
