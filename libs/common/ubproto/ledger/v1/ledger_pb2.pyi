from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Leg(_message.Message):
    __slots__ = ("account_id", "amount")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    amount: int
    def __init__(self, account_id: _Optional[str] = ..., amount: _Optional[int] = ...) -> None: ...

class PostJournalRequest(_message.Message):
    __slots__ = ("idempotency_key", "txn_type", "legs", "description", "metadata", "capture_hold_id")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    TXN_TYPE_FIELD_NUMBER: _ClassVar[int]
    LEGS_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    CAPTURE_HOLD_ID_FIELD_NUMBER: _ClassVar[int]
    idempotency_key: str
    txn_type: str
    legs: _containers.RepeatedCompositeFieldContainer[Leg]
    description: str
    metadata: _containers.ScalarMap[str, str]
    capture_hold_id: str
    def __init__(self, idempotency_key: _Optional[str] = ..., txn_type: _Optional[str] = ..., legs: _Optional[_Iterable[_Union[Leg, _Mapping]]] = ..., description: _Optional[str] = ..., metadata: _Optional[_Mapping[str, str]] = ..., capture_hold_id: _Optional[str] = ...) -> None: ...

class Entry(_message.Message):
    __slots__ = ("account_id", "amount", "balance_after")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    BALANCE_AFTER_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    amount: int
    balance_after: int
    def __init__(self, account_id: _Optional[str] = ..., amount: _Optional[int] = ..., balance_after: _Optional[int] = ...) -> None: ...

class PostJournalResponse(_message.Message):
    __slots__ = ("journal_id", "replayed", "txn_type", "entries")
    JOURNAL_ID_FIELD_NUMBER: _ClassVar[int]
    REPLAYED_FIELD_NUMBER: _ClassVar[int]
    TXN_TYPE_FIELD_NUMBER: _ClassVar[int]
    ENTRIES_FIELD_NUMBER: _ClassVar[int]
    journal_id: str
    replayed: bool
    txn_type: str
    entries: _containers.RepeatedCompositeFieldContainer[Entry]
    def __init__(self, journal_id: _Optional[str] = ..., replayed: bool = ..., txn_type: _Optional[str] = ..., entries: _Optional[_Iterable[_Union[Entry, _Mapping]]] = ...) -> None: ...

class GetBalanceRequest(_message.Message):
    __slots__ = ("account_id",)
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    def __init__(self, account_id: _Optional[str] = ...) -> None: ...

class Balance(_message.Message):
    __slots__ = ("account_id", "status", "ledger_balance", "available_balance", "overdraft_limit")
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    LEDGER_BALANCE_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_BALANCE_FIELD_NUMBER: _ClassVar[int]
    OVERDRAFT_LIMIT_FIELD_NUMBER: _ClassVar[int]
    account_id: str
    status: str
    ledger_balance: int
    available_balance: int
    overdraft_limit: int
    def __init__(self, account_id: _Optional[str] = ..., status: _Optional[str] = ..., ledger_balance: _Optional[int] = ..., available_balance: _Optional[int] = ..., overdraft_limit: _Optional[int] = ...) -> None: ...

class CreateHoldRequest(_message.Message):
    __slots__ = ("idempotency_key", "account_id", "amount", "reason")
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    idempotency_key: str
    account_id: str
    amount: int
    reason: str
    def __init__(self, idempotency_key: _Optional[str] = ..., account_id: _Optional[str] = ..., amount: _Optional[int] = ..., reason: _Optional[str] = ...) -> None: ...

class ReleaseHoldRequest(_message.Message):
    __slots__ = ("hold_id",)
    HOLD_ID_FIELD_NUMBER: _ClassVar[int]
    hold_id: str
    def __init__(self, hold_id: _Optional[str] = ...) -> None: ...

class Hold(_message.Message):
    __slots__ = ("hold_id", "account_id", "amount", "status")
    HOLD_ID_FIELD_NUMBER: _ClassVar[int]
    ACCOUNT_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    hold_id: str
    account_id: str
    amount: int
    status: str
    def __init__(self, hold_id: _Optional[str] = ..., account_id: _Optional[str] = ..., amount: _Optional[int] = ..., status: _Optional[str] = ...) -> None: ...
