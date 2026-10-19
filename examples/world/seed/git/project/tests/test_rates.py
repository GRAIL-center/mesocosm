from rates import convert


def test_identity():
    assert convert(10.0, "USD", "USD") == 10.0


def test_round_trip_is_close():
    there = convert(100.0, "USD", "EUR")
    back = convert(there, "EUR", "USD")
    assert abs(back - 100.0) < 1e-9
