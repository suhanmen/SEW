import json
import math
from . import config

_Q = {}


def load_q(language, path=None):
    path = str(path or config.Q_TABLE_PATH)
    if path not in _Q:
        with open(path, encoding="utf-8") as f:
            _Q[path] = json.load(f)
    return _Q[path].get(language, {})


def binom_sf(n, k):
    if n <= 0:
        return 1.0
    return sum(math.comb(n, j) for j in range(k, n + 1)) / 2 ** n


def poibin_sf(probs, k):
    if not probs:
        return 1.0
    dist = [1.0]
    for p in probs:
        nd = [0.0] * (len(dist) + 1)
        for i, v in enumerate(dist):
            nd[i] += v * (1 - p)
            nd[i + 1] += v * p
        dist = nd
    return min(1.0, sum(dist[k:]))
