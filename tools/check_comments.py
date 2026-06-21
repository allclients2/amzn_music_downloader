"""Enforce the project convention that explanatory comments and docstrings live
only at the top of a file. A single module-level docstring is allowed; a
shebang/encoding line and tooling-directive comments (`# noqa`, `# type:`,
`# ruff:`, `# pragma:`) are exempt. Any other `#` comment, or a docstring on a
function/class/method, is reported as an error. Run as a pre-commit hook over
the amzdl sources (the amazonmusic submodule is excluded by the hook's file
filter). Filenames are passed as argv; exits non-zero if any violation is
found."""

import ast
import sys
import tokenize
from pathlib import Path

_DIRECTIVE_PREFIXES = ("type:", "noqa", "ruff:", "pragma:", "isort:", "mypy:")


def _comment_violations(path):
    found = []
    with tokenize.open(path) as handle:
        try:
            tokens = list(tokenize.generate_tokens(handle.readline))
        except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
            return [(getattr(exc, "lineno", 0) or 0, f"could not tokenize: {exc}")]
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        if token.start == (1, 0) and token.string.startswith("#!"):
            continue
        body = token.string.lstrip("#").strip()
        if (
            body.startswith(_DIRECTIVE_PREFIXES)
            or "coding:" in body
            or "coding=" in body
        ):
            continue
        found.append((
            token.start[0],
            "inline comment is not allowed "
            "(only a top-of-file module docstring is permitted)",
        ))
    return found


def _docstring_violations(path):
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [(exc.lineno or 0, f"could not parse: {exc.msg}")]
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if ast.get_docstring(node, clean=False) is not None:
            line = node.body[0].lineno
            found.append((
                line,
                f"docstring on {node.name!r} is not allowed "
                "(only a top-of-file module docstring is permitted)",
            ))
    return found


def main(argv):
    failed = False
    for name in argv:
        path = Path(name)
        if path.suffix != ".py" or not path.is_file():
            continue
        violations = _comment_violations(path) + _docstring_violations(path)
        for line, message in sorted(violations):
            print(f"{path}:{line}: {message}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
