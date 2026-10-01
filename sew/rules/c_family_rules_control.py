from .base import Edit, Rule, Site, named_children, node_path
from .c_family_rules import block_stmts, cond_inner, is_block, txt, walk

DECL_TYPES = {"java": "local_variable_declaration", "cpp": "declaration"}
T10_BODY_OK = {"expression_statement", "return_statement", "break_statement", "continue_statement", "throw_statement"}
T2_BAD = {"ternary_expression", "conditional_expression", "assignment_expression", "comma_expression", "lambda_expression"}


def indent_unit(src: bytes) -> bytes:
    return b"\t" if b"\n\t" in src else b"    "


def line_indent(src: bytes, offset: int):
    ls = src.rfind(b"\n", 0, offset) + 1
    i = ls
    while i < len(src) and src[i:i + 1] in (b" ", b"\t"):
        i += 1
    return ls, src[ls:i]


def has_comment(node) -> bool:
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type.endswith("comment"):
            return True
        stack.extend(n.children)
    return False


def single_line(src, node):
    return b"\n" not in src[node.start_byte:node.end_byte]


def clean_tail(src, node):
    nl = src.find(b"\n", node.end_byte)
    tail = src[node.end_byte:] if nl < 0 else src[node.end_byte:nl]
    return tail.strip() == b""


def sibling_stmts(node):
    p = node.parent
    if p is None or not is_block(p):
        return [], -1
    sibs = block_stmts(p)
    try:
        return sibs, sibs.index(node)
    except ValueError:
        return sibs, -1


def separator_ok(src, a, b):
    _, indent = line_indent(src, b.start_byte)
    return src[a.end_byte:b.start_byte] in (b"\n" + indent, b"\r\n" + indent)


class _ControlRule(Rule):
    def __init__(self, language):
        self.language = language


class T10SingleBraces(_ControlRule):
    rule_id = "T10"
    variants = ("if (c) s;", "if (c) { s; }")
    sid_mode = "body"

    def match(self, tree, src):
        for n in walk(tree):
            if n.type not in ("if_statement", "for_statement", "while_statement", "for_range_loop"):
                continue
            if n.type == "if_statement" and n.child_by_field_name("alternative") is not None:
                continue
            b = n.child_by_field_name("body") or n.child_by_field_name("consequence")
            if b is None:
                continue
            if is_block(b):
                st = block_stmts(b)
                if len(st) != 1:
                    continue
                yield Site(rule_id=self.rule_id, variant=1, span=(b.start_byte, b.end_byte),
                           node_path=node_path(b), meta={"inner": st[0], "block": b})
            elif b.type in T10_BODY_OK:
                yield Site(rule_id=self.rule_id, variant=0, span=(b.start_byte, b.end_byte),
                           node_path=node_path(b), meta={"inner": b, "block": None})

    def guard(self, site, tree, src):
        inner, blk = site.meta["inner"], site.meta["block"]
        if blk is not None and has_comment(blk):
            return False
        return (inner.type in T10_BODY_OK and single_line(src, inner)
                and not has_comment(inner) and inner.end_byte > inner.start_byte)

    def write_bytes(self, site, src, target):
        inner = site.meta["inner"]
        body = src[inner.start_byte:inner.end_byte]
        return [Edit(site.span[0], site.span[1], b"{ " + body + b" }" if target == 1 else body)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class S12MultiDeclarator(_ControlRule):
    rule_id = "S12"
    variants = ("int a, b;", "int a; int b;")

    def _declarators(self, stmt):
        out = []
        for c in named_children(stmt):
            if c.type in ("variable_declarator", "init_declarator", "identifier"):
                out.append(c)
            elif c.type in ("pointer_declarator", "reference_declarator", "array_declarator",
                            "function_declarator", "parenthesized_declarator"):
                return None, None
        if not out:
            return None, None
        for d in out:
            if d.type == "variable_declarator":
                nm = d.child_by_field_name("name")
                if nm is None or nm.type != "identifier" or d.child_by_field_name("dimensions") is not None:
                    return None, None
            elif d.type == "init_declarator":
                nm = d.child_by_field_name("declarator")
                if nm is None or nm.type != "identifier":
                    return None, None
        return out, out[0].start_byte

    def match(self, tree, src):
        dt = DECL_TYPES[self.language]
        seen = set()
        for n in walk(tree):
            if n.type != dt or n.start_byte in seen:
                continue
            decls, pre_end = self._declarators(n)
            if decls is None:
                continue
            prefix = src[n.start_byte:pre_end].strip()
            if not prefix:
                continue
            sibs, i = sibling_stmts(n)
            if i < 0:
                continue
            def ok(m):
                if m.type != dt:
                    return None
                d2, p2 = self._declarators(m)
                if d2 is None or src[m.start_byte:p2].strip() != prefix:
                    return None
                return d2
            lo = i
            while lo - 1 >= 0 and ok(sibs[lo - 1]) is not None and separator_ok(src, sibs[lo - 1], sibs[lo]):
                lo -= 1
            hi = i
            while hi + 1 < len(sibs) and ok(sibs[hi + 1]) is not None and separator_ok(src, sibs[hi], sibs[hi + 1]):
                hi += 1
            run = sibs[lo:hi + 1]
            for m in run:
                seen.add(m.start_byte)
            groups = [ok(m) for m in run]
            if sum(len(g) for g in groups) != 2:
                continue
            if len(run) == 1:
                yield Site(rule_id=self.rule_id, variant=0, span=(run[0].start_byte, run[0].end_byte),
                           node_path=node_path(run[0]), meta={"prefix": prefix, "decls": groups[0], "stmts": run})
            elif len(run) == 2:
                yield Site(rule_id=self.rule_id, variant=1, span=(run[0].start_byte, run[1].end_byte),
                           node_path=node_path(run[0]),
                           meta={"prefix": prefix, "decls": groups[0] + groups[1], "stmts": run})

    def guard(self, site, tree, src):
        if any(has_comment(s) for s in site.meta["stmts"]):
            return False
        return all(single_line(src, s) and clean_tail(src, s) for s in site.meta["stmts"])

    def write_bytes(self, site, src, target):
        pre = site.meta["prefix"]
        D = [src[d.start_byte:d.end_byte] for d in site.meta["decls"]]
        s, e = site.span
        if target == 0:
            new = pre + b" " + D[0] + b", " + D[1] + b";"
        else:
            _, ind = line_indent(src, s)
            new = pre + b" " + D[0] + b";\n" + ind + pre + b" " + D[1] + b";"
        return [Edit(s, e, new)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class T2CondAssign(_ControlRule):
    rule_id = "T2"
    variants = ("x = c ? a : b;", "if (c) { x = a; } else { x = b; }")
    sid_mode = "parent"

    def _assign(self, stmt):
        if stmt is None or stmt.type != "expression_statement":
            return None
        k = named_children(stmt)
        if len(k) != 1 or k[0].type != "assignment_expression":
            return None
        a = k[0]
        l, r = a.child_by_field_name("left"), a.child_by_field_name("right")
        op = a.child_by_field_name("operator")
        if l is None or r is None or l.type != "identifier":
            return None
        if op is not None and txt(op) != "=":
            return None
        ops = [c for c in a.children if not c.is_named]
        if ops and txt(ops[0]) != "=":
            return None
        return l, r, a

    def match(self, tree, src):
        for n in walk(tree):
            if n.type == "expression_statement":
                got = self._assign(n)
                if got is None:
                    continue
                l, r, _ = got
                if r.type not in ("ternary_expression", "conditional_expression"):
                    continue
                cond = r.child_by_field_name("condition")
                cons = r.child_by_field_name("consequence")
                alt = r.child_by_field_name("alternative")
                if cond is None or cons is None or alt is None:
                    continue
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n),
                           meta={"name": txt(l), "cond": cond, "cons": cons, "alt": alt,
                                 "node": n, "cond_parens": False})
            elif n.type == "if_statement":
                alt_node = n.child_by_field_name("alternative")
                cons_b = n.child_by_field_name("consequence")
                cnode, _span = cond_inner(n)
                if alt_node is None or cons_b is None or cnode is None:
                    continue
                if alt_node.type == "else_clause":
                    k = named_children(alt_node)
                    alt_node = k[-1] if k else None
                if alt_node is None or alt_node.type == "if_statement":
                    continue
                pair = []
                for b in (cons_b, alt_node):
                    if not is_block(b):
                        pair = None
                        break
                    st = block_stmts(b)
                    if len(st) != 1:
                        pair = None
                        break
                    got = self._assign(st[0])
                    if got is None:
                        pair = None
                        break
                    pair.append(got)
                if not pair or txt(pair[0][0]) != txt(pair[1][0]):
                    continue
                yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n),
                           meta={"name": txt(pair[0][0]), "cond": cnode, "cons": pair[0][1], "alt": pair[1][1],
                                 "node": n, "cond_parens": True})

    def guard(self, site, tree, src):
        n = site.meta["node"]
        if has_comment(n) or not clean_tail(src, n):
            return False
        for key in ("cond", "cons", "alt"):
            x = site.meta[key]
            if x.type in T2_BAD or not single_line(src, x):
                return False
        _, ind = line_indent(src, n.start_byte)
        return n.start_byte == src.rfind(b"\n", 0, n.start_byte) + 1 + len(ind)

    def write_bytes(self, site, src, target):
        g = lambda x: src[x.start_byte:x.end_byte]
        cond, cons, alt = g(site.meta["cond"]), g(site.meta["cons"]), g(site.meta["alt"])
        name = site.meta["name"].encode()
        s, e = site.span
        if target == 0:
            new = name + b" = " + cond + b" ? " + cons + b" : " + alt + b";"
        else:
            _, ind = line_indent(src, s)
            u = indent_unit(src)
            new = (b"if (" + cond + b") {\n" + ind + u + name + b" = " + cons + b";\n"
                   + ind + b"} else {\n" + ind + u + name + b" = " + alt + b";\n" + ind + b"}")
        return [Edit(s, e, new)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


CONTROL_CLASSES = [T10SingleBraces, S12MultiDeclarator, T2CondAssign]
JAVA_RULES = {c.rule_id: c("java") for c in CONTROL_CLASSES}
CPP_RULES = {c.rule_id: c("cpp") for c in CONTROL_CLASSES}
