"""Code-editing attacks that do not know the rule set.

External tools (versions used for the reported numbers):
  black 26.5.1, ruff 0.16.6 (pip), clang-tidy 22.1.8 (pip install clang-tidy),
  Error Prone 2.50.0 with JDK 21 (see ``atk_errorprone`` for the two jars).
Tree-sitter based attacks (comment removal, renaming, Java/C++ re-formatting) need nothing else.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from sew.rules.base import parse

_COMMENT_TYPES = {"comment", "line_comment", "block_comment"}


# ------------------------------------------------------------------ formatting

def atk_black(code):
    import black
    return black.format_str(code, mode=black.Mode())


def atk_format(code, language="python"):
    """Python: black. Java/C++: token re-emission with 4-space indent, one space around
    operators and K&R braces (no external formatter was available for these languages)."""
    if language == "python":
        return atk_black(code)
    return atk_reformat(code, language)


# ------------------------------------------------------------------ linting

def atk_ruff(code):
    """``ruff check --fix --unsafe-fixes --isolated`` (default rule set)."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        p = f.name
    subprocess.run(["ruff", "check", "--fix", "--unsafe-fixes", "--quiet", "--isolated", p],
                   capture_output=True, text=True, timeout=120)
    out = open(p, encoding="utf-8").read()
    os.unlink(p)
    return out


def atk_clang_tidy(code):
    """``clang-tidy --fix --fix-errors`` with the tool's default checks."""
    if shutil.which("clang-tidy") is None:
        raise RuntimeError("clang-tidy is not on PATH (pip install clang-tidy)")
    d = tempfile.mkdtemp(prefix="ct_")
    p = os.path.join(d, "a.cpp")
    open(p, "w", encoding="utf-8").write(code)
    try:
        subprocess.run(["clang-tidy", "--fix", "--fix-errors", "--quiet", p, "--", "-std=c++17"],
                       capture_output=True, text=True, timeout=120)
        out = open(p, encoding="utf-8").read()
    except Exception:
        out = code
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return out or code


# Error Prone patch mode. Two jars from Maven Central are needed, placed in $ERRORPRONE_DIR:
#   com/google/errorprone/error_prone_core/2.50.0/error_prone_core-2.50.0-with-dependencies.jar
#   io/github/eisop/dataflow-errorprone/3.41.0-eisop1/dataflow-errorprone-3.41.0-eisop1.jar
# ``errorprone_checks_2.50.0.txt`` lists the checks of that release (E = error, W = warning,
# D = disabled by default). The default level applies all E and W checks that have a fix.
EP_DIR = Path(os.environ.get("ERRORPRONE_DIR", str(Path(__file__).resolve().parent / "errorprone")))
EP_JARS = [EP_DIR / "error_prone_core-2.50.0-with-dependencies.jar", EP_DIR / "dataflow-errorprone-3.41.0-eisop1.jar"]
_EP_CHECKS_FILE = Path(__file__).resolve().parent / "errorprone_checks_2.50.0.txt"
# Checks whose fixes add imports of external annotations (jspecify, errorprone annotations)
# and therefore break single-file compilation are excluded.
_EP_EXCLUDE = ("Nullable", "NullMarked", "Suggester", "Immutable", "Inject", "Annotate", "CanIgnoreReturnValue",
               "CheckReturnValue", "MustBeClosed", "ThreadSafe", "RestrictedApi", "FormatMethod", "InlineMe",
               "DoNotCall", "Keep")
_EP_CHECKS = {}


def _ep_checks(level):
    if level not in _EP_CHECKS:
        rows = [l.split() for l in _EP_CHECKS_FILE.read_text().splitlines() if l.strip()]
        _EP_CHECKS[level] = [n for k, n in rows if (k in ("E", "W") or level == "max") and n != "Var"
                             and not any(x in n for x in _EP_EXCLUDE)]
    return _EP_CHECKS[level]


def atk_errorprone(code, level="default"):
    """Error Prone with ``-XepPatchChecks`` applied in place. Files that do not compile are
    returned unchanged, and so is a patch that no longer compiles."""
    if not all(j.exists() for j in EP_JARS) or shutil.which("javac") is None:
        raise RuntimeError(f"Error Prone jars not found in {EP_DIR} or javac is not on PATH")
    st = atk_errorprone.stats
    st["n"] = st.get("n", 0) + 1
    d = tempfile.mkdtemp(prefix="ep_")
    p = os.path.join(d, "Main.java")
    open(p, "w", encoding="utf-8").write(code)
    J = [f"-J--add-exports=jdk.compiler/com.sun.tools.javac.{m}=ALL-UNNAMED"
         for m in ("api", "file", "main", "model", "parser", "processing", "tree", "util")] + \
        [f"-J--add-opens=jdk.compiler/com.sun.tools.javac.{m}=ALL-UNNAMED" for m in ("code", "comp")]
    plugin = "-Xplugin:ErrorProne" + (" -XepAllDisabledChecksAsWarnings" if level == "max" else "") + \
             " -XepPatchChecks:" + ",".join(_ep_checks(level)) + " -XepPatchLocation:IN_PLACE"
    cmd = ["javac"] + J + ["-XDaddTypeAnnotationsToSymbol=true", "-XDcompilePolicy=simple",
                           "--should-stop=ifError=FLOW", "-encoding", "UTF-8",
                           "-processorpath", os.pathsep.join(str(j) for j in EP_JARS), plugin, "-d", d, p]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        out = open(p, encoding="utf-8").read()
        if "error:" in r.stderr and out == code:
            st["compile_fail"] = st.get("compile_fail", 0) + 1
        elif out != code:
            for f in os.listdir(d):
                if f.endswith(".class"):
                    os.unlink(os.path.join(d, f))
            r2 = subprocess.run(["javac", "-encoding", "UTF-8", "-d", d, p], capture_output=True, text=True, timeout=300)
            if r2.returncode != 0:
                out = code
                st["patch_broke"] = st.get("patch_broke", 0) + 1
            else:
                st["changed"] = st.get("changed", 0) + 1
    except Exception:
        out = code
        st["exception"] = st.get("exception", 0) + 1
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return out or code


atk_errorprone.stats = {}


def atk_lint(code, language="python"):
    if language == "python":
        return atk_ruff(code)
    if language == "java":
        return atk_errorprone(code, "default")
    return atk_clang_tidy(code)


# ------------------------------------------------------------------ comment removal

def atk_strip_comments(code, language="python"):
    """Deletes every comment node. A comment that fills a line is removed with the line;
    a trailing comment is removed with the whitespace before it."""
    tree, data = parse(code.encode(), language)
    if tree is None:
        return code
    spans = []

    def walk(n):
        if n.type in _COMMENT_TYPES:
            spans.append((n.start_byte, n.end_byte))
        for c in n.children:
            walk(c)
    walk(tree.root_node)
    out = bytearray(data)
    for s, e in sorted(spans, reverse=True):
        ls = data.rfind(b"\n", 0, s) + 1
        if data[ls:s].strip() == b"":
            le = data.find(b"\n", e)
            le = len(data) if le < 0 else le + 1
            del out[ls:le]
        else:
            k = s
            while k > 0 and data[k - 1:k] in (b" ", b"\t"):
                k -= 1
            del out[k:e]
    return out.decode()


# ------------------------------------------------------------------ renaming

PY_KW = set("False None True and as assert async await break class continue def del elif else except finally for "
            "from global if import in is lambda nonlocal not or pass raise return try while with yield self cls print "
            "len range int str float list dict set tuple bool sorted enumerate zip map filter sum min max abs any all "
            "isinstance type Exception ValueError TypeError".split())


def atk_rename(code, language="python"):
    """Renames parameters and local variables of every top-level function to v0, v1, ...
    One injective mapping per outermost function also covers its nested functions and lambdas,
    so no two distinct names collapse into one."""
    if language in ("java", "cpp"):
        return _rename_c_family(code, language)
    tree, data = parse(code.encode(), "python")
    if tree is None:
        return code
    edits = []

    def funcs(n):
        if n.type == "function_definition":
            yield n
            return
        for c in n.children:
            yield from funcs(c)

    for f in funcs(tree.root_node):
        bound = []

        def collect_params(n):
            if n.type == "identifier":
                bound.append(data[n.start_byte:n.end_byte].decode())
            for c in n.children:
                if c.type not in ("type", "default_value"):
                    collect_params(c)

        def all_params(n):
            if n.type in ("function_definition", "lambda"):
                p = n.child_by_field_name("parameters")
                if p is not None:
                    collect_params(p)
            for c in n.children:
                all_params(c)
        all_params(f)
        body = f.child_by_field_name("body")

        def collect_assign(n):
            if n.type in ("assignment", "augmented_assignment", "for_statement", "named_expression"):
                l = n.child_by_field_name("left")
                if l is not None:
                    for x in ([l] if l.type == "identifier" else [c for c in l.children if c.type == "identifier"]):
                        bound.append(data[x.start_byte:x.end_byte].decode())
            for c in n.children:
                collect_assign(c)
        if body:
            collect_assign(body)
        names = [x for x in dict.fromkeys(bound) if x not in PY_KW and not x.startswith("__")]
        if not names:
            continue
        mp = {x: f"v{i}" for i, x in enumerate(names)}

        def rep(n):
            if n.type == "identifier" and n.parent is not None and n.parent.type not in ("attribute", "keyword_argument") \
                    or (n.type == "identifier" and n.parent is not None and n.parent.type == "attribute"
                        and n.parent.child_by_field_name("object") == n):
                t = data[n.start_byte:n.end_byte].decode()
                if t in mp:
                    edits.append((n.start_byte, n.end_byte, mp[t].encode()))
            for c in n.children:
                rep(c)
        rep(f)
    uniq = {}
    for s, e, new in edits:
        uniq[(s, e)] = new
    out = bytearray(data)
    for (s, e), new in sorted(uniq.items(), reverse=True):
        out[s:e] = new
    return out.decode()


# Names that are never renamed in Java/C++ even if they are bound locally.
C_KEEP = set("""this super null true false new main String Integer Double Long Boolean Character Object List ArrayList Map HashMap
Set HashSet Math System Arrays Collections Collectors Stream StringBuilder Comparable Comparator Exception Optional
std vector string map set pair size_t cout cin endl printf scanf sort abs max min swap begin end push_back nullptr
NULL int long short float double char bool void auto unsigned signed const static return if else for while do""".split())


def _c_type_like_names(f, data):
    """Names that also appear in a type position (C++ most vexing parse: ``vector<bool> seen(n, false);``)."""
    out = set()

    def walk(n):
        if n.type in ("type_identifier", "namespace_identifier", "sized_type_specifier", "primitive_type"):
            out.add(data[n.start_byte:n.end_byte].decode())
        for c in n.children:
            walk(c)
    walk(f)
    return out


def _c_callee_names(f, data, language):
    """Names used as a method/field/function name; a local with the same name is left alone."""
    out = set()

    def walk(n):
        if n.type == "identifier":
            p = n.parent
            if p is not None:
                if language == "java" and p.type == "method_invocation" and p.child_by_field_name("name") is not None \
                        and p.child_by_field_name("name").id == n.id:
                    out.add(data[n.start_byte:n.end_byte].decode())
                if language == "java" and p.type == "field_access" and p.child_by_field_name("field") is not None \
                        and p.child_by_field_name("field").id == n.id:
                    out.add(data[n.start_byte:n.end_byte].decode())
                if language == "cpp" and p.type == "call_expression" and p.child_by_field_name("function") is not None \
                        and p.child_by_field_name("function").id == n.id:
                    out.add(data[n.start_byte:n.end_byte].decode())
        for c in n.children:
            walk(c)
    walk(f)
    return out


_C_DECL_WRAP = {"init_declarator", "pointer_declarator", "reference_declarator", "array_declarator",
                "parenthesized_declarator"}


def _c_decl_ident(n):
    seen = 0
    while n is not None and seen < 8:
        seen += 1
        if n.type == "identifier":
            return n
        if n.type in _C_DECL_WRAP:
            nxt = n.child_by_field_name("declarator")
            if nxt is None:
                nxt = next((c for c in n.children if c.is_named), None)
            n = nxt
            continue
        return None
    return None


def _c_bound_names(f, data, language):
    """Parameters and local declarations of function ``f`` in declaration order."""
    names = []

    def add(n):
        if n is not None and n.type == "identifier":
            names.append(data[n.start_byte:n.end_byte].decode())

    def walk(n):
        t = n.type
        if language == "java":
            if t in ("formal_parameter", "catch_formal_parameter", "spread_parameter"):
                add(n.child_by_field_name("name"))
            elif t == "variable_declarator" and n.parent is not None and n.parent.type == "local_variable_declaration":
                add(n.child_by_field_name("name"))
            elif t == "enhanced_for_statement":
                add(n.child_by_field_name("name"))
            elif t == "inferred_parameters":
                for c in n.children:
                    add(c)
            elif t == "lambda_expression":
                p = n.child_by_field_name("parameters")
                if p is not None and p.type == "identifier":
                    add(p)
        else:
            if t in ("parameter_declaration", "optional_parameter_declaration"):
                add(_c_decl_ident(n.child_by_field_name("declarator")))
            elif t == "declaration":
                for c in n.children_by_field_name("declarator"):
                    add(_c_decl_ident(c))
            elif t == "for_range_loop":
                add(_c_decl_ident(n.child_by_field_name("declarator")))
            elif t == "structured_binding_declarator":
                for c in n.children:
                    add(c if c.type == "identifier" else None)
        for c in n.children:
            walk(c)

    walk(f)
    return names


def _c_replaceable(n, language):
    p = n.parent
    if p is None:
        return False
    t = p.type
    if t.startswith("preproc"):
        return False
    if language == "java":
        if t in ("scoped_identifier", "scoped_type_identifier", "method_declaration", "constructor_declaration",
                 "class_declaration", "interface_declaration", "enum_declaration", "import_declaration",
                 "package_declaration", "annotation", "marker_annotation", "type_identifier"):
            return False
        if t == "method_invocation" and p.child_by_field_name("name") is not None and p.child_by_field_name("name").id == n.id:
            return False
        if t == "field_access" and p.child_by_field_name("field") is not None and p.child_by_field_name("field").id == n.id:
            return False
    else:
        if t in ("qualified_identifier", "template_function", "using_declaration", "namespace_definition",
                 "function_declarator", "destructor_name", "field_designator"):
            return False
        if t == "call_expression" and p.child_by_field_name("function") is not None and p.child_by_field_name("function").id == n.id:
            return False
    a = p
    while a is not None:
        if a.type.startswith("preproc"):
            return False
        a = a.parent
    return True


_C_FUNC_NODES = {"java": ("method_declaration", "constructor_declaration"), "cpp": ("function_definition",)}


def _rename_c_family(code, language):
    tree, data = parse(code.encode(), language)
    if tree is None:
        return code
    edits = []
    fn_types = _C_FUNC_NODES[language]

    def funcs(n):
        if n.type in fn_types:
            yield n
            return
        for c in n.children:
            yield from funcs(c)

    for f in funcs(tree.root_node):
        callee = _c_callee_names(f, data, language) | _c_type_like_names(f, data)
        names = [x for x in dict.fromkeys(_c_bound_names(f, data, language))
                 if x not in C_KEEP and x not in callee and not x.startswith("_") and not re.fullmatch(r"v\d+", x)]
        if not names:
            continue
        mp = {x: f"v{i}" for i, x in enumerate(names)}

        def rep(n):
            if n.type == "identifier" and _c_replaceable(n, language):
                t = data[n.start_byte:n.end_byte].decode()
                if t in mp:
                    edits.append((n.start_byte, n.end_byte, mp[t].encode()))
            for c in n.children:
                rep(c)
        rep(f)
    out = bytearray(data)
    for s, e, new in sorted(set(edits), reverse=True):
        out[s:e] = new
    return out.decode()


# ------------------------------------------------------------------ Java / C++ re-formatting

# The result is used only if re-parsing it yields exactly the same leaf-token sequence as the
# input; otherwise the input is returned unchanged (counted in ``atk_reformat.stats``).
_ATOMIC = {
    "java": {"comment", "line_comment", "block_comment", "string_literal", "character_literal", "text_block",
             "scoped_identifier", "scoped_type_identifier", "generic_type", "type_arguments", "annotation",
             "marker_annotation", "import_declaration", "package_declaration"},
    "cpp": {"comment", "string_literal", "raw_string_literal", "char_literal", "concatenated_string",
            "system_lib_string", "template_type", "template_argument_list", "template_function",
            "qualified_identifier", "preproc_include", "preproc_def", "preproc_function_def", "preproc_call",
            "using_declaration", "attribute_declaration"},
}
_BAIL = {"preproc_if", "preproc_ifdef", "preproc_else", "preproc_elif", "preproc_elifdef", "ERROR"}
_FORCE_NL = {"comment", "line_comment", "block_comment", "import_declaration", "package_declaration",
             "using_declaration", "preproc_include", "preproc_def", "preproc_function_def", "preproc_call"}
_INIT_BRACE_PARENT = {"initializer_list", "array_initializer", "initializer_pair", "subscript_argument_list"}
_KW_BEFORE_PAREN = {"if", "for", "while", "switch", "catch", "return", "synchronized", "do", "else", "sizeof"}
_KEEP_SAME_LINE = {"else", "catch", "finally", "while", ";", ",", ")", "]"}
_ATTACH_LEFT = {")", "]", ";", ",", ".", "->", "::"}
_UNARY_PARENTS = {"unary_expression", "pointer_expression", "not_operator"}


def _leaf_tokens(tree, data, language, atomic=True):
    A = _ATOMIC[language] if atomic else set()
    toks, bail = [], False

    def walk(n):
        nonlocal bail
        if n.type in _BAIL:
            bail = True
            return
        if n.type in A or n.child_count == 0:
            if n.end_byte > n.start_byte:
                toks.append((data[n.start_byte:n.end_byte].decode(), n))
            return
        for c in n.children:
            walk(c)
    walk(tree.root_node)
    return None if bail else toks


def _space_before(tok, node, ptok, pnode):
    if ptok is None:
        return False
    if tok in _ATTACH_LEFT:
        return False
    if tok == "(":
        return ptok in _KW_BEFORE_PAREN
    if tok == "[":
        return False
    if tok in ("++", "--") and node.parent is not None and node.parent.type == "update_expression":
        return node.parent.children and node.parent.children[0].id == node.id
    if tok == ":" and node.parent is not None and node.parent.type in ("labeled_statement", "switch_label",
                                                                       "case_statement", "access_specifier"):
        return False
    if ptok in ("(", "[", ".", "->", "::", "!", "~"):
        return False
    if pnode.parent is not None and pnode.parent.type in _UNARY_PARENTS and not pnode.is_named:
        return False
    if pnode.parent is not None and pnode.parent.type in ("pointer_declarator", "reference_declarator",
                                                          "abstract_pointer_declarator", "abstract_reference_declarator") \
            and ptok in ("*", "&", "&&"):
        return False
    if ptok in ("++", "--") and pnode.parent is not None and pnode.parent.type == "update_expression" \
            and pnode.parent.children and pnode.parent.children[0].id == pnode.id:
        return False
    return True


def _render(toks):
    lines, cur, ind, cur_ind, paren, pend = [], "", 0, 0, 0, False
    ptok, pnode = None, None

    def flush():
        nonlocal cur, cur_ind
        if cur.strip():
            lines.append("    " * max(cur_ind, 0) + cur.strip())
        cur, cur_ind = "", ind

    for tok, node in toks:
        if pend and tok not in _KEEP_SAME_LINE:
            flush()
            ptok, pnode = None, None
            pend = False
        if tok == "{" and (node.parent is None or node.parent.type not in _INIT_BRACE_PARENT):
            cur = (cur.rstrip() + " {") if cur.strip() else "{"
            flush()
            ind += 1
            cur_ind = ind
            ptok, pnode, pend = None, None, False
            continue
        if tok == "}" and (node.parent is None or node.parent.type not in _INIT_BRACE_PARENT):
            flush()
            ind -= 1
            cur_ind = ind
            cur = "}"
            ptok, pnode, pend = tok, node, True
            continue
        if tok == ";" and paren == 0:
            cur = cur.rstrip() + ";"
            ptok, pnode, pend = tok, node, True
            continue
        if tok == "(":
            paren += 1
        elif tok == ")":
            paren = max(paren - 1, 0)
        if _space_before(tok, node, ptok, pnode) and cur.strip():
            cur += " "
        cur += tok
        ptok, pnode, pend = tok, node, False
        if node.type in _FORCE_NL:
            flush()
            ptok, pnode = None, None
        elif tok == ":" and node.parent is not None and node.parent.type in ("labeled_statement", "switch_label",
                                                                             "case_statement", "access_specifier"):
            flush()
            ptok, pnode = None, None
    flush()
    return "\n".join(lines) + "\n"


def atk_reformat(code, language):
    if language == "python":
        return code
    atk_reformat.stats["n"] = atk_reformat.stats.get("n", 0) + 1
    tree, data = parse(code.encode(), language)
    if tree is None:
        atk_reformat.stats["parse_fail"] = atk_reformat.stats.get("parse_fail", 0) + 1
        return code
    toks = _leaf_tokens(tree, data, language)
    if toks is None:
        atk_reformat.stats["bail_preproc"] = atk_reformat.stats.get("bail_preproc", 0) + 1
        return code
    out = _render(toks)
    t2, d2 = parse(out.encode(), language)
    if t2 is None:
        atk_reformat.stats["reparse_fail"] = atk_reformat.stats.get("reparse_fail", 0) + 1
        return code
    a = _leaf_tokens(tree, data, language, atomic=False)
    b = _leaf_tokens(t2, d2, language, atomic=False)
    norm = lambda xs: [(x.rstrip() if n.type in _COMMENT_TYPES else x) for x, n in xs]
    if a is None or b is None or norm(a) != norm(b):
        atk_reformat.stats["token_mismatch"] = atk_reformat.stats.get("token_mismatch", 0) + 1
        return code
    atk_reformat.stats["ok"] = atk_reformat.stats.get("ok", 0) + 1
    return out


atk_reformat.stats = {}
