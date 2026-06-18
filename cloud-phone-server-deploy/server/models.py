from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class DeviceStatus(str, Enum):
    idle = "idle"
    busy = "busy"
    offline = "offline"


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    stopping = "stopping"
    completed = "completed"
    stopped = "stopped"
    deleted = "deleted"


class CommandType(str, Enum):
    search_and_walk = "search_and_walk"
    stop = "stop"
    noop = "noop"


class LogLevel(str, Enum):
    info = "info"
    success = "success"
    warning = "warning"
    error = "error"


class DeviceRegistration(BaseModel):
    device_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)


class DeviceRecord(BaseModel):
    device_id: str
    name: str
    status: DeviceStatus = DeviceStatus.idle
    current_task_id: Optional[str] = None
    last_seen_at: datetime = Field(default_factory=datetime.utcnow)


class TaskSettings(BaseModel):
    launch_app: bool = True
    app_package: str = Field("com.zhiliaoapp.musically")
    swipe_videos_before_search: int = Field(2, ge=0, le=20)
    search_entry: str = Field("top_right_search")
    search_from_input: bool = True
    profile_match_mode: str = Field("exact_result_text")
    open_followers_after_profile: bool = True
    tap_mode_in_followers: str = Field("left_of_red_follow")
    max_followers_per_list: int = Field(50, ge=1, le=200)
    max_outer_users: int = Field(50, ge=1, le=200)
    max_inner_users: int = Field(50, ge=1, le=200)
    return_to_search_after_query: bool = True


class TaskCreateRequest(BaseModel):
    device_ids: List[str]
    queries: List[str]
    settings: TaskSettings = Field(default_factory=TaskSettings)


class TaskCommand(BaseModel):
    command_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    device_id: str
    type: CommandType
    payload: Dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    claimed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class TaskRecord(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    status: TaskStatus = TaskStatus.pending
    device_ids: List[str]
    queries: List[str]
    settings: TaskSettings
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    stop_requested: bool = False
    command_ids: List[str] = Field(default_factory=list)


class CommandAckRequest(BaseModel):
    success: bool = True
    note: Optional[str] = None


class EventLog(BaseModel):
    log_id: str = Field(default_factory=lambda: str(uuid4()))
    level: LogLevel = LogLevel.info
    message: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    task_id: Optional[str] = None
    device_id: Optional[str] = None


class StateStore:
    def __init__(self) -> None:
        self.devices: Dict[str, DeviceRecord] = {}
        self.tasks: Dict[str, TaskRecord] = {}
        self.commands: Dict[str, TaskCommand] = {}
        self.device_queues: Dict[str, List[str]] = {}
        self.logs: List[EventLog] = []

    def _append_log(
        self,
        message: str,
        level: LogLevel = LogLevel.info,
        task_id: Optional[str] = None,
        device_id: Optional[str] = None,
    ) -> EventLog:
        log = EventLog(message=message, level=level, task_id=task_id, device_id=device_id)
        self.logs.append(log)
        self.logs = self.logs[-300:]
        return log

    def _release_task_devices(self, task: TaskRecord) -> None:
        for device_id in task.device_ids:
            device = self.devices.get(device_id)
            if not device:
                continue
            if device.current_task_id == task.task_id:
                device.current_task_id = None
                device.status = DeviceStatus.idle

    async def list_logs(self, limit: int = 80) -> List[EventLog]:
        return list(reversed(self.logs[-limit:]))

    async def register_device(self, registration: DeviceRegistration) -> DeviceRecord:
        record = self.devices.get(registration.device_id)
        if record:
            old_name = record.name
            was_offline = record.status == DeviceStatus.offline
            record.name = registration.name
            record.last_seen_at = datetime.utcnow()
            if record.status == DeviceStatus.offline:
                record.status = DeviceStatus.idle
            if old_name != registration.name:
                self._append_log(
                    f"设备已更新名称：{old_name} -> {registration.name}",
                    LogLevel.info,
                    device_id=registration.device_id,
                )
            elif was_offline:
                self._append_log(
                    f"设备重新上线：{registration.name}",
                    LogLevel.success,
                    device_id=registration.device_id,
                )
        else:
            record = DeviceRecord(device_id=registration.device_id, name=registration.name)
            self.devices[registration.device_id] = record
            self.device_queues.setdefault(registration.device_id, [])
            self._append_log(
                f"新设备已注册：{registration.name}",
                LogLevel.success,
                device_id=registration.device_id,
            )
        return record

    async def delete_device(self, device_id: str) -> DeviceRecord:
        record = self.devices[device_id]
        if record.current_task_id:
            raise ValueError("device is busy")
        del self.devices[device_id]
        self.device_queues.pop(device_id, None)
        self._append_log(f"设备已删除：{record.name}", LogLevel.warning, device_id=device_id)
        return record

    async def heartbeat(self, device_id: str) -> DeviceRecord:
        record = self.devices[device_id]
        record.last_seen_at = datetime.utcnow()
        return record

    async def list_devices(self) -> List[DeviceRecord]:
        return list(self.devices.values())

    async def create_task(self, payload: TaskCreateRequest) -> TaskRecord:
        missing = [device_id for device_id in payload.device_ids if device_id not in self.devices]
        if missing:
            raise ValueError(f"Unknown devices: {', '.join(missing)}")
        if not payload.device_ids:
            raise ValueError("No devices selected")
        if not payload.queries:
            raise ValueError("No queries provided")

        task = TaskRecord(
            device_ids=payload.device_ids,
            queries=payload.queries,
            settings=payload.settings,
        )
        task.status = TaskStatus.running
        task.started_at = datetime.utcnow()
        self.tasks[task.task_id] = task
        self._append_log(
            f"任务已创建，设备 {len(payload.device_ids)} 台，文本 {len(payload.queries)} 条",
            LogLevel.success,
            task_id=task.task_id,
        )

        for device_id in payload.device_ids:
            device = self.devices[device_id]
            device.status = DeviceStatus.busy
            device.current_task_id = task.task_id
            command = TaskCommand(
                task_id=task.task_id,
                device_id=device_id,
                type=CommandType.search_and_walk,
                payload={
                    "queries": payload.queries,
                    "settings": payload.settings.model_dump(),
                },
            )
            self.commands[command.command_id] = command
            self.device_queues.setdefault(device_id, []).append(command.command_id)
            task.command_ids.append(command.command_id)
            self._append_log(
                f"任务已下发到设备：{device.name}",
                LogLevel.info,
                task_id=task.task_id,
                device_id=device_id,
            )

        return task

    async def list_tasks(self) -> List[TaskRecord]:
        return [task for task in self.tasks.values() if task.status != TaskStatus.deleted]

    async def get_task(self, task_id: str) -> TaskRecord:
        task = self.tasks[task_id]
        if task.status == TaskStatus.deleted:
            raise KeyError(task_id)
        return task

    async def stop_task(self, task_id: str) -> TaskRecord:
        task = self.tasks[task_id]
        if task.status in {TaskStatus.completed, TaskStatus.stopped, TaskStatus.deleted}:
            return task
        if task.status == TaskStatus.stopping:
            return task

        task.stop_requested = True
        task.status = TaskStatus.stopping
        self._append_log(f"任务收到停止请求", LogLevel.warning, task_id=task.task_id)

        for device_id in task.device_ids:
            command = TaskCommand(
                task_id=task.task_id,
                device_id=device_id,
                type=CommandType.stop,
                payload={},
            )
            self.commands[command.command_id] = command
            self.device_queues.setdefault(device_id, []).insert(0, command.command_id)
            task.command_ids.append(command.command_id)

        return task

    async def delete_task(self, task_id: str) -> TaskRecord:
        task = self.tasks[task_id]
        if task.status in {TaskStatus.running, TaskStatus.stopping}:
            raise ValueError("task is still active")
        task.status = TaskStatus.deleted
        self._append_log(f"任务已删除", LogLevel.warning, task_id=task.task_id)
        return task

    async def next_command(self, device_id: str) -> TaskCommand:
        queue = self.device_queues.setdefault(device_id, [])
        device = self.devices[device_id]
        device.last_seen_at = datetime.utcnow()
        if not queue:
            return TaskCommand(task_id="", device_id=device_id, type=CommandType.noop)

        command_id = queue.pop(0)
        command = self.commands[command_id]
        command.claimed_at = datetime.utcnow()
        return command

    async def ack_command(self, device_id: str, command_id: str, ack: CommandAckRequest) -> TaskCommand:
        command = self.commands[command_id]
        command.completed_at = datetime.utcnow()

        task = self.tasks.get(command.task_id)
        note_suffix = f"，备注：{ack.note}" if ack.note else ""
        if command.type != CommandType.noop:
            self._append_log(
                f"设备命令回执：{command.type.value} / {'成功' if ack.success else '失败'}{note_suffix}",
                LogLevel.success if ack.success else LogLevel.error,
                task_id=command.task_id or None,
                device_id=device_id,
            )

        if not task or task.status == TaskStatus.deleted:
            return command

        if command.type == CommandType.stop:
            task.status = TaskStatus.stopped
            task.finished_at = datetime.utcnow()
            self._release_task_devices(task)
            self._append_log("任务已停止", LogLevel.warning, task_id=task.task_id)
            return command

        if ack.success:
            search_commands = [
                cid for cid in task.command_ids
                if cid in self.commands and self.commands[cid].type == CommandType.search_and_walk
            ]
            if search_commands and all(self.commands[cid].completed_at for cid in search_commands):
                task.status = TaskStatus.completed
                task.finished_at = datetime.utcnow()
                self._release_task_devices(task)
                self._append_log("任务已完成", LogLevel.success, task_id=task.task_id)
        else:
            self._append_log("任务执行出现失败回执", LogLevel.error, task_id=task.task_id, device_id=device_id)

        return command


store = StateStore()
