from opentelemetry.context.context import Context  # type: ignore
from opentelemetry.context.contextvars_context import ContextVarsRuntimeContext  # type: ignore


class SentryContextVarsRuntimeContext(ContextVarsRuntimeContext):  # type: ignore
    def attach(self, context):
        # type: (Context) -> object
        # TODO-neel-potel do scope management
        return super().attach(context)

    def detach(self, token):
        # type: (object) -> None
        # TODO-neel-potel not sure if we need anything here, see later
        super().detach(token)
