from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .models import (
    CommandAckRequest,
    DeviceRegistration,
    DeviceStatus,
    EventLog,
    TaskCreateRequest,
    TaskRecord,
    TaskSettings,
    store,
)

app = FastAPI(title="Cloud Phone Task Server", version="0.4.0")
templates = Jinja2Templates(directory="server/templates")
OFFLINE_AFTER_SECONDS = 20


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


def _device_online_status(last_seen_at: datetime) -> str:
    if datetime.utcnow() - last_seen_at > timedelta(seconds=OFFLINE_AFTER_SECONDS):
        return DeviceStatus.offline.value
    return "online"


def _serialize_device(device: Any) -> Dict[str, Any]:
    return {
        "device_id": device.device_id,
        "name": device.name,
        "status": device.status.value if hasattr(device.status, "value") else str(device.status),
        "online_status": _device_online_status(device.last_seen_at),
        "current_task_id": device.current_task_id,
        "last_seen_at": device.last_seen_at.isoformat(),
    }


def _serialize_task(task: TaskRecord) -> Dict[str, Any]:
    return {
        "task_id": task.task_id,
        "status": task.status.value if hasattr(task.status, "value") else str(task.status),
        "device_ids": task.device_ids,
        "queries": task.queries,
        "settings": task.settings.model_dump(),
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "stop_requested": task.stop_requested,
    }


def _serialize_log(log: EventLog) -> Dict[str, Any]:
    return {
        "log_id": log.log_id,
        "level": log.level.value if hasattr(log.level, "value") else str(log.level),
        "message": log.message,
        "created_at": log.created_at.isoformat(),
        "task_id": log.task_id,
        "device_id": log.device_id,
    }


async def _dashboard_payload() -> Dict[str, Any]:
    devices = await store.list_devices()
    tasks = await store.list_tasks()
    logs = await store.list_logs()
    return {
        "devices": [_serialize_device(device) for device in devices],
        "tasks": [_serialize_task(task) for task in tasks],
        "logs": [_serialize_log(log) for log in logs],
        "default_settings": TaskSettings().model_dump(),
        "offline_after_seconds": OFFLINE_AFTER_SECONDS,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    payload = await _dashboard_payload()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        payload,
    )


@app.get("/dashboard/data")
async def dashboard_data():
    return await _dashboard_payload()


@app.post("/dashboard/tasks", response_class=HTMLResponse)
async def create_task_from_form(
    request: Request,
    device_ids: List[str] = Form(default=[]),
    queries: str = Form(...),
    max_followers_per_list: int = Form(50),
    max_outer_users: int = Form(50),
    max_inner_users: int = Form(50),
):
    payload = TaskCreateRequest(
        device_ids=device_ids,
        queries=[item.strip() for item in queries.splitlines() if item.strip()],
        settings=TaskSettings(
            max_followers_per_list=max_followers_per_list,
            max_outer_users=max_outer_users,
            max_inner_users=max_inner_users,
        ),
    )
    try:
        await store.create_task(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await dashboard(request)


@app.post("/dashboard/tasks/{task_id}/stop", response_class=HTMLResponse)
async def stop_task_from_form(request: Request, task_id: str):
    try:
        await store.stop_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    return await dashboard(request)


@app.post("/dashboard/tasks/{task_id}/delete", response_class=HTMLResponse)
async def delete_task_from_form(request: Request, task_id: str):
    try:
        await store.delete_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await dashboard(request)


@app.post("/dashboard/devices/{device_id}/delete", response_class=HTMLResponse)
async def delete_device_from_form(request: Request, device_id: str):
    try:
        await store.delete_device(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="device not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await dashboard(request)


@app.get("/protocol")
async def protocol_doc():
    return {
        "device_register": {
            "method": "POST",
            "path": "/devices/register",
            "body": {"device_id": "cloud-01", "name": "云手机1号"},
            "note": "APK 打开后自动注册，控制台会自动显示，无需手填 Device IDs。",
        },
        "next_command": {
            "method": "GET",
            "path": "/devices/{device_id}/next-command",
        },
        "ack_command": {
            "method": "POST",
            "path": "/devices/{device_id}/commands/{command_id}/ack",
            "body": {"success": True, "note": "done"},
        },
        "search_and_walk_payload": {
            "queries": ["elsieruth645280jfz"],
            "settings": TaskSettings().model_dump(),
        },
    }


@app.post("/devices/register")
async def register_device(payload: DeviceRegistration):
    return await store.register_device(payload)


@app.get("/devices")
async def list_devices():
    devices = await store.list_devices()
    return [_serialize_device(device) for device in devices]


@app.delete("/devices/{device_id}")
async def delete_device(device_id: str):
    try:
        return await store.delete_device(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="device not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/devices/{device_id}/heartbeat")
async def heartbeat(device_id: str):
    try:
        return await store.heartbeat(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="device not found") from exc


@app.get("/devices/{device_id}/next-command")
async def next_command(device_id: str):
    try:
        return await store.next_command(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="device not found") from exc


@app.post("/devices/{device_id}/commands/{command_id}/ack")
async def ack_command(device_id: str, command_id: str, payload: CommandAckRequest):
    try:
        return await store.ack_command(device_id, command_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="command or device not found") from exc


@app.post("/tasks")
async def create_task(payload: TaskCreateRequest):
    try:
        return await store.create_task(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/tasks")
async def list_tasks():
    tasks = await store.list_tasks()
    return [_serialize_task(task) for task in tasks]


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    try:
        return _serialize_task(await store.get_task(task_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc


@app.post("/tasks/{task_id}/stop")
async def stop_task(task_id: str):
    try:
        return await store.stop_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc


@app.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    try:
        return await store.delete_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="task not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
