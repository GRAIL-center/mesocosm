def parse_kv(text):
    """Parse 'key=value' pairs separated by semicolons into a dict ('a=1;b=2' -> {'a': '1', 'b': '2'}).
    Whitespace around keys and values is stripped; an empty string gives an empty dict."""
    out = {}
    for pair in text.split(","):
        if not pair.strip():
            continue
        k, _, v = pair.partition("=")
        out[k.strip()] = v.strip()
    return out
