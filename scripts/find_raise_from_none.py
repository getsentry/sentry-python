import ast
import pathlib
from collections import defaultdict


class RaiseFromNoneVisitor(ast.NodeVisitor):
    line_numbers = defaultdict(list)

    def __init__(self, filename):
        self.filename = filename

    def visit_Raise(self, node: ast.Raise):
        if node.cause is not None:
            if isinstance(node.cause, ast.Constant) and node.cause.value is None:
                RaiseFromNoneVisitor.line_numbers[self.filename].append(node.lineno)
        self.generic_visit(node)


def scan_file(module_path: pathlib.Path):
    source = pathlib.Path(module_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=module_path)

    RaiseFromNoneVisitor(module_path).visit(tree)


def walk_package_modules():
    for p in pathlib.Path("sentry_sdk").rglob("*.py"):
        yield p


def format_detected_raises(line_numbers) -> str:
    lines = []
    for filepath, line_numbers_in_file in line_numbers.items():
        lines_string = ", ".join(f"line {ln}" for ln in sorted(line_numbers_in_file))
        lines.append(
            f"{filepath}: {len(line_numbers_in_file)} occurrence(s) at {lines_string}"
        )
    return "\n".join(lines)


def main():
    for module_path in walk_package_modules():
        scan_file(module_path)

    # TODO: Investigate why we suppress exception chains here.
    ignored_raises = {
        pathlib.Path("sentry_sdk/integrations/asgi.py"): 2,
        pathlib.Path("sentry_sdk/integrations/asyncio.py"): 1,
    }

    raise_from_none_count = {
        file: len(occurences)
        for file, occurences in RaiseFromNoneVisitor.line_numbers.items()
    }
    if raise_from_none_count != ignored_raises:
        exc = Exception("Detected unexpected raise ... from None.")
        exc.add_note(
            "Raise ... from None suppresses chained exceptions, removing valuable context."
        )
        exc.add_note(format_detected_raises(RaiseFromNoneVisitor.line_numbers))
        raise exc


if __name__ == "__main__":
    main()
