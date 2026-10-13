import math
import sys
import types
from pathlib import Path

import atheris

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("mqtt_interceptor")
package.__path__ = [str(ROOT / "mqtt-interceptor/src/mqtt_interceptor")]
sys.modules[package.__name__] = package

with atheris.instrument_imports():
    from mqtt_interceptor.config import TraceConfig


def test_one_input(data: bytes) -> None:
    from pydantic import ValidationError

    try:
        config = TraceConfig.model_validate_json(data)
    except ValidationError:
        return
    assert math.isfinite(config.sample_rate) and 0 <= config.sample_rate <= 1  # nosec B101 - intentional test oracle
    assert all(isinstance(topic, str) for topic in config.topic_patterns)  # nosec B101 - intentional test oracle
    assert TraceConfig.model_validate_json(config.model_dump_json()) == config  # nosec B101 - intentional test oracle


if __name__ == "__main__":
    atheris.Setup(sys.argv, atheris.instrument_func(test_one_input))
    atheris.Fuzz()
