from .python_rules import RULES as _PY_CORE
from .python_rules_extra import RULES as _PY_EXTRA
from .python_rules_idiom import RULES as _PY_IDIOM
from .python_rules_format import RULES as _PY_FORMAT
from .c_family_rules import JAVA_RULES as _JV_CORE, CPP_RULES as _CP_CORE
from .c_family_rules_control import JAVA_RULES as _JV_CONTROL, CPP_RULES as _CP_CONTROL
from .c_family_rules_format import JAVA_RULES as _JV_FORMAT, CPP_RULES as _CP_FORMAT

RULES_BY_LANGUAGE = {
    "python": {**_PY_CORE, **_PY_EXTRA, **_PY_IDIOM, **_PY_FORMAT},
    "java": {**_JV_CORE, **_JV_CONTROL, **_JV_FORMAT},
    "cpp": {**_CP_CORE, **_CP_CONTROL, **_CP_FORMAT},
}

FORMAT_RULE_IDS = frozenset({"F40", "F41", "F42", "F43", "F44"})


def active_rules(language):
    return RULES_BY_LANGUAGE[language]
