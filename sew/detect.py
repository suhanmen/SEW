from . import config
from .keying import target_bit
from .rules import FORMAT_RULE_IDS
from .sites.discovery import discover
from .stats import binom_sf, load_q, poibin_sf


def detect(code, language, key: bytes, calibrate=True, alpha=None, q_path=None):
    alpha = config.ALPHA if alpha is None else alpha
    tree, _, recs = discover(code, language)
    if tree is None:
        return {"verdict": "Insufficient evidence", "reason": "parse", "p": 1.0, "n_grades": 0}
    q = load_q(language, q_path) if calibrate else {}
    grades = {}
    for r in recs:
        grades.setdefault(r.sid, []).append(r.value)
    h_all, k_all, h_syn, k_syn = [], 0, [], 0
    for sid, vals in grades.items():
        vote = 1 if sum(vals) * 2 >= len(vals) else 0
        t = target_bit(key, sid)
        qr = q.get(sid[0], 0.5)
        h = qr if t == 1 else 1 - qr
        hit = int(vote == t)
        h_all.append(h); k_all += hit
        if sid[0] not in FORMAT_RULE_IDS:
            h_syn.append(h); k_syn += hit
    sf = (lambda h, k: poibin_sf(h, k)) if calibrate else (lambda h, k: binom_sf(len(h), k))
    p_all = sf(h_all, k_all) if h_all else 1.0
    p_syn = sf(h_syn, k_syn) if h_syn else 1.0
    p = min(1.0, config.N_TESTS * min(p_all, p_syn))
    if len(h_all) < config.MIN_EVIDENCE:
        verdict = "Insufficient evidence"
    else:
        verdict = "Watermark detected" if p < alpha else "No watermark evidence"
    return {"verdict": verdict, "p": p, "p_all": p_all, "p_syntax": p_syn, "n_grades": len(h_all), "k_grades": k_all,
            "n_syntax": len(h_syn), "k_syntax": k_syn, "n_sites": len(recs)}
