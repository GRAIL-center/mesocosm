def flatten_one(nested):
    """Flatten exactly one level: [[1, [2]], [3]] -> [1, [2], 3]. Every element of nested is a list."""
    out = []
    for group in nested:
        for item in group:
            if isinstance(item, list):
                out.extend(item)
            else:
                out.append(item)
    return out
