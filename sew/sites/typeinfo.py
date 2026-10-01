import ast

NUMERIC_TYPES = {"int", "float", "complex", "bool"}
NUMERIC_CALLS = {"len", "abs", "int", "float", "round", "ord", "sum", "min", "max", "pow", "divmod"}
STRICT_NUM_OPS = (ast.Sub, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
NUM_CMP = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _is_num_literal(n):
    return isinstance(n, ast.Constant) and isinstance(n.value, (int, float, complex)) and not isinstance(n.value, bool)


def _ann_numeric(ann):
    if ann is None:
        return False
    if isinstance(ann, ast.Name):
        return ann.id in NUMERIC_TYPES
    if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name) and ann.value.id == "Optional":
        return _ann_numeric(ann.slice)
    return False


def _ann_elem_numeric(ann):
    if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name) \
       and ann.value.id in {"List", "Sequence", "Iterable", "Tuple", "Set", "list", "tuple", "set"}:
        s = ann.slice
        if isinstance(s, ast.Tuple):
            return bool(s.elts) and _ann_numeric(s.elts[0])
        return _ann_numeric(s)
    return False


class NumericInference:

    def __init__(self, func):
        self.func = func
        self.names = set()
        self.numeric_seq = set()
        self._collect_annotations()
        self._fixpoint()

    def is_numeric(self, e):
        if _is_num_literal(e):
            return True
        if isinstance(e, ast.Name):
            return e.id in self.names
        if isinstance(e, ast.UnaryOp) and isinstance(e.op, (ast.USub, ast.UAdd)):
            return self.is_numeric(e.operand)
        if isinstance(e, ast.BinOp):
            if isinstance(e.op, STRICT_NUM_OPS):
                return self.is_numeric(e.left) or self.is_numeric(e.right)
            if isinstance(e.op, (ast.Add, ast.Mult)):
                return self.is_numeric(e.left) and self.is_numeric(e.right)
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id in NUMERIC_CALLS:
            if e.func.id in ("sum", "min", "max"):
                return any(self.is_numeric(a) for a in e.args) or any(
                    isinstance(a, ast.Name) and a.id in self.numeric_seq for a in e.args)
            return True
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) and e.func.attr in ("count", "index", "find"):
            return True
        return False

    def _collect_annotations(self):
        for a in self.func.args.args + self.func.args.kwonlyargs:
            if _ann_numeric(a.annotation):
                self.names.add(a.arg)
            elif _ann_elem_numeric(a.annotation):
                self.numeric_seq.add(a.arg)

    def _targets(self, t):
        if isinstance(t, ast.Name):
            return [t.id]
        if isinstance(t, (ast.Tuple, ast.List)):
            return [n for x in t.elts for n in self._targets(x)]
        return []

    def _fixpoint(self):
        for _ in range(10):
            before = (len(self.names), len(self.numeric_seq))
            for n in ast.walk(self.func):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and n is not self.func:
                    continue
                if isinstance(n, ast.Assign) and self.is_numeric(n.value):
                    for t in n.targets:
                        self.names.update(self._targets(t))
                elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                    if _ann_numeric(n.annotation) or (n.value is not None and self.is_numeric(n.value)):
                        self.names.add(n.target.id)
                    elif _ann_elem_numeric(n.annotation):
                        self.numeric_seq.add(n.target.id)
                elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name) and _is_num_literal(n.value):
                    self.names.add(n.target.id)
                elif isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.ops[0], NUM_CMP):
                    l, r = n.left, n.comparators[0]
                    if isinstance(l, ast.Name) and _is_num_literal(r):
                        self.names.add(l.id)
                    if isinstance(r, ast.Name) and _is_num_literal(l):
                        self.names.add(r.id)
                elif isinstance(n, ast.BinOp) and isinstance(n.op, STRICT_NUM_OPS):
                    for side, other in ((n.left, n.right), (n.right, n.left)):
                        if isinstance(side, ast.Name) and (_is_num_literal(other) or self.is_numeric(other)):
                            self.names.add(side.id)
                elif isinstance(n, ast.For):
                    it = n.iter
                    if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "range":
                        self.names.update(self._targets(n.target))
                    elif isinstance(it, ast.Name) and it.id in self.numeric_seq:
                        self.names.update(self._targets(n.target))
                    elif isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "enumerate":
                        ts = self._targets(n.target)
                        if ts:
                            self.names.add(ts[0])
                        if len(ts) == 2 and it.args and isinstance(it.args[0], ast.Name) and it.args[0].id in self.numeric_seq:
                            self.names.add(ts[1])
            if (len(self.names), len(self.numeric_seq)) == before:
                break


def numeric_names_by_function(source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[(n.name, n.lineno)] = NumericInference(n)
    return out


def inference_for_offset(source, infos, byte_offset):
    line = source[:byte_offset].count(b"\n") + 1
    best = None
    for (name, lineno), inf in infos.items():
        end = getattr(inf.func, "end_lineno", None) or lineno
        if lineno <= line <= end and (best is None or lineno > best[0]):
            best = (lineno, inf)
    return best[1] if best else None
