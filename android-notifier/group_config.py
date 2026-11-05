"""The single group explicitly selected for this installation."""
import unicodedata

GROUP = '软件工程(唐班)'

def normalized_name(value):
    return unicodedata.normalize('NFKC',value or '').strip().casefold()

def matches(value):
    return normalized_name(value)==normalized_name(GROUP)

def meta_key(name):
    return GROUP+':'+name
