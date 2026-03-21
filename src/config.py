"""
DynamoDB-backed runtime configuration.

Stores operational settings in a CONFIG#SETTINGS record in the same DynamoDB
table as tasks. Settings are re-read each poll cycle / task start so changes
take effect without restarting services.

Env vars serve as initial defaults. Once the DynamoDB record exists, it takes
precedence.
"""

import logging
import os

log = logging.getLogger(__name__)

DEFAULTS = {
    "max_concurrent_runners": int(os.getenv("MAX_CONCURRENT_RUNNERS", "1")),
    "min_spawn_interval": int(os.getenv("MIN_SPAWN_INTERVAL", "300")),
    "task_timeout": int(os.getenv("TASK_TIMEOUT", "900")),
    "budget_daily_usd": float(os.getenv("BUDGET_DAILY_USD", "0")),
}

VALIDATORS = {
    "max_concurrent_runners": lambda v: isinstance(v, int) and 1 <= v <= 4,
    "min_spawn_interval": lambda v: isinstance(v, int) and 0 <= v <= 3600,
    "task_timeout": lambda v: isinstance(v, int) and 60 <= v <= 3600,
    "budget_daily_usd": lambda v: isinstance(v, (int, float)) and 0 <= v <= 1000,
}

_CONFIG_PK = "CONFIG#GLOBAL"
_CONFIG_SK = "SETTINGS"


def _get_table():
    import boto3

    region = os.getenv("AWS_REGION", "us-west-2")
    table_name = os.getenv("DYNAMO_TABLE", "agent-tasks")
    ddb = boto3.resource("dynamodb", region_name=region)
    return ddb.Table(table_name)


def get_settings():
    # type: () -> Dict[str, Any]
    """Read settings from DynamoDB, falling back to env-based defaults."""
    try:
        table = _get_table()
        resp = table.get_item(Key={"pk": _CONFIG_PK, "sk": _CONFIG_SK})
        item = resp.get("Item", {})
    except Exception:
        log.warning("Failed to read config from DynamoDB, using defaults", exc_info=True)
        return dict(DEFAULTS)

    merged = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in item:
            val = item[key]
            # DynamoDB stores numbers as Decimal
            from decimal import Decimal

            if isinstance(val, Decimal):
                val = float(val) if "." in str(val) else int(val)
            merged[key] = val
    return merged


def update_settings(patch):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """Validate and update settings in DynamoDB. Returns the full merged settings."""
    errors = []
    clean = {}  # type: Dict[str, Any]
    for key, val in patch.items():
        if key not in DEFAULTS:
            errors.append("unknown setting: %s" % key)
            continue
        # Coerce types
        expected_type = type(DEFAULTS[key])
        try:
            if expected_type is int:
                val = int(val)
            elif expected_type is float:
                val = float(val)
        except (TypeError, ValueError):
            errors.append("%s: invalid type (expected %s)" % (key, expected_type.__name__))
            continue
        validator = VALIDATORS.get(key)
        if validator and not validator(val):
            errors.append("%s: value %r out of range" % (key, val))
            continue
        clean[key] = val

    if errors:
        raise ValueError("; ".join(errors))

    if not clean:
        return get_settings()

    from decimal import Decimal

    table = _get_table()
    update_parts = []
    names = {}  # type: Dict[str, str]
    values = {}  # type: Dict[str, Any]
    for i, (key, val) in enumerate(clean.items()):
        alias = "#k%d" % i
        placeholder = ":v%d" % i
        update_parts.append("%s = %s" % (alias, placeholder))
        names[alias] = key
        values[placeholder] = Decimal(str(val)) if isinstance(val, float) else val

    table.update_item(
        Key={"pk": _CONFIG_PK, "sk": _CONFIG_SK},
        UpdateExpression="SET " + ", ".join(update_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )
    return get_settings()
