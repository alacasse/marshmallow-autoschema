# MIT License
#
# Copyright (c) 2017- Delve Labs Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from datetime import datetime
from enum import Enum
from collections import namedtuple
from functools import partial
from inspect import Parameter, signature
from typing import Callable, Generic, List, Optional, TypeVar, Union

from marshmallow import fields, missing
from marshmallow.fields import List as FieldList
from marshmallow_enum import EnumField

from .utilities import FactorySchema


class Raw:
    pass


T = TypeVar('T')
SCHEMA_ATTRNAME = '__schema__'
MODEL_ATTRNAME = '__model__'


class One(Generic[T]):
    pass


class Many(Generic[T]):
    pass


def kwsift(kw, f):
    '''
    Returns the subset of the kwarg dictionary `kw` that function `f` can
    accept. If `f` is keyword-variadic, return `kw` unchanged.
    '''

    sig = signature(f)
    kw_kinds = {Parameter.KEYWORD_ONLY, Parameter.POSITIONAL_OR_KEYWORD}
    out = {}
    # go backward to catch **kwargs on the first pass
    for name, p in list(sig.parameters.items())[::-1]:
        if p.kind == p.VAR_KEYWORD:
            return kw
        elif p.kind in kw_kinds and name in kw.keys():
            out[name] = kw[name]

    return out


def get_schema_cls_name(model_cls: type) -> str:
    return model_cls.__name__ + 'Schema'


def get_schema_cls(model_cls: type) -> type:
    '''
    Returns the autogenerated schema class from an autoschemad model class.

    Args:
        model_cls:
            The class object of the model to find the schema for.

    Returns:
        The schema class object associated with the model class.

    '''
    sn = get_schema_cls_name(model_cls)
    try:
        return getattr(model_cls, SCHEMA_ATTRNAME)  # type: ignore
    except AttributeError:
        raise ValueError(
            '''{} does not appear to be a valid model, as it '
            does not have an autogenerated Schema. Expected to find '
            {} attribute, did not.'''.format(model_cls, sn))


def is_model_init(init: Callable[..., None]) -> bool:
    '''
    Check if an __init__ callable correponds to one monkeypatched by
    a schema factory.
    '''
    return '_mro_offset' in signature(init).parameters.keys()


def check_type(typ: type, *typs: type) -> bool:
    '''
    Checks by name whether a given type is one of several options

    Args:
        typ: the type to check
        *typs: the types to check against
    Returns:
        True if `typ` matches any type in `typs`.
    '''
    def get_type(t):
        # Ptyhon 3.7 no longer has the __name__ property as typing.List is no longer a class.
        # https://bugs.python.org/issue34422
        try:
            return t.__name__
        except AttributeError:
            return t._name

    return any(get_type(typ) == get_type(t) for t in typs)


Fieldspec = namedtuple(
    'fieldspec', ('default', 'annotation', 'required', 'allow_none'))


class schema_metafactory:

    def __init__(
            self, *, field_namer=lambda x: x,
            schema_base_class=FactorySchema, extended_field_map=None):
        '''
        Creates a domain-specific schema factory.

        Arguments:
            field_namer: callable taking a model attrbute name and returning the
                marshmallow field name to use in `load_from` and `dump_to`
            schema_base_class: the base schema class to use for autogenerated
                schemas.
            extended_field_map: dictionary mapping annotation types to
            marshmallow
                field types. Can be used to implement domain specific field
                types.

        Returns:
            schema_factory: class decorator wrapping a model stub class and
                returning a complete model class with a generated __schema__
                attribute, as well as automatic instance attribute setting
                logic in
                __init__.
        '''

        self.field_namer = field_namer
        self.schema_base_class = schema_base_class

        self.extended_field_map = extended_field_map or {}

    @staticmethod
    def get_primitive_field_cls(typ: type, instantiate=False)\
            -> Union[type, fields.Field]:
        '''
        Returns the `marshmallow.Field` subtype corresponding to Python type
        `typ`.

        Args:
            typ: the type to be serialized.
            instantiate: if True, a field instance will be returned. Otherwise,
                a closure over the constructor with appropriate arguments
                closed over is returned.

        Returns:
            type:

        Raises:
            ValueError: `typ` doesn't correpond to a basic field type.
        '''
        if issubclass(typ, Enum):
            field_constr = partial(EnumField, typ)

        elif typ == Raw:
            field_constr = fields.Raw

        elif typ == int:
            field_constr = fields.Integer

        elif typ == str:
            field_constr = fields.String

        elif typ == bool:
            field_constr = fields.Boolean

        elif typ == datetime:
            field_constr = partial(fields.DateTime, format='iso')

        else:
            raise ValueError(
                '{} does not correspond to a primitive field type'.format(typ)
            )

        return field_constr if not instantiate else field_constr()

    def get_field_from_annotation(
            self,
            fspec: Fieldspec,
            load_dump_to: Optional[str] = None) -> fields.Field:
        '''
        Args:
            load_dump_to:
                If not none, the load_from and dump_to parameters of the field.
                will be set to this value.
            fspec:

        Returns:
            The field class object to associate with the annotated argument.
        '''

        if fspec.annotation in self.extended_field_map:
            field_constr = self.extended_field_map[fspec.annotation]

        elif check_type(fspec.annotation, Many, One, List):
            # will get either the schema directly, or recurse into this
            # function, returning an atomic schema

            arg = fspec.annotation.__args__[0]

            try:
                nested_type = get_schema_cls(arg)
                field_constr = partial(fields.Nested, nested_type)
            except ValueError:
                nested_type = self.get_primitive_field_cls(
                    arg, instantiate=True)
                field_constr = partial(fields.List, nested_type)
        else:
            try:
                field_constr = self.get_primitive_field_cls(fspec.annotation)
            except ValueError:
                raise ValueError(
                    'Unsupported annotation "{}", which is neither a '
                    'primitive type nor a Schema.'.format(fspec.annotation)
                )

        return field_constr(
            default=(fspec.default or missing),
            many=check_type(fspec.annotation, Many, List),
            required=fspec.required,
            load_from=load_dump_to,
            dump_to=load_dump_to,
            allow_none=fspec.allow_none,
        )

    def __call__(self, model_cls: type) -> type:
        '''
        Class wrapper generating a marshmallow schema for the wrapped class.

        Uses model_cls's __init__'s signature's parameter names and
        annotations to magically figure out what fields to add.

        The following class attributes can be set on the model stub to
        control behaviour.

        Supported Class Attrbutes:
            irregular_names: dictionary mapping attribute names to schema field
                names. Overrides the translation function given to the
                metafactory.

        Arguments:
            model_cls: a stub model class with an appropriately annotated
                __init__ from which to generate a schema.

        Returns:
            model_cls: the patched model class with a __schema__ attribute
                and an attribute setting __init__.

        '''

        base_init = model_cls.__init__  # type: ignore

        # parse init to construct st_fieldspecs
        init_named_kwargs = {
            name: Fieldspec(
                default=(
                    p.default if p.default is not Parameter.empty else None
                ),
                annotation=p.annotation,
                required=p.default == p.empty,
                allow_none=p.default is None,
            )
            for name, p in signature(base_init).parameters.items()
            if p.kind == p.KEYWORD_ONLY
        }

        schema_attrs = {}

        # generate field objects from fieldspecs
        for kwname, fspec in init_named_kwargs.items():
            load_dump_to = getattr(
                model_cls, 'irregular_names', {}
            ).get(kwname, self.field_namer(kwname))

            schema_attrs[kwname] = self.get_field_from_annotation(
                fspec, load_dump_to=load_dump_to,
            )

        # construct the dependent Schema class
        # mirror the model inhertance structure in the schema, important!
        schema_bases = tuple(
            model_base.__dict__[SCHEMA_ATTRNAME]
            for model_base in model_cls.__mro__
            if SCHEMA_ATTRNAME in model_base.__dict__
        ) + (self.schema_base_class,)

        schema_cls = type(
            get_schema_cls_name(model_cls), schema_bases, schema_attrs,
        )

        setattr(model_cls, SCHEMA_ATTRNAME, schema_cls)
        setattr(schema_cls, MODEL_ATTRNAME, model_cls)

        def model_init(model_self, _mro_offset=1, **kwargs):
            '''
            Factor out the mindnumbing 'self.kwarg = kwarg' pattern.

            That should honestly be the default behaviour.
            '''

            # XXX: super(self.__class__, self).__init__ seems to fail
            # in a monkeypatched __init__ such as this one, forcing this kind
            # of manual __mro__ traversal. I'm sure something more sensible
            # can be done. This is the kind of stuff that gives metaprogramming
            # a bad name... blame super()'s super opacity

            cur_model_cls = model_self.__class__
            next_in_line = cur_model_cls.__mro__[_mro_offset]

            if is_model_init(next_in_line.__init__):
                next_in_line.__init__(
                    model_self, _mro_offset=_mro_offset + 1,
                    **kwsift(kwargs, next_in_line.__init__),
                )
            elif next_in_line is not object:
                next_in_line.__init__(
                    model_self, **kwsift(kwargs, next_in_line.__init__)
                )

            for kwname, fspec in init_named_kwargs.items():
                attr = kwargs.get(kwname, fspec.default)

                if check_type(fspec.annotation, Many, List):
                    attr = attr or []
                elif check_type(fspec.annotation, Raw):
                    attr = attr or {}
                elif callable(fspec.default):
                    attr = attr or fspec.default()

                setattr(model_self, kwname, attr)

            base_init(model_self, **kwsift(kwargs, base_init))

        def model_dump(model_self, *args, **kwargs):
            strict = kwargs.pop('strict', True)
            schema_instance = getattr(model_self, SCHEMA_ATTRNAME)(
                *args, strict=strict, **kwargs)
            return schema_instance.dump(model_self)

        def model_load(cls, data, *args, **kwargs):
            strict = kwargs.pop('strict', True)
            schema_instance = getattr(cls, SCHEMA_ATTRNAME)(
                *args, strict=strict, **kwargs)
            return schema_instance.load(data)

        model_cls.dump = model_dump
        model_cls.load = classmethod(model_load)
        model_cls.__init__ = model_init
        model_cls._field_namer = self.field_namer

        return model_cls


def validate_field(attr, validator, validate_child=True):
    '''
    Attaches a validator to the field defined from attr.

    Arguments:
        attr:
            The field name to validate. Must be a keyword_only argument
            processed by autoschema.
        validator:
            The marshmallow Validator instance to attach to the field.
        validate_child:
            If True, when the field given by `attr` is a list, will attach
            the validator to the child field. Otherwise, validates the list
            itself.
    '''

    def valdeco(model_cls):
        if not hasattr(model_cls, SCHEMA_ATTRNAME):
            raise ValueError(
                'This decorator only works on models with autogenerated '
                'schemas. Make sure it\'s on top of @autoschema')

        schema_cls = getattr(model_cls, SCHEMA_ATTRNAME)
        field = schema_cls._declared_fields[attr]
        if isinstance(field, FieldList) and validate_child:
            field.container.validators.append(validator)
        else:
            field.validators.append(validator)

        return model_cls

    return valdeco
