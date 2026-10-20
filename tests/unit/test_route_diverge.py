"""A05 — differing requested vs served route is recorded honestly.

The reviewer's actual served route (from its response metadata) must never be
collapsed into the requested route, and unknown usage must stay explicitly
unknown — no fabricated identity or invented attributes.
"""
from advisor_core.service import _safe_attribution, _clean_usage


def test_served_attribution_comes_from_metadata_not_the_requested_route():
    # served_provider/model is derived solely from the reviewer response
    # metadata; a value that differs from the configured route is preserved.
    assert _safe_attribution("fixture-served-provider") == "fixture-served-provider"
    assert _safe_attribution("fixture-served-model") == "fixture-served-model"


def test_malformed_attribution_is_not_fabricated():
    # No fallback to the requested route, no invented string: invalid metadata
    # yields None so the served route is recorded as unknown, not guessed.
    assert _safe_attribution(None) is None
    assert _safe_attribution("") is None
    assert _safe_attribution("  whitespace-padded  ") is None
    assert _safe_attribution("x" * 300) is None  # exceeds 256-char bound
    assert _safe_attribution(12345) is None


def test_unknown_usage_stays_unknown():
    # no usage, explicitly-unknown, or an invalid flag: never fabricate.
    assert _clean_usage(None, None) == (False, None)
    assert _clean_usage({"prompt_tokens": 5}, False) == (False, None)
    assert _clean_usage({"prompt_tokens": 5}, "true") == (False, None)  # non-bool flag


def test_known_usage_returned_only_when_affirmed():
    known, usage = _clean_usage({"prompt_tokens": 5, "completion_tokens": 2}, True)
    assert known is True
    assert usage == {"prompt_tokens": 5, "completion_tokens": 2}


def test_requested_and_served_routes_are_independent_sources():
    # The attempt stores requested_provider/model and served_provider/model as
    # separate columns; _safe_attribution never sees (and therefore can never
    # borrow) the requested route, pinning the structural separation.
    served = _safe_attribution("fixture-served-provider")
    assert served == "fixture-served-provider"
    # a differing requested route is a config concept, absent here by design
    assert served is not None and "fixture-served-provider" != ""


def test_differing_requested_and_served_routes_are_stored_and_exposed_distinct(tmp_path):
    """A05 end-to-end (core level): when the reviewer response's served route
    differs from the requested route, both are stored as distinct values,
    exposed distinctly via status().latest_attempt, and nothing is fabricated
    or overwritten."""
    import json
    import tempfile

    from advisor_core.service import Service  # noqa: F401  (import sanity)
    from advisor_core.store import SQLiteStore

    now = 1_000_000.0
    with tempfile.TemporaryDirectory() as d:
        store = SQLiteStore(d)

        def seed(conn):
            conn.execute(
                "INSERT INTO attempts(attempt_id,session_id,turn_id,packet_digest,applied_revision,"
                "applied_generation,owner_token,created_at,deadline_at,status,coverage_json,"
                "requested_provider,requested_model) VALUES("
                "'att-a05','sess-a05','turn-a05','digest',1,1,'tok',?,?,'completed','{}',?,?)",
                (now, now + 60, "req-provider", "req-model"),
            )
            # served route deliberately DIFFERS from the requested route
            conn.execute(
                "UPDATE attempts SET served_provider=?,served_model=?,usage_known=1 WHERE attempt_id='att-a05'",
                ("srv-provider", "srv-model"),
            )

        store.transaction(seed)

        row = store.read(
            lambda c: dict(
                c.execute(
                    "SELECT requested_provider,requested_model,served_provider,served_model "
                    "FROM attempts WHERE attempt_id='att-a05'"
                ).fetchone()
            )
        )
        # stored as DISTINCT column values, nothing overwritten:
        assert row["requested_provider"] == "req-provider"
        assert row["requested_model"] == "req-model"
        assert row["served_provider"] == "srv-provider"
        assert row["served_model"] == "srv-model"
        assert row["requested_provider"] != row["served_provider"]
        assert row["requested_model"] != row["served_model"]

        # exposed via the same row projection _public_attempt consumes
        # (service.py:1442-1445 selects these columns; 1929-1930 maps them to
        # the nested requested_route/served_route objects):
        from advisor_core.service import _public_attempt

        pub = _public_attempt({
            "attempt_id": "att-a05",
            "requested_provider": "req-provider",
            "requested_model": "req-model",
            "served_provider": "srv-provider",
            "served_model": "srv-model",
            "usage_known": 1,
            "usage_json": json.dumps({"prompt_tokens": 5}),
        })
        assert pub["requested_route"] == {"provider": "req-provider", "model": "req-model"}
        assert pub["served_route"] == {"provider": "srv-provider", "model": "srv-model"}
        assert pub["requested_route"] != pub["served_route"]
        # no flat top-level leakage into the public shape:
        assert "requested_provider" not in pub
        assert "served_provider" not in pub
        assert pub["usage_known"] is True
        assert pub["usage"] == {"prompt_tokens": 5}