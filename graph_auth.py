# graph_auth.py — Acquire an app-only Microsoft Graph token via OAuth2 client credentials

import os
import requests
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# DEPLOYMENT: these three values must match the Azure AD app registration for the target environment
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
SCOPE = "https://graph.microsoft.com/.default"


# Request and return an app-only Graph API access token using client credentials
def get_graph_token():
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials",
        "scope": SCOPE,
    }

    try:
        resp = requests.post(TOKEN_URL, data=data)
        resp.raise_for_status()
        logger.info("Graph API token acquired successfully")
        return resp.json()["access_token"]
    except requests.exceptions.HTTPError as e:
        logger.error("Graph token request failed: %s %s", e.response.status_code, e.response.text)
        raise
    except Exception as e:
        logger.exception("Unexpected error acquiring Graph token")
        raise