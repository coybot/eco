"""Reject LLM-written drone code that calls functions the aircraft does not have.

The aircraft runs planner code in daemon.execute_code with the public names of
drone_sdk and vehicles/*, a fixed set of safe builtins, and math/time/sleep.
A call to anything else (e.g. `go_up(2)`, seen 2026-11-17) dies there with a
NameError, after the operator has already said OK to fly. Checking here lets
the backend ask the model again instead of sending code that cannot run.

Only bare-name calls are checked (`foo(...)`); attribute calls (`math.ceil`,
`result.get`) are left to the aircraft. Names the code binds itself (def,
assignment, loop variables, imports) count as known.
"""
from __future__ import annotations

import ast

from drone_sdk_names import SDK_NAMES

# Mirrors SAFE_BUILTINS in drone/common/daemon.py execute_code.
SAFE_BUILTINS = frozenset({
    'abs', 'all', 'any', 'bool', 'bytes', 'callable', 'chr', 'dict',
    'divmod', 'enumerate', 'filter', 'float', 'format', 'frozenset',
    'hasattr', 'hash', 'hex', 'id', 'int', 'isinstance', 'issubclass',
    'iter', 'len', 'list', 'map', 'max', 'min', 'next', 'oct', 'ord',
    'pow', 'print', 'range', 'repr', 'reversed', 'round', 'set',
    'slice', 'sorted', 'str', 'sum', 'tuple', 'type', 'zip',
    'Exception', 'ValueError', 'TypeError', 'RuntimeError', 'KeyError',
    'IndexError', 'AttributeError',
})
# Added to the sandbox by execute_code next to the SDK.
SANDBOX_EXTRAS = frozenset({'math', 'time', 'sleep'})

KNOWN_CALLS = SDK_NAMES | SAFE_BUILTINS | SANDBOX_EXTRAS


def _bound_names(tree: ast.AST) -> set[str]:
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            bound.update(a.arg for a in node.args.args + node.args.kwonlyargs)
        elif isinstance(node, ast.Lambda):
            bound.update(a.arg for a in node.args.args)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update((a.asname or a.name).split('.')[0] for a in node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    return bound


def unknown_calls(code: str) -> list[str]:
    """Bare function names `code` calls that the aircraft's sandbox lacks.
    A syntax error is reported as one entry, since the aircraft cannot run it."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError as e:
        return [f"<syntax error line {e.lineno}: {e.msg}>"]
    bound = _bound_names(tree)
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    return sorted(c for c in called if c not in KNOWN_CALLS and c not in bound)


def retry_hint(bad: list[str]) -> str:
    """Instruction appended to the request when asking the model again."""
    return ("\n\nYour previous code could not run on the aircraft: it called "
            f"{', '.join(bad)}, which the drone SDK does not have. Write it again "
            "using only the drone SDK functions you were given.")

