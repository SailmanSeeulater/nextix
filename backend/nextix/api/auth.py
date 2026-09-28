"""Single-user MVP auth: a static bearer token from ``NEXTIX_API_TOKEN``.

``require_user`` is the one seam to change when GitHub OAuth is added:
return a real principal from a session instead of comparing a static token.
"""

import hmac
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from nextix.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)

# The value .env.example ships with (and the settings default). Anyone who read the
# example knows it, so it is never accepted as a credential (the web app does the same).
EXAMPLE_TOKEN = "change-me"


def configured_api_token(settings: Settings) -> str | None:
    """NEXTIX_API_TOKEN, or None when it is unset, blank, or still the example value.

    None means the API fails closed: every authenticated route answers 401.
    """
    token = settings.nextix_api_token
    if not token.strip() or token.strip() == EXAMPLE_TOKEN:
        return None
    return token


@dataclass(frozen=True)
class Principal:
    name: str


async def require_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    expected = configured_api_token(settings)
    if (
        not expected
        or creds is None
        or not hmac.compare_digest(creds.credentials.encode(), expected.encode())
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Principal(name="owner")
