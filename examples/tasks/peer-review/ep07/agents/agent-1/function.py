def second_largest(xs):
    """Return the second largest distinct value in xs, or None if there are fewer than two distinct values
    ([3, 3, 2] -> 2, [5] -> None, [4, 4] -> None)."""
    s = sorted(xs)
    if len(s) < 2:
        return None
    return s[-2]
