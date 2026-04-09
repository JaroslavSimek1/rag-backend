import os
from dataclasses import dataclass

import requests
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError

# Internal URL (backend → keycloak inside Docker network)
KEYCLOAK_INTERNAL_URL = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
# Public URL (what appears in token issuer — matches what the browser sees)
KEYCLOAK_PUBLIC_URL = os.getenv("KEYCLOAK_PUBLIC_URL", "http://localhost:8080")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "rag")

JWKS_URL = f"{KEYCLOAK_INTERNAL_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
ISSUER = f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}"

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token",
    auto_error=False,
)

_jwks_cache = None


@dataclass
class UserInfo:
    id: str
    username: str
    role: str


def _get_jwks():
    global _jwks_cache
    if _jwks_cache is None:
        try:
            resp = requests.get(JWKS_URL, timeout=10)
            resp.raise_for_status()
            _jwks_cache = resp.json()
        except Exception as e:
            print(f"[Auth] Failed to fetch JWKS from {JWKS_URL}: {e}")
            raise HTTPException(status_code=503, detail="Auth service unavailable")
    return _jwks_cache


def _get_signing_key(token: str):
    jwks = _get_jwks()
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    for key in jwks.get("keys", []):
        if key["kid"] == kid:
            return key
    # Key not found — maybe rotated, clear cache and retry once
    global _jwks_cache
    _jwks_cache = None
    jwks = _get_jwks()
    for key in jwks.get("keys", []):
        if key["kid"] == kid:
            return key
    raise HTTPException(status_code=401, detail="Token signing key not found")


def decode_token(token: str) -> dict:
    key = _get_signing_key(token)
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=ISSUER,
            options={"verify_aud": False},
        )
        return payload
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
) -> UserInfo:
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(token)
    roles = payload.get("realm_access", {}).get("roles", [])
    return UserInfo(
        id=payload.get("sub", ""),
        username=payload.get("preferred_username", ""),
        role="admin" if "admin" in roles else "user",
    )


async def get_current_admin(user: UserInfo = Depends(get_current_user)) -> UserInfo:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
