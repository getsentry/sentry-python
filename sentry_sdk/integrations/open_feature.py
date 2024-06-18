from typing import List, Optional

import sentry_sdk
from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagResolutionDetails
from openfeature.hook import Hook
from openfeature.provider.metadata import Metadata


class SentryOpenFeatureProviderDecorator:

    def __init__(self, provider):
        self.provider = provider

    def resolve_boolean_details(
        self,
        flag_key: str,
        default_value: bool,
        evaluation_context: Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[bool]:
        details = self.provider.resolve_boolean_details(
            flag_key, default_value, evaluation_context
        )
        sentry_sdk.set_tag(flag_key, details.value)
        return details

    def resolve_string_details(self, *args, **kwargs):
        return self.provider.resolve_string_details(*args, **kwargs)

    def resolve_integer_details(self, *args, **kwargs):
        return self.provider.resolve_integer_details(*args, **kwargs)

    def resolve_float_details(self, *args, **kwargs):
        return self.provider.resolve_float_details(*args, **kwargs)

    def resolve_object_details(self, *args, **kwargs):
        return self.provider.resolve_object_details(*args, **kwargs)

    def initialize(self, evaluation_context: EvaluationContext):
        return self.provider.initialize(evaluation_context)

    def shutdown(self):
        return self.provider.shutdown()

    def get_metadata(self) -> Metadata:
        return self.provider.get_metadata()

    def get_provider_hooks(self) -> List[Hook]:
        return self.provider.get_provider_hooks()
