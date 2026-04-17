"""Back-compat shim — struct tree probe lives in src/tools/struct_tree_probe.py now.

Kept so external scripts that imported from here keep working.
"""
from src.tools.struct_tree_probe import (  # noqa: F401
    StructFacts,
    STANDARD_TAGS,
    probe_struct_tree,
    _get_obj_text,
)
