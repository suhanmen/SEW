BLOCK_TYPES = {"block", "module", "compound_statement", "translation_unit", "program", "class_body", "switch_block",
               "switch_block_statement_group", "constructor_body", "field_declaration_list", "enum_body", "interface_body",
               "declaration_list", "case_statement", "else_clause", "elif_clause"}


def _statement_of(tree, byte):
    n = tree.root_node.descendant_for_byte_range(byte, byte)
    while n is not None and n.parent is not None and n.parent.type not in BLOCK_TYPES:
        n = n.parent
    return n if n is not None else tree.root_node


def _depth(node):
    d, n = 0, node
    while n is not None and n.parent is not None:
        if n.parent.type in BLOCK_TYPES:
            d += 1
        n = n.parent
    return d


def _is_inf_loop(node):
    """`for (;;)` and `while (true)`, the two variants of rule S7 (Java, C++)."""
    if node.type == "for_statement":
        return all(node.child_by_field_name(f) is None for f in ("initializer", "condition", "update"))
    if node.type == "while_statement":
        from ..rules.c_family_rules import cond_inner
        c, _ = cond_inner(node)
        return c is not None and c.type == "true"
    return False


def _type(node, norm=False):
    """Node type ("root" for none). With ``norm``, both infinite-loop forms are written as "inf_loop", so that
    the identifiers of the S7 site and of the sites inside the loop do not depend on the S7 variant."""
    if node is None:
        return "root"
    return "inf_loop" if norm and _is_inf_loop(node) else node.type


def site_id(tree, rule_id, span, mode="stmt", language=None):
    norm = language in ("java", "cpp")
    if mode == "root":
        return (rule_id, "module", "root")
    if mode == "body":
        n = tree.root_node.descendant_for_byte_range(span[0], max(span[0], span[1] - 1))
        while n is not None and n.parent is not None and n.parent.type not in BLOCK_TYPES:
            n = n.parent
        stmt = n if n is not None else tree.root_node
        return (rule_id, _type(stmt, norm), _type(stmt.parent, norm))
    stmt = _statement_of(tree, span[0])
    parent = stmt.parent
    grand = parent.parent if parent is not None else None
    if mode == "parent":
        return (rule_id, _type(parent, norm), _type(grand, norm))
    return (rule_id, _type(stmt, norm), _type(parent, norm), _type(grand, norm), _depth(stmt))
