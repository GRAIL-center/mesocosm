def chunks(xs, n):
    """Split xs into consecutive lists of n elements each; the last chunk may be shorter. n >= 1."""
    return [xs[i:i + n] for i in range(0, len(xs), n)]
