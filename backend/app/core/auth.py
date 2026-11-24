"""Operator authentication and authorization security module for RootLens.

Enforces strict identity and role boundaries:
1. Cryptographically verified `Authorization: Bearer <token>` using HMAC-SHA256 (HS256).
2. Canonical authenticated developer/operator tokens for local development and testing.
3. Explicit Trusted-Gateway boundary: `X-Operator-*` headers are strictly REJECTED
   unless the request arrives through an explicitly configured trusted proxy presenting
   the matching `X-Gateway-Secret`. Arbitrary client-supplied headers are never trusted.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import base64
import hmac
import hashlib
from typing import Any, Dict, Optional, Set
from fastapi import Header, HTTPException, status

from app.core.config import settings

AUTHORIZED_DECISION_ROLES: Set[str] = {
    "OPERATOR",
    "INCIDENT_RESPONDER",
    "LEAD_SRE",
    "SRE",
    "ADMIN",
}

DEV_TOKENS: Dict[str, Dict[str, Any]] = {
    "dev-operator-token": {
        "sub": "op-default",
        "name": "On-Call Operator",
        "email": "operator@rootlens.internal",
        "role": "OPERATOR",
    },
    "dev-lead-sre-token": {
        "sub": "op-lead-sre",
        "name": "Lead SRE",
        "email": "lead-sre@rootlens.internal",
        "role": "LEAD_SRE",
    },
    "dev-admin-token": {
        "sub": "op-admin",
        "name": "System Administrator",
        "email": "admin@rootlens.internal",
        "role": "ADMIN",
    },
    "dev-viewer-token": {
        "sub": "guest-viewer",
        "name": "Read-Only Viewer",
        "email": "viewer@rootlens.internal",
        "role": "VIEWER",
    },
}


@dataclass(frozen=True)
class OperatorIdentity:
    """Authenticated and authorization-checked operator context."""

    operator_id: str
    operator_name: str
    operator_email: str
    role: str
    is_authorized: bool = False


def create_signed_token(
    payload: Dict[str, Any],
    secret_key: Optional[str] = None,
    expires_in_seconds: Optional[int] = 3600,
    issuer: Optional[str] = None,
    audience: Optional[str] = None,
) -> str:
    """Create an HMAC-SHA256 (HS256) signed token with standard claims."""
    now_ts = int(datetime.now(timezone.utc).timestamp())
    claims = dict(payload)

    if "iat" not in claims:
        claims["iat"] = now_ts
    if expires_in_seconds is not None and "exp" not in claims:
        claims["exp"] = now_ts + expires_in_seconds

    iss = issuer if issuer is not None else settings.AUTH_TOKEN_ISSUER
    if iss and "iss" not in claims:
        claims["iss"] = iss

    aud = audience if audience is not None else settings.AUTH_TOKEN_AUDIENCE
    if aud and "aud" not in claims:
        claims["aud"] = aud

    secret = (secret_key or settings.AUTH_SECRET_KEY).encode("utf-8")
    header_json = json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8")
    payload_json = json.dumps(claims, separators=(",", ":")).encode("utf-8")

    b64_header = base64.urlsafe_b64encode(header_json).decode("utf-8").rstrip("=")
    b64_payload = base64.urlsafe_b64encode(payload_json).decode("utf-8").rstrip("=")

    msg = f"{b64_header}.{b64_payload}".encode("utf-8")
    signature = hmac.new(secret, msg, hashlib.sha256).digest()
    b64_sig = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")

    return f"{b64_header}.{b64_payload}.{b64_sig}"


def verify_signed_token(
    token: str,
    secret_key: Optional[str] = None,
    expected_issuer: Optional[str] = None,
    expected_audience: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Verify an HMAC-SHA256 signed token and return claims if valid."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    b64_header, b64_payload, b64_sig = parts

    # 1. Header Validation: Ensure valid base64 and strict HS256 algorithm
    header_pad = b64_header + "=" * ((4 - len(b64_header) % 4) % 4)
    try:
        header_bytes = base64.urlsafe_b64decode(header_pad.encode("utf-8"))
        header = json.loads(header_bytes.decode("utf-8"))
        if not isinstance(header, dict) or header.get("alg") != "HS256":
            return None
    except Exception:
        return None

    # 2. Secret Key Validation
    effective_secret = secret_key or settings.AUTH_SECRET_KEY
    is_prod = settings.ENVIRONMENT.lower() in {"production", "prod"}
    if is_prod and (
        not effective_secret
        or effective_secret == "rootlens-insecure-dev-secret-key-change-in-production"
        or len(effective_secret) < 32
    ):
        return None

    secret = effective_secret.encode("utf-8")

    # 3. Cryptographic Signature Verification
    msg = f"{b64_header}.{b64_payload}".encode("utf-8")
    expected_sig = hmac.new(secret, msg, hashlib.sha256).digest()

    sig_pad = b64_sig + "=" * ((4 - len(b64_sig) % 4) % 4)
    try:
        actual_sig = base64.urlsafe_b64decode(sig_pad.encode("utf-8"))
    except Exception:
        return None

    if not hmac.compare_digest(expected_sig, actual_sig):
        return None

    # 4. Payload Claims Validation
    payload_pad = b64_payload + "=" * ((4 - len(b64_payload) % 4) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(payload_pad.encode("utf-8"))
        data = json.loads(payload_bytes.decode("utf-8"))
        if not isinstance(data, dict):
            return None
    except Exception:
        return None

    # Expiration (exp) check
    now_ts = datetime.now(timezone.utc).timestamp()
    if "exp" in data:
        try:
            exp_val = float(data["exp"])
            if now_ts > exp_val:
                return None  # Expired
        except (ValueError, TypeError):
            return None

    # Not-Before (nbf) check
    if "nbf" in data:
        try:
            nbf_val = float(data["nbf"])
            if now_ts < nbf_val:
                return None  # Not yet active
        except (ValueError, TypeError):
            return None

    # Issuer (iss) check
    target_iss = expected_issuer if expected_issuer is not None else settings.AUTH_TOKEN_ISSUER
    if target_iss and "iss" in data:
        if str(data["iss"]) != str(target_iss):
            return None

    # Audience (aud) check
    target_aud = expected_audience if expected_audience is not None else settings.AUTH_TOKEN_AUDIENCE
    if target_aud and "aud" in data:
        if str(data["aud"]) != str(target_aud):
            return None

    return data


async def get_current_operator(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_gateway_secret: Optional[str] = Header(None, alias="X-Gateway-Secret"),
    x_operator_id: Optional[str] = Header(None, alias="X-Operator-Id"),
    x_operator_name: Optional[str] = Header(None, alias="X-Operator-Name"),
    x_operator_email: Optional[str] = Header(None, alias="X-Operator-Email"),
    x_operator_role: Optional[str] = Header(None, alias="X-Operator-Role"),
) -> OperatorIdentity:
    """Extract and authenticate the calling operator.

    Trust hierarchy:
    1. Cryptographically verified `Authorization: Bearer <token>` or recognized dev token.
    2. Trusted Gateway: `X-Operator-*` headers are accepted ONLY IF `TRUSTED_GATEWAY_ENABLED`
       is active and `X-Gateway-Secret` matches `GATEWAY_SHARED_SECRET`.
    3. Untrusted direct gateway headers are strictly rejected with HTTP 401.
    """
    token_claims: Optional[Dict[str, Any]] = None

    # 1. Bearer Token Verification
    if authorization:
        parts = authorization.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            raw_token = parts[1]
        elif len(parts) == 1:
            raw_token = parts[0]
        else:
            raw_token = None

        if raw_token:
            if raw_token in DEV_TOKENS:
                is_prod = settings.ENVIRONMENT.lower() in {"production", "prod"}
                if not settings.ALLOW_DEV_TOKENS or is_prod:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Built-in developer tokens are disabled in production or when ALLOW_DEV_TOKENS is False.",
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                token_claims = DEV_TOKENS[raw_token]
            else:
                verified = verify_signed_token(raw_token)
                if verified is not None:
                    token_claims = verified
                else:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid, expired, or tampered authentication token.",
                        headers={"WWW-Authenticate": "Bearer"},
                    )

    # 2. Trusted Gateway Header Verification
    is_gateway_trusted = False
    has_gateway_headers = bool(x_operator_id or x_operator_role or x_operator_email)

    if has_gateway_headers:
        if (
            settings.TRUSTED_GATEWAY_ENABLED
            and settings.GATEWAY_SHARED_SECRET
            and x_gateway_secret
            and hmac.compare_digest(x_gateway_secret, settings.GATEWAY_SHARED_SECRET)
        ):
            is_gateway_trusted = True
        else:
            # If client supplied spoofable gateway headers without a valid Bearer token:
            if not token_claims:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=(
                        "Direct client-supplied identity headers ('X-Operator-*') are untrusted and rejected. "
                        "Authentication requires a valid 'Authorization: Bearer <token>' or verified gateway secret."
                    ),
                    headers={"WWW-Authenticate": "Bearer"},
                )

    # 3. Resolve Identity Context
    if token_claims:
        resolved_id = str(token_claims.get("sub") or token_claims.get("operator_id") or "op-default")
        resolved_email = str(token_claims.get("email") or f"{resolved_id}@rootlens.internal")
        resolved_name = str(token_claims.get("name") or resolved_id.replace("-", " ").title())
        resolved_role = str(token_claims.get("role") or "OPERATOR").strip().upper()
    elif is_gateway_trusted:
        resolved_id = x_operator_id or "op-gateway-default"
        resolved_email = x_operator_email or f"{resolved_id}@rootlens.internal"
        resolved_name = x_operator_name or resolved_id.replace("-", " ").title()
        resolved_role = (x_operator_role or "OPERATOR").strip().upper()
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided. Expected 'Authorization: Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    is_authorized = resolved_role in AUTHORIZED_DECISION_ROLES

    return OperatorIdentity(
        operator_id=resolved_id,
        operator_name=resolved_name,
        operator_email=resolved_email,
        role=resolved_role,
        is_authorized=is_authorized,
    )


async def require_authorized_operator(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_gateway_secret: Optional[str] = Header(None, alias="X-Gateway-Secret"),
    x_operator_id: Optional[str] = Header(None, alias="X-Operator-Id"),
    x_operator_name: Optional[str] = Header(None, alias="X-Operator-Name"),
    x_operator_email: Optional[str] = Header(None, alias="X-Operator-Email"),
    x_operator_role: Optional[str] = Header(None, alias="X-Operator-Role"),
) -> OperatorIdentity:
    """Enforce that the calling operator has an authorized role (OPERATOR, SRE, ADMIN, etc.).

    Raises 401 UNAUTHORIZED if not authenticated.
    Raises 403 FORBIDDEN if the authenticated role is not authorized.
    """
    operator = await get_current_operator(
        authorization=authorization,
        x_gateway_secret=x_gateway_secret,
        x_operator_id=x_operator_id,
        x_operator_name=x_operator_name,
        x_operator_email=x_operator_email,
        x_operator_role=x_operator_role,
    )

    if not operator.is_authorized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Operator '{operator.operator_id}' with role '{operator.role}' is not authorized "
                f"to confirm or reject hypotheses. Required one of: {sorted(list(AUTHORIZED_DECISION_ROLES))}"
            ),
        )

    return operator
