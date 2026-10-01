from dataclasses import dataclass, field


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    new: bytes

    def __post_init__(self):
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid range: [{self.start}, {self.end})")


@dataclass
class Site:
    rule_id: str
    variant: int
    span: tuple
    footprint: tuple = ()
    node_path: tuple = ()
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.variant not in (0, 1):
            raise ValueError(f"variant must be 0 or 1: {self.variant}")
        if not self.footprint:
            self.footprint = (self.span,)


class Rule:
    rule_id: str = ""
    language: str = ""
    variants: tuple = ("", "")
    sid_mode: str = "stmt"

    def match(self, tree, src: bytes):
        raise NotImplementedError

    def guard(self, site: Site, tree, src: bytes) -> bool:
        return True

    def variant_names(self) -> tuple:
        return self.variants

    def read(self, site: Site, src: bytes) -> int:
        return site.variant

    def write(self, site: Site, src: bytes, target: int):
        if target not in (0, 1):
            raise ValueError(f"target must be 0 or 1: {target}")
        if site.variant == target:
            return []
        edits = self.write_bytes(site, src, target)
        check_edits(edits, site)
        return edits

    def write_bytes(self, site: Site, src: bytes, target: int):
        raise NotImplementedError

    def erase(self, site: Site, src: bytes):
        edits = self.erase_bytes(site, src)
        check_edits(edits, site)
        return edits

    def erase_bytes(self, site: Site, src: bytes):
        raise NotImplementedError

    def footprint(self, site: Site) -> tuple:
        return site.footprint


def check_edits(edits, site: Site):
    fp = site.footprint
    for e in edits:
        if not any(a <= e.start and e.end <= b for a, b in fp):
            raise ValueError(
                f"{site.rule_id}: edit [{e.start},{e.end}) outside footprint {fp}")
    ordered = sorted(edits, key=lambda e: e.start)
    for a, b in zip(ordered, ordered[1:]):
        if b.start < a.end:
            raise ValueError(f"{site.rule_id}: overlapping edits {a} / {b}")


def apply_edits(src: bytes, edits) -> bytes:
    out = src
    for e in sorted(edits, key=lambda e: (e.start, e.end), reverse=True):
        if e.start > len(out) or e.end > len(out):
            raise ValueError(f"edit outside source: [{e.start},{e.end}) / len={len(out)}")
        out = out[:e.start] + e.new + out[e.end:]
    return out


_PARSERS = {}


def get_parser(language: str):
    if language in _PARSERS:
        return _PARSERS[language]
    from tree_sitter import Language, Parser
    if language == "python":
        import tree_sitter_python as ts
    elif language == "java":
        import tree_sitter_java as ts
    elif language == "cpp":
        import tree_sitter_cpp as ts
    else:
        raise ValueError(f"unsupported language: {language}")
    _PARSERS[language] = Parser(Language(ts.language()))
    return _PARSERS[language]


def parse(src, language: str):
    if isinstance(src, str):
        src = src.encode("utf-8")
    tree = get_parser(language).parse(src)
    return (None if tree.root_node.has_error else tree), src


def named_children(node):
    return [c for c in node.children if c.is_named]


def node_path(node) -> tuple:
    path, cur = [], node
    while cur.parent is not None:
        sibs = [c for c in cur.parent.children if c.is_named]
        try:
            idx = sibs.index(cur)
        except ValueError:
            idx = -1
        path.append((cur.type, idx))
        cur = cur.parent
    return tuple(reversed(path))
