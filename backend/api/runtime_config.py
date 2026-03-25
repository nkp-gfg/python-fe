import os
from copy import deepcopy
from typing import Any


_DEFAULT_MULTIFLIGHT_SETTINGS = {
    "timeoutSeconds": int(os.environ.get("SABRE_MULTIFLIGHT_TIMEOUT_SECONDS", "10")),
    "maxAttempts": int(os.environ.get("SABRE_MULTIFLIGHT_MAX_ATTEMPTS", "6")),
    "includeCpaidEndpoint": os.environ.get(
        "SABRE_MULTIFLIGHT_INCLUDE_CPAID_ENDPOINT", "false"
    ).lower() == "true",
}

_runtime_config = {
    "multiflight": deepcopy(_DEFAULT_MULTIFLIGHT_SETTINGS),
}


def get_multiflight_settings() -> dict[str, Any]:
    return deepcopy(_runtime_config["multiflight"])


def load_runtime_config(db) -> None:
    doc = db["app_settings"].find_one({"key": "multiflight"}) or {}
    persisted = doc.get("value", {}) if isinstance(
        doc.get("value", {}), dict) else {}
    merged = deepcopy(_DEFAULT_MULTIFLIGHT_SETTINGS)
    merged.update({
        "timeoutSeconds": int(persisted.get("timeoutSeconds", merged["timeoutSeconds"])),
        "maxAttempts": int(persisted.get("maxAttempts", merged["maxAttempts"])),
        "includeCpaidEndpoint": bool(
            persisted.get("includeCpaidEndpoint",
                          merged["includeCpaidEndpoint"])
        ),
    })
    _runtime_config["multiflight"] = merged


def update_multiflight_settings(db, settings: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(_runtime_config["multiflight"])
    merged.update({
        "timeoutSeconds": int(settings["timeoutSeconds"]),
        "maxAttempts": int(settings["maxAttempts"]),
        "includeCpaidEndpoint": bool(settings["includeCpaidEndpoint"]),
    })
    db["app_settings"].update_one(
        {"key": "multiflight"},
        {"$set": {"key": "multiflight", "value": merged}},
        upsert=True,
    )
    _runtime_config["multiflight"] = merged
    return deepcopy(merged)
