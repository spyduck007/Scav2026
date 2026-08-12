from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Challenge,
    ChallengeCategory,
    ChallengeDependency,
    ChallengeSolve,
    Participant,
)


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    SCAV_HUNT_TEAM_YEARS=[2027, 2028, 2029, 2030],
    SCAV_SUBMISSION_COOLDOWN_SECONDS=0,
)
class FunctionalRegressionTests(TestCase):
    def setUp(self):
        self.category = ChallengeCategory.objects.create(name="Audit category")
        self.challenge = Challenge.objects.create(
            category=self.category,
            title="Windowed challenge",
            description="A challenge used for request tests.",
            answer="correct",
        )
        self.student = Participant.objects.create(
            ion_username="student", graduation_year=2027
        )
        self.admin = Participant.objects.create(
            ion_username="admin", graduation_year=2027, is_admin=True, is_scavcomm=True
        )

    def client_for(self, participant):
        client = Client()
        session = client.session
        session["ion_participant_id"] = participant.pk
        session.save()
        return client

    @override_settings(
        SCAV_HUNT_START=timezone.now() + timedelta(days=1), SCAV_HUNT_END=None
    )
    def test_closed_hunt_blocks_student_submissions_but_allows_organizers(self):
        student_client = self.client_for(self.student)
        admin_client = self.client_for(self.admin)
        submit_url = reverse("core:submit_challenge", args=[self.challenge.slug])

        self.assertEqual(student_client.get(reverse("core:challenge")).status_code, 403)
        student_client.post(submit_url, {"answer": "correct"})
        self.assertFalse(
            ChallengeSolve.objects.filter(
                challenge=self.challenge, team_year=self.student.graduation_year
            ).exists()
        )

        admin_client.post(submit_url, {"answer": "correct"})
        self.assertTrue(
            ChallengeSolve.objects.filter(
                challenge=self.challenge, team_year=self.admin.graduation_year
            ).exists()
        )

    def test_switching_analytics_class_does_not_change_account_team(self):
        client = self.client_for(self.admin)
        original_year = self.admin.graduation_year

        response = client.post(reverse("core:switch_class"), {"class_year": "2028"})
        self.assertRedirects(response, reverse("core:analytics"))
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.graduation_year, original_year)
        self.assertEqual(client.session["admin_view_as_class"], 2028)

        analytics = client.get(reverse("core:analytics"))
        selected_teams = [
            entry["year"]
            for entry in analytics.context["leaderboard"]
            if entry["is_user_team"]
        ]
        self.assertEqual(selected_teams, [2028])

        client.post(reverse("core:switch_class"), {"class_year": "reset"})
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.graduation_year, original_year)
        self.assertNotIn("admin_view_as_class", client.session)

    def test_indirect_dependency_cycles_are_rejected(self):
        first = Challenge.objects.create(
            category=self.category, title="First", description="", answer="first"
        )
        second = Challenge.objects.create(
            category=self.category, title="Second", description="", answer="second"
        )
        third = Challenge.objects.create(
            category=self.category, title="Third", description="", answer="third"
        )
        for challenge, prerequisite in ((first, second), (second, third)):
            dependency = ChallengeDependency(
                challenge=challenge, prerequisite=prerequisite
            )
            dependency.full_clean()
            dependency.save()

        closing_dependency = ChallengeDependency(challenge=third, prerequisite=first)
        with self.assertRaises(ValidationError):
            closing_dependency.full_clean()
