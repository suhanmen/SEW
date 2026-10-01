from .base import Edit, Rule, Site, named_children, node_path
from .python_rules import line_indent, starts_own_line, txt


def walk(tree):
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        stack.extend(n.children)
        yield n


def indent_unit(src: bytes) -> bytes:
    return b"\t" if b"\n\t" in src else b"    "


def has_comment(node) -> bool:
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == "comment":
            return True
        stack.extend(n.children)
    return False


def block_stmts(block):
    return [c for c in named_children(block) if c.type != "comment"]


def single_line(src: bytes, node) -> bool:
    return b"\n" not in src[node.start_byte:node.end_byte]


def clean_tail(src: bytes, node) -> bool:
    nl = src.find(b"\n", node.end_byte)
    tail = src[node.end_byte:] if nl < 0 else src[node.end_byte:nl]
    return tail.strip() == b""


class _ControlRule(Rule):
    language = "python"


class T2CondAssign(_ControlRule):
    rule_id = "T2"
    variants = ("x = a if c else b", "if c: x = a else: x = b")
    sid_mode = "parent"
    BAD = {"conditional_expression", "lambda", "yield", "named_expression",
           "tuple", "expression_list", "list_splat", "dictionary_splat", "assignment"}

    def match(self, tree, src):
        for n in walk(tree):
            if n.type == "expression_statement":
                k = named_children(n)
                if len(k) != 1 or k[0].type != "assignment":
                    continue
                a = k[0]
                left, right = a.child_by_field_name("left"), a.child_by_field_name("right")
                if left is None or right is None or a.child_by_field_name("type") is not None:
                    continue
                if left.type != "identifier" or right.type != "conditional_expression":
                    continue
                kk = named_children(right)
                if len(kk) != 3:
                    continue
                cons, cond, alt = kk
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n),
                           meta={"name": txt(left), "cond": cond, "cons": cons, "alt": alt, "node": n})
            elif n.type == "if_statement":
                alt_nodes = [c for c in named_children(n) if c.type in ("else_clause", "elif_clause")]
                if len(alt_nodes) != 1 or alt_nodes[0].type != "else_clause":
                    continue
                cond = n.child_by_field_name("condition")
                cons_b = n.child_by_field_name("consequence")
                alt_b = named_children(alt_nodes[0])
                alt_b = alt_b[-1] if alt_b else None
                if cond is None or cons_b is None or alt_b is None or alt_b.type != "block":
                    continue
                pair = []
                for b in (cons_b, alt_b):
                    st = block_stmts(b)
                    if len(st) != 1 or st[0].type != "expression_statement":
                        pair = None
                        break
                    kk = named_children(st[0])
                    if len(kk) != 1 or kk[0].type != "assignment":
                        pair = None
                        break
                    asg = kk[0]
                    l, r = asg.child_by_field_name("left"), asg.child_by_field_name("right")
                    if l is None or r is None or l.type != "identifier" or asg.child_by_field_name("type") is not None:
                        pair = None
                        break
                    pair.append((l, r, st[0]))
                if not pair or txt(pair[0][0]) != txt(pair[1][0]):
                    continue
                yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n),
                           meta={"name": txt(pair[0][0]), "cond": cond, "cons": pair[0][1], "alt": pair[1][1],
                                 "node": n, "stmts": (pair[0][2], pair[1][2])})

    def guard(self, site, tree, src):
        n = site.meta["node"]
        if has_comment(n) or not starts_own_line(src, n) or not clean_tail(src, n):
            return False
        for key in ("cond", "cons", "alt"):
            x = site.meta[key]
            if x.type in self.BAD or not single_line(src, x):
                return False
        if site.variant == 1:
            for st in site.meta["stmts"]:
                if not starts_own_line(src, st):
                    return False
        return True

    def _texts(self, site, src):
        g = lambda x: src[x.start_byte:x.end_byte]
        return g(site.meta["cond"]), g(site.meta["cons"]), g(site.meta["alt"])

    def write_bytes(self, site, src, target):
        cond, cons, alt = self._texts(site, src)
        name = site.meta["name"].encode()
        s, e = site.span
        if target == 0:
            new = name + b" = " + cons + b" if " + cond + b" else " + alt
        else:
            _, ind = line_indent(src, s)
            u = indent_unit(src)
            new = (b"if " + cond + b":\n" + ind + u + name + b" = " + cons + b"\n"
                   + ind + b"else:\n" + ind + u + name + b" = " + alt)
        return [Edit(s, e, new)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


RULES = {r.rule_id: r() for r in (T2CondAssign,)}
