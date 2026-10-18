def dedupe_keep_order(xs):
    """Return the elements of xs without duplicates, keeping the order of first appearance."""
    seen = set()
    out = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
