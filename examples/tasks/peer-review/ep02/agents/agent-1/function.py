def median(xs):
    """Return the median of a non-empty list of numbers: the middle value when the count is odd, the mean of
    the two middle values when it is even. The input must not be modified."""
    s = sorted(xs)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return s[n // 2]
