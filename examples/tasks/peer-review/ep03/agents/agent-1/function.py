def safe_div(a, b):
    """Return a divided by b as a float, or None when b is zero (never raise)."""
    if b == 0:
        return None
    return a // b
