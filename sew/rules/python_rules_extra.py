from .base import Edit, Rule, Site, named_children, node_path
from .python_rules import NUMERIC_LITERAL, is_pure, line_indent, txt

IMMUTABLE_LITERAL = {"integer", "float", "string", "true", "false", "none"}
ATOMIC = {"identifier", "attribute", "subscript", "call", "parenthesized_expression",
          "integer", "float", "string", "list", "tuple", "dictionary", "set"}

DEFINING_PARENTS = {"assignment", "augmented_assignment", "function_definition", "class_definition",
                    "parameters", "default_parameter", "typed_parameter", "typed_default_parameter",
                    "for_statement", "aliased_import", "dotted_name", "as_pattern_target",
                    "pattern_list", "tuple_pattern", "list_pattern", "global_statement",
                    "nonlocal_statement", "lambda_parameters", "named_expression", "with_item",
                    "except_clause", "list_splat_pattern", "keyword_argument"}


def shadowed_names(tree):
    out = set()
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        stack.extend(n.children)
        if n.type != "identifier" or n.parent is None:
            continue
        p = n.parent
        if p.type in ("assignment", "augmented_assignment", "named_expression"):
            if p.child_by_field_name("left") == n:
                out.add(txt(n))
        elif p.type in ("function_definition", "class_definition"):
            if p.child_by_field_name("name") == n:
                out.add(txt(n))
        elif p.type == "for_statement":
            if p.child_by_field_name("left") == n:
                out.add(txt(n))
        elif p.type == "keyword_argument":
            pass
        elif p.type == "dotted_name":
            if p.parent is not None and p.parent.type in ("import_statement", "import_from_statement"):
                out.add(txt(n).split(".")[0])
        elif p.type in DEFINING_PARENTS:
            out.add(txt(n))
    return out


def identifiers_in(node):
    out, stack = set(), [node]
    while stack:
        n = stack.pop()
        stack.extend(n.children)
        if n.type == "identifier":
            out.add(txt(n))
    return out


def walk(tree):
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        stack.extend(n.children)
        yield n


def is_call_to(n, name):
    return (n.type == "call" and n.child_by_field_name("function") is not None
            and n.child_by_field_name("function").type == "identifier"
            and txt(n.child_by_field_name("function")) == name)


def positional_args(call):
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    kids = named_children(args)
    if any(k.type in ("keyword_argument", "list_splat", "dictionary_splat") for k in kids):
        return None
    return kids


def block_stmts(block):
    return [c for c in named_children(block) if c.type != "comment"]


def next_sibling_stmt(stmt):
    if stmt.parent is None:
        return None
    sibs = block_stmts(stmt.parent)
    for a, b in zip(sibs, sibs[1:]):
        if a == stmt:
            return b
    return None


def separator_ok(src, a, b):
    gap = src[a.end_byte:b.start_byte]
    _, indent = line_indent(src, b.start_byte)
    return gap in (b"\n" + indent, b"\r\n" + indent)


class _Base(Rule):
    language = "python"

    def _shadow(self, tree, *names):
        sh = shadowed_names(tree)
        return any(n in sh for n in names)


_NAMEISH = {"identifier", "attribute", "subscript"}


class E2Commutative(_Base):
    rule_id = "E2"
    variants = ("x + 1", "1 + x")
    OPS = {"+", "*", "==", "!="}
    HIGHER = {"+": {"*", "/", "//", "%", "@", "**"}, "*": {"**"},
              "==": {"+", "-", "*", "/", "//", "%", "@", "**", "<<", ">>", "&", "^", "|"},
              "!=": {"+", "-", "*", "/", "//", "%", "@", "**", "<<", ">>", "&", "^", "|"}}

    def match(self, tree, src):
        for n in walk(tree):
            if n.type not in ("binary_operator", "comparison_operator"):
                continue
            kids = n.children
            if len(kids) != 3 or kids[1].is_named or txt(kids[1]) not in self.OPS:
                continue
            left, op, right = kids
            l_lit, r_lit = left.type in NUMERIC_LITERAL, right.type in NUMERIC_LITERAL
            if l_lit == r_lit:
                if l_lit or left.type not in _NAMEISH or right.type not in _NAMEISH or txt(left) == txt(right):
                    continue
                if txt(op) == "+":
                    if left.type != "identifier" or right.type != "identifier":
                        continue
                    from .python_rules import _numeric_inference
                    inf = _numeric_inference(src, n.start_byte)
                    if inf is None or txt(left) not in inf.names or txt(right) not in inf.names:
                        continue
                v = 0 if txt(left) < txt(right) else 1
                yield Site(rule_id=self.rule_id, variant=v,
                           span=(n.start_byte, n.end_byte), node_path=node_path(n),
                           meta={"left": left, "op": op, "right": right, "names": True})
                continue
            yield Site(rule_id=self.rule_id, variant=1 if l_lit else 0,
                       span=(n.start_byte, n.end_byte), node_path=node_path(n),
                       meta={"left": left, "op": op, "right": right})

    def guard(self, site, tree, src):
        if site.meta.get("names"):
            return site.meta["left"].type != "call" and site.meta["right"].type != "call"
        other = site.meta["right"] if site.variant == 1 else site.meta["left"]
        if other.type in ATOMIC or other.type == "unary_operator":
            return True
        if other.type == "binary_operator" and len(other.children) == 3:
            return txt(other.children[1]) in self.HIGHER[txt(site.meta["op"])]
        return False

    def write_bytes(self, site, src, target):
        left, op, right = site.meta["left"], site.meta["op"], site.meta["right"]
        sp1 = src[left.end_byte:op.start_byte]
        sp2 = src[op.end_byte:right.start_byte]
        new = src[right.start_byte:right.end_byte] + sp1 + src[op.start_byte:op.end_byte] + sp2 + src[left.start_byte:left.end_byte]
        return [Edit(site.span[0], site.span[1], new)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class E4InTupleList(_Base):
    rule_id = "E4"
    variants = ("x in (a, b)", "x in [a, b]")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "comparison_operator":
                continue
            ops = [c for c in n.children if not c.is_named]
            if not ops or txt(ops[0]) not in ("in", "not"):
                continue
            kids = named_children(n)
            if len(kids) != 2 or kids[1].type not in ("tuple", "list"):
                continue
            coll = kids[1]
            if not named_children(coll):
                continue
            yield Site(rule_id=self.rule_id, variant=0 if coll.type == "tuple" else 1,
                       span=(coll.start_byte, coll.end_byte), node_path=node_path(coll),
                       meta={"coll": coll})

    def guard(self, site, tree, src):
        return all(is_pure(e) for e in named_children(site.meta["coll"]))

    def write_bytes(self, site, src, target):
        s, e = site.span
        o, c = (b"[", b"]") if target == 1 else (b"(", b")")
        return [Edit(s, s + 1, o), Edit(e - 1, e, c)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class E5RedundantParens(_Base):
    rule_id = "E5"
    variants = ("a + b * c", "a + (b * c)")
    HIGHER = {("+", "*"), ("+", "/"), ("+", "//"), ("+", "%"), ("+", "@"),
              ("-", "*"), ("-", "/"), ("-", "//"), ("-", "%"), ("-", "@"),
              ("*", "**"), ("/", "**"), ("//", "**"), ("%", "**")}

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "binary_operator":
                continue
            kids = n.children
            if len(kids) != 3:
                continue
            pop = txt(kids[1])
            for child in (kids[0], kids[2]):
                inner, wrapped = child, False
                if child.type == "parenthesized_expression":
                    k = named_children(child)
                    if len(k) != 1:
                        continue
                    inner, wrapped = k[0], True
                if inner.type != "binary_operator" or len(inner.children) != 3:
                    continue
                if (pop, txt(inner.children[1])) not in self.HIGHER:
                    continue
                yield Site(rule_id=self.rule_id, variant=1 if wrapped else 0,
                           span=(child.start_byte, child.end_byte), node_path=node_path(child),
                           meta={"inner": inner})

    def write_bytes(self, site, src, target):
        s, e = site.span
        if target == 1:
            return [Edit(s, e, b"(" + src[s:e] + b")")]
        inner = site.meta["inner"]
        return [Edit(s, e, src[inner.start_byte:inner.end_byte])]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class E7DefaultArg(_Base):
    rule_id = "E7"
    variants = ("range(n)", "range(0, n)")
    SPEC = {"range": ("first", b"0"), "enumerate": ("second", b"0"), "sum": ("second", b"0")}

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "call":
                continue
            fn = n.child_by_field_name("function")
            if fn is None or fn.type != "identifier" or txt(fn) not in self.SPEC:
                continue
            args = positional_args(n)
            if args is None:
                continue
            pos, default = self.SPEC[txt(fn)]
            name = txt(fn)
            if len(args) == 1:
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"name": name, "args": args, "pos": pos, "default": default})
            elif len(args) == 2:
                lit = args[0] if pos == "first" else args[1]
                if lit.type == "integer" and src[lit.start_byte:lit.end_byte] == default:
                    yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, n.end_byte),
                               node_path=node_path(n), meta={"name": name, "args": args, "pos": pos, "default": default})

    def guard(self, site, tree, src):
        return not self._shadow(tree, site.meta["name"])

    def write_bytes(self, site, src, target):
        args, pos, default = site.meta["args"], site.meta["pos"], site.meta["default"]
        if target == 1:
            a = args[0]
            if pos == "first":
                return [Edit(a.start_byte, a.start_byte, default + b", ")]
            return [Edit(a.end_byte, a.end_byte, b", " + default)]
        if pos == "first":
            return [Edit(args[0].start_byte, args[1].start_byte, b"")]
        return [Edit(args[0].end_byte, args[1].end_byte, b"")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class E9SliceLower(_Base):
    rule_id = "E9"
    variants = ("x[:n]", "x[0:n]")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "slice":
                continue
            kids = n.children
            colons = [c for c in kids if not c.is_named and c.type == ":"]
            if len(colons) != 1:
                continue
            colon = colons[0]
            before = [c for c in kids if c.is_named and c.end_byte <= colon.start_byte]
            after = [c for c in kids if c.is_named and c.start_byte >= colon.end_byte]
            if len(after) != 1:
                continue
            if not before:
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"colon": colon, "lower": None})
            elif len(before) == 1 and before[0].type == "integer" and txt(before[0]) == "0":
                yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"colon": colon, "lower": before[0]})

    def write_bytes(self, site, src, target):
        lower = site.meta["lower"]
        if target == 1:
            return [Edit(site.span[0], site.span[0], b"0")]
        return [Edit(lower.start_byte, lower.end_byte, b"")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class E10LenZero(_Base):
    rule_id = "E10"
    variants = ("len(x) > 0", "len(x) != 0")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "comparison_operator" or len(n.children) != 3:
                continue
            left, op, right = n.children
            if txt(op) not in (">", "!=") or not is_call_to(left, "len"):
                continue
            if right.type != "integer" or txt(right) != "0":
                continue
            yield Site(rule_id=self.rule_id, variant=0 if txt(op) == ">" else 1,
                       span=(n.start_byte, n.end_byte), footprint=((op.start_byte, op.end_byte),),
                       node_path=node_path(n), meta={"op": op})

    def guard(self, site, tree, src):
        return not self._shadow(tree, "len")

    def write_bytes(self, site, src, target):
        op = site.meta["op"]
        return [Edit(op.start_byte, op.end_byte, b"!=" if target == 1 else b">")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class E11Pow(_Base):
    rule_id = "E11"
    variants = ("x ** y", "pow(x, y)")
    SAFE_PARENTS = {"assignment", "augmented_assignment", "return_statement", "expression_statement",
                    "argument_list", "keyword_argument", "parenthesized_expression", "list", "tuple",
                    "set", "dictionary", "pair", "slice", "comparison_operator", "boolean_operator",
                    "conditional_expression", "if_statement", "while_statement", "expression_list",
                    "for_statement", "lambda", "unary_operator", "not_operator", "list_comprehension",
                    "generator_expression", "for_in_clause", "if_clause", "yield", "await"}

    def match(self, tree, src):
        for n in walk(tree):
            if n.type == "binary_operator" and len(n.children) == 3 and txt(n.children[1]) == "**":
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"l": n.children[0], "r": n.children[2], "node": n})
            elif is_call_to(n, "pow"):
                args = positional_args(n)
                if args is not None and len(args) == 2:
                    yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, n.end_byte),
                               node_path=node_path(n), meta={"l": args[0], "r": args[1], "node": n})

    def guard(self, site, tree, src):
        if self._shadow(tree, "pow"):
            return False
        p = site.meta["node"].parent
        if p is None:
            return False
        if p.type == "binary_operator":
            return txt(p.children[1]) != "**" if len(p.children) == 3 else False
        if p.type == "subscript":
            return p.child_by_field_name("value") != site.meta["node"]
        return p.type in self.SAFE_PARENTS

    def write_bytes(self, site, src, target):
        l, r = site.meta["l"], site.meta["r"]
        L, R = src[l.start_byte:l.end_byte], src[r.start_byte:r.end_byte]
        if target == 1:
            return [Edit(site.span[0], site.span[1], b"pow(" + L + b", " + R + b")")]
        def wrap(node, text):
            return text if node.type in ATOMIC else b"(" + text + b")"
        simple = {"identifier", "integer", "float", "attribute"}
        sep = b"**" if (l.type in simple and r.type in simple) else b" ** "
        return [Edit(site.span[0], site.span[1], wrap(l, L) + sep + wrap(r, R))]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class E12ReversedRange(_Base):
    rule_id = "E12"
    variants = ("reversed(range(n))", "range(n - 1, -1, -1)")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "for_statement":
                continue
            it = n.child_by_field_name("right")
            if it is None or it.type != "call":
                continue
            if is_call_to(it, "reversed"):
                a = positional_args(it)
                if a and len(a) == 1 and is_call_to(a[0], "range"):
                    ra = positional_args(a[0])
                    if ra and len(ra) == 1:
                        yield Site(rule_id=self.rule_id, variant=0, span=(it.start_byte, it.end_byte),
                                   node_path=node_path(it), meta={"n": ra[0]})
            elif is_call_to(it, "range"):
                a = positional_args(it)
                if a and len(a) == 3 and self._is_minus1(a[1]) and self._is_minus1(a[2]) \
                   and a[0].type == "binary_operator" and len(a[0].children) == 3 \
                   and txt(a[0].children[1]) == "-" and txt(a[0].children[2]) == "1":
                    yield Site(rule_id=self.rule_id, variant=1, span=(it.start_byte, it.end_byte),
                               node_path=node_path(it), meta={"n": a[0].children[0]})

    @staticmethod
    def _is_minus1(node):
        return node.type == "unary_operator" and txt(node) == "-1"

    def guard(self, site, tree, src):
        return is_pure(site.meta["n"]) and not self._shadow(tree, "range", "reversed")

    def write_bytes(self, site, src, target):
        n = site.meta["n"]
        N = src[n.start_byte:n.end_byte]
        if target == 1:
            return [Edit(site.span[0], site.span[1], b"range(" + N + b" - 1, -1, -1)")]
        return [Edit(site.span[0], site.span[1], b"reversed(range(" + N + b"))")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class I2DigitGrouping(_Base):
    rule_id = "I2"
    variants = ("1000000", "1_000_000")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "integer":
                continue
            t = txt(n)
            if t[:2].lower() in ("0x", "0o", "0b") or not t.replace("_", "").isdigit():
                continue
            digits = t.replace("_", "")
            if len(digits) < 4:
                continue
            if "_" in t:
                if t != self._group(digits):
                    continue
                yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"digits": digits})
            else:
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"digits": digits})

    @staticmethod
    def _group(digits):
        out, rem = [], digits
        while len(rem) > 3:
            out.insert(0, rem[-3:]); rem = rem[:-3]
        out.insert(0, rem)
        return "_".join(out)

    def write_bytes(self, site, src, target):
        d = site.meta["digits"]
        return [Edit(site.span[0], site.span[1], (self._group(d) if target == 1 else d).encode())]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class L1EmptyCollection(_Base):
    rule_id = "L1"
    variants = ("[]", "list()")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type == "list" and not named_children(n):
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"kind": "list"})
            elif n.type == "dictionary" and not named_children(n):
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"kind": "dict"})
            elif n.type == "call":
                fn = n.child_by_field_name("function")
                args = n.child_by_field_name("arguments")
                if fn is not None and fn.type == "identifier" and txt(fn) in ("list", "dict") \
                   and args is not None and not named_children(args):
                    yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, n.end_byte),
                               node_path=node_path(n), meta={"kind": txt(fn)})

    def guard(self, site, tree, src):
        return not self._shadow(tree, site.meta["kind"])

    def write_bytes(self, site, src, target):
        k = site.meta["kind"]
        new = (b"list()" if k == "list" else b"dict()") if target == 1 else (b"[]" if k == "list" else b"{}")
        return [Edit(site.span[0], site.span[1], new)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class S2TupleAssign(_Base):
    rule_id = "S2"
    variants = ("a, b = x, y", "a = x; b = y")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "expression_statement":
                continue
            k = named_children(n)
            if len(k) != 1 or k[0].type != "assignment":
                continue
            a = k[0]
            left, right = a.child_by_field_name("left"), a.child_by_field_name("right")
            if left is None or right is None:
                continue
            if left.type == "pattern_list" and right.type == "expression_list":
                ls, rs = named_children(left), named_children(right)
                if len(ls) == 2 and len(rs) == 2 and all(x.type == "identifier" for x in ls):
                    yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte),
                               node_path=node_path(n), meta={"ls": ls, "rs": rs, "eq_gap": src[left.end_byte:right.start_byte],
                                                              "stmts": (n,)})
            elif left.type == "identifier":
                m = next_sibling_stmt(n)
                if m is None or m.type != "expression_statement":
                    continue
                k2 = named_children(m)
                if len(k2) != 1 or k2[0].type != "assignment":
                    continue
                b = k2[0]
                l2, r2 = b.child_by_field_name("left"), b.child_by_field_name("right")
                if l2 is None or r2 is None or l2.type != "identifier":
                    continue
                if right.type in ("expression_list", "pattern_list") or r2.type in ("expression_list", "pattern_list"):
                    continue
                if a.child_by_field_name("type") is not None or b.child_by_field_name("type") is not None:
                    continue
                if right.type == "assignment" or r2.type == "assignment":
                    continue
                if not separator_ok(src, n, m):
                    continue
                yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, m.end_byte),
                           node_path=node_path(n), meta={"ls": [left, l2], "rs": [right, r2],
                                                          "eq_gap": src[left.end_byte:right.start_byte],
                                                          "stmts": (n, m)})

    def guard(self, site, tree, src):
        ls, rs = site.meta["ls"], site.meta["rs"]
        names = {txt(x) for x in ls}
        if len(names) != 2:
            return False
        n0 = site.meta["stmts"][0]
        if src[src.rfind(b"\n", 0, n0.start_byte) + 1:n0.start_byte].strip() != b"":
            return False
        if all(r.type in IMMUTABLE_LITERAL for r in rs) and \
           src[rs[0].start_byte:rs[0].end_byte] == src[rs[1].start_byte:rs[1].end_byte]:
            return False
        refs = set()
        for r in rs:
            refs |= identifiers_in(r)
        return not (names & refs)

    def write_bytes(self, site, src, target):
        ls, rs, gap = site.meta["ls"], site.meta["rs"], site.meta["eq_gap"]
        L = [src[x.start_byte:x.end_byte] for x in ls]
        R = [src[x.start_byte:x.end_byte] for x in rs]
        if target == 1:
            _, indent = line_indent(src, site.span[0])
            new = L[0] + gap + R[0] + b"\n" + indent + L[1] + gap + R[1]
        else:
            new = L[0] + b", " + L[1] + gap + R[0] + b", " + R[1]
        return [Edit(site.span[0], site.span[1], new)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 1) if site.variant == 0 else []


class S3ChainAssign(_Base):
    rule_id = "S3"
    variants = ("x = y = 0", "x = 0; y = 0")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "expression_statement":
                continue
            k = named_children(n)
            if len(k) != 1 or k[0].type != "assignment":
                continue
            a = k[0]
            l1, r1 = a.child_by_field_name("left"), a.child_by_field_name("right")
            if l1 is None or r1 is None or l1.type != "identifier":
                continue
            if a.child_by_field_name("type") is not None:
                continue
            eq1 = next(c for c in a.children if not c.is_named and c.type == "=")
            if r1.type == "assignment":
                l2, r2 = r1.child_by_field_name("left"), r1.child_by_field_name("right")
                if l2 is None or r2 is None or l2.type != "identifier" or r2.type == "assignment":
                    continue
                eq2 = next(c for c in r1.children if not c.is_named and c.type == "=")
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"l": [l1, l2], "eq": [eq1, eq2], "lit": r2,
                                                          "gaps": [(src[l1.end_byte:eq1.start_byte], src[eq1.end_byte:l2.start_byte]),
                                                                   (src[l2.end_byte:eq2.start_byte], src[eq2.end_byte:r2.start_byte])]})
            elif r1.type in IMMUTABLE_LITERAL:
                m = next_sibling_stmt(n)
                if m is None or m.type != "expression_statement":
                    continue
                k2 = named_children(m)
                if len(k2) != 1 or k2[0].type != "assignment":
                    continue
                b = k2[0]
                l2, r2 = b.child_by_field_name("left"), b.child_by_field_name("right")
                if l2 is None or r2 is None or l2.type != "identifier" or r2.type != r1.type:
                    continue
                if b.child_by_field_name("type") is not None:
                    continue
                if src[r2.start_byte:r2.end_byte] != src[r1.start_byte:r1.end_byte]:
                    continue
                if not separator_ok(src, n, m):
                    continue
                eq2 = next(c for c in b.children if not c.is_named and c.type == "=")
                yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, m.end_byte),
                           node_path=node_path(n), meta={"l": [l1, l2], "eq": [eq1, eq2], "lit": r1,
                                                          "gaps": [(src[l1.end_byte:eq1.start_byte], src[eq1.end_byte:r1.start_byte]),
                                                                   (src[l2.end_byte:eq2.start_byte], src[eq2.end_byte:r2.start_byte])]})

    def guard(self, site, tree, src):
        lit = site.meta["lit"]
        l = site.meta["l"]
        return lit.type in IMMUTABLE_LITERAL and txt(l[0]) != txt(l[1])

    def write_bytes(self, site, src, target):
        l, lit, gaps = site.meta["l"], site.meta["lit"], site.meta["gaps"]
        L = [src[x.start_byte:x.end_byte] for x in l]
        V = src[lit.start_byte:lit.end_byte]
        (a1, b1), (a2, b2) = gaps
        if target == 1:
            _, indent = line_indent(src, site.span[0])
            new = L[0] + a1 + b"=" + b1 + V + b"\n" + indent + L[1] + a2 + b"=" + b2 + V
        else:
            new = L[0] + a1 + b"=" + b1 + L[1] + a2 + b"=" + b2 + V
        return [Edit(site.span[0], site.span[1], new)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 1) if site.variant == 0 else []


class S4ReturnTuple(_Base):
    rule_id = "S4"
    variants = ("return a, b", "return (a, b)")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "return_statement":
                continue
            k = named_children(n)
            if len(k) != 1:
                continue
            v = k[0]
            if v.type == "expression_list":
                yield Site(rule_id=self.rule_id, variant=0, span=(v.start_byte, v.end_byte), node_path=node_path(v))
            elif v.type == "tuple" and named_children(v):
                yield Site(rule_id=self.rule_id, variant=1, span=(v.start_byte, v.end_byte), node_path=node_path(v))

    def write_bytes(self, site, src, target):
        s, e = site.span
        if target == 1:
            return [Edit(s, e, b"(" + src[s:e] + b")")]
        return [Edit(s, e, src[s + 1:e - 1])]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class S5BareReturn(_Base):
    rule_id = "S5"
    variants = ("return", "return None")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "return_statement":
                continue
            k = named_children(n)
            if not k:
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"node": n})
            elif len(k) == 1 and k[0].type == "none":
                yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"node": n})

    def guard(self, site, tree, src):
        n = site.meta["node"]
        fn = n.parent
        while fn is not None and fn.type != "function_definition":
            fn = fn.parent
        if fn is None:
            return False
        body = fn.child_by_field_name("body")
        if body is None or (block_stmts(body) and block_stmts(body)[-1] == n):
            return False
        stack = list(named_children(body))
        while stack:
            m = stack.pop()
            if m.type in ("function_definition", "class_definition", "lambda"):
                continue
            if m.type == "return_statement" and m != n:
                k = named_children(m)
                if k and not (len(k) == 1 and k[0].type == "none"):
                    return False
            stack.extend(m.children)
        return True

    def write_bytes(self, site, src, target):
        s, e = site.span
        if target == 1:
            return [Edit(e, e, b" None")]
        return [Edit(s + len(b"return"), e, b"")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class S6EllipsisPass(_Base):
    rule_id = "S6"
    variants = ("...", "pass")
    sid_mode = "parent"

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "block":
                continue
            st = block_stmts(n)
            if len(st) != 1:
                continue
            s = st[0]
            if s.type == "pass_statement":
                yield Site(rule_id=self.rule_id, variant=1, span=(s.start_byte, s.end_byte), node_path=node_path(s))
            elif s.type == "expression_statement":
                k = named_children(s)
                if len(k) == 1 and k[0].type == "ellipsis":
                    yield Site(rule_id=self.rule_id, variant=0, span=(s.start_byte, s.end_byte), node_path=node_path(s))

    def write_bytes(self, site, src, target):
        return [Edit(site.span[0], site.span[1], b"pass" if target == 1 else b"...")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class S7WhileTrue(_Base):
    rule_id = "S7"
    variants = ("while True:", "while 1:")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "while_statement":
                continue
            c = n.child_by_field_name("condition")
            if c is None:
                continue
            if c.type == "true":
                yield Site(rule_id=self.rule_id, variant=0, span=(c.start_byte, c.end_byte), node_path=node_path(c))
            elif c.type == "integer" and txt(c) == "1":
                yield Site(rule_id=self.rule_id, variant=1, span=(c.start_byte, c.end_byte), node_path=node_path(c))

    def write_bytes(self, site, src, target):
        return [Edit(site.span[0], site.span[1], b"1" if target == 1 else b"True")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


RULES = {r.rule_id: r() for r in (
    E2Commutative, E4InTupleList, E5RedundantParens, E7DefaultArg, E9SliceLower, E10LenZero,
    E11Pow, E12ReversedRange, I2DigitGrouping, L1EmptyCollection, S2TupleAssign, S3ChainAssign,
    S4ReturnTuple, S5BareReturn, S6EllipsisPass, S7WhileTrue)}
