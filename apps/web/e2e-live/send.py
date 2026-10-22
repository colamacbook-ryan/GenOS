import asyncio, json, os, sys, uuid
sys.path.insert(0, os.environ.get("GH_API_DIR", os.path.join(os.path.dirname(__file__), "../../api")))
from redis.asyncio import Redis
from gh.chassis.bus import EventBus, BRIDGE_INBOUND
async def main():
    r = Redis.from_url(os.environ["GH_REDIS_URL"]); bus = EventBus(r, 10000)
    for m in json.loads(sys.argv[1]):
        await bus.publish(BRIDGE_INBOUND, "message", {"channel": m.get("channel", "zalo"),
            "external_msg_id": uuid.uuid4().hex, "external_group_id": m.get("group"), "group_name": None,
            "sender_external_id": m["sender"], "sender_name": m["name"], "sender_phone": m.get("phone"),
            "body_text": m["text"], "kind": "text", "mentions_self": m.get("mention", False)},
            actor="bridge:zalo", org_id=os.environ["ORG"])
    await r.aclose()
asyncio.run(main())
