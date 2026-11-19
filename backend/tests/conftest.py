"""Test process defaults.

Set before the Sabre client module is imported. SabreClient.API_CALL_DELAY is
read from the environment at import, and load_dotenv() does not override
variables that are already set.
"""

import os

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"
os.environ.setdefault("SABRE_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("SABRE_CPAID", "GF")
os.environ.setdefault("SABRE_USERNAME", "placeholder-user")
os.environ.setdefault("SABRE_PASSWORD", "placeholder-password")
os.environ.setdefault("SABRE_PSEUDO_CITY_CODE", "BAH")
os.environ.setdefault("SABRE_ORGANIZATION", "GF")
os.environ.setdefault("SABRE_DOMAIN", "GF")
