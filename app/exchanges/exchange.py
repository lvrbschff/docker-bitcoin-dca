"""Exchange adapter factory."""
from app.exchanges.bitvavo import Bitvavo
from app.exchanges.kraken import Kraken


def create_exchange(settings):
    exchange = settings["exchange"]
    if exchange == "BITVAVO":
        return Bitvavo(settings.get("bitvavo_api_key"), settings.get("bitvavo_api_secret"))
    if exchange == "KRAKEN":
        return Kraken(settings["quote"], settings.get("kraken_withdraw_key", ""), settings.get("kraken_api_key"), settings.get("kraken_api_secret"))
    raise ValueError("EXCHANGE must be BITVAVO or KRAKEN")
