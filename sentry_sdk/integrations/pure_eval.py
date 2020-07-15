from __future__ import absolute_import

import ast

from sentry_sdk import Hub
from sentry_sdk._types import MYPY
from sentry_sdk.integrations import Integration, DidNotEnable
from sentry_sdk.scope import add_global_event_processor
from sentry_sdk.utils import walk_exception_chain, iter_stacks

if MYPY:
    from typing import Optional, Dict, Any
    from types import FrameType

    from sentry_sdk._types import Event, Hint

try:
    import executing
except ImportError:
    raise DidNotEnable("executing is not installed")

try:
    import pure_eval
except ImportError:
    raise DidNotEnable("pure_eval is not installed")

try:
    # Used implicitly, just testing it's available
    import asttokens  # noqa
except ImportError:
    raise DidNotEnable("asttokens is not installed")


class PureEvalIntegration(Integration):
    identifier = "pure_eval"

    @staticmethod
    def setup_once():
        # type: () -> None

        @add_global_event_processor
        def add_executing_info(event, hint):
            # type: (Event, Optional[Hint]) -> Optional[Event]
            if Hub.current.get_integration(PureEvalIntegration) is None:
                return event

            if hint is None:
                return event

            exc_info = hint.get("exc_info", None)

            if exc_info is None:
                return event

            exception = event.get("exception", None)

            if exception is None:
                return event

            values = exception.get("values", None)

            if values is None:
                return event

            for exception, (_exc_type, _exc_value, exc_tb) in zip(
                reversed(values), walk_exception_chain(exc_info)
            ):
                sentry_frames = [
                    frame
                    for frame in exception.get("stacktrace", {}).get("frames", [])
                    if frame.get("function")
                ]
                tbs = list(iter_stacks(exc_tb))
                if len(sentry_frames) != len(tbs):
                    continue

                for sentry_frame, tb in zip(sentry_frames, tbs):
                    sentry_frame["vars"].update(pure_eval_frame(tb.tb_frame))
            return event


def pure_eval_frame(frame):
    # type: (FrameType) -> Dict[str, Any]
    source = executing.Source.for_frame(frame)
    if not source.tree:
        return {}

    statements = source.statements_at_line(frame.f_lineno)
    if not statements:
        return {}

    stmt = list(statements)[0]
    while True:
        # Get the parent first in case the original statement is already
        # a function definition, e.g. if we're calling a decorator
        # In that case we still want the surrounding scope, not that function
        stmt = stmt.parent
        if isinstance(stmt, (ast.FunctionDef, ast.ClassDef, ast.Module)):
            break

    evaluator = pure_eval.Evaluator.from_frame(frame)
    expressions = evaluator.interesting_expressions_grouped(stmt)
    atok = source.asttokens()
    return {atok.get_text(nodes[0]): value for nodes, value in expressions}
