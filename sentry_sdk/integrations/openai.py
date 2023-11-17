from sentry_sdk.hub import Hub
from sentry_sdk.integrations import DidNotEnable, Integration


try:
    from openai import resources
except ImportError:
    raise DidNotEnable("OpenAI is not installed")

print("init_monkey")


class OpenAiIntegration(Integration):
    identifier = "openai"

    @staticmethod
    def setup_once():
        print("monkey0?")
        # type: () -> None
        patch_openai()


def patch_openai():
    # type: () -> None
    old_open_ai_resources_chat_completions_create = (
        resources.chat.completions.Completions.create
    )

    def monkeypatched_openai(*args, **kwargs):
        hub = Hub.current

        if hub.get_integration(OpenAiIntegration) is None:
            return old_open_ai_client_create(**kwargs)

        with hub.start_span(op="openai", description="init") as span:
            span.set_data(
                "chat_completion_input",
                remove_token_from_keys(kwargs),
            )
            ret = old_open_ai_resources_chat_completions_create(*args, **kwargs)
            if ret.usage:
                span.set_data(
                    "chat_completion_output",
                    remove_token_from_keys(ret.model_dump(exclude_unset=True)),
                )

            return ret

    resources.chat.completions.Completions.create = monkeypatched_openai


# ChatCompletion(
#     id="chatcmpl-8LhQjMhPt54ydUYBmQ4N3CTtXKsss",
#     choices=[
#         Choice(
#             finish_reason="stop",
#             index=0,
#             message=ChatCompletionMessage(
#                 content="b", role="assistant", function_call=None, tool_calls=None
#             ),
#         )
#     ],
#     created=1700182525,
#     model="gpt-3.5-turbo-0613",
#     object="chat.completion",
#     system_fingerprint=None,
#     usage=CompletionUsage(completion_tokens=1, prompt_tokens=13, total_tokens=14),
# )


def remove_token_from_keys(d):
    """
    Recursively remove mentions of 'token' from the keys of the dictionary.

    :param d: The dictionary to process.
    :param token: The string to remove from the keys.
    :return: A new dictionary with the modified keys.
    """
    new_dict = {}
    for key, value in d.items():
        # Remove 'token' from the key
        new_key = key.replace("token", "tken")

        # Recursively apply the function if the value is a dictionary
        if isinstance(value, dict):
            value = remove_token_from_keys(value)

        new_dict[new_key] = value

    return new_dict
