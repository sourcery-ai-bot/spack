# -*- coding: utf-8 -*-
# flake8: noqa
import marshal
import sys

PY2 = sys.version_info[0] == 2
PYPY = hasattr(sys, "pypy_translation_info")
_identity = lambda x: x

if not PY2:
    unichr = chr
    range_type = range
    text_type = str
    string_types = (str,)
    integer_types = (int,)

    iterkeys = lambda d: iter(d.keys())
    itervalues = lambda d: iter(d.values())
    iteritems = lambda d: iter(d.items())

    import pickle
    from io import BytesIO, StringIO

    NativeStringIO = StringIO

    def reraise(tp, value, tb=None):
        if value.__traceback__ is not tb:
            raise value.with_traceback(tb)
        raise value

    ifilter = filter
    imap = map
    izip = zip
    intern = sys.intern

    implements_iterator = _identity
    implements_to_string = _identity
    encode_filename = _identity

    marshal_dump = marshal.dump
    marshal_load = marshal.load

else:
    unichr = unichr
    text_type = unicode
    range_type = xrange
    string_types = (str, unicode)
    integer_types = (int, long)

    iterkeys = lambda d: d.iterkeys()
    itervalues = lambda d: d.itervalues()
    iteritems = lambda d: d.iteritems()

    import cPickle as pickle
    from cStringIO import StringIO as BytesIO, StringIO

    NativeStringIO = BytesIO

    exec("def reraise(tp, value, tb=None):\n raise tp, value, tb")

    from itertools import imap, izip, ifilter

    intern = intern

    def implements_iterator(cls):
        cls.next = cls.__next__
        del cls.__next__
        return cls

    def implements_to_string(cls):
        cls.__unicode__ = cls.__str__
        cls.__str__ = lambda x: x.__unicode__().encode("utf-8")
        return cls

    def encode_filename(filename):
        return filename.encode("utf-8") if isinstance(filename, unicode) else filename

    def marshal_dump(code, f):
        if isinstance(f, file):
            marshal.dump(code, f)
        else:
            f.write(marshal.dumps(code))

    def marshal_load(f):
        return marshal.load(f) if isinstance(f, file) else marshal.loads(f.read())


def with_metaclass(meta, *bases):
    """Create a base class with a metaclass."""
    # This requires a bit of explanation: the basic idea is to make a
    # dummy metaclass for one level of class instantiation that replaces
    # itself with the actual metaclass.
    class metaclass(type):
        def __new__(cls, name, this_bases, d):
            return meta(name, bases, d)

    return type.__new__(metaclass, "temporary_class", (), {})


try:
    from urllib.parse import quote_from_bytes as url_quote
except ImportError:
    from urllib import quote as url_quote


try:
    from collections import abc
except ImportError:
    import collections as abc


try:
    from os import fspath
except ImportError:
    try:
        from pathlib import PurePath
    except ImportError:
        PurePath = None

    def fspath(path):
        if hasattr(path, "__fspath__"):
            return path.__fspath__()

        # Python 3.5 doesn't have __fspath__ yet, use str.
        if PurePath is not None and isinstance(path, PurePath):
            return str(path)

        return path
