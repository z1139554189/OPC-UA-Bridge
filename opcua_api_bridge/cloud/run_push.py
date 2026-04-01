"""
独立运行的数据推送脚本
从本地桥接器 API 拉取数据，推送到云 MySQL

用法：
  python cloud/run_push.py              # 单次推送
  python cloud/run_push.py --loop       # 持续循环推送（每 60 秒）
  python cloud/run_push.py --interval 30  # 自定义推送间隔
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# 把项目根目录加到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from cloud.cloud_pusher import CloudPusher
from cloud.cloud_config import CLOUD_MYSQL, CLOUD_PUSH_CONFIG


BRIDGE_API = "http://localhost:8000"


async def fetch_cache_snapshot() -> dict:
    """从桥接器获取所有缓存数据"""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{BRIDGE_API}/api/v1/nodes/cache")
        resp.raise_for_status()
        return resp.json()


async def single_push(pusher: CloudPusher) -> bool:
    """单次推送：从桥接器缓存拉取数据 → 推送到云 MySQL"""
    try:
        snapshot = await fetch_cache_snapshot()
        cache = snapshot.get("cache", {})
        
        if not cache:
            print("[INFO] 桥接器缓存为空，无数据推送")
            return True

        now_iso = datetime.now().isoformat()
        count = 0
        for node_id, entry in cache.items():
            val = entry.get("value")
            if val is None:
                continue
            quality = entry.get("quality", "Good")
            timestamp = entry.get("timestamp", now_iso)
            pusher.enqueue(
                node_id=node_id,
                value=float(val) if not isinstance(val, (int, float)) else val,
                quality=quality,
                timestamp=timestamp,
            )
            count += 1

        if count > 0:
            success = await pusher.push()
            if success:
                print(f"[OK] 推送成功：{count} 条记录，{len(set(item[0] for item in pusher._buffer))} 个节点")
            else:
                print(f"[FAIL] 推送失败")
                return False
        else:
            print("[INFO] 缓存中没有有效数值")

        return True

    except httpx.ConnectError:
        print("[ERROR] 无法连接桥接器，请确认桥接器正在运行 (http://localhost:8000)")
        return False
    except Exception as e:
        print(f"[ERROR] 推送异常: {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(description="OPC UA 数据推送脚本")
    parser.add_argument("--loop", action="store_true", help="持续循环推送")
    parser.add_argument("--interval", type=int, default=60, help="推送间隔（秒），默认 60")
    args = parser.parse_args()

    print("=" * 50)
    print("OPC UA 云端数据推送")
    print(f"目标：{CLOUD_MYSQL['host']}:{CLOUD_MYSQL['port']}/{CLOUD_MYSQL['database']}")
    print("=" * 50)

    pusher = CloudPusher(
        host=CLOUD_MYSQL["host"],
        port=CLOUD_MYSQL["port"],
        database=CLOUD_MYSQL["database"],
        user=CLOUD_MYSQL["user"],
        password=CLOUD_MYSQL["password"],
        push_interval=args.interval,
        **{k: v for k, v in CLOUD_PUSH_CONFIG.items() if k != "push_interval"},
    )

    connected = await pusher.connect()
    if not connected:
        print("[FATAL] 无法连接云 MySQL，请检查网络和配置")
        sys.exit(1)

    try:
        if args.loop:
            print(f"[循环模式] 每 {args.interval} 秒推送一次，Ctrl+C 退出")
            while True:
                await single_push(pusher)
                await asyncio.sleep(args.interval)
        else:
            await single_push(pusher)
    except KeyboardInterrupt:
        print("\n[INFO] 用户中断，退出")
    finally:
        await pusher.close()


if __name__ == "__main__":
    asyncio.run(main())
