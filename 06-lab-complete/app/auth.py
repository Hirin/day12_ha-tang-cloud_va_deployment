from fastapi import HTTPException, Security
from fastapi.security.api_key import APIKeyHeader


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def build_api_key_dependency(expected_api_key: str):
    def verify_api_key(api_key: str = Security(api_key_header)) -> str:
        if not api_key or api_key != expected_api_key:
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing API key. Include header: X-API-Key: <key>",
            )
        return api_key

    return verify_api_key
