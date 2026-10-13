import asyncio
import hashlib
import hmac

import httpx
import pytest

import graph_client
from conftest import FAKE_TOKEN

SECRET = "s" * 32


def client_with(handler):
    return graph_client.GraphClient(FAKE_TOKEN, SECRET, transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def test_get_sends_token_and_appsecret_proof():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"id": "1"})

    assert run(client_with(handler).get("act_1", {"fields": "id"})) == {"id": "1"}

    [request] = seen
    expected_proof = hmac.new(SECRET.encode(), FAKE_TOKEN.encode(), hashlib.sha256).hexdigest()
    assert request.url.host == "graph.facebook.com"
    assert request.url.path == f"/{graph_client.GRAPH_API_VERSION}/act_1"
    assert request.headers["authorization"] == f"Bearer {FAKE_TOKEN}"
    assert "access_token" not in request.url.params  # token never appears in a URL
    assert request.url.params["appsecret_proof"] == expected_proof
    assert request.url.params["fields"] == "id"


def test_get_without_app_secret_omits_proof():
    seen = []
    client = graph_client.GraphClient(FAKE_TOKEN, None, transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200, json={})))

    run(client.get("me"))

    assert "appsecret_proof" not in seen[0].url.params


def test_graph_error_is_raised_without_secrets():
    error = {"error": {"message": "Invalid parameter", "code": 100, "error_subcode": 33}}

    with pytest.raises(graph_client.GraphError) as exc:
        run(client_with(lambda r: httpx.Response(400, json=error)).get("act_1"))

    assert exc.value.code == 100
    assert "Invalid parameter" in str(exc.value)
    assert FAKE_TOKEN not in str(exc.value)


def test_network_error_is_wrapped_without_url():
    def handler(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)

    with pytest.raises(graph_client.GraphError) as exc:
        run(client_with(handler).get("act_1"))

    assert "ConnectError" in str(exc.value)
    assert FAKE_TOKEN not in str(exc.value)


def test_non_json_response_is_an_error():
    with pytest.raises(graph_client.GraphError):
        run(client_with(lambda r: httpx.Response(502, text="<html>bad gateway</html>")).get("act_1"))


def test_get_all_follows_cursor_pages():
    pages = {
        None: {"data": [{"id": "1"}, {"id": "2"}], "paging": {"cursors": {"after": "A"}, "next": "https://graph.facebook.com/next"}},
        "A": {"data": [{"id": "3"}], "paging": {"cursors": {"after": "B"}}},
    }

    def handler(request):
        return httpx.Response(200, json=pages[request.url.params.get("after")])

    items = run(client_with(handler).get_all("act_1/campaigns", {"fields": "id"}))

    assert [item["id"] for item in items] == ["1", "2", "3"]


def test_get_all_respects_limit_and_max_pages():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [{"id": str(len(calls))}], "paging": {"cursors": {"after": "x"}, "next": "n"}})

    items = run(client_with(handler).get_all("act_1/campaigns", max_items=3))

    assert len(items) == 3
    assert len(calls) == 3


def test_from_env_requires_token():
    with pytest.raises(graph_client.GraphError, match="META_ACCESS_TOKEN"):
        graph_client.GraphClient.from_env({})


def test_check_token_uses_shared_api_version():
    import check_token

    assert check_token.GRAPH_DEBUG_URL == f"{graph_client.GRAPH_BASE_URL}/debug_token"


def test_iter_pages_yields_each_page():
    pages = {None: {"data": [{"id": "1"}], "paging": {"cursors": {"after": "A"}, "next": "n"}}, "A": {"data": [{"id": "2"}]}}

    async def collect():
        client = client_with(lambda request: httpx.Response(200, json=pages[request.url.params.get("after")]))
        return [page async for page in client.iter_pages("x/media", page_size=1)]

    assert run(collect()) == [[{"id": "1"}], [{"id": "2"}]]


@pytest.mark.parametrize("payload", [["not", "a", "dict"], {"error": "plain string"}, {"error": ["x"]}])
def test_unexpected_payload_shapes_raise_graph_error(payload):
    with pytest.raises(graph_client.GraphError):
        run(client_with(lambda r: httpx.Response(200, json=payload)).get("act_1"))
