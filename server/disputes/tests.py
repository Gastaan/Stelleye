from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from calls import services as calls
from calls.models import CallSession
from calls.testing import FakeEscrow, make_person
from people.models import Profile

from . import services
from .models import Report


class DisputeTests(TestCase):
    def setUp(self):
        for target in ("calls.services.EscrowClient",):
            patcher = mock.patch(target, FakeEscrow)
            patcher.start()
            self.addCleanup(patcher.stop)
        FakeEscrow.reset()
        self.member = make_person("maria", Profile.Role.MEMBER)
        self.guide = make_person("ana", Profile.Role.GUIDE, rate="0.60")
        self.reviewer = get_user_model().objects.create_user("reviewer", is_staff=True)
        call = calls.create_call(self.member.user_id, "en", Decimal("3"))
        calls.assign_guide(call.id, self.guide.user_id)
        calls.begin_connecting(call.id, self.guide.user_id)
        calls.start_call(call.id)
        FakeEscrow.advance(60)
        calls.begin_ending(call.id)
        calls.end_call(call.id, self.member.user_id)
        self.call_id = call.id

    def test_report_freezes_and_review_splits(self):
        services.report_call(self.call_id, self.member.user_id, "Misread the medicine label")
        self.assertEqual(CallSession.objects.get(pk=self.call_id).status, CallSession.Status.DISPUTED)

        report = Report.objects.get(call_id=self.call_id)
        services.resolve(report.id, Decimal("0.40"), self.reviewer, "partial refund")

        call = CallSession.objects.get(pk=self.call_id)
        self.assertEqual(call.status, CallSession.Status.RESOLVED)
        self.assertEqual(call.refunded, Decimal("2.80"))
        self.assertEqual(call.guide_payout, calls.guide_share(Decimal("0.20")))

    def test_guide_cannot_report(self):
        with self.assertRaises(PermissionError):
            services.report_call(self.call_id, self.guide.user_id, "nope")

    def test_refund_cannot_exceed_earnings(self):
        services.report_call(self.call_id, self.member.user_id, "bad call")
        report = Report.objects.get(call_id=self.call_id)
        with self.assertRaises(ValueError):
            services.resolve(report.id, Decimal("5"), self.reviewer)
