-- OPC UA 云端历史数据库初始化脚本
-- 在腾讯云 TDSQL-C MySQL 上执行此脚本

-- 节点历史数据表（单表设计，所有节点共用，用 node_id 区分）
-- 对比本地 SQLite 的按节点分表方案，MySQL 用单表 + 索引更高效
CREATE TABLE IF NOT EXISTS node_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    node_id VARCHAR(255) NOT NULL COMMENT 'OPC UA 节点 ID（完整格式，如 ns=1;s=FIT_05R201F01.PV）',
    timestamp DATETIME(3) NOT NULL COMMENT '数据时间戳（毫秒精度）',
    value DOUBLE COMMENT '过程值',
    quality VARCHAR(32) NOT NULL DEFAULT 'Good' COMMENT '数据质量（Good/Uncertain/Bad）',
    source_timestamp DATETIME(3) NULL COMMENT '服务器端源时间戳',
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '记录写入时间',
    
    -- 复合索引：按节点+时间范围查询（历史趋势最常用）
    INDEX idx_node_ts (node_id, timestamp),
    -- 索引：按时间范围清理过期数据
    INDEX idx_timestamp (timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='OPC UA 节点历史数据';

-- 节点实时快照表（存储各节点最新值，供快速查询）
CREATE TABLE IF NOT EXISTS node_latest (
    node_id VARCHAR(255) PRIMARY KEY COMMENT 'OPC UA 节点 ID',
    value DOUBLE COMMENT '当前值',
    quality VARCHAR(32) NOT NULL DEFAULT 'Good' COMMENT '数据质量',
    timestamp DATETIME(3) NOT NULL COMMENT '数据时间戳',
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '最后更新时间',
    
    INDEX idx_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='OPC UA 节点实时值快照';

-- 数据推送日志表（记录每次推送的状态，便于排查）
CREATE TABLE IF NOT EXISTS push_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    push_time DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    node_count INT NOT NULL DEFAULT 0 COMMENT '推送的节点数',
    record_count INT NOT NULL DEFAULT 0 COMMENT '推送的记录数',
    status VARCHAR(16) NOT NULL DEFAULT 'success' COMMENT 'success/failed',
    error_msg TEXT NULL COMMENT '错误信息',
    duration_ms INT NULL COMMENT '推送耗时（毫秒）'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='数据推送日志';

-- 定时清理过期数据的存储过程（保留 7 天）
DELIMITER //
CREATE PROCEDURE IF NOT EXISTS cleanup_expired_history(IN retention_days INT)
BEGIN
    DECLARE cutoff DATETIME(3);
    SET cutoff = DATE_SUB(NOW(3), INTERVAL retention_days DAY);
    
    -- 清理历史数据
    DELETE FROM node_history WHERE timestamp < cutoff;
    
    -- 清理推送日志（保留 30 天）
    DELETE FROM push_log WHERE push_time < DATE_SUB(NOW(3), INTERVAL 30 DAY);
END //
DELIMITER ;
