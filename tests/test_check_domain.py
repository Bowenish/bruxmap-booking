"""POST /check-domain: the page's last email check. Only a domain ever reaches the server."""
import server


def test_invalid_domain_is_rejected_without_lookup(monkeypatch):
    monkeypatch.setattr(server, "_domain_accepts_mail", lambda d: (_ for _ in ()).throw(AssertionError("looked up")))
    for bad in ("not a domain", "", "localhost", "a.b", "someone@example.org", "x" * 300 + ".com"):
        r = server.check_domain({"domain": bad})
        assert r.status_code == 200 and b'"ok":false' in r.body and b"invalid" in r.body, bad


def test_lookup_is_normalised_and_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_domain_accepts_mail", lambda d: (calls.append(d), (True, "ok"))[1])
    server._dom_cache.clear()
    a = server.check_domain({"domain": " Example.ORG. "})
    b = server.check_domain({"domain": "example.org"})
    assert calls == ["example.org"]
    assert b'"ok":true' in a.body and b'"ok":true' in b.body
    assert a.headers["cache-control"].startswith("no-store")


def test_unresolvable_domain_reports_no_mx(monkeypatch):
    monkeypatch.setattr(server, "_domain_accepts_mail", lambda d: (False, "no-mx"))
    server._dom_cache.clear()
    r = server.check_domain({"domain": "nosuchdomain-xyz123.com"})
    assert b'"ok":false' in r.body and b"no-mx" in r.body
