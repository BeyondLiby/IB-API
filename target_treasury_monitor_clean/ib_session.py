from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from ib_async import IB, util
from ib_async.ib import StartupFetch

from .settings import IBSettings


def connect_ib(settings: IBSettings, *, fetch_fields: StartupFetch | None = None) -> IB:
    """Open a readonly IB connection and set the requested market-data mode."""
    util.startLoop()
    ib = IB()
    ib.connect(
        settings.host,
        settings.port,
        clientId=settings.client_id,
        timeout=10,
        readonly=settings.readonly,
        fetchFields=fetch_fields if fetch_fields is not None else StartupFetch(0),
    )
    ib.reqMarketDataType(settings.market_data_type)
    return ib


@contextmanager
def ib_connection(
    settings: IBSettings,
    *,
    fetch_fields: StartupFetch | None = None,
) -> Iterator[IB]:
    """Context manager that always disconnects from IB on exit."""
    ib = connect_ib(settings, fetch_fields=fetch_fields)
    try:
        yield ib
    finally:
        if ib.isConnected():
            ib.disconnect()

