"""CLI entry point for interactive Microsoft account authentication.

Exposed as console script `microsoft-mcp-auth` so it can be invoked via
`uvx --from <source> microsoft-mcp-auth` without needing a local checkout.
"""

import os
import sys

from dotenv import load_dotenv

from microsoft_mcp import auth


def main() -> None:
    # Load environment variables from .env if present (optional)
    load_dotenv()

    if not os.getenv("MICROSOFT_MCP_CLIENT_ID"):
        print("Error: MICROSOFT_MCP_CLIENT_ID environment variable is required")
        print("\nSet it before running, e.g.:")
        print('  export MICROSOFT_MCP_CLIENT_ID="<your-app-id>"')
        print('  export MICROSOFT_MCP_TENANT_ID="<your-tenant-id>"   # optional')
        sys.exit(1)

    print("Microsoft MCP Authentication")
    print("============================\n")

    accounts = auth.list_accounts()
    if accounts:
        print("Currently authenticated accounts:")
        for i, account in enumerate(accounts, 1):
            print(f"{i}. {account.username} (ID: {account.account_id})")
        print()
    else:
        print("No accounts currently authenticated.\n")

    while True:
        choice = input("Do you want to authenticate a new account? (y/n): ").lower()
        if choice == "n":
            break
        elif choice == "y":
            try:
                new_account = auth.authenticate_new_account()
                if new_account:
                    print("\n✓ Authentication successful!")
                    print(f"Signed in as: {new_account.username}")
                    print(f"Account ID: {new_account.account_id}")
                else:
                    print(
                        "\n✗ Authentication failed: Could not retrieve account information"
                    )
            except Exception as e:
                print(f"\n✗ Authentication failed: {e}")
                continue
            print()
        else:
            print("Please enter 'y' or 'n'")

    accounts = auth.list_accounts()
    if accounts:
        print("\nAuthenticated accounts summary:")
        print("==============================")
        for account in accounts:
            print(f"• {account.username}")
            print(f"  Account ID: {account.account_id}")
        print(
            "\nYou can use these account IDs with any MCP tool by passing account_id parameter."
        )
        print("Example: send_email(..., account_id='<account-id>')")
    else:
        print("\nNo accounts authenticated.")

    print("\nAuthentication complete!")


if __name__ == "__main__":
    main()
