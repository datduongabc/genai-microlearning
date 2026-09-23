#!/usr/bin/env python3
"""Replaces the database and Moodle admin passwords with new random ones, everywhere they live.

One password lives in several places that do not read each other: .env, the role inside
Postgres, Moodle's config.php (written once at install) and the admin account inside
Moodle's database. Changing .env alone leaves the rest on the old value or stops the
services from connecting, which is why this is a script and not a checklist.

    python devops/scripts/rotate_secrets.py              rotate all four passwords
    python devops/scripts/rotate_secrets.py --dry-run    say what would change, touch nothing
    python devops/scripts/rotate_secrets.py --only worker,moodle-admin

Run it from the repository root with Docker running. It never prints a password: it says
which ones it changed and how long they are, and the new values are in .env. Only the
standard library is used.

What it does, in this order, so that a failure leaves the old passwords working:
  1. writes the new .env beside the old one, without replacing it yet;
  2. sets the new passwords on the database roles (through the local socket of the
     database container, which needs no password);
  3. replaces .env, updates Moodle's config.php and recreates the services;
  4. sets the Moodle admin password with Moodle's own CLI;
  5. checks that the old passwords are refused and the new ones accepted.
"""

import argparse
import os
import pathlib
import re
import secrets
import string
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
DB_CONTAINER = "moodle_postgres"
MOODLE_CONTAINER = "moodle_app"
BACKEND_HEALTH = "http://127.0.0.1:8000/health"

# What each --only name changes.
KINDS = {
    "db": "DB_PASSWORD",
    "runtime": "APP_RUNTIME_DB_PASSWORD",
    "worker": "APP_WORKER_DB_PASSWORD",
    "moodle-admin": "MOODLE_ADMIN_PASSWORD",
}
# Only characters that need no quoting in a URL, a SQL string, a PHP string or a shell.
SAFE = re.compile(r"[A-Za-z0-9_-]+")
LETTERS_DIGITS = string.ascii_letters + string.digits


def generate_password(length: int = 32, *, for_moodle: bool = False) -> str:
    """Random, and made only of characters that need no quoting anywhere. Moodle's default
    policy also wants a lower and an upper case letter, a digit and a non-alphanumeric
    character, so those are guaranteed for its admin account."""
    alphabet = LETTERS_DIGITS + "_-"
    while True:
        # Never starts with '-', which a command line would take for an option.
        password = secrets.choice(LETTERS_DIGITS) + "".join(secrets.choice(alphabet) for _ in range(length - 1))
        if not for_moodle:
            return password
        if (re.search(r"[a-z]", password) and re.search(r"[A-Z]", password)
                and re.search(r"\d", password) and re.search(r"[_-]", password)):
            return password


def _strip_quotes(raw: str) -> tuple[str, str]:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[0], raw[1:-1]
    return "", raw


def _with_password(url: str, user: str, password: str) -> str:
    return re.sub(r"^([\w+.-]+://)([^:@/]+):([^@]*)@", lambda m: f"{m.group(1)}{user}:{password}@", url, count=1)


def update_env(text: str, passwords: dict[str, str]) -> str:
    """`text` (the content of .env) with the given password variables replaced, the
    passwords inside every ...DATABASE_URL... variable following them, and URLs for the
    worker role added (derived from the runtime ones) when they are not there yet. Every
    other line is left exactly as it was."""
    for name, value in passwords.items():
        if not SAFE.fullmatch(value):
            raise ValueError(f"the new value of {name} contains characters that need quoting")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()

    def value_of(key: str) -> str | None:
        for line in lines:
            match = re.match(rf"^{key}=(.*)$", line)
            if match:
                return _strip_quotes(match.group(1))[1]
        return None

    owner = value_of("DB_USER")
    by_user = {"app_runtime": passwords.get("APP_RUNTIME_DB_PASSWORD"), "app_worker": passwords.get("APP_WORKER_DB_PASSWORD")}
    if owner:
        by_user[owner] = passwords.get("DB_PASSWORD")

    out, seen, runtime_urls = [], set(), {}
    for line in lines:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not match:
            out.append(line)
            continue
        key, raw = match.groups()
        seen.add(key)
        quote, value = _strip_quotes(raw)
        if key in passwords:
            out.append(f"{key}={quote}{passwords[key]}{quote}")
        elif "DATABASE_URL" in key:
            user = re.match(r"^[\w+.-]+://([^:@/]+):", value)
            if user and by_user.get(user.group(1)):
                value = _with_password(value, user.group(1), by_user[user.group(1)])
            if key.startswith("APP_RUNTIME_DATABASE_URL"):
                runtime_urls[key] = (quote, value)
            out.append(f"{key}={quote}{value}{quote}")
        else:
            out.append(line)

    for key, value in passwords.items():
        if key not in seen:
            out.append(f"{key}={value}")
    worker_password = passwords.get("APP_WORKER_DB_PASSWORD")
    if worker_password:
        for runtime_key, (quote, url) in runtime_urls.items():
            worker_key = runtime_key.replace("APP_RUNTIME", "APP_WORKER", 1)
            if worker_key not in seen:
                out.append(f"{worker_key}={quote}{_with_password(url, 'app_worker', worker_password)}{quote}")

    ended = text.endswith("\n")
    return newline.join(out) + (newline if ended else "")


def update_moodle_config(text: str, password: str) -> str:
    """Moodle's config.php with $CFG->dbpass set to `password`, and nothing else changed."""
    if not SAFE.fullmatch(password):
        raise ValueError("the new password contains characters that need quoting")
    pattern = re.compile(r"(\$CFG->dbpass\s*=\s*)'(?:[^'\\]|\\.)*'")
    if len(pattern.findall(text)) != 1:
        raise ValueError("config.php must contain exactly one $CFG->dbpass line")
    return pattern.sub(lambda m: f"{m.group(1)}'{password}'", text)


def parse_env(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if match:
            values[match.group(1)] = _strip_quotes(match.group(2))[1]
    return values


def _docker(args: list[str], *, stdin: str | None = None, extra_env: dict[str, str] | None = None,
            check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["docker", *args], input=stdin, capture_output=True, text=True, check=False, cwd=ROOT,
        env={**os.environ, **(extra_env or {})},
    )
    if check and result.returncode != 0:
        # The output of these commands never holds a password (they are given through
        # stdin or the environment), so it is safe to show what went wrong.
        raise RuntimeError(f"docker {' '.join(args[:3])} failed:\n{result.stdout}{result.stderr}")
    return result


def _set_database_passwords(env: dict[str, str], new: dict[str, str]) -> None:
    statements = []
    if "DB_PASSWORD" in new:
        statements.append(f'ALTER ROLE "{env["DB_USER"]}" PASSWORD \'{new["DB_PASSWORD"]}\';')
    for variable, role in (("APP_RUNTIME_DB_PASSWORD", "app_runtime"), ("APP_WORKER_DB_PASSWORD", "app_worker")):
        if variable in new:
            # A role that does not exist yet is created by its migration with the password
            # from .env, so there is nothing to change for it here.
            statements.append(
                f"DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN "
                f"ALTER ROLE {role} PASSWORD '{new[variable]}'; END IF; END $$;"
            )
    if statements:
        _docker(["exec", "-i", DB_CONTAINER, "psql", "-U", env["DB_USER"], "-d", env["DB_NAME"],
                 "-v", "ON_ERROR_STOP=1"], stdin="\n".join(statements) + "\n")


def _accepted(env: dict[str, str], role: str, password: str) -> bool:
    """Whether the database accepts `password` for `role` over the network (the local
    socket and loopback are trusted, so this connects to the container's own address)."""
    result = _docker(
        ["exec", "-i", "-e", "CHECK_PASSWORD", DB_CONTAINER, "sh", "-c",
         'PGPASSWORD="$CHECK_PASSWORD" psql -h "$(hostname -i)" -U "$1" -d "$2" -Atc "select 1"', "sh", role, env["DB_NAME"]],
        extra_env={"CHECK_PASSWORD": password}, check=False,
    )
    return result.returncode == 0


def _wait_for_backend(seconds: int = 180) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(BACKEND_HEALTH, timeout=3) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(3)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rotate the database and Moodle admin passwords.")
    parser.add_argument("--dry-run", action="store_true", help="say what would change and touch nothing")
    parser.add_argument("--only", default=",".join(KINDS), help=f"comma separated subset of: {', '.join(KINDS)}")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--moodle-config", default=str(ROOT / "moodle-src-523" / "config.php"))
    args = parser.parse_args(argv)

    chosen = [name.strip() for name in args.only.split(",") if name.strip()]
    unknown = [name for name in chosen if name not in KINDS]
    if unknown:
        parser.error(f"unknown --only value(s): {', '.join(unknown)}")

    env_path, config_path = pathlib.Path(args.env_file), pathlib.Path(args.moodle_config)
    env_text = env_path.read_text(encoding="utf-8")
    env = parse_env(env_text)
    for required in ("DB_USER", "DB_NAME"):
        if required not in env:
            parser.error(f"{required} is not set in {env_path}")

    new = {KINDS[name]: generate_password(for_moodle=name == "moodle-admin") for name in chosen}
    old = {name: env[name] for name in new if name in env}

    print("Passwords to change:")
    for name, value in new.items():
        print(f"  {name}: {'set' if name in env else 'not set yet'} -> new value of {len(value)} characters")
    if args.dry_run:
        print("Dry run: nothing was changed.")
        return 0

    new_env_text = update_env(env_text, new)
    staged = env_path.with_name(env_path.name + ".rotating")
    staged.write_text(new_env_text, encoding="utf-8", newline="")
    try:
        print("Setting the new passwords on the database roles...")
        _set_database_passwords(env, new)
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    os.replace(staged, env_path)
    print(f"{env_path.name} updated.")

    if "DB_PASSWORD" in new and config_path.exists():
        config_path.write_text(update_moodle_config(config_path.read_text(encoding="utf-8"), new["DB_PASSWORD"]),
                               encoding="utf-8", newline="")
        print("Moodle config.php updated.")

    print("Recreating the services with the new environment...")
    _docker(["compose", "up", "-d", "--remove-orphans"])
    # Moodle keeps config.php in OPcache and never re-reads it.
    _docker(["restart", MOODLE_CONTAINER])

    if "MOODLE_ADMIN_PASSWORD" in new:
        print("Setting the Moodle admin password...")
        deadline = time.monotonic() + 120
        while True:
            result = _docker(
                ["exec", "-i", "-e", "NEW_MOODLE_PASSWORD", MOODLE_CONTAINER, "sh", "-c",
                 'php admin/cli/reset_password.php --username="$MOODLE_ADMIN_USER" --password="$NEW_MOODLE_PASSWORD"'],
                extra_env={"NEW_MOODLE_PASSWORD": new["MOODLE_ADMIN_PASSWORD"]}, check=False,
            )
            if result.returncode == 0:
                break
            if time.monotonic() > deadline:
                raise RuntimeError("Moodle would not accept the admin password change:\n" + result.stdout + result.stderr)
            time.sleep(5)

    print("Checking...")
    problems = []
    if not _wait_for_backend():
        problems.append("the backend did not come back healthy")
    roles = {"DB_PASSWORD": env["DB_USER"], "APP_RUNTIME_DB_PASSWORD": "app_runtime", "APP_WORKER_DB_PASSWORD": "app_worker"}
    for name, role in roles.items():
        if name not in new:
            continue
        if not _accepted(env, role, new[name]):
            problems.append(f"the new {name} is not accepted by the database")
        if name in old and old[name] != new[name] and _accepted(env, role, old[name]):
            problems.append(f"the old {name} is STILL accepted by the database")
    for line in problems:
        print(f"PROBLEM: {line}")
    if not problems:
        print("The backend is healthy, the new database passwords work and the old ones are refused.")
    print(f"The new values are in {env_path}. Nothing was printed.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
