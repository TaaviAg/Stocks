"""Set or change the password other devices use to reach the tracker.

    .venv\\Scripts\\python.exe tools\\set_password.py

Typed without echo and asked twice. Stored only as a salted scrypt hash in
data/auth.json. Changing it signs every device out.
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import auth    # noqa: E402


def main():
    print("Password for opening the tracker from your phone.")
    print("At least 8 characters. Nothing is shown while you type.\n")
    first = getpass.getpass("New password: ")
    if len(first) < 8:
        print("\nToo short - use at least 8 characters. Nothing was changed.")
        return 1
    if getpass.getpass("Same again:   ") != first:
        print("\nThe two did not match. Nothing was changed.")
        return 1
    replacing = auth.enabled()
    auth.set_password(first)
    print("\nPassword %s." % ("changed - every device has been signed out"
                              if replacing else "set"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
