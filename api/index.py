"""Vercel serverless entry point."""
from main import app

# Vercel Python runtime expects the ASGI app as `app`
# The variable name must match what vercel.json routes to.
