"""Terminal chat interface for AWS Bill Whisperer."""

from __future__ import annotations

import asyncio

from .chatbot import BillWhispererChat

BANNER = r"""
╔══════════════════════════════════════════════════════════╗
║        AWS Bill Whisperer – Interactive Chat CLI         ║
║  Ask about storage, compute, or cost quick wins in plain ║
║  English. Type 'quit' or 'exit' to leave.                ║
╚══════════════════════════════════════════════════════════╝
"""


async def main():
    chat = BillWhispererChat()
    print(BANNER)

    while True:
        try:
            user_message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye 👋")
            return

        if not user_message:
            continue
        if user_message.lower() in {"quit", "exit"}:
            print("bye 👋")
            return

        response = await chat.ask(user_message)
        print("agent> " + response.text)
        if response.commands:
            print("\nSuggested commands:")
            for cmd in response.commands[:4]:
                print(f"  - {cmd['description']}:\n    {cmd['command']}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
