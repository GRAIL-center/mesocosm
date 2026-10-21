def title_case(s):
    """Capitalize the first letter of every whitespace-separated word and lowercase the rest
    ('hello wORLD' -> 'Hello World')."""
    words = s.split()
    if not words:
        return ""
    return " ".join([words[0].capitalize()] + words[1:])
