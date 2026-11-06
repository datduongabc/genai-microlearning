# The API under /api, as Caddy and CloudFront serve it, so the redirect URI matches.
from starlette.applications import Starlette
from starlette.routing import Mount

from app.main import app as api

app = Starlette(routes=[Mount("/api", app=api)])
