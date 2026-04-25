from fastapi import APIRouter
from fastapi.responses import JSONResponse

from kms.store import get_kms
from jwks.cache import get_cached_keys, set_cached_keys

router = APIRouter()


@router.get("/jwks")
async def jwks_endpoint():
    cached = get_cached_keys()
    if cached is not None:
        keys = cached
    else:
        kms = get_kms()
        keys = kms.get_all_public_keys()
        set_cached_keys(keys)

    return JSONResponse(
        content={"keys": keys},
        headers={"Cache-Control": "public, max-age=300"},
    )
