from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import httpx
from functools import lru_cache
from config import get_settings

bearer_scheme = HTTPBearer()


@lru_cache(maxsize=1)
def _get_jwks(supabase_url: str) -> dict:
    """
    Busca as chaves publicas do Supabase (JWKS).
    Fica em cache para nao buscar a cada request.
    """
    url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    settings = get_settings()
    token = credentials.credentials

    # Descobre qual algoritmo o token usa
    try:
        header = jwt.get_unverified_header(token)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail=f"Token mal formado: {e}")

    alg = header.get("alg", "HS256")

    try:
        if alg == "HS256":
            # Supabase antigo: verifica com JWT Secret string pura
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )

        else:
            # Supabase novo (ES256 etc): verifica com chave publica via JWKS
            kid = header.get("kid")
            jwks = _get_jwks(settings.supabase_url)

            # Acha a chave certa pelo kid
            public_key = None
            for key_data in jwks.get("keys", []):
                if key_data.get("kid") == kid:
                    public_key = jwt.algorithms.ECAlgorithm.from_jwk(key_data)
                    break

            if not public_key:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Chave publica nao encontrada para kid={kid}",
                )

            payload = jwt.decode(
                token,
                public_key,
                algorithms=[alg],
                options={"verify_aud": False},
            )

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado. Faca login novamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token invalido: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id: str = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token sem identificador de usuario",
        )

    return {"user_id": user_id, "payload": payload}