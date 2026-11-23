"""Environment for feeder tests.

SabreClient reads SABRE_API_DELAY_SECONDS when the class body is executed,
so these values must be set before that module is imported.
"""

import os

os.environ["SABRE_API_DELAY_SECONDS"] = "0"
os.environ["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"
os.environ["SABRE_BASE_URL"] = "http://127.0.0.1:9"
os.environ["SABRE_CPAID"] = "GF"
os.environ["SABRE_USERNAME"] = "placeholder-user"
os.environ["SABRE_PASSWORD"] = "placeholder-password"
os.environ["SABRE_PSEUDO_CITY_CODE"] = "XXX"
os.environ["SABRE_ORGANIZATION"] = "GF"
os.environ["SABRE_DOMAIN"] = "GF"
# Leave POSTGRES_URI unset. Sequence lookup must fail closed without a database.
os.environ.pop("POSTGRES_URI", None)
