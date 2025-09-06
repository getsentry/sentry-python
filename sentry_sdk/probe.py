import dis
import linecache
import random
import sys
import time
import types
from bytecode import Bytecode, Instr
from dataclasses import dataclass, field
from pathlib import Path
from sentry_sdk.utils import logger


@dataclass
class LogPoint:
    id: int
    source_file: str
    line_number: int
    message_template: str
    tags: dict[str, str] = field(default_factory=dict)
    sample_rate: float | None = None
    sample_expression: str | None = None
    valid_until: int | None = None


class Registry:
    def __init__(self):
        self.log_points = {}
        self.log_point_hooks = {}

    def insert_log_point(self, log_point):
        fn = function_at_location(log_point.source_file, log_point.line_number)
        code = Bytecode.from_code(fn.__code__)
        for i, instr in enumerate(code):
            if getattr(instr, "lineno", None) == log_point.line_number:
                logger.debug("Injecting log point at index=%s instr=%s", i, instr)
                hook_instrs = _hook_instructions(log_point.id)
                code[i:i] = hook_instrs
                self.log_point_hooks[log_point.id] = (i, i+len(hook_instrs))
                break
        fn.__code__ = code.to_code()
        start_index, end_index = self.log_point_hooks[log_point.id]
        logger.debug("Injected log point into function %s at start_index=%s end_index=%s", fn, start_index, end_index)
        self.log_points[log_point.id] = log_point

    def remove_log_point(self, log_point_id):
        log_point = self.log_points[log_point_id]
        fn = function_at_location(log_point.source_file, log_point.line_number)
        code = Bytecode.from_code(fn.__code__)
        start_index, end_index = self.log_point_hooks[log_point_id]
        logger.debug("Ejecting log point at start_index=%s end_index=%s", start_index, end_index)
        del code[start_index:end_index]
        fn.__code__ = code.to_code()
        logger.debug("Ejected log point from function %s", fn)
        del self.log_point_hooks[log_point_id]
        del self.log_points[log_point_id]


registry = Registry()


def _hook_instructions(log_point_id):
    # sentry_sdk.probe._handle_log_point(log_point_id, locals())
    return [
        Instr("LOAD_GLOBAL", (False, "sentry_sdk")),
        Instr("LOAD_ATTR", (False, "probe")),
        Instr("LOAD_ATTR", (True, "_handle_log_point")),
        Instr("LOAD_CONST", log_point_id),
        Instr("LOAD_GLOBAL", (True, "locals")),
        Instr("CALL", 0),
        Instr("CALL", 2),
        Instr("POP_TOP"),
    ]


def _handle_log_point(log_point_id, local_vars):
    import sentry_sdk

    logger.debug("Handling log point id=%s, local_vars=%s", log_point_id, local_vars)
    log_point = registry.log_points[log_point_id]

    if log_point.valid_until is not None and log_point.valid_until < time.time():
        logger.debug("Log point id=%s not valid", log_point_id)
        return

    if log_point.sample_rate is not None and log_point.sample_rate < random.uniform(0, 1):
        logger.debug("Log point id=%s not sampled: rate", log_point_id)
        return

    if (
        log_point.sample_expression is not None and
        not eval(log_point.sample_expression, globals={}, locals=local_vars) # 😬
    ):
        logger.debug("Log point id=%s not sampled: expression", log_point_id)
        return

    attributes = {
        f"locals.{key}": value
        for key, value in local_vars.items()
    } | {
        "log_point.source_file": log_point.source_file,
        "log_point.line_number": log_point.line_number,
        "log_point.sample_rate": log_point.sample_rate,
        "log_point.valid_until": log_point.valid_until,
        "log_point.code_context": linecache.getline(log_point.source_file, log_point.line_number),
    } | log_point.tags

    sentry_sdk.logger.info(log_point.message_template, attributes=attributes)


def function_at_location(source_file, line_number):
    module = next(
        iter(
            m for m
            in sys.modules.values()
            if getattr(m, "__file__", "").lower() == source_file
        )
    )
    for fn in module.__dict__.values():
        if isinstance(fn, types.FunctionType):
            for _, line in dis.findlinestarts(fn.__code__):
                if line == line_number:
                    return fn
