# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import sys
import inspect
import dis
import copy

if sys.version_info[0] >= 3:
    from unittest.mock import create_autospec
else:
    from mock import create_autospec


class UndefinedBehavior(BaseException):
    """
    Tentative access of an attribute from a StrictMock that is not defined yet.
    Inherits from BaseException to avoid being caught by tested code.
    """

    __slots__ = ["strict_mock", "attr", "message"]

    def __init__(self, strict_mock, attr, message):
        super(UndefinedBehavior, self).__init__(strict_mock, attr, message)
        self.strict_mock = strict_mock
        self.attr = attr
        self.message = message

    def __str__(self):
        return (
            "{}:\n"
            "  Attribute '{}' has no behavior defined.\n"
            "  You can define behavior by assigning a value to it."
        ).format(repr(self.strict_mock), self.attr)


class NoSuchAttribute(BaseException):
    """
    Tentative of setting of an attribute from a StrictMock that is not present
    at the template class.
    Inherits from BaseException to avoid being caught by tested code.
    """

    __slots__ = ["strict_mock", "attr", "message"]

    def __init__(self, strict_mock, attr, message):
        super(NoSuchAttribute, self).__init__(strict_mock, attr, message)
        self.strict_mock = strict_mock
        self.attr = attr
        self.message = message

    def __str__(self):
        return ("{}:\n" "  No such attribute '{}'.\n" "  {}").format(
            repr(self.strict_mock), self.attr, self.message
        )


class _DescriptorProxy(object):
    def __init__(self, name):
        self.name = name
        self.attrs = {}

    def __get__(self, instance, _owner):
        if instance in self.attrs:
            return self.attrs[instance]
        else:
            raise AttributeError(
                "{}:\n  Object has no attribute '{}'".format(repr(instance), self.name)
            )

    def __set__(self, instance, value):
        self.attrs[instance] = value

    def __delete__(self, instance):
        if instance in self.attrs:
            del self.attrs[instance]


class StrictMock(object):
    """
    Mock object that won't allow any attribute access or method call, unless its
    behavior has been explicitly defined. This is meant to be a safer
    alternative to Python's standard Mock object, that will always return
    another mock when referred by default.

    StrictMock is "safe by default", meaning that it will never misbehave by
    lack of configuration. It will raise in the following situations:

    - Get/Set attribute that's not part of the specification (template or
      runtime_attrs).
    - Get attribute that is part of the specification, but has not yet been
      defined.
    - Call a method with different signature from the template.

    When appropriate, raised exceptions inherits from BaseException, in order to
    let exceptions raise the test, outside tested code, so we can get a clear
    signal of what is happening: either the mock is missing a required behavior
    or the tested code is misbehaving.
    """

    def __init__(self, template=None, runtime_attrs=None, name=None):
        """
        template: Template class to be used as a template for the mock. If the
        template class implements a context manager, empty mocks for __enter__()
        and __exit__() will be setup automatically.
        runtime_attrs: Often attributes are created within an instance's
        lifecycle, typically from __init__(). To allow mocking such attributes,
        specify their names here.
        name: an optional name for this mock instance.
        """
        if template:
            assert inspect.isclass(template), "Template must be a class."

        # avoid __getattr_ recursion
        self.__dict__["__template"] = template
        self.__dict__["__runtime_attrs"] = runtime_attrs or []
        self.__dict__["__name"] = name

        if (
            self.__template
            and hasattr(self.__template, "__enter__")
            and hasattr(self.__template, "__exit__")
        ):
            self.__enter__ = lambda: self
            self.__exit__ = lambda exc_type, exc_value, traceback: None

    @property
    def __class__(self):
        return self.__template if self.__template is not None else type(self)

    @property
    def __template(self):
        return self.__dict__["__template"]

    @property
    def __template_name(self):
        return self.__template.__name__ if self.__template else "None"

    @property
    def __runtime_attrs(self):
        return self.__dict__["__runtime_attrs"]

    def __is_runtime_attr(self, name):
        if sys.version_info[0] >= 3 and self.__template:
            for klass in self.__template.mro():
                template_init = getattr(klass, "__init__")
                if not inspect.isfunction(template_init):
                    continue
                for instruction in dis.get_instructions(template_init):
                    if (
                        instruction.opname == "STORE_ATTR"
                        and name == instruction.argval
                    ):
                        return True
        return False

    def __can_mock_attr(self, name):
        if not self.__template:
            return True
        return (
            hasattr(self.__template, name)
            or name in self.__runtime_attrs
            or name in getattr(self.__template, "__slots__", [])
            or self.__is_runtime_attr(name)
        )

    def __is_instance_method(self, f):
        return not inspect.isfunction(f) and not f.im_self

    def __get_mock_value(self, name, value):
        if hasattr(self.__template, name):
            template_function = getattr(self.__template, name)

            # If we are working with a callable we need to actually
            # set the side effect of the callable, not directly assign
            # the value to the callable
            if callable(template_function):
                if sys.version_info[0] == 2 and not self.__is_instance_method(
                    getattr(self.__template, name)
                ):
                    # This is needed for Python 2, as create_autospec breaks
                    # with TypeError when caling either static or class
                    # methods
                    value = create_autospec(
                        getattr(self.__template, name), side_effect=value
                    )
                else:
                    instance_mock = create_autospec(self.__template)
                    function_mock = getattr(instance_mock, name)
                    function_mock.side_effect = value
                    value = function_mock
        return value

    def __setattr__(self, name, value):
        if self.__can_mock_attr(name):
            if name in type(self).__dict__:
                type(self).__dict__[name].__set__(
                    self, self.__get_mock_value(name, value)
                )
            else:
                setattr(type(self), name, _DescriptorProxy(name))
                self.__setattr__(name, value)
        else:
            # If the template classs has the attribute we we haven't yet defined its
            # behavior we use a different exception than when the attribute
            # doesn't event exist in the template class
            if hasattr(self.__template, name):
                raise UndefinedBehavior(
                    self,
                    name,
                    "The attribute {} is defined in the template class "
                    "{}, but its behavior is not yet defined in this "
                    "StrictMock".format(self.__template_name),
                )
            else:
                raise NoSuchAttribute(
                    self,
                    name,
                    "Can not set attribute {} that is neither "
                    "part of template class {} or runtime_attrs={}.".format(
                        name, self.__template_name, self.__runtime_attrs
                    ),
                )

    def __getattr__(self, attr):
        if attr in type(self).__dict__:
            try:
                return type(self).__dict__[attr].__get__(self, type(self))
            except AttributeError:
                pass

        if self.__can_mock_attr(attr):
            raise UndefinedBehavior(
                self,
                attr,
                "Can not getattr() an undefined StrictMock "
                "attribute. Use setattr() to define it.",
            )
        else:
            raise AttributeError(
                "Can not getattr() an attribute '{}' that is neither part of "
                "template class {} or runtime_attrs={}.".format(
                    attr, self.__template_name, self.__runtime_attrs
                )
            )

    def __delattr__(self, attr):
        if attr in type(self).__dict__:
            type(self).__dict__[attr].__delete__(self)

    def __repr__(self):
        template = (
            " template={}.{}".format(
                self.__template.__module__, self.__template.__name__
            )
            if self.__template
            else ""
        )
        if self.__dict__["__name"]:
            name = " name={}".format(repr(self.__dict__["__name"]))
        else:
            name = ""
        return "<StrictMock 0x{:02X}{name}{template}>".format(
            id(self), name=name, template=template
        )

    def __get_copy(self):
        return type(self)(template=self.__template, runtime_attrs=self.__runtime_attrs)

    def __get_instance_attr_items(self):
        items = []
        for name in type(self).__dict__:
            descriptor_proxy = type(self).__dict__[name]
            if type(descriptor_proxy) is not _DescriptorProxy:
                continue
            if self in descriptor_proxy.attrs:
                items.append((name, descriptor_proxy.attrs[self]))
        return items

    def __copy__(self):
        self_copy = self.__get_copy()
        for name, value in self.__get_instance_attr_items():
            setattr(self_copy, name, value)
        return self_copy

    def __deepcopy__(self, memo=None):
        if memo is None:
            memo = {}
        self_copy = self.__get_copy()
        memo[id(self)] = self_copy
        for name, value in self.__get_instance_attr_items():
            setattr(self_copy, name, copy.deepcopy(value, memo))
        return self_copy