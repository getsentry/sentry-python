import contextlib
import itertools
import inspect
import typing as t

import sentry_sdk.hub
import sentry_sdk.utils
import sentry_sdk.integrations
import sentry_sdk.integrations.wsgi
import sentry_sdk._types

try:
    from trytond.backend.postgresql import database  # type: ignore
except ImportError:
    database = None
from trytond.exceptions import TrytonException  # type: ignore
from trytond.model import ModelStorage  # type: ignore
from trytond.model import ModelView
from trytond.model import Workflow
from trytond.pool import Pool  # type: ignore
from trytond.protocols.wrappers import Request  # type: ignore
from trytond.protocols.wrappers import Response
from trytond.transaction import Transaction  # type: ignore


if sentry_sdk._types.TYPE_CHECKING:
    from trytond.wsgi import TrytondWSGI  # type: ignore


ErrorHandler = t.Callable[["TrytondWSGI", Request, Exception], t.Optional[Response]]
Transition = t.Callable[[t.List[Workflow]], None]
TransitionDecorator = t.Callable[[Transition], Transition]

_pool_setup = Pool.setup
_transaction_start = Transaction.start
_workflow_transition: t.Callable[[str], TransitionDecorator] = Workflow.transition

if database:
    _db_cursor = database.LoggingCursor
else:
    _db_cursor = type


class TracingDBCursor(_db_cursor):  # type: ignore
    def __trace_sql_queries(self, exec_, sql, params, executemany):  # type: ignore
        hub = sentry_sdk.Hub.current

        if hub.get_integration(TrytondWSGIIntegration) is not None:

            def ret(query, vars):  # type: ignore
                with sentry_sdk.tracing_utils.record_sql_queries(
                    hub,
                    self,
                    sql,
                    params,
                    paramstyle="format",
                    executemany=executemany,
                ):
                    return exec_(query, vars)

        else:
            ret = exec_

        return ret

    def execute(self, query, vars=None):  # type: ignore
        exec_ = super().execute
        exec_ = self.__trace_sql_queries(
            exec_,
            query,
            vars,
            executemany=False,
        )
        return exec_(query, vars)

    def executemany(self, query, vars_list):  # type: ignore
        exec_ = super().executemany
        exec_ = self.__trace_sql_queries(
            exec_,
            query,
            vars_list,
            executemany=True,
        )
        return exec_(query, vars_list)


def _tracing_span(description, op=sentry_sdk.consts.OP.FUNCTION):  # type: ignore
    span = sentry_sdk.get_current_span()
    if span:
        return span.start_child(
            op=op,
            description=description,
        )


def _trace_classmethod(func):  # type: ignore
    def tracing_span(cls, *args, **kwargs):  # type: ignore
        span = _tracing_span(f"{cls.__name__}:{func.__name__}")  # noqa: E231
        if span:
            with span:
                return func(*args, **kwargs)
        else:
            return func(*args, **kwargs)

    return tracing_span


@contextlib.contextmanager
def tracing_transaction_start(*args, **kwargs):  # type: ignore
    span = _tracing_span("trytond.transaction.Transaction:start")
    if span:
        # TODO: set span data
        with span:
            with _transaction_start(*args, **kwargs) as transaction:
                yield transaction
    else:
        with _transaction_start(*args, **kwargs) as transaction:
            yield transaction


_transitions2trace: t.List[Transition] = []


def tracing_workflow_transition(state: str) -> TransitionDecorator:
    _transition_decorator = _workflow_transition(state)

    def _tracing_decorator(func: Transition) -> Transition:
        _transitions2trace.append(func)
        return _transition_decorator(func)

    return _tracing_decorator


def trace_pool_setup(self: Pool, classes: t.Any = None) -> None:  # noqa: C901
    def get_classname(method: t.Any) -> str:
        return inspect._findclass(method).__name__  # type: ignore

    def get_methodname(method: t.Any) -> str:
        return method.__name__

    global _transitions2trace
    _sorted_transitions = sorted(
        _transitions2trace,
        key=get_classname,
    )
    _grouped_transitions = itertools.groupby(
        _sorted_transitions,
        key=get_classname,
    )
    _transitions_sets: t.Dict[str, t.Set[str]] = {
        # Multiply overriden Tryton transactions are usually
        # (wisely and conveniently but not necessarily)
        # multiply decorated as well. Get a `set`.
        class_name: set(map(get_methodname, transitions))
        for (class_name, transitions) in _grouped_transitions
    }

    class TracingModel:
        @classmethod
        def __post_setup__(cls) -> None:
            super(TracingModel, cls).__post_setup__()  # type: ignore
            if issubclass(cls, ModelStorage):
                cls.create = classmethod(_trace_classmethod(cls.create))  # type: ignore
                cls.read = classmethod(_trace_classmethod(cls.read))  # type: ignore
                cls.write = classmethod(_trace_classmethod(cls.write))  # type: ignore
                cls.delete = classmethod(_trace_classmethod(cls.delete))  # type: ignore
                cls.copy = classmethod(_trace_classmethod(cls.copy))  # type: ignore
                cls.search = classmethod(_trace_classmethod(cls.search))  # type: ignore
            if issubclass(cls, Workflow):
                for name in _transitions_sets.get(cls.__name__, []):
                    trans = getattr(cls, name, None)
                    if trans:
                        setattr(cls, name, classmethod(_trace_classmethod(trans)))
            if issubclass(cls, ModelView):
                # TODO: trace buttons that aren't Workflow transitions
                pass

    class TracingWizard:
        @classmethod
        def __post_setup__(cls) -> None:
            super(TracingWizard, cls).__post_setup__()  # type: ignore
            # TODO trace transaction_xxxx and do_xxxx action states
            cls.execute = classmethod(_trace_classmethod(cls.execute))  # type: ignore

    class TracingReport:
        @classmethod
        def __post_setup__(cls) -> None:
            super(TracingReport, cls).__post_setup__()  # type: ignore
            cls.execute = classmethod(_trace_classmethod(cls.execute))  # type: ignore

    mixins = {
        "model": TracingModel,
        "wizard": TracingWizard,
        "report": TracingReport,
    }

    for type_ in mixins:
        for name, klass in self.iterobject(type=type_):
            klass = type(name, (mixins[type_], klass), {"__slots__": ()})
            self.add(klass, type=type_)

    _pool_setup(self, classes=classes)


# TODO: trytond-worker, trytond-cron and trytond-admin intergations


class TrytondWSGIIntegration(sentry_sdk.integrations.Integration):
    identifier = "trytond_wsgi"

    def __init__(self):  # type: () -> None
        pass

    @staticmethod
    def setup_once():  # type: () -> None
        # Lazy import for potential setup as a trytond middleware
        from trytond.wsgi import app
        from trytond.wsgi import TrytondWSGI

        app_dispatch_request = app.dispatch_request

        def error_handler(e: Exception) -> None:
            hub = sentry_sdk.hub.Hub.current

            if hub.get_integration(TrytondWSGIIntegration) is None:
                return
            elif isinstance(e, TrytonException):
                return
            else:
                # If an integration is there, a client has to be there.
                client = hub.client  # type: t.Any
                event, hint = sentry_sdk.utils.event_from_exception(
                    e,
                    client_options=client.options,
                    mechanism={"type": "trytond", "handled": False},
                )
                hub.capture_event(event, hint=hint)

        # Expected error handlers signature was changed
        # when the error_handler decorator was introduced
        # in Tryton-5.4
        if hasattr(app, "error_handler"):
            error_handler_decorator: t.Callable[
                [ErrorHandler], ErrorHandler
            ] = app.error_handler

            @error_handler_decorator
            def _(app: TrytondWSGI, request: Request, e: Exception) -> None:
                error_handler(e)

        else:
            app.error_handlers.append(error_handler)

        def tracing_app_dispatch_request(request: Request) -> t.Any:
            username = None
            if request.authorization:
                username = request.authorization.username
            elif request.rpc_method == "common.db.login":
                try:
                    (username, *_) = request.rpc_params
                except (TypeError, ValueError):
                    pass
            if username:
                sentry_sdk.set_user({"username": username})

            if request.rpc_method:

                def trace_dispatch(req: Request) -> t.Any:
                    with sentry_sdk.configure_scope() as scope:
                        scope.set_transaction_name(request.rpc_method)
                        return app_dispatch_request(req)

                dispatch = trace_dispatch
            else:
                # TODO: Figure out non-RPC "res.user.application" requests (but
                # sentry_sdk.integrations.wsgi.SentryWsgiMiddleware might do the job)
                dispatch = app_dispatch_request

            return dispatch(request)

        # Patch time!
        app.wsgi_app = sentry_sdk.integrations.wsgi.SentryWsgiMiddleware(app.wsgi_app)
        app.dispatch_request = tracing_app_dispatch_request
        Pool.setup = trace_pool_setup
        Transaction.start = tracing_transaction_start
        Workflow.transition = tracing_workflow_transition
        if database:
            database.LoggingCursor = TracingDBCursor
