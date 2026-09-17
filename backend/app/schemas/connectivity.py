from app.models.enums import AnprDeploymentMode, ConnectivityMode
from app.schemas.common import ORMModel
from app.schemas.gateway import GatewayOut


class SiteConnectivityOut(ORMModel):
    site_id: str
    organization_id: str
    site_name: str
    connectivity_mode: ConnectivityMode
    anpr_deployment_mode: AnprDeploymentMode
    primary_gateway_id: str | None
    gateway: GatewayOut | None = None
    camera_count: int = 0
    nvr_count: int = 0
    edge_agent_count: int = 0


class SiteConnectivityUpdate(ORMModel):
    connectivity_mode: ConnectivityMode | None = None
    anpr_deployment_mode: AnprDeploymentMode | None = None
    primary_gateway_id: str | None = None
