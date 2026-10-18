def word_freq(text):
    """Return a dict mapping each word to its count, where words are whitespace-separated and compared
    case-insensitively ('A a b' -> {'a': 2, 'b': 1})."""
    out = {}
    for w in text.lower().split():
        out[w] = out.get(w, 0) + 1
    return out
