"""HAMCP-171 offline before/after harness (synthetic store; not production).

Usage: python measure.py <tree> <plans> <applied> <out.json>
Run with cwd=<tree>, PYTHONPATH=<tree>:<tree>/tests.
"""
import asyncio, copy, inspect, json, os, random, statistics, sys, time
tree, n_plans, n_applied, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
sys.path.insert(0, tree); sys.path.insert(0, os.path.join(tree, "tests"))
sys.path.insert(0, os.path.join(tree, "hass_mcp_engineering_beta"))
import logging; logging.disable(logging.CRITICAL)
from tests import test_f3_runtime_integration as f3
from tests.test_dev14_configuration_plans import PROPOSED_AUTOMATION

WORDS = ("kitchen hallway bedroom office garage porch attic basement lounge study "
         "motion presence sunset sunrise humidity temperature brightness scene "
         "notify delay wait template condition trigger window door lock fan").split()

def proposed(seed):
    rng = random.Random(seed)
    cfg = copy.deepcopy(PROPOSED_AUTOMATION)
    cfg["alias"] = f"Synthetic {seed} " + " ".join(rng.choices(WORDS, k=4))
    cfg["description"] = " ".join(rng.choices(WORDS, k=40))
    cfg["trigger"] = [
        {"platform": "state", "entity_id": f"binary_sensor.{rng.choice(WORDS)}_{rng.randrange(99)}",
         "to": rng.choice(("on", "off")), "for": {"seconds": rng.randrange(600)}}
        for _ in range(6)]
    cfg["condition"] = [
        {"condition": "numeric_state", "entity_id": f"sensor.{rng.choice(WORDS)}_{rng.randrange(99)}",
         "above": rng.randrange(40)} for _ in range(4)]
    cfg["action"] = [
        {"service": f"light.turn_{rng.choice(('on','off'))}",
         "alias": f"step {i} " + " ".join(rng.choices(WORDS, k=3)),
         "target": {"entity_id": [f"light.{rng.choice(WORDS)}_{rng.randrange(99)}" for _ in range(3)]},
         "data": {"brightness_pct": rng.randrange(100), "transition": rng.randrange(10),
                  "color_name": rng.choice(WORDS)}}
        for i in range(ACTIONS)]
    return cfg

ACTIONS = int(os.environ.get("ACTIONS", "18"))

async def echo_probe(stop):
    async def handle(reader, writer):
        try:
            while True:
                data = await reader.readexactly(1)
                writer.write(data); await writer.drain()
        except Exception:
            writer.close()
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    rtts = []
    while not stop.is_set():
        t = time.perf_counter(); writer.write(b"x"); await writer.drain()
        await reader.readexactly(1); rtts.append(time.perf_counter() - t)
    writer.close(); server.close()
    return rtts

async def ticker(stop):
    gaps, last = [], time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(0.001)
        now = time.perf_counter(); gaps.append(now - last); last = now
    return gaps

def pct(values, q):
    values = sorted(values); return values[min(len(values) - 1, int(q * len(values)))] if values else None

async def probed(coro_fn):
    stop = asyncio.Event()
    echo = asyncio.create_task(echo_probe(stop)); tick = asyncio.create_task(ticker(stop))
    await asyncio.sleep(0.05)
    t = time.perf_counter(); result = await coro_fn(); wall = time.perf_counter() - t
    stop.set(); rtts = await echo; gaps = await tick
    return result, wall, {
        "round_trips": len(rtts), "rtt_median_ms": round(statistics.median(rtts) * 1e3, 3) if rtts else None,
        "rtt_p99_ms": round(pct(rtts, .99) * 1e3, 3) if rtts else None,
        "rtt_max_ms": round(max(rtts) * 1e3, 3) if rtts else None,
        "max_loop_gap_ms": round(max(gaps) * 1e3, 3) if gaps else None,
        "ticks": len(gaps)}

async def main():
    fx = f3.F3ConfigurationActivationTests(); await fx.asyncSetUp()
    report = {"tree": tree, "plans": n_plans, "applied": n_applied, "actions_per_plan": ACTIONS}
    try:
        t = time.perf_counter()
        for i in range(n_plans):
            created = await fx.service.create_configuration_plan(
                title=f"Synthetic plan {i}", description="Synthetic offline measurement plan",
                operations=[{"operation_id": "update_comfort_automation", "resource_type": "automation",
                             "action": "update", "target_id": "apply_hvac_comfort", "depends_on": [],
                             "proposed_config": proposed(i)}])
            if i < n_applied:
                await fx.approve(created)
                applied = await fx.service.apply(created["plan_id"], created["plan_hash"])
                assert applied["task_state"] == "succeeded_verified", applied["task_state"]
        report["build_seconds"] = round(time.perf_counter() - t, 1)
        plan_dir = fx.root / "plans"
        sizes = [p.stat().st_size for p in plan_dir.glob("*.json")]
        report["plan_files"] = len(sizes); report["plan_bytes_mean"] = int(statistics.mean(sizes)) if sizes else 0
        report["plan_bytes_total"] = sum(sizes)
        idle_stop = asyncio.Event()
        async def idle():
            await asyncio.sleep(2.0)
        _, _, report["idle_probe"] = await probed(idle)
        supports_deep = "deep_storage_audit" in inspect.signature(fx.service.async_health_summary).parameters
        sequence = [False, False, False] + ([True, False] if supports_deep else [])
        calls = []
        for deep in sequence:
            kwargs = {"deep_storage_audit": True} if deep else {}
            result, wall, probe = await probed(lambda: fx.service.async_health_summary(**kwargs))
            ps = result["plan_store_scaling"]
            phases = ps["hot_paths"]["governance_health"]["phase_elapsed_ms"]
            calls.append({"deep": deep, "wall_s": round(wall, 3),
                          "validation_wait_ms": round(phases.get("validation_wait_ms", 0), 1),
                          "worker_elapsed_ms": round(phases.get("worker_elapsed_ms", 0), 1),
                          "phases_ms": {k: round(v, 1) for k, v in phases.items()},
                          "health_cache_hit_count": ps.get("health_cache_hit_count"),
                          "safety_verdict_cache": (ps.get("safety_verdict_cache") or {}).get("last_run"),
                          "probe": probe})
        report["health_calls"] = calls
        repo = fx.service.task_repository; original = repo.get; count = [0]
        def counting(*a, **k):
            count[0] += 1; return original(*a, **k)
        repo.get = counting
        passes = []
        for _ in range(5):
            count[0] = 0
            result, wall, probe = await probed(lambda: fx.runtime.recover_once("periodic"))
            passes.append({"task_repository_get": count[0], "wall_s": round(wall, 3),
                           "max_loop_gap_ms": probe["max_loop_gap_ms"]})
        repo.get = original
        report["sweep_passes"] = passes
    finally:
        await fx.asyncTearDown()
    json.dump(report, open(out, "w"), indent=1)
    print(json.dumps(report, indent=1))
asyncio.run(main())
