def count_vowels(s):
    """Return how many characters of s are vowels (a, e, i, o, u), counting upper and lower case alike."""
    return sum(1 for ch in s.lower() if ch in "aeiou")
