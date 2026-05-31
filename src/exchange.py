import os
import ccxt
from src.config import Config


def get_exchange(exchange_name: str = "binance", testnet: bool = None) -> ccxt.Exchange:
    """
    Returns a configured and market-loaded exchange instance.

    Security model:
      Testnet:  HMAC-SHA256 keys (testnet, no real money)
      Live:     Ed25519 asymmetric keys (preferred) OR HMAC-SHA256 fallback.

    Ed25519 setup (live only):
      1. Generate keypair: openssl genpkey -algorithm ed25519 -out private.pem
      2. Extract public:   openssl pkey -in private.pem -pubout -out public.pem
      3. Register public key on Binance (API Management → Create API → Ed25519)
      4. Set Railway env var: BINANCE_PRIVATE_KEY = <contents of private.pem>
         (leave BINANCE_API_KEY as the "API key ID" shown by Binance, BINANCE_SECRET empty)

    If BINANCE_PRIVATE_KEY is set → Ed25519 mode (more secure, recommended).
    If not set → falls back to HMAC-SHA256 using BINANCE_API_KEY + BINANCE_SECRET.
    """
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
            private_key = os.environ.get("BINANCE_PRIVATE_KEY", "").strip()
            if private_key:
                # Ed25519 mode — private key never shared with Binance, more secure
                exchange = ccxt.binance({
                    **_binance_opts,
                    "apiKey":  Config.BINANCE_API_KEY,   # the key ID from Binance
                    "secret":  "",                        # unused in Ed25519 mode
                    "options": {
                        **_binance_opts["options"],
                        "defaultType": "spot",
                    },
                    "privateKey": private_key,           # CCXT uses this for Ed25519 signing
                })
            else:
                # HMAC-SHA256 fallback — still safe with IP whitelist + no-withdrawal permission
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
