# Phase 2 Handover

Phase 2 provides a PostgreSQL-backed virtual home with living room, bedroom,
kitchen, study and utility room. Nineteen stable devices cover lights, fans, AC,
TV, curtains, refrigerator, washer, vacuum, purifier, water heater, water pump
and climate sensing.

REST/UI and multilingual voice mutations share `SmartHomeService.command`, which
validates availability, capability, ranges and enums, commits state plus audit
history, then publishes a verified WebSocket event. Ambiguity requires
clarification. Washer and vacuum expose activity state and persistent remaining
seconds. Morning, Night, Study and Away routines use the same dispatcher. Energy
is explicitly estimated, never presented as a physical measurement.

Known limits: complex compound/relative language is partial; schedules are in the
activities contract but not user-configurable; MQTT hardware is not included.
