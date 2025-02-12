"""
Commonly useful converters.
"""

from __future__ import absolute_import, division, print_function

from ._compat import PY2
from ._make import NOTHING, Factory, pipe


if not PY2:
    import inspect
    import typing


__all__ = [
    "pipe",
    "optional",
    "default_if_none",
]


def optional(converter):
    """
    A converter that allows an attribute to be optional. An optional attribute
    is one which can be set to ``None``.

    Type annotations will be inferred from the wrapped converter's, if it
    has any.

    :param callable converter: the converter that is used for non-``None``
        values.

    .. versionadded:: 17.1.0
    """

    def optional_converter(val):
        return None if val is None else converter(val)

    if not PY2:
        sig = None
        try:
            sig = inspect.signature(converter)
        except (ValueError, TypeError):  # inspect failed
            pass
        if sig:
            params = list(sig.parameters.values())
            if params and params[0].annotation is not inspect.Parameter.empty:
                optional_converter.__annotations__["val"] = typing.Optional[
                    params[0].annotation
                ]
            if sig.return_annotation is not inspect.Signature.empty:
                optional_converter.__annotations__["return"] = typing.Optional[
                    sig.return_annotation
                ]

    return optional_converter


def default_if_none(default=NOTHING, factory=None):
    """
    A converter that allows to replace ``None`` values by *default* or the
    result of *factory*.

    :param default: Value to be used if ``None`` is passed. Passing an instance
       of `attr.Factory` is supported, however the ``takes_self`` option
       is *not*.
    :param callable factory: A callable that takes no parameters whose result
       is used if ``None`` is passed.

    :raises TypeError: If **neither** *default* or *factory* is passed.
    :raises TypeError: If **both** *default* and *factory* are passed.
    :raises ValueError: If an instance of `attr.Factory` is passed with
       ``takes_self=True``.

    .. versionadded:: 18.2.0
    """
    if default is NOTHING and factory is None:
        raise TypeError("Must pass either `default` or `factory`.")

    if default is not NOTHING and factory is not None:
        raise TypeError(
            "Must pass either `default` or `factory` but not both."
        )

    if factory is not None:
        default = Factory(factory)

    if isinstance(default, Factory):
        if default.takes_self:
            raise ValueError(
                "`takes_self` is not supported by default_if_none."
            )

        def default_if_none_converter(val):
            return val if val is not None else default.factory()

    else:

        def default_if_none_converter(val):
            return val if val is not None else default

    return default_if_none_converter
