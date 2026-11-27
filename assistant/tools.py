from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from assistant.questions import pass_to_pharmacist
from core.services import pharmacy_details
from inventory.services import find_medicines
from prescriptions.services import items_of, prescriptions_of
from reservations import services as reservations

PRESCRIPTIONS_SHOWN = 5
NO_PATIENT_RECORD = "This login has no patient record at the pharmacy, so this cannot be done here."
SEVERAL_MATCHES = "Several medicines match. Ask the patient which one they mean."
REFILL_RULE = (
    f"A refill can be requested when the medicine runs out in {reservations.REMINDER_DAYS} days or less "
    "and no refill is already on its way. These are the refills due now."
)


def day(moment):
    if moment is None:
        return None
    return timezone.localtime(moment).strftime("%d %b %Y")


def medicines_named_in(text):
    text = text.lower()
    named = [medicine for medicine in find_medicines() if medicine.name.lower() in text]
    with_strength = [medicine for medicine in named if str(medicine).lower() in text]
    return with_strength or named


def not_stocked(medicine):
    return f"The pharmacy does not stock a medicine called {medicine}."


def pharmacy_info(user, patient):
    return pharmacy_details()


def stock_line(medicine, quantity):
    stock = reservations.availability(medicine, quantity)
    return {
        "medicine": str(medicine),
        "price_usd": str(medicine.price),
        "in_stock": stock["available"],
        "status": stock["label"],
    }


def check_stock(user, patient, medicine, quantity=1):
    reservations.expire_reservations()
    medicines = medicines_named_in(medicine)
    if not medicines:
        return {"found": False, "message": not_stocked(medicine)}
    return {"found": True, "medicines": [stock_line(match, int(quantity)) for match in medicines]}


def reserve_medicine(user, patient, medicine, quantity):
    if patient is None:
        return {"reserved": False, "message": NO_PATIENT_RECORD}
    medicines = medicines_named_in(medicine)
    if not medicines:
        return {"reserved": False, "message": not_stocked(medicine)}
    if len(medicines) > 1:
        return {"reserved": False, "message": SEVERAL_MATCHES, "choices": [str(match) for match in medicines]}
    reservations.expire_reservations()
    try:
        reservation = reservations.reserve_medicine(user, patient, medicines[0], int(quantity))
    except ValidationError as problem:
        return {"reserved": False, "message": " ".join(problem.messages)}
    pick_up_before = timezone.localtime(reservations.pickup_deadline(reservation))
    return {
        "reserved": True,
        "medicine": str(reservation.medicine),
        "quantity": reservation.quantity,
        "pick_up_before": pick_up_before.strftime("%d %b %Y, %H:%M"),
    }


def prescription_line(prescription):
    return {
        "number": prescription.pk,
        "medicines": [str(item.medicine) for item in items_of(prescription)],
        "status": prescription.get_status_display(),
        "received_on": day(prescription.created_at),
        "ready_on": day(prescription.ready_at),
        "collected_on": day(prescription.collected_at),
    }


def prescription_status(user, patient):
    if patient is None:
        return {"prescriptions": []}
    newest = prescriptions_of(patient)[:PRESCRIPTIONS_SHOWN]
    return {"prescriptions": [prescription_line(prescription) for prescription in newest]}


def due_refill_line(reminder):
    return {"medicine": str(reminder["item"].medicine), "runs_out_on": reminder["runs_out_on"].strftime("%d %b %Y")}


def request_refill(user, patient, medicine=""):
    if patient is None:
        return {"requested": False, "message": NO_PATIENT_RECORD}
    due = reservations.refill_reminders(patient)
    named = medicines_named_in(medicine)
    chosen = [reminder for reminder in due if reminder["item"].medicine in named]
    if len(chosen) != 1:
        return {"requested": False, "message": REFILL_RULE, "due_refills": [due_refill_line(reminder) for reminder in due]}
    item = chosen[0]["item"]
    reservations.request_refill(user, item)
    return {"requested": True, "medicine": str(item.medicine), "status": "Waiting for the pharmacist"}


def ask_pharmacist(user, patient, question):
    if patient is None:
        return {"passed_to_pharmacist": False, "message": NO_PATIENT_RECORD}
    try:
        pass_to_pharmacist(user, patient, question)
    except ValidationError as problem:
        return {"passed_to_pharmacist": False, "message": " ".join(problem.messages)}
    return {
        "passed_to_pharmacist": True,
        "reply_expected": f"During opening hours: {settings.PHARMACY_HOURS}",
        "reply_arrives": "As a notification in CarePharm.",
    }


TOOL_DECLARATIONS = [
    {
        "type": "function",
        "name": "pharmacy_info",
        "description": "Get the pharmacy's name, address, phone number and opening hours. Use this whenever the user asks when the pharmacy is open, where it is, or how to contact it.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "check_stock",
        "description": "Check whether the pharmacy has a medicine, its price in US dollars and how many are in stock. Use this whenever the user asks if a medicine is available or how much it costs.",
        "parameters": {
            "type": "object",
            "properties": {
                "medicine": {
                    "type": "string",
                    "description": "The medicine's name, with its strength if the user gave one, for example Metformin 500 mg.",
                },
                "quantity": {"type": "integer", "description": "How many the user needs. Use 1 if they did not say."},
            },
            "required": ["medicine"],
        },
    },
    {
        "type": "function",
        "name": "reserve_medicine",
        "description": "Reserve a medicine for the patient to pick up at the pharmacy within 2 hours. Only call this after the patient has confirmed the exact medicine and the quantity.",
        "parameters": {
            "type": "object",
            "properties": {
                "medicine": {"type": "string", "description": "The medicine's name and strength, for example Metformin 500 mg."},
                "quantity": {"type": "integer", "description": "How many to reserve."},
            },
            "required": ["medicine", "quantity"],
        },
    },
    {
        "type": "function",
        "name": "prescription_status",
        "description": "Get the patient's latest prescriptions with their medicines, status (Received, Preparing, Ready, Collected or Returned) and dates. Use this whenever the user asks about their prescription or whether it is ready.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "request_refill",
        "description": "Request a refill of one of the patient's long-term medicines. Call it without a medicine to list the refills that are due. Only request a refill after the patient has confirmed which medicine.",
        "parameters": {
            "type": "object",
            "properties": {
                "medicine": {"type": "string", "description": "The medicine to refill. Leave it out to list the refills that are due."},
            },
        },
    },
    {
        "type": "function",
        "name": "ask_pharmacist",
        "description": "Pass a medical question to a pharmacist, who will answer it during opening hours. Use this for any question about symptoms, side effects, doses, taking medicines together, or anything else only a pharmacist can answer, and whenever you are unsure.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The patient's question, in their own words."},
            },
            "required": ["question"],
        },
    },
]

TOOL_FUNCTIONS = {
    "pharmacy_info": pharmacy_info,
    "check_stock": check_stock,
    "reserve_medicine": reserve_medicine,
    "prescription_status": prescription_status,
    "request_refill": request_refill,
    "ask_pharmacist": ask_pharmacist,
}
