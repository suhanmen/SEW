FUNC = {"python": {"function_definition"}, "java": {"method_declaration", "constructor_declaration"}, "cpp": {"function_definition"}}
PARAMS = {"python": {"parameters", "lambda_parameters"}, "java": {"formal_parameters"}, "cpp": {"parameter_list"}}
PARAM_ITEM = {"python": {"identifier", "typed_parameter", "default_parameter", "typed_default_parameter", "list_splat_pattern", "dictionary_splat_pattern"},
              "java": {"formal_parameter", "spread_parameter"}, "cpp": {"parameter_declaration", "optional_parameter_declaration", "variadic_parameter_declaration"}}
LOOP = {"python": {"for_statement", "while_statement"}, "java": {"for_statement", "enhanced_for_statement", "while_statement", "do_statement"},
        "cpp": {"for_statement", "for_range_loop", "while_statement", "do_statement"}}
RET = {"python": {"return_statement"}, "java": {"return_statement"}, "cpp": {"return_statement"}}


def context(tree, language):
    nf = np = nl = nr = 0
    stack = [tree.root_node]
    while stack:
        n = stack.pop(); stack.extend(n.children)
        if n.type in FUNC[language]: nf += 1
        elif n.type in PARAMS[language]: np += sum(1 for c in n.children if c.type in PARAM_ITEM[language])
        elif n.type in LOOP[language]: nl += 1
        elif n.type in RET[language]: nr += 1
    return (f"ctx:{nf}.{np}.{nl}.{nr}",)
