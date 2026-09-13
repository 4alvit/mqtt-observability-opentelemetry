"""Exercise the same module loading path as the container command."""

import subprocess
import sys


def test_module_entrypoint_registers_metrics_once():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import asyncio, runpy; "
            "asyncio.run = lambda coroutine: coroutine.close(); "
            "runpy.run_module('mqtt_interceptor', run_name='__main__', alter_sys=True)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "found in sys.modules" not in result.stderr
