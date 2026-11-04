import httpx

from paperdesk.context import S2_KEY, OPENALEX_KEY, _add_openalex_key


class _Secrets:
    def __init__(self, **kv):
        self.kv = kv

    def get(self, name):
        return self.kv.get(name)


def _req(url):
    return httpx.Request("GET", url)


def test_s2_key_goes_only_to_the_s2_host_as_a_header():
    secrets = _Secrets(**{S2_KEY: "s2-secret", OPENALEX_KEY: "oa-secret"})
    s2 = _req("https://api.semanticscholar.org/graph/v1/paper/search?query=x")
    _add_openalex_key(secrets, s2)
    assert s2.headers["x-api-key"] == "s2-secret" and "s2-secret" not in str(s2.url)
    other = _req("https://api.crossref.org/works?query=x")
    _add_openalex_key(secrets, other)
    assert "x-api-key" not in other.headers and "secret" not in str(other.url)
    oa = _req("https://api.openalex.org/works?search=x")
    _add_openalex_key(secrets, oa)
    assert "api_key=oa-secret" in str(oa.url) and "x-api-key" not in oa.headers


def test_no_key_saved_changes_nothing():
    r = _req("https://api.semanticscholar.org/graph/v1/paper/search?query=x")
    _add_openalex_key(_Secrets(), r)
    assert "x-api-key" not in r.headers


def test_s2_key_endpoints_roundtrip(api):
    assert api.get("/settings/s2-key").json() == {"has_key": False}
    assert api.put("/settings/s2-key", {"api_key": "  abc123  "}).json() == {"has_key": True}
    assert api.get("/settings/s2-key").json() == {"has_key": True}
    assert api.put("/settings/s2-key", {"api_key": "  "}).status_code == 400
    assert api.delete("/settings/s2-key").json() == {"has_key": False}
