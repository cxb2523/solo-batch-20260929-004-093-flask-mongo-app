from datetime import datetime
import uuid


# A document counts as not-deleted / not-hidden when the flag is missing,
# explicitly null (old documents) or explicitly False. Only an explicit
# True removes it.
FLAG_OFF = [
    {"deleted": {"$exists": False}},
    {"deleted": None},
    {"deleted": False},
]
HIDDEN_OFF = [
    {"hidden_for_sender": {"$exists": False}},
    {"hidden_for_sender": None},
    {"hidden_for_sender": False},
]


def is_party(document, user_id):
    # Author always has access. A named recipient has access too. When the
    # recipient field is missing or empty, the document stays private to
    # its author.
    if document.get("from_id") == user_id or document.get("user_id") == user_id:
        return True
    recipient_id = document.get("to_id")
    return bool(recipient_id) and recipient_id == user_id


# Message class
class Message():

    # Main initialiser
    def __init__(self, title, body, from_id, from_name, to_id, to_name, id="", deleted=False, hidden_for_sender=False):
        self.title = title
        self.body = body
        self.from_id = from_id
        self.from_name = from_name
        self.to_id = to_id
        self.to_name = to_name
        self.timestamp = datetime.utcnow()
        self.date_string = self.timestamp.strftime("%d %b %Y %H:%M")
        self.id = uuid.uuid4().hex if not id else id
        self.deleted = deleted
        self.hidden_for_sender = hidden_for_sender

    # Return dictionary representation of object
    def dict(self):
        return {
            "title": self.title,
            "body": self.body,
            "from_id": self.from_id,
            "from_name": self.from_name,
            "to_id": self.to_id,
            "to_name": self.to_name,
            "timestamp": self.timestamp,
            "date_string": self.date_string,
            "id": self.id,
            "deleted": self.deleted,
            "hidden_for_sender": self.hidden_for_sender
        }

    # Single entry point for every per-record read / write / delete:
    # returns the document only for its author or recipient, otherwise
    # None so callers respond 404 without revealing existence.
    @classmethod
    def get_for_user(cls, collection, message_id, user_id):
        message = collection.find_one({"id": message_id})
        if message is None or not is_party(message, user_id):
            return None
        return message

    # List and counts produced by one aggregation so they can never
    # disagree under concurrent deletes.
    @classmethod
    def user_overview(cls, collection, user_id):
        inbox_match = {
            "to_id": user_id,
            "$or": FLAG_OFF,
        }
        sent_match = {
            "from_id": user_id,
            "$and": [
                {"$or": FLAG_OFF},
                {"$or": HIDDEN_OFF},
            ],
        }
        result = list(collection.aggregate([
            {"$facet": {
                "inbox_messages": [
                    {"$match": inbox_match},
                    {"$sort": {"timestamp": -1}},
                ],
                "inbox_count": [
                    {"$match": inbox_match},
                    {"$count": "count"},
                ],
                "sent_messages": [
                    {"$match": sent_match},
                    {"$sort": {"timestamp": -1}},
                ],
                "sent_count": [
                    {"$match": sent_match},
                    {"$count": "count"},
                ],
            }}
        ]))[0]
        return {
            "inbox_messages": result.get("inbox_messages", []),
            "inbox_count": cls._count(result, "inbox_count"),
            "sent_messages": result.get("sent_messages", []),
            "sent_count": cls._count(result, "sent_count"),
        }

    @staticmethod
    def _count(facet_result, key):
        rows = facet_result.get(key) or [{}]
        return rows[0].get("count", 0)
