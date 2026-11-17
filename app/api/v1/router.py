"""V1 API Router aggregation."""
from fastapi import APIRouter, Depends
from app.core.auth import require_identity
from app.api.v1.routes.auth import router as auth_router
from app.api.v1.routes.health import router as health_router
from app.api.v1.routes.ready import router as ready_router
from app.api.v1.routes.imports import router as imports_router
from app.api.v1.routes.demo import router as demo_router
from app.api.v1.routes.merchants import router as merchants_router
from app.api.v1.routes.merchant_reviews import router as merchant_reviews_router
from app.api.v1.routes.merchant_aliases import router as merchant_aliases_router
from app.api.v1.routes.commitments import router as commitments_router
from app.api.v1.routes.insights import router as insights_router
from app.api.v1.routes.financial_impact import router as financial_impact_router
from app.api.v1.routes.savings import router as savings_router
from app.api.v1.routes.cancellations import router as cancellations_router
from app.api.v1.routes.dashboard import router as dashboard_router
from app.api.v1.routes.graph import router as graph_router
from app.api.v1.routes.workspace import router as workspace_router
from app.api.v1.routes.assistant import router as assistant_router

api_v1_router = APIRouter()
api_v1_router.include_router(workspace_router, dependencies=[Depends(require_identity)])
api_v1_router.include_router(assistant_router, dependencies=[Depends(require_identity)])
api_v1_router.include_router(health_router)
api_v1_router.include_router(ready_router)
api_v1_router.include_router(auth_router)
api_v1_router.include_router(imports_router, dependencies=[Depends(require_identity)])
api_v1_router.include_router(demo_router)
api_v1_router.include_router(merchants_router, dependencies=[Depends(require_identity)])
api_v1_router.include_router(merchant_reviews_router, dependencies=[Depends(require_identity)])
api_v1_router.include_router(merchant_aliases_router, dependencies=[Depends(require_identity)])
api_v1_router.include_router(commitments_router, dependencies=[Depends(require_identity)])
api_v1_router.include_router(insights_router, dependencies=[Depends(require_identity)])
api_v1_router.include_router(financial_impact_router, dependencies=[Depends(require_identity)])
api_v1_router.include_router(savings_router, dependencies=[Depends(require_identity)])
api_v1_router.include_router(cancellations_router, dependencies=[Depends(require_identity)])
api_v1_router.include_router(dashboard_router, dependencies=[Depends(require_identity)])
api_v1_router.include_router(graph_router, dependencies=[Depends(require_identity)])

from app.api.v1.routes.mobile import router as mobile_router
api_v1_router.include_router(mobile_router, dependencies=[Depends(require_identity)])
