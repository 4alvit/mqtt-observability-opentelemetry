"""Run the service without importing its implementation twice."""

import asyncio

from mqtt_interceptor.app import main

if __name__ == "__main__":
    asyncio.run(main())
