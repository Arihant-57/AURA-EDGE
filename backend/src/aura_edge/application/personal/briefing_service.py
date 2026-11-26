"""Phase 4 Daily Briefing service aggregating verified local PostgreSQL state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from aura_edge.application.notification_service import NotificationService
from aura_edge.application.personal.calendar_service import CalendarService
from aura_edge.application.personal.grocery_service import GroceryService
from aura_edge.application.personal.parcel_service import ParcelService
from aura_edge.application.personal.task_service import TaskService
from aura_edge.application.scheduler_service import SchedulerService
from aura_edge.intelligence.schemas import LanguageCode


class BriefingService:
    """Builds strictly grounded EN/HI/KN daily briefings from verified database queries."""

    def __init__(
        self,
        *,
        task_service: TaskService,
        calendar_service: CalendarService,
        scheduler_service: SchedulerService | None = None,
        notification_service: NotificationService | None = None,
        grocery_service: GroceryService | None = None,
        parcel_service: ParcelService | None = None,
        default_timezone: str = "Asia/Kolkata",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.task_service = task_service
        self.calendar_service = calendar_service
        self.scheduler_service = scheduler_service
        self.notification_service = notification_service
        self.grocery_service = grocery_service
        self.parcel_service = parcel_service
        self.default_timezone = default_timezone
        self._clock = clock or (lambda: datetime.now(UTC))

    def build_daily_briefing(
        self,
        *,
        language: LanguageCode = "en",
        timezone_name: str | None = None,
    ) -> dict[str, Any]:
        tz = ZoneInfo(timezone_name or self.default_timezone)
        now_utc = self._clock()
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=UTC)
        now_local = now_utc.astimezone(tz)

        events_today = self.calendar_service.list_events(range_kind="today")
        pending_tasks = self.task_service.list_tasks(status="pending", limit=50)

        reminders: list[dict[str, Any]] = []
        scheduled_appliances: list[dict[str, Any]] = []
        if self.scheduler_service is not None:
            reminders = self.scheduler_service.list_jobs(
                job_type="reminder", statuses=("scheduled", "due", "paused"), limit=20
            )
            scheduled_appliances = self.scheduler_service.list_upcoming_smart_home_schedules()[:20]

        unread_notifications: list[dict[str, Any]] = []
        if self.notification_service is not None:
            unread_notifications = self.notification_service.list_notifications(
                unread_only=True, limit=20
            )

        low_stock: list[dict[str, Any]] = []
        if self.grocery_service is not None:
            low_stock = self.grocery_service.list_missing_or_low_stock()

        parcels_due: list[dict[str, Any]] = []
        if self.parcel_service is not None:
            today_d = now_local.date()
            for p in self.parcel_service.list_parcels(limit=20):
                exp = p.get("expected_delivery_date")
                if exp and exp >= today_d.isoformat():
                    parcels_due.append(p)

        date_str = now_local.strftime("%A, %B %d, %Y")
        summary = self._format_summary(
            language=language,
            date_str=date_str,
            events=events_today,
            tasks=pending_tasks,
            reminders=reminders,
            appliances=scheduled_appliances,
            unread=unread_notifications,
            low_stock=low_stock,
        )

        return {
            "generated_at_utc": now_utc.isoformat(),
            "timezone": tz.key,
            "local_date": now_local.date().isoformat(),
            "local_date_label": date_str,
            "events_today": events_today,
            "pending_tasks": pending_tasks,
            "upcoming_reminders": reminders,
            "scheduled_device_actions": scheduled_appliances,
            "unread_notifications": unread_notifications,
            "low_stock_items": low_stock,
            "parcels_due": parcels_due,
            "counts": {
                "events_today": len(events_today),
                "pending_tasks": len(pending_tasks),
                "upcoming_reminders": len(reminders),
                "scheduled_device_actions": len(scheduled_appliances),
                "unread_notifications": len(unread_notifications),
                "low_stock_items": len(low_stock),
                "parcels_due": len(parcels_due),
            },
            "summary_text": summary,
        }

    @staticmethod
    def _format_summary(
        *,
        language: LanguageCode,
        date_str: str,
        events: list[dict[str, Any]],
        tasks: list[dict[str, Any]],
        reminders: list[dict[str, Any]],
        appliances: list[dict[str, Any]],
        unread: list[dict[str, Any]],
        low_stock: list[dict[str, Any]],
    ) -> str:
        ev_n = len(events)
        tk_n = len(tasks)
        rm_n = len(reminders)
        ap_n = len(appliances)
        un_n = len(unread)
        ls_n = len(low_stock)

        if language == "hi":
            parts = [f"आज की तारीख {date_str} है।"]
            parts.append(
                f"आपके पास आज {ev_n} कैलेंडर इवेंट, {tk_n} लंबित कार्य, और {rm_n} आगामी रिमाइंडर हैं।"
            )
            if events:
                parts.append(f"पहला इवेंट: {events[0]['title']}।")
            if ap_n:
                parts.append(f"{ap_n} स्मार्ट होम कार्य निर्धारित हैं।")
            if un_n:
                parts.append(f"{un_n} अपठित सूचनाएं हैं।")
            if ls_n:
                parts.append(f"{ls_n} किराने का सामान कम है।")
            return " ".join(parts)

        if language == "kn":
            parts = [f"ಇಂದಿನ ದಿನಾಂಕ {date_str}."]
            parts.append(
                f"ಇಂದು ನಿಮಗೆ {ev_n} ಕ್ಯಾಲೆಂಡರ್ ಕಾರ್ಯಕ್ರಮಗಳು, {tk_n} ಬಾಕಿ ಕೆಲಸಗಳು ಮತ್ತು {rm_n} ಮುಂಬರುವ ಜ್ಞಾಪನೆಗಳು ಇವೆ."
            )
            if events:
                parts.append(f"ಮೊದಲ ಕಾರ್ಯಕ್ರಮ: {events[0]['title']}.")
            if ap_n:
                parts.append(f"{ap_n} ಸ್ಮಾರ್ಟ್ ಹೋಮ್ ಕ್ರಿಯೆಗಳು ನಿಗದಿಯಾಗಿವೆ.")
            if un_n:
                parts.append(f"{un_n} ಓದದ ಅಧಿಸೂಚನೆಗಳಿವೆ.")
            if ls_n:
                parts.append(f"{ls_n} ದಿನಸಿ ವಸ್ತುಗಳು ಕಡಿಮೆಯಾಗಿವೆ.")
            return " ".join(parts)

        parts = [f"Today is {date_str}."]
        parts.append(
            f"You have {ev_n} calendar event(s) today, {tk_n} pending task(s), and {rm_n} upcoming reminder(s)."
        )
        if events:
            parts.append(f"Next event: {events[0]['title']}.")
        if tasks:
            parts.append(f"Top pending task: {tasks[0]['title']}.")
        if ap_n:
            parts.append(f"{ap_n} smart-home action(s) scheduled.")
        if un_n:
            parts.append(f"{un_n} unread notification(s).")
        if ls_n:
            parts.append(f"{ls_n} grocery item(s) low on stock.")
        return " ".join(parts)
