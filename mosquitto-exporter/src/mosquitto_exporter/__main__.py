"""Run the service without importing its implementation twice."""

import asyncio

from mosquitto_exporter.app import main

if __name__ == "__main__":
    asyncio.run(main())
