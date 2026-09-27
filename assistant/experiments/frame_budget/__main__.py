"""Entry point: python -m assistant.experiments.frame_budget"""

from assistant.experiments.frame_budget.experiment import main

if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
