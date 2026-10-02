        calls.append(True)

    sentry_init(integrations=[FastApiIntegration()])

    app = FastAPI(dependencies=[Depends(custom_dependency)])

    @app.get("/")
    async def _root():
        return {"message": "ok"}

    client = TestClient(app)
    response = client.get("/")

    assert response.json() == {"message": "ok"}
    assert calls == [True]


def test_global_dependency_runs_before_existing_dependencies(sentry_init):
    seen_transaction_names = []

    def custom_dependency():
        transaction = sentry_sdk.get_current_scope().transaction
        seen_transaction_names.append(transaction.name if transaction else None)

    sentry_init(
        auto_enabling_integrations=False,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=1.0,
    )

    app = FastAPI(dependencies=[Depends(custom_dependency)])

    @app.get("/items/{item_id}")
    async def _get_item(item_id: int):
        return {"item_id": item_id}

    response = TestClient(app).get("/items/123")

    assert response.status_code == 200
    assert seen_transaction_names == ["/items/{item_id}"]


@pytest.mark.skipif(
    FASTAPI_VERSION < (0, 121),
    reason="FastAPI < 0.121 uses Starlette's request_response implementation",
)
def test_global_dependency_captures_request_data(sentry_init, capture_events):
    sentry_init(
        auto_enabling_integrations=False,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        send_default_pii=True,
    )

    app = FastAPI()

    @app.post("/message")
    async def _message():
        capture_message("request body captured")
        return {"message": "ok"}

    events = capture_events()

    response = TestClient(app).post("/message", json=BODY_JSON)

    assert response.status_code == 200
    (event,) = events
    assert event["request"]["data"] == BODY_JSON


def test_global_dependency_does_not_break_websockets(sentry_init):
    sentry_init(integrations=[FastApiIntegration()])

    app = FastAPI()

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("ok")

    client = TestClient(app)

    with client.websocket_connect("/ws") as websocket:
        assert websocket.receive_text() == "ok"


@pytest.mark.parametrize("endpoint", ["/sync/thread_ids", "/async/thread_ids"])
def test_active_thread_id_span_streaming(sentry_init, capture_items, endpoint):
    sentry_init(
        auto_enabling_integrations=False,  # Ensure httpx is not auto-enabled; its legacy start_span interferes with streaming mode
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=1.0,
        trace_lifecycle="stream",
    )
    app = fastapi_app_factory()

    items = capture_items("span")

    client = TestClient(app)
    response = client.get(endpoint)
    assert response.status_code == 200

    data = json.loads(response.content)

    sentry_sdk.flush()

    segments = [item.payload for item in items if item.payload.get("is_segment")]
    assert len(segments) == 1
    assert str(data["active"]) == segments[0]["attributes"]["thread.id"]


@pytest.mark.parametrize("span_streaming", [True, False])
@pytest.mark.asyncio