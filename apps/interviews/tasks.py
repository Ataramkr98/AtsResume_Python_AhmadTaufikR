from celery import shared_task
from django.core.mail import EmailMessage
from icalendar import Calendar, Event

from .models import Interview


@shared_task
def send_interview_invites(interview_id: str):
    interview = Interview.objects.select_related("application__candidate", "application__job").get(id=interview_id)
    calendar = Calendar()
    event = Event()
    event.add("summary", interview.title)
    event.add("dtstart", interview.start_at)
    event.add("dtend", interview.end_at)
    event.add("location", interview.meeting_url or interview.location)
    calendar.add_component(event)
    for participant in interview.participants.select_related("user"):
        email = EmailMessage(
            subject=f"Interview: {interview.title}",
            body=(
                f"You are invited to interview {interview.application.candidate} for "
                f"{interview.application.job.title}."
            ),
            to=[participant.user.email],
        )
        email.attach("interview.ics", calendar.to_ical(), "text/calendar")
        email.send(fail_silently=False)
