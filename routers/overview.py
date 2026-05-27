from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from rbac import require_any_role
import crud

router = APIRouter()


@router.get("/overview")
async def overview(
    current_user: dict = Depends(require_any_role()),
    db: Session = Depends(get_db),
):
    return crud.get_overview(
        db, user_id=current_user["id"], role=current_user["app_role"]
    )
