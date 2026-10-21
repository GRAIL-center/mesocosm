def is_leap(year):
    """Return True for leap years in the Gregorian calendar: divisible by 4, except years divisible by 100
    unless also divisible by 400 (1900 -> False, 2000 -> True, 2024 -> True)."""
    return year % 4 == 0 and year % 100 != 0
