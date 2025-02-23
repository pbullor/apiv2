from __future__ import annotations
from datetime import datetime
from typing import Any
from rest_framework.test import APITestCase
from django.db.models import Model
from django.db.models.query import QuerySet
from . import interfaces

from ..sha256_mixin import Sha256Mixin
from ..token_mixin import TokenMixin

__all__ = ['Check']


class Check:
    """Mixin with the purpose of cover all the related with the custom asserts"""

    sha256 = Sha256Mixin.assertHash
    token = TokenMixin.assertToken
    _parent: APITestCase
    _bc: interfaces.BreathecodeInterface

    def __init__(self, parent, bc: interfaces.BreathecodeInterface) -> None:
        self._parent = parent
        self._bc = bc

    def datetime_in_range(self, start: datetime, end: datetime, date: datetime) -> None:
        """
        Check if a range if between start and end argument.

        Usage:

        ```py
        from django.utils import timezone

        start = timezone.now()
        in_range = timezone.now()
        end = timezone.now()
        out_of_range = timezone.now()

        # pass because this datetime is between start and end
        self.bc.check.datetime_in_range(start, end, in_range)  # 🟢

        # fail because this datetime is not between start and end
        self.bc.check.datetime_in_range(start, end, out_of_range)  # 🔴
        ```
        """

        self._parent.assertLess(start, date)
        self._parent.assertGreater(end, date)

    def partial_equality(self, first: dict | list[dict], second: dict | list[dict]) -> None:
        """
        Fail if the two objects are partially unequal as determined by the '==' operator.

        Usage:

        ```py
        obj1 = {'key1': 1, 'key2': 2}
        obj2 = {'key2': 2, 'key3': 1}
        obj3 = {'key2': 2}

        # it's fail because the key3 is not in the obj1
        self.bc.check.partial_equality(obj1, obj2)  # 🔴

        # it's fail because the key1 is not in the obj2
        self.bc.check.partial_equality(obj2, obj1)  # 🔴

        # it's pass because the key2 exists in the obj1
        self.bc.check.partial_equality(obj1, obj3)  # 🟢

        # it's pass because the key2 exists in the obj2
        self.bc.check.partial_equality(obj2, obj3)  # 🟢

        # it's fail because the key1 is not in the obj3
        self.bc.check.partial_equality(obj3, obj1)  # 🔴

        # it's fail because the key3 is not in the obj3
        self.bc.check.partial_equality(obj3, obj2)  # 🔴
        ```
        """

        assert type(first) == type(second)

        if isinstance(first, list):
            assert len(first) == len(second)

            original = []

            for i in range(0, len(first)):
                original.append(self._fill_partial_equality(first[i], second[i]))

        else:
            original = self._fill_partial_equality(first, second)

        self._parent.assertEqual(original, second)

    def _fill_partial_equality(self, first: dict, second: dict) -> dict:
        original = {}

        for key in second.keys():
            original[key] = second[key]

        return original

    def queryset_of(self, query: Any, model: Model) -> None:
        """
        Check if the first argument is a queryset of a models provided as second argument.

        Usage:

        ```py
        from breathecode.admissions.models import Cohort, Academy

        self.bc.database.create(cohort=1)

        collection = []
        queryset = Cohort.objects.filter()

        # pass because the first argument is a QuerySet and it's type Cohort
        self.bc.check.queryset_of(queryset, Cohort)  # 🟢

        # fail because the first argument is a QuerySet and it is not type Academy
        self.bc.check.queryset_of(queryset, Academy)  # 🔴

        # fail because the first argument is not a QuerySet
        self.bc.check.queryset_of(collection, Academy)  # 🔴
        ```
        """

        if not isinstance(query, QuerySet):
            self._parent.fail('The first argument is not a QuerySet')

        if query.model != model:
            self._parent.fail(f'The QuerySet is type {query.model.__name__} instead of {model.__name__}')

    def queryset_with_pks(self, query: Any, pks: list[int]) -> None:
        """
        Check if the queryset have the following primary keys.

        Usage:

        ```py
        from breathecode.admissions.models import Cohort, Academy

        self.bc.database.create(cohort=1)

        collection = []
        queryset = Cohort.objects.filter()

        # pass because the QuerySet has the primary keys 1
        self.bc.check.queryset_with_pks(queryset, [1])  # 🟢

        # fail because the QuerySet has the primary keys 1 but the second argument is empty
        self.bc.check.queryset_with_pks(queryset, [])  # 🔴
        ```
        """

        if not isinstance(query, QuerySet):
            self._parent.fail('The first argument is not a QuerySet')

        self._parent.assertEqual([x.pk for x in query], pks)

    def list_with_pks(self, query: Any, pks: list[int]) -> None:
        """
        Check if the list have the following primary keys.

        Usage:

        ```py
        from breathecode.admissions.models import Cohort, Academy

        model = self.bc.database.create(cohort=1)

        collection = [model.cohort]

        # pass because the QuerySet has the primary keys 1
        self.bc.check.list_with_pks(collection, [1])  # 🟢

        # fail because the QuerySet has the primary keys 1 but the second argument is empty
        self.bc.check.list_with_pks(collection, [])  # 🔴
        ```
        """

        if not isinstance(query, list):
            self._parent.fail('The first argument is not a list')

        self._parent.assertEqual([x.pk for x in query], pks)
