import uuid

from app.auth.connector import RayClient
from app.config import domains
from app.ray.service import RayService


class TestRayService:
    def test_get_service(self):
        client_id = str(uuid.uuid4())
        api_token = str(uuid.uuid4())
        ray_service = RayService.get_service(client_id, api_token)
        assert ray_service.ray_client_id == client_id
        assert ray_service.token == api_token
        assert ray_service._ray.base_url == domains.stingray

    def test_get_service_ray_client(self, ray_client: RayClient):
        ray_service = RayService.get_service(ray_client)
        assert ray_service.ray_client_id == ray_client.id
        assert ray_service.token == ray_client.access_token
        assert ray_service._ray.base_url == domains.stingray
