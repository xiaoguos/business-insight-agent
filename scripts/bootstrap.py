"""Create the first tenant administrator; no default credentials."""

import getpass
import os
from app.platform import Platform


def main():
    platform = Platform()
    email = os.getenv("BOOTSTRAP_EMAIL") or input("Administrator email: ").strip()
    tenant = os.getenv("BOOTSTRAP_TENANT") or input("Tenant identifier: ").strip()
    password = os.getenv("BOOTSTRAP_PASSWORD") or getpass.getpass(
        "Password (14+ characters): "
    )
    if not email or "@" not in email or not tenant:
        raise ValueError("Valid email and tenant required")
    platform.bootstrap(tenant, email, password)
    platform.engine.dispose()
    print("Administrator bootstrap completed (existing accounts are not overwritten).")


if __name__ == "__main__":
    main()
