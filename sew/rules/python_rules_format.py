import re
from .base import Edit, Rule, Site, node_path
from .python_rules import txt, line_indent
from .python_rules_extra import walk, block_stmts
from .python_rules_control import indent_unit

ARITH = {"+", "-", "*", "/", "//", "%", "**", "@", "<<", ">>", "&", "|", "^"}
CMP = {"<", ">", "==", "!=", "<=", ">="}
KEYWORDS = {"if", "elif", "while", "for", "in", "return", "not", "and", "or", "import", "from", "as", "lambda", "yield", "assert", "del",
            "with", "except", "raise", "global", "nonlocal", "def", "class", "is", "await", "async"}
SPACES = re.compile(rb"^[ ]*$")


class _F(Rule):
    language = "python"


def _gap_variant(src, a, b):
    g = src[a:b]
    return len(g) if SPACES.match(g) else None


class F40OperatorSpacing(_F):
    rule_id = "F40"
    variants = ("a + b", "a+b")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type == "binary_operator":
                l, op, r = n.child_by_field_name("left"), n.child_by_field_name("operator"), n.child_by_field_name("right")
                if l is None or op is None or r is None or txt(op) not in ARITH:
                    continue
            elif n.type == "comparison_operator" and len(n.children) == 3 and txt(n.children[1]) in CMP:
                l, op, r = n.children
            elif n.type in ("assignment", "augmented_assignment"):
                l, r = n.child_by_field_name("left"), n.child_by_field_name("right")
                if l is None or r is None or n.child_by_field_name("type") is not None:
                    continue
                ops = [c for c in n.children if not c.is_named and c.start_byte >= l.end_byte and c.end_byte <= r.start_byte]
                if len(ops) != 1:
                    continue
                op = ops[0]
            else:
                continue
            g1, g2 = _gap_variant(src, l.end_byte, op.start_byte), _gap_variant(src, op.end_byte, r.start_byte)
            if g1 is None or g2 is None or (g1, g2) not in ((1, 1), (0, 0)):
                continue
            yield Site(rule_id=self.rule_id, variant=0 if g1 == 1 else 1, span=(l.end_byte, r.start_byte),
                       footprint=((l.end_byte, op.start_byte), (op.end_byte, r.start_byte)), node_path=node_path(n), meta={"op": op})

    def write_bytes(self, site, src, target):
        (a1, b1), (a2, b2) = site.footprint
        sp = b" " if target == 0 else b""
        return [Edit(a1, b1, sp), Edit(a2, b2, sp)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class F41KeywordSpacing(_F):
    rule_id = "F41"
    variants = ("if x", "if  x")

    def match(self, tree, src):
        for n in walk(tree):
            if n.is_named or txt(n) not in KEYWORDS:
                continue
            nxt = n.next_sibling
            if nxt is None or nxt.start_byte <= n.end_byte or b"\n" in src[n.end_byte:nxt.start_byte]:
                continue
            g = _gap_variant(src, n.end_byte, nxt.start_byte)
            if g not in (1, 2):
                continue
            yield Site(rule_id=self.rule_id, variant=0 if g == 1 else 1, span=(n.end_byte, nxt.start_byte), node_path=node_path(n.parent), meta={})

    def write_bytes(self, site, src, target):
        s, e = site.span
        return [Edit(s, e, b" " if target == 0 else b"  ")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


BRACKETED = {"argument_list", "list", "tuple", "dictionary", "set", "parenthesized_expression", "parameters", "subscript"}


class F42ClosingBracket(_F):
    rule_id = "F42"
    variants = ("closing bracket at opening-line indent", "closing bracket one unit deeper")

    def match(self, tree, src):
        u = indent_unit(src)
        for n in walk(tree):
            if n.type not in BRACKETED or b"\n" not in src[n.start_byte:n.end_byte]:
                continue
            close = n.children[-1] if n.children else None
            if close is None or close.is_named or txt(close) not in (")", "]", "}"):
                continue
            ls, ind = line_indent(src, close.start_byte)
            if close.start_byte != ls + len(ind):
                continue
            _, oind = line_indent(src, n.start_byte)
            if ind == oind:
                v = 0
            elif ind == oind + u:
                v = 1
            else:
                continue
            yield Site(rule_id=self.rule_id, variant=v, span=(ls, close.start_byte) if close.start_byte > ls else (ls, ls), node_path=node_path(n), meta={"oind": oind, "u": u, "ls": ls, "cb": close.start_byte})

    def write_bytes(self, site, src, target):
        m = site.meta
        return [Edit(m["ls"], m["cb"], m["oind"] if target == 0 else m["oind"] + m["u"])]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class F43EOFNewline(_F):
    rule_id = "F43"
    variants = ("trailing newline", "no trailing newline")
    sid_mode = "root"

    def match(self, tree, src):
        n = len(src)
        if n == 0 or src.strip() == b"":
            return
        if src.endswith(b"\n\n"):
            return
        if src.endswith(b"\n"):
            yield Site(rule_id=self.rule_id, variant=0, span=(n - 1, n), node_path=("module",), meta={"n": n})
        else:
            yield Site(rule_id=self.rule_id, variant=1, span=(n, n), node_path=("module",), meta={"n": n})

    def write_bytes(self, site, src, target):
        n = site.meta["n"]
        return [Edit(n - 1, n, b"")] if target == 1 else [Edit(n, n, b"\n")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


BLANK_GAP = re.compile(rb"^(\n+)([ \t]*)$")


class F44BlankLinesBeforeDef(_F):
    rule_id = "F44"
    variants = ("PEP8 blank lines before def", "one fewer")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type not in ("function_definition", "class_definition"):
                continue
            d = n.parent if n.parent is not None and n.parent.type == "decorated_definition" else n
            par = d.parent
            if par is None:
                continue
            sibs = block_stmts(par)
            prev = None
            for a, b in zip(sibs, sibs[1:]):
                if b == d:
                    prev = a; break
            if prev is None:
                continue
            m = BLANK_GAP.match(src[prev.end_byte:d.start_byte])
            if not m:
                continue
            blank = len(m.group(1)) - 1
            expected = 2 if par.type == "module" else 1
            if blank == expected:
                v = 0
            elif blank == expected - 1:
                v = 1
            else:
                continue
            yield Site(rule_id=self.rule_id, variant=v, span=(prev.end_byte, d.start_byte), node_path=node_path(d), meta={"expected": expected, "indent": m.group(2)})

    def write_bytes(self, site, src, target):
        s, e = site.span; k = site.meta["expected"] - (1 if target == 1 else 0)
        return [Edit(s, e, b"\n" * (k + 1) + site.meta["indent"])]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


RULES = {r.rule_id: r for r in (F40OperatorSpacing(), F41KeywordSpacing(), F42ClosingBracket(), F43EOFNewline(), F44BlankLinesBeforeDef())}
