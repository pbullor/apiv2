import re
from django.contrib.auth.models import Group, User
from django.db import models

from breathecode.admissions.models import DRAFT, Academy, Cohort, Country
from breathecode.mentorship.models import MentorshipService
from currencies import Currency as CurrencyFormatter
from . import signals

# https://devdocs.prestashop-project.org/1.7/webservice/resources/warehouses/


class Currency(models.Model):
    """
    Represents a currency.
    """

    code = models.CharField(max_length=3)
    name = models.CharField(max_length=20)

    countries = models.ManyToManyField(Country,
                                       related_name='currencies',
                                       help_text='Countries that use this currency officially')

    def format_price(self, value):
        currency = CurrencyFormatter('USD')
        currency.get_money_currency()
        return currency.get_money_format(value)

    def clean(self) -> None:
        self.code = self.code.upper()
        return super().clean()


class Price(models.Model):
    """
    This model is used to store the price of a Product or a Service.
    """

    price = models.FloatField(default=0)
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE)

    def format_price(self):
        return self.currency.format_price(self.price)


class Asset(models.Model):
    """
    This model represents a product or a service that can be sold.
    """

    slug = models.CharField(max_length=60, unique=True)
    title = models.CharField(max_length=60)
    description = models.CharField(max_length=255)
    prices = models.ManyToManyField(Price)

    owner = models.ForeignKey(Academy, on_delete=models.CASCADE, blank=True, null=True)
    private = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True


class Service(Asset):
    """
    Represents the service that can be purchased by the customer.
    """

    groups = models.ManyToManyField(Group)
    cohorts = models.ManyToManyField(Cohort)
    mentorship_services = models.ManyToManyField(MentorshipService)

    def clean(self):
        if self.unit_type:
            self.unit_type = self.unit_type.upper()

    def __str__(self):
        return self.slug

    def save(self):
        self.full_clean()

        super().save()


UNIT = 'UNIT'
SERVICE_UNITS = [
    (UNIT, 'Unit'),
]


class CommonServiceItem:
    """
    Common fields for ServiceItem and Consumable.
    """

    service = models.ForeignKey(Service, on_delete=models.CASCADE)

    # the unit between a service and a product are different
    unit_type = models.CharField(max_length=10, choices=SERVICE_UNITS, default=UNIT)
    how_many = models.IntegerField(default=0)

    def __str__(self):
        return f'{self.service.slug} {self.how_many}'


# this class is used as referenced of units of a service can be used
class ServiceItem(models.Model, CommonServiceItem):
    """
    This model is used as referenced of units of a service can be used.
    """

    pass


DAY = 'DAY'
WEEK = 'WEEK'
MONTH = 'MONTH'
YEAR = 'YEAR'
PAY_EVERY_UNIT = [
    (DAY, 'Day'),
    (WEEK, 'Week'),
    (MONTH, 'Month'),
    (YEAR, 'Year'),
]

DRAFT = 'DRAFT'
VISIBLE = 'VISIBLE'
HIDDEN = 'HIDDEN'
DELETED = 'DELETED'
PLAN_STATUS = [
    (DRAFT, 'Draft'),
    (VISIBLE, 'Visible'),
    (HIDDEN, 'Hidden'),
    (DELETED, 'Deleted'),
]


class Plan(models.Model):
    """
    A plan is a group of services that can be purchased by a user.
    """

    slug = models.CharField(max_length=60, unique=True)
    title = models.CharField(max_length=60)
    description = models.CharField(max_length=255)
    status = models.CharField(max_length=7, choices=PLAN_STATUS, default=DRAFT)
    prices = models.ManyToManyField(Price)

    renew_every = models.IntegerField(default=1)
    renew_every_unit = models.CharField(max_length=10, choices=PAY_EVERY_UNIT, default=MONTH)

    trial_duration = models.IntegerField(default=1)
    trial_duration_unit = models.CharField(max_length=10, choices=PAY_EVERY_UNIT, default=MONTH)

    services = models.ManyToManyField(ServiceItem)
    owner = models.ForeignKey(Academy, on_delete=models.CASCADE, blank=True, null=True)


FREE_TRIAL = 'FREE_TRIAL'
ACTIVE = 'ACTIVE'
CANCELLED = 'CANCELLED'
DEPRECATED = 'DEPRECATED'
PAYMENT_ISSUE = 'PAYMENT_ISSUE'
SUBSCRIPTION_STATUS = [
    (FREE_TRIAL, 'Free trial'),
    (ACTIVE, 'Active'),
    (CANCELLED, 'Cancelled'),
    (DEPRECATED, 'Deprecated'),
    (PAYMENT_ISSUE, 'Payment issue'),
]


# this class can be consumed by the api
class Consumable(models.Model, CommonServiceItem):
    """
    This model is used to represent the units of a service that can be consumed.
    """

    # if null, this is valid until resources are exhausted
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    # if null, this is valid until resources are exhausted
    valid_until = models.DateTimeField(null=True, blank=True, default=None)


FULFILLED = 'FULFILLED'
REJECTED = 'REJECTED'
PENDING = 'PENDING'
REFUNDED = 'REFUNDED'
DISPUTED_AS_FRAUD = 'DISPUTED_AS_FRAUD'
INVOICE_STATUS = [
    (FULFILLED, 'Fulfilled'),
    (REJECTED, 'Rejected'),
    (PENDING, 'Pending'),
    (REFUNDED, 'Refunded'),
    (DISPUTED_AS_FRAUD, 'Disputed as fraud'),
]


class Invoice(models.Model):
    """
    Represents a payment made by a user
    """

    amount = models.FloatField(default=0)
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE)
    paid_at = models.DateTimeField()
    status = models.CharField(max_length=10, choices=INVOICE_STATUS, default=PENDING)

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    academy = models.ForeignKey(Academy, on_delete=models.CASCADE)

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)


class Subscription(models.Model):
    """
    Allows to create a subscription to a plan and services.
    """

    paid_at = models.DateTimeField()
    status = models.CharField(max_length=9, choices=SUBSCRIPTION_STATUS, default=ACTIVE)

    is_cancellable = models.BooleanField(default=True)
    is_refundable = models.BooleanField(default=True)
    is_recurrent = models.BooleanField(default=True)
    invoices = models.ManyToManyField(Invoice)

    # if null, this is valid until resources are exhausted
    valid_until = models.DateTimeField()
    last_renew = models.DateTimeField()
    renew_credits_at = models.DateTimeField()

    pay_every = models.IntegerField(default=1)
    pay_every_unit = models.CharField(max_length=10, choices=PAY_EVERY_UNIT, default=MONTH)

    renew_every = models.IntegerField(default=1)
    renew_every_unit = models.CharField(max_length=10, choices=PAY_EVERY_UNIT, default=MONTH)

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    services = models.ManyToManyField(ServiceItem)
    plans = models.ManyToManyField(Plan)

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)


class Credit(models.Model):
    """
    Represents a credit that can be used by a user to use a service.
    """

    # if null, this is valid until resources are exhausted
    valid_until = models.DateTimeField(null=True, blank=True, default=None)
    is_free_trial = models.BooleanField(default=False)

    services = models.ManyToManyField(Consumable)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE)

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    def save(self, *args, **kwargs):
        created = not self.id

        self.full_clean()
        super().save(*args, **kwargs)

        if created:
            signals.grant_service_permissions.send(instance=self, sender=self.__class__)


GOOD = 'GOOD'
BAD = 'BAD'
FRAUD = 'FRAUD'
UNKNOWN = 'UNKNOWN'
REPUTATION_STATUS = [
    (GOOD, 'Good'),
    (BAD, 'BAD'),
    (FRAUD, 'Fraud'),
    (UNKNOWN, 'Unknown'),
]


class FinancialReputation(models.Model):
    """
    The purpose of this model is to store the reputation of a user, if the user has a bad reputation, the
    user will not be able to buy services.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='reputation')

    in_4geeks = models.CharField(max_length=10, choices=INVOICE_STATUS, default=GOOD)
    in_stripe = models.CharField(max_length=10, choices=INVOICE_STATUS, default=GOOD)

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    def get_reputation(self):
        """
        Returns the worst reputation between the two.
        """

        if self.in_4geeks == FRAUD or self.in_stripe == FRAUD:
            return FRAUD

        if self.in_4geeks == BAD or self.in_stripe == BAD:
            return BAD

        if self.in_4geeks == GOOD or self.in_stripe == GOOD:
            return GOOD

        return UNKNOWN
