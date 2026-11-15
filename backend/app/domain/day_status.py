from enum import StrEnum


class DayStatus(StrEnum):
    NOT_LOGGED = "not_logged"
    PARTIAL = "partial"
    COMPLETE = "complete"


class DayEvent(StrEnum):
    ITEM_ADDED = "item_added"
    ITEM_EDITED = "item_edited"
    ITEM_MOVED_IN = "item_moved_in"
    ITEM_DELETED = "item_deleted"
    ITEM_MOVED_OUT = "item_moved_out"
    MARK_COMPLETE = "mark_complete"
    MARK_PARTIAL = "mark_partial"


def next_status(current: DayStatus, event: DayEvent) -> DayStatus:
    if event == DayEvent.MARK_COMPLETE:
        return DayStatus.COMPLETE
    if event == DayEvent.MARK_PARTIAL:
        return DayStatus.PARTIAL
    if current == DayStatus.NOT_LOGGED:
        return DayStatus.PARTIAL
    if event in (DayEvent.ITEM_DELETED, DayEvent.ITEM_MOVED_OUT):
        return DayStatus.PARTIAL
    return current
