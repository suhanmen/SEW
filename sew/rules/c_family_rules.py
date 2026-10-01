from .base import Edit, Rule, Site, named_children, node_path

CMP_FLIP = {"<": ">", ">": "<", "<=": ">=", ">=": "<="}
COMM_OPS = {"+", "*", "==", "!=", "&", "|", "^"}
PREC = {"*": 1, "/": 1, "%": 1, "+": 2, "-": 2, "<<": 3, ">>": 3,
        "<": 4, ">": 4, "<=": 4, ">=": 4, "==": 5, "!=": 5, "&": 6, "^": 7, "|": 8, "&&": 9, "||": 10}
HIGHER = {(a, b) for a in ("+", "-") for b in ("*", "/", "%")} | {(a, b) for a in ("*", "/", "%") for b in ()}
NUM_LIT = {"number_literal", "decimal_integer_literal", "decimal_floating_point_literal", "hex_integer_literal"}
LITERAL = NUM_LIT | {"string_literal", "character_literal", "char_literal", "true", "false", "null_literal", "null"}
PURE_LEAF = {"identifier", "field_access", "field_expression", "array_access", "subscript_expression", "this"} | LITERAL
ATOMIC = PURE_LEAF | {"parenthesized_expression", "method_invocation", "call_expression"}
NEEDS_PARENS = {"binary_expression", "assignment_expression", "conditional_expression",
                "ternary_expression", "lambda_expression", "comma_expression"}
PRIMITIVE_NUM = {"int", "long", "short", "double", "float", "size_t", "unsigned", "unsigned int", "long long"}


def txt(n):
    return n.text.decode("utf-8")


def walk(tree):
    stack = [tree.root_node]
    while stack:
        n = stack.pop(); stack.extend(n.children); yield n


def is_pure(n):
    if n is None:
        return False
    if n.type in PURE_LEAF:
        return True
    if n.type in ("parenthesized_expression", "unary_expression"):
        k = named_children(n); return len(k) == 1 and is_pure(k[0])
    return False


def needs_parens(n):
    return n is not None and n.type in NEEDS_PARENS


def cond_inner(if_node):
    c = if_node.child_by_field_name("condition")
    if c is None:
        return None, None
    if c.type in ("parenthesized_expression", "condition_clause"):
        k = named_children(c)
        if len(k) != 1:
            return None, None
        return k[0], (k[0].start_byte, k[0].end_byte)
    return c, (c.start_byte, c.end_byte)


def else_body(if_node):
    alt = if_node.child_by_field_name("alternative")
    if alt is not None and alt.type == "else_clause":
        k = named_children(alt); alt = k[-1] if k else None
    return alt


def is_block(n):
    return n is not None and n.type in ("block", "compound_statement")


def block_stmts(n):
    return [c for c in named_children(n) if not c.type.endswith("comment")]


def enclosing_body(node):
    p = node.parent
    while p is not None:
        if p.type in ("method_declaration", "function_definition", "constructor_declaration"):
            return p.child_by_field_name("body")
        p = p.parent
    return None


def reassigned_in(body, name):
    if body is None:
        return True
    stack = [body]
    while stack:
        n = stack.pop(); stack.extend(n.children)
        if n.type == "assignment_expression":
            l = n.child_by_field_name("left")
            if l is not None and l.type == "identifier" and txt(l) == name:
                return True
        elif n.type == "update_expression":
            a = n.child_by_field_name("argument") or (named_children(n)[0] if named_children(n) else None)
            if a is not None and a.type == "identifier" and txt(a) == name:
                return True
        elif n.type == "unary_expression" and txt(n).startswith("&"):
            k = named_children(n)
            if k and k[0].type == "identifier" and txt(k[0]) == name:
                return True
        elif n.type == "pointer_expression" and n.child_by_field_name("operator") is not None \
                and txt(n.child_by_field_name("operator")) == "&":
            a = n.child_by_field_name("argument")
            if a is not None and a.type == "identifier" and txt(a) == name:
                return True
    return False


_BYVALUE_CALLEES = {"abs", "fabs", "labs", "llabs", "max", "min", "sqrt", "pow", "floor", "ceil", "round", "trunc", "log", "log2", "log10", "exp",
                    "sin", "cos", "tan", "fmod", "to_string", "isdigit", "isalpha", "isalnum", "isspace", "isupper", "islower", "toupper", "tolower",
                    "printf", "push_back", "emplace_back", "push", "insert", "emplace", "count", "find", "at", "erase", "reserve", "resize", "assign",
                    "append", "substr", "make_pair", "gcd", "lcm", "sort", "reverse"}


def _nodes(node):
    stack = [node]
    while stack:
        n = stack.pop(); stack.extend(n.children); yield n


def _file_function_params(tree):
    out = {}
    for n in walk(tree):
        if n.type != "function_definition":
            continue
        d = n.child_by_field_name("declarator")
        while d is not None and d.type != "function_declarator":
            d = d.child_by_field_name("declarator") or next((c for c in named_children(d) if c.type.endswith("declarator")), None)
        if d is None:
            continue
        nm = d.child_by_field_name("declarator"); ps = d.child_by_field_name("parameters")
        if nm is None or ps is None:
            continue
        name = txt(nm).split("::")[-1]
        flags = []
        for pd in named_children(ps):
            if pd.type not in ("parameter_declaration", "optional_parameter_declaration"):
                continue
            has_ref = any(c.type == "reference_declarator" for c in _nodes(pd))
            is_const = any(c.type == "type_qualifier" and txt(c) == "const" for c in named_children(pd))
            flags.append(has_ref and not is_const)
        prev = out.get(name, [])
        out[name] = [(i < len(prev) and prev[i]) or (i < len(flags) and flags[i]) for i in range(max(len(prev), len(flags)))]
    return out


def passed_by_nonconst_ref(tree, body, name):
    defs = _file_function_params(tree)
    for n in _nodes(body):
        if n.type != "call_expression":
            continue
        args = n.child_by_field_name("arguments")
        if args is None:
            continue
        idx = [i for i, a in enumerate(named_children(args)) if a.type == "identifier" and txt(a) == name]
        if not idx:
            continue
        fn = n.child_by_field_name("function")
        callee = txt(fn) if fn is not None else ""
        base = callee.split("::")[-1].split(".")[-1].split("->")[-1]
        if base in defs:
            flags = defs[base]
            if any(i < len(flags) and flags[i] for i in idx):
                return True
            continue
        if base not in _BYVALUE_CALLEES:
            return True
    return False


COLLECTION_TYPES = {"List", "ArrayList", "LinkedList", "Set", "HashSet", "TreeSet", "Map", "HashMap",
                    "TreeMap", "Collection", "Queue", "Deque", "Stack", "Vector"}


def declared_type(body, name):
    if body is None:
        return None
    fn = body.parent
    if fn is not None:
        params = fn.child_by_field_name("parameters")
        if params is not None:
            for pnode in named_children(params):
                if pnode.type in ("formal_parameter", "parameter_declaration"):
                    t = pnode.child_by_field_name("type")
                    nm = pnode.child_by_field_name("name") or pnode.child_by_field_name("declarator")
                    if t is not None and nm is not None and nm.type == "identifier" and txt(nm) == name:
                        return txt(t)
    stack = [body]
    while stack:
        n = stack.pop(); stack.extend(n.children)
        if n.type not in ("local_variable_declaration", "declaration"):
            continue
        t = n.child_by_field_name("type")
        if t is None:
            continue
        for d in named_children(n):
            nm = d
            if d.type in ("variable_declarator", "init_declarator"):
                nm = d.child_by_field_name("name") or d.child_by_field_name("declarator") or d
            if nm is not None and nm.type == "identifier" and txt(nm) == name:
                return txt(t)
    return None


def base_type_name(t):
    return (t or "").split("<")[0].strip()


class _CRule(Rule):
    def __init__(self, language):
        self.language = language


class E1Compare(_CRule):
    rule_id = "E1"; variants = ("a < b", "b > a")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "binary_expression" or len(n.children) != 3:
                continue
            l, op, r = n.children
            if op.is_named or txt(op) not in CMP_FLIP:
                continue
            yield Site(rule_id=self.rule_id, variant=0 if txt(op) in ("<", "<=") else 1,
                       span=(n.start_byte, n.end_byte), node_path=node_path(n),
                       meta={"l": l, "op": op, "r": r})

    def guard(self, site, tree, src):
        l, r = site.meta["l"], site.meta["r"]
        no_lit = l.type not in LITERAL and r.type not in LITERAL
        return is_pure(l) and is_pure(r) and no_lit

    def write_bytes(self, site, src, target):
        l, op, r = site.meta["l"], site.meta["op"], site.meta["r"]
        sp1 = src[l.end_byte:op.start_byte]; sp2 = src[op.end_byte:r.start_byte]
        return [Edit(*site.span, src[r.start_byte:r.end_byte] + sp1 + CMP_FLIP[txt(op)].encode() + sp2 + src[l.start_byte:l.end_byte])]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class E2Commutative(_CRule):
    rule_id = "E2"; variants = ("x + 1", "1 + x")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "binary_expression" or len(n.children) != 3:
                continue
            l, op, r = n.children
            if op.is_named or txt(op) not in COMM_OPS:
                continue
            ll, rl = l.type in NUM_LIT, r.type in NUM_LIT
            if ll == rl:
                continue
            yield Site(rule_id=self.rule_id, variant=1 if ll else 0,
                       span=(n.start_byte, n.end_byte), node_path=node_path(n),
                       meta={"l": l, "op": op, "r": r})

    _STR_LIT = {"string_literal"}

    @classmethod
    def _unparen(cls, n, depth=0):
        while n is not None and n.type == "parenthesized_expression" and depth < 4:
            k = [c for c in n.children if c.is_named]
            if len(k) != 1:
                break
            n = k[0]; depth += 1
        return n

    def guard(self, site, tree, src):
        l, op, r = site.meta["l"], site.meta["op"], site.meta["r"]
        if txt(op) == "+" and (self._unparen(l).type in self._STR_LIT or self._unparen(r).type in self._STR_LIT):
            return False
        other = r if site.variant == 1 else l
        return other.type in ATOMIC

    def write_bytes(self, site, src, target):
        l, op, r = site.meta["l"], site.meta["op"], site.meta["r"]
        sp1 = src[l.end_byte:op.start_byte]; sp2 = src[op.end_byte:r.start_byte]
        return [Edit(*site.span, src[r.start_byte:r.end_byte] + sp1 + src[op.start_byte:op.end_byte] + sp2 + src[l.start_byte:l.end_byte])]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class E3DeMorgan(_CRule):
    rule_id = "E3"; variants = ("!(a && b)", "!a || !b")
    OPP = {"&&": "||", "||": "&&"}

    def match(self, tree, src):
        for n in walk(tree):
            if n.type == "unary_expression":
                ops = [c for c in n.children if not c.is_named]
                if not ops or txt(ops[0]) != "!":
                    continue
                arg = n.child_by_field_name("argument") or (named_children(n)[0] if named_children(n) else None)
                if arg is None or arg.type != "parenthesized_expression":
                    continue
                k = named_children(arg)
                if len(k) != 1 or k[0].type != "binary_expression" or len(k[0].children) != 3:
                    continue
                if txt(k[0].children[1]) not in self.OPP:
                    continue
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"inner": k[0]})
            elif n.type == "binary_expression" and len(n.children) == 3 and txt(n.children[1]) in self.OPP:
                l, r = n.children[0], n.children[2]
                if self._neg(l) is None or self._neg(r) is None:
                    continue
                yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"l": l, "op": n.children[1], "r": r})

    @staticmethod
    def _neg(n):
        if n.type != "unary_expression":
            return None
        ops = [c for c in n.children if not c.is_named]
        if not ops or txt(ops[0]) != "!":
            return None
        a = n.child_by_field_name("argument") or (named_children(n)[0] if named_children(n) else None)
        return a

    def guard(self, site, tree, src):
        if site.variant == 0:
            inner = site.meta["inner"]
            return is_pure(inner.children[0]) and is_pure(inner.children[2])
        return all(is_pure(self._neg(x)) for x in (site.meta["l"], site.meta["r"]))

    def write_bytes(self, site, src, target):
        if target == 1:
            inner = site.meta["inner"]; a, op, b = inner.children
            new = b"!" + self._wrap(src, a) + b" " + self.OPP[txt(op)].encode() + b" " + b"!" + self._wrap(src, b)
        else:
            a = self._neg(site.meta["l"]); b = self._neg(site.meta["r"]); op = site.meta["op"]
            ai = self._unwrap(src, a); bi = self._unwrap(src, b)
            new = b"!(" + ai + b" " + self.OPP[txt(op)].encode() + b" " + bi + b")"
        return [Edit(*site.span, new)]

    @staticmethod
    def _wrap(src, n):
        t = src[n.start_byte:n.end_byte]
        return t if n.type in ATOMIC else b"(" + t + b")"

    @staticmethod
    def _unwrap(src, n):
        if n.type == "parenthesized_expression":
            k = named_children(n)
            if len(k) == 1 and not needs_parens(k[0]):
                return src[k[0].start_byte:k[0].end_byte]
        return src[n.start_byte:n.end_byte]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class E5Parens(_CRule):
    rule_id = "E5"; variants = ("a + b * c", "a + (b * c)")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "binary_expression" or len(n.children) != 3:
                continue
            pop = txt(n.children[1]); pp = PREC.get(pop)
            if pp is None:
                continue
            for child in (n.children[0], n.children[2]):
                inner, wrapped = child, False
                if child.type == "parenthesized_expression":
                    k = named_children(child)
                    if len(k) != 1:
                        continue
                    inner, wrapped = k[0], True
                if inner.type != "binary_expression" or len(inner.children) != 3:
                    continue
                ip = PREC.get(txt(inner.children[1]))
                if ip is None or ip >= pp:
                    continue
                yield Site(rule_id=self.rule_id, variant=1 if wrapped else 0,
                           span=(child.start_byte, child.end_byte), node_path=node_path(child),
                           meta={"inner": inner})

    def write_bytes(self, site, src, target):
        s, e = site.span
        if target == 1:
            return [Edit(s, e, b"(" + src[s:e] + b")")]
        i = site.meta["inner"]
        return [Edit(s, e, src[i.start_byte:i.end_byte])]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class E7DefaultArg(_CRule):
    rule_id = "E7"; variants = ("s.indexOf(c)", "s.indexOf(c, 0)")
    SPEC = {"indexOf": b"0", "lastIndexOf": None}

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "method_invocation":
                continue
            name = n.child_by_field_name("name")
            args = n.child_by_field_name("arguments")
            if name is None or args is None or txt(name) != "indexOf":
                continue
            obj = n.child_by_field_name("object")
            k = named_children(args)
            if len(k) == 1:
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"args": k, "obj": obj, "node": n})
            elif len(k) == 2 and k[1].type in NUM_LIT and txt(k[1]) == "0":
                yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"args": k, "obj": obj, "node": n})

    def guard(self, site, tree, src):
        obj = site.meta["obj"]
        if obj is None:
            return False
        if obj.type == "string_literal":
            return True
        if obj.type != "identifier":
            return False
        return base_type_name(declared_type(enclosing_body(site.meta["node"]), txt(obj))) == "String"

    def write_bytes(self, site, src, target):
        a = site.meta["args"]
        if target == 1:
            return [Edit(a[0].end_byte, a[0].end_byte, b", 0")]
        return [Edit(a[0].end_byte, a[1].end_byte, b"")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class E10SizeZero(_CRule):
    rule_id = "E10"; variants = ("v.size() > 0", "v.size() != 0")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "binary_expression" or len(n.children) != 3:
                continue
            l, op, r = n.children
            if txt(op) not in (">", "!=") or r.type not in NUM_LIT or txt(r) != "0":
                continue
            if not self._is_size(l):
                continue
            yield Site(rule_id=self.rule_id, variant=0 if txt(op) == ">" else 1,
                       span=(n.start_byte, n.end_byte), footprint=((op.start_byte, op.end_byte),),
                       node_path=node_path(n), meta={"op": op})

    @staticmethod
    def _is_size(n):
        if n.type == "method_invocation":
            nm = n.child_by_field_name("name")
            return nm is not None and txt(nm) in ("size", "length")
        if n.type == "call_expression":
            f = n.child_by_field_name("function")
            return f is not None and f.type == "field_expression" and txt(f.child_by_field_name("field")) in ("size", "length")
        if n.type == "field_access":
            return txt(n.child_by_field_name("field")) == "length"
        return False

    def write_bytes(self, site, src, target):
        op = site.meta["op"]
        return [Edit(op.start_byte, op.end_byte, b"!=" if target == 1 else b">")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class E17Increment(_CRule):
    rule_id = "E17"; variants = ("i++", "++i")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "update_expression":
                continue
            p = n.parent
            if p is None or p.type not in ("expression_statement", "for_statement"):
                continue
            kids = n.children
            if len(kids) != 2:
                continue
            pre = not kids[0].is_named
            arg = kids[1] if pre else kids[0]
            op = kids[0] if pre else kids[1]
            if arg.type != "identifier" or txt(op) not in ("++", "--"):
                continue
            yield Site(rule_id=self.rule_id, variant=1 if pre else 0,
                       span=(n.start_byte, n.end_byte), node_path=node_path(n),
                       meta={"arg": arg, "op": op})

    def write_bytes(self, site, src, target):
        a = src[site.meta["arg"].start_byte:site.meta["arg"].end_byte]
        o = src[site.meta["op"].start_byte:site.meta["op"].end_byte]
        return [Edit(*site.span, (o + a) if target == 1 else (a + o))]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class S1CompoundAssign(_CRule):
    rule_id = "S1"; variants = ("x += 1", "x = x + 1")
    AUG = {"+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "assignment_expression":
                continue
            l = n.child_by_field_name("left"); r = n.child_by_field_name("right")
            op = n.child_by_field_name("operator")
            if l is None or r is None or l.type != "identifier":
                continue
            opt = txt(op) if op is not None else self._op_token(n)
            if opt in self.AUG:
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"l": l, "op": op, "r": r, "bl": None})
            elif opt == "=" and r.type == "binary_expression" and len(r.children) == 3:
                bl = r.children[0]
                if bl.type == "identifier" and txt(bl) == txt(l) and txt(r.children[1]) + "=" in self.AUG:
                    eq = next((c for c in n.children if not c.is_named and c.type == "="), None)
                    if eq is not None:
                        yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, n.end_byte),
                                   node_path=node_path(n),
                                   meta={"l": l, "op": r.children[1], "r": r.children[2], "bl": bl, "eq": eq})

    @staticmethod
    def _op_token(n):
        for c in n.children:
            if not c.is_named and c.type not in ("(", ")"):
                return c.type
        return ""

    def guard(self, site, tree, src):
        name = txt(site.meta["l"])
        body = enclosing_body(site.meta["l"])
        if body is None or not self._declared_numeric(body, name):
            return False
        r = site.meta["r"]
        if r.type in NUM_LIT:
            return True
        if r.type == "identifier":
            return declared_type(body, name) == declared_type(body, txt(r)) is not None
        return False

    @staticmethod
    def _declared_numeric(body, name):
        stack = [body]
        while stack:
            n = stack.pop(); stack.extend(n.children)
            if n.type in ("local_variable_declaration", "declaration"):
                t = n.child_by_field_name("type")
                if t is None or txt(t).replace("unsigned ", "") not in PRIMITIVE_NUM:
                    continue
                for d in named_children(n):
                    nm = d
                    if d.type in ("variable_declarator", "init_declarator"):
                        nm = d.child_by_field_name("name") or d.child_by_field_name("declarator") or d
                    if nm is not None and nm.type == "identifier" and txt(nm) == name:
                        return True
        return False

    def write_bytes(self, site, src, target):
        l, op, r = site.meta["l"], site.meta["op"], site.meta["r"]
        name = src[l.start_byte:l.end_byte]; rhs = src[r.start_byte:r.end_byte]
        if site.variant == 0:
            sp1 = src[l.end_byte:op.start_byte]; sp2 = src[op.end_byte:r.start_byte]; base = txt(op)[:-1]
        else:
            eq, bl = site.meta["eq"], site.meta["bl"]
            sp1 = src[l.end_byte:eq.start_byte]; sp2 = src[eq.end_byte:bl.start_byte]; base = txt(op)
        if target == 1:
            new = name + sp1 + b"=" + sp2 + name + sp1 + base.encode() + sp2 + rhs
        else:
            new = name + sp1 + (base + "=").encode() + sp2 + rhs
        return [Edit(*site.span, new)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class S7InfiniteLoop(_CRule):
    rule_id = "S7"; variants = ("for (;;)", "while (true)")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type == "for_statement" and n.child_by_field_name("condition") is None \
               and n.child_by_field_name("initializer") is None and n.child_by_field_name("update") is None:
                body = n.child_by_field_name("body")
                if body is None:
                    continue
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, body.start_byte),
                           node_path=node_path(n), meta={})
            elif n.type == "while_statement":
                c, _ = cond_inner(n)
                body = n.child_by_field_name("body")
                if c is None or body is None or c.type != "true":
                    continue
                yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, body.start_byte),
                           node_path=node_path(n), meta={})

    def write_bytes(self, site, src, target):
        s, e = site.span
        tail = src[s:e]
        gap = tail[tail.rfind(b")") + 1:] if b")" in tail else b" "
        return [Edit(s, e, (b"while (true)" if target == 1 else b"for (;;)") + gap)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class S9FinalConst(_CRule):
    rule_id = "S9"; variants = ("int x = f();", "final int x = f();")
    KW = {"java": b"final ", "cpp": b"const "}

    def match(self, tree, src):
        decl_type = "local_variable_declaration" if self.language == "java" else "declaration"
        for n in walk(tree):
            if n.type != decl_type:
                continue
            if self.language == "cpp" and (n.parent is None or n.parent.type != "compound_statement"):
                continue
            inits = [d for d in named_children(n) if d.type in ("variable_declarator", "init_declarator")]
            if len(inits) != 1:
                continue
            d = inits[0]
            if d.child_by_field_name("value") is None and self.language == "java":
                continue
            nm = d.child_by_field_name("name") or d.child_by_field_name("declarator") or d
            if nm is None or nm.type != "identifier":
                continue
            mods = [m for m in named_children(n) if m.type in ("modifiers", "type_qualifier")]
            has_kw = any(self.KW[self.language].strip().decode() in txt(m) for m in mods)
            t = n.child_by_field_name("type")
            if t is None:
                continue
            yield Site(rule_id=self.rule_id, variant=1 if has_kw else 0,
                       span=(n.start_byte, n.end_byte), node_path=node_path(n),
                       meta={"name": txt(nm), "type": t, "mods": mods, "decl": n})

    def guard(self, site, tree, src):
        body = enclosing_body(site.meta["decl"])
        if body is None:
            return False
        if self.language == "cpp":
            t = site.meta["type"]
            if t.type != "primitive_type" or txt(t) not in (PRIMITIVE_NUM | {"bool", "char", "void"}):
                return False
            d = [x for x in named_children(site.meta["decl"]) if x.type == "init_declarator"]
            if d and any(c.type in ("pointer_declarator", "reference_declarator") for c in named_children(d[0])):
                return False
            if passed_by_nonconst_ref(tree, body, site.meta["name"]):
                return False
        return not reassigned_in(body, site.meta["name"])

    def write_bytes(self, site, src, target):
        kw = self.KW[self.language]
        t = site.meta["type"]
        if target == 1:
            return [Edit(t.start_byte, t.start_byte, kw)]
        m = [x for x in site.meta["mods"] if kw.strip().decode() in txt(x)][0]
        end = m.end_byte
        while end < len(src) and src[end:end + 1] in (b" ", b"\t"):
            end += 1
        return [Edit(m.start_byte, end, b"")]

    def footprint(self, site):
        return ((site.span[0], site.span[1]),)

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class S16ReturnParens(_CRule):
    rule_id = "S16"; variants = ("return x;", "return (x);")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "return_statement":
                continue
            k = named_children(n)
            if len(k) != 1:
                continue
            v = k[0]
            wrapped = v.type == "parenthesized_expression" and len(named_children(v)) == 1
            yield Site(rule_id=self.rule_id, variant=1 if wrapped else 0,
                       span=(v.start_byte, v.end_byte), node_path=node_path(v), meta={"v": v})

    def write_bytes(self, site, src, target):
        s, e = site.span
        if target == 1:
            return [Edit(s, e, b"(" + src[s:e] + b")")]
        return [Edit(s, e, src[s + 1:e - 1].strip())]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class T1BranchPolarity(_CRule):
    rule_id = "T1"; variants = ("if (c) A else B", "if (!c) B else A")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "if_statement":
                continue
            cond, span = cond_inner(n)
            alt = else_body(n); cons = n.child_by_field_name("consequence")
            if cond is None or alt is None or cons is None:
                continue
            if alt.type == "if_statement":
                continue
            neg = cond.type == "unary_expression" and txt(cond).startswith("!")
            yield Site(rule_id=self.rule_id, variant=1 if neg else 0,
                       span=(n.start_byte, n.end_byte),
                       footprint=(span, (cons.start_byte, cons.end_byte), (alt.start_byte, alt.end_byte)),
                       node_path=node_path(n), meta={"cond": cond, "cspan": span, "cons": cons, "alt": alt})

    def guard(self, site, tree, src):
        if not (is_block(site.meta["cons"]) and is_block(site.meta["alt"])):
            return False
        if site.variant == 1:
            return self._inner(site.meta["cond"]) is not None
        return True

    @staticmethod
    def _inner(cond):
        if cond.type != "unary_expression":
            return None
        a = cond.child_by_field_name("argument") or (named_children(cond)[0] if named_children(cond) else None)
        if a is None:
            return None
        if a.type == "parenthesized_expression":
            k = named_children(a)
            if len(k) != 1:
                return None
            return k[0] if needs_parens(k[0]) else a
        return a

    def write_bytes(self, site, src, target):
        cond, cspan, cons, alt = site.meta["cond"], site.meta["cspan"], site.meta["cons"], site.meta["alt"]
        if target == 1:
            body = src[cond.start_byte:cond.end_byte]
            if needs_parens(cond):
                body = b"(" + body + b")"
            new_cond = b"!" + body
        else:
            i = self._inner(cond)
            new_cond = src[i.start_byte:i.end_byte]
        return [Edit(cspan[0], cspan[1], new_cond),
                Edit(cons.start_byte, cons.end_byte, src[alt.start_byte:alt.end_byte]),
                Edit(alt.start_byte, alt.end_byte, src[cons.start_byte:cons.end_byte])]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class T11EmptyBody(_CRule):
    rule_id = "T11"; variants = ("while (c);", "while (c) {}")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type not in ("while_statement", "for_statement", "do_statement"):
                continue
            b = n.child_by_field_name("body")
            if b is None:
                continue
            if b.type in ("empty_statement",) or (b.type == "expression_statement" and not named_children(b)):
                yield Site(rule_id=self.rule_id, variant=0, span=(b.start_byte, b.end_byte), node_path=node_path(b))
            elif is_block(b) and not block_stmts(b):
                yield Site(rule_id=self.rule_id, variant=1, span=(b.start_byte, b.end_byte), node_path=node_path(b))

    def write_bytes(self, site, src, target):
        return [Edit(*site.span, b"{}" if target == 1 else b";")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class L2IsEmpty(_CRule):
    rule_id = "L2"; variants = ("c.isEmpty()", "c.size() == 0")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type == "method_invocation":
                nm = n.child_by_field_name("name"); args = n.child_by_field_name("arguments")
                obj = n.child_by_field_name("object")
                if nm is None or obj is None or txt(nm) != "isEmpty" or named_children(args or n):
                    continue
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"obj": obj, "node": n})
            elif n.type == "binary_expression" and len(n.children) == 3 and txt(n.children[1]) == "==":
                l, r = n.children[0], n.children[2]
                if r.type not in NUM_LIT or txt(r) != "0" or l.type != "method_invocation":
                    continue
                nm = l.child_by_field_name("name"); obj = l.child_by_field_name("object")
                if nm is None or obj is None or txt(nm) != "size":
                    continue
                yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, n.end_byte),
                           node_path=node_path(n), meta={"obj": obj, "node": n})

    def guard(self, site, tree, src):
        obj = site.meta["obj"]
        if obj is None or obj.type != "identifier":
            return False
        t = base_type_name(declared_type(enclosing_body(site.meta["node"]), txt(obj)))
        return t in COLLECTION_TYPES

    def write_bytes(self, site, src, target):
        o = src[site.meta["obj"].start_byte:site.meta["obj"].end_byte]
        if target == 1:
            p = site.meta["node"].parent
            body = o + b".size() == 0"
            if p is not None and p.type in ("unary_expression", "binary_expression", "field_access", "method_invocation"):
                body = b"(" + body + b")"
            return [Edit(*site.span, body)]
        return [Edit(*site.span, o + b".isEmpty()")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


JAVA_CLASSES = [E1Compare, E2Commutative, E3DeMorgan, E5Parens, E7DefaultArg, E10SizeZero,
                E17Increment, S1CompoundAssign, S7InfiniteLoop, S9FinalConst, S16ReturnParens,
                T1BranchPolarity, T11EmptyBody, L2IsEmpty]
CPP_CLASSES = [E1Compare, E2Commutative, E3DeMorgan, E5Parens, E10SizeZero, E17Increment,
               S1CompoundAssign, S7InfiniteLoop, S9FinalConst, T1BranchPolarity, T11EmptyBody]

JAVA_RULES = {c.rule_id: c("java") for c in JAVA_CLASSES}
CPP_RULES = {c.rule_id: c("cpp") for c in CPP_CLASSES}
