from fastapi import FastAPI, Response
from DB import (
    get_connection, TOTAL_GPUS, TOTAL_BUDGET,
    get_available_gpus, get_budget_used, get_queue_depth, get_overrun_count,
)

app = FastAPI()

@app.get("/health")
def health():
    conn = None
    try:
        # Checking if it can actually open database and run real query on it
        conn = get_connection()
        conn.execute("SELECT 1")
        return {"status": "ok"}
    except Exception:
        return Response(content='{"status": "error"}', status_code=503, media_type="application/json") # 503 - this service can't currently handle requests
    finally:
        if conn is not None:
            conn.close()


@app.get("/metrics")
def metrics():
    conn = get_connection()

    hours_used = get_budget_used(conn)
    slots_active = TOTAL_GPUS - get_available_gpus(conn)
    remaining_percent = (TOTAL_BUDGET - hours_used) / TOTAL_BUDGET * 100
    queue_depth = get_queue_depth(conn)
    overrun_count = get_overrun_count(conn)

    conn.close()

    body = (
        "# HELP gpu_hours_used_total Total GPU-hours consumed since ledger start\n"
        "# TYPE gpu_hours_used_total counter\n"
        f"gpu_hours_used_total {hours_used:.2f}\n"
        "\n"
        "# HELP gpu_slots_active Currently allocated GPU slots\n"
        "# TYPE gpu_slots_active gauge\n"
        f"gpu_slots_active {slots_active}\n"
        "\n"
        "# HELP gpu_budget_remaining_percent Remaining budget as percentage of total\n"
        "# TYPE gpu_budget_remaining_percent gauge\n"
        f"gpu_budget_remaining_percent {remaining_percent:.2f}\n"
        "\n"
        "# HELP allocation_queue_depth Current number of requests in queue\n"
        "# TYPE allocation_queue_depth gauge\n"
        f"allocation_queue_depth {queue_depth}\n"
        "\n"
        "# HELP session_overrun_count Number of sessions currently past their scheduled end time\n"
        "# TYPE session_overrun_count gauge\n"
        f"session_overrun_count {overrun_count}\n"
    )

    return Response(content=body, media_type="text/plain")
