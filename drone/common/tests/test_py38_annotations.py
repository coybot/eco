"""Everything that runs on an aircraft must import on Python 3.8.

JetPack 5 companions (the quadcopter included) run Python 3.8. `tuple[int,
int]` and `X | None` are valid 3.8 *syntax*, so they pass every parse check,
but annotations are evaluated when the def or class statement runs, and on
3.8 both raise TypeError at import. One such annotation in the camera
package's base class took out every photo on the quadcopter: the package
never imported, and "Camera not available" was all the log said.

This walks the code that ships to an aircraft and fails on any annotation
that 3.8 would evaluate and reject. A module with
`from __future__ import annotations` is exempt: it never evaluates them.

Run: cd eco && python3 -m pytest drone/common/tests/test_py38_annotations.py -v
"""

import ast
from pathlib import Path

DRONE = Path(__file__).resolve().parents[2]

# What install.sh puts on an aircraft: drone/common/*.py flat, plus the camera
# package. Tests do not ship.
SHIPPED = sorted(
    [p for p in (DRONE / "common").glob("*.py")]
    + [p for p in (DRONE / "camera").rglob("*.py") if "test" not in p.name]
)

BUILTIN_GENERICS = {"tuple", "list", "dict", "set", "frozenset", "type"}


def _runtime_annotations(tree):
    """Annotations 3.8 evaluates: function signatures, and class/module-level
    variable annotations (local variable annotations are never evaluated)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for a in args.posonlyargs + args.args + args.kwonlyargs + [args.vararg, args.kwarg]:
                if a is not None and a.annotation is not None:
                    yield a.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, (ast.ClassDef, ast.Module)):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign):
                    yield stmt.annotation


def _problems(annotation):
    for node in ast.walk(annotation):
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id in BUILTIN_GENERICS):
            yield f"{node.value.id}[...] needs Python 3.9"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            yield "X | Y needs Python 3.10"


def _defers_annotations(tree):
    return any(
        isinstance(n, ast.ImportFrom) and n.module == "__future__"
        and any(a.name == "annotations" for a in n.names)
        for n in tree.body
    )


def test_the_scan_covers_the_shipped_code():
    names = {p.name for p in SHIPPED}
    assert {"daemon.py", "drone_sdk.py", "reasoning_loop.py", "vlm.py"} <= names
    assert any(p.parent.name == "v4l2" for p in SHIPPED)


def test_shipped_code_parses_and_imports_on_python_38():
    failures = []
    for path in SHIPPED:
        source = path.read_text()
        tree = ast.parse(source, filename=str(path), feature_version=(3, 8))
        if _defers_annotations(tree):
            continue
        for ann in _runtime_annotations(tree):
            for problem in _problems(ann):
                failures.append(f"{path.relative_to(DRONE)}:{ann.lineno}: {problem}")
    assert not failures, "Would fail at import on a Python 3.8 aircraft:\n" + "\n".join(failures)
