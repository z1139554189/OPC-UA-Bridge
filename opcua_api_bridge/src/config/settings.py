"""
应用配置管理
Author: WorkBuddy SRE
Version: 1.0.0
"""

import os
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """应用配置"""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )
    
    # 应用配置
    APP_NAME: str = "opcua-api-bridge"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    
    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 4
    
    # OPC UA 配置
    OPCUA_ENDPOINT: str = Field(default="opc.tcp://localhost:4840", description="OPC UA 服务器端点")
    OPCUA_USERNAME: str | None = None
    OPCUA_PASSWORD: str | None = None
    OPCUA_SECURITY_POLICY: str = "Basic256Sha256"
    OPCUA_SECURITY_MODE: str = "SignAndEncrypt"
    OPCUA_RECONNECT_DELAY: int = 5  # 秒
    OPCUA_MAX_RECONNECT_ATTEMPTS: int = 3
    
    # 安全配置
    SECRET_KEY: str = Field(
        default="your-secret-key-here-change-in-production",
        description="JWT 密钥"
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # CORS 配置
    ALLOWED_ORIGINS: List[str] = ["http://localhost:8080", "http://127.0.0.1:8080"]
    
    # 数据库配置
    DATABASE_URL: str = "sqlite:///./opcua_bridge.db"
    
    # Redis 配置
    REDIS_URL: str = Field(default="redis://localhost:6379", description="Redis 连接URL")
    REDIS_CACHE_TTL: int = 300  # 秒
    
    # 监控配置
    PROMETHEUS_ENABLED: bool = True
    LOG_LEVEL: str = "INFO"
    
    # API 配置
    API_V1_PREFIX: str = "/api/v1"
    DEFAULT_PAGE_SIZE: int = 100
    MAX_PAGE_SIZE: int = 1000
    
    # 性能配置
    REQUEST_TIMEOUT: int = 30  # 秒
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = 60
    
    # 历史数据配置
    HISTORY_MAX_POINTS: int = 10000
    HISTORY_CHUNK_SIZE: int = 1000
    
    # Grafana 配置（docker-compose 用）
    GRAFANA_PASSWORD: str = "admin"
    
    @property
    def is_production(self) -> bool:
        """是否生产环境"""
        return self.ENVIRONMENT.lower() == "production"
    
    @property
    def is_development(self) -> bool:
        """是否开发环境"""
        return self.ENVIRONMENT.lower() == "development"
    
    @property
    def is_testing(self) -> bool:
        """是否测试环境"""
        return self.ENVIRONMENT.lower() == "test"
    
    def get_redis_connection_kwargs(self):
        """获取 Redis 连接参数"""
        from urllib.parse import urlparse
        
        if not self.REDIS_URL:
            return {}
        
        parsed = urlparse(self.REDIS_URL)
        
        kwargs = {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 6379,
            "db": int(parsed.path.strip('/') or 0),
            "decode_responses": True
        }
        
        if parsed.password:
            kwargs["password"] = parsed.password
            
        if self.is_production:
            kwargs["socket_keepalive"] = True
            kwargs["retry_on_timeout"] = True
            
        return kwargs
    
    def get_database_connection_kwargs(self):
        """获取数据库连接参数"""
        kwargs = {}
        
        if self.is_production and "sqlite" not in self.DATABASE_URL.lower():
            kwargs.update({
                "pool_size": 20,
                "max_overflow": 30,
                "pool_recycle": 3600,
                "pool_pre_ping": True
            })
            
        return kwargs
    
    def get_opcua_security_kwargs(self):
        """获取 OPC UA 安全参数"""
        kwargs = {}
        
        if self.OPCUA_USERNAME and self.OPCUA_PASSWORD:
            kwargs.update({
                "username": self.OPCUA_USERNAME,
                "password": self.OPCUA_PASSWORD
            })
            
        if self.OPCUA_SECURITY_POLICY and self.OPCUA_SECURITY_MODE:
            kwargs.update({
                "security_policy": self.OPCUA_SECURITY_POLICY,
                "security_mode": self.OPCUA_SECURITY_MODE
            })
            
        return kwargs

# 全局配置实例
settings = Settings()

# 环境变量映射（用于文档）
ENV_VAR_MAPPING = {
    "OPCUA_ENDPOINT": {
        "description": "OPC UA 服务器端点 URL",
        "default": "opc.tcp://localhost:4840",
        "example": "opc.tcp://192.168.1.100:4840"
    },
    "OPCUA_USERNAME": {
        "description": "OPC UA 认证用户名",
        "default": None,
        "required": False
    },
    "OPCUA_PASSWORD": {
        "description": "OPC UA 认证密码",
        "default": None,
        "required": False,
        "secret": True
    },
    "SECRET_KEY": {
        "description": "JWT 密钥",
        "required": True if not settings.is_development else False
    },
    "ENVIRONMENT": {
        "description": "运行环境",
        "options": ["development", "staging", "production"],
        "default": "development"
    }
}

def print_config_summary():
    """打印配置摘要（安全地）"""
    summary = {
        "app": {
            "name": settings.APP_NAME,
            "environment": settings.ENVIRONMENT,
            "debug": settings.DEBUG
        },
        "server": {
            "host": settings.HOST,
            "port": settings.PORT
        },
        "opcua": {
            "endpoint": settings.OPCUA_ENDPOINT,
            "has_auth": bool(settings.OPCUA_USERNAME and settings.OPCUA_PASSWORD)
        },
        "security": {
            "cors_origins": len(settings.ALLOWED_ORIGINS)
        },
        "monitoring": {
            "prometheus_enabled": settings.PROMETHEUS_ENABLED,
            "log_level": settings.LOG_LEVEL
        }
    }
    
    return summary