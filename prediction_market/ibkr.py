from __future__ import annotations

from typing import Any

from ib_async import IB, util
from ib_async.ib import StartupFetch

from .config import IBConnectionSettings


STATUS_ERROR_CODES = {2103, 2104, 2106, 2107, 2108, 2119, 2158}


def connect_ib(settings: IBConnectionSettings) -> IB:
    """Open a lightweight readonly IB connection."""
    util.startLoop()
    ib = IB()
    ib.RequestTimeout = settings.timeout
    ib.connect(
        settings.host,
        settings.port,
        clientId=settings.client_id,
        timeout=settings.timeout,
        readonly=settings.readonly,
        fetchFields=StartupFetch(0),
    )
    ib.reqMarketDataType(settings.market_data_type)
    return ib


def attach_error_collector(ib: IB) -> tuple[list[dict[str, Any]], Any]:
    """Collect non-status IB API errors so scan/quote output can be audited."""
    errors: list[dict[str, Any]] = []

    def on_error(req_id: int, error_code: int, error_string: str, contract: Any) -> None:
        if int(error_code or 0) in STATUS_ERROR_CODES:
            return
        errors.append(
            {
                "reqId": req_id,
                "errorCode": error_code,
                "errorString": error_string,
                "contract": repr(contract) if contract is not None else "",
            }
        )

    ib.errorEvent += on_error
    return errors, on_error


def detach_error_collector(ib: IB, handler: Any) -> None:
    try:
        ib.errorEvent -= handler
    except ValueError:
        pass
