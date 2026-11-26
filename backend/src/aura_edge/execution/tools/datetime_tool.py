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


_KN_SMALL_NUMBERS = (
    "ಸೊನ್ನೆ", "ಒಂದು", "ಎರಡು", "ಮೂರು", "ನಾಲ್ಕು", "ಐದು", "ಆರು", "ಏಳು", "ಎಂಟು", "ಒಂಬತ್ತು",
    "ಹತ್ತು", "ಹನ್ನೊಂದು", "ಹನ್ನೆರಡು", "ಹದಿಮೂರು", "ಹದಿನಾಲ್ಕು", "ಹದಿನೈದು",
    "ಹದಿನಾರು", "ಹದಿನೇಳು", "ಹದಿನೆಂಟು", "ಹತ್ತೊಂಬತ್ತು",
)
_KN_TENS = ("", "", "ಇಪ್ಪತ್ತು", "ಮೂವತ್ತು", "ನಲವತ್ತು", "ಐವತ್ತು", "ಅರವತ್ತು", "ಎಪ್ಪತ್ತು", "ಎಂಬತ್ತು", "ತೊಂಬತ್ತು")


def _kn_clock_number(value: int) -> str:
    if value < 20:
        return _KN_SMALL_NUMBERS[value]
    tens, units = divmod(value, 10)
    prefix = _KN_TENS[tens]
    if not units:
        return prefix
    suffixes = ("", "ೊಂದು", "ೆರಡು", "ಮೂರು", "ನಾಲ್ಕು", "ೈದು", "ಾರು", "ೇಳು", "ೆಂಟು", "ೊಂಬತ್ತು")
    return prefix[:-1] + suffixes[units]


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
            msg = f"अभी {period} के {hour_12} बजकर {minute} मिनट हुए हैं।"
        elif lang == "kn":
            period = "ಬೆಳಿಗ್ಗೆ" if now.hour < 12 else ("ಮಧ್ಯಾಹ್ನ" if now.hour < 17 else "ಸಂಜೆ")
            hour_word = _kn_clock_number(int(hour_12))
            minute_word = _kn_clock_number(int(minute))
            msg = f"ಈಗ {period} {hour_word} ಗಂಟೆ."
            if int(minute):
                msg = f"ಈಗ {period} {hour_word} ಗಂಟೆ {minute_word} ನಿಮಿಷ."
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
            year = str(now.year)
            if 2000 <= now.year < 2100:
                year = "ಎರಡು ಸಾವಿರ"
                if now.year != 2000:
                    year = f"{year}ದ {_kn_clock_number(now.year - 2000)}"
            msg = f"ಇಂದು {_WEEKDAYS_KN[wd]}. {_MONTHS_KN[mo]} {_kn_clock_number(now.day)}, {year}."
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
