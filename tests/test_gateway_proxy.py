"""What the gateway must preserve when it forwards a request.

A proxy bug is the worst kind to find late, because every service behind it looks correct
in isolation and the fault only appears through the console. This one did exactly that:
repeated query parameters were collapsed to their last value, so a multi-select filter
narrowed to one choice and produced a screen that was entirely plausible and wrong.
"""
from starlette.datastructures import QueryParams


def test_repeated_query_parameters_survive_the_proxy():
    """The regression.

    ``httpx`` iterates a mapping with ``.items()``. Starlette's ``QueryParams.items()``
    keeps only the last value for a repeated key, so passing ``request.query_params``
    straight through silently dropped every value but the last. Selecting UPI and NEFT on
    a dashboard filtered on NEFT alone — every multi-select filter in the console was
    affected: rails, families, severities, regions, products, segments, states,
    fmr_categories and dispositions.
    """
    q = QueryParams("rails=UPI&rails=NEFT&families=VEL&families=LAY&days=7")

    # What the old code passed.
    assert list(q.items()) == [("rails", "NEFT"), ("families", "LAY"), ("days", "7")]

    # What it passes now.
    forwarded = list(q.multi_items())
    assert forwarded.count(("rails", "UPI")) == 1
    assert forwarded.count(("rails", "NEFT")) == 1
    assert forwarded.count(("families", "VEL")) == 1
    assert forwarded.count(("families", "LAY")) == 1
    assert ("days", "7") in forwarded


def test_the_gateway_forwards_every_value_not_just_the_last():
    """Asserted against the source, so the fix cannot be undone by a tidy-up that
    'simplifies' the call back to passing the mapping."""
    import inspect

    from services.gateway.app import main

    src = inspect.getsource(main)
    assert "multi_items()" in src, \
        "the gateway must forward repeated query parameters, not collapse them"
    assert "params=request.query_params," not in src, \
        "passing the QueryParams mapping directly drops repeated values"


def test_a_single_valued_query_is_unaffected():
    q = QueryParams("days=7&rail=UPI")
    assert list(q.multi_items()) == [("days", "7"), ("rail", "UPI")]


def test_an_empty_query_forwards_nothing():
    assert list(QueryParams("").multi_items()) == []
