from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class EvaluateRequest(_message.Message):
    __slots__ = ("txn_id", "customer_id", "from_account_id", "to_account_id", "amount", "channel", "device_id", "ip", "payee_trusted", "account_idle_days", "event_time_ms")
    TXN_ID_FIELD_NUMBER: _ClassVar[int]
    CUSTOMER_ID_FIELD_NUMBER: _ClassVar[int]
    FROM_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    TO_ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    CHANNEL_FIELD_NUMBER: _ClassVar[int]
    DEVICE_ID_FIELD_NUMBER: _ClassVar[int]
    IP_FIELD_NUMBER: _ClassVar[int]
    PAYEE_TRUSTED_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_IDLE_DAYS_FIELD_NUMBER: _ClassVar[int]
    EVENT_TIME_MS_FIELD_NUMBER: _ClassVar[int]
    txn_id: str
    customer_id: str
    from_account_id: str
    to_account_id: str
    amount: int
    channel: str
    device_id: str
    ip: str
    payee_trusted: bool
    account_idle_days: int
    event_time_ms: int
    def __init__(self, txn_id: _Optional[str] = ..., customer_id: _Optional[str] = ..., from_account_id: _Optional[str] = ..., to_account_id: _Optional[str] = ..., amount: _Optional[int] = ..., channel: _Optional[str] = ..., device_id: _Optional[str] = ..., ip: _Optional[str] = ..., payee_trusted: bool = ..., account_idle_days: _Optional[int] = ..., event_time_ms: _Optional[int] = ...) -> None: ...

class RuleHit(_message.Message):
    __slots__ = ("rule", "weight", "shadow", "detail")
    RULE_FIELD_NUMBER: _ClassVar[int]
    WEIGHT_FIELD_NUMBER: _ClassVar[int]
    SHADOW_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    rule: str
    weight: int
    shadow: bool
    detail: str
    def __init__(self, rule: _Optional[str] = ..., weight: _Optional[int] = ..., shadow: bool = ..., detail: _Optional[str] = ...) -> None: ...

class EvaluateResponse(_message.Message):
    __slots__ = ("score", "decision", "reasons", "hits", "degraded")
    SCORE_FIELD_NUMBER: _ClassVar[int]
    DECISION_FIELD_NUMBER: _ClassVar[int]
    REASONS_FIELD_NUMBER: _ClassVar[int]
    HITS_FIELD_NUMBER: _ClassVar[int]
    DEGRADED_FIELD_NUMBER: _ClassVar[int]
    score: int
    decision: str
    reasons: _containers.RepeatedScalarFieldContainer[str]
    hits: _containers.RepeatedCompositeFieldContainer[RuleHit]
    degraded: bool
    def __init__(self, score: _Optional[int] = ..., decision: _Optional[str] = ..., reasons: _Optional[_Iterable[str]] = ..., hits: _Optional[_Iterable[_Union[RuleHit, _Mapping]]] = ..., degraded: bool = ...) -> None: ...
