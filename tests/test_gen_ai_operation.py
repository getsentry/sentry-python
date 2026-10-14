from sentry_sdk.consts import GEN_AI_OPERATION


def test_gen_ai_operation_names_are_string_compatible():
    assert GEN_AI_OPERATION.INVOKE_AGENT == "invoke_agent"
    assert isinstance(GEN_AI_OPERATION.INVOKE_AGENT, str)
    assert GEN_AI_OPERATION.EMBEDDINGS == "embeddings"


def test_gen_ai_operation_names_are_unique():
    values = [operation.value for operation in GEN_AI_OPERATION]
    assert len(values) == len(set(values))
