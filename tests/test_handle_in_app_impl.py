import pytest
from approvaltests.approvals import verify

from sentry_sdk.utils import handle_in_app_impl

SAMPLE_FRAMES = [
    {
        "function": "__call__",
        "filename": "starlette/middleware/exceptions.py",
        "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/starlette/middleware/exceptions.py",
        "in_app": True,
    },
    {
        "function": "__call__",
        "filename": "fastapi/middleware/asyncexitstack.py",
        "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/middleware/asyncexitstack.py",
        "in_app": False,
    },
    {
        "function": "__call__",
        "filename": "fastapi/middleware/asyncexitstack.py",
        "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/middleware/asyncexitstack.py",
        "in_app": None,
    },
    {
        "function": "__call__",
        "filename": "starlette/routing.py",
        "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/starlette/routing.py",
    },
    {
        "function": "handle",
        "module": "starlette.routing",
        "filename": "starlette/routing.py",
        "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/starlette/routing.py",
        "in_app": True,
    },
    {
        "function": "app",
        "module": "starlette.routing2",
        "filename": "starlette/routing.py",
        "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/starlette/routing.py",
        "in_app": False,
    },
    {
        "function": "app",
        "module": "fastapi.routing3",
        "filename": "fastapi/routing.py",
        "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
        "in_app": None,
    },
    {
        "function": "run_endpoint_function",
        "module": "fastapi.routing4",
        "filename": "fastapi/routing.py",
        "abs_path": "/home/ubuntu/fastapi/.venv/lib/python3.10/site-packages/fastapi/routing.py",
    },
    {
        "function": "trigger_error",
        "module": "something",
        "filename": "something.py",
        "abs_path": "/home/ubuntu/fastapi/something.py",
        "in_app": True,
    },
    {
        "function": "trigger_error",
        "module": "bla",
        "filename": "bla.py",
        "abs_path": "/home/ubuntu/fastapi/bla/bla.py",
        "in_app": False,
    },
    {
        "function": "trigger_error",
        "module": "main",
        "filename": "main.py",
        "abs_path": "/home/ubuntu/fastapi/main.py",
        "in_app": None,
    },
    {
        "function": "trigger_error",
        "module": "blub",
        "filename": "blub.py",
        "abs_path": "/home/ubuntu/fastapi/something/blub.py",
    },
    {
        "function": "trigger_error",
        "filename": "something.py",
        "abs_path": "/home/ubuntu/fastapi/something.py",
        "in_app": True,
    },
    {
        "function": "trigger_error",
        "filename": "bla.py",
        "abs_path": "/home/ubuntu/fastapi/bla/bla.py",
        "in_app": False,
    },
    {
        "function": "trigger_error",
        "filename": "main.py",
        "abs_path": "/home/ubuntu/fastapi/main.py",
        "in_app": None,
    },
    {
        "function": "trigger_error",
        "filename": "blub.py",
        "abs_path": "/home/ubuntu/fastapi/something/blub.py",
    },
]


@pytest.mark.parametrize(
    "frames, in_app_exclude, in_app_include, default_in_app",
    [
        [
            SAMPLE_FRAMES,
            [],
            [],
            False,
        ],
        [
            SAMPLE_FRAMES,
            [],
            [],
            True,
        ],
        [
            SAMPLE_FRAMES,
            ["starlette.routing", "bla"],
            [],
            False,
        ],
        [
            SAMPLE_FRAMES,
            ["starlette.routing", "bla"],
            [],
            False,
        ],
        [
            SAMPLE_FRAMES,
            [],
            ["fastapi.routing3", "blub"],
            False,
        ],
        [
            SAMPLE_FRAMES,
            [],
            ["fastapi.routing3", "blub"],
            True,
        ],
        [
            SAMPLE_FRAMES,
            ["starlette.routing", "bla"],
            ["fastapi.routing3", "blub"],
            False,
        ],
        [
            SAMPLE_FRAMES,
            ["starlette.routing", "bla"],
            ["fastapi.routing3", "blub"],
            True,
        ],
    ],
)
def test_handle_in_app_impl(frames, in_app_exclude, in_app_include, default_in_app):
    new_frames = handle_in_app_impl(
        frames, in_app_exclude, in_app_include, default_in_app
    )
    verify(new_frames)
