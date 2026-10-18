def is_palindrome(s):
    """Return True if s reads the same forwards and backwards once case is ignored and every character
    that is not a letter or digit is dropped ('A man, a plan, a canal: Panama' -> True)."""
    cleaned = [ch for ch in s if ch.isalnum()]
    return cleaned == cleaned[::-1]
