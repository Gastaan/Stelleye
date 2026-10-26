from decimal import Decimal
from unittest import mock

from django.test import TestCase

from calls.testing import make_person
from people.models import Profile
from stellar_bridge.anchor import AnchorTransaction

from .models import AnchorTransfer


def anchor_tx(anchor_id, kind, status, amount_in=None, amount_out=None, **extra):
    return AnchorTransaction.from_json({"id": anchor_id, "kind": kind, "status": status,
                                        "amount_in": amount_in, "amount_out": amount_out, **extra})


class FakeAnchor:
    """Stands in for the SEP-24 anchor: hands out ids and reports whatever status a test sets."""

    statuses: dict = {}
    started: list = []

    def __init__(self, *args, **kwargs):
        pass

    @classmethod
    def reset(cls):
        cls.statuses, cls.started = {}, []

    def limits(self, kind, asset_code):
        return {"min_amount": 1, "max_amount": 10}

    def start(self, keypair, kind, amount, lang="en"):
        anchor_id = f"{kind}-{len(self.started) + 1}"
        self.started.append((kind, amount))
        self.statuses[anchor_id] = anchor_tx(anchor_id, kind, "incomplete")
        return anchor_id, f"https://anchor.test/{anchor_id}"

    def transaction(self, keypair, anchor_id):
        return self.statuses[anchor_id]


class AnchorTransferTests(TestCase):
    def setUp(self):
        FakeAnchor.reset()
        patches = [
            mock.patch("wallets.services.AnchorClient", FakeAnchor),
            mock.patch("wallets.services.accounts.ensure_trustline"),
            mock.patch("wallets.services.balance_of", return_value=Decimal("8")),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pay = mock.patch("wallets.services.accounts.pay", return_value="ab" * 32).start()
        self.addCleanup(mock.patch.stopall)
        self.member = make_person("maria", Profile.Role.MEMBER)
        self.guide = make_person("ana", Profile.Role.GUIDE)

    def test_member_adds_money_and_hears_when_it_arrives(self):
        self.client.force_login(self.member.user)
        started = self.client.post("/wallet/add-money/", {"amount": "5"}).json()
        self.assertTrue(started["ok"])  # the request worked, even though the transfer is not done yet
        self.assertFalse(started["succeeded"])
        self.assertEqual(started["url"], "https://anchor.test/deposit-1")
        self.assertIn("Finish the steps on the payment page", started["label"])
        transfer = AnchorTransfer.objects.get(pk=started["id"])
        self.assertEqual((transfer.kind, transfer.requested_amount), ("deposit", Decimal("5.00")))

        FakeAnchor.statuses["deposit-1"] = anchor_tx("deposit-1", "deposit", "completed", "5", "4.5",
                                                     stellar_transaction_id="cd" * 32)
        done = self.client.get(f"/wallet/transfers/{transfer.pk}/").json()
        self.assertTrue(done["ok"] and done["succeeded"] and done["done"])
        self.assertEqual(done["label"], "Added 4.50 USDC to your wallet.")
        self.assertEqual(done["amount_fee"], "0.50")
        self.assertEqual(done["balance"], "8.00")
        self.assertIn("/tx/" + "cd" * 32, done["tx_url"])

    def test_amounts_outside_the_anchor_limits_are_refused(self):
        self.client.force_login(self.member.user)
        response = self.client.post("/wallet/add-money/", {"amount": "25"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("1.00 to 10.00", response.json()["message"])
        self.assertEqual(FakeAnchor.started, [])

    def test_guide_cashes_out_only_after_confirming_and_pays_once(self):
        self.client.force_login(self.guide.user)
        self.assertEqual(self.client.post("/wallet/cash-out/", {"amount": "9"}).status_code, 400)  # balance is 8
        started = self.client.post("/wallet/cash-out/", {"amount": "3"}).json()
        transfer_id = started["id"]

        FakeAnchor.statuses["withdraw-1"] = anchor_tx(
            "withdraw-1", "withdraw", "pending_user_transfer_start", "3", "2.7",
            withdraw_anchor_account="GANCHOR", withdraw_memo="8273344665109799751", withdraw_memo_type="id",
        )
        ready = self.client.get(f"/wallet/transfers/{transfer_id}/").json()
        self.assertTrue(ready["needs_confirmation"])
        self.assertIn("send 3.00 USDC, you receive 2.70 USD", ready["label"])
        self.pay.assert_not_called()

        self.client.post(f"/wallet/transfers/{transfer_id}/confirm/")
        self.client.post(f"/wallet/transfers/{transfer_id}/confirm/")  # a double tap
        self.pay.assert_called_once()
        self.assertEqual(self.pay.call_args.args[1:], ("GANCHOR", "3", "8273344665109799751", "id"))
        self.assertEqual(AnchorTransfer.objects.get(pk=transfer_id).payment_tx, "ab" * 32)

        FakeAnchor.statuses["withdraw-1"] = anchor_tx("withdraw-1", "withdraw", "completed", "3", "2.7")
        done = self.client.get(f"/wallet/transfers/{transfer_id}/").json()
        self.assertEqual(done["label"], "Sent. 2.70 USD is on its way to your bank.")

    def test_confirming_too_early_sends_nothing(self):
        self.client.force_login(self.guide.user)
        transfer_id = self.client.post("/wallet/cash-out/", {"amount": "2"}).json()["id"]
        response = self.client.post(f"/wallet/transfers/{transfer_id}/confirm/")
        self.assertEqual(response.status_code, 400)
        self.pay.assert_not_called()

    def test_roles_and_ownership(self):
        self.client.force_login(self.member.user)
        self.assertEqual(self.client.post("/wallet/cash-out/", {"amount": "2"}).status_code, 403)
        transfer_id = self.client.post("/wallet/add-money/", {"amount": "5"}).json()["id"]
        self.client.force_login(self.guide.user)
        self.assertEqual(self.client.post("/wallet/add-money/", {"amount": "5"}).status_code, 403)
        self.assertEqual(self.client.get(f"/wallet/transfers/{transfer_id}/").status_code, 404)


class TransactionsFeedTests(TestCase):
    """The caller's wallet lists money added and calls together, newest first."""

    @mock.patch("calls.views.wallets.balance_of", return_value=Decimal("12.5"))
    def test_feed_merges_deposits_and_calls(self, _balance):
        from datetime import timedelta

        from django.utils import timezone

        from calls.models import CallSession

        member = make_person("maria", Profile.Role.MEMBER)
        guide = make_person("ana", Profile.Role.GUIDE)
        call = CallSession.objects.create(member=member.user, guide=guide.user, language="en", budget=Decimal("3"),
                                          rate_per_minute=Decimal("0.20"), status=CallSession.Status.RELEASED,
                                          earned=Decimal("0.10"), refunded=Decimal("2.90"))
        CallSession.objects.filter(pk=call.pk).update(created_at=timezone.now() - timedelta(minutes=5))
        AnchorTransfer.objects.create(owner=member.user, kind="deposit", anchor_domain="anchor.test", anchor_id="d-1",
                                      status="completed", requested_amount=Decimal("10"), amount_in=Decimal("10"),
                                      amount_out=Decimal("9"), amount_fee=Decimal("1"))
        AnchorTransfer.objects.create(owner=member.user, kind="deposit", anchor_domain="anchor.test", anchor_id="d-2",
                                      status="incomplete", requested_amount=Decimal("5"))  # never finished: hidden
        self.client.force_login(member.user)
        rows = self.client.get("/api/me/").json()["transactions"]
        self.assertEqual([(r["title"], r["amount"]) for r in rows], [("Added money", "+9.00"), ("Call with Ana", "−0.10")])
        self.assertEqual(rows[0]["detail"], "10.00 USD by card or bank · fee 1.00")
        self.assertEqual(rows[1]["status_label"], "Paid out")
        page = self.client.get("/help/")
        self.assertContains(page, "Transactions")
        self.assertContains(page, "+9.00")
