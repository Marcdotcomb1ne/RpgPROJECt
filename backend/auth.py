from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import base64
from jwt.exceptions import InvalidTokenError as JWTError
from config import get_settings

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    Validates the Supabase JWT from the Authorization header.
    Returns the decoded token payload (includes 'sub' = user UUID).
    """
    settings = get_settings()
    token = credentials.credentials

    # O Supabase JWT Secret é base64 — PyJWT precisa dos bytes decodificados
    try:
        secret_bytes = base64.b64decode(settings.supabase_jwt_secret)
    except Exception:
        # Se nao for base64 valido, tenta usar como string direta
        secret_bytes = settings.supabase_jwt_secret.encode("utf-8")

    try:
        payload = jwt.decode(
            token,
            secret_bytes,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token invalido: {e}. Verifique SUPABASE_JWT_SECRET no .env",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id: str = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token sem identificador de usuario",
        )

    return {"user_id": user_id, "payload": payload}