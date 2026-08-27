"""Public surface of the http capability.

`HttpClient` is the intended entry point for callers outside this
package. This module stays decoupled from `connections_export.archive`: a
`Fetched` merely duck-types what `Archive.write_response` needs.
"""

from connections_export.http.auth import (
    AuthError,
    AuthStrategy,
    BasicAuth,
    KerberosAuth,
    PasteTokenAuth,
    SspiAuth,
)
from connections_export.http.client import HttpClient, RetryPolicy
from connections_export.http.results import FailureKind, Fetched, FetchFailure, FetchResult

__all__ = [
    "AuthError",
    "AuthStrategy",
    "BasicAuth",
    "FailureKind",
    "FetchFailure",
    "Fetched",
    "FetchResult",
    "HttpClient",
    "KerberosAuth",
    "PasteTokenAuth",
    "RetryPolicy",
    "SspiAuth",
]
