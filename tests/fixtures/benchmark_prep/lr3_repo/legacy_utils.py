def normalize_name(name):
    if isinstance(name, unicode):  # noqa: F821
        text = name
    else:
        text = unicode(name, "utf-8")  # noqa: F821
    return text.strip().lower()


def ratio(numerator, denominator):
    return long(numerator) / long(denominator)  # noqa: F821
