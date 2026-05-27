"""CLI helper for generating the argon2 password hash used by the web UI.

Usage::

    python -m dragontag.tools.hash_password 'my-password' > secrets/password.txt

The output is a single argon2 hash string; the web app reads it via the
``AIO_PASSWORD_FILE`` env var (set in ``docker-compose.yml``).
"""
import sys

from argon2 import PasswordHasher


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m dragontag.tools.hash_password <password>", file=sys.stderr)
        sys.exit(2)
    print(PasswordHasher().hash(sys.argv[1]))


if __name__ == "__main__":
    main()
