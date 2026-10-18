def percent(part, whole):
    """Return part as a percentage of whole, rounded to one decimal (1, 3 -> 33.3); 0.0 when whole is zero."""
    if whole == 0:
        return 0.0
    return round(100.0 * part / whole, 1)
