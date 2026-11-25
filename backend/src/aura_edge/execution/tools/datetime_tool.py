"""Local system clock and calendar date tool (zero network dependencies)."""

from __future__ import annotations

from datetime import datetime

from aura_edge.intelligence.schemas import LanguageCode, VerifiedActionResult

_WEEKDAYS_HI = [
    "सोमवार",
    "मंगलवार",
    "बुधवार",
    "गुरुवार",
    "शुक्रवार",
    "शनिवार",
    "रविवार",
]
_MONTHS_HI = [
    "जनवरी",
    "फरवरी",
    "मार्च",
    "अप्रैल",
    "मई",
    "जून",
    "जुलाई",
    "अगस्त",
    "सितंबर",
    "अक्टूबर",
    "नवंबर",
    "दिसंबर",
]

_WEEKDAYS_KN = [
    "ಸೋಮವಾರ",
    "ಮಂಗಳವಾರ",
    "ಬುಧವಾರ",
    "ಗುರುವಾರ",
    "ಶುಕ್ರವಾರ",
    "ಶನಿವಾರ",
    "ರವಿವಾರ",
]
_MONTHS_KN = [
    "ಜನವರಿ",
    "ಫೆಬ್ರವರಿ",
    "ಮಾರ್ಚ್",
    "ಏಪ್ರಿಲ್",
    "ಮೇ",
    "ಜೂನ್",
    "ಜುಲೈ",
    "ಆಗಸ್ಟ್",
    "ಸೆಪ್ಟೆಂಬರ್",
    "ಅಕ್ಟೋಬರ್",
    "ನವೆಂಬರ್",
    "ಡಿಸೆಂಬರ್",
]


class DateTimeTool:
    """Reads actual host system clock & timezone."""

    def get_time(self, lang: LanguageCode = "en") -> VerifiedActionResult:
        now = datetime.now().astimezone()
        hour_12 = now.strftime("%I").lstrip("0") or "12"
        minute = now.strftime("%M")
        ampm = now.strftime("%p")
        tz_name = now.tzname() or "local"

        if lang == "hi":
            period = "सुबह" if now.hour < 12 else ("दोपहर" if now.hour < 17 else "शाम")
            msg = f"अभी {period} के {hour_12} बजकर {minute} मिनट हुए हैं ({tz_name})।"
        elif lang == "kn":
            period = "ಬೆಳಿಗ್ಗೆ" if now.hour < 12 else ("ಮಧ್ಯಾಹ್ನ" if now.hour < 17 else "ಸಂಜೆ")
            msg = f"ಈಗ {period} {hour_12} ಗಂಟೆ {minute} ನಿಮಿಷ ಆಗಿದೆ ({tz_name})."
        else:
            msg = f"The time is {hour_12}:{minute} {ampm} ({tz_name})."

        return VerifiedActionResult(
            success=True,
            action="GetTime",
            status="verified",
            data={
                "iso_time": now.isoformat(),
                "time_12h": f"{hour_12}:{minute} {ampm}",
                "time_24h": now.strftime("%H:%M:%S"),
                "timezone": tz_name,
            },
            message=msg,
        )

    def get_date(self, lang: LanguageCode = "en") -> VerifiedActionResult:
        now = datetime.now().astimezone()
        wd = now.weekday()
        mo = now.month - 1

        if lang == "hi":
            msg = f"आज { _WEEKDAYS_HI[wd] }, {now.day} {_MONTHS_HI[mo]} {now.year} है।"
        elif lang == "kn":
            msg = f"ಇಂದು {_WEEKDAYS_KN[wd]}, {_MONTHS_KN[mo]} {now.day}, {now.year}."
        else:
            msg = f"Today is {now.strftime('%A, %B %d, %Y')}."

        return VerifiedActionResult(
            success=True,
            action="GetDate",
            status="verified",
            data={
                "iso_date": now.date().isoformat(),
                "weekday": now.strftime("%A"),
                "timezone": now.tzname() or "local",
            },
            message=msg,
        )
