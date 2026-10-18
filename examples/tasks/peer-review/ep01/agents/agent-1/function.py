def clamp(x, lo, hi):
    """Return x limited to the closed interval [lo, hi]: lo if x < lo, hi if x > hi, else x. Assumes lo <= hi."""
    return max(lo, min(x, hi))
