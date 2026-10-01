import re

from .base import Edit, Rule, Site, node_path
from .c_family_rules import block_stmts, txt, walk

SPACES = re.compile(rb"^[ ]*\Z")
BLANK_GAP = re.compile(rb"^(\n+)([ \t]*)\Z")

OPCHARS = set(b"+-*/%&|^<>=!~:.?")

BIN_OPS = {"+", "-", "*", "/", "%", "<<", ">>", ">>>",
           "<", ">", "<=", ">=", "==", "!=", "&", "|", "^", "&&", "||"}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "<<=", ">>=", ">>>=", "&=", "|=", "^="}

KEYWORDS = {"if", "else", "for", "while", "do", "switch", "case", "catch", "try", "return",
            "throw", "new", "delete", "synchronized"}

BRACKETED = {
    "java": {"argument_list", "formal_parameters", "array_initializer", "parenthesized_expression"},
    "cpp": {"argument_list", "parameter_list", "initializer_list", "parenthesized_expression"},
}

DECLS = {
    "java": {"method_declaration", "constructor_declaration", "class_declaration",
             "interface_declaration", "enum_declaration", "record_declaration"},
    "cpp": {"function_definition", "class_specifier", "struct_specifier", "template_declaration"},
}
CONTAINERS = {
    "java": {"class_body", "interface_body", "enum_body", "program", "block"},
    "cpp": {"field_declaration_list", "declaration_list", "translation_unit"},
}
BAD_PREV = {"access_specifier", "labeled_statement", "label_statement"}


class _CF(Rule):

    def __init__(self, language):
        self.language = language


def _gap_variant(src, a, b):
    if b < a:
        return None
    g = src[a:b]
    return len(g) if SPACES.match(g) else None


def _in_preproc(node) -> bool:
    n = node
    while n is not None:
        if n.type.startswith("preproc"):
            return True
        n = n.parent
    return False


def indent_unit(src: bytes) -> bytes:
    return b"\t" if b"\n\t" in src else b"    "


def line_indent(src: bytes, offset: int):
    ls = src.rfind(b"\n", 0, offset) + 1
    i = ls
    while i < len(src) and src[i:i + 1] in (b" ", b"\t"):
        i += 1
    return ls, src[ls:i]


def _trim_end(src: bytes, node):
    e = node.end_byte
    while e > node.start_byte and src[e - 1:e] in (b" ", b"\t", b"\r", b"\n"):
        e -= 1
    return e


class F40OperatorSpacing(_CF):
    rule_id = "F40"
    variants = ("a + b", "a+b")

    def _parts(self, n):
        t = n.type
        if t == "binary_expression":
            l, r = n.child_by_field_name("left"), n.child_by_field_name("right")
            ok = BIN_OPS
        elif t == "assignment_expression":
            l, r = n.child_by_field_name("left"), n.child_by_field_name("right")
            ok = ASSIGN_OPS
        elif t == "variable_declarator" and self.language == "java":
            l, r = n.child_by_field_name("name"), n.child_by_field_name("value")
            ok = {"="}
        elif t == "init_declarator" and self.language == "cpp":
            l, r = n.child_by_field_name("declarator"), n.child_by_field_name("value")
            ok = {"="}
        else:
            return None
        if l is None or r is None or r.start_byte < l.end_byte:
            return None
        ops = [c for c in n.children
               if not c.is_named and l.end_byte <= c.start_byte and c.end_byte <= r.start_byte]
        if len(ops) != 1 or txt(ops[0]) not in ok:
            return None
        return l, ops[0], r

    def match(self, tree, src):
        for n in walk(tree):
            p = self._parts(n)
            if p is None:
                continue
            l, op, r = p
            g1 = _gap_variant(src, l.end_byte, op.start_byte)
            g2 = _gap_variant(src, op.end_byte, r.start_byte)
            if g1 is None or g2 is None or (g1, g2) not in ((1, 1), (0, 0)):
                continue
            yield Site(rule_id=self.rule_id, variant=0 if g1 == 1 else 1,
                       span=(l.end_byte, r.start_byte),
                       footprint=((l.end_byte, r.start_byte),),
                       node_path=node_path(n),
                       meta={"n": n, "l": l, "op": op, "r": r, "ob": src[op.start_byte:op.end_byte]})

    def guard(self, site, tree, src):
        n, l, op, r = site.meta["n"], site.meta["l"], site.meta["op"], site.meta["r"]
        if _in_preproc(n):
            return False
        ob = src[op.start_byte:op.end_byte]
        if not ob or any(c not in OPCHARS for c in ob):
            return False
        lb, rb = src[l.start_byte:l.end_byte], src[r.start_byte:r.end_byte]
        if not lb or not rb:
            return False
        if lb[-1] in OPCHARS or rb[0] in OPCHARS:
            return False
        return True

    def write_bytes(self, site, src, target):
        s, e = site.span
        sp = b" " if target == 0 else b""
        return [Edit(s, e, sp + site.meta["ob"] + sp)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class F41KeywordSpacing(_CF):
    rule_id = "F41"
    variants = ("if (c)", "if  (c)")

    def match(self, tree, src):
        for n in walk(tree):
            if n.is_named or txt(n) not in KEYWORDS:
                continue
            nxt = n.next_sibling
            if nxt is None or nxt.start_byte <= n.end_byte:
                continue
            g = _gap_variant(src, n.end_byte, nxt.start_byte)
            if g not in (1, 2):
                continue
            yield Site(rule_id=self.rule_id, variant=0 if g == 1 else 1,
                       span=(n.end_byte, nxt.start_byte),
                       node_path=node_path(n.parent) if n.parent is not None else (),
                       meta={"n": n})

    def guard(self, site, tree, src):
        return not _in_preproc(site.meta["n"])

    def write_bytes(self, site, src, target):
        s, e = site.span
        return [Edit(s, e, b" " if target == 0 else b"  ")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class F42ClosingBracket(_CF):
    rule_id = "F42"
    variants = ("closing bracket at opening-line indent", "closing bracket one unit deeper")

    def match(self, tree, src):
        u = indent_unit(src)
        want = BRACKETED[self.language]
        for n in walk(tree):
            if n.type not in want or b"\n" not in src[n.start_byte:n.end_byte]:
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
            yield Site(rule_id=self.rule_id, variant=v,
                       span=(ls, close.start_byte + 1),
                       node_path=node_path(n),
                       meta={"n": n, "oind": oind, "u": u, "ls": ls, "cb": close.start_byte,
                             "cch": src[close.start_byte:close.start_byte + 1]})

    def guard(self, site, tree, src):
        return not _in_preproc(site.meta["n"])

    def write_bytes(self, site, src, target):
        m = site.meta
        ind = m["oind"] if target == 0 else m["oind"] + m["u"]
        return [Edit(m["ls"], m["cb"] + 1, ind + m["cch"])]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class F43EOFNewline(_CF):
    rule_id = "F43"
    variants = ("trailing newline", "no trailing newline")
    sid_mode = "root"

    def match(self, tree, src):
        n = len(src)
        if n == 0 or src.strip() == b"":
            return
        if src.endswith(b"\n\n"):
            return
        body = src[:-1] if src.endswith(b"\n") else src
        last = body[body.rfind(b"\n") + 1:].lstrip()
        if last.startswith(b"#") or last.endswith(b"\\"):
            return
        root = tree.root_node.type
        if src.endswith(b"\n"):
            yield Site(rule_id=self.rule_id, variant=0, span=(n - 1, n), node_path=(root,), meta={"n": n})
        else:
            yield Site(rule_id=self.rule_id, variant=1, span=(n, n), node_path=(root,), meta={"n": n})

    def write_bytes(self, site, src, target):
        n = site.meta["n"]
        return [Edit(n - 1, n, b"")] if target == 1 else [Edit(n, n, b"\n")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class F44BlankLinesBeforeDecl(_CF):
    rule_id = "F44"
    variants = ("one blank line before declaration", "none")
    EXPECTED = 1

    def match(self, tree, src):
        want, cont = DECLS[self.language], CONTAINERS[self.language]
        for n in walk(tree):
            if n.type not in want:
                continue
            d = n
            if self.language == "cpp" and d.parent is not None and d.parent.type == "template_declaration":
                d = d.parent
            par = d.parent
            if par is None or par.type not in cont:
                continue
            sibs = block_stmts(par)
            prev = None
            for a, b in zip(sibs, sibs[1:]):
                if b.id == d.id:
                    prev = a
                    break
            if prev is None or prev.type in BAD_PREV:
                continue
            pe = _trim_end(src, prev)
            m = BLANK_GAP.match(src[pe:d.start_byte])
            if not m:
                continue
            blank = len(m.group(1)) - 1
            if blank == self.EXPECTED:
                v = 0
            elif blank == self.EXPECTED - 1:
                v = 1
            else:
                continue
            yield Site(rule_id=self.rule_id, variant=v, span=(pe, d.start_byte),
                       node_path=node_path(d), meta={"n": d, "indent": m.group(2)})

    def guard(self, site, tree, src):
        return not _in_preproc(site.meta["n"])

    def write_bytes(self, site, src, target):
        s, e = site.span
        k = self.EXPECTED - (1 if target == 1 else 0)
        return [Edit(s, e, b"\n" * (k + 1) + site.meta["indent"])]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


FMT_CLASSES = [F40OperatorSpacing, F41KeywordSpacing, F42ClosingBracket, F43EOFNewline, F44BlankLinesBeforeDecl]

JAVA_RULES = {c.rule_id: c("java") for c in FMT_CLASSES}
CPP_RULES = {c.rule_id: c("cpp") for c in FMT_CLASSES}
