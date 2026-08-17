import asyncio, time
from app.services.local_db.database import get_db
from app.services.coding_sessions import get_coding_session_bridge_outbox

async def main():
    db = get_db(); await db.connect()
    ob = get_coding_session_bridge_outbox()
    total = 0; stalls = 0; started = time.time()
    while time.time() - started < 3200:
        res = await ob.sync_pending()
        total += res.get("sent", 0)
        if res.get("blocked"):
            print("BLOCKED:", res["blocked"], flush=True); break
        if res.get("failed") or res.get("sent", 0) == 0:
            # Either a transient upstream failure or a head row still in
            # backoff. Both mean: wait like the real publisher loop does.
            stalls += 1
            if stalls > 60:
                print("GIVING UP after sustained stall", flush=True); break
            await asyncio.sleep(15)
            continue
        stalls = 0
        if total % 500 < 100:
            print(f"progress sent={total}", flush=True)
    st = await ob.status()
    print(f"SENT_TOTAL={total} PENDING={st['pending']} QUARANTINED={st['quarantined']}", flush=True)
    await db.close()

asyncio.run(main())
