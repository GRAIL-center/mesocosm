def running_max(xs):
    """Return a list where element i is the largest value among xs[0..i]. Works for negative numbers;
    an empty input gives an empty list."""
    out = []
    best = None
    for x in xs:
        best = x if best is None or x > best else best
        out.append(best)
    return out
