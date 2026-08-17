import typer
from datetime import datetime, timedelta
from DB import get_connection, TOTAL_BUDGET, TOTAL_GPUS, get_available_gpus, get_budget_used, get_committed_budget, get_next_request_id, flag_overruns
from rich.console import Console
from rich.table import Table


app = typer.Typer()
console = Console() # For printing tables


@app.command()
def request(user: str = typer.Option(help="PI or researcher submitting the request"),
            gpus: int = typer.Option(help="Number of GPUs requested"),
            hours: float = typer.Option(help="Requested duration in hours")):
    """Submit a new GPU request. Rejected immediately if the requested GPU count isn't currently available."""

    if gpus <= 0 or hours <= 0:
        print("invalid request")
        return

    if gpus * hours > TOTAL_BUDGET:
        print(f"Request rejected - {gpus} GPUs x {hours}h exceeds total grant budget ({TOTAL_BUDGET} GPU-hours)")
        return

    conn = get_connection()
    available = get_available_gpus(conn)
    
    if gpus > available:
        print(f"Request rejected - requested {gpus} GPUs, only {available} available")
    else:
        req_id = get_next_request_id(conn)
        conn.execute(
            "INSERT INTO requests (id, user, gpus, requested_hours, status, requested_at) VALUES (?, ?, ?, ?, ?, ?)",
            (req_id, user, gpus, hours, 'pending', datetime.now().isoformat()))
        conn.commit()
        print(f'request_id: {req_id} | status: pending')
    return


@app.command()
def approve(request_id: str = typer.Argument(help="ID of the request to approve (e.g. req_001)")):
    """Approve a pending request, allocating GPUs and reserving budget for its requested duration."""
    conn = get_connection()
    res = conn.execute("SELECT * FROM requests WHERE id = ?", (request_id, )).fetchone()

    if not res: 
        print(f'{request_id} not found!')
        return

    if res['status'] != 'pending':
        print('Cannot approve a non-pending request!')
        return

    # Calculating available gpus
    available_gpus = get_available_gpus(conn)

    # Committed budget (reserved worst-case cost of active jobs + actual charge of ended ones)
    committed_budget = get_committed_budget(conn)
    available_budget = TOTAL_BUDGET - committed_budget

    if res['gpus'] > available_gpus:
        print(f"Cannot allocate request {request_id} yet. Requested gpus - {res['gpus']}, available gpus - {available_gpus}")
        return
    
    if res['gpus'] * res['requested_hours'] > available_budget:
        print(f'Rejecting request {request_id} as it exceeds current available budget limit {available_budget}')
        return

    # approving request
    approved_at = datetime.now()
    expires_at = approved_at +  timedelta(hours=res['requested_hours'])

    conn.execute("UPDATE requests " \
    "SET approved_at = ?, expires_at = ?, " \
    "status = ? " \
    "WHERE id = ?",
    (approved_at.isoformat(), expires_at.isoformat() , 'allocated', request_id))

    new_remaining = available_budget - (res['gpus']*res['requested_hours'])
    conn.commit()

    print(f'{request_id}: allocated | session expires at {expires_at.isoformat()} | budget remaining: {new_remaining:.1f} GPU-hours')
    return

@app.command()
def status():
    """Show active requests, the pending queue, budget usage, and recent request history."""
    conn = get_connection()

    # change status of overrun jobs still running after expiry time
    flag_overruns(conn)

    active_requests = conn.execute("SELECT * FROM requests WHERE status IN ('allocated', 'overrun')").fetchall()
    queue = conn.execute("SELECT * FROM requests WHERE status = 'pending'").fetchall()
    budget_used = get_budget_used(conn)
    remaining = TOTAL_BUDGET - budget_used
    remaining_percent = remaining /TOTAL_BUDGET * 100

    # Printing stats in a table
    active_table = Table(title="Active Requests")
    active_table.add_column("ID")
    active_table.add_column("User")
    active_table.add_column("GPUs", justify="right")
    active_table.add_column("Status")
    active_table.add_column("Expires At")
    for row in active_requests:
        active_table.add_row(row["id"], row["user"], str(row["gpus"]), row["status"], row["expires_at"] or "-")

    # Queued requests
    queue_table = Table(title="Queue (pending)")
    queue_table.add_column("ID")
    queue_table.add_column("User")
    queue_table.add_column("GPUs", justify="right")
    queue_table.add_column("Hours Requested", justify="right")
    for row in queue:
        queue_table.add_row(row["id"], row["user"], str(row["gpus"]), str(row["requested_hours"]))

    # history of requests
    history_table = Table(title="Request History")
    history_table.add_column("ID")
    history_table.add_column("User")
    history_table.add_column("GPUs", justify="right")
    history_table.add_column("Status")
    history_table.add_column("Charged Hours", justify="right")
    history_table.add_column("Ended At")

    history_rows = conn.execute(
        "SELECT * FROM requests WHERE status IN ('ended', 'cancelled') "
        "ORDER BY COALESCE(ended_at, requested_at) DESC LIMIT 10"
    ).fetchall()

    for row in history_rows:
        charged = f"{row['charged_hours']:.1f}" if row['charged_hours'] is not None else "-"
        history_table.add_row(
            row["id"], row["user"], str(row["gpus"]), row["status"], charged, row["ended_at"] or "-"
        )

    # summary of metrics and GPU hours
    available_gpus = get_available_gpus(conn)
    used_gpus = TOTAL_GPUS - available_gpus

    summary_table = Table(title="Summary")
    summary_table.add_column("Metric")
    summary_table.add_column("Value", justify="right")
    consumed_percent = budget_used / TOTAL_BUDGET * 100

    summary_table.add_row("GPUs in use", f"{used_gpus} / {TOTAL_GPUS}")
    summary_table.add_row("GPU-hours used", f"{budget_used:.1f} / {TOTAL_BUDGET} ({consumed_percent:.1f}%)")

    remaining_str = f"{remaining:.1f} GPU-hours ({remaining_percent:.1f}%)"
    if remaining_percent < 20:
        remaining_str = f"[bold red]{remaining_str}[/bold red]"
    summary_table.add_row("Budget remaining", remaining_str)

    console.print(active_table)
    console.print(queue_table)
    console.print(summary_table)
    console.print(history_table)

    
@app.command()
def end(request_id: str = typer.Argument(help="ID of the request to end (e.g. req_001)")):
    """End a running request and charge actual GPU-hours used. Ending a still-pending request cancels it instead."""
    conn = get_connection()
    res = conn.execute("SELECT * FROM requests WHERE id = ?", (request_id, )).fetchone()

    if not res: 
        print(f'{request_id} not found!')
        return

    if res['status'] == 'pending':
        conn.execute("UPDATE requests "
        "SET status = 'cancelled' "
        "WHERE id = ?",
        (request_id, ))
        conn.commit()
        print(f'Request {request_id} was pending. Now stands cancelled')
        return

    if res['status'] in ('ended', 'cancelled'):
        status = res['status']
        print(f'Request {request_id} already {status}')
        return

    approved_at = datetime.fromisoformat(res['approved_at'])

    elapsed_time = datetime.now() - approved_at
    total_sec = elapsed_time.total_seconds()
    elapsed_hours = total_sec / 3600
    h = int(total_sec // 3600)
    m = int((total_sec % 3600) // 60)

    # calculate charges
    charged_hours = res['gpus']*elapsed_hours

    conn.execute("UPDATE requests "
    "SET status = ?, ended_at = ?, charged_hours = ? "
    "WHERE id = ?",
    ('ended', datetime.now().isoformat(), charged_hours, request_id))
    conn.commit()

    print(f'{request_id}: session ended | actual usage: {h}h {m}m | charged: {charged_hours:.1f} GPU-hours')

    return


if __name__ == '__main__':
    app()