"""How the two people see each other once a call is paid for: a WhatsApp video call.

Stelleye matches, meters and pays; the guide places the WhatsApp call, so the member only
has to answer a normal WhatsApp call. Numbers are revealed only to the matched pair, only
after the guide accepts. In-app video (static/js/videocall.js) is the backup.
"""
from people import whatsapp


def _both_have_numbers(info: dict) -> bool:
    return bool(info.get("guide_whatsapp") and info.get("member_whatsapp"))


def for_member(info: dict) -> dict | None:
    if not _both_have_numbers(info):
        return None
    guide = info["guide_name"]
    return {
        "channel": "whatsapp",
        "peer_number": whatsapp.display(info["guide_whatsapp"]),
        "link": whatsapp.chat_link(info["guide_whatsapp"], f"Hi {guide}, I'm ready for your video call."),
        "app_link": whatsapp.app_link(info["guide_whatsapp"], f"Hi {guide}, I'm ready for your video call."),
        "instructions": f"{guide} will video call you on WhatsApp. Answer the call when it rings.",
    }


def for_guide(info: dict) -> dict | None:
    if not _both_have_numbers(info):
        return None
    member, guide = info["member_name"], info["guide_name"]
    return {
        "channel": "whatsapp",
        "peer_number": whatsapp.display(info["member_whatsapp"]),
        "link": whatsapp.chat_link(info["member_whatsapp"], f"Hi {member}, this is {guide} from Stelleye. I'm video calling you now."),
        "app_link": whatsapp.app_link(info["member_whatsapp"], f"Hi {member}, this is {guide} from Stelleye. I'm video calling you now."),
        "instructions": f"Open WhatsApp and tap the video button to call {member}. The meter is running.",
    }
