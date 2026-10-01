from .base import Edit, Rule, Site, named_children, node_path
from ..sites.typeinfo import numeric_names_by_function, inference_for_offset

_INFER_CACHE = {}


def _numeric_inference(src: bytes, byte_offset: int):
    key = hash(src)
    if key not in _INFER_CACHE:
        _INFER_CACHE[key] = numeric_names_by_function(src.decode("utf-8", errors="replace"))
        if len(_INFER_CACHE) > 256:
            _INFER_CACHE.pop(next(iter(_INFER_CACHE)))
    return inference_for_offset(src, _INFER_CACHE[key], byte_offset)

PURE_LEAF = {"identifier", "attribute", "integer", "float", "string", "true", "false", "none"}
LITERAL = {"integer", "float", "string", "concatenated_string", "true", "false", "none"}
NUMERIC_LITERAL = {"integer", "float"}


def txt(n):
    return n.text.decode("utf-8")


def is_pure(n):
    if n is None:
        return False
    if n.type in PURE_LEAF:
        return True
    if n.type == "subscript":
        return is_pure(n.child_by_field_name("value")) and is_pure(n.child_by_field_name("subscript"))
    if n.type == "unary_operator":
        return is_pure(n.child_by_field_name("argument"))
    if n.type == "parenthesized_expression":
        k = named_children(n)
        return len(k) == 1 and is_pure(k[0])
    return False


NEEDS_PARENS = {"boolean_operator", "conditional_expression", "lambda",
                "named_expression", "tuple", "yield"}


def needs_parens(n):
    return n is not None and n.type in NEEDS_PARENS


def line_indent(src: bytes, offset: int):
    ls = src.rfind(b"\n", 0, offset) + 1
    i = ls
    while i < len(src) and src[i:i + 1] in (b" ", b"\t"):
        i += 1
    return ls, src[ls:i]


def starts_own_line(src: bytes, node):
    ls, indent = line_indent(src, node.start_byte)
    return node.start_byte == ls + len(indent)


class T1BranchPolarity(Rule):
    rule_id = "T1"
    language = "python"
    variants = ("if c: A else: B", "if not c: B else: A")

    def match(self, tree, src):
        stack = [tree.root_node]
        while stack:
            n = stack.pop()
            stack.extend(n.children)
            if n.type != "if_statement":
                continue
            cond = n.child_by_field_name("condition")
            alt = n.child_by_field_name("alternative")
            if cond is None or alt is None or alt.type != "else_clause":
                continue
            cons = n.child_by_field_name("consequence")
            else_block = next((c for c in named_children(alt) if c.type == "block"), None)
            kw = next((c for c in n.children if not c.is_named and c.type == "if"), None)
            if cons is None or else_block is None or kw is None:
                continue
            yield Site(
                rule_id=self.rule_id,
                variant=1 if cond.type == "not_operator" else 0,
                span=(n.start_byte, n.end_byte),
                footprint=((kw.end_byte, cond.end_byte),
                           (cons.start_byte, cons.end_byte),
                           (else_block.start_byte, else_block.end_byte)),
                node_path=node_path(n),
                meta={"cond": cond, "cons": cons, "else_block": else_block,
                      "if_node": n, "kw_end": kw.end_byte},
            )

    def guard(self, site, tree, src):
        n = site.meta["if_node"]
        if any(c.type == "elif_clause" for c in n.children):
            return False
        cons, eb = site.meta["cons"], site.meta["else_block"]
        if cons.type != "block" or eb.type != "block":
            return False
        if not (starts_own_line(src, cons) and starts_own_line(src, eb)):
            return False
        if line_indent(src, cons.start_byte)[1] != line_indent(src, eb.start_byte)[1]:
            return False
        if site.variant == 1:
            return self._inner(site.meta["cond"]) is not None
        return True

    @staticmethod
    def _inner(cond):
        if cond.type != "not_operator":
            return None
        arg = cond.child_by_field_name("argument")
        if arg is None:
            return None
        if arg.type == "parenthesized_expression":
            k = named_children(arg)
            if len(k) != 1:
                return None
            return k[0] if needs_parens(k[0]) else arg
        return arg

    def write_bytes(self, site, src, target):
        cond, cons, eb = site.meta["cond"], site.meta["cons"], site.meta["else_block"]
        kw_end = site.meta["kw_end"]
        gap = src[kw_end:cond.start_byte] or b" "
        if target == 1:
            body = src[cond.start_byte:cond.end_byte]
            if needs_parens(cond):
                body = b"(" + body + b")"
            new_cond = gap + b"not " + body
        else:
            inner = self._inner(cond)
            new_cond = gap + src[inner.start_byte:inner.end_byte]
        return [
            Edit(kw_end, cond.end_byte, new_cond),
            Edit(cons.start_byte, cons.end_byte, src[eb.start_byte:eb.end_byte]),
            Edit(eb.start_byte, eb.end_byte, src[cons.start_byte:cons.end_byte]),
        ]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


FLIP = {"<": ">", ">": "<", "<=": ">=", ">=": "<="}


class E1CompareDirection(Rule):
    rule_id = "E1"
    language = "python"
    variants = ("a < b", "b > a")

    def match(self, tree, src):
        stack = [tree.root_node]
        while stack:
            n = stack.pop()
            stack.extend(n.children)
            if n.type != "comparison_operator":
                continue
            kids = n.children
            if len(kids) != 3:
                continue
            left, op, right = kids
            if op.is_named or txt(op) not in FLIP:
                continue
            yield Site(
                rule_id=self.rule_id,
                variant=0 if txt(op) in ("<", "<=") else 1,
                span=(n.start_byte, n.end_byte),
                node_path=node_path(n),
                meta={"left": left, "op": op, "right": right},
            )

    def guard(self, site, tree, src):
        left, right = site.meta["left"], site.meta["right"]
        if not (is_pure(left) and is_pure(right)):
            return False
        if left.type in LITERAL or right.type in LITERAL:
            return False
        return True

    def write_bytes(self, site, src, target):
        left, op, right = site.meta["left"], site.meta["op"], site.meta["right"]
        sp1 = src[left.end_byte:op.start_byte]
        sp2 = src[op.end_byte:right.start_byte]
        new = (src[right.start_byte:right.end_byte] + sp1
               + FLIP[txt(op)].encode() + sp2
               + src[left.start_byte:left.end_byte])
        return [Edit(site.span[0], site.span[1], new)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


AUG_OPS = {"+=", "-=", "*=", "//=", "%=", "**=", "&=", "|=", "^=", "<<=", ">>="}


_PREC = {"**": 6, "*": 4, "/": 4, "//": 4, "%": 4, "@": 4, "+": 3, "-": 3, "<<": 2, ">>": 2, "&": 1, "^": 0, "|": -1}
_ATOMIC_RHS = {"identifier", "integer", "float", "string", "concatenated_string", "true", "false", "none", "call", "attribute",
               "subscript", "parenthesized_expression", "list", "tuple", "dictionary", "set", "list_comprehension",
               "generator_expression", "dictionary_comprehension", "set_comprehension"}


def _rhs_safe_to_expand(right, base_op):
    if right.type in _ATOMIC_RHS:
        return True
    if right.type == "unary_operator":
        return True
    if right.type == "binary_operator":
        rop = right.child_by_field_name("operator")
        return rop is not None and _PREC.get(txt(rop), -99) > _PREC.get(base_op, 99)
    return False


class S1CompoundAssign(Rule):
    rule_id = "S1"
    language = "python"
    variants = ("x += 1", "x = x + 1")

    def match(self, tree, src):
        stack = [tree.root_node]
        while stack:
            n = stack.pop()
            stack.extend(n.children)
            if n.type == "augmented_assignment":
                left = n.child_by_field_name("left")
                op = n.child_by_field_name("operator")
                right = n.child_by_field_name("right")
                if left is None or op is None or right is None:
                    continue
                yield Site(rule_id=self.rule_id, variant=0,
                           span=(n.start_byte, n.end_byte), node_path=node_path(n),
                           meta={"left": left, "op": op, "right": right,
                                 "eq": None, "bl": None})
            elif n.type == "assignment":
                left = n.child_by_field_name("left")
                right = n.child_by_field_name("right")
                if left is None or right is None or right.type != "binary_operator":
                    continue
                bl = right.child_by_field_name("left")
                if bl is None or left.type != "identifier" or bl.type != "identifier":
                    continue
                if txt(bl) != txt(left):
                    continue
                eq = next((c for c in n.children if not c.is_named and c.type == "="), None)
                if eq is None:
                    continue
                yield Site(rule_id=self.rule_id, variant=1,
                           span=(n.start_byte, n.end_byte), node_path=node_path(n),
                           meta={"left": left, "op": right.child_by_field_name("operator"),
                                 "right": right.child_by_field_name("right"),
                                 "eq": eq, "bl": bl})

    def guard(self, site, tree, src):
        left, op, right = site.meta["left"], site.meta["op"], site.meta["right"]
        if left is None or op is None or right is None:
            return False
        if left.type != "identifier":
            return False
        if right.type not in NUMERIC_LITERAL:
            inf = _numeric_inference(src, site.span[0])
            if inf is None or txt(left) not in inf.names:
                return False
        aug = txt(op) if site.variant == 0 else txt(op) + "="
        if aug not in AUG_OPS:
            return False
        if site.variant == 0 and not _rhs_safe_to_expand(right, txt(op)[:-1]):
            return False
        return True

    def write_bytes(self, site, src, target):
        left, op, right = site.meta["left"], site.meta["op"], site.meta["right"]
        name = src[left.start_byte:left.end_byte]
        rhs = src[right.start_byte:right.end_byte]
        if site.variant == 0:
            sp1 = src[left.end_byte:op.start_byte]
            sp2 = src[op.end_byte:right.start_byte]
            base_op = txt(op)[:-1]
        else:
            eq, bl = site.meta["eq"], site.meta["bl"]
            sp1 = src[left.end_byte:eq.start_byte]
            sp2 = src[eq.end_byte:bl.start_byte]
            base_op = txt(op)
        if target == 1:
            new = name + sp1 + b"=" + sp2 + name + sp1 + base_op.encode() + sp2 + rhs
        else:
            new = name + sp1 + (base_op + "=").encode() + sp2 + rhs
        return [Edit(site.span[0], site.span[1], new)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


RULES = {r.rule_id: r() for r in (T1BranchPolarity, E1CompareDirection, S1CompoundAssign)}
