"""Registers an LMS that may launch this tool, with the values the LMS shows for the tool.

    PYTHONPATH=. python scripts/register_lti_platform.py --issuer https://moodle.example.edu --client-id AbC123 \\
        --auth-login-url https://moodle.example.edu/mod/lti/auth.php \\
        --jwks-url https://moodle.example.edu/mod/lti/certs.php \\
        --deployment 1 [--deployment 2] [--links-moodle]

Run with DATABASE_URL, the owner's URL: the request role may only read platforms, and the owner
session is for migrations, tests and scripts like this one, never for app/. Running it
again with the same issuer and client id updates the URLs and adds deployments.
"""

import argparse

from sqlalchemy import select

from app.db import SessionLocal
from app.models import LtiDeployment, LtiPlatform


def register(db, *, issuer: str, client_id: str, auth_login_url: str, jwks_url: str,
             deployments: list[str], links_moodle: bool) -> LtiPlatform:
    for url in (auth_login_url, jwks_url):
        # The keys must come over TLS, or anyone on the path could sign launches. Plain http
        # only for a local LMS.
        if not url.startswith(("https://", "http://localhost", "http://127.0.0.1", "http://moodle")):
            raise SystemExit(f"{url} must be https")
    platform = db.scalar(select(LtiPlatform).where(LtiPlatform.issuer == issuer, LtiPlatform.client_id == client_id))
    if platform is None:
        platform = LtiPlatform(issuer=issuer, client_id=client_id, auth_login_url=auth_login_url, jwks_url=jwks_url)
        db.add(platform)
    platform.auth_login_url, platform.jwks_url, platform.links_moodle = auth_login_url, jwks_url, links_moodle
    db.flush()
    known = set(db.scalars(select(LtiDeployment.deployment_id).where(LtiDeployment.platform_id == platform.id)))
    for deployment in deployments:
        if deployment not in known:
            db.add(LtiDeployment(platform_id=platform.id, deployment_id=deployment))
    db.commit()
    return platform


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--auth-login-url", required=True)
    parser.add_argument("--jwks-url", required=True)
    parser.add_argument("--deployment", action="append", required=True, dest="deployments")
    parser.add_argument("--links-moodle", action="store_true",
                        help="this is the project's Moodle: confirm Moodle user and course ids from launches")
    args = parser.parse_args()
    with SessionLocal() as db:
        platform = register(db, issuer=args.issuer, client_id=args.client_id, auth_login_url=args.auth_login_url,
                            jwks_url=args.jwks_url, deployments=args.deployments, links_moodle=args.links_moodle)
        print(f"registered {platform.issuer} client {platform.client_id}, deployments {', '.join(args.deployments)}")


if __name__ == "__main__":
    main()
