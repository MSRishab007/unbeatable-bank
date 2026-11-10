from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class GetRequest(_message.Message):
    __slots__ = ("key", "r")
    KEY_FIELD_NUMBER: _ClassVar[int]
    R_FIELD_NUMBER: _ClassVar[int]
    key: str
    r: int
    def __init__(self, key: _Optional[str] = ..., r: _Optional[int] = ...) -> None: ...

class GetResponse(_message.Message):
    __slots__ = ("found", "type", "value", "siblings", "context", "counter", "replicas", "repaired")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    SIBLINGS_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    COUNTER_FIELD_NUMBER: _ClassVar[int]
    REPLICAS_FIELD_NUMBER: _ClassVar[int]
    REPAIRED_FIELD_NUMBER: _ClassVar[int]
    found: bool
    type: str
    value: bytes
    siblings: _containers.RepeatedScalarFieldContainer[bytes]
    context: str
    counter: int
    replicas: int
    repaired: bool
    def __init__(self, found: bool = ..., type: _Optional[str] = ..., value: _Optional[bytes] = ..., siblings: _Optional[_Iterable[bytes]] = ..., context: _Optional[str] = ..., counter: _Optional[int] = ..., replicas: _Optional[int] = ..., repaired: bool = ...) -> None: ...

class PutRequest(_message.Message):
    __slots__ = ("key", "value", "context", "ttl_seconds", "w")
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    TTL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    W_FIELD_NUMBER: _ClassVar[int]
    key: str
    value: bytes
    context: str
    ttl_seconds: int
    w: int
    def __init__(self, key: _Optional[str] = ..., value: _Optional[bytes] = ..., context: _Optional[str] = ..., ttl_seconds: _Optional[int] = ..., w: _Optional[int] = ...) -> None: ...

class PutResponse(_message.Message):
    __slots__ = ("context", "acks", "sloppy")
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    ACKS_FIELD_NUMBER: _ClassVar[int]
    SLOPPY_FIELD_NUMBER: _ClassVar[int]
    context: str
    acks: int
    sloppy: bool
    def __init__(self, context: _Optional[str] = ..., acks: _Optional[int] = ..., sloppy: bool = ...) -> None: ...

class PutIfAbsentRequest(_message.Message):
    __slots__ = ("key", "value", "ttl_seconds", "r", "w")
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    TTL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    R_FIELD_NUMBER: _ClassVar[int]
    W_FIELD_NUMBER: _ClassVar[int]
    key: str
    value: bytes
    ttl_seconds: int
    r: int
    w: int
    def __init__(self, key: _Optional[str] = ..., value: _Optional[bytes] = ..., ttl_seconds: _Optional[int] = ..., r: _Optional[int] = ..., w: _Optional[int] = ...) -> None: ...

class PutIfAbsentResponse(_message.Message):
    __slots__ = ("created", "value")
    CREATED_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    created: bool
    value: bytes
    def __init__(self, created: bool = ..., value: _Optional[bytes] = ...) -> None: ...

class IncrementRequest(_message.Message):
    __slots__ = ("key", "delta", "ttl_seconds", "w")
    KEY_FIELD_NUMBER: _ClassVar[int]
    DELTA_FIELD_NUMBER: _ClassVar[int]
    TTL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    W_FIELD_NUMBER: _ClassVar[int]
    key: str
    delta: int
    ttl_seconds: int
    w: int
    def __init__(self, key: _Optional[str] = ..., delta: _Optional[int] = ..., ttl_seconds: _Optional[int] = ..., w: _Optional[int] = ...) -> None: ...

class IncrementResponse(_message.Message):
    __slots__ = ("value", "acks", "sloppy")
    VALUE_FIELD_NUMBER: _ClassVar[int]
    ACKS_FIELD_NUMBER: _ClassVar[int]
    SLOPPY_FIELD_NUMBER: _ClassVar[int]
    value: int
    acks: int
    sloppy: bool
    def __init__(self, value: _Optional[int] = ..., acks: _Optional[int] = ..., sloppy: bool = ...) -> None: ...

class ClusterStatusRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class NodeInfo(_message.Message):
    __slots__ = ("node_id", "address", "alive", "is_self")
    NODE_ID_FIELD_NUMBER: _ClassVar[int]
    ADDRESS_FIELD_NUMBER: _ClassVar[int]
    ALIVE_FIELD_NUMBER: _ClassVar[int]
    IS_SELF_FIELD_NUMBER: _ClassVar[int]
    node_id: str
    address: str
    alive: bool
    is_self: bool
    def __init__(self, node_id: _Optional[str] = ..., address: _Optional[str] = ..., alive: bool = ..., is_self: bool = ...) -> None: ...

class ClusterStatusResponse(_message.Message):
    __slots__ = ("node_id", "nodes", "n", "r", "w", "keys", "pending_hints", "stats", "blocked_peers", "simulated_down")
    class StatsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    NODE_ID_FIELD_NUMBER: _ClassVar[int]
    NODES_FIELD_NUMBER: _ClassVar[int]
    N_FIELD_NUMBER: _ClassVar[int]
    R_FIELD_NUMBER: _ClassVar[int]
    W_FIELD_NUMBER: _ClassVar[int]
    KEYS_FIELD_NUMBER: _ClassVar[int]
    PENDING_HINTS_FIELD_NUMBER: _ClassVar[int]
    STATS_FIELD_NUMBER: _ClassVar[int]
    BLOCKED_PEERS_FIELD_NUMBER: _ClassVar[int]
    SIMULATED_DOWN_FIELD_NUMBER: _ClassVar[int]
    node_id: str
    nodes: _containers.RepeatedCompositeFieldContainer[NodeInfo]
    n: int
    r: int
    w: int
    keys: int
    pending_hints: int
    stats: _containers.ScalarMap[str, int]
    blocked_peers: _containers.RepeatedScalarFieldContainer[str]
    simulated_down: bool
    def __init__(self, node_id: _Optional[str] = ..., nodes: _Optional[_Iterable[_Union[NodeInfo, _Mapping]]] = ..., n: _Optional[int] = ..., r: _Optional[int] = ..., w: _Optional[int] = ..., keys: _Optional[int] = ..., pending_hints: _Optional[int] = ..., stats: _Optional[_Mapping[str, int]] = ..., blocked_peers: _Optional[_Iterable[str]] = ..., simulated_down: bool = ...) -> None: ...

class KeyInfoRequest(_message.Message):
    __slots__ = ("key",)
    KEY_FIELD_NUMBER: _ClassVar[int]
    key: str
    def __init__(self, key: _Optional[str] = ...) -> None: ...

class ReplicaState(_message.Message):
    __slots__ = ("node_id", "reachable", "found", "counter", "siblings", "digest")
    NODE_ID_FIELD_NUMBER: _ClassVar[int]
    REACHABLE_FIELD_NUMBER: _ClassVar[int]
    FOUND_FIELD_NUMBER: _ClassVar[int]
    COUNTER_FIELD_NUMBER: _ClassVar[int]
    SIBLINGS_FIELD_NUMBER: _ClassVar[int]
    DIGEST_FIELD_NUMBER: _ClassVar[int]
    node_id: str
    reachable: bool
    found: bool
    counter: int
    siblings: int
    digest: str
    def __init__(self, node_id: _Optional[str] = ..., reachable: bool = ..., found: bool = ..., counter: _Optional[int] = ..., siblings: _Optional[int] = ..., digest: _Optional[str] = ...) -> None: ...

class KeyInfoResponse(_message.Message):
    __slots__ = ("preference_list", "replicas")
    PREFERENCE_LIST_FIELD_NUMBER: _ClassVar[int]
    REPLICAS_FIELD_NUMBER: _ClassVar[int]
    preference_list: _containers.RepeatedScalarFieldContainer[str]
    replicas: _containers.RepeatedCompositeFieldContainer[ReplicaState]
    def __init__(self, preference_list: _Optional[_Iterable[str]] = ..., replicas: _Optional[_Iterable[_Union[ReplicaState, _Mapping]]] = ...) -> None: ...

class PingRequest(_message.Message):
    __slots__ = ("from_node",)
    FROM_NODE_FIELD_NUMBER: _ClassVar[int]
    from_node: str
    def __init__(self, from_node: _Optional[str] = ...) -> None: ...

class PingResponse(_message.Message):
    __slots__ = ("node_id",)
    NODE_ID_FIELD_NUMBER: _ClassVar[int]
    node_id: str
    def __init__(self, node_id: _Optional[str] = ...) -> None: ...

class ReplicaGetRequest(_message.Message):
    __slots__ = ("from_node", "key")
    FROM_NODE_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    from_node: str
    key: str
    def __init__(self, from_node: _Optional[str] = ..., key: _Optional[str] = ...) -> None: ...

class ReplicaGetResponse(_message.Message):
    __slots__ = ("found", "envelope")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    found: bool
    envelope: str
    def __init__(self, found: bool = ..., envelope: _Optional[str] = ...) -> None: ...

class ReplicaMergeRequest(_message.Message):
    __slots__ = ("from_node", "key", "envelope", "hint_for")
    FROM_NODE_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    HINT_FOR_FIELD_NUMBER: _ClassVar[int]
    from_node: str
    key: str
    envelope: str
    hint_for: str
    def __init__(self, from_node: _Optional[str] = ..., key: _Optional[str] = ..., envelope: _Optional[str] = ..., hint_for: _Optional[str] = ...) -> None: ...

class ReplicaMergeResponse(_message.Message):
    __slots__ = ("ok",)
    OK_FIELD_NUMBER: _ClassVar[int]
    ok: bool
    def __init__(self, ok: bool = ...) -> None: ...

class KeyDigest(_message.Message):
    __slots__ = ("key", "digest")
    KEY_FIELD_NUMBER: _ClassVar[int]
    DIGEST_FIELD_NUMBER: _ClassVar[int]
    key: str
    digest: str
    def __init__(self, key: _Optional[str] = ..., digest: _Optional[str] = ...) -> None: ...

class KeyEnvelope(_message.Message):
    __slots__ = ("key", "envelope")
    KEY_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    key: str
    envelope: str
    def __init__(self, key: _Optional[str] = ..., envelope: _Optional[str] = ...) -> None: ...

class SyncRequest(_message.Message):
    __slots__ = ("from_node", "digests")
    FROM_NODE_FIELD_NUMBER: _ClassVar[int]
    DIGESTS_FIELD_NUMBER: _ClassVar[int]
    from_node: str
    digests: _containers.RepeatedCompositeFieldContainer[KeyDigest]
    def __init__(self, from_node: _Optional[str] = ..., digests: _Optional[_Iterable[_Union[KeyDigest, _Mapping]]] = ...) -> None: ...

class SyncResponse(_message.Message):
    __slots__ = ("updates", "want")
    UPDATES_FIELD_NUMBER: _ClassVar[int]
    WANT_FIELD_NUMBER: _ClassVar[int]
    updates: _containers.RepeatedCompositeFieldContainer[KeyEnvelope]
    want: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, updates: _Optional[_Iterable[_Union[KeyEnvelope, _Mapping]]] = ..., want: _Optional[_Iterable[str]] = ...) -> None: ...

class SetFaultsRequest(_message.Message):
    __slots__ = ("blocked_peers", "down")
    BLOCKED_PEERS_FIELD_NUMBER: _ClassVar[int]
    DOWN_FIELD_NUMBER: _ClassVar[int]
    blocked_peers: _containers.RepeatedScalarFieldContainer[str]
    down: bool
    def __init__(self, blocked_peers: _Optional[_Iterable[str]] = ..., down: bool = ...) -> None: ...

class SetFaultsResponse(_message.Message):
    __slots__ = ("ok",)
    OK_FIELD_NUMBER: _ClassVar[int]
    ok: bool
    def __init__(self, ok: bool = ...) -> None: ...
