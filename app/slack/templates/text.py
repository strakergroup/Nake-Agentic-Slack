from ...config import straker_config


def whoami_text(username: str) -> str:
    return f'Your connected DeltaRay account is: <{straker_config.deltaray_domain}|{username}>'
