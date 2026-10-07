"""Query-layer ownership filters and aggregations.

Every ownership decision for notes and messages lives here so that all
routes (single-record read/update/delete and the list pages) go through
the same entry points:

* notes are visible to their author only;
* messages are visible to their sender or their recipient;
* a message with a missing or null recipient is treated as private
  (visible to its sender only);
* legacy documents missing flag fields (``deleted``,
  ``hidden_for_sender``) or storing explicit nulls are treated the same
  way as current documents (missing flags count as false/zero).
"""

# Matches documents that are not deleted, including legacy documents
# where the field is missing or explicitly null.
NOT_DELETED = {"deleted": {"$ne": True}}

# Matches documents not hidden for their sender (legacy-safe).
NOT_HIDDEN_FOR_SENDER = {"hidden_for_sender": {"$ne": True}}


def note_owner_filter(user_id):
    # Notes are private to their author.
    return {"user_id": user_id}


def message_access_filter(user_id):
    # A missing or null to_id never matches, so such messages stay
    # private to their sender.
    return {"$or": [{"from_id": user_id}, {"to_id": user_id}]}


def find_owned_note(db, note_id, user_id):
    # Single entry point for single-note read/update/delete.
    if not note_id:
        return None
    query = {"id": note_id}
    query.update(note_owner_filter(user_id))
    return db.notes.find_one(query)


def find_accessible_message(db, message_id, user_id):
    # Single entry point for single-message read/update/delete.
    if not message_id:
        return None
    query = {"id": message_id}
    query.update(message_access_filter(user_id))
    return db.messages.find_one(query)


def find_user(db, user_id):
    if not user_id:
        return None
    return db.users.find_one({"id": user_id})


def other_users(db, user_id):
    return db.users.find({"id": {"$ne": user_id}}, {"_id": 0})


def _items_and_total(items, total):
    # A missing count field counts as zero.
    items = items or []
    total = total or []
    count = total[0].get("count", 0) if total else 0
    return items, count


def notes_page(db, user_id):
    # Return (notes, total) for a user's profile from ONE aggregation so
    # the list and the count can never disagree.
    pipeline = [
        {"$match": dict(note_owner_filter(user_id), **NOT_DELETED)},
        {"$sort": {"timestamp": -1}},
        {"$facet": {
            "items": [],
            "total": [{"$count": "count"}],
        }},
    ]
    result = list(db.notes.aggregate(pipeline))
    faceted = result[0] if result else {}
    return _items_and_total(faceted.get("items"), faceted.get("total"))


def messages_page(db, user_id):
    # Return (inbox, inbox_total, sent, sent_total) from ONE aggregation.
    # The page and the sent box reuse this single result, and records the
    # user may not see are filtered out before either side is produced.
    inbox_match = {"$match": {"to_id": user_id}}
    sent_match = {"$match": dict({"from_id": user_id}, **NOT_HIDDEN_FOR_SENDER)}
    pipeline = [
        {"$match": dict(message_access_filter(user_id), **NOT_DELETED)},
        {"$sort": {"timestamp": -1}},
        {"$facet": {
            "inbox_items": [inbox_match],
            "inbox_total": [inbox_match, {"$count": "count"}],
            "sent_items": [sent_match],
            "sent_total": [sent_match, {"$count": "count"}],
        }},
    ]
    result = list(db.messages.aggregate(pipeline))
    faceted = result[0] if result else {}
    inbox, inbox_total = _items_and_total(
        faceted.get("inbox_items"), faceted.get("inbox_total"))
    sent, sent_total = _items_and_total(
        faceted.get("sent_items"), faceted.get("sent_total"))
    return inbox, inbox_total, sent, sent_total
