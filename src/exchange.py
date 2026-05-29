import ccxt
from src.config import Config


def get_exchange(exchange_name: str = "binance", testnet: bool = None) -> ccxt.Exchange:
    """Returns a configured and market-loaded exchange instance."""
    use_testnet = testnet if testnet is not None else (Config.ENV == "testnet")

    # Common options applied to all Binance instances
    _binance_opts = {
        "enableRateLimit": True,          # respect exchange rate limits automatically
        "adjustForTimeDifference": True,  # auto-sync local clock with Binance server
        "options": {
            "recvWindow": 60000,          # 60s window — handles clock drift up to 60s
        },
    }

    if exchange_name == "binance":
        if use_testnet:
            exchange = ccxt.binance({
                **_binance_opts,
                "apiKey": Config.BINANCE_TESTNET_API_KEY,
                "secret": Config.BINANCE_TESTNET_SECRET,
            })
            exchange.set_sandbox_mode(True)
        else:
            exchange = ccxt.binance({
                **_binance_opts,
                "apiKey": Config.BINANCE_API_KEY,
                "secret": Config.BINANCE_SECRET,
            })

    elif exchange_name == "bybit":
        exchange = ccxt.bybit({
            "apiKey": Config.BYBIT_API_KEY,
            "secret": Config.BYBIT_SECRET,
        })

    else:
        raise ValueError(f"Unsupported exchange: {exchange_name}")

    exchange.load_markets()
    return exchange
