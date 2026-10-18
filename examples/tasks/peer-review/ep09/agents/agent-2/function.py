def gcd(a, b):
    """Return the greatest common divisor of two non-negative integers, not both zero (gcd(12, 18) -> 6,
    gcd(0, 5) -> 5)."""
    while b:
        a, b = b, a % b
    return a
