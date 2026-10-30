"""Exchange adapter factory."""
from bitvavo import Bitvavo
from kraken import Kraken


def create_exchange(settings):
    exchange = settings["exchange"]
    if exchange == "BITVAVO":
        return Bitvavo()
    if exchange == "KRAKEN":
        return Kraken(settings["quote"], settings.get("kraken_withdraw_key", ""))
    raise ValueError("EXCHANGE must be BITVAVO or KRAKEN")
