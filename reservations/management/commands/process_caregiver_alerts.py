from django.core.management.base import BaseCommand

from reservations.services import send_caregiver_refill_reminders, send_uncollected_refill_alerts


class Command(BaseCommand):
    help = "Notify caregivers about refills running out and refills uncollected for 48 hours."

    def handle(self, *args, **options):
        reminders = send_caregiver_refill_reminders()
        uncollected = send_uncollected_refill_alerts()
        self.stdout.write(
            self.style.SUCCESS(f"Sent {reminders} refill reminders and {uncollected} uncollected-refill alerts.")
        )
