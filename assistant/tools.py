from core.services import pharmacy_details


def pharmacy_info(user):
    return pharmacy_details()


TOOL_DECLARATIONS = [
    {
        "type": "function",
        "name": "pharmacy_info",
        "description": "Get the pharmacy's name, address, phone number and opening hours. Use this whenever the user asks when the pharmacy is open, where it is, or how to contact it.",
        "parameters": {"type": "object", "properties": {}},
    },
]

TOOL_FUNCTIONS = {
    "pharmacy_info": pharmacy_info,
}
