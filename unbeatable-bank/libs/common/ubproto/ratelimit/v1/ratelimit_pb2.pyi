from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Descriptor(_message.Message):
    __slots__ = ("scope", "id")
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    scope: str
    id: str
    def __init__(self, scope: _Optional[str] = ..., id: _Optional[str] = ...) -> None: ...

class CheckRequest(_message.Message):
    __slots__ = ("policy_set", "descriptors", "cost", "amount")
    POLICY_SET_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTORS_FIELD_NUMBER: _ClassVar[int]
    COST_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    policy_set: str
    descriptors: _containers.RepeatedCompositeFieldContainer[Descriptor]
    cost: int
    amount: int
    def __init__(self, policy_set: _Optional[str] = ..., descriptors: _Optional[_Iterable[_Union[Descriptor, _Mapping]]] = ..., cost: _Optional[int] = ..., amount: _Optional[int] = ...) -> None: ...

class CheckResponse(_message.Message):
    __slots__ = ("allowed", "limited_by", "limit", "remaining", "retry_after_seconds", "degraded")
    ALLOWED_FIELD_NUMBER: _ClassVar[int]
    LIMITED_BY_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    REMAINING_FIELD_NUMBER: _ClassVar[int]
    RETRY_AFTER_SECONDS_FIELD_NUMBER: _ClassVar[int]
    DEGRADED_FIELD_NUMBER: _ClassVar[int]
    allowed: bool
    limited_by: str
    limit: int
    remaining: int
    retry_after_seconds: int
    degraded: bool
    def __init__(self, allowed: bool = ..., limited_by: _Optional[str] = ..., limit: _Optional[int] = ..., remaining: _Optional[int] = ..., retry_after_seconds: _Optional[int] = ..., degraded: bool = ...) -> None: ...
