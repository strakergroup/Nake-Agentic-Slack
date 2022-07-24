import os
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class StrakerConfig:
    """App configuration settings related to Straker apps and environment."""

    environment: str
    deltaray_domain: str = field(init=False)

    def __post_init__(self):
        # Validate and format the Straker environment.
        object.__setattr__(self, 'environment', self.environment.lower())
        if self.environment not in ['live', 'uat', 'dev', 'local']:
            raise ValueError('The Straker environment must be one of: live, uat, dev, or local')
        # Set the domains for the environment.
        object.__setattr__(self, 'deltaray_domain', self.get_deltaray_domain(self.environment))

    @staticmethod
    def get_deltaray_domain(env: str) -> str:
        match env:
            case 'local' | 'dev' | 'uat':
                return f'https://{env}-deltaray.strakertranslations.com'
            case 'live':
                return 'https://deltaray.strakertranslations.com'
        return ''


straker_config = StrakerConfig(os.getenv('STRAKER_ENVIRONMENT', ''))
