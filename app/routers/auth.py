from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from app.config import get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    """Verify API key from header."""
    settings = get_settings()
    
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="API key is missing. Provide X-API-Key header."
        )
    
    if api_key != settings.api_key:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key"
        )
    
    return api_key
