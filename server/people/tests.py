from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase

from calls.testing import make_person

from . import whatsapp
from .models import Profile


class WhatsAppNumberTests(SimpleTestCase):
    def test_normalizes_human_formats(self):
        self.assertEqual(whatsapp.normalize("+34 600 11-12-22"), "34600111222")
        self.assertEqual(whatsapp.normalize("0034 600111222"), "34600111222")

    def test_validates_length(self):
        self.assertTrue(whatsapp.is_valid("+34 600 111 222"))
        self.assertFalse(whatsapp.is_valid("12345"))
        self.assertFalse(whatsapp.is_valid(""))

    def test_builds_wa_me_link_with_message(self):
        self.assertEqual(whatsapp.chat_link("+34 600 111 222", "Hi Ana"), "https://wa.me/34600111222?text=Hi%20Ana")


class SetWhatsAppViewTests(TestCase):
    def setUp(self):
        self.person = make_person("maria", Profile.Role.MEMBER, whatsapp="")
        self.client.force_login(self.person.user)

    def test_saves_normalized_number(self):
        response = self.client.post("/me/whatsapp/", {"whatsapp": "+34 600 111 222"})
        self.assertEqual(response.json()["whatsapp"], "+34600111222")
        self.person.refresh_from_db()
        self.assertEqual(self.person.whatsapp, "34600111222")

    def test_rejects_invalid_number(self):
        response = self.client.post("/me/whatsapp/", {"whatsapp": "123"})
        self.assertEqual(response.status_code, 400)


class SignupTests(TestCase):
    """Joining creates the person and their Stellar wallet; the network is mocked here."""

    def setUp(self):
        from stellar_sdk import Keypair

        patcher = mock.patch("people.services.accounts.provision", side_effect=lambda amount: Keypair.random())
        self.provision = patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, **overrides):
        data = {"role": "member", "display_name": "Lucía", "username": "Lucia", "password": "secret1",
                "languages": ["es", "en"], "whatsapp": "+34 600 111 333", "city": "Sevilla"}
        data.update(overrides)
        return self.client.post("/join/", data)

    def test_member_signup_creates_wallet_logs_in_and_lands_on_help(self):
        response = self._post()
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        profile = Profile.objects.get(user__username="lucia")
        self.assertEqual((profile.role, profile.languages, profile.whatsapp), ("member", "es,en", "34600111333"))
        self.assertTrue(profile.user.wallet.public_key.startswith("G"))
        self.provision.assert_called_once_with(Decimal("25.00"))
        self.assertEqual(self.client.get("/").url, "/help/")

    def test_guide_signup_is_unverified_with_their_rate_and_no_starting_funds(self):
        self._post(role="guide", username="sam", rate="0.35")
        profile = Profile.objects.get(user__username="sam")
        self.assertFalse(profile.is_verified)
        self.assertEqual(profile.rate_per_minute, Decimal("0.35"))
        self.provision.assert_called_once_with(Decimal("0"))

    def test_errors_are_listed_and_nothing_is_created(self):
        make_person("taken", Profile.Role.MEMBER)
        response = self._post(username="taken", whatsapp="12", languages=[], role="")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "There is a problem", status_code=400)
        self.assertContains(response, "That username is taken", status_code=400)
        self.assertContains(response, "country code", status_code=400)
        self.assertContains(response, "at least one language", status_code=400)
        self.provision.assert_not_called()

    def test_whatsapp_is_required(self):
        response = self._post(whatsapp="")
        self.assertContains(response, "The video call happens on WhatsApp", status_code=400)

    def test_wallet_failure_saves_nothing_and_explains(self):
        from stellar_bridge.errors import StellarBridgeError

        self.provision.side_effect = StellarBridgeError("Friendbot failed (502)")
        response = self._post()
        self.assertContains(response, "We could not create your wallet", status_code=400)
        self.assertFalse(Profile.objects.filter(user__username="lucia").exists())


class SaveGuideTests(TestCase):
    def setUp(self):
        self.member = make_person("maria", Profile.Role.MEMBER)
        self.guide = make_person("ana", Profile.Role.GUIDE)
        self.client.force_login(self.member.user)

    def test_member_saves_and_unsaves_a_guide(self):
        self.assertTrue(self.client.post(f"/guides/{self.guide.id}/save/", {"saved": "1"}).json()["saved"])
        self.assertIn(self.guide, self.member.favorite_guides.all())
        self.assertFalse(self.client.post(f"/guides/{self.guide.id}/save/", {"saved": "0"}).json()["saved"])
        self.assertNotIn(self.guide, self.member.favorite_guides.all())

    def test_guides_cannot_save_and_members_cannot_be_saved(self):
        self.assertEqual(self.client.post(f"/guides/{self.member.id}/save/").status_code, 404)
        self.client.force_login(self.guide.user)
        self.assertEqual(self.client.post(f"/guides/{self.guide.id}/save/").status_code, 400)


class VerifyGuideTests(TestCase):
    def test_staff_verifies_a_guide_on_chain(self):
        from django.contrib.auth import get_user_model

        guide = make_person("sam", Profile.Role.GUIDE, verified=False)
        staff = get_user_model().objects.create_user("reviewer", is_staff=True)
        self.client.force_login(staff)
        self.assertContains(self.client.get("/review/"), "Verify on Stellar")
        with mock.patch("people.services.EscrowClient") as escrow:
            escrow.return_value.verify_guide.return_value = "ab" * 32
            response = self.client.post(f"/review/guides/{guide.id}/verify/", follow=True)
        escrow.return_value.verify_guide.assert_called_once_with(guide.user.wallet.public_key)
        guide.refresh_from_db()
        self.assertTrue(guide.is_verified)
        self.assertContains(response, "verified in the on-chain guide registry")

    def test_members_cannot_verify(self):
        guide = make_person("sam", Profile.Role.GUIDE, verified=False)
        self.client.force_login(make_person("maria", Profile.Role.MEMBER).user)
        self.client.post(f"/review/guides/{guide.id}/verify/")
        guide.refresh_from_db()
        self.assertFalse(guide.is_verified)


class PublicPagesTests(TestCase):
    def test_landing_introduces_and_navigation_carries_the_actions(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "eyes on call for blind people,")
        self.assertContains(page, "paid on Stellar worldwide.")
        self.assertContains(page, 'aria-label="Main"')
        self.assertContains(page, 'href="/login/"')
        self.assertContains(page, 'href="/join/"')
        self.assertNotContains(page, 'name="password"')

    def test_sign_in_is_its_own_page_and_signed_in_people_skip_the_landing(self):
        self.assertContains(self.client.get("/login/"), 'name="password"')
        person = make_person("maria", Profile.Role.MEMBER)
        self.client.force_login(person.user)
        self.assertRedirects(self.client.get("/"), "/help/", fetch_redirect_response=False)
        self.client.post("/logout/")
        self.assertEqual(self.client.get("/").status_code, 200)
