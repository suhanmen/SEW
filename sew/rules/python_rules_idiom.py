from .base import Edit, Rule, Site, named_children, node_path
from .python_rules import txt, is_pure, line_indent, starts_own_line
from .python_rules_extra import walk, block_stmts, next_sibling_stmt, separator_ok, identifiers_in, shadowed_names, is_call_to, positional_args
from .python_rules_control import indent_unit, has_comment, single_line, clean_tail, T2CondAssign

BAD_EXPR = {"named_expression", "yield", "await", "lambda", "list_splat", "dictionary_splat"}


def _contains_type(node, types):
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in types:
            return True
        stack.extend(n.children)
    return False


def _scope(node):
    n = node
    while n is not None and n.type != "function_definition":
        n = n.parent
    return n if n is not None else _root(node)


def _root(node):
    while node.parent is not None:
        node = node.parent
    return node


def _names_used_outside(scope, span, names):
    stack = [scope]
    while stack:
        n = stack.pop()
        if n.type == "identifier" and txt(n) in names and not (span[0] <= n.start_byte and n.end_byte <= span[1]):
            return True
        stack.extend(n.children)
    return False


def _target_names(pat):
    if pat.type == "identifier":
        return {txt(pat)}
    if pat.type in ("pattern_list", "tuple_pattern", "tuple"):
        k = named_children(pat)
        if k and all(c.type == "identifier" for c in k):
            return {txt(c) for c in k}
    return None


def _ok_piece(src, n):
    return n is not None and single_line(src, n) and not _contains_type(n, BAD_EXPR) and not has_comment(n)


def _return_of(stmt, lit):
    if stmt.type != "return_statement":
        return False
    k = named_children(stmt)
    return len(k) == 1 and k[0].type == lit


def _stmt_ok(src, n):
    return starts_own_line(src, n) and clean_tail(src, n) and not has_comment(n)


class _SC(Rule):
    language = "python"


class SC4ListComp(_SC):
    rule_id = "SC4"
    variants = ("r = [f(i) for i in it]", "r = []; for i in it: r.append(f(i))")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type != "expression_statement":
                continue
            k = named_children(n)
            if len(k) != 1 or k[0].type != "assignment":
                continue
            a = k[0]
            left, right = a.child_by_field_name("left"), a.child_by_field_name("right")
            if left is None or right is None or a.child_by_field_name("type") is not None or left.type != "identifier":
                continue
            if right.type == "list_comprehension":
                kk = named_children(right)
                if len(kk) < 2 or len(kk) > 3:
                    continue
                body, fc = kk[0], kk[1]
                if fc.type != "for_in_clause":
                    continue
                cond = None
                if len(kk) == 3:
                    if kk[2].type != "if_clause":
                        continue
                    ic = named_children(kk[2])
                    if len(ic) != 1:
                        continue
                    cond = ic[0]
                tgt, it = fc.child_by_field_name("left"), fc.child_by_field_name("right")
                if tgt is None or it is None:
                    continue
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte), node_path=node_path(n),
                           meta={"name": txt(left), "body": body, "tgt": tgt, "it": it, "cond": cond, "node": n, "stmts": (n,)})
            elif right.type == "list" and not named_children(right):
                m = next_sibling_stmt(n)
                if m is None or m.type != "for_statement" or not separator_ok(src, n, m):
                    continue
                if any(c.type == "else_clause" for c in named_children(m)):
                    continue
                tgt, it, body_b = m.child_by_field_name("left"), m.child_by_field_name("right"), m.child_by_field_name("body")
                if tgt is None or it is None or body_b is None:
                    continue
                st = block_stmts(body_b)
                if len(st) != 1:
                    continue
                cond, app = None, st[0]
                if app.type == "if_statement":
                    if any(c.type in ("else_clause", "elif_clause") for c in named_children(app)):
                        continue
                    cond = app.child_by_field_name("condition")
                    cb = app.child_by_field_name("consequence")
                    if cond is None or cb is None:
                        continue
                    st2 = block_stmts(cb)
                    if len(st2) != 1:
                        continue
                    app = st2[0]
                arg = self._append_arg(app, txt(left))
                if arg is None:
                    continue
                yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, m.end_byte), node_path=node_path(n),
                           meta={"name": txt(left), "body": arg, "tgt": tgt, "it": it, "cond": cond, "node": m, "stmts": (n, m, app)})

    @staticmethod
    def _append_arg(stmt, name):
        if stmt.type != "expression_statement":
            return None
        k = named_children(stmt)
        if len(k) != 1 or k[0].type != "call":
            return None
        call = k[0]
        f = call.child_by_field_name("function")
        if f is None or f.type != "attribute":
            return None
        obj, attr = f.child_by_field_name("object"), f.child_by_field_name("attribute")
        if obj is None or attr is None or obj.type != "identifier" or txt(obj) != name or txt(attr) != "append":
            return None
        args = positional_args(call)
        if args is None or len(args) != 1:
            return None
        return args[0]

    def guard(self, site, tree, src):
        m = site.meta
        for st in m["stmts"]:
            if not _stmt_ok(src, st):
                return False
        if not (_ok_piece(src, m["body"]) and _ok_piece(src, m["it"]) and (m["cond"] is None or _ok_piece(src, m["cond"]))):
            return False
        names = _target_names(m["tgt"])
        if not names or m["name"] in names:
            return False
        refs = identifiers_in(m["body"]) | identifiers_in(m["it"]) | (identifiers_in(m["cond"]) if m["cond"] is not None else set())
        if m["name"] in refs:
            return False
        if m["it"].type == "tuple":
            return False
        return not _names_used_outside(_scope(m["node"]), site.span, names)

    def write_bytes(self, site, src, target):
        m = site.meta
        g = lambda x: src[x.start_byte:x.end_byte]
        name = m["name"].encode(); body, tgt, it = g(m["body"]), g(m["tgt"]), g(m["it"])
        cond = g(m["cond"]) if m["cond"] is not None else None
        s, e = site.span
        if target == 0:
            new = name + b" = [" + body + b" for " + tgt + b" in " + it + (b" if " + cond if cond else b"") + b"]"
        else:
            _, ind = line_indent(src, s); u = indent_unit(src)
            new = name + b" = []\n" + ind + b"for " + tgt + b" in " + it + b":\n"
            if cond:
                new += ind + u + b"if " + cond + b":\n" + ind + u + u + name + b".append(" + body + b")"
            else:
                new += ind + u + name + b".append(" + body + b")"
        return [Edit(s, e, new)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


class SC16UseAny(_SC):
    rule_id = "SC16"
    variants = ("return any(c for i in it)", "for i in it: if c: return True; return False")
    sid_mode = "parent"

    def match(self, tree, src):
        for n in walk(tree):
            if n.type == "return_statement":
                k = named_children(n)
                if len(k) != 1 or not is_call_to(k[0], "any"):
                    continue
                argn = k[0].child_by_field_name("arguments")
                if argn is None:
                    continue
                if argn.type == "generator_expression":
                    gen = argn
                else:
                    args = positional_args(k[0])
                    if args is None or len(args) != 1 or args[0].type != "generator_expression":
                        continue
                    gen = args[0]
                kk = named_children(gen)
                if len(kk) != 2 or kk[1].type != "for_in_clause":
                    continue
                cond, fc = kk
                tgt, it = fc.child_by_field_name("left"), fc.child_by_field_name("right")
                if tgt is None or it is None:
                    continue
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte), node_path=node_path(n),
                           meta={"cond": cond, "tgt": tgt, "it": it, "node": n, "stmts": (n,)})
            elif n.type == "for_statement":
                if any(c.type == "else_clause" for c in named_children(n)):
                    continue
                tgt, it, body_b = n.child_by_field_name("left"), n.child_by_field_name("right"), n.child_by_field_name("body")
                if tgt is None or it is None or body_b is None:
                    continue
                st = block_stmts(body_b)
                if len(st) != 1 or st[0].type != "if_statement":
                    continue
                ifs = st[0]
                if any(c.type in ("else_clause", "elif_clause") for c in named_children(ifs)):
                    continue
                cond, cb = ifs.child_by_field_name("condition"), ifs.child_by_field_name("consequence")
                if cond is None or cb is None:
                    continue
                st2 = block_stmts(cb)
                if len(st2) != 1 or not _return_of(st2[0], "true"):
                    continue
                m = next_sibling_stmt(n)
                if m is None or not _return_of(m, "false") or not separator_ok(src, n, m):
                    continue
                yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, m.end_byte), node_path=node_path(n),
                           meta={"cond": cond, "tgt": tgt, "it": it, "node": n, "stmts": (n, ifs, st2[0], m)})

    def guard(self, site, tree, src):
        m = site.meta
        if site.variant == 0 and "any" in shadowed_names(tree):
            return False
        for st in m["stmts"]:
            if not _stmt_ok(src, st):
                return False
        if not (_ok_piece(src, m["cond"]) and _ok_piece(src, m["it"])):
            return False
        names = _target_names(m["tgt"])
        if not names or m["it"].type == "tuple":
            return False
        return not _names_used_outside(_scope(m["node"]), site.span, names)

    def write_bytes(self, site, src, target):
        m = site.meta
        g = lambda x: src[x.start_byte:x.end_byte]
        cond, tgt, it = g(m["cond"]), g(m["tgt"]), g(m["it"])
        s, e = site.span
        if target == 0:
            new = b"return any(" + cond + b" for " + tgt + b" in " + it + b")"
        else:
            _, ind = line_indent(src, s); u = indent_unit(src)
            new = (b"for " + tgt + b" in " + it + b":\n" + ind + u + b"if " + cond + b":\n" + ind + u + u + b"return True\n"
                   + ind + b"return False")
        return [Edit(s, e, new)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


SAFE_PARENTS = {"if_statement", "while_statement", "elif_clause", "expression_statement", "assignment", "return_statement",
                "parenthesized_expression", "conditional_expression", "argument_list", "keyword_argument", "boolean_operator"}


def _parent_ok(node, allow_and=False):
    p = node.parent
    if p is None or p.type not in SAFE_PARENTS:
        return False
    if p.type == "boolean_operator":
        op = [c for c in p.children if not c.is_named]
        return bool(op) and (txt(op[0]) == "or" or (allow_and and txt(op[0]) == "and"))
    if p.type in ("if_statement", "while_statement", "elif_clause"):
        return p.child_by_field_name("condition") == node
    if p.type == "assignment":
        return p.child_by_field_name("right") == node
    if p.type == "conditional_expression":
        k = named_children(p)
        return len(k) == 3 and k[1] == node
    return True


class SC6MergeComparisons(_SC):
    rule_id = "SC6"
    variants = ("x in (a, b)", "x == a or x == b")

    def match(self, tree, src):
        for n in walk(tree):
            if n.type == "comparison_operator":
                ops = [c for c in n.children if not c.is_named]
                kids = named_children(n)
                if len(ops) != 1 or txt(ops[0]) != "in" or len(kids) != 2 or kids[1].type != "tuple":
                    continue
                elems = named_children(kids[1])
                if len(elems) < 2:
                    continue
                yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte), node_path=node_path(n),
                           meta={"x": kids[0], "elems": elems, "node": n})
            elif n.type == "boolean_operator":
                op = [c for c in n.children if not c.is_named]
                if not op or txt(op[0]) != "or":
                    continue
                p = n.parent
                if p is not None and p.type == "boolean_operator":
                    pop = [c for c in p.children if not c.is_named]
                    if pop and txt(pop[0]) == "or":
                        continue
                terms = self._flatten(n)
                if terms is None or len(terms) < 2:
                    continue
                xs, rs = [], []
                for t in terms:
                    if t.type != "comparison_operator":
                        terms = None; break
                    tops = [c for c in t.children if not c.is_named]; tk = named_children(t)
                    if len(tops) != 1 or txt(tops[0]) != "==" or len(tk) != 2:
                        terms = None; break
                    xs.append(tk[0]); rs.append(tk[1])
                if terms is None or len({src[x.start_byte:x.end_byte] for x in xs}) != 1:
                    continue
                yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, n.end_byte), node_path=node_path(n),
                           meta={"x": xs[0], "elems": rs, "node": n})

    @staticmethod
    def _flatten(n):
        out = []
        cur = n
        while True:
            op = [c for c in cur.children if not c.is_named]
            k = named_children(cur)
            if cur.type != "boolean_operator" or not op or txt(op[0]) != "or" or len(k) != 2:
                out.append(cur); break
            out.append(k[1]); cur = k[0]
        return list(reversed(out))

    def guard(self, site, tree, src):
        m = site.meta
        x = m["x"]
        if x.type not in ("identifier", "attribute") or not is_pure(x):
            return False
        if not all(is_pure(e) for e in m["elems"]):
            return False
        if has_comment(m["node"]) or not single_line(src, m["node"]):
            return False
        return _parent_ok(m["node"])

    def write_bytes(self, site, src, target):
        m = site.meta
        g = lambda x: src[x.start_byte:x.end_byte]
        x = g(m["x"]); es = [g(e) for e in m["elems"]]
        s, e = site.span
        new = x + b" in (" + b", ".join(es) + b")" if target == 0 else b" or ".join(x + b" == " + v for v in es)
        return [Edit(s, e, new)]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


CONTAINER_LITERAL = {"list", "dictionary", "set", "string", "concatenated_string", "tuple", "list_comprehension", "dictionary_comprehension", "set_comprehension"}
CONTAINER_CALLS = {"list", "dict", "set", "str", "sorted", "input", "tuple", "frozenset"}
CONTAINER_METHODS = {"split", "strip", "keys", "values", "items", "copy", "lower", "upper", "splitlines"}
CONTAINER_ANN = {"list", "dict", "set", "str", "tuple", "List", "Dict", "Set", "Tuple", "Sequence", "Mapping"}


def _container_rhs(r):
    if r is None:
        return False
    if r.type in CONTAINER_LITERAL:
        return True
    if r.type == "call":
        f = r.child_by_field_name("function")
        if f is not None and f.type == "identifier" and txt(f) in CONTAINER_CALLS:
            return True
        if f is not None and f.type == "attribute":
            a = f.child_by_field_name("attribute")
            return a is not None and txt(a) in CONTAINER_METHODS
    return False


def _container_ann(ann):
    if ann is None:
        return False
    if ann.type == "identifier":
        return txt(ann) in CONTAINER_ANN
    if ann.type in ("generic_type", "subscript"):
        k = named_children(ann)
        return bool(k) and k[0].type == "identifier" and txt(k[0]) in CONTAINER_ANN
    return False


def _is_container_name(scope, name):
    evidence, counter = 0, 0
    stack = [scope]
    while stack:
        n = stack.pop()
        stack.extend(n.children)
        if n.type == "assignment":
            l = n.child_by_field_name("left")
            if l is not None and l.type == "identifier" and txt(l) == name:
                if n.child_by_field_name("type") is not None and _container_ann(n.child_by_field_name("type")):
                    evidence += 1
                elif _container_rhs(n.child_by_field_name("right")):
                    evidence += 1
                else:
                    counter += 1
            elif l is not None and l.type in ("pattern_list", "tuple_pattern") and name in {txt(c) for c in named_children(l) if c.type == "identifier"}:
                counter += 1
        elif n.type == "augmented_assignment":
            l = n.child_by_field_name("left")
            if l is not None and l.type == "identifier" and txt(l) == name:
                counter += 1
        elif n.type == "typed_parameter":
            k = named_children(n)
            if k and k[0].type == "identifier" and txt(k[0]) == name:
                if _container_ann(n.child_by_field_name("type")):
                    evidence += 1
                else:
                    counter += 1
        elif n.type == "identifier" and n.parent is not None and n.parent.type == "parameters" and txt(n) == name:
            counter += 1
        elif n.type in ("for_statement", "for_in_clause"):
            l = n.child_by_field_name("left")
            if l is not None and name in identifiers_in(l):
                counter += 1
        elif n.type == "with_statement" or n.type == "except_clause" or n.type == "global_statement" or n.type == "nonlocal_statement":
            if name in identifiers_in(n) and n.type != "with_statement":
                counter += 1
    return evidence >= 1 and counter == 0


class SC14LenEmpty(_SC):
    rule_id = "SC14"
    variants = ("not x", "len(x) == 0")
    SAFE = SAFE_PARENTS | {"not_operator"}

    def match(self, tree, src):
        for n in walk(tree):
            if n.type == "not_operator":
                k = named_children(n)
                if len(k) == 1 and k[0].type == "identifier":
                    yield Site(rule_id=self.rule_id, variant=0, span=(n.start_byte, n.end_byte), node_path=node_path(n), meta={"x": k[0], "node": n})
            elif n.type == "comparison_operator" and len(n.children) == 3:
                left, op, right = n.children
                if txt(op) != "==" or not is_call_to(left, "len") or right.type != "integer" or txt(right) != "0":
                    continue
                args = positional_args(left)
                if args is None or len(args) != 1 or args[0].type != "identifier":
                    continue
                yield Site(rule_id=self.rule_id, variant=1, span=(n.start_byte, n.end_byte), node_path=node_path(n), meta={"x": args[0], "node": n})

    def guard(self, site, tree, src):
        m = site.meta
        if "len" in shadowed_names(tree):
            return False
        p = m["node"].parent
        if p is None or p.type not in self.SAFE:
            return False
        if p.type == "boolean_operator":
            pass
        elif not _parent_ok(m["node"], allow_and=True) and p.type != "not_operator":
            return False
        return _is_container_name(_scope(m["node"]), txt(m["x"]))

    def write_bytes(self, site, src, target):
        x = src[site.meta["x"].start_byte:site.meta["x"].end_byte]
        s, e = site.span
        return [Edit(s, e, b"not " + x if target == 0 else b"len(" + x + b") == 0")]

    def erase_bytes(self, site, src):
        return self.write_bytes(site, src, 0) if site.variant == 1 else []


RULES = {r.rule_id: r for r in (SC4ListComp(), SC16UseAny(), SC6MergeComparisons(), SC14LenEmpty(), T2CondAssign())}
