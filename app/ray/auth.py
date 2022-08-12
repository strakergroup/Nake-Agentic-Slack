from ray_sdk import RayV3
from ..config import config


def get_ray_service(access_token: str | None = None) -> RayV3:
    """Gets a RayV3 service for a given access token."""
    # TODO cache services?
    return RayV3(
        token=access_token,
        base_url=config.stingray_domain,
    )
