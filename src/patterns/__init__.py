# AWS Bill Whisperer - Pattern Registry
# Add new patterns by creating a file in this directory

import importlib
import pkgutil
from pathlib import Path


# Auto-discover all pattern modules
def discover_patterns():
    """Auto-discover all Pattern classes in this directory"""
    patterns = []
    package_dir = Path(__file__).parent

    for module_info in pkgutil.iter_modules([str(package_dir)]):
        if module_info.name.startswith('_'):
            continue
        module = importlib.import_module(f'.{module_info.name}', __package__)
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type) and
                hasattr(attr, 'PATTERN_ID') and
                attr_name != 'BasePattern'):
                patterns.append(attr)

    return sorted(patterns, key=lambda p: p.PATTERN_ID)

__all__ = ['discover_patterns']
