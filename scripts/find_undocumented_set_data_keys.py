"""
Best-effort script to find all attribute names set by the SDK that are not documented in sentry-conventions.
Install both the `sentry_sdk` and `sentry_conventions` packages in your environment to run the script.
"""

from dataclasses import dataclass

import ast
import re
from pathlib import Path
from typing import Any

from sentry_sdk.consts import SPANDATA
from sentry_conventions.attributes import ATTRIBUTE_NAMES

ALLOWED_EXPRESSION_PATTERNS_FOR_UNRESOLVED_KEYS = {
    # User-provided attributes in the `sentry_data` keyword argument of the `ai_track` decorator.
    Path("sentry_sdk") / "ai" / "monitoring.py": [r"^k$"],
    # Caller provides parameter name to `set_data_normalized()`.
    Path("sentry_sdk") / "ai" / "utils.py": [r"^key$"],
    # OTel span attributes from external instrumentation.
    Path("sentry_sdk") / "integrations" / "opentelemetry" / "span_processor.py": [
        r"^key$"
    ],
    # Rust extensions instrumented with the `tracing` crate.
    Path("sentry_sdk") / "integrations" / "rust_tracing.py": [r"^field$", r"^key$"],
    # Determined based on MCP tool parameters only known at runtime.
    Path("sentry_sdk") / "integrations" / "mcp.py": [r"mcp\.request\.argument"],
}


@dataclass
class ResolvedSetDataKey:
    value: str
    path: Path
    line_number: int


@dataclass
class UnresolvedSetDataKey:
    argument_expression: str
    path: Path
    line_number: int


def try_eval(node: "ast.expr", namespace: "dict[str, Any]", path: "Path") -> "Any":
    """
    Evaluate expressions that can be statically resolved with the namespace.
    """
    try:
        return eval(compile(ast.Expression(body=node), path, "eval"), namespace)
    except Exception:
        return None


def evaluate_dictionary_keys(
    node: "ast.Dict", namespace: "dict[str, Any]", path: "Path"
) -> "dict[str, None] | None":
    """
    Evaluate dict literal keys that can be statically resolved with the namespace.
    """
    partial = {}
    for key_node in node.keys:
        if key_node is None:
            continue

        resolved_key = try_eval(node=key_node, namespace=namespace, path=path)
        if resolved_key is None:
            continue

        # Dictionary values do not matter as attribute names only appear as dictionary keys.
        partial[resolved_key] = None

    return partial


def build_file_namespace(
    tree: "ast.AST", namespace: "dict[str, Any]", path: "Path"
) -> "dict[str, Any]":
    """
    Walk tree and add assignment targets to the namespace, including annotated assignments and subscripted assignments.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue

        if isinstance(target, ast.Name):
            val = try_eval(node=value, namespace=namespace, path=path)
            if val is not None:
                namespace[target.id] = val
            elif isinstance(value, ast.Dict):
                # Store dictionary with the subset of keys that could be statically resolved in the namespace.
                namespace[target.id] = evaluate_dictionary_keys(value, namespace, path)

        elif (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id in namespace
            and isinstance(namespace[target.value.id], dict)
        ):
            key = try_eval(
                node=target.slice,
                namespace=namespace,
                path=path,
            )
            if key is None:
                continue

            namespace[target.value.id][key] = None

    return namespace


def get_key_node(node: "ast.Call") -> "ast.expr | None":
    """
    Get attribute key if the node is either a `Span.set_data()` method call or a `set_data_normalized()` function call.
    """
    if isinstance(node.func, ast.Attribute) and node.func.attr == "set_data":
        return node.args[0]
    if isinstance(node.func, ast.Name) and node.func.id == "set_data_normalized":
        return node.args[1]
    return None


class SetDataKeysCollector(ast.NodeVisitor):
    """
    AST traversal that collects all attribute keys passed to functions that set an attribute on a span.
    A best-effort name resolution evaluates expressions given the namespace and by tracing for loop
    variables.
    """

    def __init__(self, namespace: "dict[str, Any]", path: "Path") -> None:
        self.namespace = namespace
        self.path = path
        self.for_stack: "list[ast.For]" = []
        self.resolved: "list[ResolvedSetDataKey]" = []
        self.unresolved: "list[UnresolvedSetDataKey]" = []

    def visit_For(self, node: "ast.For") -> None:
        self.for_stack.append(node)
        self.generic_visit(node)
        self.for_stack.pop()

    def visit_Call(self, node: "ast.Call") -> None:
        key_node = get_key_node(node)
        if key_node is not None:
            self._resolve(node, key_node)
        self.generic_visit(node)

    def _resolve(self, node: "ast.Call", key_node: "ast.expr") -> None:
        direct = try_eval(key_node, self.namespace, self.path)
        if direct is not None:
            self.resolved.append(
                ResolvedSetDataKey(
                    value=direct,
                    path=self.path,
                    line_number=node.lineno,
                )
            )
            return

        if self.for_stack:
            set_data_keys = self._eval_via_loop(key_node)
            if set_data_keys:
                self.resolved += [
                    ResolvedSetDataKey(
                        value=value, path=self.path, line_number=node.lineno
                    )
                    for value in set_data_keys
                ]
                return

        # The key is considered unresolved as neither direct evaluation nor resolution via loop variables worked.
        self.unresolved.append(
            UnresolvedSetDataKey(
                argument_expression=ast.unparse(key_node),
                path=self.path,
                line_number=node.lineno,
            )
        )

    def _eval_via_loop(self, key_node: "ast.expr") -> "list[str] | None":
        for_node = self.for_stack[-1]

        # Trick: build a list comprehension that mirrors the for loop statement.
        list_comprehension = ast.ListComp(
            elt=key_node,
            generators=[
                ast.comprehension(
                    target=for_node.target,
                    iter=for_node.iter,
                    ifs=[],
                    is_async=0,
                )
            ],
        )

        ast.fix_missing_locations(
            list_comprehension
        )  # Adds information required to call compile.
        try:
            values = eval(
                compile(ast.Expression(body=list_comprehension), self.path, "eval"),
                self.namespace,
            )
        except NameError:
            return None

        return values


def format_unknown_resolved_attributes(keys: "list[ResolvedSetDataKey]") -> "str":
    lines = [
        "The following resolved string attribute names are not in sentry-conventions:\n"
    ]
    for key in keys:
        lines.append(f"{key.value} ({key.path}:{key.line_number})")
    return "\n".join(lines) + "\n"


def format_unresolved_attributes(keys: "list[UnresolvedSetDataKey]") -> "str":
    lines = [
        "The following unresolved expressions for attribute names may not be in sentry-conventions:\n"
    ]
    for key in keys:
        lines.append(f"{key.argument_expression} ({key.path}:{key.line_number})")
    return "\n".join(lines)


def main():
    # Includes special attributes (with double underscores), but only proper attributes should match.
    convention_keys = vars(ATTRIBUTE_NAMES).values()

    all_resolved: "list[ResolvedSetDataKey]" = []
    all_unresolved: "list[UnresolvedSetDataKey]" = []

    for path in Path("sentry_sdk").rglob("*.py"):
        tree = ast.parse(path.read_text(), path)

        # A limited namespace is used to resolve keys as resolution is best-effort and some keys depend on runtime values.
        # In practice, span attribute names are often stored as dictionary keys in the same file.
        namespace = build_file_namespace(
            tree=tree,
            namespace={"SPANDATA": SPANDATA},
            path=path,
        )

        collector = SetDataKeysCollector(namespace=namespace, path=path)
        collector.visit(tree)

        all_resolved += collector.resolved
        all_unresolved += collector.unresolved

    unknown_resolved_keys = [
        resolved for resolved in all_resolved if resolved.value not in convention_keys
    ]

    truly_unresolved = []
    for unresolved_set_data in all_unresolved:
        patterns = ALLOWED_EXPRESSION_PATTERNS_FOR_UNRESOLVED_KEYS.get(
            unresolved_set_data.path
        )
        if patterns and any(
            re.search(p, unresolved_set_data.argument_expression) for p in patterns
        ):
            continue
        truly_unresolved.append(unresolved_set_data)

    if unknown_resolved_keys or truly_unresolved:
        exc = Exception("Undocumented attributes not in the allow-list detected.")

        if unknown_resolved_keys:
            exc.add_note(format_unknown_resolved_attributes(unknown_resolved_keys))

        if truly_unresolved:
            exc.add_note(format_unresolved_attributes(truly_unresolved))

        raise exc


if __name__ == "__main__":
    main()
