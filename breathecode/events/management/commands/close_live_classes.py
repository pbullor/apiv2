from datetime import timedelta
from typing import Any
from django.core.management.base import BaseCommand

from breathecode.admissions.models import Cohort, CohortTimeSlot
from ...models import Event, LiveClass, Organization, EventbriteWebhook
from django.utils import timezone

from breathecode.events import tasks


class Command(BaseCommand):
    help = 'Close live classes'

    def handle(self, *args: Any, **options: Any):
        
        utc_now = timezone.now()
        
        old_classes_that_never_started = LiveClass.objects.filter(started_at__isnull=True, ended_at__isnull=True, ending_at__lte=utc_now)
        self.style.SUCCESS(f'There are {str(old_classes_that_never_started.count())} old classes that never started and should be closed')
        
        live_classes_that_nerver_closed = LiveClass.objects.filter(started_at__isnull=False, ended_at__isnull=True)
        self.style.SUCCESS(f'There are {str(live_classes_that_nerver_closed.count())} live classes that did start, but still need to be closed')

        classes_to_close = list(old_classes_that_never_started) +list(live_classes_that_nerver_closed)
        for _class in classes_to_close:
            ended_at = _class.ending_at + timedelta(minutes=30)
            if ended_at < utc_now:
                _class.ended_at = ended_at
                _class.save()
